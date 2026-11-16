"""Historical IO boundary; Yahoo defaults preserved, with optional Fugle IO."""
from datetime import datetime, time, timedelta
import re
import warnings

import pandas as pd

from .history import HistoryResult, SessionCalendar, TAIPEI, analyze_history, local_time
from .calendar import bundled_calendar, validate_yahoo_reference


class YFinanceProvider:
    def __init__(self, *, session=None, ticker_factory=None, timeout=15):
        if ticker_factory is None:
            import yfinance as yf
            ticker_factory = yf.Ticker
        self.session, self.ticker_factory, self.timeout = session, ticker_factory, timeout

    def history(self, symbol, *, start, end, interval):
        options = {} if self.session is None else dict(session=self.session)
        ticker = self.ticker_factory(symbol, **options)
        with warnings.catch_warnings():
            warnings.filterwarnings('ignore', message="'raise_errors' deprecated.*", category=DeprecationWarning)
            return ticker.history(start=start, end=end, interval=interval,
                auto_adjust=False, back_adjust=False, prepost=False, actions=False,
                repair=False, keepna=True, rounding=False, timeout=self.timeout,
                raise_errors=True)


def fetch_history(symbol, *, as_of=None, lookback_days=60, include_short_bar=True,
                  provider=None, calendar=None):
    """Fetch enough default history for SMA60 and ten display rows.

    Explicit .TW/.TWO symbols avoid the existing resolver's daily downloads and
    state. A caller can inject a provider and/or verified SessionCalendar.
    API exceptions and empty responses produce a result with no valid MA60.
    """
    symbol = str(symbol).strip().upper()
    if not re.fullmatch(r'[0-9A-Z]+\.(TW|TWO)', symbol):
        raise ValueError('Use an explicit Taiwan Yahoo symbol, e.g. 2330.TW or 6488.TWO')
    if type(lookback_days) is not int or lookback_days < 1:
        raise ValueError('lookback_days must be a positive integer')
    cutoff = local_time(as_of if as_of is not None else datetime.now(TAIPEI))
    fugle = getattr(provider, 'source_id', 'yahoo') == 'fugle'
    first_day = cutoff.date() - timedelta(days=lookback_days)
    start = datetime.combine(first_day, time(), TAIPEI)
    end = datetime.combine(cutoff.date() + timedelta(days=1), time(), TAIPEI)
    request = dict(source='Fugle' if fugle else 'Yahoo Finance', interval='60m', timezone='Asia/Taipei',
                   as_of=cutoff.isoformat(), request_start=start.isoformat(),
                   request_end_exclusive=end.isoformat(), lookback_days=lookback_days,
                   include_short_session_bar=include_short_bar)
    try:
        provider = provider or YFinanceProvider()
        if hasattr(provider, 'as_of') and cutoff > provider.as_of:
            raise ValueError('SNAPSHOT_AS_OF')
        history = provider.history(symbol, start=start, end=end, interval='60m')
    except Exception as error:
        return HistoryResult(symbol, 'SOURCE_ERROR', metadata=request,
            issues=[dict(code='FUGLE_HISTORY_FAILED' if fugle else 'YAHOO_HISTORY_FAILED',
                         error_type=type(error).__name__, message='Fugle history failed' if fugle else str(error))])
    if not isinstance(history, pd.DataFrame) or history.empty:
        return HistoryResult(symbol, 'SOURCE_ERROR', metadata=request,
            issues=[dict(code='EMPTY_HISTORY', message='No 60m history returned')])
    if calendar is None:
        try:
            calendar, calendar_metadata = bundled_calendar(first_day, cutoff.date())
            if fugle:
                calendar_metadata.update(reference='Verified bundled calendar; no Yahoo request')
            else:
                daily = provider.history('^TWII', start=start, end=end, interval='1d')
                calendar_metadata.update(validate_yahoo_reference(daily, calendar, cutoff))
        except Exception as error:
            return HistoryResult(symbol, 'CALENDAR_ERROR', metadata=request,
                issues=[dict(code='YAHOO_SESSION_REFERENCE_FAILED', error_type=type(error).__name__, message=str(error))])
    else:
        calendar_metadata = {}
    result = analyze_history(history, symbol=symbol, calendar=calendar,
        request_start=start, as_of=cutoff, include_short_bar=include_short_bar)
    result.metadata.update(request_end_exclusive=end.isoformat(), lookback_days=lookback_days)
    result.metadata.update(calendar_metadata)
    if fugle:
        result.metadata.update(provider.metadata, interval='60m')
    return result
