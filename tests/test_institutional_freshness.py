"""Freshness regressions, including captured official 2026-09-21 reports."""
import copy
from dataclasses import asdict, replace
from datetime import datetime, timedelta
import json
from pathlib import Path
import sqlite3

import pytest

from app.institutional_flow.config import FlowConfig
from app.institutional_flow.features import build_context, TAIPEI, taipei_time
from app.institutional_flow.freshness import session_dates
from app.institutional_flow.provider import OfficialProvider, OfficialFirstProvider
from app.institutional_flow.service import InstitutionalFlowService
from app.institutional_flow.storage import FlowStore
from app.institutional_flow.presentation import format_context
from app.institutional_flow.adjustment import adjust_probability
from app.decision_engine import DecisionEngine
from test_institutional_flow import rows
from test_trade_actions import BASE


@pytest.fixture
def store(tmp_path):
    return FlowStore(lambda: sqlite3.connect(tmp_path / 'flow.db'))


def observed(day, net=20000):
    result = rows([net], [net], start=day)
    result[0]['available_at'] = day + 'T16:00:00+08:00'
    return result


@pytest.mark.parametrize('cutoff,actual,status,prior', [
    ('2026-09-21T20:00:00', '2026-09-21', 'FRESH', False),
    ('2026-09-21T20:00:00', '2026-09-18', 'STALE', False),
    ('2026-09-20T20:00:00', '2026-09-18', 'FRESH', False),
    ('2026-09-21T08:00:00', '2026-09-18', 'FRESH', True),
    ('2026-09-21T11:00:00', '2026-09-18', 'FRESH', True),
    ('2026-09-21T14:00:00', '2026-09-18', 'FRESH', True),
    ('2026-09-21T16:00:00', '2026-09-18', 'STALE', False),
])
def test_session_freshness_and_display(cutoff, actual, status, prior):
    context = build_context(observed(actual), cutoff)
    assert context['freshness'] == status
    assert context['data_date'] == context['actual_data_date'] == actual
    assert context['previous_session_context'] == prior
    text = format_context(context)
    if status == 'STALE':
        assert context['signal'] == context['institutional_level'] == 'UNKNOWN'
        assert context['institutional_score'] is None and context['features'] == {}
        assert '資料更新異常' in text and '2026-09-21 法人資料尚未成功取得' in text
        assert '偏多' not in text and '強力支撐' not in text and '連續' not in text
        assert adjust_probability(.7, context) == .7
    elif prior:
        assert '今日法人資料尚未公布' in text
    else:
        assert f'截至 {actual}' in text


def test_publication_target_timezone_holiday():
    assert session_dates(taipei_time('2026-09-21T14:00:00')) == ('2026-09-21', '2026-09-18')
    assert session_dates(taipei_time('2026-09-21T12:00:00+00:00')) == ('2026-09-21', '2026-09-21')
    config = FlowConfig(closed_dates=('2026-09-21',))
    assert build_context(observed('2026-09-18'), '2026-09-21T20:00:00', config)['freshness'] == 'FRESH'


def test_cache_morning_failure_evening_and_restart_retry(store):
    now = [taipei_time('2026-09-21T11:00:00')]
    class Provider:
        calls = []
        ready = False
        def fetch(self, symbol, start, target):
            self.calls.append(target)
            if target == '2026-09-21' and not self.ready:
                raise RuntimeError('not published')
            return observed(target)
    p = Provider()
    service = InstitutionalFlowService(store, p, clock=lambda: now[0])
    store.mark_attempt('3211', '2026-09-21')  # Legacy same-day marker must not block refresh.
    assert service.context('3211.TWO')['data_date'] == '2026-09-18'
    now[0] = taipei_time('2026-09-21T20:00:00')
    assert service.context('3211.TWO')['freshness'] == 'STALE'
    restart = InstitutionalFlowService(store, p, clock=lambda: now[0])
    assert restart.context('3211.TWO')['freshness'] == 'STALE'
    assert p.calls == ['2026-09-18', '2026-09-21']
    now[0] += timedelta(minutes=15)
    p.ready = True
    assert restart.context('3211.TWO')['freshness'] == 'FRESH'
    restart.context('3211.TWO')
    assert p.calls == ['2026-09-18', '2026-09-21', '2026-09-21']
    assert store.refresh_due('3211', 'TWSE', '2026-09-21', now[0], 900)


def test_same_day_observation_replay_and_missing_data(store):
    row = observed('2026-09-21')
    store.upsert(row, '2026-09-21T19:00:00+08:00')
    assert store.history('3211', '2026-09-21T18:00:00') == []
    assert len(store.history('3211', '2026-09-21T20:00:00')) == 1
    assert store.history('3211', '2026-09-21T20:00:00', market='TWSE') == []
    class Forbidden:
        def fetch(self, *args):
            pytest.fail('Replay must never download')
    service = InstitutionalFlowService(store, Forbidden(), clock=lambda: taipei_time('2026-09-21T20:00:00'))
    assert service.context('3211.TWO', as_of='2026-09-21T20:00:00')['freshness'] == 'FRESH'
    c = service.context('3211.TWO', as_of='2026-09-21T18:00:00')
    assert c['freshness'] == 'UNAVAILABLE' and c['foreign_net'] is None


def test_streak_ends_at_current_session_and_breaks_on_missing_session():
    data = observed('2026-09-16') + observed('2026-09-17') + observed('2026-09-18')
    assert build_context(data, '2026-09-21T20:00:00')['features'] == {}
    data += observed('2026-09-21', -20000)
    c = build_context(data, '2026-09-21T20:00:00')
    assert c['features']['foreign_streak'] == -1
    data[-1]['foreign_net'] = 20000
    assert build_context(data, '2026-09-21T20:00:00')['features']['foreign_streak'] == 4
    data.pop(2)
    assert build_context(data, '2026-09-21T20:00:00')['features']['foreign_streak'] == 1


@pytest.mark.parametrize('level', ['STRONG_SUPPORT', 'STRONG_PRESSURE'])
@pytest.mark.parametrize('changes', [
    {}, {'position_status': 'HOLDING'},
    {'position_status': 'HOLDING', 'support_status': 'MINOR_BREAK', 'macd_momentum': 'bullish_weakening'},
    {'short_term_direction': -1, 'medium_term_direction': -1},
])
def test_stale_cannot_change_trade_decisions_or_scores(level, changes):
    baseline = replace(BASE, institutional_level='UNKNOWN', **changes)
    stale = replace(baseline, institutional_level=level, institutional_score=6,
                    institutional_confidence=1, institutional_selling_weakened=True,
                    institutional_freshness='STALE')
    engine = DecisionEngine()
    assert asdict(engine.evaluate(stale)) == asdict(engine.evaluate(baseline))


def test_decision_adapter_rejects_stale_payload():
    from test_decision_context import inputs
    from app.decision_context import build_decision_context
    result, bars, prior = inputs()
    flow = result['support_resistance']['institutional_context']
    flow.update(freshness='STALE', institutional_level='STRONG_SUPPORT', institutional_score=6,
                features={'foreign_flow_trend': 'SELLING_WEAKENING'})
    c = build_decision_context(result, bars, previous_zones=prior)
    assert c.institutional_level == 'UNKNOWN' and c.institutional_score is None
    assert not c.institutional_selling_weakened
    assert adjust_probability(.7, flow) == .7


def fixture(name):
    return json.loads((Path(__file__).parent / 'fixtures/institutional' / (name + '.json')).read_text(encoding='utf-8'))


@pytest.mark.parametrize('symbol,prefix,foreign,trust,dealer', [
    ('2408.TW', 'twse', -4122631, -38146, -145219),
    ('3211.TWO', 'tpex', 1257239, 434000, 122573),
])
def test_real_official_response_contract(monkeypatch, symbol, prefix, foreign, trust, dealer):
    calls = []
    def request(url, **kwargs):
        calls.append((url, kwargs))
        name = prefix + ('_flow' if 'T86' in url or 'dailyTrade' in url else '_quote')
        return fixture(name)
    monkeypatch.setattr('app.institutional_flow.provider.request_json', request)
    r = OfficialProvider().fetch_day(symbol, '2026-09-21')
    assert r['date'] == '2026-09-21' and r['source'] == r['market']
    assert (r['foreign_net'], r['investment_trust_net'], r['dealer_net']) == (foreign, trust, dealer)
    assert r['foreign_net_ratio'] == foreign / r['volume']
    assert all(c[1]['params']['date'] in ('20260921', '2026/09/21') for c in calls)


@pytest.mark.parametrize('prefix,symbol', [('twse', '2408.TW'), ('tpex', '3211.TWO')])
@pytest.mark.parametrize('fault', ['old_date', 'missing_stock', 'missing_number', 'wrong_volume_date'])
def test_invalid_official_report_never_becomes_today(monkeypatch, prefix, symbol, fault):
    flow, quote = fixture(prefix + '_flow'), fixture(prefix + '_quote')
    table = flow if prefix == 'twse' else flow['tables'][0]
    if fault == 'old_date':
        table['date'] = '20260918'
    elif fault == 'missing_stock':
        table['data'] = []
    elif fault == 'missing_number':
        table['data'][0][2 if prefix == 'twse' else 8] = '--'
    elif prefix == 'twse':
        quote['data'] = [r for r in quote['data'] if r[0] != '115/09/21']
    else:
        quote['date'] = '20260918'
    monkeypatch.setattr(OfficialProvider, '_get', lambda self, url, **params:
                        flow if 'T86' in url or 'dailyTrade' in url else quote)
    with pytest.raises((ValueError, KeyError, StopIteration)):
        OfficialProvider().fetch_day(symbol, '2026-09-21')


def test_official_retry_fallback_and_priority(monkeypatch):
    provider = OfficialFirstProvider()
    calls = []
    def get(symbol, target):
        calls.append(target)
        if len(calls) == 1:
            raise ValueError('temporary')
        return dict(observed(target)[0], source='TPEx')
    monkeypatch.setattr(provider.official, 'fetch_day', get)
    monkeypatch.setattr(provider.fallback, 'fetch', lambda *args: observed('2026-09-18'))
    result = provider.fetch('3211.TWO', '2026-08-01', '2026-09-21')
    assert calls == ['2026-09-21'] * 2
    assert result[-1]['source'] == 'TPEx'
    monkeypatch.setattr(provider.official, 'fetch_day', lambda *args: (_ for _ in ()).throw(ValueError('offline')))
    result = provider.fetch('3211.TWO', '2026-08-01', '2026-09-21')
    assert build_context(result, '2026-09-21T20:00:00')['freshness'] == 'STALE'


def test_total_api_failure_preserves_analysis(store):
    from app.institutional_flow.integration import attach_institutional_flow
    from app.decision_context import build_decision_context
    from test_decision_context import inputs
    class Broken:
        def fetch(self, *args):
            raise RuntimeError('offline')
    service = InstitutionalFlowService(store, Broken(), clock=lambda: taipei_time('2026-09-21T20:00:00'))
    result, bars, prior = inputs()
    candidate = result['support_resistance']['bayesian_support_selected']
    candidate['result'] = dict(model_status='ready', posterior_success_probability=.7)
    candidate['display'].update(thresholds=[.2, .4, .6, .8], rating_description='')
    before = copy.deepcopy(result)
    attach_institutional_flow(result, '3211.TWO', service=service)
    assert result['macd_analysis'] == before['macd_analysis']
    assert result['support_resistance']['nearest_support'] == before['support_resistance']['nearest_support']
    assert candidate['adjusted_support_probability'] == .7
    assert '暫無可用資料' in result['support_resistance_text']
    assert DecisionEngine().evaluate(build_decision_context(result, bars, previous_zones=prior)).decision


def test_windows_trust_fallback_keeps_tls_validation(monkeypatch):
    import io
    import ssl
    import requests
    from app.institutional_flow.provider import request_json
    import app.institutional_flow.provider as module
    if module.os.name != 'nt':
        pytest.skip('Windows certificate store compatibility')
    monkeypatch.delenv('REQUESTS_CA_BUNDLE', raising=False)
    monkeypatch.delenv('CURL_CA_BUNDLE', raising=False)
    def fail(*args, **kwargs):
        raise requests.exceptions.SSLError('untrusted by certifi')
    monkeypatch.setattr(module.requests, 'get', fail)
    calls = []
    def urlopen(request, *, context, timeout):
        assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname
        assert timeout == 10 and 'date=20260921' in request.full_url
        calls.append(request.full_url)
        return io.BytesIO(b'{"stat":"OK"}')
    monkeypatch.setattr('urllib.request.urlopen', urlopen)
    assert request_json('https://www.twse.com.tw/report', params={'date': '20260921'}, timeout=10)['stat'] == 'OK'
    monkeypatch.setenv('REQUESTS_CA_BUNDLE', 'explicit-user-ca.pem')
    with pytest.raises(requests.exceptions.SSLError):
        request_json('https://www.twse.com.tw/report', params={}, timeout=10)
    assert len(calls) == 1
