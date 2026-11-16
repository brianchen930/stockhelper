"""Strict full-window data preparation; no truncation, filling or app imports."""
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
import hashlib
import json
from pathlib import Path
import re

import pandas as pd

from ..calendar import bundled_calendar, validate_yahoo_reference
from ..daily import DailyTrendResult, analyze_daily_trend, session_close
from ..history import HistoryResult, SessionCalendar, TAIPEI, analyze_history, local_time
from ..provider import YFinanceProvider


class BacktestDataError(ValueError):
    def __init__(self, code, message, **details):
        self.code, self.details = code, details
        super().__init__(f'{code}: {message}' + (f' ({json.dumps(details, ensure_ascii=False)})' if details else ''))


def dates(start, end, *, now=None):
    start, end = date.fromisoformat(str(start)), date.fromisoformat(str(end))
    if start > end:
        raise BacktestDataError('INVALID_RANGE', '開始日期不可晚於結束日期')
    now = local_time(now if now is not None else datetime.now(TAIPEI))
    if session_close(end) > now:
        raise BacktestDataError('UNFINISHED_RANGE', '結束日期必須已過當日收盤時間')
    return start, end


@dataclass(frozen=True)
class DataWindow:
    calendar: SessionCalendar
    sessions: tuple[date, ...]
    minute_start: pd.Timestamp
    daily_start: pd.Timestamp
    cutoff: pd.Timestamp


def data_window(start, end, *, calendar=None):
    if calendar is None:
        # Try a generous warmup, then the known current year if an unnecessary
        # earlier year is not bundled. Never invent holidays in unknown years.
        try:
            calendar, _ = bundled_calendar(start - timedelta(days=240), end)
        except ValueError:
            try:
                calendar, _ = bundled_calendar(start.replace(month=1, day=1), end)
            except ValueError as error:
                raise BacktestDataError('CALENDAR_UNAVAILABLE', str(error)) from error
    if calendar.start_date > start or calendar.through_date < end:
        raise BacktestDataError('CALENDAR_COVERAGE', '日曆未涵蓋完整指定區間')
    sessions = tuple(day for day in calendar.sessions if start <= day <= end)
    if not sessions:
        raise BacktestDataError('NO_TRADING_SESSIONS', '指定區間沒有已確認交易日')
    prior = [day for day in calendar.sessions if day < sessions[0]]
    if len(prior) < 120:
        raise BacktestDataError('WARMUP_CALENDAR_INSUFFICIENT', '需要回測前 120 個已確認交易日',
                                available=len(prior), required=120)
    return DataWindow(calendar, sessions,
        local_time(datetime.combine(prior[-12], time(9), TAIPEI)),
        local_time(datetime.combine(prior[-120], time(), TAIPEI)), session_close(end))


@dataclass
class BacktestData:
    symbol: str
    start: date
    end: date
    window: DataWindow
    history: HistoryResult
    daily: DailyTrendResult
    metadata: dict


def prepare_data(raw_60m, raw_daily, *, symbol, start, end, calendar=None, now=None):
    start, end = dates(start, end, now=now)
    window = data_window(start, end, calendar=calendar)
    history = analyze_history(raw_60m, symbol=symbol, calendar=window.calendar,
        request_start=window.minute_start, as_of=window.cutoff)
    expected = 60 + 5 * len(window.sessions)
    if (history.status != 'OK' or len(history.bars) != expected
            or not history.bars.is_valid.all()):
        raise BacktestDataError('INCOMPLETE_60M', '60分K 暖身或指定區間不完整，停止回測',
            required_start=window.minute_start.isoformat(), required_end=window.cutoff.isoformat(),
            expected_bars=expected, returned_grid_bars=len(history.bars),
            status=history.status, issues=history.issues[:12])
    if not history.bars.ma60_valid.iloc[59:].all():
        raise BacktestDataError('INSUFFICIENT_60M_WARMUP', 'MA60 或相鄰訊號比較沒有完整暖身')
    daily = analyze_daily_trend(raw_daily, symbol=symbol, calendar=window.calendar,
        request_start=window.daily_start, as_of=window.cutoff)
    first_open = local_time(datetime.combine(window.sessions[0], time(9), TAIPEI))
    # UNKNOWN remains meaningful in the decision library. An actual research run
    # fails explicitly on unavailable data rather than silently omitting trades.
    checkpoints = [first_open, *list(daily.bars.get('daily_close_time', []))]
    unknown = [daily.snapshot_at(at) for at in checkpoints
               if at >= first_open and daily.snapshot_at(at)['status'] == 'UNKNOWN']
    if (not daily.usable or daily.bars.empty or not daily.bars.is_valid.all() or unknown):
        raise BacktestDataError('INCOMPLETE_DAILY', '日線暖身／指定區間資料不足或 UNKNOWN，停止回測',
            required_start=window.daily_start.isoformat(), required_end=window.cutoff.isoformat(),
            issues=daily.issues[:12], unknown=unknown[:3])
    return BacktestData(symbol, start, end, window, history, daily, dict(
        minute_warmup_bars=60, daily_warmup_sessions=120, expected_run_bars=5*len(window.sessions),
        minute_request_start=window.minute_start.isoformat(), daily_request_start=window.daily_start.isoformat(),
        as_of=window.cutoff.isoformat(), source='Yahoo Finance', source_mode='caller_supplied',
        unknown_policy='fail explicitly; no partial backtest', auto_adjust=False,
        data_quality=dict(validated_60m_bars=len(history.bars), validated_daily_closes=len(daily.bars),
            source_60m_start=history.metadata['raw_start'], source_60m_end=history.metadata['raw_end'],
            calendar_source=window.calendar.source, daily_issues=daily.issues)))


class SnapshotProvider:
    """Replay an exact saved download; availability is still fully validated."""
    def __init__(self, path):
        path = Path(path)
        saved = json.loads(path.read_text(encoding='utf-8'))
        self.as_of = local_time(saved['as_of'])
        self.metadata = dict(source_mode='saved_yahoo_snapshot', source_file=str(path),
                             source_sha256=hashlib.sha256(path.read_bytes()).hexdigest())
        self.frames = {}
        for item in saved['source_snapshots']:
            if not item['rows']:
                continue
            frame = pd.DataFrame(item['rows']).set_index('bar_start')
            frame.index = pd.DatetimeIndex(pd.to_datetime(frame.index, utc=True)).tz_convert(TAIPEI)
            self.frames[item['symbol'], item['interval']] = frame

    def history(self, symbol, *, start, end, interval):
        frame = self.frames.get((symbol, interval))
        return pd.DataFrame() if frame is None else frame.copy(deep=True)


def fetch_backtest_data(symbol, start, end, *, provider=None, calendar=None, now=None):
    start, end = dates(start, end, now=now)
    symbol = str(symbol).strip().upper()
    if not re.fullmatch(r'[0-9A-Z]+(?:\.(?:TW|TWO))?', symbol):
        raise BacktestDataError('INVALID_SYMBOL', '請輸入股票代號，例如 2330、2330.TW 或 6488.TWO')
    provider = provider or YFinanceProvider()
    fugle = getattr(provider, 'source_id', 'yahoo') == 'fugle'
    snapshot = isinstance(provider, SnapshotProvider) or (fugle and hasattr(provider, 'as_of'))
    if calendar is None and fugle:
        calendar = getattr(provider, 'calendar', None)
    instant = local_time(now if now is not None else datetime.now(TAIPEI))
    if not snapshot and not fugle and session_close(start) < instant - timedelta(days=730):
        raise BacktestDataError('YAHOO_60M_RANGE', '指定開始日已超過目前保守的730日查詢界線；不縮短區間')
    window = data_window(start, end, calendar=calendar)
    if snapshot:
        if window.cutoff > provider.as_of:
            raise BacktestDataError('SNAPSHOT_AS_OF', '快照尚未涵蓋指定結束日期', as_of=provider.as_of.isoformat())
    elif not fugle and window.minute_start < instant - timedelta(days=730):
        raise BacktestDataError('YAHOO_60M_RANGE', '含暖身的60分K區間超過目前保守的730日查詢界線；不縮短區間',
            required_start=window.minute_start.isoformat(), checked_at=instant.isoformat())
    request_end = local_time(datetime.combine(end + timedelta(days=1), time(), TAIPEI))
    candidates = [symbol] if '.' in symbol else ([symbol+'.TW'] if fugle else [symbol+'.TW', symbol+'.TWO'])
    try:
        for resolved in candidates:
            minute = provider.history(resolved, start=window.minute_start, end=request_end, interval='60m')
            if isinstance(minute, pd.DataFrame) and not minute.empty:
                break
        else:
            if fugle:
                raise BacktestDataError('FUGLE_60M_UNAVAILABLE', 'Fugle 未提供所需60分K；不縮短或改用其他來源')
            raise BacktestDataError('YAHOO_60M_UNAVAILABLE', 'Yahoo 未提供所需60分K；不縮短或改用日K')
        daily = provider.history(resolved, start=window.daily_start, end=request_end, interval='1d')
        if fugle:
            reference_meta = dict(reference='Verified session calendar; native stock 60m and daily grids validated independently')
        elif calendar is None:
            reference = provider.history('^TWII', start=window.minute_start, end=request_end, interval='1d')
            reference_calendar = SessionCalendar(
                tuple(d for d in window.calendar.sessions if d >= window.minute_start.date()),
                window.minute_start.date(), end, window.calendar.source)
            reference_meta = validate_yahoo_reference(reference, reference_calendar, window.cutoff)
        else:
            reference_meta = dict(reference='caller supplied verified calendar')
    except BacktestDataError:
        raise
    except Exception as error:
        if fugle:
            # Even an injected client must not expose arbitrary exception text.
            from ..fugle_client import ProbeError
            code = str(error) if isinstance(error, ProbeError) else 'FUGLE_REQUEST_FAILED'
            raise BacktestDataError('FUGLE_SOURCE_ERROR', code, error_type=type(error).__name__) from None
        raise BacktestDataError('YAHOO_SOURCE_ERROR', str(error), error_type=type(error).__name__) from error
    result = prepare_data(minute, daily, symbol=resolved, start=start, end=end,
                          calendar=window.calendar, now=instant)
    result.metadata.update(getattr(provider, 'metadata', dict(source_mode='live_yahoo')))
    result.metadata.update(reference_meta)
    if fugle:
        result.history.metadata.update(provider.metadata, interval='60m')
        result.daily.metadata.update(provider.metadata, interval='1d')
    else:
        result.metadata['yahoo_60m_policy'] = (
            '730-day preflight for live requests; exact returned grid is authoritative; '
            'Yahoo may impose tighter limits, and any shortfall fails')
    return result
