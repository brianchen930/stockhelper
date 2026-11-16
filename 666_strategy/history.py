"""Pure, causal validation of Taiwan regular-session Yahoo 60m bars."""
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
import math
from typing import Iterable
from zoneinfo import ZoneInfo

import pandas as pd

TAIPEI = ZoneInfo('Asia/Taipei')
OHLCV = ('Open', 'High', 'Low', 'Close', 'Volume')
MA_WINDOW = 60
BAR_HOURS = (9, 10, 11, 12, 13)


def local_time(value: datetime | pd.Timestamp) -> pd.Timestamp:
    stamp = pd.Timestamp(value)
    if pd.isna(stamp) or stamp.tzinfo is None:
        raise ValueError('as_of must be a valid timezone-aware timestamp')
    return stamp.tz_convert(TAIPEI)


def aware_index(frame: pd.DataFrame) -> pd.DatetimeIndex:
    if not isinstance(frame.index, pd.DatetimeIndex) or frame.index.hasnans:
        raise ValueError('A DatetimeIndex without NaT is required')
    if frame.index.tz is None:
        raise ValueError('Timezone-naive source timestamps are ambiguous; refusing to guess')
    return frame.index.tz_convert(TAIPEI)


@dataclass(frozen=True)
class SessionCalendar:
    """Explicit sessions and coverage, injectable without network for replay.

    The caller asserts complete session coverage over the declared range,
    including exceptional closures. Observed quote dates alone cannot establish
    that a missing whole session was a holiday.
    """
    sessions: tuple[date, ...]
    start_date: date
    through_date: date
    source: str = 'caller_supplied'

    def __post_init__(self):
        if self.start_date > self.through_date:
            raise ValueError('Calendar coverage is reversed')
        if tuple(sorted(set(self.sessions))) != self.sessions:
            raise ValueError('Calendar sessions must be sorted and unique')
        if any(type(day) is not date or not self.start_date <= day <= self.through_date
               for day in self.sessions):
            raise ValueError('Calendar sessions must be dates inside declared coverage')

def finite(value):
    try:
        return not isinstance(value, bool) and math.isfinite(float(value))
    except (TypeError, ValueError, OverflowError):
        return False


def empty_bars():
    frame = pd.DataFrame(columns=[*OHLCV, 'bar_end', 'duration_minutes',
                                  'is_short_session_bar', 'is_valid', 'MA60', 'ma60_valid'])
    frame.index = pd.DatetimeIndex([], tz=TAIPEI, name='bar_start')
    return frame


def json_value(value):
    if value is None or value is pd.NA or value is pd.NaT:
        return None
    if isinstance(value, (datetime, date, pd.Timestamp)):
        return value.isoformat()
    if hasattr(value, 'item'):
        value = value.item()
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


@dataclass
class HistoryResult:
    symbol: str
    status: str
    bars: pd.DataFrame = field(default_factory=empty_bars)
    metadata: dict = field(default_factory=dict)
    issues: list[dict] = field(default_factory=list)

    @property
    def ma60(self) -> float | None:
        if self.status != 'OK' or self.bars.empty or not self.bars.iloc[-1].ma60_valid:
            return None
        return float(self.bars.iloc[-1].MA60)

    def recent(self, count=10):
        """Presentation filter only: gaps remain in the calculation grid."""
        return self.bars.loc[self.bars.is_valid.eq(True)].tail(count).copy()

    @staticmethod
    def records(frame):
        return [dict(bar_start=stamp.isoformat(), **{key: json_value(value)
                    for key, value in row.items()}) for stamp, row in frame.iterrows()]

    def to_dict(self, *, include_bars=True):
        result = dict(symbol=self.symbol, status=self.status, ma60=self.ma60,
                      metadata=self.metadata, issues=self.issues,
                      recent_10=self.records(self.recent()))
        if include_bars:
            result['bars'] = self.records(self.bars)
        return result


def expected_bars(sessions: Iterable[date], start, cutoff, include_short_bar):
    rows = []
    for day in sessions:
        for hour in BAR_HOURS:
            beginning = pd.Timestamp(datetime.combine(day, time(hour), TAIPEI))
            end = min(beginning + timedelta(hours=1),
                      pd.Timestamp(datetime.combine(day, time(13, 30), TAIPEI)))
            if beginning < start or end > cutoff or (hour == 13 and not include_short_bar):
                continue
            rows.append((beginning, end, int((end - beginning).total_seconds() / 60)))
    return rows


def analyze_history(history: pd.DataFrame, *, symbol: str, calendar: SessionCalendar,
                    request_start: datetime, as_of: datetime,
                    include_short_bar: bool = True) -> HistoryResult:
    """Return SMA60 only for 60 consecutive valid, completed expected bars.

    13:00-13:30 is one truncated 60m-series bar by default. Missing/invalid
    bars occupy their expected slot and invalidate every window containing
    them. No fill, interpolation, resampling, daily MA, or price repair.
    Future/forming values cannot affect historical MA values or window validity.
    """
    cutoff, start = local_time(as_of), local_time(request_start)
    if start > cutoff:
        raise ValueError('request_start must not follow as_of')
    if type(include_short_bar) is not bool:
        raise ValueError('include_short_bar must be bool')
    meta = dict(source='Yahoo Finance', interval='60m', timezone='Asia/Taipei',
                as_of=cutoff.isoformat(), request_start=start.isoformat(),
                auto_adjust=False, prepost=False, repair=False,
                ma_definition='SMA of 60 consecutive valid completed 60m-series closes',
                include_short_session_bar=include_short_bar,
                calendar_source=calendar.source,
                calendar_coverage_start=calendar.start_date.isoformat(),
                calendar_coverage_end=calendar.through_date.isoformat())
    result = HistoryResult(symbol=symbol, status='INVALID_DATA', metadata=meta)
    if calendar.start_date > start.date() or calendar.through_date < cutoff.date():
        result.status = 'CALENDAR_ERROR'
        result.issues.append(dict(code='CALENDAR_COVERAGE_INSUFFICIENT'))
        return result
    if not isinstance(history, pd.DataFrame) or history.empty:
        result.status = 'SOURCE_ERROR'
        result.issues.append(dict(code='EMPTY_HISTORY', message='No intraday data returned'))
        return result
    try:
        index = aware_index(history)
    except ValueError as error:
        result.issues.append(dict(code='INVALID_TIMESTAMPS', message=str(error)))
        return result
    if isinstance(history.columns, pd.MultiIndex) or not history.columns.is_unique:
        result.issues.append(dict(code='AMBIGUOUS_COLUMNS'))
        return result
    # Slice before checking values, duplicates or calendar membership.
    data = history.copy(deep=True)
    data.index = index
    data = data.loc[(index >= start) & (index <= cutoff)].sort_index()
    meta.update(raw_rows=len(data), raw_start=None if data.empty else data.index.min().isoformat(),
                raw_end=None if data.empty else data.index.max().isoformat(),
                excluded_future_rows=int((index > cutoff).sum()))
    closed, excluded_open, excluded_short, excluded_outside, excluded_closed = [], 0, 0, 0, 0
    fatal = False
    for stamp in data.index:
        clock = stamp.time()
        if not time(9) <= clock < time(13, 30):
            excluded_outside += 1
            continue
        if clock.minute or clock.second or clock.microsecond or stamp.nanosecond:
            result.issues.append(dict(code='MISALIGNED_BAR', time=stamp.isoformat()))
            fatal = True
            continue
        end = min(stamp + timedelta(hours=1), stamp.normalize() + timedelta(hours=13, minutes=30))
        if end > cutoff:
            excluded_open += 1
            continue
        if stamp.date() not in calendar.sessions:
            rows = data.loc[[stamp]]
            empty_prices = all(not finite(value) for value in rows.reindex(columns=list(OHLCV[:4])).to_numpy().flat)
            zero_volumes = all(not finite(value) or float(value) == 0 for value in rows.get('Volume', []))
            if empty_prices and zero_volumes:
                excluded_closed += 1
                continue  # Only an explicitly closed session can discard placeholders.
            result.issues.append(dict(code='UNCONFIRMED_SESSION', time=stamp.isoformat()))
            fatal = True
            continue
        if stamp.hour == 13 and not include_short_bar:
            excluded_short += 1
            continue
        closed.append(stamp)
    meta.update(excluded_open_rows=excluded_open, excluded_short_rows=excluded_short,
                excluded_out_of_session_rows=excluded_outside,
                excluded_closed_session_rows=excluded_closed)
    if fatal:
        return result
    data = data.loc[data.index.isin(closed)]
    if not data.index.is_unique:
        result.issues.append(dict(code='DUPLICATE_BARS', times=[stamp.isoformat()
            for stamp in data.index[data.index.duplicated(keep=False)].unique()]))
        return result
    schedule = expected_bars(calendar.sessions, start, cutoff, include_short_bar)
    grid = pd.DatetimeIndex([row[0] for row in schedule], tz=TAIPEI, name='bar_start')
    if not len(grid):
        result.status = 'INSUFFICIENT_DATA'
        result.issues.append(dict(code='NO_COMPLETED_EXPECTED_BARS'))
        return result
    bars = data.reindex(index=grid, columns=list(OHLCV)).copy()
    for column in OHLCV:
        bars[column] = bars[column].map(lambda value: float(value) if finite(value) else float('nan'))
    valid = bars.notna().all(axis=1)
    valid &= bars[list(OHLCV[:4])].gt(0).all(axis=1) & bars.Volume.ge(0)
    valid &= bars.Low.le(bars[['Open', 'Close']].min(axis=1))
    valid &= bars.High.ge(bars[['Open', 'Close']].max(axis=1)) & bars.High.ge(bars.Low)
    missing = ~grid.isin(data.index)
    for stamp, absent, good in zip(grid, missing, valid):
        if not good:
            result.issues.append(dict(code='MISSING_BAR' if absent else 'INVALID_OHLCV',
                                      time=stamp.isoformat()))
    bars['bar_end'] = [row[1] for row in schedule]
    bars['duration_minutes'] = [row[2] for row in schedule]
    bars['is_short_session_bar'] = bars.duration_minutes.lt(60)
    bars['is_valid'] = valid
    closes = bars.Close.where(valid)
    bars['MA60'] = closes.rolling(MA_WINDOW, min_periods=MA_WINDOW).mean()
    bars['ma60_valid'] = bars.MA60.map(finite)
    bars['MA60'] = bars.MA60.where(bars.ma60_valid)
    result.bars = bars
    # A recovered window may be valid despite older, explicitly reported gaps.
    tail = bars.tail(MA_WINDOW)
    if not tail.is_valid.all():
        result.status = 'INCOMPLETE_DATA'
    elif len(tail) < MA_WINDOW:
        result.status = 'INSUFFICIENT_DATA'
        result.issues.append(dict(code='MA60_WARMUP', valid_count=int(valid.sum()), required=MA_WINDOW))
    elif not bars.iloc[-1].ma60_valid:
        result.status = 'INVALID_DATA'
        result.issues.append(dict(code='MA60_CALCULATION_NONFINITE'))
    else:
        result.status = 'OK'
    meta.update(expected_closed_bars=len(bars), valid_closed_bars=int(valid.sum()),
                missing_closed_bars=int(missing.sum()),
                invalid_closed_bars=int((~valid & ~missing).sum()),
                valid_ma60_count=int(bars.ma60_valid.sum()),
                completed_start=bars.index.min().isoformat(), completed_end=bars.bar_end.max().isoformat(),
                latest_expected_bar=bars.index[-1].isoformat(),
                returned_session_count=len({stamp.date() for stamp in data.index}),
                expected_session_count=len({stamp.date() for stamp in grid}))
    return result
