"""Isolated experiment: daily "high-level volume blow-off" exit.

Production signals/decisions/runner are NOT modified. The unchanged
`backtest.runner.run_backtest` runs with `evaluate_decision` wrapped (in both
runner and decisions namespaces, so confirm_execution sees the same rules).

Exit signal on completed daily bar d (known at 13:30 of d, executed at the
next session 09:00 Open, full position), definitions confirmed by the user:
    high_level = Close > 1.10*MA20  or  Close >= 0.97*max(High of last 20 sessions)
    volume_spike = Volume >= 2 * mean(Volume of the previous 20 sessions)
    long_upper_shadow = High-max(Open,Close) >= 0.5*(High-Low)
    long_black = Close < Open and Open-Close >= 4% of previous Close
    signal = high_level and volume_spike and (long_upper_shadow or long_black)
Option `daily_exit=False` ignores the original daily NON_BULL EXIT while holding
(BUY still requires daily BULL; raw 60m SELL is unchanged).
"""
from contextlib import contextmanager
import json
from pathlib import Path

import pandas as pd

from .. import decisions as _decisions
from ..backtest import runner as _runner
from ..decisions import TradeDecision, executable_time
from ..history import local_time

TAG = 'VOLTOP'


def daily_ohlcv(snapshot_path, symbol, valid_dates):
    payload = json.loads(Path(snapshot_path).read_text(encoding='utf-8'))
    rows = next(s['rows'] for s in payload['source_snapshots'] if s['symbol'] == symbol and s['interval'] == '1d')
    frame = pd.DataFrame(rows)
    frame['date'] = pd.to_datetime(frame.bar_start.str[:10]).dt.date
    frame = frame.loc[frame.date.isin(valid_dates)].set_index('date').sort_index()
    for column in ('Open', 'High', 'Low', 'Close', 'Volume'):
        frame[column] = pd.to_numeric(frame[column], errors='coerce')
    return frame[['Open', 'High', 'Low', 'Close', 'Volume']]


def volume_top_signals(daily: pd.DataFrame) -> pd.DataFrame:
    d = daily.copy()
    d['ma20'] = d.Close.rolling(20, min_periods=20).mean()
    d['high20'] = d.High.rolling(20, min_periods=20).max()
    d['avg_vol20_prev'] = d.Volume.shift(1).rolling(20, min_periods=20).mean()
    d['prev_close'] = d.Close.shift(1)
    rng = d.High - d.Low
    d['high_level'] = (d.Close > 1.10*d.ma20) | (d.Close >= 0.97*d.high20)
    d['volume_spike'] = d.Volume >= 2*d.avg_vol20_prev
    d['long_upper_shadow'] = (rng > 0) & ((d.High - d[['Open', 'Close']].max(axis=1)) >= 0.5*rng)
    d['long_black'] = (d.Close < d.Open) & ((d.Open - d.Close) >= 0.04*d.prev_close)
    d['signal'] = d.high_level & d.volume_spike & (d.long_upper_shadow | d.long_black)
    return d


class _MaskedDaily:
    """Daily trend view that hides NON_BULL from the holding-exit check only."""
    def __init__(self, daily):
        self._daily = daily

    def __getattr__(self, name):
        return getattr(self._daily, name)

    def snapshot_at(self, at):
        snap = self._daily.snapshot_at(at)
        if snap.get('status') == 'NON_BULL':
            snap = dict(snap, status='NON_BULL_IGNORED_WHILE_HOLDING')
        return snap


@contextmanager
def patched(signal_dates, *, daily_exit=True, volume_exit=True, log=None):
    original = _decisions.evaluate_decision

    def wrapped(state, *, at, daily, calendar, signal=None):
        view = daily if daily_exit or not state.holding else _MaskedDaily(daily)
        result = original(state, at=at, daily=view, calendar=calendar, signal=signal)
        moment = local_time(at)
        new = result.state
        if (volume_exit and new.holding and (moment.hour, moment.minute) == (13, 30)
                and moment.date() in signal_dates and (new.pending is None or new.pending.action != 'EXIT')):
            decision = TradeDecision(f'{new.symbol}:EXIT:{TAG}:{moment.isoformat()}', new.symbol, 'EXIT',
                'VOLUME_TOP_EXIT', moment.isoformat(), executable_time(moment, calendar),
                signal_time=moment.isoformat(), trend_close_time=result.trend.get('daily_close_time'))
            new = _decisions.replace(new, pending=decision)
            if log is not None:
                log.append(dict(at=moment.isoformat(), replaced=None if result.state.pending is None
                                else result.state.pending.action))
            result = _decisions.DecisionResult(new, decision, result.trend, 'VOLUME_TOP_EXIT')
        return result

    _decisions.evaluate_decision = wrapped
    _runner.evaluate_decision = wrapped
    try:
        yield
    finally:
        _decisions.evaluate_decision = original
        _runner.evaluate_decision = original


def run_variant(data, snapshot_path, *, daily_exit, volume_exit, config=None):
    valid = set(data.daily.bars.index[data.daily.bars.is_valid.eq(True)].date)
    table = volume_top_signals(daily_ohlcv(snapshot_path, data.symbol, valid))
    dates = set(table.index[table.signal.fillna(False)])
    log = []
    with patched(dates, daily_exit=daily_exit, volume_exit=volume_exit, log=log):
        result = _runner.run_backtest(data, config=config)
    result.metadata['experiment'] = dict(daily_exit=daily_exit, volume_exit=volume_exit,
                                         volume_signal_days=len(dates), volume_exit_injections=log)
    return result, table
