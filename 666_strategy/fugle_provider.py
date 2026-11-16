"""Independent TWSE/TPEx Fugle history and deterministic snapshot replay.

Native 60m OHLCV is never resampled or changed to match another vendor.
The 13:00 bar completes at 13:30 and includes the source's closing auction.
"""
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re

import pandas as pd

from .calendar import bundled_calendar
from .fugle_client import FugleClient, ProbeError, chunks, normalize
from .history import BAR_HOURS, OHLCV, SessionCalendar, TAIPEI, local_time

SCHEMA = 'fugle_history_snapshot_v1'
POLICY = dict(source='Fugle', timezone='Asia/Taipei', interval='native 60m / daily',
    normalized_volume_unit='shares', adjusted=False,
    minute_source_volume_unit='lots', daily_source_volume_unit='shares',
    native_bar_hours=list(BAR_HOURS), last_bar_start='13:00', last_bar_end='13:30',
    closing_auction='Included in native 13:00 60m; no appended/merged 13:30 bar',
    daily_volume_scope='All daily sessions; not required to equal intraday volume sum')
SCHEMA_V2 = 'fugle_history_snapshot_v2'
POLICY_V2 = dict(POLICY, supported_markets={'.TW': {'exchange': 'TWSE', 'market': 'TSE'},
    '.TWO': {'exchange': 'TPEx', 'market': 'OTC'}},
    market_identity='Explicit per-series identity; TPEx rows also retain source_exchange/source_market',
    exceptional_close_policy='Native 13:00 bar may complete after 13:30; consult closing_time_validation before intraday replay')


def canonical_digest(document):
    return hashlib.sha256(json.dumps(document, sort_keys=True, ensure_ascii=False,
        separators=(',', ':'), allow_nan=False).encode('utf-8')).hexdigest()


def twse_symbol(symbol):
    if not re.fullmatch(r'\d{4}\.TW', symbol):
        raise ProbeError('FUGLE_UNSUPPORTED_SYMBOL')
    return symbol.removesuffix('.TW')


def fugle_symbol(symbol):
    """Resolve explicit exchange suffixes without guessing or market fallback."""
    match = re.fullmatch(r'(\d{4})\.(TW|TWO)', symbol)
    if not match:
        raise ProbeError('FUGLE_UNSUPPORTED_SYMBOL')
    code, suffix = match.groups()
    return (code, 'TWSE', 'TSE') if suffix == 'TW' else (code, 'TPEx', 'OTC')


def checked_rows(rows, *, symbol, interval, start, end):
    """Revalidate saved normalized values against their source timestamps/units."""
    tf = {'60m': '60', '1d': 'D'}.get(interval)
    if tf is None:
        raise ProbeError('FUGLE_UNSUPPORTED_INTERVAL')
    try:
        code, exchange, market = fugle_symbol(symbol)
        for row in rows:
            if exchange == 'TPEx' and (row.get('source_exchange'), row.get('source_market')) != (exchange, market):
                raise ProbeError('FUGLE_SNAPSHOT_MARKET_MISMATCH')
            if any(key in row and row[key] != value for key, value in
                   [('source_exchange', exchange), ('source_market', market)]):
                raise ProbeError('FUGLE_SNAPSHOT_MARKET_MISMATCH')
        payload = dict(symbol=code, exchange=exchange, market=market, timeframe=tf,
            adjusted=False, data=[dict(date=row['source_timestamp'],
                **{name.lower(): row[name] for name in OHLCV[:4]},
                volume=row['source_volume']) for row in rows])
        result = normalize(payload, dict(symbol=payload['symbol'], timeframe=tf, exchange=exchange, market=market,
                                        start=start, end=end))
        originals = {row['bar_start']: row for row in rows}
        if len(originals) != len(rows):
            raise ProbeError('DUPLICATE_TIMESTAMP')
        for row in result:
            original = originals[row['bar_start']]
            if any(original[name] != row[name] for name in (*OHLCV, 'source_volume_unit')):
                raise ProbeError('FUGLE_SNAPSHOT_UNIT_OR_VALUE_MISMATCH')
        return result
    except ProbeError:
        raise
    except (KeyError, TypeError, ValueError):
        raise ProbeError('FUGLE_INVALID_SNAPSHOT_ROWS') from None


def native_frame(rows, interval):
    if not rows:
        return pd.DataFrame()
    frame = pd.DataFrame(rows).set_index('bar_start')
    try:
        # Never let pandas guess a timezone for saved naive timestamps.
        if any(pd.Timestamp(value).tzinfo is None for value in frame.index):
            raise ValueError()
        frame.index = pd.DatetimeIndex(pd.to_datetime(frame.index, utc=True)).tz_convert(TAIPEI)
        if not frame.index.is_unique:
            raise ProbeError('DUPLICATE_TIMESTAMP')
        if interval == '60m':
            if any(stamp.hour not in BAR_HOURS or stamp.minute or stamp.second
                   or stamp.microsecond or stamp.nanosecond for stamp in frame.index):
                raise ProbeError('FUGLE_NON_NATIVE_60M_GRID')
        elif any(stamp != stamp.normalize() for stamp in frame.index):
            raise ProbeError('FUGLE_INVALID_DAILY_TIMESTAMP')
    except (TypeError, ValueError) as error:
        if isinstance(error, ProbeError):
            raise
        raise ProbeError('FUGLE_INVALID_TIMESTAMPS') from None
    frame.index.name = 'bar_start'
    return frame.sort_index()


class FugleProvider:
    """End-exclusive native history for explicit .TW (TSE) / .TWO (OTC).

    A client is created lazily, so importing the package and offline replay
    never read credentials. Each live query is split into <=180 calendar days.
    """
    source_id = 'fugle'

    def __init__(self, *, client=None, environ=None, ca_bundle=None):
        self._client, self._environ, self._ca_bundle = client, environ, ca_bundle
        self.metadata = dict(POLICY, source_mode='live_fugle')
        self.snapshots = []

    def history(self, symbol, *, start, end, interval):
        code, exchange, market = fugle_symbol(symbol)
        tf = {'60m': '60', '1d': 'D'}.get(interval)
        if tf is None:
            raise ProbeError('FUGLE_UNSUPPORTED_INTERVAL')
        start, end = local_time(start), local_time(end)
        if start >= end:
            raise ProbeError('INVALID_REQUEST_RANGE')
        if self._client is None:
            self._client = FugleClient(environ=self._environ, ca_bundle=self._ca_bundle)
        rows = []
        for first, last in chunks(start.date(), (end-pd.Timedelta(nanoseconds=1)).date()):
            spec = dict(symbol=code, timeframe=tf, start=str(first), end=str(last))
            if exchange == 'TPEx':
                spec.update(exchange=exchange, market=market)
            part = self._client.candles(spec)
            if exchange == 'TPEx':
                checked_rows(part, symbol=symbol, interval=interval, start=str(first), end=str(last))
            rows.extend(part)
        frame = native_frame(rows, interval)
        if not frame.empty:
            frame = frame.loc[(frame.index >= start) & (frame.index < end)].copy()
        captured = dict(symbol=symbol, interval=interval,
            received_at=datetime.now(TAIPEI).isoformat(), rows=rows)
        if exchange == 'TPEx':
            captured.update(exchange=exchange, market=market)
            self.metadata.update(exchange=exchange, market=market)
        else:
            self.metadata.pop('exchange', None)
            self.metadata.pop('market', None)
        self.snapshots.append(captured)
        return frame


def create_fugle_snapshot(verification_path, output):
    """Copy only revalidated, successful full-coverage data into a sealed artifact."""
    path = Path(verification_path)
    source_bytes = path.read_bytes()
    report = json.loads(source_bytes)
    if (report.get('schema') != 'fugle_feasibility_v2'
            or report.get('status') != 'NATIVE_COVERAGE_PASSED_REVIEW_COMPARISONS'
            or report.get('normalized_volume_unit') != 'shares'):
        raise ProbeError('FUGLE_UNVERIFIED_SOURCE_ARTIFACT')
    first, last = report['daily_warmup_start'][:10], report['requested_end']
    calendar, _ = bundled_calendar(pd.Timestamp(first).date(), pd.Timestamp(last).date())
    snapshots, requests = {}, []
    for item in report['full_range_results']:
        spec = item['request']
        if item.get('status') != 'FETCHED' or spec['timeframe'] not in ('60', 'D'):
            raise ProbeError('FUGLE_INCOMPLETE_SOURCE_ARTIFACT')
        exchange = spec.get('exchange', 'TWSE')
        market = spec.get('market', 'TSE' if exchange == 'TWSE' else 'OTC')
        if (exchange, market) not in (('TWSE', 'TSE'), ('TPEx', 'OTC')):
            raise ProbeError('FUGLE_UNSUPPORTED_MARKET')
        symbol = spec['symbol']+('.TW' if exchange == 'TWSE' else '.TWO')
        interval = '60m' if spec['timeframe'] == '60' else '1d'
        rows = checked_rows(item['rows'], symbol=symbol, interval=interval,
                            start=spec['start'], end=spec['end'])
        snapshots.setdefault((symbol, interval), []).extend(rows)
        requests.append(dict(symbol=symbol, interval=interval, start=spec['start'],
                             end=spec['end'], rows=len(rows), status='FETCHED'))
    sources = []
    for (symbol, interval), rows in sorted(snapshots.items()):
        native_frame(rows, interval)  # Reject duplicates across request chunks.
        series = dict(symbol=symbol, interval=interval, rows=sorted(rows, key=lambda row: row['bar_start']))
        if symbol.endswith('.TWO'):
            series.update(exchange='TPEx', market='OTC')
        sources.append(series)
    if not sources:
        raise ProbeError('FUGLE_INCOMPLETE_SOURCE_ARTIFACT')
    # Re-run the existing full-window validator instead of trusting a saved
    # success flag. This also verifies both warmup requirements before sealing.
    from .backtest.data import prepare_data
    frames = {(item['symbol'], item['interval']): native_frame(item['rows'], item['interval'])
              for item in sources}
    validation = []
    for symbol in sorted({item['symbol'] for item in sources}):
        if (symbol, '60m') not in frames or (symbol, '1d') not in frames:
            raise ProbeError('FUGLE_INCOMPLETE_SOURCE_ARTIFACT')
        data = prepare_data(frames[symbol, '60m'], frames[symbol, '1d'], symbol=symbol,
            start=report['requested_start'], end=last, calendar=calendar, now=report['generated_at'])
        validation.append(dict(symbol=symbol, status='EXISTING_VALIDATION_PASSED',
            minute_bars=len(data.history.bars), daily_closes=len(data.daily.bars),
            run_sessions=len(data.window.sessions)))
    v2 = any(s['symbol'].endswith('.TWO') for s in sources)
    if v2:
        for series in sources:
            _, exchange, market = fugle_symbol(series['symbol'])
            series.update(exchange=exchange, market=market)
    snapshot = dict(schema=SCHEMA_V2 if v2 else SCHEMA, policy=POLICY_V2 if v2 else POLICY,
        as_of=last+'T13:30:00+08:00',
        provenance=dict(source_file=path.name, source_sha256=hashlib.sha256(source_bytes).hexdigest(),
            received_at=report['generated_at'], requests=requests),
        calendar=dict(sessions=[str(day) for day in calendar.sessions],
            start_date=str(calendar.start_date), through_date=str(calendar.through_date), source=calendar.source),
        source_snapshots=sources, validation=validation)
    if v2:
        snapshot['research_sample'] = report.get('research_sample', {'origin': 'unspecified'})
        snapshot['closing_time_validation'] = report.get('closing_time_validation', {'status': 'NOT_VERIFIED'})
    snapshot['payload_sha256'] = canonical_digest(snapshot)
    output = Path(output)
    if output.resolve() == path.resolve():
        raise ProbeError('FUGLE_SOURCE_OVERWRITE_REFUSED')
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    return output


class FugleSnapshotProvider:
    """Offline-only replay: no credentials, downloads or fallback to other prices."""
    source_id = 'fugle'

    def __init__(self, path):
        path = Path(path)
        raw = path.read_bytes()
        document = json.loads(raw)
        self.schema = document.get('schema')
        policy = POLICY if self.schema == SCHEMA else POLICY_V2 if self.schema == SCHEMA_V2 else None
        if policy is None or document.get('policy') != policy:
            raise ProbeError('FUGLE_INVALID_SNAPSHOT_SCHEMA')
        expected = document.pop('payload_sha256', None)
        if expected != canonical_digest(document):
            raise ProbeError('FUGLE_SNAPSHOT_DIGEST_MISMATCH')
        self.as_of = local_time(document['as_of'])
        cal = document['calendar']
        self.calendar = SessionCalendar(tuple(pd.Timestamp(day).date() for day in cal['sessions']),
            pd.Timestamp(cal['start_date']).date(), pd.Timestamp(cal['through_date']).date(), cal['source'])
        self.metadata = dict(policy, source_mode='saved_fugle_snapshot', source_file=str(path),
            source_sha256=hashlib.sha256(raw).hexdigest(), payload_sha256=expected,
            original_source_sha256=document['provenance']['source_sha256'])
        self.frames = {}
        self.snapshots = document['source_snapshots']
        self.research_sample = document.get('research_sample')
        self.closing_time_validation = document.get('closing_time_validation')
        for item in self.snapshots:
            pair = item['symbol'], item['interval']
            _, exchange, market = fugle_symbol(pair[0])
            if self.schema == SCHEMA and exchange != 'TWSE':
                raise ProbeError('FUGLE_INVALID_SNAPSHOT_SCHEMA')
            if self.schema == SCHEMA_V2 and (item.get('exchange'), item.get('market')) != (exchange, market):
                raise ProbeError('FUGLE_SNAPSHOT_MARKET_MISMATCH')
            if pair in self.frames:
                raise ProbeError('FUGLE_DUPLICATE_SNAPSHOT_SERIES')
            rows = checked_rows(item['rows'], symbol=pair[0], interval=pair[1],
                start=cal['start_date'], end=cal['through_date'])
            self.frames[pair] = native_frame(rows, pair[1])

    def history(self, symbol, *, start, end, interval):
        _, exchange, market = fugle_symbol(symbol)
        if self.schema == SCHEMA and exchange != 'TWSE':
            raise ProbeError('FUGLE_UNSUPPORTED_SYMBOL')
        if interval not in ('60m', '1d'):
            raise ProbeError('FUGLE_UNSUPPORTED_INTERVAL')
        start, end = local_time(start), local_time(end)
        if end <= start:
            raise ProbeError('INVALID_REQUEST_RANGE')
        frame = self.frames.get((symbol, interval))
        if self.schema == SCHEMA_V2:
            self.metadata.update(exchange=exchange, market=market,
                research_sample=self.research_sample, closing_time_validation=self.closing_time_validation)
        return pd.DataFrame() if frame is None else frame.loc[
            (frame.index >= start) & (frame.index < end)].copy(deep=True)
