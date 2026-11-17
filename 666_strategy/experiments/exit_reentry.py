"""Isolated experiment on top of the user's base (daily MA60>MA120, MA60 SELL only).

Production code is untouched: the unchanged run_backtest runs with its
`generate_signals` swapped (signals) and evaluate_decision wrapped to ignore
the daily NON_BULL exit while holding (same as run_ma_bull N2).

Parameters fixed before running:
  exit='raw'     SELL = Close crosses below MA60 (original)
  exit='two_bar' SELL on the 2nd consecutive completed bar with Close < MA60
                 (Close[t-2] >= MA60[t-2], Close[t-1] < MA60[t-1], Close[t] < MA60[t])
  exit='atr'     SELL = Close crosses below MA60 - 0.5*ATR14 (BUY unchanged)
  reentry=True   extra BUY at the 13:30 bar of a day whose completed daily bar
                 turns NON_BULL -> BULL while that bar's Close > MA60
"""
from contextlib import ExitStack
from copy import deepcopy

import pandas as pd

from ..signals import generate_signals
from ..backtest import runner as _runner
from .atr_buffer import atr14, _num
from .volume_top_exit import patched as daily_patched

ATR_K = 0.5


def make_signals(history, daily_bars, *, exit='raw', reentry=False):
    base = generate_signals(history)
    bars = base.bars
    close, ma = _num(bars.Close), _num(bars.MA60)
    valid = bars.signal_valid.eq(True)
    below = close.lt(ma)
    raw = bars.signal
    buy = raw.eq('BUY')
    if exit == 'raw':
        sell = raw.eq('SELL')
    elif exit == 'two_bar':
        sell = valid & valid.shift(1, fill_value=False) & below & below.shift(1, fill_value=False) \
               & close.shift(2).ge(ma.shift(2))
    elif exit == 'atr':
        lower = ma - ATR_K*atr14(bars)
        ok = valid & lower.notna() & lower.shift(1).notna()
        sell = ok & close.shift(1).ge(lower.shift(1)) & close.lt(lower)
    else:
        raise ValueError(exit)
    extra = pd.Series(False, index=bars.index)
    if reentry:
        status = daily_bars.trend_status
        turned = status.eq('BULL') & status.shift(1).eq('NON_BULL')
        turn_days = {stamp.date() for stamp in status.index[turned]}
        ends = pd.to_datetime(bars.bar_end.map(lambda v: pd.Timestamp(v).isoformat()))
        is_last = [(e.hour, e.minute) == (13, 30) for e in ends]
        extra = pd.Series([last and stamp.date() in turn_days for stamp, last in zip(bars.index, is_last)],
                          index=bars.index) & valid & close.gt(ma) & ~buy
    result = deepcopy(base)
    result.bars['signal'] = None
    result.bars.loc[buy | extra, 'signal'] = 'BUY'
    result.bars.loc[sell, 'signal'] = 'SELL'
    signals = []
    for i, stamp in enumerate(bars.index):
        kind = result.bars.signal.iloc[i]
        if kind is None:
            continue
        end = pd.Timestamp(bars.bar_end.iloc[i]).isoformat()
        signals.append(dict(symbol=history.symbol, bar_start=stamp.isoformat(), bar_end=end, signal_time=end,
            close=float(close.iloc[i]), ma60=float(ma.iloc[i]), signal_type=kind,
            origin='DAILY_TURN_REENTRY' if bool(extra.iloc[i]) else exit))
    result.signals = signals
    return result


def run(data, *, exit='raw', reentry=False, config=None):
    original = _runner.generate_signals
    _runner.generate_signals = lambda history: make_signals(history, data.daily.bars, exit=exit, reentry=reentry)
    try:
        with daily_patched(set(), daily_exit=False, volume_exit=False):
            result = _runner.run_backtest(data, config=config)
    finally:
        _runner.generate_signals = original
    result.metadata['experiment'] = dict(exit=exit, reentry=reentry)
    return result
