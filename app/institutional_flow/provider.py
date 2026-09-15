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
            response = requests.get('https://api.finmindtrade.com/api/v4/data',
                params=dict(dataset=dataset, data_id=code, start_date=start, end_date=end),
                headers=headers, timeout=self.config.timeout_seconds)
            response.raise_for_status()
            payload = response.json()
            if payload.get('status') != 200 or not isinstance(payload.get('data'), list):
                raise ValueError('Institutional provider unavailable')
            return payload['data']
        return normalize_records(code, market, get('TaiwanStockInstitutionalInvestorsBuySell'), get('TaiwanStockPrice'))
