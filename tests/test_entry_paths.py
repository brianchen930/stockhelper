from copy import deepcopy
from dataclasses import asdict, replace
import json
import sqlite3

import pytest

from app.decision_engine import DecisionEngine, TradingDecision, ActionState as A
from app.decision_formatter import format_operation_reference
from app.decision_state import stabilize, update_monitor_decision
from app.entry_paths import EntryPath as P, PathStatus as S
from tests.test_decision_engine import bullish


def context(**changes):
    c = replace(bullish(), current_price=121, support_strength=7,
                active_support_zone=dict(low=100, high=103, zone_id='support'),
                active_resistance_zone=dict(low=118, high=120, zone_id='resistance'),
                support_status='DISTANT', distance_to_support=9,
                distance_to_resistance=.5)
    return replace(c, **changes)


def pullback(**changes):
    return context(current_price=103, support_status='HOLDING', distance_to_support=0,
                   resistance_status='UNKNOWN', **changes)


def evaluate(c):
    return DecisionEngine().evaluate(c)


@pytest.mark.parametrize('c,key,expected', [
    (context(), 'breakout', S.READY),
    (context(resistance_status='TESTING', current_price=119), 'breakout', S.WAITING_CONFIRMATION),
    (context(overextended=True), 'breakout', S.BLOCKED_BY_RISK),
    (context(volatility_level='EXTREME'), 'breakout', S.BLOCKED_BY_RISK),
    (context(resistance_status='REJECTED'), 'breakout', S.INVALIDATED),
    (pullback(), 'pullback', S.READY),
    (context(current_price=103, support_status='TESTING', distance_to_support=0,
             resistance_status='UNKNOWN'), 'pullback', S.WAITING_CONFIRMATION),
    (pullback(medium_term_direction=-1), 'pullback', S.INVALIDATED),
    (context(current_price=99, support_status='CONFIRMED_BREAK'), 'pullback', S.INVALIDATED),
    (context(current_price=99, support_status='MINOR_BREAK'), 'pullback', S.WAITING_CONFIRMATION),
    (pullback(macd_momentum='bullish_weakening'), 'pullback', S.WAITING_CONFIRMATION),
    (pullback(overextended=True), 'pullback', S.BLOCKED_BY_RISK),
])
def test_path_states(c, key, expected):
    assert evaluate(c).entry_paths[key]['status'] == expected


@pytest.mark.parametrize('c,ready,other,path', [
    (context(), 'breakout', 'pullback', P.BREAKOUT_ENTRY),
    (pullback(), 'pullback', 'breakout', P.PULLBACK_ENTRY),
])
def test_or_candidates_do_not_require_other_path(c, ready, other, path):
    d = evaluate(c)
    assert d.entry_paths[ready]['ready']
    assert not d.entry_paths[other]['ready']
    assert d.entry_paths['entry_ready'] and d.entry_paths['active_path'] == path
    first, state = stabilize(d, c, {})
    assert not first.entry_paths['entry_ready']
    assert first.entry_action == A.WATCH_FOR_CONFIRMATION
    c2 = replace(c, observation_time='2026-09-15')
    second, _ = stabilize(evaluate(c2), c2, state)
    assert second.entry_paths['entry_ready']
    assert second.entry_action in (A.ALLOW_PROBE_ENTRY, A.ENTRY_CONDITION_MET)


def test_both_paths_can_be_ready():
    c = context(support_status='HOLDING', distance_to_support=.5)
    d = evaluate(c)
    assert d.entry_paths['breakout']['ready'] and d.entry_paths['pullback']['ready']


def test_touch_requires_two_new_completed_observations_and_momentum():
    c = context(current_price=103, support_status='TESTING', distance_to_support=0,
                resistance_status='UNKNOWN')
    first, state = stabilize(evaluate(c), c, {})
    assert first.entry_paths['pullback']['confirmation_count'] == 1
    again, state2 = stabilize(evaluate(c), c, state)
    assert state2 == state
    assert not again.entry_paths['entry_ready']
    c2 = replace(c, observation_time='2026-09-15')
    second, _ = stabilize(evaluate(c2), c2, state)
    assert second.entry_paths['pullback']['ready']
    incomplete = replace(c2, observation_complete=False)
    assert not stabilize(evaluate(incomplete), incomplete, state)[0].entry_paths['entry_ready']
    weak = replace(c2, macd_momentum='bullish_weakening')
    assert not stabilize(evaluate(weak), weak, state)[0].entry_paths['entry_ready']


def test_path_switch_and_zone_switch_do_not_borrow_confirmation():
    c = context()
    _, state = stabilize(evaluate(c), c, {})
    changed = replace(pullback(), observation_time='2026-09-15')
    d, _ = stabilize(evaluate(changed), changed, state)
    assert not d.entry_paths['entry_ready']
    assert d.entry_paths['pullback']['confirmation_count'] == 1
    moved = replace(c, observation_time='2026-09-15',
                    active_resistance_zone=dict(low=119, high=120, zone_id='different'))
    d, _ = stabilize(evaluate(moved), moved, state)
    assert not d.entry_paths['entry_ready']


def test_failed_interaction_overrides_legacy_confirmed_status():
    c = context(active_resistance_zone=dict(low=118, high=120,
        interaction=dict(state='RESISTANCE_BREAKOUT_FAILED')))
    assert evaluate(c).entry_paths['breakout']['status'] == S.INVALIDATED
    c = context(previous_resistance_status='CONFIRMED_BREAKOUT', current_price=119,
                previous_resistance_zone=dict(low=118, high=120))
    assert not evaluate(c).entry_paths['breakout']['ready']


def test_confirmed_role_reversal_can_use_existing_pullback_path():
    c = pullback(active_support_zone=dict(low=100, high=103, status='RESISTANCE_TO_SUPPORT'))
    assert evaluate(c).entry_paths['pullback']['ready']


def test_valid_pullback_is_primary_when_breakout_failed():
    c = context(current_price=103, support_status='TESTING', distance_to_support=0,
                resistance_status='REJECTED')
    assert evaluate(c).entry_paths['primary_path'] == 'pullback'


def test_pending_support_and_confirmed_break_cannot_be_used_as_hold():
    for state in ('SUPPORT_BREAKDOWN_PENDING', 'SUPPORT_BREAKDOWN_CONFIRMED'):
        c = pullback(active_support_zone=dict(low=100, high=103, interaction=dict(state=state, location='below')))
        d = evaluate(c)
        assert not d.entry_paths['pullback']['ready']
        if state.endswith('CONFIRMED'):
            text = format_operation_reference(d)['for_non_holder']
            assert '原支撐已確認失守' in text
            assert '等待支撐確認守穩' not in text


def test_3443_paths_and_formatter_are_independent_and_serializable():
    c = context(symbol='3443', current_price=6500, atr=100,
                short_term_score=5, medium_term_score=7,
                resistance_status='TESTING', support_status='DISTANT',
                volatility_level='EXTREME', overextended=True,
                active_support_zone=dict(low=6077, high=6128),
                active_resistance_zone=dict(low=6477, high=6503))
    d = evaluate(c)
    assert d.entry_paths['breakout']['status'] == S.WAITING_CONFIRMATION
    assert d.entry_paths['pullback']['status'] == S.INACTIVE
    assert not d.entry_paths['entry_ready']
    saved = deepcopy(asdict(d))
    restored = TradingDecision(**json.loads(json.dumps(saved)))
    text = format_operation_reference(restored)['for_non_holder']
    assert '主要進場路徑：突破型' in text
    assert '目前正在測試 6477.00～6503.00 壓力區' in text
    assert '另一條獨立路徑：回檔型' in text and '6077.00～6128.00' in text
    assert '風險限制解除' in text
    assert '必要條件' not in text
    assert asdict(d) == saved


def test_path_memory_persists_restart_replay_and_stale(tmp_path):
    db = tmp_path / 'paths.db'
    def run(day):
        payload = dict(decision_context=asdict(replace(pullback(), symbol='entry-test', observation_time=day)),
                       timeframe_analysis={})
        with sqlite3.connect(db) as conn:
            update_monitor_decision(payload, conn)
        return payload['trading_decision']
    assert not run('2026-09-14')['entry_paths']['entry_ready']
    assert not run('2026-09-14')['entry_paths']['entry_ready']
    ready = run('2026-09-15')
    assert ready['entry_paths']['pullback']['ready']
    assert ready['entry_paths'] == run('2026-09-15')['entry_paths']
    assert not run('2026-09-13')['entry_paths']['entry_ready']
    assert run('2026-09-15')['entry_paths']['entry_ready']


def test_touch_confirmation_transition_explains_new_entry(tmp_path):
    c = context(current_price=103, support_status='TESTING', distance_to_support=0,
                resistance_status='UNKNOWN')
    with sqlite3.connect(tmp_path / 'touch.db') as conn:
        for day in ('2026-09-14', '2026-09-15'):
            payload = dict(decision_context=asdict(replace(c, symbol='touch', observation_time=day)),
                           timeframe_analysis={})
            update_monitor_decision(payload, conn)
    d = payload['trading_decision']
    assert d['entry_paths']['pullback']['ready']
    event = next(e for e in d['transition_events'] if e['role'] == 'ENTRY')
    assert event['primary_transition_reason']['code'] == 'CONFIRMATION_COUNT_REACHED'
