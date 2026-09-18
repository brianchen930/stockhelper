from copy import deepcopy
from dataclasses import asdict, replace
import json
import pytest
from app.decision_context import build_decision_context
from app.decision_engine import DecisionEngine, ReasonCode as R, ActionState as A, HolderActionState as H
from app.decision_formatter import format_operation_reference
from app.decision_state import stabilize
from app.decision_zones import displayed_zones
from app.decision_transitions import TriggerCode as T
from app.analysis.price_context import enrich_price_context
from app.analysis.timeframe_summary import format_timeframe_discord
from app.support_resistance_analysis.formatting import format_support_resistance_output
from tests.test_decision_context import inputs
from tests.test_decision_engine import BASE, bullish
from tests.test_price_context import baseline, zone
from tests.test_support_probability_display import candidate


def regression():
    result, bars, _ = inputs()
    current = zone(469.05, 470.95, -0.6)
    previous = zone(479., 481., -1.)
    model = candidate(.3)
    model.update(support_low=469.01, support_high=470.99)
    result.update(close=473., ma5=475., ma20=465., ma60=450.)
    result['support_resistance'].update(current_price=473., as_of='2026-09-14',
        nearest_support=current, support_zones=[current], resistance_zones=[zone(479., 481., 1.5)],
        active_zones=[], bayesian_support_selected=model)
    result['support_resistance']['institutional_context']['reasons'] = []
    bars.loc[bars.index[-2], 'Close'] = 480.
    bars.loc[bars.index[-1], ['Close', 'High']] = [473., 474.]
    return result, bars, dict(nearest_support=previous, as_of='2026-09-11')


def test_473_current_support_not_broken_previous_support_separate():
    result, bars, prior = regression()
    c = build_decision_context(result, bars, previous_zones=prior)
    assert c.active_support_zone['low'] == 469.01
    assert c.active_support_zone['high'] == 470.99
    assert c.current_price > c.active_support_zone['high']
    assert c.current_active_support_status not in ('CONFIRMED_BREAK', 'FLIPPED_TO_RESISTANCE')
    assert c.support_status == c.current_active_support_status
    assert c.previous_support_status == 'CONFIRMED_BREAK'
    assert c.previous_support_zone['low'] == 479.
    d = DecisionEngine().evaluate(c)
    assert R.SUPPORT_BREAK not in d.entry_reasons
    assert R.PREVIOUS_SUPPORT_BREAK in d.entry_reasons
    text = format_operation_reference(d)
    assert '前支撐 479.00～481.00' in text['for_non_holder']
    assert '469.01～470.99 為目前防守區' in text['for_holder']
    assert '支撐已有效失守' not in json.dumps(text, ensure_ascii=False)


def test_main_narrative_decision_share_union_and_do_not_mutate():
    result, bars, prior = regression()
    snapshot = deepcopy(result)
    sr = result['support_resistance']
    main = format_support_resistance_output(sr)
    narrative = enrich_price_context(baseline(), sr)['overall_summary']
    c = build_decision_context(result, bars, previous_zones=prior)
    for text in (main, narrative, format_operation_reference(DecisionEngine().evaluate(c))['for_holder']):
        assert '469.01～470.99' in text
        assert '469.05～470.95' not in text
    assert result == snapshot


def test_snapshot_ids_are_repeatable_and_distinguish_old_new():
    result, bars, prior = regression()
    first = build_decision_context(result, bars, previous_zones=prior)
    again = build_decision_context(result, bars, previous_zones=prior)
    assert first.active_support_zone['zone_id'] == again.active_support_zone['zone_id']
    assert first.active_support_zone['zone_id'] != first.previous_support_zone['zone_id']
    assert first.previous_support_zone['as_of'] == '2026-09-11'


def test_no_history_no_claim_of_break_above_support():
    result, bars, _ = regression()
    c = build_decision_context(result, bars)
    d = DecisionEngine().evaluate(c)
    assert R.SUPPORT_BREAK not in d.entry_reasons
    assert R.PREVIOUS_SUPPORT_BREAK not in d.entry_reasons
    assert c.previous_support_status == 'UNKNOWN'


def test_engine_defends_against_inconsistent_external_current_status():
    c = replace(BASE, current_price=473., active_support_zone=dict(low=469., high=471.), support_status='CONFIRMED_BREAK')
    assert R.SUPPORT_BREAK not in DecisionEngine().evaluate(c).entry_reasons


def test_avoid_upgrade_and_tighten_worsen_are_structured():
    c = replace(BASE, support_probability='低', institutional_level='STRONG_PRESSURE', volatility_level='極高波動')
    d = DecisionEngine().evaluate(c)
    assert (d.entry_action, d.holder_action) == (A.AVOID, H.TIGHTEN_RISK)
    assert d.entry_upgrade_triggers
    assert d.holder_worsen_triggers
    assert T.RISK_GATES_CLEAR in [t['code'] for t in d.entry_upgrade_triggers]
    assert T.MEDIUM_TERM_TREND_BREAK in [t['code'] for t in d.holder_worsen_triggers]


def test_pending_confirmation_and_debug_survive_serialization():
    c = bullish()
    d, _ = stabilize(DecisionEngine().evaluate(c), c, {})
    assert d.entry_action == A.WATCH_FOR_CONFIRMATION
    confirmation = next(t for t in d.entry_upgrade_triggers if t['code'] == T.CONSECUTIVE_CONFIRMATION)
    assert confirmation['required_observations'] == 2 and confirmation['completed_observations'] == 1
    text = format_operation_reference(d)
    assert '連續 2 根新收盤日線' in text['for_non_holder']
    from app.decision_engine import TradingDecision
    restored = TradingDecision(**json.loads(json.dumps(asdict(d))))
    debug = format_operation_reference(restored, debug=True)['debug']
    for field in ('Entry Score', 'Hold Risk Score', 'Risk Gate', 'Entry Reasons', 'Holder Reasons',
                  'entry_upgrade_triggers', 'entry_downgrade_triggers', 'holder_improve_triggers', 'holder_worsen_triggers'):
        assert field in debug


def test_normal_recommendation_does_not_repeat_indicators_and_roles_differ():
    result, bars, prior = regression()
    c = replace(build_decision_context(result, bars, previous_zones=prior), institutional_level='STRONG_PRESSURE',
                volatility_level='極高波動', macd_momentum='bearish_strengthening')
    d = DecisionEngine().evaluate(c)
    text = format_operation_reference(d)
    rendered = text['for_non_holder'] + text['for_holder']
    for term in ('ATR', 'MACD', 'RSI', 'KD', '重新檢視風險承受度', '重要價格區'):
        assert term not in rendered
    assert '法人' in text['for_holder']  # Evidence actually used by the REDUCE branch.
    assert '進場確認' in text['for_non_holder']
    assert '防守區' in text['for_holder'] and '無法站回' in text['for_holder']
    assert len(text['for_non_holder'].split('。')) <= 3
    assert len(text['for_holder'].split('。')) <= 6


def test_newest_display_list_wins_over_stale_nearest_pointer():
    result, bars, prior = regression()
    result['support_resistance']['nearest_support'] = prior['nearest_support']
    c = build_decision_context(result, bars, previous_zones=prior)
    assert c.active_support_zone['low'] == 469.01


def test_support_turns_into_active_testing_zone_uses_displayed_boundary():
    result, bars, prior = regression()
    sr = result['support_resistance']
    sr['active_zones'] = [zone(472., 474., 0)]
    prior['nearest_support'] = zone(472., 474., 0)
    c = build_decision_context(result, bars, previous_zones=prior)
    assert c.active_support_zone['role'] == 'active'
    assert c.support_status == 'TESTING'
    assert '472.00～474.00' in format_operation_reference(DecisionEngine().evaluate(c))['for_holder']
