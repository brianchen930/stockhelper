from dataclasses import replace, asdict
import json
import sqlite3
import pytest
from app.decision_engine import DecisionEngine, DecisionContext as C, TradingDecision, ActionState as A, HolderActionState as H
from app.decision_state import stabilize, update_monitor_decision, create_table
from app.decision_formatter import format_operation_reference

OLD = {'stable_zone_id': 'old', 'low': 469., 'high': 471.}
NEW = {'stable_zone_id': 'new', 'low': 484., 'high': 490.}
BASE = C(symbol='TEST', observation_time='2026-09-16', observation_complete=True, current_price=492.5,
         active_support_zone=NEW, support_status='HOLDING', current_active_support_status='HOLDING',
         previous_support_zone=OLD, previous_support_status='CONFIRMED_BREAK',
         institutional_level='BEARISH', atr=4, volatility_level='NORMAL',
         short_term_direction=1, short_term_score=2, medium_term_direction=1, medium_term_score=3,
         macd_momentum='bearish_weakening')

def evaluate(c=BASE, previous=None):
    return stabilize(DecisionEngine().evaluate(c), c, previous or {})

def improved(**kwargs):
    return replace(BASE, previous_support_status='UNKNOWN', previous_support_zone=None, **kwargs)

def run(connection, context):
    data = {'decision_context': asdict(context), 'timeframe_analysis': {}}
    update_monitor_decision(data, connection)
    return data

def test_old_trigger_and_new_defense_are_separate():
    d, _ = evaluate()
    assert d.holder_action == H.REDUCE_EXPOSURE
    e = d.current_trigger_evidence
    assert e['trigger_zone']['stable_zone_id'] == 'old'
    assert e['current_defense_zone']['stable_zone_id'] == 'new'
    text = format_operation_reference(d)['for_holder']
    assert '原支撐 469.00～471.00 已確認失守' in text
    assert '484.00～490.00 為目前防守區' in text
    assert '484.00～490.00 已確認失守' not in text

def test_tighten_without_break_not_reduce():
    d, _ = evaluate(improved(short_term_direction=-1, short_term_score=-3, macd_momentum='bearish_strengthening',
                            institutional_level='STRONG_PRESSURE', volatility_level='EXTREME'))
    assert d.holder_action == H.TIGHTEN_RISK
    assert d.current_trigger_evidence['matched_rule_id'] == 'RISK_SCORE_THRESHOLD'
    assert '降低曝險' not in format_operation_reference(d)['for_holder']

def test_reduce_records_only_actual_branch_inputs():
    d, _ = evaluate(replace(BASE, volatility_level='EXTREME', rsi_state='OVERSOLD', kd_state='BEARISH'))
    assert [r['code'] for r in d.current_trigger_evidence['triggered_conditions']] == ['PREVIOUS_SUPPORT_BREAK_CONFIRMED', 'INSTITUTIONAL_BEARISH']

@pytest.mark.parametrize('state', [H.REDUCE_EXPOSURE, H.EXIT_CONDITION_APPROACHING, H.TIGHTEN_RISK])
def test_missing_evidence_falls_back_with_warning(state):
    d, saved = stabilize(TradingDecision(A.WAIT, state), replace(BASE, rsi_state='OVERSOLD'), {})
    assert d.holder_action == H.HOLD_WITH_CAUTION and d.state_basis == 'INSUFFICIENT_EVIDENCE'
    assert str(state) + '_WITHOUT_TRIGGER_EVIDENCE' in d.consistency_warnings
    assert saved['holder_state'] == 'HOLD_WITH_CAUTION'

def test_exit_approaching_never_claims_exit_position():
    d, _ = evaluate(replace(BASE, medium_term_direction=-1, macd_momentum='bearish_strengthening', institutional_level='STRONG_PRESSURE'))
    assert d.holder_action == H.EXIT_CONDITION_APPROACHING
    assert {r['code'] for r in d.current_trigger_evidence['triggered_conditions']} == {
        'PREVIOUS_SUPPORT_BREAK_CONFIRMED', 'MEDIUM_TERM_BEARISH', 'BEARISH_MACD_ACCELERATION', 'STRONG_INSTITUTIONAL_PRESSURE'}
    assert d.current_trigger_evidence['trade_thesis_assessment']['trade_thesis_invalidated'] is None
    assert '不代表出清條件已成立' in format_operation_reference(d)['for_holder']

def test_retained_is_not_presented_as_current_break():
    _, old = evaluate()
    d, state = evaluate(improved(), old)
    assert d.holder_action == H.REDUCE_EXPOSURE and d.state_basis == 'RETAINED_PENDING_CONFIRMATION'
    assert d.current_trigger_evidence['holder_state'] == 'HOLD_WITH_CAUTION'
    assert d.retained_state_evidence['trigger_zone']['stable_zone_id'] == 'old'
    text = format_operation_reference(d, debug=True)
    assert '原風險條件已部分緩解' in text['for_holder']
    assert '原支撐 469.00～471.00 已確認失守' not in text['for_holder']
    assert '[CURRENT ACTION EVIDENCE]' in text['debug']

@pytest.mark.parametrize('status, reason', [('RECLAIMED_SUPPORT', 'TRIGGER_ZONE_RECLAIMED'),
                                          ('ACTIVE_SUPPORT', 'LIFECYCLE_SUPPORT_RECONFIRMED'),
                                          ('RESISTANCE_TO_SUPPORT', 'TRIGGER_ZONE_RECLAIMED')])
def test_lifecycle_repair_invalidates_before_ttl(status, reason):
    _, old = evaluate()
    d, _ = evaluate(improved(active_support_zone=dict(OLD, status=status, current_role='SUPPORT')), old)
    assert d.holder_action == H.HOLD_WITH_CAUTION and not d.retained_state_evidence
    assert reason in d.consistency_warnings

def test_new_zone_holding_is_not_old_zone_reclaim():
    _, old = evaluate()
    d, _ = evaluate(improved(active_support_zone=dict(NEW, status='RECLAIMED_SUPPORT')), old)
    assert d.state_basis == 'RETAINED_PENDING_CONFIRMATION'

def test_two_new_observations_release_retention():
    _, old = evaluate()
    first, state = evaluate(improved(observation_time='2026-09-17'), old)
    assert first.state_basis == 'RETAINED_PENDING_CONFIRMATION'
    second, _ = evaluate(improved(observation_time='2026-09-18'), state)
    assert second.holder_action == H.HOLD_WITH_CAUTION and not second.retained_state_evidence
    assert 'IMPROVEMENT_CONFIRMATION_REACHED' in second.consistency_warnings

def test_new_opposite_confirmation_supersedes():
    _, old = evaluate()
    d, _ = evaluate(improved(resistance_status='CONFIRMED_BREAKOUT', volume_state='EXPANDING', macd_momentum='bullish_strengthening'), old)
    assert d.holder_action != H.REDUCE_EXPOSURE
    assert 'SUPERSEDED_BY_CONFIRMED_BULLISH_STRUCTURE' in d.consistency_warnings

def test_expiry_uses_original_time_not_refresh():
    _, old = evaluate()
    d, state = evaluate(improved(observation_time='2026-09-20', observation_complete=False), old)
    assert d.state_basis == 'RETAINED_PENDING_CONFIRMATION'
    assert d.retained_state_evidence['observation_time'] == '2026-09-16'
    d, state = evaluate(improved(observation_time='2026-09-26', observation_complete=False), state)
    assert d.holder_action == H.HOLD_WITH_CAUTION and 'EVIDENCE_MAX_AGE_REACHED' in d.consistency_warnings

def test_restart_and_replay_restore_retention(tmp_path):
    path = tmp_path / 'evidence.db'
    with sqlite3.connect(path) as c:
        run(c, BASE)
        first = run(c, improved())['trading_decision']
    with sqlite3.connect(path) as c:
        second = run(c, improved())['trading_decision']
        assert second['retained_state_evidence'] == first['retained_state_evidence']
        assert second['state_basis'] == 'RETAINED_PENDING_CONFIRMATION'
        assert not second['transition_events']

def test_current_evidence_and_transition_are_independent():
    with sqlite3.connect(':memory:') as c:
        run(c, improved(institutional_level='NEUTRAL'))
        context = replace(BASE, institutional_level='NEUTRAL')
        first = run(c, context)
        assert first['trading_decision']['holder_action'] == 'TIGHTEN_RISK'
        assert first['trading_decision']['transition_events']
        second = run(c, context)
        assert not second['trading_decision']['transition_events']
        assert '已確認失守' in second['timeframe_analysis']['operation_reference']['for_holder']

def test_formatter_legacy_payload_does_not_claim_reduce():
    d = TradingDecision(A.WAIT, H.REDUCE_EXPOSURE)
    text = format_operation_reference(d, debug=True)
    assert text['holder_label'] == '證據不足'
    assert 'REDUCE_EXPOSURE_WITHOUT_TRIGGER_EVIDENCE' in text['debug']
    assert d.holder_action == H.REDUCE_EXPOSURE

def test_missing_legacy_memory_falls_back_and_schema_migrates():
    old = dict(symbol='TEST', entry_state='WAIT', holder_state='REDUCE_EXPOSURE', candidate_entry_state='WAIT',
               candidate_holder_state='REDUCE_EXPOSURE', confirmation_count=1, holder_confirmation_count=1,
               last_observation_time='2026-09-16')
    d, _ = evaluate(improved(), old)
    assert d.holder_action == H.HOLD_WITH_CAUTION
    assert 'REDUCE_EXPOSURE_WITHOUT_TRIGGER_EVIDENCE' in d.consistency_warnings
    with sqlite3.connect(':memory:') as c:
        c.execute('''CREATE TABLE decision_state (symbol TEXT PRIMARY KEY, entry_state TEXT, holder_state TEXT,
            candidate_entry_state TEXT, candidate_holder_state TEXT, confirmation_count INTEGER NOT NULL DEFAULT 0,
            holder_confirmation_count INTEGER NOT NULL DEFAULT 0, last_observation_time TEXT,
            previous_context_summary TEXT, context_fingerprint TEXT)''')
        create_table(c)
        create_table(c)
        assert 'holder_evidence_summary' in {r[1] for r in c.execute('PRAGMA table_info(decision_state)')}

def test_compact_memory_does_not_store_future_conditions():
    _, old = evaluate()
    _, state = evaluate(improved(), old)
    memory = json.loads(state['holder_evidence_summary'])
    assert 'worsen_conditions' not in memory['current_trigger_evidence']
    assert 'improve_conditions' not in memory['retained_state_evidence']

def test_current_break_not_claimed_above_active_support():
    d, _ = evaluate(improved(support_status='CONFIRMED_BREAK', current_active_support_status='CONFIRMED_BREAK'))
    assert d.holder_action not in (H.REDUCE_EXPOSURE, H.EXIT_CONDITION_APPROACHING)
    assert not d.current_trigger_evidence['trigger_zone']

def test_already_existing_opposite_conditions_do_not_supersede():
    c = replace(BASE, resistance_status='CONFIRMED_BREAKOUT', volume_state='EXPANDING', macd_momentum='bullish_strengthening')
    _, old = evaluate(c)
    d, _ = evaluate(replace(c, previous_support_status='UNKNOWN', previous_support_zone=None), old)
    assert d.state_basis == 'RETAINED_PENDING_CONFIRMATION'

def test_non_nearest_trigger_lifecycle_repair_still_invalidates():
    _, old = evaluate()
    c = improved(zone_lifecycle_statuses={'old': {'status': 'RECLAIMED_SUPPORT', 'current_role': 'SUPPORT'}})
    d, _ = evaluate(c, old)
    assert d.holder_action == H.HOLD_WITH_CAUTION
    assert 'TRIGGER_ZONE_RECLAIMED' in d.consistency_warnings

def test_role_flip_to_resistance_does_not_clear_old_break():
    _, old = evaluate()
    c = improved(zone_lifecycle_statuses={'old': {'status': 'SUPPORT_TO_RESISTANCE', 'current_role': 'RESISTANCE'}})
    d, _ = evaluate(c, old)
    assert d.state_basis == 'RETAINED_PENDING_CONFIRMATION'
