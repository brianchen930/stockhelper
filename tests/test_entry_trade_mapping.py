"""Final action routing must preserve EntryEngine and holder risk policies."""
from dataclasses import asdict, replace
import sqlite3

import pytest

from app.decision_engine import ActionState, DecisionEngine, TradingDecision, HolderActionState
from app.decision_formatter import format_trade_recommendation
from app.decision_state import stabilize, update_monitor_decision
from tests.test_trade_actions import BASE


@pytest.mark.parametrize('entry,expected', [
    ('ALLOW_ENTRY', 'ENTER'), (ActionState.ENTRY_CONDITION_MET, 'ENTER'),
    (ActionState.ALLOW_PROBE_ENTRY, 'ENTER'),
    (ActionState.WATCH_FOR_CONFIRMATION, 'OBSERVE'), (ActionState.WAIT, 'OBSERVE'),
    (ActionState.DO_NOT_CHASE, 'OBSERVE'), (ActionState.AVOID, 'OBSERVE'),
])
def test_watching_routes_finalized_entry_action(entry, expected):
    c = replace(BASE, institutional_level='UNKNOWN')
    d = TradingDecision(entry, HolderActionState.HOLD_WITH_CAUTION)
    result = DecisionEngine().evaluate_trade(d, c)
    assert result.decision == expected
    assert result.entry_action == entry
    assert result.trade_evidence['action_constraints'] == ['缺少有效法人資料']
    if expected == 'ENTER':
        text = '\n'.join(format_trade_recommendation(asdict(result)))
        assert '目前動作：建倉' in text
        assert ('進場類型：試單' if entry == ActionState.ALLOW_PROBE_ENTRY else '進場類型：正常進場') in text


@pytest.mark.parametrize('changes', [dict(data_valid=False), dict(atr=None),
    dict(current_price=None), dict(observation_status='FUTURE')])
def test_invalid_observation_cannot_enter_even_with_positive_entry_state(changes):
    d = TradingDecision(ActionState.ALLOW_PROBE_ENTRY, HolderActionState.HOLD)
    assert DecisionEngine().evaluate_trade(d, replace(BASE, **changes)).decision == 'OBSERVE'


def test_close_confirmation_replay_and_live_monitor_transition():
    engine = DecisionEngine()
    # This existing setup requires two completed closes; no criteria are mocked.
    c = replace(BASE, volatility_level='EXTREME', overextended=True,
                institutional_level='UNKNOWN')
    first, state = stabilize(engine.evaluate(c), c, {})
    assert first.entry_action == 'WATCH_FOR_CONFIRMATION'
    assert first.decision == 'OBSERVE'
    assert first.confirmation_count == 1
    same, _ = stabilize(engine.evaluate(c), c, state)
    assert same.confirmation_count == 1 and same.decision == 'OBSERVE'
    day2 = replace(c, observation_time='2026-09-16')
    second, next_state = stabilize(engine.evaluate(day2), day2, state)
    assert second.confirmation_count == 2
    assert second.entry_action == 'ALLOW_PROBE_ENTRY' and second.decision == 'ENTER'
    assert second.previous_action_state['entry_state'] == 'WATCH_FOR_CONFIRMATION'
    with sqlite3.connect(':memory:') as connection:
        for context, expected in ((c, 'OBSERVE'), (day2, 'ENTER'), (day2, 'ENTER')):
            data = dict(decision_context=asdict(context), timeframe_analysis={})
            update_monitor_decision(data, connection=connection,
                                    signal_observation_time=context.observation_time + 'T13:30:00+08:00')
            assert data['trading_decision']['decision'] == expected
        assert connection.execute('SELECT count(*) FROM trading_decision_history').fetchone()[0] == 2
    intraday = replace(day2, observation_time='2026-09-17', observation_complete=False)
    pending, _ = stabilize(engine.evaluate(intraday), intraday, next_state)
    assert pending.decision == 'OBSERVE'


@pytest.mark.parametrize('entry', list(ActionState))
def test_holding_action_independent_of_entry_action(entry):
    engine = DecisionEngine()
    for c, expected in (
        (replace(BASE, position_status='HOLDING'), 'HOLD'),
        (replace(BASE, position_status='HOLDING', current_price=90,
            support_status='CONFIRMED_BREAK', medium_term_direction=-1,
            macd_momentum='bearish_strengthening'), 'EXIT'),
    ):
        d = engine.evaluate(c)
        d.entry_action = entry
        assert engine.evaluate_trade(d, c).decision == expected
