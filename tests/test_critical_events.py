from dataclasses import asdict, replace
import json
import sqlite3

import pytest

from app.decision_context import classify_support
from app.decision_engine import DecisionConfig, DecisionEngine, HolderActionState as H
from app.decision_formatter import format_trade_recommendation
from app.decision_state import stabilize
from test_signal_hysteresis import observation
from test_signal_persistence import tick, memory


def important_break(**changes):
    # Consume the real support classifier's confirmed result, not a new rule.
    status, distance = classify_support(99., 101., 100., 101., 1.)
    c = observation(99., 98., 97., 96., current_price=99., atr=1., volatility_level='NORMAL',
        support_status=status, break_distance_atr=distance, support_strength=6.,
        active_support_zone=dict(low=100., high=101., strength_score=6., stable_zone_id='support'),
        institutional_level='NEUTRAL', macd_momentum='neutral')
    return replace(c, **changes)


def evaluate(c, previous='偏多', **memory_fields):
    return DecisionEngine().evaluate(c, dict(signal_state=previous, **memory_fields))


def test_ordinary_bearish_signal_still_requires_confirmation():
    c = observation(97., 98., 99., 100., atr=1., volatility_level='NORMAL', short_term_direction=-1)
    with sqlite3.connect(':memory:') as db:
        first = tick(db, c)
        assert not first['critical_event'] and not first['bypass_persistence']
        assert first['persistence_held'] and first['final_action_state'] == '觀望'
        assert tick(db, c, minute=1)['final_action_state'] == '偏空'


@pytest.mark.parametrize('changes', [
    dict(rsi_state='OVERBOUGHT'), dict(kd_state='BEARISH'),
    dict(macd_momentum='bullish_weakening'),
    dict(macd_momentum='bearish_strengthening', macd_histogram_change_atr=-.001),
    dict(macd_momentum='bearish_strengthening', macd_histogram_change_atr=-.03),
    dict(medium_term_direction=-1), dict(institutional_level='STRONG_PRESSURE'),
    dict(relative_market_strength=-3.),
    dict(short_term_direction=-1, medium_term_direction=-1),
    dict(rsi_state='OVERBOUGHT', kd_state='BEARISH', volatility_level='EXTREME'),
    dict(support_probability='LOW', institutional_level='STRONG_PRESSURE'),
])
def test_single_weakness_and_correlated_indicators_cannot_bypass(changes):
    c = replace(observation(97., 98., 99., 100., atr=1., volatility_level='NORMAL'), **changes)
    d = evaluate(c, previous='觀望')
    assert not d.critical_event and not d.bypass_persistence
    assert d.final_action_state == '觀望' and d.signal_confirmation_count == 1


def test_confirmed_important_support_break_overrides_hysteresis():
    d = evaluate(important_break())
    assert d.holder_action == H.TIGHTEN_RISK
    assert d.raw_action_state == d.pre_bypass_candidate_state == '偏多'
    assert d.hysteresis_candidate_state == d.signal_hysteresis['final_action_state'] == '偏多'
    assert d.critical_event and d.bypass_persistence and d.bypass_hysteresis
    assert d.critical_event_rules == ['IMPORTANT_SUPPORT_CONFIRMED_BREAK']
    assert d.final_action_state == '偏空' and not d.persistence_held
    assert d.pending_candidate_state is None and d.signal_confirmation_count == 0
    assert d.signal_persistence['status'] == 'CRITICAL_BYPASS'


@pytest.mark.parametrize('changes', [
    dict(observation_complete=False), dict(data_valid=False),
    dict(support_status='MINOR_BREAK'), dict(support_strength=3.),
    dict(active_support_zone=None), dict(current_price=101.),
    dict(support_status='TESTING'), dict(support_status='RECLAIMED'),
])
def test_unconfirmed_intraday_unimportant_or_reclaimed_support_cannot_bypass(changes):
    d = evaluate(important_break(**changes))
    assert not d.critical_event and not d.bypass_persistence


def test_previous_support_uses_its_own_strength_and_current_price():
    c = important_break(support_status='DISTANT', previous_support_status='CONFIRMED_BREAK',
        previous_support_zone=dict(low=100., high=101., strength_score=6.),
        active_support_zone=dict(low=95., high=96.), support_strength=1.)
    assert evaluate(c).bypass_persistence
    assert not evaluate(replace(c, previous_support_zone=dict(low=100., high=101., strength_score=1.),
                                support_strength=9.)).critical_event
    assert not evaluate(replace(c, current_price=102.)).critical_event


@pytest.mark.parametrize('delta,expected', [(-.02, True), (-.03, True), (-.001, False), (None, False)])
def test_medium_bearish_requires_measured_macd_acceleration(delta, expected):
    c = observation(99.9, atr=1., volatility_level='NORMAL', medium_term_direction=-1,
        macd_momentum='bearish_strengthening', macd_histogram_change_atr=delta)
    d = evaluate(c)
    assert d.critical_event == d.bypass_persistence == expected
    if expected:
        assert 'MEDIUM_BEARISH_WITH_MACD_ACCELERATION' in d.critical_event_rules
        assert d.final_action_state == '偏空'


@pytest.mark.parametrize('changes,state', [
    (dict(institutional_level='BEARISH', medium_term_direction=-1), H.REDUCE_EXPOSURE),
    (dict(institutional_level='STRONG_PRESSURE', medium_term_direction=-1,
          macd_momentum='bearish_strengthening', macd_histogram_change_atr=-.03), H.EXIT_CONDITION_APPROACHING),
])
def test_holding_current_risk_rule_is_reused(changes, state):
    c = important_break(position_status='HOLDING', support_strength=1.,
        structural_support_zone=dict(low=100., high=101., methods=['swing_low']),
        structural_support_status='CONFIRMED_BREAK', **changes)
    d = evaluate(c)
    assert d.holder_action == state
    assert 'HOLDER_' + state in d.critical_event_rules
    assert d.final_action_state == '偏空' and d.bypass_persistence


def test_multiple_independent_strong_risks_bypass_without_support_break():
    c = observation(99.9, current_price=99.9, atr=1., volatility_level='NORMAL', medium_term_direction=-1,
        institutional_level='STRONG_PRESSURE', relative_market_strength=-3.)
    d = evaluate(c)
    assert d.critical_event_rules == ['MULTIPLE_INDEPENDENT_HIGH_RISKS']
    assert set(d.critical_event_details['risk_dimensions']) == {'trend', 'institutional', 'relative_market'}
    assert d.trade_evidence['market_action'] == 'OBSERVE'  # Signal risk is separate from structural selling permission.
    assert d.final_action_state == '偏空' and d.bypass_persistence
    # Two dimensions / a stale institutional feed cannot supply three votes.
    assert not evaluate(replace(c, relative_market_strength=None)).critical_event
    assert not evaluate(replace(c, institutional_freshness='STALE')).critical_event


def test_bypass_clears_pending_and_survives_restart_with_normal_recovery(tmp_path):
    path = tmp_path / 'bypass.db'
    with sqlite3.connect(path) as db:
        pending = tick(db, observation(101.))
        assert pending['pending_candidate_state'] == '偏多'
        bypass = tick(db, important_break(), minute=1)
        assert bypass['pre_bypass_candidate_state'] == '偏多'
        assert bypass['persistence_cleared'] and bypass['bypass_persistence']
        saved = memory(db)
        assert saved['signal_state'] == '偏空'
        assert saved['signal_pending_candidate_state'] is None and saved['signal_confirmation_count'] == 0
        payload = json.loads(db.execute('SELECT decision_json FROM trading_decision_history ORDER BY id DESC').fetchone()[0])
        assert payload['decision']['critical_event_rules'] == ['IMPORTANT_SUPPORT_CONFIRMED_BREAK']
        assert payload['decision']['persistence_cleared']
    with sqlite3.connect(path) as db:
        # The event remaining active cannot accumulate a bullish recovery.
        continuing = tick(db, important_break(), minute=2)
        assert continuing['critical_event'] and not continuing['bypass_persistence']
        assert continuing['final_action_state'] == '偏空' and continuing['signal_confirmation_count'] == 0
        improved = tick(db, observation(101.), minute=3)
        assert not improved['critical_event'] and not improved['bypass_persistence']
        assert improved['persistence_held'] and improved['final_action_state'] == '偏空'
        assert improved['signal_confirmation_count'] == 1
        assert tick(db, observation(101.), minute=4)['final_action_state'] == '偏多'


def test_bullish_signal_without_critical_risk_cannot_bypass():
    d = evaluate(observation(101.), previous='偏空')
    assert not d.critical_event and not d.bypass_persistence
    assert d.final_action_state == '偏空' and d.persistence_held


def test_risk_relief_inside_hysteresis_band_does_not_accumulate_recovery():
    d = evaluate(observation(100.1, 100., 101., 102.), previous='偏空')
    assert not d.critical_event and d.hysteresis_held
    assert d.final_action_state == '偏空' and d.signal_confirmation_count == 0


def test_retained_holder_risk_does_not_retrigger_critical_event():
    engine = DecisionEngine()
    risk = important_break(position_status='HOLDING', institutional_level='BEARISH', medium_term_direction=-1)
    _, state = stabilize(engine.evaluate(risk), risk, {'signal_state': '偏多'})
    improved = observation(103., 102., 101., 100., position_status='HOLDING',
        atr=1., volatility_level='NORMAL', short_term_direction=1, medium_term_direction=1,
        institutional_level='NEUTRAL', macd_momentum='bullish_strengthening')
    d, _ = stabilize(engine.evaluate(improved), improved, state,
        signal_observation_time='2026-09-24T01:00:00+00:00')
    assert d.holder_action == H.REDUCE_EXPOSURE  # Existing retained holder evidence.
    assert not d.critical_event and not d.bypass_persistence
    assert d.final_action_state == '偏空' and d.persistence_held


def test_stale_or_same_round_critical_observation_cannot_change_saved_state():
    with sqlite3.connect(':memory:') as db:
        tick(db)
        before = memory(db)
        stale = tick(db, replace(important_break(), observation_time='2026-09-23'), minute=1)
        assert not stale['bypass_persistence'] and memory(db) == before
        replay = tick(db, important_break())
        assert not replay['bypass_persistence']
        assert replay['final_action_state'] == '觀望' and replay['signal_confirmation_count'] == 1


def test_formatter_reads_bypass_audit_without_re_evaluation(monkeypatch):
    d = asdict(evaluate(important_break()))
    import app.critical_events as events
    def forbidden(*args, **kwargs):
        raise AssertionError('formatter must not classify risk')
    monkeypatch.setattr(events, 'evaluate_critical_event', forbidden)
    assert any('重大風險' in line and '偏空' in line for line in format_trade_recommendation(d))


@pytest.mark.parametrize('invalid', [0, 1, 2, 3., True])
def test_independent_risk_requirement_cannot_be_weakened_to_one_vote(invalid):
    with pytest.raises(ValueError):
        DecisionConfig(critical_min_risk_dimensions=invalid)
