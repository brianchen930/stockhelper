from copy import deepcopy
from datetime import datetime
from zoneinfo import ZoneInfo
import sqlite3
import pandas as pd
import pytest
from app.support_resistance_analysis.lifecycle import (advance_lifecycle, lifecycle_view,
    match_zones, compatible, detected_zones)
from app.support_resistance_analysis.lifecycle_storage import LifecycleStore, create_tables
from app.support_resistance_analysis.lifecycle_integration import attach_zone_lifecycle
from app.support_resistance_analysis.formatting import format_support_resistance_output
from app.decision_context import build_decision_context


def zone(low=469.05, high=470.95, role='support', **kw):
    return dict(low=low, high=high, type=role, distance_pct=0., strength_score=5,
                strength_label='medium', methods=['swing_low'], **kw)


def sr(*zones):
    return {r + '_zones': [z for z in zones if z['type'] == r] for r in ('support', 'resistance', 'active')}


def step(previous, day, price, zones, high=None, completed=True, atr=4):
    return advance_lifecycle(sr(*zones), previous, symbol='TEST', observation_time=f'2026-09-{day:02d}T13:30:00+08:00',
        close=price, high=price if high is None else high, low=price-1, atr=atr, completed=completed)


def test_break_then_later_retest_flip_and_new_support():
    first = step([], 10, 473, [zone()])
    assert first[0]['status'] == 'ACTIVE_SUPPORT'
    broken = step(first, 11, 465.5, [zone(469.07,470.93,'resistance'), zone(451,453)], high=471)
    old = next(r for r in broken if r['stable_zone_id'] == first[0]['stable_zone_id'])
    assert old['status'] == 'BROKEN_SUPPORT' and old['flip_confirmed_at'] is None
    view = lifecycle_view(broken, 465.5, '2026-09-11T13:30:00+08:00')
    assert [z['low'] for z in view['support_zones']] == [451]
    assert not view['resistance_zones']  # Same-bar high does not confirm a later retest.
    flipped = step(broken, 14, 467, [zone(469.06,470.94,'resistance'),zone(451,453)], high=470)
    view = lifecycle_view(flipped,467,'2026-09-14T13:30:00+08:00')
    assert view['resistance_zones'][0]['status'] == 'SUPPORT_TO_RESISTANCE'
    assert view['resistance_zones'][0]['stable_zone_id'] == first[0]['stable_zone_id']
    result = dict(sr(zone(469.06,470.94,'resistance'),zone(451,453)), zone_lifecycle=dict(view, records=flipped))
    output = format_support_resistance_output(result)
    assert '原支撐轉壓力' in output and 'Swing Low' in output
    assert '451.00～453.00' in output


def test_minor_break_reclaim_not_flip():
    initial = step([],10,473,[zone()])
    minor = step(initial,11,468.8,[zone(role='resistance')])
    assert minor[0]['status'] == 'MINOR_BREAK'
    reclaimed = step(minor,14,472,[zone()])
    assert reclaimed[0]['status'] == 'RECLAIMED_SUPPORT'
    assert reclaimed[0]['break_confirmed_at'] is None and reclaimed[0]['flip_confirmed_at'] is None


def test_boundary_drift_same_id_adjacent_high_atr_not_merged():
    first = step([],10,475,[zone()])
    next_day = step(first,11,475,[zone(469.07,470.93)])
    assert first[0]['stable_zone_id'] == next_day[0]['stable_zone_id']
    assert not compatible(zone(), zone(471,473), atr=1000)
    assert not compatible(zone(origin_id='swing-A'), zone(origin_id='swing-B'), atr=1000)
    distinct = step(first,11,475,[zone(469.07,470.93),zone(471,473)],atr=1000)
    assert len({r['stable_zone_id'] for r in distinct}) == 2


def test_deterministic_one_to_one_no_chain_merge():
    previous = step([],10,475,[zone()])
    current = [zone(469.07,470.93), zone(469.09,470.91)]
    a, b = match_zones(current,previous,4), match_zones(list(reversed(current)),previous,4)
    assert len(a) == len(b) == 1
    assert current[next(iter(a))]['low'] == list(reversed(current))[next(iter(b))]['low']
    grouped = detected_zones(sr(zone(100,102), zone(100.2,102.2,'resistance'), zone(100.4,102.4,'active')),1000)
    assert len(grouped) >= 2


def test_opposing_duplicate_roles_share_one_id_and_no_dual_view():
    first = step([],10,473,[zone()])
    broken = step(first,11,465.5,[zone(),zone(469.07,470.93,'resistance')])
    assert len(broken) == 1
    assert broken[0]['stable_zone_id'] == first[0]['stable_zone_id']
    view = lifecycle_view(broken,465.5,'2026-09-11T13:30:00+08:00')
    assert not view['support_zones'] and not view['resistance_zones']


def test_duplicate_out_of_order_and_incomplete_do_not_advance():
    first = step([],10,473,[zone()])
    same = step(first,10,460,[zone(role='resistance')])
    assert same == first
    assert step(first,9,460,[zone(role='resistance')]) == first
    incomplete = step(first,11,460,[zone(role='resistance')],completed=False)
    assert incomplete[0]['status'] == 'ACTIVE_SUPPORT'
    assert incomplete[0]['break_confirmed_at'] is None


def live_result(day, price, zones, model=False):
    from tests.test_support_probability_display import candidate
    data = pd.DataFrame(dict(Close=[473.] * 19 + [price], High=[474.] * 19 + [max(price,470.)],
                             Low=[472.] * 19 + [price-1], Volume=[1000.] * 20),
                        index=pd.date_range(end=f'2026-09-{day:02d}',periods=20,freq='B'))
    result = dict(stock_code='TEST', date=f'2026-09-{day:02d}', close=price,atr=4,atr_percent=1.,
        support_resistance=dict(sr(*zones),current_price=price),
        timeframe_analysis=dict(short_term=dict(score=-3,label='偏空'),medium_term=dict(score=4,label='偏多')))
    if model:
        item = candidate(.3)
        item.update(support_low=469.05,support_high=470.95,
                    prediction_time=f'2026-09-{day:02d}T14:00:00+08:00',model_reference='model-1')
        result['support_resistance'].update(bayesian_support=[item],bayesian_support_selected=item)
    return result,data


def test_storage_migration_restart_events_and_presentation(tmp_path):
    path = tmp_path / 'test.db'
    factory = lambda: sqlite3.connect(path)
    with factory() as con:
        con.execute('CREATE TABLE watchlist (symbol TEXT)')
        con.execute("INSERT INTO watchlist VALUES ('KEEP')")
        create_tables(con)
        create_tables(con)
    store = LifecycleStore(factory)
    for day, price, zones, model in [(10,473,[zone()],True), (11,465.5,[zone(469.07,470.93,'resistance'),zone(451,453)],True),
                                     (14,467,[zone(469.07,470.93,'resistance'),zone(451,453)],True)]:
        result,data = live_result(day,price,zones,model)
        now = datetime(2026,9,day,14,tzinfo=ZoneInfo('Asia/Taipei'))
        attach_zone_lifecycle(result,data,store=LifecycleStore(factory),now=now)
        view = result['support_resistance']['zone_lifecycle']
        if day == 11:
            assert not view['resistance_zones']
        if day >= 11:
            output = format_support_resistance_output(result['support_resistance'])
            assert '模型評估支撐' not in output
            assert result['support_resistance']['bayesian_support_selected']['zone_lifecycle']['active_support'] is False
    assert view['resistance_zones'][0]['status'] == 'SUPPORT_TO_RESISTANCE'
    c = build_decision_context(result,data)
    assert c.active_support_zone['low'] == 451
    assert c.active_resistance_zone['low'] == 469.07
    debug = format_support_resistance_output(result['support_resistance'],debug=True)
    assert '[ZONE DEBUG]' in debug and 'BREAK' in debug and 'stable_zone_id' in debug
    with factory() as con:
        events = store.events(con,'TEST','1d')
        assert len(events) == 1
        assert events[0]['actual_outcome'] == 'BREAK'
        assert events[0]['predicted_level'] == '低'
        assert events[0]['prediction_time'] < events[0]['outcome_time']
        assert con.execute('SELECT symbol FROM watchlist').fetchone()[0] == 'KEEP'
    attach_zone_lifecycle(result,data,store=store,now=now)
    with factory() as con:
        assert len(store.events(con,'TEST','1d')) == 1


def test_break_day_prediction_never_backfilled_as_prior_forecast(tmp_path):
    store = LifecycleStore(lambda: sqlite3.connect(tmp_path / 'event.db'))
    result,data = live_result(11,465.5,[zone(role='resistance')],True)
    attach_zone_lifecycle(result,data,dict(sr(zone()),atr=4),store=store,
        now=datetime(2026,9,11,14,tzinfo=ZoneInfo('Asia/Taipei')))
    with store.connect() as con:
        assert store.events(con,'TEST','1d') == []


def test_frozen_anchor_prevents_drift_chain_and_exact_duplicates():
    first = step([],10,475,[zone(),zone()])
    assert len(first) == 1
    second = step(first,11,475,[zone(469.2,471.1)])
    third = step(second,14,475,[zone(469.4,471.3)])
    assert second[0]['stable_zone_id'] == first[0]['stable_zone_id']
    assert len(third) == 2  # A near B near C does not imply A is C.


def test_history_replay_and_intraday_do_not_write_live_lifecycle(tmp_path):
    store = LifecycleStore(lambda: sqlite3.connect(tmp_path / 'read.db'))
    result,data = live_result(14,473,[zone()],True)
    now = datetime(2026,9,14,14,tzinfo=ZoneInfo('Asia/Taipei'))
    attach_zone_lifecycle(result,data,store=store,now=now)
    with store.connect() as con:
        saved = store.load(con,'TEST','1d')
    past,past_data = live_result(11,465.5,[zone(role='resistance')],True)
    attach_zone_lifecycle(past,past_data,store=store,now=now)
    assert not past['support_resistance']['zone_lifecycle']['persisted']
    intraday,intraday_data = live_result(15,465.5,[zone(role='resistance')],True)
    attach_zone_lifecycle(intraday,intraday_data,store=store,
        now=datetime(2026,9,15,12,tzinfo=ZoneInfo('Asia/Taipei')))
    view = intraday['support_resistance']['zone_lifecycle']
    assert not view['persisted'] and not view['support_zones'] and not view['resistance_zones']
    with store.connect() as con:
        assert store.load(con,'TEST','1d') == saved


def test_reverse_role_requires_later_retest_and_broken_zone_is_retained():
    first = step([],10,465,[zone(role='resistance')])
    broken = step(first,11,475,[zone()],high=476)
    assert broken[0]['status'] == 'BROKEN_RESISTANCE'
    flipped = advance_lifecycle(sr(zone()),broken,symbol='TEST',observation_time='2026-09-14T13:30:00+08:00',
        close=474,high=475,low=470,atr=4)
    assert flipped[0]['status'] == 'RESISTANCE_TO_SUPPORT'
    missing = step(broken,14,475,[])
    assert len(missing) == 1 and missing[0]['stable_zone_id'] == first[0]['stable_zone_id']


def test_original_detection_and_bayesian_numbers_not_mutated(tmp_path):
    store = LifecycleStore(lambda: sqlite3.connect(tmp_path / 'original.db'))
    result,data = live_result(11,465.5,[zone(role='resistance'),zone(451,453)],True)
    raw = deepcopy(result['support_resistance'])
    attach_zone_lifecycle(result,data,dict(sr(zone()),atr=4),store=store,
        now=datetime(2026,9,11,14,tzinfo=ZoneInfo('Asia/Taipei')))
    for key in ('support_zones','resistance_zones','active_zones'):
        assert result['support_resistance'][key] == raw[key]
    assert result['support_resistance']['bayesian_support_selected']['result'] == raw['bayesian_support_selected']['result']
