"""Descriptive event counts, not probabilities predicted by a trading model."""
import pandas as pd

LABELS = ('success', 'failure', 'neutral')
DISTANCE_BINS = (0.0, 0.25, 0.5, 1.0, float('inf'))
DISTANCE_LABELS = ('0–0.25 ATR', '(0.25–0.5] ATR', '(0.5–1] ATR', '>1 ATR')
VOLUME_RATIO_BINS = (0, 0.8, 1.0, 1.2, 1.5, 2.0, float('inf'))
VOLUME_RATIO_LABELS = ('<0.8', '[0.8,1.0)', '[1.0,1.2)', '[1.2,1.5)', '[1.5,2.0)', '>=2.0')


def summarize_support_events(events_df: pd.DataFrame) -> dict:
    """Rates are fractions in [0,1]; absent denominators return None.

    Unknown/invalid/incomplete labels are excluded, with an explicit count.
    """
    labels = events_df.get('label', pd.Series(index=events_df.index, dtype='object'))
    counts = {label: int(labels.eq(label).sum()) for label in LABELS}
    total = sum(counts.values())
    resolved = counts['success'] + counts['failure']
    return dict(total_events=total, excluded_count=len(events_df) - total,
                **{label + '_count': count for label, count in counts.items()},
                **{label + '_rate': count / total if total else None for label, count in counts.items()},
                resolved_success_rate=counts['success'] / resolved if resolved else None)


def summarize_by_volatility(events_df: pd.DataFrame) -> pd.DataFrame:
    return _group_summary(events_df, events_df.get(
        'volatility_level', pd.Series('資料不足', index=events_df.index)), 'volatility_level')


def summarize_by_atr_distance(events_df: pd.DataFrame) -> pd.DataFrame:
    distance = pd.to_numeric(events_df.get(
        'distance_to_support_atr', pd.Series(index=events_df.index, dtype=float)), errors='coerce')
    distance = distance.where(distance.ge(0) & distance.lt(float('inf')))
    groups = pd.cut(distance, bins=DISTANCE_BINS, labels=DISTANCE_LABELS,
                    include_lowest=True, right=True).astype('object').fillna('資料不足')
    return _group_summary(events_df, groups, 'distance_bucket')


def _group_summary(events_df, groups, name):
    records = []
    for group, data in events_df.groupby(groups.fillna('資料不足'), sort=True, dropna=False):
        records.append({name: str(group), **summarize_support_events(data)})
    return pd.DataFrame(records, columns=[name, *summarize_support_events(pd.DataFrame()).keys()])


def _touch_groups(events_df):
    count = pd.to_numeric(events_df.get('current_touch_number', pd.Series(index=events_df.index, dtype=float)), errors='coerce')
    def bucket(value):
        if pd.isna(value) or value < 1 or value == float('inf') or value != int(value):
            return '資料不足'
        return '#4+' if value >= 4 else f'#{int(value)}'
    return count.map(bucket)


def _evidence_groups(events_df, groups, name):
    table = _group_summary(events_df, groups, name)
    table.insert(1, 'event_count', table.total_events)
    return table


def summarize_touch_statistics(events_df):
    return _evidence_groups(events_df, _touch_groups(events_df), 'touch_number')


def summarize_volume_statistics(events_df):
    groups = events_df.get('touch_volume_level', pd.Series(index=events_df.index, dtype='object'))
    groups = groups.where(groups.isin(['low', 'normal', 'elevated', 'spike']), '資料不足')
    return _evidence_groups(events_df, groups, 'touch_volume_level')


def summarize_volume_ratio_bins(events_df):
    ratio = pd.to_numeric(events_df.get('touch_volume_ratio', pd.Series(index=events_df.index, dtype=float)), errors='coerce')
    ratio = ratio.where(ratio.ge(0) & ratio.lt(float('inf')))
    groups = pd.cut(ratio, bins=VOLUME_RATIO_BINS, labels=VOLUME_RATIO_LABELS,
                    right=False).astype('object').fillna('資料不足')
    return _evidence_groups(events_df, groups, 'volume_ratio_bin')


def summarize_touch_volume_cross(events_df):
    data = events_df.copy()
    data['_touch_group'] = _touch_groups(data)
    levels = data.get('touch_volume_level', pd.Series(index=data.index, dtype='object'))
    data['_volume_group'] = levels.where(levels.isin(['low', 'normal', 'elevated', 'spike']), '資料不足')
    records = []
    for (touch, volume), group in data.groupby(['_touch_group', '_volume_group'], sort=True):
        summary = summarize_support_events(group)
        records.append(dict(touch_number=touch, touch_volume_level=volume,
                            event_count=summary['total_events'], **summary))
    return pd.DataFrame(records, columns=['touch_number', 'touch_volume_level', 'event_count',
                                         *summarize_support_events(pd.DataFrame()).keys()])
