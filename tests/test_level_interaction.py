from copy import deepcopy
from dataclasses import asdict, replace
from datetime import datetime
import json
import sqlite3
from zoneinfo import ZoneInfo

import pytest

from app.support_resistance_analysis.interaction import classify_interaction, LevelInteractionState as S
from app.support_resistance_analysis.interaction_formatting import interaction_text, interaction_guidance
from app.support_resistance_analysis.lifecycle import advance_lifecycle, lifecycle_view
from app.support_resistance_analysis.lifecycle_storage import create_tables, LifecycleStore
from app.support_resistance_analysis.lifecycle_integration import attach_zone_lifecycle
from app.support_resistance_analysis.formatting import format_support_resistance_output
from app.decision_context import build_decision_context
from app.decision_engine import DecisionEngine, TradingDecision
from app.decision_formatter import format_operation_reference
from tests.test_zone_lifecycle import live_result


@pytest.mark.parametrize('role,price,state', [
    ('resistance', 6300, S.BELOW_RESISTANCE),
    ('resistance', 6500, S.TESTING_RESISTANCE),
    ('resistance', 6477, S.TESTING_RESISTANCE),
    ('resistance', 6503, S.TESTING_RESISTANCE),
    ('resistance', 6504, S.RESISTANCE_BREAKOUT_PENDING),
    ('support', 6600, S.ABOVE_SUPPORT),
    ('support', 6500, S.TESTING_SUPPORT),
    ('support', 6477, S.TESTING_SUPPORT),
    ('support', 6503, S.TESTING_SUPPORT),
    ('support', 6476, S.SUPPORT_BREAKDOWN_PENDING),
])
def test_inclusive_boundaries(role, price, state):
    assert classify_interaction(price, 6477, 6503, role)['state'] == state


@pytest.mark.parametrize('role,price,state', [
    ('resistance', 6504, S.RESISTANCE_BREAKOUT_CONFIRMED),
    ('support', 6476, S.SUPPORT_BREAKDOWN_CONFIRMED),
])
def test_confirmation_is_an_external_result(role, price, state):
    assert classify_interaction(price, 6477, 6503, role, confirmed=True)['state'] == state
    assert classify_interaction(6500, 6477, 6503, role, confirmed=True)['state'].startswith('TESTING')


@pytest.mark.parametrize('role,pending_price,return_price,state,location', [
    ('resistance', 6504, 6500, S.RESISTANCE_BREAKOUT_FAILED, 'inside'),
    ('resistance', 6504, 6400, S.RESISTANCE_BREAKOUT_FAILED, 'below'),
    ('support', 6476, 6500, S.SUPPORT_BREAKDOWN_FAILED, 'inside'),
    ('support', 6476, 6600, S.SUPPORT_BREAKDOWN_FAILED, 'above'),
])
def test_failure_requires_previous_pending(role, pending_price, return_price, state, location):
    prior = classify_interaction(pending_price, 6477, 6503, role)
    current = classify_interaction(return_price, 6477, 6503, role, previous=prior)
    assert current['state'] == state and current['previous_state'] == prior['state']
    assert current['location'] == location
    assert '失敗' in interaction_text(current)
    assert '失敗' not in interaction_text(classify_interaction(return_price, 6477, 6503, role))
    confirmed = classify_interaction(pending_price, 6477, 6503, role, confirmed=True)
    assert classify_interaction(return_price, 6477, 6503, role, previous=confirmed)['state'] != state


@pytest.mark.parametrize('role', ['support', 'resistance'])
def test_zero_width_and_boundary_distance(role):
    i = classify_interaction(100, 100, 100, role)
    assert i['position_ratio'] == .5 and i['position_in_zone'] == 'middle'
    assert i['distance_pct'] is None
    json.dumps(i, allow_nan=False)
    assert classify_interaction(110, 90, 100, 'support')['distance_pct'] == pytest.approx((100 / 110 - 1) * 100)
    assert classify_interaction(90, 100, 110, 'resistance')['distance_pct'] == pytest.approx((100 / 90 - 1) * 100)
    assert classify_interaction(120, 100, 110, 'resistance')['distance_pct'] == pytest.approx((120 / 110 - 1) * 100)


@pytest.mark.parametrize('price,low,high,role', [(0, 1, 2, 'support'), (float('nan'), 1, 2, 'support'),
    (1, 2, 1, 'support'), (1, 1, 2, 'active'), (None, 1, 2, 'resistance')])
def test_invalid_or_unknown_provenance(price, low, high, role):
    assert classify_interaction(price, low, high, role) is None


def zone(role='resistance', low=6477., high=6503.):
    return dict(low=low, high=high, type=role, strength_label='weak', strength_score=3,
                methods=['kmeans', 'swing_high'], distance_pct=0.)


def advance(records, day, price, role='resistance', **kwargs):
    return advance_lifecycle({role + '_zones': [zone(role)]}, records, symbol='3443',
        observation_time=f'2026-09-{day:02d}T13:30:00+08:00', close=price,
        high=price + 1, low=price - 1, atr=100, **kwargs)


def test_boundary_oscillation_never_becomes_confirmed_and_same_bar_is_idempotent():
    records = []
    states = []
    for day, price in enumerate([6502, 6504, 6501, 6505], 10):
        records = advance(records, day, price)
        states.append(records[0]['interaction']['state'])
        assert records[0]['break_confirmed_at'] is None
        assert advance(records, day, price) == records
    assert states == [S.TESTING_RESISTANCE, S.RESISTANCE_BREAKOUT_PENDING,
                      S.RESISTANCE_BREAKOUT_FAILED, S.RESISTANCE_BREAKOUT_PENDING]


@pytest.mark.parametrize('role,start,pending,confirmed,state', [
    ('resistance', 6400, 6504, 6580, S.RESISTANCE_BREAKOUT_CONFIRMED),
    ('support', 6600, 6476, 6400, S.SUPPORT_BREAKDOWN_CONFIRMED),
])
def test_existing_atr_confirmation_and_no_automatic_flip(role, start, pending, confirmed, state):
    records = advance([], 10, start, role)
    records = advance(records, 11, pending, role)
    assert records[0]['interaction']['state'].endswith('PENDING')
    records = advance(records, 12, confirmed, role)
    assert records[0]['interaction']['state'] == state
    assert records[0]['current_role'] == 'NONE' and records[0]['flip_confirmed_at'] is None
    view = lifecycle_view(records, confirmed, records[0]['last_observation_at'])
    output = format_support_resistance_output(dict(zone_lifecycle=view))
    assert '原壓力' not in output and '原支撐' not in output
    assert str(state) in format_support_resistance_output(dict(zone_lifecycle=view), debug=True)


def test_persistent_volume_confirmation_is_reused():
    records = advance([], 10, 6400)
    records = advance(records, 11, 6540, volume_ratio=1.)
    assert records[0]['interaction']['state'] == S.RESISTANCE_BREAKOUT_PENDING
    records = advance(records, 12, 6540, volume_ratio=1.5)
    assert records[0]['interaction']['state'] == S.RESISTANCE_BREAKOUT_CONFIRMED


def test_previous_state_survives_database_restart_and_migration(tmp_path):
    path = tmp_path / 'state.db'
    records = advance([], 10, 6500)
    records = advance(records, 11, 6504)
    with sqlite3.connect(path) as con:
        create_tables(con)
        # Simulate the pre-feature schema, then migrate twice.
        con.execute('ALTER TABLE zone_lifecycle DROP COLUMN interaction')
        create_tables(con)
        create_tables(con)
        LifecycleStore.save(con, records)
    with sqlite3.connect(path) as con:
        restored = LifecycleStore.load(con, '3443', '1d')
    assert restored == records
    failed = advance(restored, 12, 6500)
    assert failed[0]['interaction']['state'] == S.RESISTANCE_BREAKOUT_FAILED
    assert failed[0]['interaction']['previous_state'] == S.RESISTANCE_BREAKOUT_PENDING
    assert advance(failed, 12, 6500) == failed
    assert advance(failed, 11, 6500) == failed


def test_intraday_and_replay_do_not_write_interactions(tmp_path):
    store = LifecycleStore(lambda: sqlite3.connect(tmp_path / 'live.db'))
    now = datetime(2026, 9, 14, 14, tzinfo=ZoneInfo('Asia/Taipei'))
    result, data = live_result(14, 6500, [zone()])
    attach_zone_lifecycle(result, data, store=store, now=now)
    with store.connect() as con:
        saved = store.load(con, 'TEST', '1d')
    live, live_data = live_result(15, 6504, [zone()])
    attach_zone_lifecycle(live, live_data, store=store,
        now=datetime(2026, 9, 15, 12, tzinfo=ZoneInfo('Asia/Taipei')))
    view = live['support_resistance']['zone_lifecycle']
    assert view['historical_zones'][0]['interaction']['state'] == S.RESISTANCE_BREAKOUT_PENDING
    assert view['historical_zones'][0]['status'] == 'ROLE_TRANSITION'
    past, past_data = live_result(11, 6500, [zone()])
    attach_zone_lifecycle(past, past_data, store=store, now=now)
    assert not past['support_resistance']['zone_lifecycle']['persisted']
    with store.connect() as con:
        assert store.load(con, 'TEST', '1d') == saved


def test_unknown_active_role_and_missing_atr_do_not_invent_confirmation():
    unknown = advance([], 10, 6500, role='active')
    assert unknown[0]['interaction'] is None
    records = advance([], 10, 6400)
    records = advance_lifecycle({}, records, symbol='3443', observation_time='2026-09-11', close=6700, atr=None)
    assert records[0]['interaction']['state'] == S.RESISTANCE_BREAKOUT_PENDING


def test_new_identity_and_confirmed_role_flip_do_not_inherit_failed_state():
    previous = classify_interaction(6504, 6477, 6503, 'resistance')
    flipped = classify_interaction(6500, 6477, 6503, 'support', previous=previous)
    assert flipped['state'] == S.TESTING_SUPPORT and flipped['previous_state'] is None
    records = advance([], 10, 6504)
    new = advance_lifecycle({'resistance_zones': [zone(low=6600, high=6620)]}, records,
        symbol='3443', observation_time='2026-09-11', close=6610, atr=100)
    fresh = next(r for r in new if r['zone_low'] == 6600)
    assert fresh['interaction']['state'] == S.TESTING_RESISTANCE
    assert fresh['interaction']['previous_state'] is None


def test_3443_report_decision_and_serialization_share_states():
    support = zone('support', 6077.11, 6127.56)
    support.update(strength_label='medium', methods=['anchored_vwap_swing_high', 'anchored_vwap_swing_low', 'kmeans'])
    sr = dict(current_price=6500, support_zones=[support], resistance_zones=[zone()], active_zones=[])
    snapshot = deepcopy(sr)
    text = format_support_resistance_output(sr)
    assert '目前位置：位於支撐區上方，尚未測試' in text
    assert '目前位置：正在測試壓力區，價格接近區間上緣' in text
    assert '6477.00～6503.00（' not in text and '-0.15%' not in text
    result = dict(stock_code='3443', close=6500, atr=100, history_date='2026-09-14',
        support_resistance=sr, timeframe_analysis=dict(short_term=dict(score=3), medium_term=dict(score=3)))
    c = build_decision_context(result)
    assert c.active_resistance_zone['interaction']['state'] == S.TESTING_RESISTANCE
    d = DecisionEngine().evaluate(c)
    restored = TradingDecision(**json.loads(json.dumps(asdict(d), allow_nan=False)))
    operation = format_operation_reference(restored)['for_non_holder']
    assert '目前正在測試 6477.00～6503.00 壓力區' in operation
    assert '重新站上壓力區' not in operation and '等待接近' not in operation
    baseline = DecisionEngine().evaluate(replace(c, level_interactions=[]))
    for key in ('entry_action', 'holder_action', 'entry_score', 'risk_score', 'risk_gate'):
        assert getattr(d, key) == getattr(baseline, key)
    assert sr == snapshot


def test_preclassified_failure_is_not_erased_by_display_adapter():
    z = zone()
    prior = classify_interaction(6504, z['low'], z['high'], 'resistance')
    z['interaction'] = classify_interaction(6500, z['low'], z['high'], 'resistance', previous=prior)
    sr = dict(current_price=6500, resistance_zones=[z])
    assert '壓力突破失敗' in format_support_resistance_output(sr)
    context = build_decision_context(dict(support_resistance=sr, atr=100))
    assert context.active_resistance_zone['interaction'] == z['interaction']


@pytest.mark.parametrize('role,prices,expected', [
    ('resistance', [6400, 6504], '尚未完成有效突破確認'),
    ('resistance', [6400, 6504, 6500], '壓力突破失敗'),
    ('resistance', [6400, 6580], '壓力區已完成突破確認'),
    ('support', [6600, 6476], '尚未完成有效失守確認'),
    ('support', [6600, 6476, 6500], '支撐跌破失敗'),
    ('support', [6600, 6400], '支撐區已確認失守'),
])
def test_lifecycle_states_reach_operation_reference(role, prices, expected):
    records = []
    for day, price in enumerate(prices, 10):
        records = advance(records, day, price, role)
    view = lifecycle_view(records, prices[-1], records[0]['last_observation_at'])
    sr = dict(current_price=prices[-1], zone_lifecycle=view)
    c = build_decision_context(dict(support_resistance=sr, atr=100,
        history_date=records[0]['last_observation_at'],
        timeframe_analysis=dict(short_term=dict(score=3), medium_term=dict(score=3))))
    assert c.level_interactions[0]['state'] == records[0]['interaction']['state']
    text = format_operation_reference(DecisionEngine().evaluate(c))
    assert expected in text['for_holder' if role == 'support' else 'for_non_holder']
    assert expected in interaction_guidance({'interaction': c.level_interactions[0]})
