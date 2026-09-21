"""Taipei session policy. Weekday fallback; explicit exchange closures may be supplied."""
from datetime import date, timedelta

from .config import DEFAULT_CONFIG


def trading_day(day, config=DEFAULT_CONFIG):
    return day.weekday() < 5 and day.isoformat() not in config.closed_dates


def previous_session(day, config=DEFAULT_CONFIG):
    day -= timedelta(days=1)
    while not trading_day(day, config):
        day -= timedelta(days=1)
    return day


def session_dates(now, config=DEFAULT_CONFIG):
    """Query today after close; require today only after the publication grace period."""
    day = now.date()
    minute = now.hour * 60 + now.minute
    if not trading_day(day, config):
        target = expected = previous_session(day, config)
    else:
        previous = previous_session(day, config)
        target = day if minute >= 13 * 60 + 30 else previous
        expected = day if minute >= config.publication_hour * 60 else previous
    return target.isoformat(), expected.isoformat()


def metadata(actual, now, config=DEFAULT_CONFIG):
    target, expected = session_dates(now, config)
    status = 'UNAVAILABLE' if actual is None else 'FRESH' if expected <= actual <= now.date().isoformat() else 'STALE'
    return dict(data_date=actual, actual_data_date=actual, expected_trade_date=expected,
                target_date=target, freshness=status,
                previous_session_context=bool(status == 'FRESH' and trading_day(now.date(), config)
                                              and actual < now.date().isoformat()),
                calendar_policy='weekdays_with_configured_closures')


def usable(context):
    # Legacy callers may omit freshness; explicit stale/unavailable always fails closed.
    return context.get('freshness', 'FRESH') == 'FRESH'
