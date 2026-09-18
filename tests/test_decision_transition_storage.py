from dataclasses import asdict, replace
import json
import sqlite3

import pytest

from app.decision_engine import DecisionContext as C
from app.decision_state import create_table, update_monitor_decision


BASE = C(symbol='TEST', observation_time='2026-09-16', observation_complete=True,
         short_term_direction=-1, short_term_score=-2, medium_term_direction=1, medium_term_score=3,
         institutional_level='NEUTRAL', volatility_level='NORMAL', atr=3,
         macd_momentum='bearish_weakening', support_status='TESTING')


def run(connection, context=BASE):
    data = {'decision_context': asdict(context), 'timeframe_analysis': {}}
    update_monitor_decision(data, connection)
    return data


def count(connection):
    return connection.execute('SELECT COUNT(*) FROM decision_transition_events').fetchone()[0]


def stored(connection):
    return connection.execute('SELECT * FROM decision_state').fetchone()


def test_case7_restart_restores_state_and_snapshot(tmp_path):
    path = tmp_path / 'decisions.db'
    with sqlite3.connect(path) as c:
        run(c)
        before = stored(c)
    with sqlite3.connect(path) as c:
        data = run(c)
        assert stored(c) == before
        assert count(c) == 0
        assert data['trading_decision']['previous_action_state']['entry_state'] == 'WAIT'
        assert data['trading_decision']['transition_debug']['status'] == 'PURE_REPLAY'
        assert 'state_change' not in data['timeframe_analysis']['operation_reference']


def test_case8_same_observation_replay_no_event_or_confirmation_increment():
    with sqlite3.connect(':memory:') as c:
        run(c)
        bad = replace(BASE, support_status='CONFIRMED_BREAK')
        first = run(c, bad)
        assert first['trading_decision']['transition_events']
        before, n = stored(c), count(c)
        repeat = run(c, bad)
        assert stored(c) == before and count(c) == n
        assert not repeat['trading_decision']['transition_events']
        assert 'state_change' not in repeat['timeframe_analysis']['operation_reference']


def test_same_daily_timestamp_new_context_can_emit_even_same_edge():
    with sqlite3.connect(':memory:') as c:
        run(c)
        bad = replace(BASE, support_status='CONFIRMED_BREAK')
        first = run(c, bad)
        first_entry = next(e for e in first['trading_decision']['transition_events'] if e['role'] == 'ENTRY')
        run(c, BASE)
        # Identical edge and fingerprint is durably deduplicated, including after an intervening state.
        duplicate = run(c, bad)
        assert not duplicate['trading_decision']['transition_events']
        assert 'state_change' not in duplicate['timeframe_analysis']['operation_reference']
        run(c, BASE)
        revised = run(c, replace(bad, institutional_level='BEARISH', institutional_as_of='2026-09-16T14:00:00'))
        second_entry = next(e for e in revised['trading_decision']['transition_events'] if e['role'] == 'ENTRY')
        assert second_entry['data_timestamp'] == first_entry['data_timestamp']
        assert second_entry['observation_fingerprint'] != first_entry['observation_fingerprint']
        assert (second_entry['previous_state'], second_entry['current_state']) == ('WAIT', 'AVOID')


def test_old_schema_migration_preserves_state_and_seeds_baseline():
    with sqlite3.connect(':memory:') as c:
        c.execute('''CREATE TABLE decision_state (symbol TEXT PRIMARY KEY, entry_state TEXT, holder_state TEXT,
            candidate_entry_state TEXT, candidate_holder_state TEXT, confirmation_count INTEGER NOT NULL DEFAULT 0,
            holder_confirmation_count INTEGER NOT NULL DEFAULT 0, last_observation_time TEXT)''')
        c.execute("INSERT INTO decision_state VALUES ('TEST','WAIT','HOLD_WITH_CAUTION','WAIT','HOLD_WITH_CAUTION',1,1,'2026-09-16')")
        c.commit()
        create_table(c)
        create_table(c)
        assert stored(c)[1] == 'WAIT'
        result = run(c)
        assert count(c) == 0
        assert result['trading_decision']['transition_debug']['status'] == 'BASELINE_UNAVAILABLE'
        summary = json.loads(c.execute('SELECT previous_context_summary FROM decision_state').fetchone()[0])
        assert summary['version'] == 1 and summary['decision']['entry_state'] == 'WAIT'
        assert summary['context']['institutional_level'] == 'NEUTRAL'


def test_stale_observation_does_not_replace_snapshot():
    with sqlite3.connect(':memory:') as c:
        run(c)
        before = stored(c)
        data = run(c, replace(BASE, observation_time='2026-09-15', support_status='CONFIRMED_BREAK'))
        assert stored(c) == before and count(c) == 0
        assert data['trading_decision']['transition_debug']['status'] == 'STALE_OBSERVATION_IGNORED'


def test_event_and_state_update_are_atomic():
    with sqlite3.connect(':memory:') as c:
        run(c)
        before = stored(c)
        c.execute("CREATE TRIGGER fail_state BEFORE UPDATE ON decision_state BEGIN SELECT RAISE(ABORT, 'test rollback'); END")
        c.commit()
        with pytest.raises(sqlite3.IntegrityError, match='test rollback'):
            run(c, replace(BASE, support_status='CONFIRMED_BREAK'))
        assert stored(c) == before and count(c) == 0


def test_context_changes_without_state_changes_only_refresh_latest_summary():
    with sqlite3.connect(':memory:') as c:
        run(c)
        data = run(c, replace(BASE, institutional_score=0.2))
        assert count(c) == 0
        assert 'context.institutional_score' in data['trading_decision']['transition_debug']['context_delta']['changed']
        assert 'state_change' not in data['timeframe_analysis']['operation_reference']


def test_fingerprint_ignores_equivalent_numeric_serialization():
    from app.decision_transition_analyzer import context_fingerprint
    assert context_fingerprint(BASE) == context_fingerprint(replace(BASE, atr=3.0, short_term_score=-2.0))


def test_stale_bar_cannot_replace_newer_incomplete_snapshot():
    with sqlite3.connect(':memory:') as c:
        run(c)
        run(c, replace(BASE, observation_time='2026-09-17', observation_complete=False))
        before = stored(c)
        data = run(c, replace(BASE, support_status='CONFIRMED_BREAK'))
        assert stored(c) == before and count(c) == 0
        assert data['trading_decision']['transition_debug']['status'] == 'STALE_OBSERVATION_IGNORED'
