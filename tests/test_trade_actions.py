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
    ({}, 'OBSERVE'),
    (dict(rsi_state='OVERSOLD', medium_term_direction=-1, support_status='CONFIRMED_BREAK',
          current_price=90, macd_momentum='bearish_strengthening'), 'OBSERVE'),
    (dict(position_status='HOLDING', distance_to_support=4), 'HOLD'),
    (dict(position_status='HOLDING', average_cost=500, current_price=90, medium_term_direction=-1,
          support_status='CONFIRMED_BREAK', unrealized_return=-82), 'REDUCE'),
    (dict(position_status='HOLDING'), 'HOLD'),
    (dict(position_status='HOLDING', support_status='MINOR_BREAK', macd_momentum='bullish_weakening'), 'HOLD'),
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
def test_five_actions_never_invent_entry_or_sell_from_one_change(changes):
    assert evaluate(**changes).decision == 'OBSERVE'
    assert evaluate(position_status='HOLDING', **changes).decision in ('HOLD', 'OBSERVE')


def test_watching_observes_and_kd_alone_does_not_sell():
    assert evaluate(kd_state='BEARISH').decision == 'OBSERVE'
    assert evaluate(position_status='HOLDING', kd_state='BEARISH').decision == 'HOLD'
    assert evaluate(volatility_level='EXTREME').decision == 'OBSERVE'
    assert evaluate(volatility_level='EXTREME', support_probability='極高',
                    macd_momentum='bullish_strengthening').decision == 'OBSERVE'


@pytest.mark.parametrize('cost', [50, 100, 500, None])
def test_cost_cannot_force_averaging_or_prevent_exit(cost):
    assert evaluate(position_status='HOLDING', average_cost=cost).decision == 'HOLD'
    assert evaluate(position_status='HOLDING', average_cost=cost, current_price=90,
                    support_status='CONFIRMED_BREAK', medium_term_direction=-1,
                    macd_momentum='bearish_strengthening').decision == 'EXIT'


@pytest.mark.parametrize('cost,state,action', [(80, 'LARGE_PROFIT', 'HOLD'),
    (100, 'NEAR_COST', 'HOLD'), (120, 'LOSS', 'HOLD')])
def test_same_technical_weakness_respects_position_pnl(cost, state, action):
    d = evaluate(position_status='HOLDING', average_cost=cost, macd_momentum='bullish_weakening')
    assert d.decision == action
    assert d.trade_evidence['cost_context']['state'] == state
    assert 'MACD 動能轉弱' in d.reasons
    assert '持倉狀態' not in '\n'.join(format_trade_recommendation(asdict(d)))


@pytest.mark.parametrize('cost', [80, 100, 120, None, 0])
def test_watching_ignores_all_cost_inputs(cost):
    assert asdict(evaluate(average_cost=cost, unrealized_return=-50)) == asdict(evaluate())


@pytest.mark.parametrize('cost', [None, 0, -1, float('nan'), float('inf')])
def test_missing_or_invalid_cost_preserves_technical_decision(cost):
    d = evaluate(position_status='HOLDING', average_cost=cost, unrealized_return=-50)
    assert d.decision == 'HOLD'
    assert d.trade_evidence['cost_context'] is None


def test_near_cost_does_not_override_market_support_or_invent_support():
    d = evaluate(position_status='HOLDING', average_cost=100, support_status='TESTING')
    assert d.decision == 'HOLD'
    assert d.trade_evidence['cost_context']['support_near_cost']
    d = evaluate(position_status='HOLDING', average_cost=100, active_support_zone=None)
    assert d.decision == 'OBSERVE'
    assert not d.trade_evidence['cost_context']['support_near_cost']


def test_profit_protection_never_overrides_invalid_data_or_exit():
    d = evaluate(position_status='HOLDING', average_cost=50, data_valid=False)
    assert d.decision == 'OBSERVE'
    assert not any('移動停利' in r for r in d.reasons + d.follow_up)
    d = evaluate(position_status='HOLDING', average_cost=50)
    assert d.decision == 'HOLD'
    assert not any('移動停利' in r for r in d.warnings)


@pytest.mark.parametrize('rate,state', [(10, 'LARGE_PROFIT'), (3, 'NEAR_COST'),
    (-3, 'NEAR_COST'), (-3.1, 'LOSS')])
def test_cost_thresholds_and_inconsistent_return(rate, state):
    d = evaluate(position_status='HOLDING', average_cost=100 / (1 + rate / 100),
                 unrealized_return=rate)
    assert d.trade_evidence['cost_context']['state'] == state
    assert d.trade_evidence['unrealized_return'] == rate
    d = evaluate(position_status='HOLDING', average_cost=100, unrealized_return=-80)
    assert d.trade_evidence['cost_context']['state'] == 'NEAR_COST'


def test_cost_management_does_not_change_market_assessment():
    decisions = [evaluate(position_status='HOLDING', average_cost=cost) for cost in (80, 100, 120)]
    for d in decisions[1:]:
        for field in ('entry_action', 'holder_action', 'entry_score', 'risk_score', 'risk_gate'):
            assert getattr(d, field) == getattr(decisions[0], field)


@pytest.mark.parametrize('changes', [dict(rsi_state='OVERSOLD'), dict(macd_momentum='bearish_strengthening'),
                                    dict(support_status='CONFIRMED_BREAK', current_price=90)])
def test_single_indicator_does_not_exit(changes):
    assert evaluate(position_status='HOLDING', **changes).decision != 'EXIT'


@pytest.mark.parametrize('changes,reason', [
    (dict(institutional_level='BEARISH'), '法人籌碼偏空'),
    (dict(short_term_direction=-1), '短期趨勢偏空'),
    (dict(medium_term_direction=-1), '中期趨勢偏空'),
    (dict(relative_market_strength=-3), '相對大盤落後 3 個百分點'),
    (dict(resistance_status='REJECTED'), '上方壓力測試受阻'),
    (dict(volatility_level='HIGH'), '波動度偏高'),
    (dict(atr_percent=9), '波動度極高'),
])
def test_each_analysis_dimension_affects_weighted_risk(changes, reason):
    baseline = dict(position_status='HOLDING', macd_momentum='bullish_weakening')
    assert evaluate(**baseline).decision == 'HOLD'
    d = evaluate(**baseline, **changes)
    assert d.trade_evidence['weighted_score'] >= evaluate(**baseline).trade_evidence['weighted_score']
    assert d.decision in ('HOLD', 'OBSERVE')
    if 'volatility_level' in changes or 'atr_percent' in changes:
        assert not d.trade_evidence['volatility_effect']['affects_action']
        assert not any('ATR' in text for text in d.reasons + d.warnings)
    else:
        assert any(reason in text for text in d.trade_evidence['risk_evidence'])



@pytest.mark.parametrize('changes', [dict(institutional_level='BEARISH'),
    dict(institutional_level='UNKNOWN'), dict(relative_market_strength=-3),
    dict(short_term_direction=-1)])
def test_market_conflicts_block_new_exposure(changes):
    assert evaluate(**changes).decision == 'OBSERVE'
    assert evaluate(position_status='HOLDING', **changes).decision == ('OBSERVE' if changes.get('institutional_level') == 'UNKNOWN' else 'HOLD')


@pytest.mark.parametrize('cost', [50, 100, 150, None])
def test_profit_and_loss_do_not_vote_on_market_actions(cost):
    c = dict(position_status='HOLDING', average_cost=cost)
    assert evaluate(**c).decision == 'HOLD'
    healthy = evaluate(**c, distance_to_support=4)
    assert healthy.decision == 'HOLD'
    assert '中期趨勢偏多' in healthy.reasons
    assert any('仍守穩' in reason for reason in healthy.reasons)
    weak = evaluate(**c, current_price=90, support_status='CONFIRMED_BREAK',
                    macd_momentum='bullish_weakening', institutional_level='BEARISH')
    assert weak.decision == 'OBSERVE'  # Structural loss without medium deterioration remains caution.
    display = '\n'.join(format_trade_recommendation(asdict(weak)))
    for reason in ('結構防守已確認失守', 'MACD 動能轉弱', '法人籌碼偏空'):
        assert reason in display
    assert '持倉狀態' not in display


def test_correlated_trends_and_volatility_are_not_multiple_sell_votes():
    d = evaluate(position_status='HOLDING', short_term_direction=-1, medium_term_direction=-1)
    assert d.trade_evidence['risk_dimensions'] == ['trend']
    assert d.decision == 'HOLD'
    assert evaluate(position_status='HOLDING', volatility_level='HIGH', distance_to_support=4).decision == 'HOLD'
    assert evaluate(position_status='HOLDING', rsi_state='OVERBOUGHT', kd_state='BEARISH').decision == 'HOLD'


def test_stale_institutional_signals_cannot_confirm_reduction():
    d = evaluate(position_status='HOLDING', macd_momentum='bullish_weakening',
                 institutional_level='BEARISH', institutional_freshness='STALE')
    assert d.decision == 'OBSERVE'
    assert 'institutional' not in d.trade_evidence['risk_dimensions']
    assert not any('法人籌碼偏空' in reason for reason in d.reasons)


def test_positive_flow_and_relative_strength_appear_only_when_established():
    d = evaluate(position_status='HOLDING', institutional_level='BULLISH', relative_market_strength=4)
    assert d.decision == 'HOLD'
    assert '法人籌碼偏多' in d.trade_evidence['positive_evidence']
    assert '相對大盤領先 4 個百分點' in d.reasons
    assert not any('法人籌碼偏多' in reason or '相對大盤領先' in reason for reason in evaluate().reasons)


def monitor(connection, context=BASE):
    data = {'decision_context': asdict(context), 'timeframe_analysis': {}}
    update_monitor_decision(data, connection)
    return data['trading_decision']


def test_confirmation_replay_restart_role_change_and_history(tmp_path):
    path = tmp_path / 'trade.db'
    with sqlite3.connect(path) as conn:
        d = monitor(conn)
        assert d['decision'] == 'OBSERVE' and d['trade_confirmation_count'] == 0
        assert monitor(conn)['trade_confirmation_count'] == 0
        assert conn.execute('SELECT count(*) FROM trading_decision_history').fetchone()[0] == 1
    with sqlite3.connect(path) as conn:
        day2 = replace(BASE, observation_time='2026-09-16')
        d = monitor(conn, day2)
        assert d['decision'] == 'OBSERVE' and d['trade_confirmation_count'] == 0
        assert monitor(conn, day2)['decision'] == 'OBSERVE'
        holding = replace(day2, position_status='HOLDING', average_cost=110, unrealized_return=-9.09)
        assert monitor(conn, holding)['decision'] == 'HOLD'
        assert monitor(conn, replace(holding, observation_time='2026-09-17'))['decision'] == 'HOLD'
        assert monitor(conn, replace(holding, observation_time='2026-09-18'))['decision'] == 'HOLD'
        rows = conn.execute('SELECT date,symbol,position_status,decision,decision_reasons,context_json,decision_json FROM trading_decision_history ORDER BY id').fetchall()
        assert len(rows) == 5
        assert [row[3] for row in rows] == ['OBSERVE', 'OBSERVE', 'HOLD', 'HOLD', 'HOLD']
        assert json.loads(rows[-1][5])['average_cost'] == 110
        assert json.loads(rows[-1][6])['config']['confirmation_required'] == 2
        before = conn.execute('SELECT * FROM decision_state').fetchone()
        assert monitor(conn, BASE)['decision'] == 'OBSERVE'
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


def test_minor_risk_does_not_start_a_structural_reduction():
    with sqlite3.connect(':memory:') as conn:
        bad = replace(BASE, position_status='HOLDING', support_status='MINOR_BREAK', medium_term_direction=-1, institutional_level='STRONG_PRESSURE', macd_momentum='bullish_weakening')
        assert monitor(conn, bad)['decision'] == 'OBSERVE'
        healthy = replace(BASE, position_status='HOLDING', observation_time='2026-09-16', distance_to_support=4)
        assert monitor(conn, healthy)['decision'] == 'HOLD'
        assert monitor(conn, replace(healthy, observation_time='2026-09-17'))['decision'] == 'HOLD'


def test_no_persist_incomplete_and_new_zone_restarts_confirmation():
    with sqlite3.connect(':memory:') as conn:
        monitor(conn, replace(BASE, observation_complete=False))
        assert conn.execute('SELECT count(*) FROM trading_decision_history').fetchone()[0] == 0
        monitor(conn, BASE)
        changed = replace(BASE, observation_time='2026-09-16', active_support_zone={'low': 98, 'high': 100, 'stable_zone_id': 'NEW'})
        d = monitor(conn, changed)
        assert d['decision'] == 'OBSERVE' and d['trade_confirmation_count'] == 0


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
    assert d.decision == 'OBSERVE'
    lines = format_trade_recommendation(asdict(d))
    assert lines[0] == '【交易建議】' and lines[1] == '目前動作：觀察'
    assert '主要原因：' in lines and '持倉狀態：' not in lines
    assert '後續觀察：' in lines
