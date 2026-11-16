"""Bounded exchange calendar metadata, separate from Yahoo-only price data."""
from datetime import date, datetime, time, timedelta
import json
from pathlib import Path

import pandas as pd

from .history import OHLCV, SessionCalendar, TAIPEI, aware_index, finite


def closed_session_row_kind(row):
    """Classify ONLY after the caller establishes a confirmed closed session.

    Empty placeholders may omit volume, as before. Priced artifacts require an
    explicit finite zero volume; absent/invalid/positive volume is not ignored.
    Open-session zero-volume quotes are outside this exception.
    """
    empty_prices = all(not finite(row.get(key)) for key in OHLCV[:4])
    volume = row.get('Volume')
    if empty_prices and (not finite(volume) or float(volume) == 0):
        return 'EMPTY_PLACEHOLDER'
    if finite(volume) and float(volume) == 0:
        return 'ZERO_VOLUME_QUOTE'
    return None


def bundled_calendar(start_date, through_date):
    """Never extrapolate an unknown year's holiday schedule from weekdays."""
    closed, sources, verified = set(), [], []
    for year in range(start_date.year, through_date.year + 1):
        path = Path(__file__).with_name('calendars') / f'{year}.json'
        if not path.is_file():
            raise ValueError(f'No verified session calendar for {year}; inject SessionCalendar explicitly')
        stored = json.loads(path.read_text(encoding='utf-8'))
        if stored.get('schema') != 'taiwan_cash_sessions_v1' or stored['year'] != year:
            raise ValueError('Invalid bundled session calendar')
        closed.update(date.fromisoformat(day) for day in stored['scheduled_closed_dates'])
        closed.update(date.fromisoformat(row['date']) for row in stored['exceptional_closures'])
        sources.append(stored['scheduled_closures_source'])
        sources.extend(url for row in stored['exceptional_closures'] for url in row['sources'])
        verified.append(stored['verified_at'])
    sessions, day = [], start_date
    while day <= through_date:
        if day.weekday() < 5 and day not in closed:
            sessions.append(day)
        day += timedelta(days=1)
    calendar = SessionCalendar(tuple(sessions), start_date, through_date,
                               'Bundled TWSE holiday schedule + verified exceptional closures')
    return calendar, dict(calendar_sources=list(dict.fromkeys(sources)),
        calendar_verified_at=verified, calendar_limitation=(
            'Emergency closures are not updated automatically. Missing or invalid Yahoo '
            'reference quotes on scheduled sessions fail closed; no holiday is guessed.'))


def validate_yahoo_reference(frame, calendar, cutoff):
    """Cross-check expected closed sessions; null rows never establish holidays."""
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        raise ValueError('Yahoo ^TWII daily session reference is empty')
    index = aware_index(frame)
    if not frame.columns.is_unique or isinstance(frame.columns, pd.MultiIndex) or 'Close' not in frame:
        raise ValueError('Invalid Yahoo session reference columns')
    inside = [calendar.start_date <= stamp.date() <= cutoff.date() for stamp in index]
    data = frame.loc[inside].copy()
    data.index = index[inside]
    dates = list(data.index.date)
    if len(set(dates)) != len(dates):
        raise ValueError('Duplicate session reference dates')
    by_day = dict(zip(dates, (row for _, row in data.iterrows())))
    placeholders, zero_quotes, errors, completed = [], [], [], 0
    for day, row in by_day.items():
        if day not in calendar.sessions:
            kind = closed_session_row_kind(row)
            if kind == 'EMPTY_PLACEHOLDER':
                placeholders.append(day.isoformat())
            elif kind == 'ZERO_VOLUME_QUOTE':
                zero_quotes.append(day.isoformat())
            else:
                errors.append(f'{day}: priced data conflicts with a closed calendar session')
    for day in calendar.sessions:
        if pd.Timestamp(datetime.combine(day, time(13, 30), TAIPEI)) > cutoff:
            continue  # Today's unfinished daily reference need not have a Close.
        completed += 1
        row = by_day.get(day)
        if row is None or not finite(row.get('Close')) or float(row.Close) <= 0:
            errors.append(f'{day}: missing/invalid ^TWII Close on an expected trading session')
    if errors:
        raise ValueError('; '.join(errors))
    return dict(reference_completed_sessions=completed,
                ignored_reference_closed_session_placeholders=placeholders,
                ignored_reference_closed_session_zero_volume_quotes=zero_quotes)
