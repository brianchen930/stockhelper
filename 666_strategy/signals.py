"""Pure Close/SMA60 crossings on the first-stage canonical completed-bar grid."""
from copy import deepcopy
from dataclasses import dataclass, field

import pandas as pd

from .history import HistoryResult, TAIPEI, aware_index, finite, local_time

SIGNAL_COLUMNS = ('signal', 'signal_valid', 'signal_issue', 'is_closed')


@dataclass
class SignalResult:
    symbol: str
    status: str
    history_status: str
    bars: pd.DataFrame
    signals: list[dict] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)
    issues: list[dict] = field(default_factory=list)

    @property
    def latest_signal_valid(self):
        return bool(len(self.bars) and self.bars.iloc[-1].signal_valid)

    @property
    def latest_signal(self):
        if not self.latest_signal_valid or self.bars.iloc[-1].signal is None:
            return None
        return self.signals[-1] if self.signals else None

    def recent(self, count=10):
        # Display filtering occurs after comparisons on the original grid.
        if self.bars.empty:
            return self.bars.copy()
        return self.bars.loc[self.bars.is_valid.eq(True) & self.bars.is_closed].tail(count).copy()

    def to_dict(self, *, include_bars=True):
        result = dict(symbol=self.symbol, status=self.status, history_status=self.history_status,
            latest_signal_valid=self.latest_signal_valid, latest_signal=self.latest_signal,
            signal_count=len(self.signals), buy_count=sum(s['signal_type'] == 'BUY' for s in self.signals),
            sell_count=sum(s['signal_type'] == 'SELL' for s in self.signals),
            signals=deepcopy(self.signals), metadata=deepcopy(self.metadata), issues=deepcopy(self.issues),
            recent_10=HistoryResult.records(self.recent()))
        if include_bars:
            result['bars'] = HistoryResult.records(self.bars)
        return result


def generate_signals(history: HistoryResult) -> SignalResult:
    """Use existing MA60 values; never fetch, recalculate indicators, or fill gaps.

    BUY: Close[t-1] <= MA60[t-1] and Close[t] > MA60[t].
    SELL: Close[t-1] >= MA60[t-1] and Close[t] < MA60[t].
    Both adjacent canonical rows need valid, completed bars and valid MA60.
    Exact comparisons have no tolerance, debounce, position or extra filters.
    A later bad bar never invalidates earlier historical signal decisions.
    """
    if not isinstance(history, HistoryResult):
        raise TypeError('Pass a HistoryResult from fetch_history() or analyze_history()')
    bars = history.bars.copy(deep=True)
    for name, default in zip(SIGNAL_COLUMNS, (None, False, 'UNAVAILABLE', False)):
        bars[name] = default
    result = SignalResult(history.symbol, history.status, history.status, bars,
        metadata=dict(as_of=history.metadata.get('as_of'), interval='60m',
            timezone='Asia/Taipei', ma_source='first_stage_MA60',
            include_short_session_bar=history.metadata.get('include_short_session_bar'),
            buy_rule='Close[t-1] <= MA60[t-1] and Close[t] > MA60[t]',
            sell_rule='Close[t-1] >= MA60[t-1] and Close[t] < MA60[t]',
            signal_time_basis='bar_end', evaluable_pairs=0), issues=deepcopy(history.issues))
    if bars.empty:
        return result
    if history.status not in ('OK', 'INCOMPLETE_DATA', 'INSUFFICIENT_DATA'):
        return result
    required = {'Close', 'MA60', 'bar_end', 'is_valid', 'ma60_valid'}
    try:
        if not bars.columns.is_unique or not required.issubset(bars.columns):
            raise ValueError('Missing or ambiguous first-stage columns')
        index = aware_index(bars)
        if not index.is_unique or not index.is_monotonic_increasing:
            raise ValueError('First-stage grid must be chronological and unique')
        if history.metadata.get('interval') != '60m':
            raise ValueError('Only the first-stage 60m interval is accepted')
        if history.metadata.get('expected_closed_bars') != len(bars):
            raise ValueError('First-stage grid was filtered or has no coverage count')
        cutoff = local_time(history.metadata.get('as_of'))
    except (TypeError, ValueError) as error:
        result.status = 'INVALID_DATA'
        result.issues.append(dict(code='SIGNAL_INPUT_INVALID', message=str(error)))
        result.bars = bars.iloc[:0].copy()
        return result
    bars.index = index
    ends = []
    for value in bars.bar_end:
        try:
            ends.append(local_time(value))
        except (TypeError, ValueError, OverflowError):
            ends.append(pd.NaT)
    ends = pd.Series(pd.DatetimeIndex(ends, tz=TAIPEI), index=index)
    bars['is_closed'] = ends.notna() & ends.le(cutoff) & ends.gt(pd.Series(index, index=index))
    prices = bars.Close.map(lambda v: float(v) if finite(v) else float('nan'))
    averages = bars.MA60.map(lambda v: float(v) if finite(v) else float('nan'))
    valid_bar = bars.is_valid.eq(True) & bars.is_closed & prices.gt(0)
    valid_ma = bars.ma60_valid.eq(True) & averages.gt(0)
    valid_row = valid_bar & valid_ma
    eligible = valid_row & valid_row.shift(1, fill_value=False)
    buy = eligible & prices.shift(1).le(averages.shift(1)) & prices.gt(averages)
    sell = eligible & prices.shift(1).ge(averages.shift(1)) & prices.lt(averages)
    bars['signal_valid'] = eligible
    bars['signal_issue'] = None
    for i in range(len(bars)):
        reason = ('BAR_INVALID_OR_NOT_CLOSED' if not valid_bar.iloc[i] else
                  'MA60_UNAVAILABLE' if not valid_ma.iloc[i] else
                  'PREVIOUS_BAR_UNAVAILABLE' if i == 0 or not valid_bar.iloc[i-1] else
                  'PREVIOUS_MA60_UNAVAILABLE' if not valid_ma.iloc[i-1] else None)
        bars.iat[i, bars.columns.get_loc('signal_issue')] = reason
    bars.loc[buy, 'signal'] = 'BUY'
    bars.loc[sell, 'signal'] = 'SELL'
    for i in range(len(bars)):
        kind = bars.signal.iloc[i]
        if kind is not None:
            result.signals.append(dict(symbol=history.symbol,
                bar_start=index[i].isoformat(), bar_end=ends.iloc[i].isoformat(),
                signal_time=ends.iloc[i].isoformat(), close=float(prices.iloc[i]),
                ma60=float(averages.iloc[i]), signal_type=kind))
    result.metadata['evaluable_pairs'] = int(eligible.sum())
    if history.status == 'OK' and not result.latest_signal_valid:
        result.status = 'INSUFFICIENT_DATA'
    return result
