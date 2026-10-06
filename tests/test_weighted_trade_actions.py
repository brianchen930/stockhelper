from dataclasses import asdict, replace
import json
import sqlite3

import pytest

from app.decision_engine import DecisionConfig, DecisionEngine
from app.decision_formatter import format_trade_recommendation
from tests.test_trade_actions import BASE, monitor


NEUTRAL = replace(BASE, position_status='HOLDING', short_term_direction=0,
                  medium_term_direction=0, short_term_score=0, medium_term_score=0,
                  support_status='TESTING', macd_momentum='neutral', kd_state='NEUTRAL')


def assess(**changes):
    return DecisionEngine().evaluate(replace(NEUTRAL, **changes))


@pytest.mark.parametrize('changes,action,label', [
    (dict(medium_term_direction=1, support_status='HOLDING'), 'HOLD', '持有'),
    (dict(macd_momentum='bullish_weakening'), 'OBSERVE', '觀察'),
    (dict(medium_term_direction=-1, support_status='CONFIRMED_BREAK', current_price=90,
          macd_momentum='bearish_weakening', institutional_level='STRONG_SUPPORT', short_term_direction=1), 'CONSIDER_REDUCE', '考慮減碼'),
    (dict(medium_term_direction=-1, support_status='CONFIRMED_BREAK', current_price=90), 'REDUCE', '減碼'),
    (dict(medium_term_direction=-1, macd_momentum='bearish_strengthening', institutional_level='BEARISH',
          support_status='CONFIRMED_BREAK', current_price=90), 'EXIT', '退出'),
])
def test_five_weighted_action_bands(changes, action, label):
    d = assess(**changes)
    assert d.decision == action
    assert format_trade_recommendation(asdict(d))[1] == '目前動作：' + label
    evidence = d.trade_evidence
    assert evidence['weighted_score'] == pytest.approx(sum(x['points'] for x in evidence['contributions']))
    assert evidence['version'] == 4


@pytest.mark.parametrize('changes', [
    dict(medium_term_direction=-1), dict(short_term_direction=-1),
    dict(support_status='CONFIRMED_BREAK', current_price=90),
    dict(macd_momentum='bearish_strengthening'), dict(institutional_level='STRONG_PRESSURE'),
    dict(relative_market_strength=-10), dict(volatility_level='EXTREME'),
    dict(rsi_state='OVERSOLD'), dict(kd_state='BEARISH'),
])
def test_no_individual_signal_sells_even_with_extreme_atr(changes):
    for extreme in (False, True):
        d = assess(**{**changes, **({'atr_percent': 15} if extreme else {})})
        assert d.decision == 'OBSERVE'
        assert len(d.trade_evidence['risk_dimensions']) <= 1


def test_healthy_structure_offsets_short_term_weakness():
    d = assess(medium_term_direction=1, support_status='HOLDING',
               short_term_direction=-1, macd_momentum='bullish_weakening',
               kd_state='BEARISH', rsi_state='OVERBOUGHT')
    assert d.decision == 'HOLD'
    assert '中期趨勢偏多' in d.reasons
    assert any('仍守穩' in reason for reason in d.reasons)
    assert d.trade_evidence['dimension_scores']['trend'] < 0
    assert len(d.trade_evidence['risk_dimensions']) == 1


def test_support_and_medium_outweigh_oscillators():
    d = assess(medium_term_direction=-1, support_status='CONFIRMED_BREAK', current_price=90,
               rsi_state='OVERBOUGHT', kd_state='BEARISH')
    weights = {x['code']: x['points'] for x in d.trade_evidence['contributions']}
    assert weights['SUPPORT_BREAK'] > weights['MEDIUM_TREND'] > weights['RSI'] + weights['KD']
    weaker_support = assess(support_strength=1, support_status='CONFIRMED_BREAK', current_price=90)
    assert weights['SUPPORT_BREAK'] > weaker_support.trade_evidence['weighted_score']


def test_correlated_momentum_does_not_supply_three_confirmations():
    d = assess(macd_momentum='bearish_strengthening', kd_state='BEARISH', rsi_state='OVERSOLD')
    assert d.trade_evidence['weighted_score'] >= 3
    assert d.trade_evidence['risk_dimensions'] == ['momentum']
    assert d.decision == 'OBSERVE'


def test_joint_break_macd_flow_and_relative_weakness_escalate():
    c = dict(support_status='CONFIRMED_BREAK', current_price=90,
             medium_term_direction=-1, macd_momentum='bullish_weakening', short_term_direction=1)
    assert assess(**c).decision == 'REDUCE'
    stronger = assess(**c, institutional_level='STRONG_PRESSURE', relative_market_strength=-5)
    assert stronger.decision == 'EXIT'
    assert stronger.trade_evidence['weighted_score'] > assess(**c).trade_evidence['weighted_score']


def test_atr_amplifies_risk_without_new_direction_or_confirmation():
    c = dict(medium_term_direction=-1, support_status='CONFIRMED_BREAK', current_price=90,
             institutional_level='BULLISH', macd_momentum='bearish_weakening', kd_state='BULLISH')
    low, high = assess(**c), assess(**c, atr_percent=9)
    assert low.decision == 'CONSIDER_REDUCE'
    assert high.decision == 'REDUCE'
    assert high.trade_evidence['risk_dimensions'] == low.trade_evidence['risk_dimensions']
    healthy = assess(medium_term_direction=1, support_status='HOLDING', atr_percent=9)
    assert healthy.decision == 'HOLD'
    assert 'ATR_RISK_AMPLIFIER' not in healthy.trade_evidence['key_signal_codes']
    assert not any('ATR' in w for w in healthy.warnings)
    assert high.trade_evidence['volatility_effect']['affects_action']
    assert not healthy.trade_evidence['volatility_effect']['affects_action']


@pytest.mark.parametrize('cost', [50, 100, 200, None])
def test_cost_only_changes_management_not_scores_or_key_reasons(cost):
    c = dict(medium_term_direction=-1, institutional_level='STRONG_PRESSURE',
             support_status='CONFIRMED_BREAK', current_price=90, macd_momentum='bearish_strengthening')
    baseline, d = assess(**c), assess(**c, average_cost=cost, unrealized_return=-99)
    assert baseline.decision == d.decision == 'EXIT'
    assert baseline.reasons == d.reasons
    for key in ('weighted_score', 'contributions', 'risk_dimensions'):
        assert baseline.trade_evidence[key] == d.trade_evidence[key]
    assert not any('不因虧損而攤平' in w for w in d.warnings)


def test_freshness_and_adjusted_probabilities_never_duplicate_flow():
    c = dict(medium_term_direction=-1, institutional_level='STRONG_PRESSURE')
    baseline = assess(**c)
    duplicated = assess(**c, institutional_score=-99, adjusted_support_probability=.01,
                        adjusted_support_level='VERY_LOW', institutional_selling_weakened=True)
    assert baseline.trade_evidence['weighted_score'] == duplicated.trade_evidence['weighted_score']
    stale = assess(**c, institutional_freshness='STALE')
    assert 'institutional' not in stale.trade_evidence['risk_dimensions']
    assert not any('法人賣壓' in r for r in stale.reasons)


def test_compact_reasons_are_ranked_nonzero_current_contributions():
    d = assess(medium_term_direction=-1, short_term_direction=-1,
               support_status='CONFIRMED_BREAK', current_price=90,
               macd_momentum='bearish_strengthening', institutional_level='STRONG_PRESSURE',
               relative_market_strength=-4, rsi_state='OVERBOUGHT', kd_state='BEARISH')
    e = d.trade_evidence
    assert len(d.reasons) <= 4 < len(e['contributions'])
    selected = [x for x in e['contributions'] if x['code'] in e['key_signal_codes']]
    assert set(d.reasons) == {x['text'] for x in selected}
    assert all(x['points'] != 0 for x in selected)
    assert 'RSI' not in e['key_signal_codes'] and 'KD' not in e['key_signal_codes']


@pytest.mark.parametrize('threshold,score,below,at', [
    ('trade_observe_score', 1., 'HOLD', 'OBSERVE'),
    ('trade_consider_reduce_score', 3., 'OBSERVE', 'CONSIDER_REDUCE'),
    ('trade_reduce_score', 6., 'CONSIDER_REDUCE', 'REDUCE'),
    ('trade_exit_score', 9., 'REDUCE', 'EXIT'),
])
def test_threshold_boundaries(threshold, score, below, at):
    # Three independent adverse dimensions plus a small protective oscillator.
    c = replace(NEUTRAL, medium_term_direction=-1, institutional_level='BEARISH',
                macd_momentum='bearish_strengthening', kd_state='BULLISH', current_price=90,
                structural_support_zone=dict(low=98., high=100., strength_score=7.),
                structural_support_status='CONFIRMED_BREAK')
    # Baseline net 12.25 includes the confirmed structural break.
    values = dict(trade_observe_score=1., trade_consider_reduce_score=3., trade_reduce_score=6., trade_exit_score=9.)
    shift = 12.25 - score
    values = {key: value + shift for key, value in values.items()}
    # OBSERVE boundary needs lower risk to keep every threshold positive.
    if threshold == 'trade_observe_score':
        values = dict(trade_observe_score=12.25, trade_consider_reduce_score=13., trade_reduce_score=14., trade_exit_score=15.)
    elif threshold == 'trade_exit_score':
        values = dict(trade_observe_score=.5, trade_consider_reduce_score=2., trade_reduce_score=5., trade_exit_score=12.25)
    assert DecisionEngine(DecisionConfig(**values)).evaluate(c).decision == at
    values[threshold] += .0001
    assert DecisionEngine(DecisionConfig(**values)).evaluate(c).decision == below


def test_invalid_threshold_order_is_rejected():
    with pytest.raises(ValueError, match='strictly increasing'):
        DecisionConfig(trade_reduce_score=3.)


def test_consider_reduce_recovery_restart_replay_and_immediate_worsening(tmp_path):
    c = replace(NEUTRAL, medium_term_direction=-1, support_status='CONFIRMED_BREAK', current_price=90,
                macd_momentum='bearish_weakening', institutional_level='STRONG_SUPPORT', short_term_direction=1)
    path = tmp_path / 'weighted.db'
    with sqlite3.connect(path) as conn:
        assert monitor(conn, c)['decision'] == 'CONSIDER_REDUCE'
        healthy = replace(BASE, position_status='HOLDING', observation_time='2026-09-16')
        first = monitor(conn, healthy)
        assert first['decision'] == 'CONSIDER_REDUCE'
        assert first['trade_confirmation_count'] == 1
        assert monitor(conn, healthy)['trade_confirmation_count'] == 1
    with sqlite3.connect(path) as conn:
        recovered = replace(healthy, observation_time='2026-09-17')
        assert monitor(conn, recovered)['decision'] == 'HOLD'
        rows = conn.execute('SELECT count(*) FROM trading_decision_history').fetchone()[0]
        assert monitor(conn, recovered)['decision'] == 'HOLD'
        assert conn.execute('SELECT count(*) FROM trading_decision_history').fetchone()[0] == rows
        worsening = replace(c, observation_time='2026-09-18', support_status='CONFIRMED_BREAK', current_price=90,
                            institutional_level='STRONG_PRESSURE')
        assert monitor(conn, worsening)['decision'] == 'EXIT'
        memory = json.loads(conn.execute('SELECT trade_memory FROM decision_state').fetchone()[0])
        assert memory['version'] == 4
