"""FinMind v4 daily data: TWSE and TPEx share the stock_id endpoint.

https://finmind.github.io/tutor/TaiwanMarket/Chip/
All persisted quantities are shares; no intraday volume denominators.
"""
from collections import defaultdict
from datetime import date, timedelta
import math
import os
import requests

from .config import DEFAULT_CONFIG


def request_json(url, *, params, timeout, headers=None):
    try:
        response = requests.get(url, params=params, headers=headers or {}, timeout=timeout)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.SSLError:
        # Windows trusts locally managed roots that certifi may not contain.
        # Respect explicit CA configuration and never disable certificate validation.
        if os.name != 'nt' or os.environ.get('REQUESTS_CA_BUNDLE') or os.environ.get('CURL_CA_BUNDLE'):
            raise
        import json
        import ssl
        from urllib.parse import urlencode
        from urllib.request import Request, urlopen
        request = Request(url + '?' + urlencode(params), headers=headers or {})
        # PROTOCOL_TLS_CLIENT retains CERT_REQUIRED and hostname verification,
        # using Requests-compatible validation rather than Python 3.13's strict
        # X.509 extension checks on older Windows-managed trust roots.
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.load_default_certs()
        with urlopen(request, timeout=timeout, context=context) as response:
            return json.load(response)


def detect_market(symbol):
    symbol = str(symbol).strip().upper()
    if symbol.endswith('.TWO'):
        return 'TPEx'
    if symbol.endswith('.TW'):
        return 'TWSE'
    from app.stock import resolve_yahoo_symbol
    return detect_market(resolve_yahoo_symbol(symbol))


def number(value, multiplier=1):
    value = float(str(value).replace(',', '')) * multiplier
    if not math.isfinite(value) or value < 0:
        raise ValueError('Invalid buy/sell/volume quantity')
    return value


def normalize_records(symbol, market, records, prices, *, unit='shares'):
    if unit not in ('shares', 'lots') or market not in ('TWSE', 'TPEx'):
        raise ValueError('Unsupported unit or market')
    code = symbol.split('.')[0]
    multiplier = 1000 if unit == 'lots' else 1
    volumes = {r['date']: number(r['Trading_Volume']) for r in prices if str(r['stock_id']) == code}
    trading_days = sorted(volumes)
    previous_days = dict(zip(trading_days[1:], trading_days[:-1]))
    grouped = defaultdict(dict)
    for record in records:
        if str(record['stock_id']) != code:
            continue
        day, name = record['date'], record['name']
        date.fromisoformat(day)
        if name in grouped[day]:
            raise ValueError('Duplicate institution/date')
        grouped[day][name] = record
    result = []
    for day, groups in sorted(grouped.items()):
        required = {'Foreign_Investor', 'Investment_Trust'}
        if day >= '2018-01-15':
            required.add('Foreign_Dealer_Self')
        required.update({'Dealer_self', 'Dealer_Hedging'} if day >= '2014-12-01' else {'Dealer'})
        if not required.issubset(groups) or volumes.get(day, 0) <= 0:
            continue  # Missing groups are not zero flows.
        row = dict(symbol=code, market=market, date=day, volume=volumes[day], source='FinMind',
                   previous_trading_date=previous_days.get(day),
                   available_at=(date.fromisoformat(day) + timedelta(days=1)).isoformat() + 'T00:00:00+08:00')
        families = dict(foreign=['Foreign_Investor', 'Foreign_Dealer_Self'],
                        investment_trust=['Investment_Trust'], dealer=['Dealer', 'Dealer_self', 'Dealer_Hedging'])
        for prefix, names in families.items():
            for side in ('buy', 'sell'):
                row[prefix + '_' + side] = sum(number(groups[n][side], multiplier) for n in names if n in groups)
            row[prefix + '_net'] = row[prefix + '_buy'] - row[prefix + '_sell']
            row[prefix + '_net_ratio'] = row[prefix + '_net'] / row['volume']
        row['total_institutional_net'] = sum(row[p + '_net'] for p in families)
        row['total_institutional_net_ratio'] = row['total_institutional_net'] / row['volume']
        result.append(row)
    return result


class FinMindProvider:
    def __init__(self, config=DEFAULT_CONFIG):
        self.config = config

    def fetch(self, symbol, start, end):
        market = detect_market(symbol)
        code = symbol.split('.')[0]
        token = os.environ.get('FINMIND_TOKEN')
        headers = {'Authorization': 'Bearer ' + token} if token else {}
        def get(dataset):
            payload = request_json('https://api.finmindtrade.com/api/v4/data',
                params=dict(dataset=dataset, data_id=code, start_date=start, end_date=end),
                headers=headers, timeout=self.config.timeout_seconds)
            if payload.get('status') != 200 or not isinstance(payload.get('data'), list):
                raise ValueError('Institutional provider unavailable')
            return payload['data']
        return normalize_records(code, market, get('TaiwanStockInstitutionalInvestorsBuySell'), get('TaiwanStockPrice'))


def report_date(value):
    """Read the date in the response, never substitute the requested date."""
    value = str(value).strip()
    if len(value) == 8 and value.isdigit():
        return date(int(value[:4]), int(value[4:6]), int(value[6:])).isoformat()
    parts = value.replace('-', '/').split('/')
    year, month, day = map(int, parts)
    return date(year + 1911 if year < 1911 else year, month, day).isoformat()


class OfficialProvider:
    """Dated exchange reports, in shares, with a same-session official volume."""
    def __init__(self, config=DEFAULT_CONFIG):
        self.config = config

    def _get(self, url, **params):
        payload = request_json(url, params=dict(response='json', **params), timeout=self.config.timeout_seconds)
        if str(payload.get('stat', '')).lower() != 'ok':
            raise ValueError('Official report not published')
        return payload

    def fetch_day(self, symbol, target):
        from .freshness import previous_session
        market, code = detect_market(symbol), symbol.split('.')[0]
        if market == 'TWSE':
            payload = self._get('https://www.twse.com.tw/rwd/zh/fund/T86',
                                date=target.replace('-', ''), selectType='ALL')
            day = report_date(payload['date'])
            if day != target:
                raise ValueError('TWSE returned another session')
            fields = payload['fields']
            values = next(r for r in payload['data'] if r[0].strip() == code)
            record = dict(zip(fields, values))
            def quantity(*names):
                return sum(number(record[n]) for n in names)
            pairs = {}
            for side, label in [('buy', '買進'), ('sell', '賣出')]:
                pairs['foreign_' + side] = quantity(f'外陸資{label}股數(不含外資自營商)', f'外資自營商{label}股數')
                pairs['investment_trust_' + side] = quantity(f'投信{label}股數')
                pairs['dealer_' + side] = quantity(f'自營商{label}股數(自行買賣)', f'自營商{label}股數(避險)')
            quote = self._get('https://www.twse.com.tw/rwd/zh/afterTrading/STOCK_DAY',
                              date=target.replace('-', ''), stockNo=code)
            volume_index = quote['fields'].index('成交股數')
            volumes = {report_date(r[0]): number(r[volume_index]) for r in quote['data']}
            volume = volumes[day]
            previous = max((d for d in volumes if d < day), default=None)
            reported_net = float(record['三大法人買賣超股數'].replace(',', ''))
        else:
            payload = self._get('https://www.tpex.org.tw/www/zh-tw/insti/dailyTrade',
                                date=target.replace('-', '/'), type='Daily')
            table = payload['tables'][0]
            day = report_date(table['date'])
            if day != target or report_date(payload['date']) != day:
                raise ValueError('TPEx returned another session')
            values = next(r for r in table['data'] if r[0].strip() == code)
            # TPEx repeats subcolumn labels; its 24-column report includes subtotals.
            if len(table['fields']) != 24 or len(values) != 24 or table['fields'][-1] != '三大法人買賣超股數合計':
                raise ValueError('Unknown TPEx institutional schema')
            pairs = {}
            for family, index in [('foreign', 8), ('investment_trust', 11), ('dealer', 20)]:
                pairs[family + '_buy'] = number(values[index])
                pairs[family + '_sell'] = number(values[index + 1])
                if pairs[family + '_buy'] - pairs[family + '_sell'] != float(values[index + 2].replace(',', '')):
                    raise ValueError('Inconsistent TPEx subtotal')
            reported_net = float(values[23].replace(',', ''))
            quote = self._get('https://www.tpex.org.tw/www/zh-tw/afterTrading/dailyQuotes',
                              date=target.replace('-', '/'))
            table = quote['tables'][0]
            if report_date(quote['date']) != day or report_date(table['date']) != day:
                raise ValueError('Volume and flow dates differ')
            values = next(r for r in table['data'] if r[0].strip() == code)
            volume = number(values[table['fields'].index('成交股數')])
            previous = None
        if volume <= 0:
            raise ValueError('Missing completed daily volume')
        row = dict(symbol=code, market=market, date=day, source=market, volume=volume,
                   previous_trading_date=previous or previous_session(date.fromisoformat(day), self.config).isoformat(),
                   available_at=day + 'T' + f'{self.config.publication_hour:02d}:00:00+08:00', **pairs)
        for family in ('foreign', 'investment_trust', 'dealer'):
            row[family + '_net'] = row[family + '_buy'] - row[family + '_sell']
            row[family + '_net_ratio'] = row[family + '_net'] / volume
        row['total_institutional_net'] = sum(row[f + '_net'] for f in ('foreign', 'investment_trust', 'dealer'))
        if row['total_institutional_net'] != reported_net:
            raise ValueError('Inconsistent official institutional total')
        row['total_institutional_net_ratio'] = row['total_institutional_net'] / volume
        return row


class OfficialFirstProvider:
    def __init__(self, config=DEFAULT_CONFIG):
        self.config = config
        self.official = OfficialProvider(config)
        self.fallback = FinMindProvider(config)

    def fetch(self, symbol, start, end):
        from .freshness import previous_session
        import logging
        logger = logging.getLogger(__name__)
        latest = None
        error_name = None
        for _ in range(2):
            try:
                latest = self.official.fetch_day(symbol, end)
                break
            except (requests.RequestException, OSError, ValueError, KeyError, IndexError, StopIteration, TypeError) as error:
                error_name = type(error).__name__
        # Keep the existing historical source; official latest wins on a duplicate date.
        try:
            history_end = (date.fromisoformat(end) - timedelta(days=1)).isoformat() if latest else end
            history = self.fallback.fetch(symbol, start, history_end)
        except Exception:
            history = []
        if latest is None:
            logger.warning('[Institutional] %s source=%s target=%s official_unavailable=%s fallback=FinMind/cache',
                           symbol, detect_market(symbol), end, error_name)
            if not history:
                try:
                    history = [self.official.fetch_day(symbol, previous_session(date.fromisoformat(end), self.config).isoformat())]
                except (requests.RequestException, OSError, ValueError, KeyError, IndexError, StopIteration, TypeError):
                    pass
        result = {r['date']: r for r in history if start <= r['date'] <= end}
        if latest:
            result[latest['date']] = latest
        return [result[d] for d in sorted(result)]
