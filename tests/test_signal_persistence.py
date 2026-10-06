from dataclasses import asdict, replace
import json
import sqlite3

import pytest

from app.decision_engine import DecisionConfig, DecisionEngine
from app.decision_state import create_table, stabilize, update_monitor_decision
from test_signal_hysteresis import observation


def tick(connection, context=None, *, minute=0, previous='觀望', required=2):
    context = context or observation(101.)
    data = dict(decision_context=asdict(context), timeframe_analysis={},
                analysis={'signal': context.raw_signal_state})
    update_monitor_decision(data, connection,
        previous_action_state={'signal_state': previous},
        config=DecisionConfig(signal_confirmation_required=required),
        signal_observation_time=f'2026-09-24T02:{minute:02d}:00+00:00')
    assert data['analysis']['signal'] == data['trading_decision']['final_action_state']
    return data['trading_decision']


def memory(connection, symbol='TEST'):
    cursor = connection.execute('SELECT * FROM decision_state WHERE symbol=?', (symbol,))
    return dict(zip([col[0] for col in cursor.description], cursor.fetchone()))


@pytest.mark.parametrize('previous,context,candidate', [
    ('觀望', observation(101.), '偏多'),
    ('觀望', observation(97., 98., 99., 100.), '偏空'),
    ('偏多', observation(99.7), '觀望'),
    ('偏空', observation(100.3, 100., 101., 102.), '觀望'),
    ('偏多', observation(97., 98., 99., 100.), '偏空'),
    ('偏空', observation(101.), '偏多'),
])
def test_first_candidate_held_second_monitor_commits(previous, context, candidate):
    with sqlite3.connect(':memory:') as db:
        first = tick(db, context, previous=previous)
        assert first['hysteresis_candidate_state'] == first['pending_candidate_state'] == candidate
        assert first['signal_confirmation_count'] == 1
        assert first['required_confirmations'] == 2
        assert first['persistence_held'] and first['final_action_state'] == previous
        saved = memory(db)
        assert saved['signal_state'] == saved['signal_previous_action_state'] == previous
        assert saved['signal_pending_candidate_state'] == candidate
        assert saved['signal_confirmation_count'] == 1
        second = tick(db, context, previous=previous, minute=1)
        assert second['final_action_state'] == candidate
        assert second['pending_candidate_state'] is None and second['signal_confirmation_count'] == 0
        assert not second['persistence_held']
        assert second['signal_persistence']['confirmed_count'] == 2
        saved = memory(db)
        assert saved['signal_state'] == candidate and saved['signal_previous_action_state'] == previous
        assert saved['signal_last_observation_time'] == '2026-09-24T02:01:00+00:00'


def test_candidate_disappears_and_next_occurrence_starts_at_one():
    with sqlite3.connect(':memory:') as db:
        tick(db)
        # Hysteresis must use committed OBSERVE, never the pending bullish state.
        back = tick(db, observation(99.9), minute=1)
        assert back['hysteresis_candidate_state'] == back['final_action_state'] == '觀望'
        assert back['pending_candidate_state'] is None and back['signal_confirmation_count'] == 0
        assert not back['persistence_held']
        assert tick(db, minute=2)['signal_confirmation_count'] == 1


def test_changed_candidate_restarts_count():
    with sqlite3.connect(':memory:') as db:
        tick(db)
        other = tick(db, observation(97., 98., 99., 100.), minute=1)
        assert other['pending_candidate_state'] == '偏空'
        assert other['signal_confirmation_count'] == 1 and other['final_action_state'] == '觀望'
        assert tick(db, observation(97., 98., 99., 100.), minute=2)['final_action_state'] == '偏空'


def test_restart_continues_pending_count(tmp_path):
    path = tmp_path / 'restart.db'
    with sqlite3.connect(path) as db:
        assert tick(db, required=3)['signal_confirmation_count'] == 1
    with sqlite3.connect(path) as db:
        second = tick(db, required=3, minute=1)
        assert second['signal_confirmation_count'] == 2 and second['final_action_state'] == '觀望'
    with sqlite3.connect(path) as db:
        assert tick(db, required=3, minute=2)['final_action_state'] == '偏多'


def test_hysteresis_retention_clears_pending_instead_of_counting():
    with sqlite3.connect(':memory:') as db:
        first = tick(db, observation(99.7), previous='偏多')
        assert first['pending_candidate_state'] == '觀望'
        for minute in (1, 2):
            held = tick(db, observation(99.9), previous='偏多', minute=minute)
            assert held['raw_action_state'] == '觀望' and held['hysteresis_candidate_state'] == '偏多'
            assert held['hysteresis_held'] and not held['persistence_held']
            assert held['pending_candidate_state'] is None and held['signal_confirmation_count'] == 0
            assert memory(db)['signal_confirmation_count'] == 0


def test_same_monitor_retry_and_stale_market_data_do_not_advance():
    with sqlite3.connect(':memory:') as db:
        tick(db)
        before = memory(db)
        assert tick(db)['signal_confirmation_count'] == 1
        assert memory(db) == before
        stale = tick(db, replace(observation(101.), observation_time='2026-09-23'), minute=1)
        assert stale['signal_confirmation_count'] == 1 and stale['final_action_state'] == '觀望'
        assert memory(db) == before
        assert tick(db, minute=2)['final_action_state'] == '偏多'


def test_new_monitor_same_daily_bar_and_incomplete_bar_count():
    with sqlite3.connect(':memory:') as db:
        c = replace(observation(101.), observation_complete=False)
        assert tick(db, c)['signal_confirmation_count'] == 1
        assert tick(db, c, minute=1)['final_action_state'] == '偏多'
        assert memory(db)['last_observation_time'] is None  # Existing daily confirmation is independent.
    with sqlite3.connect(':memory:') as db:
        def run():
            data = dict(decision_context=asdict(c), timeframe_analysis={})
            update_monitor_decision(data, db, previous_action_state={'signal_state': '觀望'})
            return data['trading_decision']
        assert run()['signal_confirmation_count'] == 1
        assert run()['final_action_state'] == '偏多'  # Defaults identify separate monitoring rounds.


def test_history_records_pending_progress_and_final_state():
    with sqlite3.connect(':memory:') as db:
        for minute in range(3):
            tick(db, required=3, minute=minute)
        payloads = [json.loads(row[0])['decision'] for row in db.execute(
            'SELECT decision_json FROM trading_decision_history ORDER BY id')]
        assert [d['signal_confirmation_count'] for d in payloads] == [1, 2, 0]
        assert [d['final_action_state'] for d in payloads] == ['觀望', '觀望', '偏多']
        tick(db, required=3, minute=3)
        assert db.execute('SELECT COUNT(*) FROM trading_decision_history').fetchone()[0] == 3


def test_migration_keeps_legacy_counts_and_state():
    with sqlite3.connect(':memory:') as db:
        db.execute('CREATE TABLE decision_state (symbol TEXT PRIMARY KEY, signal_state TEXT, confirmation_count INTEGER, last_observation_time TEXT)')
        db.execute("INSERT INTO decision_state VALUES ('TEST', '偏多', 7, '2026-09-23')")
        create_table(db)
        create_table(db)
        saved = memory(db)
        assert saved['signal_state'] == '偏多' and saved['confirmation_count'] == 7
        assert saved['last_observation_time'] == '2026-09-23'
        assert saved['signal_confirmation_count'] == 0 and saved['signal_pending_candidate_state'] is None


def test_transaction_failure_does_not_advance_confirmation():
    with sqlite3.connect(':memory:') as db:
        tick(db)
        before = memory(db)
        db.execute("CREATE TRIGGER reject_state BEFORE UPDATE ON decision_state BEGIN SELECT RAISE(ABORT, 'rollback'); END")
        db.commit()
        with pytest.raises(sqlite3.IntegrityError, match='rollback'):
            tick(db, minute=1)
        assert memory(db) == before
        assert db.execute('SELECT COUNT(*) FROM trading_decision_history').fetchone()[0] == 1


@pytest.mark.parametrize('invalid', [0, -1, 1.5, 2., True, None, float('inf'), float('nan')])
def test_confirmation_requirement_validation(invalid):
    with pytest.raises(ValueError):
        DecisionConfig(signal_confirmation_required=invalid)


def test_direct_engine_and_stabilize_share_persistence_without_double_count():
    c = observation(101.)
    engine = DecisionEngine()
    old = {'signal_state': '觀望'}
    raw = engine.evaluate(c, old)
    assert raw.final_action_state == '觀望' and raw.signal_confirmation_count == 1
    first, state = stabilize(raw, c, old)
    assert first.signal_confirmation_count == state['signal_confirmation_count'] == 1
    assert first.final_action_state == '觀望'
    second, _ = stabilize(engine.evaluate(c), c, state, signal_observation_time='2026-09-24T00:05:00+00:00')
    assert second.final_action_state == '偏多'


def test_no_previous_state_initializes_and_symbols_are_isolated():
    with sqlite3.connect(':memory:') as db:
        assert tick(db, previous=None)['final_action_state'] == '偏多'
        other = tick(db, replace(observation(101.), symbol='OTHER'))
        assert other['final_action_state'] == '觀望' and other['signal_confirmation_count'] == 1
