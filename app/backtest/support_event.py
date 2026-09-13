"""Frozen event features and a separate, forward-only outcome evaluator."""
from dataclasses import asdict, dataclass
from typing import Literal
import math

import pandas as pd

from app.volatility import (calculate_atr_percent, calculate_atr_signed_distance,
                            calculate_zone_width_atr, classify_volatility)
from .config import SupportEventConfig


def finite(value) -> float | None:
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except (TypeError, ValueError, OverflowError):
        return None


def valid_bar(low, high, close) -> bool:
    low, high, close = map(finite, (low, high, close))
    return (low is not None and high is not None and close is not None
            and 0 < low <= close <= high)


def is_support_touch(low, high, support_low, support_high, atr, threshold) -> bool:
    values = list(map(finite, (low, high, support_low, support_high, atr, threshold)))
    if any(value is None for value in values):
        return False
    low, high, support_low, support_high, atr, threshold = values
    if not (0 < low <= high and 0 < support_low <= support_high and atr > 0 and threshold >= 0):
        return False
    # Symmetric expanded-zone intersection, including a near miss below it.
    return low <= support_high + threshold * atr and high >= support_low - threshold * atr


def advance_exit_count(close, low, high, atr, threshold, count, *, side='above'):
    """Reusable episode hysteresis; support events retain above-only exits.

    Research touch episodes may leave either side; resistance can use below.
    Missing prices reset consecutive-bar confirmation.
    """
    close, low, high, atr = map(finite, (close, low, high, atr))
    if None in (close, low, high, atr) or atr <= 0 or close <= 0:
        return 0
    if side not in ('above', 'below', 'either'):
        raise ValueError('side must be above, below or either')
    left = ((side in ('above', 'either') and close > high + threshold * atr)
            or (side in ('below', 'either') and close < low - threshold * atr))
    return count + 1 if left else 0


def zones_match(low, high, anchor_low, anchor_high, anchor_atr, tolerance):
    return (low <= anchor_high and high >= anchor_low
            or abs((low + high) / 2 - (anchor_low + anchor_high) / 2) <= tolerance * anchor_atr)


@dataclass(frozen=True)
class SupportEvent:
    symbol: str
    event_date: str
    event_index: int
    timeframe: str
    support_as_of: str
    support_low: float
    support_high: float
    support_mid: float
    zone_width_atr: float
    entry_close: float
    entry_low: float
    entry_high: float
    atr: float
    atr_percent: float
    volatility_level: str
    distance_to_support_atr: float
    support_touch_count: int | None
    support_sources: list[str]
    support_source_count: int
    future_max_price: float
    future_min_price: float
    future_max_return_pct: float
    future_min_return_pct: float
    max_rebound_atr: float
    max_breakdown_atr: float
    success_bar: int | None
    failure_bar: int | None
    bars_to_success: int | None
    bars_to_failure: int | None
    label: Literal['success', 'failure', 'neutral']
    same_bar_ambiguous: bool
    label_available_date: str | None = None
    # Predictor evidence: known by the event close. Historical reactions must
    # have their entire observation window end strictly BEFORE this event.
    detector_touch_count: int | None = None
    historical_touch_count: int | None = None
    current_touch_number: int | None = None
    last_touch_date: str | None = None
    bars_since_last_touch: int | None = None
    first_touch_date: str | None = None
    support_age_bars: int | None = None
    support_first_seen_date: str | None = None
    current_touch_start_date: str | None = None
    historical_touch_reaction_count: int | None = None
    historical_touch_avg_rebound_atr: float | None = None
    historical_touch_median_rebound_atr: float | None = None
    historical_touch_max_rebound_atr: float | None = None
    historical_touch_avg_breakdown_atr: float | None = None
    historical_touch_success_count: int | None = None
    historical_touch_failure_count: int | None = None
    touch_rebound_weakening: bool | None = None
    touch_rebound_trend: str = 'insufficient_data'
    historical_touch_episodes: list | None = None
    touch_close: float | None = None
    touch_low: float | None = None
    touch_high: float | None = None
    touch_volume: float | None = None
    touch_volume_ma20: float | None = None
    touch_volume_ratio: float | None = None
    touch_volume_level: str | None = None
    touch_volume_zscore: float | None = None
    pre_touch_volume_avg: float | None = None
    support_volume_share: float | None = None
    support_volume_density_ratio: float | None = None
    # OUTCOME / RESEARCH ONLY: never predictors at event_date.
    future_3bar_avg_volume: float | None = None
    future_volume_ratio: float | None = None
    post_touch_volume_avg: float | None = None
    post_pre_volume_ratio: float | None = None
    rebound_volume_confirmation: str | None = None
    breakdown_volume: float | None = None
    breakdown_volume_ma20: float | None = None
    breakdown_volume_ratio: float | None = None
    breakdown_volume_level: str | None = None

    def to_dict(self):
        return asdict(self)


EVENT_COLUMNS = list(SupportEvent.__dataclass_fields__)
OUTCOME_COLUMNS = [
    'label_available_date',
    'future_max_price', 'future_min_price', 'future_max_return_pct', 'future_min_return_pct',
    'max_rebound_atr', 'max_breakdown_atr', 'success_bar', 'failure_bar', 'bars_to_success',
    'bars_to_failure', 'label', 'same_bar_ambiguous', 'future_3bar_avg_volume',
    'future_volume_ratio', 'post_touch_volume_avg', 'post_pre_volume_ratio',
    'rebound_volume_confirmation', 'breakdown_volume', 'breakdown_volume_ma20',
    'breakdown_volume_ratio', 'breakdown_volume_level',
]
AUDIT_COLUMNS = ['historical_touch_episodes']
PREDICTOR_COLUMNS = [name for name in EVENT_COLUMNS if name not in OUTCOME_COLUMNS + AUDIT_COLUMNS]


def select_predictor_features(events_df):
    """Explicit allowlist, preventing future/outcome fields entering predictors."""
    return events_df.reindex(columns=PREDICTOR_COLUMNS).copy()


def evaluate_support_event(future: pd.DataFrame, *, entry_close: float,
                           support_low: float, atr: float, event_index: int,
                           config: SupportEventConfig) -> dict | None:
    """Evaluate exactly t+1..t+N. Returns None for incomplete/invalid windows.

    *_bar are absolute zero-based positions in the input backtest frame.
    bars_to_* are offsets (1..N). Both hits and full-window extrema are retained
    even if the earlier hit has already determined the label.
    """
    entry_close, support_low, atr = map(finite, (entry_close, support_low, atr))
    if (entry_close is None or support_low is None or atr is None
            or min(entry_close, support_low, atr) <= 0
            or len(future) != config.lookahead_bars
            or not all(col in future for col in ('Low', 'High', 'Close'))):
        return None
    rows = list(future[['Low', 'High', 'Close']].itertuples(index=False, name=None))
    if not all(valid_bar(*row) for row in rows):
        return None
    rows = [tuple(map(float, row)) for row in rows]
    success_price = entry_close + config.success_rebound_atr * atr
    failure_price = support_low - config.failure_breakdown_atr * atr
    success = failure = None
    for offset, (low, high, close) in enumerate(rows, 1):
        if success is None and high >= success_price:
            success = offset
        if failure is None and close < failure_price:
            failure = offset
    ambiguous = success is not None and success == failure
    if ambiguous:
        label = config.same_bar_policy
    elif success is not None and (failure is None or success < failure):
        label = 'success'
    elif failure is not None:
        label = 'failure'
    else:
        label = 'neutral'
    maximum = max(row[1] for row in rows)
    minimum = min(row[0] for row in rows)
    metrics = dict(future_max_price=maximum, future_min_price=minimum,
                   future_max_return_pct=(maximum / entry_close - 1) * 100,
                   future_min_return_pct=(minimum / entry_close - 1) * 100,
                   max_rebound_atr=calculate_atr_signed_distance(maximum, entry_close, atr),
                   max_breakdown_atr=calculate_atr_signed_distance(minimum, support_low, atr))
    if any(finite(value) is None for value in metrics.values()):
        return None
    return dict(**metrics, success_bar=None if success is None else event_index + success,
                failure_bar=None if failure is None else event_index + failure,
                bars_to_success=success, bars_to_failure=failure, label=label,
                same_bar_ambiguous=ambiguous)


def event_features(symbol, data, index, zone, atr, timeframe) -> dict:
    """Capture only information known by the event close; zone is from t-1."""
    low, high = float(zone['low']), float(zone['high'])
    row = data.iloc[index]
    close = float(row.Close)
    percent = calculate_atr_percent(atr, close)
    sources = sorted(set(zone.get('methods') or zone.get('sources') or []))
    touches = finite(zone.get('touch_count'))
    return dict(symbol=str(symbol), event_date=data.index[index].isoformat(),
                event_index=index, timeframe=timeframe,
                support_as_of=data.index[index - 1].isoformat(),
                support_low=low, support_high=high, support_mid=(low + high) / 2,
                zone_width_atr=calculate_zone_width_atr(low, high, atr),
                entry_close=close, entry_low=float(row.Low), entry_high=float(row.High),
                atr=atr, atr_percent=percent, volatility_level=classify_volatility(percent),
                distance_to_support_atr=max(low - close, close - high, 0) / atr,
                support_touch_count=int(touches) if touches is not None and touches >= 0 else None,
                support_sources=sources, support_source_count=len(sources))
