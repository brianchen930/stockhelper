from dataclasses import replace, asdict
import sqlite3
import pytest
from app.decision_engine import (ActionState as A, HolderActionState as H,
    DecisionContext as C, DecisionEngine, ReasonCode as R)
from app.decision_context import classify_support, build_decision_context
from app.decision_formatter import format_operation_reference
from app.decision_state import stabilize, update_monitor_decision


BASE = C(short_term_direction=-1, short_term_score=-4, medium_term_direction=1,
         medium_term_score=5, observation_time='2026-09-14', observation_complete=True)


def test_same_trends_different_actions():
    a = replace(BASE, support_probability='低', institutional_level='STRONG_PRESSURE', volatility_level='極高波動')
    b = replace(BASE, support_probability='高', institutional_level='NEUTRAL', volatility_level='中等波動', macd_momentum='bearish_weakening')
    da, db = map(DecisionEngine().evaluate, (a, b))
    assert (da.entry_action, da.holder_action) == (A.AVOID, H.TIGHTEN_RISK)
    assert (db.entry_action, db.holder_action) == (A.WATCH_FOR_CONFIRMATION, H.HOLD_WITH_CAUTION)
    assert format_operation_reference(da) != format_operation_reference(db)


def bullish():
    return replace(BASE, short_term_direction=1, short_term_score=6, medium_term_score=6,
        institutional_level='BULLISH', macd_momentum='bullish_strengthening',
        price_above_ma5=True, price_above_ma20=True, price_above_ma60=True, resistance_status='CONFIRMED_BREAKOUT',
        resistance_strength=7,
        volume_state='EXPANDING', volatility_level='中等波動', atr=2, atr_percent=2)


def test_near_resistance_overextension():
    d = DecisionEngine().evaluate(replace(bullish(), overextended=True, distance_to_resistance=.2))
    assert d.entry_action == A.DO_NOT_CHASE


def test_breakout_two_new_observations_and_duplicate():
    c = bullish()
    raw = DecisionEngine().evaluate(c)
    assert raw.entry_action == A.ENTRY_CONDITION_MET
    first, state = stabilize(raw, c, {})
    assert first.entry_action == A.WATCH_FOR_CONFIRMATION
    again, repeat = stabilize(raw, c, state)
    assert repeat['confirmation_count'] == 1
    assert again.entry_action == first.entry_action
    second, _ = stabilize(raw, replace(c, observation_time='2026-09-15'), state)
    assert second.entry_action == A.ENTRY_CONDITION_MET


def test_confirmed_break_and_recovery():
    bad = replace(BASE, support_status='CONFIRMED_BREAK', institutional_level='BEARISH', macd_momentum='bearish_strengthening')
    d = DecisionEngine().evaluate(bad)
    assert d.holder_action in (H.TIGHTEN_RISK, H.REDUCE_EXPOSURE)
    recovered = replace(bad, support_status='RECLAIMED', institutional_selling_weakened=True, macd_momentum='bearish_weakening')
    assert DecisionEngine().evaluate(recovered).entry_action == A.WATCH_FOR_CONFIRMATION


def test_deterministic_and_debug():
    d = DecisionEngine().evaluate(bullish())
    assert d == DecisionEngine().evaluate(bullish())
    assert format_operation_reference(d) == format_operation_reference(d)
    assert 'debug' not in format_operation_reference(d)
    assert 'Risk Gate:' in format_operation_reference(d, debug=True)['debug']


@pytest.mark.parametrize('changes', [dict(support_probability='極低'), dict(institutional_level='STRONG_PRESSURE'),
    dict(volatility_level='極高波動'), dict(macd_momentum='bearish_strengthening'), dict(support_status='CONFIRMED_BREAK')])
def test_risk_gates_block_high_score(changes):
    d = DecisionEngine().evaluate(replace(bullish(), **changes))
    assert d.risk_gate
    assert d.entry_action not in (A.ENTRY_CONDITION_MET, A.ALLOW_PROBE_ENTRY)


def test_adjusted_probability_and_raw_score_not_counted_twice():
    c = replace(bullish(), support_probability='高', base_support_probability=.6,
                adjusted_support_probability=.8, institutional_score=3)
    d = DecisionEngine().evaluate(c)
    other = DecisionEngine().evaluate(replace(c, adjusted_support_probability=.99, institutional_score=9))
    assert d == other


def test_atr_break_distance_and_volume_confirmation():
    assert classify_support(99, 101, 100, 102, 5)[0] == 'MINOR_BREAK'
    assert classify_support(99, 101, 100, 102, 1)[0] == 'CONFIRMED_BREAK'
    assert classify_support(98, 99, 100, 102, 5, 2)[0] == 'CONFIRMED_BREAK'
    assert classify_support(101, 99, 100, 102, 5)[0] == 'RECLAIMED'
    assert classify_support(99, 101, 100, 102, 0)[0] == 'UNKNOWN'


def test_risk_immediate_even_same_bar_holder_recovery_delayed():
    c = bullish()
    _, old = stabilize(DecisionEngine().evaluate(c), c, {})
    bad = replace(c, support_status='CONFIRMED_BREAK', institutional_level='BEARISH')
    d, state = stabilize(DecisionEngine().evaluate(bad), bad, old)
    assert d.holder_action == H.REDUCE_EXPOSURE
    improved, _ = stabilize(DecisionEngine().evaluate(c), c, state)
    assert improved.holder_action == H.REDUCE_EXPOSURE


def test_sqlite_restart_and_repeated_monitoring(tmp_path):
    path = tmp_path / 'decision.db'
    def run(day):
        data = dict(decision_context=asdict(replace(bullish(), symbol='test', observation_time=day)), timeframe_analysis={})
        with sqlite3.connect(path) as connection:
            update_monitor_decision(data, connection)
        return data
    first = run('2026-09-14')
    assert first['trading_decision']['entry_action'] == A.WATCH_FOR_CONFIRMATION
    assert run('2026-09-14')['trading_decision']['confirmation_count'] == 1
    upgraded = run('2026-09-15')
    assert upgraded['trading_decision']['entry_action'] == A.ENTRY_CONDITION_MET
    assert 'state_change' in upgraded['timeframe_analysis']['operation_reference']
    assert 'state_change' not in run('2026-09-15')['timeframe_analysis']['operation_reference']


def test_incomplete_and_invalid_do_not_confirm():
    c = replace(bullish(), observation_complete=False)
    d, state = stabilize(DecisionEngine().evaluate(c), c, {})
    assert d.entry_action == A.WATCH_FOR_CONFIRMATION
    assert state['last_observation_time'] is None
    assert state['confirmation_count'] == 0
    assert DecisionEngine().evaluate(replace(c, data_valid=False)).entry_action == A.WAIT


def test_missing_context_is_not_neutral_probability():
    c = build_decision_context({})
    assert c.support_probability == 'UNKNOWN'
    assert c.institutional_level == 'UNKNOWN'
    assert not c.data_valid
