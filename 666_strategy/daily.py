"""Stock-only, completed daily closes; independent of the 60m SMA series."""
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
import re

import pandas as pd

from .calendar import bundled_calendar, closed_session_row_kind
from .history import HistoryResult, SessionCalendar, TAIPEI, aware_index, finite, local_time
from .provider import YFinanceProvider

CONDITIONS = ('close_above_ma60', 'ma60_above_ma120', 'ma60_rising_5_sessions')


def session_close(day):
    return pd.Timestamp(datetime.combine(day, time(13, 30), TAIPEI))


@dataclass
class DailyTrendResult:
    symbol: str
    as_of: pd.Timestamp
    calendar: SessionCalendar
    bars: pd.DataFrame = field(default_factory=pd.DataFrame)
    issues: list[dict] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)
    usable: bool = True

    def snapshot_at(self, at):
        """Use the latest EXPECTED completed session, never the last good quote."""
        at = local_time(at)
        result = dict(symbol=self.symbol, evaluated_at=at.isoformat(), status='UNKNOWN',
            daily_close_time=None, close=None, daily_ma60=None, daily_ma120=None,
            daily_ma60_5_sessions_ago=None, **{key: None for key in CONDITIONS},
            reason='DAILY_DATA_UNAVAILABLE')
        if at > self.as_of:
            result['reason'] = 'AS_OF_NOT_COVERED'
            return result
        if not self.calendar.start_date <= at.date() <= self.calendar.through_date:
            result['reason'] = 'CALENDAR_NOT_COVERED'
            return result
        completed = [day for day in self.calendar.sessions if session_close(day) <= at]
        if not completed or not self.usable:
            return result
        stamp = pd.Timestamp(datetime.combine(completed[-1], time(), TAIPEI))
        result['daily_close_time'] = session_close(completed[-1]).isoformat()
        # A later calendar/source conflict cannot poison an earlier snapshot.
        if any(issue.get('blocking') and local_time(issue['known_at']) <= at for issue in self.issues):
            result['reason'] = 'DAILY_INPUT_CONFLICT'
            return result
        if stamp not in self.bars.index:
            return result
        row = self.bars.loc[stamp]
        for source, target in [('Close', 'close'), ('daily_ma60', 'daily_ma60'),
                ('daily_ma120', 'daily_ma120'),
                ('daily_ma60_5_sessions_ago', 'daily_ma60_5_sessions_ago')]:
            result[target] = float(row[source]) if finite(row[source]) else None
        result['status'], result['reason'] = row.trend_status, row.trend_issue
        for key in CONDITIONS:
            result[key] = None if pd.isna(row[key]) else bool(row[key])
        return result

    @property
    def status(self):
        return self.snapshot_at(self.as_of)['status']

    def to_dict(self):
        return dict(symbol=self.symbol, status=self.status, as_of=self.as_of.isoformat(),
            latest=self.snapshot_at(self.as_of), bars=HistoryResult.records(self.bars),
            issues=self.issues, metadata=self.metadata)


def analyze_daily_trend(raw, *, symbol, calendar, request_start, as_of):
    """Pure causal calculation on a complete expected-session grid.

    At least 120 consecutive valid daily closes are required. shift(5) counts
    trading-session slots, including gaps, rather than calendar days. No fills,
    intraday resampling, market-index filter, or fallback to an older valid row.
    """
    start, cutoff = local_time(request_start), local_time(as_of)
    if start > cutoff:
        raise ValueError('request_start must not follow as_of')
    result = DailyTrendResult(symbol, cutoff, calendar, metadata=dict(interval='1d',
        source='Yahoo Finance', timezone='Asia/Taipei', request_start=start.isoformat(),
        as_of=cutoff.isoformat(), required_daily_closes=120, slope_sessions=5,
        market_index_filter=False, auto_adjust=False, calendar_source=calendar.source))
    if calendar.start_date > start.date() or calendar.through_date < cutoff.date():
        result.usable = False
        result.issues.append(dict(code='CALENDAR_COVERAGE_INSUFFICIENT'))
        return result
    if not isinstance(raw, pd.DataFrame) or raw.empty:
        result.issues.append(dict(code='EMPTY_DAILY_HISTORY'))
        raw = pd.DataFrame({'Close': []}, index=pd.DatetimeIndex([], tz=TAIPEI))
    try:
        index = aware_index(raw)
        if not raw.columns.is_unique or isinstance(raw.columns, pd.MultiIndex) or 'Close' not in raw:
            raise ValueError('Unique daily Close column required')
        # Exclude uncompleted/future rows BEFORE validating duplicates and values.
        keep = [(stamp >= start and session_close(stamp.date()) <= cutoff) for stamp in index]
        # Retain source volume/OHLC only to classify confirmed-closure artifacts.
        # They never enter the daily Close averages.
        data = raw.loc[keep].copy()
        data.index = index[keep]
        bad_labels = (data.index != data.index.normalize()) | data.index.duplicated(keep=False)
        for stamp in data.index[bad_labels].unique():
            result.issues.append(dict(code='INVALID_DAILY_LABEL', blocking=True,
                known_at=session_close(stamp.date()).isoformat(), time=stamp.isoformat()))
        data = data.loc[~bad_labels]
        excluded = []
        for stamp, row in data.iterrows():
            if stamp.date() not in calendar.sessions:
                kind = closed_session_row_kind(row)
                if kind is not None:
                    excluded.append(stamp)
                    result.issues.append(dict(code='IGNORED_CONFIRMED_CLOSED_DAILY_ROW',
                        kind=kind, blocking=False, time=stamp.isoformat(),
                        known_at=session_close(stamp.date()).isoformat(),
                        close=float(row.Close) if finite(row.Close) else None,
                        volume=float(row.Volume) if finite(row.get('Volume')) else None,
                        calendar_source=calendar.source))
                else:
                    result.issues.append(dict(code='PRICED_DAILY_ON_CLOSED_SESSION', blocking=True,
                        known_at=session_close(stamp.date()).isoformat(), time=stamp.isoformat()))
        data = data.drop(index=excluded).loc[:, ['Close']]
        result.metadata['excluded_confirmed_closed_rows'] = len(excluded)
    except (TypeError, ValueError) as error:
        result.usable = False
        result.issues.append(dict(code='INVALID_DAILY_INPUT', message=str(error)))
        return result
    days = [day for day in calendar.sessions
            if start.date() <= day and session_close(day) <= cutoff]
    grid = pd.DatetimeIndex([datetime.combine(day, time(), TAIPEI) for day in days],
                           tz=TAIPEI, name='daily_session')
    bars = data.reindex(grid).copy()
    bars['Close'] = bars.Close.map(lambda v: float(v) if finite(v) and float(v) > 0 else float('nan'))
    bars['daily_close_time'] = [session_close(day) for day in days]
    bars['is_valid'] = bars.Close.notna()
    bars['daily_ma60'] = bars.Close.rolling(60, min_periods=60).mean()
    bars['daily_ma120'] = bars.Close.rolling(120, min_periods=120).mean()
    bars['daily_ma60_5_sessions_ago'] = bars.daily_ma60.shift(5)
    for key, values, available in [
            (CONDITIONS[0], bars.Close > bars.daily_ma60, bars.Close.notna() & bars.daily_ma60.notna()),
            (CONDITIONS[1], bars.daily_ma60 > bars.daily_ma120,
                bars.daily_ma60.notna() & bars.daily_ma120.notna()),
            (CONDITIONS[2], bars.daily_ma60 > bars.daily_ma60_5_sessions_ago,
                bars.daily_ma60.notna() & bars.daily_ma60_5_sessions_ago.notna())]:
        bars[key] = values.astype('boolean').where(available)
    known = bars[['Close', 'daily_ma60', 'daily_ma120', 'daily_ma60_5_sessions_ago']].notna().all(axis=1)
    bull = bars[list(CONDITIONS)].fillna(False).all(axis=1)
    bars['trend_status'] = 'UNKNOWN'
    bars.loc[known, 'trend_status'] = 'NON_BULL'
    bars.loc[known & bull, 'trend_status'] = 'BULL'
    bars['trend_issue'] = None
    bars.loc[~known, 'trend_issue'] = 'WARMUP_OR_INVALID_DAILY_WINDOW'
    conflicts = [local_time(issue['known_at']) for issue in result.issues if issue.get('blocking')]
    if conflicts:
        affected = bars.daily_close_time.ge(min(conflicts))
        bars.loc[affected, 'trend_status'] = 'UNKNOWN'
        bars.loc[affected, 'trend_issue'] = 'DAILY_INPUT_CONFLICT'
    result.bars = bars
    result.metadata.update(expected_completed_sessions=len(bars), valid_closes=int(bars.is_valid.sum()))
    for stamp in bars.index[~bars.is_valid]:
        result.issues.append(dict(code='MISSING_OR_INVALID_DAILY_CLOSE', time=stamp.isoformat()))
    return result


def fetch_daily_trend(symbol, *, decision_start, as_of=None, provider=None, calendar=None):
    """Fetch 120 sessions BEFORE the decision range, plus the entire range.

    No ^TWII query: the existing first-stage calendar cross-check is unchanged,
    but market price/direction never enters this stock-only filter.
    """
    symbol = str(symbol).strip().upper()
    if not re.fullmatch(r'[0-9A-Z]+\.(TW|TWO)', symbol):
        raise ValueError('Use an explicit Taiwan Yahoo symbol')
    cutoff = local_time(as_of if as_of is not None else datetime.now(TAIPEI))
    beginning = local_time(decision_start)
    if beginning > cutoff:
        raise ValueError('decision_start must not follow as_of')
    try:
        if calendar is None:
            lookback = 200
            while True:
                try:
                    calendar, _ = bundled_calendar(beginning.date() - timedelta(days=lookback), cutoff.date())
                except ValueError:
                    # The day-count estimate may enter an unavailable prior year
                    # even when this verified year already has 120 prior sessions.
                    calendar, _ = bundled_calendar(beginning.date().replace(month=1, day=1), cutoff.date())
                    if sum(day < beginning.date() for day in calendar.sessions) < 120:
                        raise ValueError('Fewer than 120 verified prior sessions; inject a longer SessionCalendar')
                prior = [day for day in calendar.sessions if day < beginning.date()]
                if len(prior) >= 120:
                    break
                lookback += 60
        prior = [day for day in calendar.sessions if day < beginning.date()]
        first = prior[-120] if len(prior) >= 120 else calendar.start_date
        start = datetime.combine(first, time(), TAIPEI)
        end = datetime.combine(cutoff.date() + timedelta(days=1), time(), TAIPEI)
        raw = (provider or YFinanceProvider()).history(symbol, start=start, end=end, interval='1d')
    except Exception as error:
        fallback = calendar or SessionCalendar((), beginning.date(), cutoff.date(), 'unavailable')
        result = DailyTrendResult(symbol, cutoff, fallback, usable=False)
        result.issues.append(dict(code='DAILY_FETCH_FAILED', error_type=type(error).__name__, message=str(error)))
        return result
    result = analyze_daily_trend(raw, symbol=symbol, calendar=calendar, request_start=start, as_of=cutoff)
    result.metadata.update(decision_start=beginning.isoformat(), requested_warmup_sessions=len(prior[-120:]),
                           request_end_exclusive=end.isoformat())
    if len(prior) < 120:
        result.issues.append(dict(code='DAILY_WARMUP_COVERAGE_INSUFFICIENT', available=len(prior), required=120))
    return result
