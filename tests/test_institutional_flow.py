import copy
from datetime import datetime, timedelta
import sqlite3
import sys
from pathlib import Path

import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.institutional_flow.provider import normalize_records, detect_market
from app.institutional_flow.features import build_context, flow_trend, unknown, TAIPEI
from app.institutional_flow.storage import FlowStore
from app.institutional_flow.adjustment import adjust_probability, adjust_candidate
from app.institutional_flow.service import InstitutionalFlowService


def rows(foreign, trust=None, start='2026-09-07'):
    trust = trust if trust is not None else [0] * len(foreign)
    raw, prices = [], []
    for i, (f, t) in enumerate(zip(foreign, trust)):
        day = (datetime.fromisoformat(start) + timedelta(days=i)).date().isoformat()
        for name, net in [('Foreign_Investor', f), ('Foreign_Dealer_Self', 0),
                          ('Investment_Trust', t), ('Dealer_self', 100), ('Dealer_Hedging', -50)]:
            raw.append(dict(stock_id='3211', date=day, name=name, buy=max(net, 0), sell=max(-net, 0)))
        prices.append(dict(stock_id='3211', date=day, Trading_Volume=100000))
    return normalize_records('3211.TWO', 'TPEx', raw, prices)


@pytest.fixture
def store(tmp_path):
    return FlowStore(lambda: sqlite3.connect(tmp_path / 'flow.db'))


def test_normalization_market_units_missing():
    assert detect_market('3211.TWO') == 'TPEx'
    assert detect_market('2330.TW') == 'TWSE'
    row = rows([10000])[0]
    assert row['foreign_net_ratio'] == .1
    assert row['dealer_net'] == 50
    assert row['total_institutional_net'] == 10050
    raw = [dict(stock_id='3211', date='2026-09-14', name=n, buy=2, sell=1) for n in
           ('Foreign_Investor', 'Foreign_Dealer_Self', 'Investment_Trust', 'Dealer_self', 'Dealer_Hedging')]
    prices = [dict(stock_id='3211', date='2026-09-14', Trading_Volume=100000)]
    assert normalize_records('3211', 'TPEx', raw, prices, unit='lots')[0]['foreign_net_ratio'] == .02
    assert normalize_records('3211', 'TPEx', raw[:-1], prices) == []
    prices[0]['Trading_Volume'] = 0
    assert normalize_records('3211', 'TPEx', raw, prices) == []


def test_storage_upsert_and_version_replay(store):
    data = rows([10000])
    store.upsert(data, '2026-09-08T08:00:00+08:00')
    data[0]['foreign_net'] = 20000
    store.upsert(data, '2026-09-10T08:00:00+08:00')
    assert store.history('3211', '2026-09-09T11:00:00+08:00')[0]['foreign_net'] == 10000
    assert store.history('3211', '2026-09-11T11:00:00+08:00')[0]['foreign_net'] == 20000
    with store.connect() as con:
        assert con.execute('SELECT count(*) FROM institutional_flow').fetchone()[0] == 1


@pytest.mark.parametrize('values,expected', [([-18000,-10000,-3000], 'SELLING_WEAKENING'),
    ([-3000,-10000,-18000], 'SELLING_ACCELERATING'), ([3000,10000,18000], 'BUYING_ACCELERATING'),
    ([18000,10000,3000], 'BUYING_WEAKENING')])
def test_trends(values, expected):
    assert flow_trend(values) == expected


def test_bull_bear_mixed_and_no_mutation():
    bullish = build_context(rows([15000]*5, [10000]*5), '2026-09-15T11:00:00')
    bearish = build_context(rows([-20000]*5), '2026-09-15T11:00:00')
    mixed = build_context(rows([15000]*5, [-15000]*5), '2026-09-15T11:00:00')
    assert bullish['institutional_level'] == 'STRONG_SUPPORT'
    assert mixed['institutional_level'] == 'MIXED' and mixed['confidence'] < 1
    assert adjust_probability(.78, bullish) > .78
    assert adjust_probability(.78, bearish) < .78
    assert adjust_probability(.78, bullish, side='resistance') < .78
    candidate = dict(support_low=488, support_high=494, result=dict(posterior_success_probability=.78, model_status='ready'))
    before = copy.deepcopy(candidate)
    adjust_candidate(candidate, bearish)
    assert candidate['result'] == before['result']
    assert (candidate['support_low'], candidate['support_high']) == (488,494)
    assert candidate['base_support_probability'] == .78


def test_windows_streak_and_weighted_ratio():
    data = rows([10000]*10)
    context = build_context(data, '2026-09-18T11:00:00')
    f = context['features']
    assert f['foreign_net_10d'] == 100000
    assert f['foreign_net_ratio_5d'] == .1
    assert f['foreign_streak'] == 10
    assert build_context(data[:1], '2026-09-08')['features']['foreign_net_3d'] is None


def test_asof_excludes_same_day_and_future_observation(store):
    data = rows([10000, 20000], start='2026-09-14')
    store.upsert(data, '2026-09-15T09:00:00+08:00')
    context = build_context(store.history('3211', '2026-09-15T11:00:00'), '2026-09-15T11:00:00')
    assert context['latest_available_institutional_date'] == '2026-09-14'
    assert store.history('3211', '2026-09-14T11:00:00') == []
    assert len(store.history('3211', '2026-09-15T03:00:00+00:00')) == 1
    assert build_context(data, '2026-10-15')['institutional_level'] == 'UNKNOWN'


def test_failed_api_and_daily_cache(store):
    class Broken:
        calls = 0
        def fetch(self, *args):
            self.calls += 1
            raise RuntimeError('offline')
    provider = Broken()
    service = InstitutionalFlowService(store, provider)
    for _ in range(2):
        context = service.context('3211.TWO')
        assert context['institutional_level'] == 'UNKNOWN'
        assert adjust_probability(.78, context) == .78
    assert provider.calls == 1
    service.context('3211.TWO', as_of='2026-09-14T11:00:00', replay=True)
    assert provider.calls == 1


def test_event_snapshot_and_outcome(store):
    c = dict(support_low=488, support_high=494, result=dict(posterior_success_probability=.78, model_status='ready'))
    adjust_candidate(c, unknown())
    for _ in range(2):
        store.save_event('3211', '2026-09-15T11:00:00+08:00', c, unknown())
    with store.connect() as con:
        event_id = con.execute('SELECT id FROM institutional_support_events').fetchone()[0]
        assert con.execute('SELECT count(*) FROM institutional_support_events').fetchone()[0] == 1
    with pytest.raises(ValueError):
        store.resolve_event(event_id, 'HOLD', '2026-09-15T10:00:00')
    store.resolve_event(event_id, 'HOLD', '2026-09-18T14:00:00')


def test_integration_display_and_full_payload(store):
    from app.institutional_flow.integration import attach_institutional_flow
    from app.support_resistance_analysis.formatting import format_support_resistance_output
    candidate = dict(support_low=488, support_high=494,
        result=dict(posterior_success_probability=.78, model_status='ready'))
    zone = dict(low=488, high=494, strength_label='strong', strength_score=8, distance_pct=-1, methods=['swing_low'])
    sr = dict(support_zones=[zone], resistance_zones=[], bayesian_support=[candidate], bayesian_support_selected=candidate)
    original = copy.deepcopy(zone)
    store.upsert(rows([-20000]*5), '2026-09-12T08:00:00+08:00')
    service = InstitutionalFlowService(store, object())
    result = dict(support_resistance=sr)
    attach_institutional_flow(result, '3211.TWO', service=service, as_of='2026-09-15T11:00:00', replay=True)
    text = result['support_resistance_text']
    assert '法人籌碼：偏空' in text
    assert text.count('支撐成功機率：') == 1
    assert 'Posterior' not in text and 'institutional_score' not in text
    assert zone == original
    debug = format_support_resistance_output(sr, debug=True)
    assert 'Adjusted Posterior' in debug and 'uncalibrated' in debug
    assert sr['adjusted_support_probability'] < sr['base_support_probability']


def test_service_success_daily_reuse_and_cache_fallback(store):
    now = datetime.now(TAIPEI)
    yesterday = (now - timedelta(days=1)).date().isoformat()
    class Provider:
        calls = 0
        def fetch(self, *args):
            self.calls += 1
            return rows([20000], start=yesterday)
    provider = Provider()
    service = InstitutionalFlowService(store, provider)
    assert service.context('3211.TWO')['institutional_level'] != 'UNKNOWN'
    assert service.context('3211.TWO')['latest_available_institutional_date'] == yesterday
    assert provider.calls == 1


def test_replay_api_keeps_labels_and_no_future_versions(store):
    import pandas as pd
    from app.institutional_flow.backtest import attach_institutional_backtest
    store.upsert(rows([-20000]*5), '2026-09-16T08:00:00+08:00')
    predictions = pd.DataFrame([dict(symbol='3211', event_date='2026-09-15T11:00:00+08:00',
        predicted_probability=.78, actual_label='success', label_available_date='2026-09-20')])
    strict = attach_institutional_backtest(predictions, store)
    assert strict.iloc[0].institutional_level == 'UNKNOWN'
    assert strict.iloc[0].adjusted_support_probability == .78
    assumed = attach_institutional_backtest(predictions, store, strict=False)
    assert assumed.iloc[0].adjusted_support_probability < .78
    assert assumed.iloc[0].actual_label == 'success'
    assert assumed.iloc[0].availability_policy == 'next_day_assumption_revised_history'


def test_missing_session_breaks_streak():
    data = rows([10000]*5)
    data.pop(2)
    features = build_context(data, '2026-09-15')['features']
    assert features['foreign_streak'] == 2
    assert features['foreign_net_3d'] is None


def test_provider_request_contract(monkeypatch):
    from app.institutional_flow.provider import FinMindProvider
    calls = []
    raw = [dict(stock_id='3211', date='2026-09-14', name=n, buy=2000, sell=1000) for n in
           ('Foreign_Investor', 'Foreign_Dealer_Self', 'Investment_Trust', 'Dealer_self', 'Dealer_Hedging')]
    class Response:
        def __init__(self, data): self.data = data
        def raise_for_status(self): pass
        def json(self): return dict(status=200, data=self.data)
    def get(url, **kwargs):
        calls.append(kwargs)
        return Response(raw if kwargs['params']['dataset'] == 'TaiwanStockInstitutionalInvestorsBuySell'
                        else [dict(stock_id='3211', date='2026-09-14', Trading_Volume=100000)])
    monkeypatch.setattr('app.institutional_flow.provider.requests.get', get)
    result = FinMindProvider().fetch('3211.TWO', '2026-09-01', '2026-09-14')
    assert result[0]['market'] == 'TPEx'
    assert result[0]['foreign_net_ratio'] == .02
    assert all(c['params']['data_id'] == '3211' and c['timeout'] > 0 for c in calls)


def test_failure_uses_valid_cache(store):
    now = datetime.now(TAIPEI)
    previous = now - timedelta(days=1)
    store.upsert(rows([20000], start=(now-timedelta(days=2)).date().isoformat()), previous.isoformat())
    class Broken:
        def fetch(self, *args): raise RuntimeError('offline')
    context = InstitutionalFlowService(store, Broken()).context('3211.TWO')
    assert context['institutional_level'] != 'UNKNOWN'


def test_invalid_config_and_probability():
    from app.institutional_flow.config import FlowConfig
    with pytest.raises(ValueError): FlowConfig(log_odds_per_point=-1)
    with pytest.raises(ValueError): adjust_probability(float('nan'), unknown())
