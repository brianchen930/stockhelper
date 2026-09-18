from copy import deepcopy
from dataclasses import asdict
import json

import pytest

from app.analysis_engine import generate_analysis, attach_signal_summary
from app.analysis.signal_layers import build_signal_layers, format_signal_summary
from app.rules.base import RuleCategory as C, evidence
from app.rules.engine import RuleEngine
from app.rules.rsi_rule import RSIRule
from app.rules.kd_rule import KDRule
from app.rules.macd_rule import MACDRule
from app.rules.signal_change_rule import SignalChangeRule
from app.decision_engine import DecisionEngine
from app.decision_context import build_decision_context


def creative(**changes):
    c = dict(current_signal='偏多', current_trend='多頭排列',
             previous_signal='偏多', previous_trend='多頭排列',
             rsi=64.76, macd=317.32, macd_signal=325.85, macd_histogram=-8.53,
             previous_macd=312, previous_macd_signal=324, previous_macd_histogram=-12,
             kd_k=75, kd_d=72, kd_j=81, previous_kd_k=74, previous_kd_d=71)
    c.update(changes)
    return c


def timeframe(score=7):
    return dict(short_term=dict(score=5, warnings=[]),
                medium_term=dict(score=score, label='偏多', warnings=[],
                    reasons=['月線方向向上', '季線方向向上', '最近 30 日區間高點與低點皆上移']))


def summary(context=None, tf=None):
    c = context or creative()
    r = RuleEngine().evaluate(c)
    return generate_analysis(c['current_trend'], c['current_signal'], r['score'],
        r['matched_rules'], rule_evidence=r['evidence'], timeframe_analysis=tf or timeframe())


def test_3443_negative_momentum_bullish_trend_divergence():
    result = summary()
    m, t, combined = (result[k] for k in ('momentum', 'trend', 'combined'))
    assert (m['direction'], m['strength'], m['bullish_count'], m['bearish_count']) == ('中性偏空', '弱', 0, 1)
    assert m['score'] == -1
    assert (t['direction'], t['strength'], t['score']) == ('偏多', '強', 7)
    assert combined == dict(direction='中性偏多', strength='弱', alignment='DIVERGENT')
    text = '\n'.join(format_signal_summary(result))
    for expected in ('【訊號摘要】', '動能：中性偏空｜弱', '趨勢：偏多｜強',
                     '綜合方向：中性偏多', '趨勢與動能分歧', 'RSI 64.76', 'KD J 81.00'):
        assert expected in text
    for old in ('基礎規則', '基礎訊號', '訊號確認 +1'):
        assert old not in text


def test_momentum_is_independent_of_ma_trend_signal_and_notification_score():
    results = [summary(creative(current_trend=trend, current_signal=signal))
               for trend, signal in [('多頭排列', '偏多'), ('空頭排列', '偏空'), ('均線糾結', '觀望')]]
    assert results[0]['momentum'] == results[1]['momentum'] == results[2]['momentum']
    items = RuleEngine().evaluate(creative())['evidence']
    a = generate_analysis('多頭排列', '偏多', 0, [], rule_evidence=items)
    b = generate_analysis('多頭排列', '偏空', 999, [], rule_evidence=items)
    assert a == b


@pytest.mark.parametrize('momentum,trend,direction,alignment', [
    (3, 7, '偏多', 'ALIGNED_BULLISH'),
    (-3, -7, '偏空', 'ALIGNED_BEARISH'),
    (-3, 7, '中性偏多', 'DIVERGENT'),
    (3, -7, '中性偏空', 'DIVERGENT'),
    (0, 0, '中性', 'NEUTRAL'),
    (0, 7, '中性偏多', 'NEUTRAL'),
])
def test_combined_mapping(momentum, trend, direction, alignment):
    result = build_signal_layers('均線糾結', [evidence(C.MOMENTUM, 'test', '證據', momentum)], timeframe(trend))
    assert result['combined']['direction'] == direction
    assert result['combined']['alignment'] == alignment
    if alignment == 'DIVERGENT':
        assert result['combined']['strength'] == '弱'


def test_events_do_not_enter_momentum_even_with_directional_words():
    unchanged = RuleEngine().evaluate(creative())
    changed = RuleEngine().evaluate(creative(previous_signal='觀望', previous_trend='均線糾結'))
    assert changed['score'] == unchanged['score'] + 6
    assert changed['should_notify'] and not unchanged['should_notify']
    assert changed['evidence'] == unchanged['evidence']
    assert changed['technical_score'] == unchanged['technical_score']
    event = changed['rule_results'][0]
    assert event['rule_category'] == C.EVENT
    assert {'signal_change', 'trend_change'} <= {e['category'] for e in event['events']}
    result = build_signal_layers('多頭排列', changed['evidence'] + [evidence(C.EVENT, 'event', '偏空跌破', -100)], timeframe())
    assert result['momentum'] == summary()['momentum']


@pytest.mark.parametrize('signal,score', [('偏多', 1), ('偏空', 1), ('觀望', 0)])
def test_neutral_rsi_confirms_without_direction_score(signal, score):
    r = RSIRule().evaluate(dict(current_signal=signal, rsi=64.76))
    assert r['score'] == score
    assert r['evidence'][0]['category'] == C.CONFIRMATION
    assert r['evidence'][0]['directional_score'] == 0
    assert '訊號確認 +1' not in '\n'.join(r['messages'])


def test_kd_can_have_momentum_and_risk_without_cancelling_direction():
    c = creative(kd_k=90, kd_d=85, kd_j=110, previous_kd_k=80, previous_kd_d=85)
    kd = KDRule().evaluate(c)
    assert kd['score'] == 4  # original notification weights: cross 2 + two risks 1 each
    assert {e['category'] for e in kd['evidence']} == {C.MOMENTUM, C.RISK}
    tf = timeframe()
    tf['medium_term']['warnings'] = ['股價與季線乖離 +30%，波段位置風險升高', 'ATR 極高波動']
    result = build_signal_layers('多頭排列', kd['evidence'], tf)
    assert result['combined']['direction'] == '偏多'
    assert result['trend']['direction'] == '偏多'
    assert result['momentum']['score'] == 2
    assert len(result['risk']['evidence']) == 4


@pytest.mark.parametrize('rsi,j', [(85, 110), (15, -10)])
def test_extremes_are_risk_not_direction(rsi, j):
    r = summary(creative(rsi=rsi, kd_j=j))
    assert r['momentum']['score'] == -1  # only existing negative MACD histogram
    assert r['trend']['direction'] == '偏多'
    assert r['risk']['evidence']


def test_trend_reuses_medium_score_without_mutation_or_new_indicators():
    tf = timeframe()
    before = deepcopy(tf)
    result = summary(tf=tf)
    assert tf == before
    assert result['trend']['source'] == 'medium_term'
    assert result['trend']['short_term_score'] == 5
    assert result['trend']['medium_term_score'] == 7
    assert '月線方向向上' in result['trend']['reasons']
    assert all(e['category'] == C.TREND for e in result['trend']['evidence'])


def test_missing_medium_falls_back_to_existing_ma_not_new_ma_calculation():
    tf = dict(medium_term=dict(score=0, label='資料不足'))
    result = build_signal_layers('多頭排列', [], tf)
    assert result['trend']['direction'] == '偏多'
    assert result['trend']['source'] == 'ma_strategy'
    assert result['trend']['score'] is None
    missing = build_signal_layers('資料不足', [], tf)
    assert missing['combined']['direction'] == '資料不足'


def test_formatter_only_uses_structured_results_and_roundtrips():
    result = summary()
    snapshot = deepcopy(result)
    text = format_signal_summary(json.loads(json.dumps(result)))
    assert result == snapshot
    result['momentum']['score'] = 9999
    result['trend']['score'] = -9999
    assert format_signal_summary(result) == text


def test_free_text_is_never_counted_as_direction():
    result = generate_analysis('多頭排列', '偏多', 999,
                               ['訊號由觀望轉偏多', 'KD 過熱偏空', 'RSI 訊號確認 +1'])
    assert result['momentum']['bullish_count'] == result['momentum']['bearish_count'] == 0
    assert not result['momentum']['available']


def test_invalid_analysis_cannot_retain_valid_layer_evidence():
    result = build_signal_layers('多頭排列', RuleEngine().evaluate(creative())['evidence'], timeframe(), valid=False)
    for key in ('momentum', 'trend', 'combined'):
        assert result[key]['direction'] == '資料不足'
    assert result['risk']['evidence'] == []
    assert result['momentum']['bullish_count'] == result['momentum']['bearish_count'] == 0


def test_api_summary_matches_rule_engine_and_does_not_change_decisions():
    c = creative()
    data = dict(c, stock_code='3443', close=6500, atr=100, history_date='2026-09-14',
                analysis=dict(trend='多頭排列', signal='偏多'), timeframe_analysis=timeframe())
    before = deepcopy(data)
    decision = asdict(DecisionEngine().evaluate(build_decision_context(data)))
    attach_signal_summary(data)
    assert data['signal_summary'] == summary()
    assert asdict(DecisionEngine().evaluate(build_decision_context(data))) == decision
    del data['signal_summary']
    assert data == before


def test_existing_notification_weights_are_unchanged():
    r = RuleEngine().evaluate(creative())
    assert r['score'] == r['technical_score'] == 2  # RSI confirmation 1 + MACD histogram 1
    assert not r['should_notify']
    assert r['level'] == '一般通知'
    assert MACDRule.rule_category == KDRule.rule_category == C.MOMENTUM
    assert RSIRule.rule_category == C.RISK
    assert SignalChangeRule.rule_category == C.EVENT
