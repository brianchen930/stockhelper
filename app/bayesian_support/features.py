"""Predictor-only categorization and explicit temporal dataset validation."""
import math
import pandas as pd

from .config import FEATURE_SPECS


def finite(value):
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError, OverflowError):
        return None


def bucket_value(feature, value):
    spec = FEATURE_SPECS[feature]
    if 'edges' not in spec:
        return value if isinstance(value, str) and value in spec['categories'] else None
    value = finite(value)
    if value is None or value < spec['minimum'] or spec.get('integer') and not value.is_integer():
        return None
    for edge, label in zip(spec['edges'], spec['categories']):
        if (value <= edge if spec['right'] else value < edge):
            return label
    return spec['categories'][-1]


def clean_scalar(value):
    if isinstance(value, str):
        return value
    return finite(value)


def validate_events(events):
    required = ['symbol', 'timeframe', 'event_date', 'support_low', 'support_high', 'label', 'label_available_date']
    if any(name not in events for name in required):
        raise ValueError('Events require identity, label and label_available_date; do not infer maturity from event_date alone')
    data = events.copy(deep=True)
    for name in ('event_date', 'label_available_date'):
        data[name] = pd.to_datetime(data[name], utc=True, format='mixed', errors='raise')
        if data[name].isna().any():
            raise ValueError(f'Missing {name}')
    if (data.label_available_date <= data.event_date).any():
        raise ValueError('label_available_date must follow event_date')
    keys = ['symbol', 'timeframe', 'event_date', 'support_low', 'support_high']
    if data[keys].isna().any().any():
        raise ValueError('Event identity may not be missing')
    for name in ('support_low', 'support_high'):
        values = data[name].map(finite)
        if values.isna().any() or (values <= 0).any():
            raise ValueError('Invalid support price')
        data[name] = values
    if (data.support_low > data.support_high).any():
        raise ValueError('Inverted support zone')
    # A duplicate is not another observation. Conflicting predictor/target rows
    # must be resolved at the source rather than silently choosing a version.
    duplicate_count = 0
    rows = []
    # The usual dataset has no duplicates; avoid constructing a Python Series
    # per row for every expanding-window fit.
    if not data.duplicated(keys).any():
        output = data.sort_values(keys[2:3] + keys[:2] + keys[3:], kind='stable').reset_index(drop=True)
        output.attrs.update(events.attrs, duplicate_count=0)
        return output
    for _, group in data.groupby(keys, sort=False):
        if len(group) > 1:
            relevant = [*keys, 'label', 'label_available_date', *[s['raw'] for s in FEATURE_SPECS.values() if s['raw'] in data]]
            if len(group[relevant].drop_duplicates()) != 1:
                raise ValueError('Conflicting duplicate event')
            duplicate_count += len(group) - 1
        rows.append(group.iloc[0])
    output = pd.DataFrame(rows, columns=data.columns).sort_values(keys[2:3] + keys[:2] + keys[3:], kind='stable').reset_index(drop=True)
    # Preserve timezone-aware dtypes even on an empty dataset.
    for name in ('event_date', 'label_available_date'):
        output[name] = pd.to_datetime(output[name], utc=True)
    output.attrs.update(events.attrs, duplicate_count=duplicate_count)
    return output


def attach_label_availability(events, histories, lookahead_bars):
    """Migrate old CSVs using actual per-symbol bar calendars, never calendar days."""
    if type(lookahead_bars) is not int or lookahead_bars < 1:
        raise ValueError('lookahead_bars must be positive')
    result = events.copy()
    dates = []
    for row in result.itertuples():
        history = histories[str(row.symbol)]
        calendar = pd.to_datetime(history.index, utc=True)
        if not calendar.is_unique or not calendar.is_monotonic_increasing:
            raise ValueError('History must have unique chronological timestamps')
        position = finite(row.event_index)
        if position is None or not position.is_integer() or position < 0:
            raise ValueError('Invalid event_index')
        i = int(position)
        if i + lookahead_bars >= len(calendar) or calendar[i] != pd.to_datetime(row.event_date, utc=True):
            raise ValueError('Event/history mismatch or incomplete outcome window')
        dates.append(calendar[i + lookahead_bars].isoformat())
    result['label_available_date'] = dates
    return result
