from copy import deepcopy
from dataclasses import asdict, replace
import json

import pytest

from app.decision_context import build_decision_context
from app.decision_engine import DecisionEngine
from app.decision_formatter import format_operation_reference
from app.decision_zones import displayed_zones
from app.support_resistance_analysis.formatting import format_support_resistance_output
from app.support_resistance_analysis.interaction import classify_interaction
from app.support_resistance_analysis.lifecycle import advance_lifecycle, lifecycle_view
from app.support_resistance_analysis.selection import select_zone_display
from app.rules.support_resistance_rule import format_support_resistance_events


STAMP = '2026-09-18T13:30:00+08:00'


def zone(low, high, status, role, *, price=525, broken_at='2026-09-17T13:30:00+08:00'):
    broken = status in ('BROKEN_SUPPORT', 'BROKEN_RESISTANCE')
    transition = broken or status in ('ROLE_TRANSITION', 'MINOR_BREAK')
    return dict(low=low, high=high, zone_low=low, zone_high=high, status=status,
                current_role='NONE' if transition else role.upper(),
                previous_role=role.upper() if broken else None,
                stable_zone_id=f'{low}-{high}', zone_id=f'{low}-{high}',
                as_of=STAMP, last_observation_at=STAMP, break_confirmed_at=broken_at if broken else None,
                strength_score=8, strength_label='strong', methods=['swing_high'],
                distance_pct=((low + high) / 2 / price - 1) * 100,
                interaction=classify_interaction(price, low, high, role, confirmed=broken))


def nanya(*, nearby=False):
    support = zone(503.95, 506.53, 'ACTIVE_SUPPORT', 'support')
    resistance = zone(531.95, 541.05, 'ACTIVE_RESISTANCE', 'resistance')
    historical = [zone(lo, hi, 'BROKEN_RESISTANCE', 'resistance', broken_at='2026-09-01')
                  for lo, hi in ((479.02, 480.98), (483.73, 485.59), (469.02, 470.98), (488.73, 494.90))]
    if nearby:
        historical.append(zone(513.42, 519.60, 'BROKEN_RESISTANCE', 'resistance'))
    view = dict(support_zones=[support], resistance_zones=[resistance], active_zones=[],
                historical_zones=historical, observation_time=STAMP)
    view['records'] = deepcopy([support, resistance, *historical])
    return dict(current_price=525, atr=10, as_of=STAMP, zone_lifecycle=view)


def context(sr):
    return build_decision_context(dict(stock_code='2408', history_date='2026-09-18',
        close=525, atr=10, volatility_level='中等波動', ma5=490, ma20=460, ma60=400,
        macd_analysis=dict(momentum='bullish_strengthening'), support_resistance=dict(
            sr, institutional_context=dict(institutional_level='BULLISH')),
        timeframe_analysis=dict(short_term=dict(score=5), medium_term=dict(score=7))))


def test_2408_main_output_only_active_roles_and_history_unchanged():
    sr = nanya()
    saved = deepcopy(sr)
    selection = select_zone_display(sr)
    assert selection['support']['is_actionable']
    assert selection['resistance']['is_actionable']
    assert selection['secondary'] is None
    text = format_support_resistance_output(sr)
    assert text.count('・') == 2
    assert '最近支撐：503.95～506.53' in text
    assert '最近壓力：531.95～541.05' in text
    assert '-3.52%' in text and '+1.32%' in text
    for hidden in ('479.02', '483.73', '469.02', '488.73'):
        assert hidden not in text
        assert hidden in format_support_resistance_output(sr, debug=True)
    assert sr == saved


def test_nearby_candidate_is_secondary_only_and_cannot_replace_support():
    sr = nanya(nearby=True)
    sr['zone_lifecycle']['historical_zones'].append(zone(510, 515, 'BROKEN_RESISTANCE', 'resistance'))
    selected = select_zone_display(sr)
    assert selected['secondary']['zone']['low'] == 513.42
    assert not selected['secondary']['is_actionable']
    assert selected['secondary']['display_priority'] == 2
    text = format_support_resistance_output(sr)
    assert text.count('次要觀察區') == 1
    assert '次要觀察區：513.42～519.60' in text
    assert '最近支撐：503.95～506.53' in text
    assert '尚待' not in text or '轉為支撐' in text
    assert context(sr).active_support_zone['low'] == 503.95


@pytest.mark.parametrize('atr,visible', [(10, True), (5, False), (None, False), (0, False)])
def test_secondary_uses_existing_one_atr_proximity(atr, visible):
    sr = nanya(nearby=True)
    sr['atr'] = atr
    assert bool(select_zone_display(sr)['secondary']) == visible


@pytest.mark.parametrize('stamp', ['2026-09-01', '2026-09-20', None])
def test_old_future_or_undated_confirmation_is_not_refreshed_by_recent_observation(stamp):
    sr = nanya(nearby=True)
    sr['zone_lifecycle']['historical_zones'][-1]['break_confirmed_at'] = stamp
    assert select_zone_display(sr)['secondary'] is None


def test_pending_crossing_does_not_claim_confirmed_role():
    sr = nanya()
    sr['zone_lifecycle']['historical_zones'].append(zone(513.42, 519.60, 'ROLE_TRANSITION', 'resistance'))
    text = format_support_resistance_output(sr)
    assert '次要觀察區' in text and '尚待有效突破確認' in text
    assert '最近支撐：513.42' not in text


def test_broken_support_is_hidden_or_secondary_never_active_resistance():
    sr = nanya()
    sr['zone_lifecycle']['historical_zones'] = [zone(530, 532, 'BROKEN_SUPPORT', 'support')]
    selected = select_zone_display(sr)
    assert selected['secondary']['zone']['low'] == 530
    assert selected['resistance']['zone']['low'] == 531.95
    assert '原支撐已跌破' in format_support_resistance_output(sr)


def test_invalid_and_transitional_states_cannot_sneak_into_primary_lists():
    sr = nanya()
    for state in ('BROKEN_RESISTANCE', 'BROKEN_SUPPORT', 'ROLE_TRANSITION', 'INVALIDATED', 'MINOR_BREAK'):
        sr['zone_lifecycle']['support_zones'].insert(0, zone(520, 524, state, 'support'))
        sr['zone_lifecycle']['resistance_zones'].insert(0, zone(526, 528, state, 'resistance'))
    selected = displayed_zones(sr)
    assert selected['support']['low'] == 503.95
    assert selected['resistance']['low'] == 531.95


def test_entry_uses_current_resistance_not_historical_breakout_even_when_nearby():
    sr = nanya(nearby=True)
    c = context(sr)
    assert c.previous_resistance_status == 'CONFIRMED_BREAKOUT'  # Tracking retained.
    d = DecisionEngine().evaluate(c)
    b = d.entry_paths['breakout']
    assert b['zone']['low'] == 531.95
    assert b['status'] == 'WATCHING'
    assert not b['ready']
    assert 'RESISTANCE_BREAKOUT' not in d.entry_reasons
    assert d.entry_paths['pullback']['zone']['low'] == 503.95
    text = format_operation_reference(d)['for_non_holder']
    assert '目前上方 531.95～541.05 為最近有效壓力' in text
    for stale in ('483.73', '513.42', '479.02'):
        assert stale not in text
    assert format_operation_reference(d)['entry_label'] == '不宜追價'


def test_holder_uses_current_support_and_not_old_break_as_reclaim_target():
    sr = nanya()
    sr['zone_lifecycle']['historical_zones'].append(zone(520, 524, 'BROKEN_SUPPORT', 'support'))
    c = context(sr)
    d = DecisionEngine().evaluate(c)
    assert d.price_context['active_support_zone']['low'] == 503.95
    targets = [t['zone'] for t in d.holder_improve_triggers if t.get('zone')]
    assert {z['low'] for z in targets} == {503.95, 531.95}
    assert '503.95～506.53 為目前防守區' in format_operation_reference(d)['for_holder']


def test_recent_confirmed_breakout_without_active_resistance_remains_eligible():
    sr = nanya(nearby=True)
    sr['zone_lifecycle']['resistance_zones'] = []
    c = replace(context(sr), overextended=False)
    b = DecisionEngine().evaluate(c).entry_paths['breakout']
    assert b['zone']['low'] == 513.42 and b['status'] == 'READY'
    old = deepcopy(sr)
    old['zone_lifecycle']['historical_zones'][-1]['break_confirmed_at'] = '2026-09-01'
    b = DecisionEngine().evaluate(replace(context(old), overextended=False)).entry_paths['breakout']
    assert not b['ready'] and b['zone'] is None


def test_actual_role_confirmation_promotes_new_support_without_deleting_history():
    z = dict(low=513, high=519, type='resistance', strength_score=8, methods=['swing_high'])
    def advance(records, day, price, low):
        return advance_lifecycle(dict(resistance_zones=[z]), records, symbol='2408',
            observation_time=f'2026-09-{day}T13:30:00+08:00', close=price, high=price+1, low=low, atr=5)
    original = advance([], 15, 510, 509)
    broken = advance(original, 16, 525, 524)
    assert broken[0]['status'] == 'BROKEN_RESISTANCE'
    pending = dict(current_price=525, atr=5, as_of=STAMP,
                   zone_lifecycle=lifecycle_view(broken, 525, broken[0]['last_observation_at']))
    assert displayed_zones(pending)['support'] is None
    flipped = advance(broken, 17, 523, 518)
    assert flipped[0]['status'] == 'RESISTANCE_TO_SUPPORT'
    view = lifecycle_view(flipped, 523, flipped[0]['last_observation_at'])
    view['support_zones'].append(zone(503.95, 506.53, 'ACTIVE_SUPPORT', 'support', price=523))
    sr = dict(current_price=523, atr=5, as_of=STAMP, zone_lifecycle=view)
    assert displayed_zones(sr)['support']['low'] == 513
    assert '最近支撐：513.00～519.00' in format_support_resistance_output(sr)
    assert flipped[0]['stable_zone_id'] == original[0]['stable_zone_id']
    assert flipped[0]['break_confirmed_at'] == broken[0]['break_confirmed_at']


def test_events_and_serialized_history_survive_display_filter():
    sr = nanya()
    before = json.dumps(sr, sort_keys=True)
    old = sr['zone_lifecycle']['historical_zones'][0]
    events = [dict(category='RESISTANCE_BREAKOUT', message='突破壓力', zone=old, distance_pct=2)]
    format_support_resistance_output(sr)
    assert '479.02～480.98' in '\n'.join(format_support_resistance_events(events, sr))
    assert json.dumps(sr, sort_keys=True) == before
    assert json.loads(json.dumps(asdict(context(sr))))['previous_resistance_zone']
