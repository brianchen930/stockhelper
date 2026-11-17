"""Isolated experiment: ATR hysteresis band around the 60m MA60.

The production signals/decisions/runner are NOT modified. The unchanged
`backtest.runner.run_backtest` is executed with its `generate_signals`
reference temporarily swapped for `buffered_signals`, so fills, costs,
daily BULL filter, EXIT priority and accounting are byte-for-byte the
original code paths.

Rule (k fixed before any result was seen; k=0 reproduces the original):
    ATR14[t] = simple mean of the last 14 canonical 60m True Ranges;
               NaN if any of those bars (or the prior Close) is invalid.
    upper[t] = MA60[t] + k*ATR14[t];  lower[t] = MA60[t] - k*ATR14[t]
    BUY  = Close[t-1] <= upper[t-1] and Close[t] > upper[t]
    SELL = Close[t-1] >= lower[t-1] and Close[t] < lower[t]
Only completed bars up to t are used, so no look-ahead is introduced.
"""
from contextlib import contextmanager
from copy import deepcopy

import pandas as pd

from ..signals import generate_signals
from ..backtest import runner as _runner

ATR_PERIOD = 14


def _num(series):
    return pd.to_numeric(series, errors='coerce').astype('float64')


def atr14(bars: pd.DataFrame) -> pd.Series:
    valid = bars.is_valid.eq(True)
    high, low, close = (_num(bars[c]).where(valid) for c in ('High', 'Low', 'Close'))
    prev = close.shift(1)
    tr = pd.concat([high-low, (high-prev).abs(), (low-prev).abs()], axis=1).max(axis=1, skipna=False)
    return tr.rolling(ATR_PERIOD, min_periods=ATR_PERIOD).mean()


def buffered_signals(history, k: float):
    base = generate_signals(history)
    if k == 0 or base.bars.empty:
        return base
    bars = base.bars
    close, ma = _num(bars.Close), _num(bars.MA60)
    atr = atr14(bars)
    upper, lower = ma + k*atr, ma - k*atr
    ok = bars.signal_valid.eq(True) & atr.notna() & atr.shift(1).notna()
    buy = ok & close.shift(1).le(upper.shift(1)) & close.gt(upper)
    sell = ok & close.shift(1).ge(lower.shift(1)) & close.lt(lower)
    result = deepcopy(base)
    result.bars['signal'] = None
    result.bars.loc[buy, 'signal'] = 'BUY'
    result.bars.loc[sell, 'signal'] = 'SELL'
    result.bars['ATR14'] = atr
    signals = []
    for i, stamp in enumerate(bars.index):
        kind = result.bars.signal.iloc[i]
        if kind is None:
            continue
        end = pd.Timestamp(bars.bar_end.iloc[i]).isoformat()
        signals.append(dict(symbol=history.symbol, bar_start=stamp.isoformat(), bar_end=end,
            signal_time=end, close=float(close.iloc[i]), ma60=float(ma.iloc[i]), signal_type=kind,
            atr14=float(atr.iloc[i]), band=float(upper.iloc[i] if kind == 'BUY' else lower.iloc[i]), k=k))
    result.signals = signals
    result.metadata.update(buy_rule=f'Close crosses above MA60+{k}*ATR14', sell_rule=f'Close crosses below MA60-{k}*ATR14',
                           atr='SMA14 of 60m True Range, NaN if any invalid bar in window')
    return result


@contextmanager
def patched(k):
    original = _runner.generate_signals
    _runner.generate_signals = lambda history: buffered_signals(history, k)
    try:
        yield
    finally:
        _runner.generate_signals = original


def run_buffered_backtest(data, *, k, config=None):
    with patched(k):
        result = _runner.run_backtest(data, config=config)
    result.metadata['experiment'] = f'ATR hysteresis k={k}'
    return result
