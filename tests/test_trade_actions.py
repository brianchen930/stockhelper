from dataclasses import asdict, replace
import json
import sqlite3

import pytest

from app.decision_engine import DecisionContext, DecisionEngine
from app.decision_state import stabilize, update_monitor_decision, create_table
from app.decision_formatter import format_trade_recommendation


BASE = DecisionContext(symbol='TEST', observation_time='2026-09-15', observation_complete=True,
    current_price=100, short_term_direction=1, short_term_score=3, medium_term_direction=1,
    medium_term_score=4, support_probability='高', support_strength=7, support_status='HOLDING',
    active_support_zone={'low': 98, 'high': 100, 'stable_zone_id': 'S'},
    active_resistance_zone={'low': 110, 'high': 112, 'stable_zone_id': 'R'},
    distance_to_support=0, distance_to_resistance=5, atr=2, atr_percent=2,
    volatility_level='NORMAL', macd_momentum='bearish_weakening', kd_state='BULLISH',
    price_above_ma5=True, price_above_ma20=True, price_above_ma60=True,
    institutional_level='NEUTRAL', volume_ratio=1, volume_state='NORMAL')


def evaluate(**changes):
    return DecisionEngine().evaluate(replace(BASE, **changes))


@pytest.mark.parametrize('changes,expected', [
    ({}, 'ENTER'),
    (dict(rsi_state='OVERSOLD', medium_term_direction=-1, support_status='CONFIRMED_BREAK',
          current_price=90, macd_momentum='bearish_strengthening'), 'WAIT'),
    (dict(position_status='HOLDING', distance_to_support=4), 'HOLD'),
    (dict(position_status='HOLDING', average_cost=500, current_price=90, medium_term_direction=-1,
          support_status='CONFIRMED_BREAK', unrealized_return=-82), 'REDUCE'),
    (dict(position_status='HOLDING'), 'ADD'),
    (dict(position_status='HOLDING', support_status='MINOR_BREAK', macd_momentum='bullish_weakening'), 'REDUCE'),
    (dict(position_status='HOLDING', current_price=90, support_status='CONFIRMED_BREAK',
          medium_term_direction=-1, macd_momentum='bearish_strengthening'), 'EXIT'),
    (dict(position_status='HOLDING', current_price=90, support_status='CONFIRMED_BREAK',
          medium_term_direction=-1, macd_momentum='bearish_strengthening', support_probability='極高'), 'EXIT'),
])
def test_requested_eight_cases(changes, expected):
    d = evaluate(**changes)
    assert d.decision == expected
    assert d.reasons and isinstance(d.warnings, list)


@pytest.mark.parametrize('changes', [
    dict(rsi_state='OVERSOLD', macd_momentum='neutral'),
    dict(support_probability='中'), dict(active_resistance_zone=None),
    dict(active_resistance_zone={'low': 102, 'high': 104}),
    dict(volume_ratio=None), dict(volume_deteriorating=True), dict(overextended=True),
    dict(atr=None), dict(observation_complete=False), dict(data_valid=False),
    dict(price_above_ma60=None), dict(medium_term_direction=-1),
    dict(institutional_level='STRONG_PRESSURE'), dict(support_status='MINOR_BREAK'),
])
def test_entry_vetoes(changes):
    assert evaluate(**changes).decision == 'WAIT'
    assert evaluate(position_status='HOLDING', **changes).decision != 'ADD'


def test_add_stricter_and_extreme_not_absolute_veto():
    assert evaluate(kd_state='BEARISH').decision == 'ENTER'
    assert evaluate(position_status='HOLDING', kd_state='BEARISH').decision == 'HOLD'
    assert evaluate(volatility_level='EXTREME').decision == 'WAIT'
    assert evaluate(volatility_level='EXTREME', support_probability='極高',
                    macd_momentum='bullish_strengthening').decision == 'ENTER'


@pytest.mark.parametrize('cost', [50, 100, 500, None])
def test_cost_cannot_force_averaging_or_prevent_exit(cost):
    assert evaluate(position_status='HOLDING', average_cost=cost).decision == 'ADD'
    assert evaluate(position_status='HOLDING', average_cost=cost, current_price=90,
                    support_status='CONFIRMED_BREAK', medium_term_direction=-1,
                    macd_momentum='bearish_strengthening').decision == 'EXIT'


@pytest.mark.parametrize('changes', [dict(rsi_state='OVERSOLD'), dict(macd_momentum='bearish_strengthening'),
                                    dict(support_status='CONFIRMED_BREAK', current_price=90)])
def test_single_indicator_does_not_exit(changes):
    assert evaluate(position_status='HOLDING', **changes).decision != 'EXIT'


def monitor(connection, context=BASE):
    data = {'decision_context': asdict(context), 'timeframe_analysis': {}}
    update_monitor_decision(data, connection)
    return data['trading_decision']


def test_confirmation_replay_restart_role_change_and_history(tmp_path):
    path = tmp_path / 'trade.db'
    with sqlite3.connect(path) as conn:
        d = monitor(conn)
        assert d['decision'] == 'WAIT' and d['trade_confirmation_count'] == 1
        assert monitor(conn)['trade_confirmation_count'] == 1
        assert conn.execute('SELECT count(*) FROM trading_decision_history').fetchone()[0] == 1
    with sqlite3.connect(path) as conn:
        day2 = replace(BASE, observation_time='2026-09-16')
        d = monitor(conn, day2)
        assert d['decision'] == 'ENTER' and d['trade_confirmation_count'] == 2
        assert monitor(conn, day2)['decision'] == 'ENTER'
        holding = replace(day2, position_status='HOLDING', average_cost=110, unrealized_return=-9.09)
        assert monitor(conn, holding)['decision'] == 'HOLD'
        assert monitor(conn, replace(holding, observation_time='2026-09-17'))['decision'] == 'HOLD'
        assert monitor(conn, replace(holding, observation_time='2026-09-18'))['decision'] == 'ADD'
        rows = conn.execute('SELECT date,symbol,position_status,decision,decision_reasons,context_json,decision_json FROM trading_decision_history ORDER BY id').fetchall()
        assert len(rows) == 5
        assert [row[3] for row in rows] == ['WAIT', 'ENTER', 'HOLD', 'HOLD', 'ADD']
        assert json.loads(rows[-1][5])['average_cost'] == 110
        assert json.loads(rows[-1][6])['config']['confirmation_required'] == 2
        before = conn.execute('SELECT * FROM decision_state').fetchone()
        assert monitor(conn, BASE)['decision'] == 'WAIT'
        assert conn.execute('SELECT * FROM decision_state').fetchone() == before
        assert conn.execute('SELECT count(*) FROM trading_decision_history').fetchone()[0] == 5


def test_risk_improvement_confirmed_but_deterioration_immediate():
    with sqlite3.connect(':memory:') as conn:
        bad = replace(BASE, position_status='HOLDING', current_price=90, support_status='CONFIRMED_BREAK',
                      medium_term_direction=-1, macd_momentum='bearish_strengthening')
        assert monitor(conn, bad)['decision'] == 'EXIT'
        healthy = replace(BASE, position_status='HOLDING', observation_time='2026-09-16', distance_to_support=4)
        assert monitor(conn, healthy)['decision'] == 'REDUCE'
        assert monitor(conn, healthy)['decision'] == 'REDUCE'
        assert monitor(conn, replace(healthy, observation_time='2026-09-17'))['decision'] == 'HOLD'


def test_minor_risk_recovery_uses_trade_confirmation():
    with sqlite3.connect(':memory:') as conn:
        bad = replace(BASE, position_status='HOLDING', support_status='MINOR_BREAK', macd_momentum='bullish_weakening')
        assert monitor(conn, bad)['decision'] == 'REDUCE'
        healthy = replace(BASE, position_status='HOLDING', observation_time='2026-09-16', distance_to_support=4)
        assert monitor(conn, healthy)['decision'] == 'REDUCE'
        assert monitor(conn, replace(healthy, observation_time='2026-09-17'))['decision'] == 'HOLD'


def test_no_persist_incomplete_and_new_zone_restarts_confirmation():
    with sqlite3.connect(':memory:') as conn:
        monitor(conn, replace(BASE, observation_complete=False))
        assert conn.execute('SELECT count(*) FROM trading_decision_history').fetchone()[0] == 0
        monitor(conn, BASE)
        changed = replace(BASE, observation_time='2026-09-16', active_support_zone={'low': 98, 'high': 100, 'stable_zone_id': 'NEW'})
        d = monitor(conn, changed)
        assert d['decision'] == 'WAIT' and d['trade_confirmation_count'] == 1


def test_atomic_history_state_rollback():
    with sqlite3.connect(':memory:') as conn:
        create_table(conn)
        conn.execute("CREATE TRIGGER reject_history BEFORE INSERT ON trading_decision_history BEGIN SELECT RAISE(ABORT, 'test'); END")
        conn.commit()
        with pytest.raises(sqlite3.IntegrityError):
            monitor(conn)
        assert conn.execute('SELECT count(*) FROM decision_state').fetchone()[0] == 0


def test_read_only_and_formatter():
    d, state = stabilize(evaluate(), BASE, {})
    assert d.decision == 'WAIT'
    lines = format_trade_recommendation(asdict(d))
    assert lines[0] == '【交易建議】' and lines[1] == '目前動作：等待'
    assert '後續觀察條件：' in lines
    assert len(lines) <= 13
