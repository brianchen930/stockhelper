"""Shared Fugle REST transport: strict normalization, TLS and secret redaction."""
from datetime import date, timedelta
import hashlib
import json
import math
import os
from pathlib import Path
import re
import ssl
import time
from urllib.error import HTTPError
from urllib.parse import quote, quote_plus, urlencode
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener

import pandas as pd
from dotenv import dotenv_values

BASE = 'https://api.fugle.tw/marketdata/v1.0/stock/historical/candles/'
FIELDS = ('open', 'high', 'low', 'close', 'volume')
TAIPEI = 'Asia/Taipei'
PROJECT_ROOT = Path(__file__).resolve().parent.parent
ERROR_BODY_LIMIT = 16 * 1024


class ProbeError(ValueError):
    """Fixed exception text; separate, sanitized HTTP diagnostics only."""

    def __init__(self, code, *, http_status=None, api_error=None):
        super().__init__(code)
        self.http_status = http_status
        self.api_error = api_error


def read_api_key(environ=None, *, project_root=None):
    """Read only the Fugle key, without changing the process environment."""
    key = (os.environ if environ is None else environ).get('FUGLE_API_KEY', '').strip()
    if key:
        return key
    try:
        # Use an explicit path, independent of cwd. Disable interpolation so
        # values remain literal and cannot draw credentials from other vars.
        with ((PROJECT_ROOT if project_root is None else Path(project_root)) / '.env').open(encoding='utf-8-sig') as stream:
            values = dotenv_values(stream=stream, interpolate=False)
    except FileNotFoundError:
        return ''
    except (OSError, UnicodeError):
        # Never expose file contents or exception text in reports/tracebacks.
        raise ProbeError('ENV_FILE_READ_ERROR') from None
    return (values.get('FUGLE_API_KEY') or '').strip()


def safe_api_error(body, key):
    """Keep only JSON message/error strings, never raw bodies or header objects.

    Parse and redact before truncating, so a cut-off key cannot leak a prefix.
    Non-JSON/oversized responses are deliberately omitted.
    """
    if len(body) > ERROR_BODY_LIMIT:
        return 'ERROR_BODY_TOO_LARGE_OMITTED'
    try:
        payload = json.loads(body)
    except (ValueError, UnicodeError):
        return 'NON_JSON_ERROR_BODY_OMITTED'
    if not isinstance(payload, dict):
        return 'NO_ALLOWLISTED_ERROR_MESSAGE'
    messages = []
    for name in ('message', 'error'):
        value = payload.get(name)
        if isinstance(value, str):
            messages.append(value)
        elif isinstance(value, list):
            messages.extend(item for item in value if isinstance(item, str))
    # Discard an entire message containing a credential assignment. Header
    # continuations may span multiple lines, so line-by-line masking is unsafe.
    sensitive = r'(?i)(?:headers?|authorization|authentication|cookie|api[ _-]?key|token|secret|password)\s*[\"\x27]?\s*[:=]'
    message = '\n'.join('[REDACTED_SENSITIVE_MESSAGE]' if re.search(sensitive, item) else item
                        for item in messages)
    for secret in sorted({key, quote(key, safe=''), quote_plus(key)}, key=len, reverse=True):
        if secret:
            message = message.replace(secret, '[REDACTED]')
    message = re.sub(r'(?i)\bBearer\s+\S+', 'Bearer [REDACTED]', message)
    message = re.sub(r'\b[A-Za-z0-9_+/=-]{24,}(?:\.[A-Za-z0-9_+/=-]+)*', '[REDACTED_TOKEN]', message)
    message = ''.join(char if char.isprintable() else ' ' for char in message)
    return message[:1000] or 'NO_ALLOWLISTED_ERROR_MESSAGE'


def request_params(spec):
    """The minimal daily request relies on documented D/unadjusted defaults."""
    params = {'from': spec['start'], 'to': spec['end']}
    if spec.get('request_mode') == 'minimal':
        if spec['timeframe'] != 'D':
            params['timeframe'] = spec['timeframe']
    else:
        params.update(timeframe=spec['timeframe'], fields=','.join(FIELDS), sort='asc')
        if spec['timeframe'] == 'D':
            params['adjusted'] = 'false'
    return params


def chunks(start, end, days=180):
    """Inclusive, adjacent ranges, each strictly shorter than a year."""
    if start > end or not 1 <= days <= 180:
        raise ValueError('INVALID_RANGE')
    while start <= end:
        stop = min(end, start + timedelta(days=days - 1))
        yield start, stop
        start = stop + timedelta(days=1)


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # Never forward X-API-KEY to a redirect target.


class FugleClient:
    def __init__(self, *, environ=None, ca_bundle=None, opener=None, minimum_interval=1.05,
                 key_loader=None, opener_factory=build_opener):
        key = (key_loader or read_api_key)(environ)
        if not key:
            raise ProbeError('MISSING_API_KEY')
        if (not key.isascii() or any(char.isspace() or not char.isprintable() for char in key)
                or key.startswith(('\"', "'")) or key.endswith(('\"', "'"))):
            raise ProbeError('INVALID_API_KEY_FORMAT')
        self._key = key
        try:
            context = ssl.create_default_context(cafile=ca_bundle)
        except Exception:
            raise ProbeError('TLS_CONFIGURATION_ERROR') from None
        self._opener = opener or opener_factory(NoRedirect(), HTTPSHandler(context=context))
        self._interval = max(1.05, minimum_interval)
        self._last = None
        self.last_http_status = None

    def candles(self, spec):
        self.last_http_status = None
        symbol, tf = spec['symbol'], spec['timeframe']
        if not re.fullmatch(r'\d{4}', symbol) or tf not in ('1', '60', 'D'):
            raise ProbeError('INVALID_REQUEST')
        try:
            if any(not re.fullmatch(r'\d{4}-\d{2}-\d{2}', spec[name]) for name in ('start', 'end')):
                raise ValueError()
            start, end = date.fromisoformat(spec['start']), date.fromisoformat(spec['end'])
        except (ValueError, TypeError):
            raise ProbeError('INVALID_REQUEST_DATE') from None
        if start > end or pd.Timestamp(end) >= pd.Timestamp(start) + pd.DateOffset(years=1):
            raise ProbeError('INVALID_REQUEST_RANGE')
        if spec.get('request_mode', 'original') not in ('minimal', 'original'):
            raise ProbeError('INVALID_REQUEST_MODE')
        params = request_params(spec)
        request = Request(BASE + symbol + '?' + urlencode(params),
                          headers={'X-API-KEY': self._key, 'Accept': 'application/json'})
        if self._last is not None:
            time.sleep(max(0, self._interval - (time.monotonic() - self._last)))
        self._last = time.monotonic()
        try:
            with self._opener.open(request, timeout=25) as response:
                self.last_http_status = int(response.status)
                if response.status != 200:
                    raise ProbeError(f'HTTP_{int(response.status)}', http_status=int(response.status),
                                     api_error=safe_api_error(response.read(ERROR_BODY_LIMIT + 1), self._key))
                body = response.read(32 * 1024 * 1024 + 1)
                if len(body) > 32 * 1024 * 1024:
                    raise ProbeError('RESPONSE_TOO_LARGE')
                payload = json.loads(body)
        except HTTPError as error:
            self.last_http_status = int(error.code)
            try:
                body = error.read(ERROR_BODY_LIMIT + 1)
                message = safe_api_error(body, self._key)
            except Exception:
                message = 'ERROR_BODY_UNAVAILABLE'
            finally:
                try:
                    error.close()
                except Exception:
                    pass
            raise ProbeError(f'HTTP_{int(error.code)}', http_status=int(error.code), api_error=message) from None
        except ProbeError:
            raise
        except Exception:
            # Exception messages can contain request headers or response bodies.
            raise ProbeError('NETWORK_TLS_OR_JSON_ERROR') from None
        return normalize(payload, spec)


def normalize(payload, spec):
    """Allowlist data; reject naive timestamps, duplicates and invalid OHLCV.

    This preserves native bar boundaries; no resampling/auction merging here.
    Minute volumes for TWSE/TPEx whole-lot stocks are converted lots -> shares.
    Legacy requests default to TWSE; TPEx must be requested explicitly as OTC.
    """
    tf = spec['timeframe']
    exchange = spec.get('exchange', 'TWSE')
    market = spec.get('market', 'TSE' if exchange == 'TWSE' else 'OTC')
    if (exchange, market) not in (('TWSE', 'TSE'), ('TPEx', 'OTC')):
        raise ProbeError('UNSUPPORTED_MARKET')
    if (not isinstance(payload, dict) or payload.get('symbol') != spec['symbol']
            or str(payload.get('timeframe')) != tf or payload.get('exchange') != exchange
            or (payload.get('market', market if exchange == 'TWSE' else None) != market)
            or payload.get('adjusted', False) is not False):
        raise ProbeError('METADATA_MISMATCH')
    rows = payload.get('data')
    if not isinstance(rows, list) or not rows:
        raise ProbeError('EMPTY_DATA')
    result, seen = [], set()
    for row in rows:
        try:
            raw_date = row['date']
            if tf == 'D':
                if not isinstance(raw_date, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', raw_date):
                    raise ValueError()
                stamp = pd.Timestamp(date.fromisoformat(raw_date), tz=TAIPEI)
                source_stamp = str(stamp.date())
            else:
                stamp = pd.Timestamp(raw_date)
                if stamp.tzinfo is None:
                    raise ValueError()
                source_stamp = stamp.isoformat()
                stamp = stamp.tz_convert(TAIPEI)
            if pd.isna(stamp) or not spec['start'] <= str(stamp.date()) <= spec['end']:
                raise ValueError()
            values = {name: float(row[name]) for name in FIELDS}
            if any(isinstance(row[name], bool) for name in FIELDS):
                raise ValueError()
            if any(not math.isfinite(value) for value in values.values()):
                raise ValueError()
            o, h, low, c, v = (values[name] for name in FIELDS)
            if min(o, h, low, c) <= 0 or low > min(o, c) or h < max(o, c) or low > h or v < 0:
                raise ValueError()
        except Exception:
            raise ProbeError('INVALID_DATE_OR_OHLCV') from None
        iso = stamp.isoformat()
        if iso in seen:
            raise ProbeError('DUPLICATE_TIMESTAMP')
        seen.add(iso)
        result.append(dict(bar_start=iso, source_timestamp=source_stamp, **{name.capitalize(): values[name] for name in FIELDS},
                           source_volume=values['volume'], source_volume_unit='shares' if tf == 'D' else 'lots'))
        result[-1]['Volume'] *= 1 if tf == 'D' else 1000
        if exchange == 'TPEx':
            result[-1].update(source_exchange=exchange, source_market=market)
    return sorted(result, key=lambda row: row['bar_start'])


