"""Acceptance coverage for scored entries and one/two completed daily bars."""
from dataclasses import asdict, replace
import sqlite3

import pytest

from app.decision_engine import DecisionConfig, DecisionEngine, ActionState as A
from app.decision_state import stabilize, update_monitor_decision
from app.decision_context import build_decision_context
from app.decision_formatter import format_operation_reference
from tests.test_entry_paths import context, pullback


def run(c, state=None, config=None):
    cfg = config or DecisionConfig()
    return stabilize(DecisionEngine(cfg).evaluate(c), c, state or {}, cfg)


@pytest.mark.parametrize('factory', [context, pullback])
@pytest.mark.parametrize('flow', ['UNKNOWN', 'MIXED', 'NEUTRAL', 'BULLISH', 'STRONG_SUPPORT'])
def test_institutions_are_optional_positive_evidence(factory, flow):
    c = factory(institutional_level=flow, price_above_ma5=False, volume_ratio=1.)
    d, _ = run(c)
    assert not d.risk_gate
    assert d.entry_action == A.ENTRY_CONDITION_MET
    neutral, _ = run(replace(c, institutional_level='NEUTRAL'))
    assert d.entry_score == neutral.entry_score + (2 if flow in ('BULLISH', 'STRONG_SUPPORT') else 0)


@pytest.mark.parametrize('delta,volume,required,accepted', [
    (.5, 1., 1, True), (.5, None, 1, True), (.25, 1.5, 2, True),
    (.3, 2., 2, True), (.24, 3., 2, False), (.49, 1.49, 2, False),
])
def test_complementary_breakout_and_confirmation(delta, volume, required, accepted):
    c = context(current_price=120 + 2 * delta, volume_ratio=volume,
        active_resistance_zone=dict(low=118, high=120, zone_id='r',
            interaction=dict(state='RESISTANCE_BREAKOUT_PENDING')))
    d, state = run(c)
    p = d.entry_paths['breakout']
    assert p['core_met'] == accepted
    assert p['confirmation_required'] == required
    assert p['ready'] == (accepted and required == 1)
    again, duplicate = run(c, state)
    assert duplicate == state
    assert again.entry_paths == d.entry_paths
    next_day, _ = run(replace(c, observation_time='2026-09-15'), state)
    assert next_day.entry_paths['breakout']['ready'] == accepted


def test_thresholds_actually_control_probe_normal_and_no_entry():
    c = pullback(price_above_ma5=False, institutional_level='UNKNOWN', volume_ratio=1.,
                 short_term_direction=-1, short_term_score=-4, macd_momentum='data_insufficient')
    first, state = run(c)
    assert first.entry_score == 3
    assert first.entry_action == A.WATCH_FOR_CONFIRMATION
    second, _ = run(replace(c, observation_time='2026-09-15'), state)
    assert second.entry_action == A.ALLOW_PROBE_ENTRY
    blocked, _ = run(c, config=DecisionConfig(probe_score_min=4))
    assert not blocked.entry_paths['pullback']['eligible']
    normal, _ = run(c, config=DecisionConfig(probe_score_min=2, entry_score_min=3))
    assert normal.entry_action == A.ENTRY_CONDITION_MET
    high_threshold, _ = run(pullback(), config=DecisionConfig(entry_score_min=20))
    assert high_threshold.entry_paths['pullback']['target_action'] == A.ALLOW_PROBE_ENTRY


@pytest.mark.parametrize('factory', [context, pullback])
def test_high_volatility_caps_both_paths(factory):
    c = factory(volatility_level='HIGH')
    d, _ = run(c)
    assert d.entry_action == A.ALLOW_PROBE_ENTRY
    assert 'HIGH_VOLATILITY' not in d.risk_gate
    text = format_operation_reference(d)['for_non_holder']
    assert '高波動限制為小幅試單' in text


@pytest.mark.parametrize('changes', [
    dict(data_valid=False), dict(atr=None), dict(atr=float('nan')), dict(atr=0),
    dict(current_price=None), dict(current_price=float('inf')), dict(volatility_level='UNKNOWN'),
    dict(medium_term_direction=-1), dict(support_status='CONFIRMED_BREAK', current_price=99),
    dict(institutional_level='STRONG_PRESSURE'), dict(support_probability='VERY_LOW'),
])
def test_hard_gates_override_scores(changes):
    d, _ = run(replace(context(), **changes))
    assert d.risk_gate
    assert d.entry_action not in (A.ALLOW_PROBE_ENTRY, A.ENTRY_CONDITION_MET)
    assert not d.entry_paths['entry_ready']


def test_support_proximity_is_real_and_strength_macd_ma5_are_optional():
    c = pullback(support_strength=None, macd_momentum='bearish_strengthening', price_above_ma5=False)
    d, _ = run(c)
    assert not d.risk_gate
    assert d.entry_action == A.ENTRY_CONDITION_MET
    far, _ = run(replace(c, current_price=110, distance_to_support=0))
    assert not far.entry_paths['pullback']['eligible']
    broken, _ = run(replace(c, current_price=99, support_status='HOLDING'))
    assert not broken.entry_paths['pullback']['eligible']


def test_same_calendar_day_does_not_add_confirmation():
    c = replace(pullback(), support_status='TESTING', observation_time='2026-09-14T13:30:00+08:00')
    first, state = run(c)
    assert first.entry_paths['pullback']['confirmation_count'] == 1
    for stamp in ('2026-09-14T14:00:00+08:00', '2026-09-14T07:00:00+00:00'):
        repeat, _ = run(replace(c, observation_time=stamp), state)
        assert repeat.entry_paths['pullback']['confirmation_count'] == 1
        assert not repeat.entry_paths['entry_ready']


@pytest.mark.parametrize('changes', [dict(observation_complete=False), dict(observation_time=None),
                                      dict(observation_time='invalid')])
def test_strong_signal_still_requires_a_valid_completed_bar(changes):
    d, _ = run(replace(context(), **changes))
    assert not d.entry_paths['entry_ready']
    assert d.confirmation_count == 0


def test_interrupted_weak_setup_needs_two_new_bars():
    c = replace(pullback(), support_status='TESTING')
    _, state = run(c)
    _, state = run(replace(c, observation_time='2026-09-15', current_price=110), state)
    first, state = run(replace(c, observation_time='2026-09-16'), state)
    assert first.entry_paths['pullback']['confirmation_count'] == 1
    assert not first.entry_paths['entry_ready']
    second, _ = run(replace(c, observation_time='2026-09-17'), state)
    assert second.entry_paths['entry_ready']


@pytest.mark.parametrize('delta,ratio,required', [(.5, 1., 1), (.25, 1.5, 2)])
def test_real_lifecycle_pending_crossing_does_not_wait_for_role_flip(delta, ratio, required):
    from app.support_resistance_analysis.lifecycle import advance_lifecycle, lifecycle_view
    zone = dict(low=118, high=120, type='resistance', strength_score=7, methods=['swing_high'])
    records = advance_lifecycle(dict(resistance_zones=[zone]), [], symbol='ENTRY',
        observation_time='2026-09-11', close=119, high=119, low=118, atr=2)
    state = {}
    for day in ('2026-09-14', '2026-09-15')[:required]:
        price = 120 + delta * 2
        records = advance_lifecycle(dict(resistance_zones=[zone]), records, symbol='ENTRY',
            observation_time=day, close=price, high=price, low=price, atr=2, volume_ratio=ratio)
        assert records[0]['status'] == 'ACTIVE_RESISTANCE'
        sr = dict(current_price=price, atr=2, as_of=day, zone_lifecycle=lifecycle_view(records, price, day))
        c = build_decision_context(dict(stock_code='ENTRY', history_date=day, close=price, atr=2,
            volatility_level='中等波動', ma5=price, support_resistance=sr,
            timeframe_analysis=dict(short_term=dict(score=6), medium_term=dict(score=6)),
            macd_analysis=dict(momentum='bullish_strengthening')))
        c = replace(c, volume_ratio=ratio)
        assert c.breakout_reference_zone['interaction']['state'] == 'RESISTANCE_BREAKOUT_PENDING'
        d, state = run(c, state)
    assert d.entry_action == A.ENTRY_CONDITION_MET
    assert d.entry_paths['breakout']['confirmation_count'] == required


def test_policy_change_recomputes_same_bar_in_sqlite(tmp_path):
    path = tmp_path / 'entry.db'
    c = context()
    def monitor(config):
        payload = dict(decision_context=asdict(c), timeframe_analysis={})
        with sqlite3.connect(path) as connection:
            update_monitor_decision(payload, connection, config=config)
        return payload['trading_decision']
    assert monitor(DecisionConfig())['entry_action'] == A.ENTRY_CONDITION_MET
    d = monitor(DecisionConfig(probe_score_min=50, entry_score_min=100))
    assert not d['entry_paths']['entry_ready']
    assert d['entry_action'] not in (A.ALLOW_PROBE_ENTRY, A.ENTRY_CONDITION_MET)


def test_stale_bar_preserves_memory_without_reissuing_entry():
    c = context()
    ready, state = run(c)
    assert ready.entry_action == A.ENTRY_CONDITION_MET
    stale, saved = run(replace(c, observation_time='2026-09-11'), state)
    assert saved == state
    assert not stale.entry_paths['entry_ready']
    assert stale.entry_action == A.WATCH_FOR_CONFIRMATION


def test_strong_confirmation_is_not_double_counted_in_output():
    c = context()
    _, state = run(c)
    d, _ = run(replace(c, observation_time='2026-09-15'), state)
    assert d.entry_paths['breakout']['confirmation_count'] == 1
    assert d.entry_paths['breakout']['confirmation_required'] == 1
    assert all(t['code'] != 'CONSECUTIVE_CONFIRMATION' for t in d.entry_upgrade_triggers)


def test_policy_upgrade_does_not_reuse_old_and_gate_counts():
    import json
    c = context()
    old = dict(last_observation_time=c.observation_time,
               entry_path_memory=json.dumps(dict(breakout=dict(identity='resistance', count=0,
                   last_observation_time=c.observation_time))))
    d, state = run(c, old)
    assert d.entry_action == A.ENTRY_CONDITION_MET
    assert json.loads(state['entry_path_memory'])['policy']['version'] == 3


@pytest.mark.parametrize('changes', [dict(probe_score_min=6), dict(volume_breakout_atr=.5)])
def test_entry_threshold_order_is_validated(changes):
    with pytest.raises(ValueError):
        DecisionConfig(**changes)
