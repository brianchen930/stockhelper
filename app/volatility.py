"""Causal Wilder ATR and display-only volatility normalization utilities."""
import math

import pandas as pd

ATR_PERIOD = 14
LOW_VOLATILITY_MAX = 1.5
MEDIUM_VOLATILITY_MAX = 3.0
HIGH_VOLATILITY_MAX = 5.0


def _number(value) -> float | None:
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError, OverflowError):
        return None


def calculate_atr(data: pd.DataFrame, period: int = ATR_PERIOD) -> pd.Series:
    """Return an index-aligned series; input must be chronological, unique HLC.

    The first bar supplies previous Close only. Seed with period consecutive
    TRs, then Wilder smoothing. Invalid bars reset warm-up, never fill forward
    or backward. Thus ATR14 first appears on bar 15. No input is mutated.
    """
    result = pd.Series(float('nan'), index=data.index, name='atr', dtype='float64')
    if (not isinstance(period, int) or isinstance(period, bool) or period < 1
            or not data.index.is_monotonic_increasing or not data.index.is_unique
            or not data.columns.is_unique
            or not all(column in data for column in ('High', 'Low', 'Close'))):
        return result
    previous = None
    average = None
    seed = []
    for i, row in enumerate(data[['High', 'Low', 'Close']].itertuples(index=False, name=None)):
        high, low, close = map(_number, row)
        if (high is None or low is None or close is None
                or low <= 0 or not low <= close <= high):
            previous, average, seed = None, None, []
            continue
        if previous is not None:
            tr = max(high - low, abs(high - previous), abs(low - previous))
            if average is None:
                seed.append(tr / period)
                if len(seed) == period:
                    average = math.fsum(seed)
            else:
                average = average * ((period - 1) / period) + tr / period
            if average is not None:
                result.iloc[i] = average
        previous = close
    return result


def calculate_atr_percent(atr: float, close: float) -> float | None:
    atr, close = _number(atr), _number(close)
    if atr is None or atr < 0 or close is None or close <= 0:
        return None
    return _number(atr / close * 100)


def classify_volatility(atr_percent: float) -> str:
    value = _number(atr_percent)
    if value is None or value < 0:
        return '資料不足'
    for ceiling, label in ((LOW_VOLATILITY_MAX, '低波動'),
                           (MEDIUM_VOLATILITY_MAX, '中等波動'),
                           (HIGH_VOLATILITY_MAX, '高波動')):
        if value < ceiling:
            return label
    return '極高波動'


def calculate_atr_signed_distance(price: float, reference_price: float, atr: float) -> float | None:
    price, reference_price, atr = map(_number, (price, reference_price, atr))
    if (price is None or reference_price is None or atr is None
            or price <= 0 or reference_price <= 0 or atr <= 0):
        return None
    return _number((price - reference_price) / atr)


def calculate_atr_distance(price_a: float, price_b: float, atr: float) -> float | None:
    distance = calculate_atr_signed_distance(price_a, price_b, atr)
    return None if distance is None else abs(distance)


def calculate_zone_width_atr(zone_low: float, zone_high: float, atr: float) -> float | None:
    width = calculate_atr_signed_distance(zone_high, zone_low, atr)
    return width if width is not None and width >= 0 else None


def summarize_volatility(data: pd.DataFrame, period: int = ATR_PERIOD) -> dict:
    series = calculate_atr(data, period)
    atr = _number(series.iloc[-1]) if len(series) else None
    close = data['Close'].iloc[-1] if len(data) and 'Close' in data else None
    percent = calculate_atr_percent(atr, close)
    return dict(atr=atr, atr_percent=percent, volatility_level=classify_volatility(percent))
