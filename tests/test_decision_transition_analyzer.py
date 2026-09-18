from copy import deepcopy
from dataclasses import asdict, replace

from app.decision_engine import DecisionContext as C, TradingDecision as D, ActionState as A, HolderActionState as H
from app.decision_transition_analyzer import context_summary, analyze_transition
from app.decision_formatter import format_operation_reference


ZONE = {'stable_zone_id': 'support-1', 'low': 469.0, 'high': 471.0}
BASE = C(symbol='TEST', observation_time='2026-09-16', observation_complete=True,
         active_support_zone=ZONE, support_status='TESTING', macd_momentum='bearish_strengthening',
         institutional_level='STRONG_PRESSURE', volatility_level='EXTREME')


def snap(context=BASE, entry=A.WAIT, holder=H.HOLD_WITH_CAUTION, gates=(), count=1,
         candidate=None, holder_count=1, holder_candidate=None, score=0, risk=0):
    d = D(entry, holder, risk_gate=list(gates), entry_score=score, risk_score=risk)
    return context_summary(context, d, dict(confirmation_count=count, holder_confirmation_count=holder_count,
        candidate_entry_state=str(candidate or entry), candidate_holder_state=str(holder_candidate or holder)))


def analyze(old, new):
    events, debug = analyze_transition(old, new, 'TEST')
    return [asdict(e) for e in events], debug


def formatted(events, debug, entry=A.WAIT, holder=H.HOLD_WITH_CAUTION):
    return format_operation_reference(D(entry, holder, transition_events=events, transition_debug=debug), debug=True)


def test_case1_only_changed_macd_not_unchanged_flow_or_atr():
    events, debug = analyze(snap(entry=A.AVOID), snap(replace(BASE, macd_momentum='bearish_weakening')))
    assert len(events) == 1
    event = events[0]
    assert event['primary_transition_reason']['code'] == 'BEARISH_MOMENTUM_WEAKENING'
    assert event['transition_reasons'] == ['BEARISH_MOMENTUM_WEAKENING']
    text = formatted(events, debug)['state_change']
    assert '主要狀態變化依據' in text and '法人' not in text and '波動' not in text
    assert 'context.institutional_level' in debug['roles']['ENTRY']['ignored_unchanged_conditions']


def test_case2_confirmed_break_and_gate_activation():
    events, _ = analyze(snap(), snap(replace(BASE, support_status='CONFIRMED_BREAK'),
                                    entry=A.AVOID, gates=['SUPPORT_BREAK']))
    assert events[0]['primary_transition_reason']['code'] == 'CONFIRMED_SUPPORT_BREAK'
    assert 'RISK_GATE_ACTIVATED' in events[0]['transition_reasons']
    assert '469～471' in events[0]['primary_transition_reason']['text']


def test_case3_hold_and_short_trend_crossing():
    old = snap(replace(BASE, short_term_score=-2, short_term_direction=-1))
    new = snap(replace(BASE, support_status='HOLDING', short_term_score=1, short_term_direction=1), entry=A.WATCH_FOR_CONFIRMATION)
    events, _ = analyze(old, new)
    assert set(events[0]['transition_reasons']) == {'SUPPORT_HOLD_CONFIRMED', 'SHORT_TERM_TREND_IMPROVED'}


def test_case4_confirmation_is_primary():
    old = snap(entry=A.WATCH_FOR_CONFIRMATION, candidate=A.ALLOW_PROBE_ENTRY)
    new = snap(entry=A.ALLOW_PROBE_ENTRY, count=2)
    events, _ = analyze(old, new)
    assert events[0]['primary_transition_reason']['code'] == 'CONFIRMATION_COUNT_REACHED'


def test_case5_holder_break_is_primary_not_entry_gate():
    events, _ = analyze(snap(), snap(replace(BASE, support_status='CONFIRMED_BREAK'),
                                    holder=H.TIGHTEN_RISK, gates=['SUPPORT_BREAK']))
    assert len(events) == 1 and events[0]['role'] == 'HOLDER'
    assert events[0]['primary_transition_reason']['code'] == 'CONFIRMED_SUPPORT_BREAK'


def test_case6_unchanged_state_delta_debug_only():
    events, debug = analyze(snap(), snap(replace(BASE, macd_momentum='bearish_weakening')))
    result = formatted(events, debug)
    assert not events and 'state_change' not in result
    assert 'UNCHANGED' in result['debug'] and 'bearish_weakening' in result['debug']


def test_partial_gates_never_claim_total_clearance():
    events, _ = analyze(snap(entry=A.AVOID, gates=['SUPPORT_BREAK', 'EXTREME_VOLATILITY']),
                         snap(gates=['EXTREME_VOLATILITY']))
    assert events[0]['primary_transition_reason']['code'] == 'RISK_GATE_PARTIALLY_RELEASED'
    assert 'RISK_GATE_RELEASED' not in events[0]['transition_reasons']
    assert '目前支撐失守' in events[0]['primary_transition_reason']['text']


def test_reposition_is_not_reclaim_and_old_break_uses_old_price():
    new_zone = {'stable_zone_id': 'support-2', 'low': 451., 'high': 453.}
    c = replace(BASE, active_support_zone=new_zone, support_status='HOLDING',
                previous_support_zone=ZONE, previous_support_status='CONFIRMED_BREAK')
    events, _ = analyze(snap(), snap(c, entry=A.AVOID))
    event = events[0]
    assert event['primary_transition_reason']['code'] == 'CONFIRMED_SUPPORT_BREAK'
    assert '469～471' in event['primary_transition_reason']['text']
    assert 'SUPPORT_RECLAIMED' not in event['transition_reasons']
    assert 'SUPPORT_HOLD_CONFIRMED' not in event['transition_reasons']


def test_small_boundary_drift_same_id_is_not_reposition():
    events, _ = analyze(snap(entry=A.AVOID), snap(replace(BASE,
        active_support_zone=dict(ZONE, low=469.02, high=470.98), macd_momentum='bearish_weakening')))
    assert 'ACTIVE_SUPPORT_REPOSITIONED' not in events[0]['transition_reasons']


def test_strong_pressure_marginal_easing_requires_new_evidence():
    events, _ = analyze(snap(entry=A.AVOID), snap(replace(BASE, institutional_selling_weakened=True)))
    assert events[0]['primary_transition_reason']['code'] == 'INSTITUTIONAL_PRESSURE_EASING'
    assert '仍偏空' in events[0]['primary_transition_reason']['text']


def test_holder_confirmation_uses_own_count():
    old = snap(holder=H.TIGHTEN_RISK, holder_count=1, holder_candidate=H.HOLD_WITH_CAUTION)
    new = snap(holder_count=2)
    events, _ = analyze(old, new)
    assert events[0]['role'] == 'HOLDER'
    assert events[0]['primary_transition_reason']['code'] == 'CONFIRMATION_COUNT_REACHED'


def test_two_roles_have_separate_primary_evidence_and_max_three_lines():
    old = snap(entry=A.WATCH_FOR_CONFIRMATION, candidate=A.ALLOW_PROBE_ENTRY, holder=H.TIGHTEN_RISK,
               holder_candidate=H.HOLD_WITH_CAUTION, holder_count=1)
    new = snap(replace(BASE, macd_momentum='bearish_weakening', short_term_score=3),
               entry=A.ALLOW_PROBE_ENTRY, count=2, holder_count=2)
    events, debug = analyze(old, new)
    assert len(events) == 2
    assert all(e['primary_transition_reason']['code'] == 'CONFIRMATION_COUNT_REACHED' for e in events)
    text = formatted(events, debug, A.ALLOW_PROBE_ENTRY)['state_change']
    assert text.count('主要狀態變化依據') == 2 and text.count('・輔助依據') <= 4


def test_analyzer_never_evaluates_or_mutates(monkeypatch):
    from app.decision_engine import DecisionEngine
    def fail(*args):
        raise AssertionError('Analyzer must not evaluate')
    monkeypatch.setattr(DecisionEngine, 'evaluate', fail)
    old, new = snap(), snap(entry=A.AVOID)
    original = deepcopy((old, new))
    events, _ = analyze(old, new)
    assert (old, new) == original
    assert events[0]['primary_transition_reason']['code'] == 'INSUFFICIENT_TRANSITION_EVIDENCE'


def test_threshold_crossing_and_neutral_classification():
    events, _ = analyze(snap(entry=A.WATCH_FOR_CONFIRMATION, score=2), snap(entry=A.ALLOW_PROBE_ENTRY, score=4))
    assert events[0]['primary_transition_reason']['code'] == 'ENTRY_THRESHOLD_CROSSED'
    events, _ = analyze(snap(), snap(replace(BASE, overextended=True), entry=A.DO_NOT_CHASE))
    assert events[0]['transition_type'] == 'NEUTRAL_RECLASSIFICATION'
    assert events[0]['primary_transition_reason']['code'] == 'OVEREXTENSION_TRIGGERED'


def test_missing_baseline_no_fake_unknown_event():
    events, debug = analyze(None, snap())
    assert events == [] and debug['status'] == 'BASELINE_UNAVAILABLE'


def test_changed_candidate_count_cannot_claim_confirmation_reached():
    events, _ = analyze(snap(entry=A.WATCH_FOR_CONFIRMATION, candidate=A.ENTRY_CONDITION_MET),
                         snap(entry=A.ALLOW_PROBE_ENTRY, count=2))
    assert 'CONFIRMATION_COUNT_REACHED' not in events[0]['transition_reasons']


def test_gate_replacement_cannot_claim_all_risk_released():
    events, debug = analyze(snap(entry=A.AVOID, gates=['SUPPORT_BREAK']),
                            snap(gates=['EXTREME_VOLATILITY']))
    assert 'RISK_GATE_RELEASED' not in events[0]['transition_reasons']
    assert debug['context_delta']['gates_added'] == ['EXTREME_VOLATILITY']
    assert debug['context_delta']['gates_removed'] == ['SUPPORT_BREAK']


def test_same_id_reclaim_and_resistance_breakout():
    events, _ = analyze(snap(replace(BASE, support_status='CONFIRMED_BREAK'), entry=A.AVOID),
                         snap(replace(BASE, support_status='RECLAIMED')))
    assert events[0]['primary_transition_reason']['code'] == 'SUPPORT_RECLAIMED'
    resistance = dict(ZONE, stable_zone_id='resistance-1', low=479., high=481.)
    old = replace(BASE, active_resistance_zone=resistance, resistance_status='TESTING')
    new = replace(old, active_resistance_zone=None, resistance_status='UNKNOWN',
                  previous_resistance_zone=resistance, previous_resistance_status='CONFIRMED_BREAKOUT')
    events, _ = analyze(snap(old), snap(new, entry=A.WATCH_FOR_CONFIRMATION))
    assert events[0]['primary_transition_reason']['code'] == 'RESISTANCE_BREAKOUT_CONFIRMED'
    assert '479～481' in events[0]['primary_transition_reason']['text']
