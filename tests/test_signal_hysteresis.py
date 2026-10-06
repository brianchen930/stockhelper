"""Exercise actual MA strategy thresholds, not invented decision scores."""
from dataclasses import asdict, replace
import json
import sqlite3

import pandas as pd
import pytest

from app.decision_context import build_decision_context
from app.decision_engine import DecisionConfig, DecisionContext, DecisionEngine
from app.decision_state import stabilize, update_monitor_decision
from app.strategies import analyze_ma_strategy


HYSTERESIS_ONLY = DecisionConfig(signal_confirmation_required=1)


def observation(close, ma5=100., ma20=99., ma60=98., **changes):
    values = dict(close=close, ma5=ma5, ma20=ma20, ma60=ma60)
    strategy = analyze_ma_strategy(pd.DataFrame([dict(Close=close, ma5=ma5, ma20=ma20, ma60=ma60)]))
    return DecisionContext(symbol='TEST', observation_time='2026-09-24', observation_complete=True,
        raw_signal_state=strategy['signal'], signal_ma_values=values, **changes)


@pytest.mark.parametrize('context,previous,raw,final,held', [
    (observation(100.01), '觀望', '偏多', '偏多', False),
    (observation(99.9), '偏多', '觀望', '偏多', True),
    (observation(99.7), '偏多', '觀望', '觀望', False),
    (observation(99.99, 100., 101., 102.), '觀望', '偏空', '偏空', False),
    (observation(100.1, 100., 101., 102.), '偏空', '觀望', '偏空', True),
    (observation(100.3, 100., 101., 102.), '偏空', '觀望', '觀望', False),
    (observation(100.), '觀望', '觀望', '觀望', False),
    (observation(99.8), '偏多', '觀望', '偏多', True),
    (observation(100.2, 100., 101., 102.), '偏空', '觀望', '偏空', True),
    (observation(99.7), None, '觀望', '觀望', False),
    (observation(99.7), 'UNKNOWN', '觀望', '觀望', False),
])
def test_transitions(context, previous, raw, final, held):
    d = DecisionEngine(HYSTERESIS_ONLY).evaluate(context, {'signal_state': previous})
    assert (d.raw_action_state, d.final_action_state, d.hysteresis_held) == (raw, final, held)
    assert d.previous_action_state['signal_state'] == previous
    assert d.signal_hysteresis['previous_action_state'] == previous
    assert d.signal_hysteresis['raw_action_state'] == raw
    assert d.signal_hysteresis['final_action_state'] == final
    assert d.signal_hysteresis['hysteresis_held'] == held


@pytest.mark.parametrize('previous,values', [
    ('偏多', (102., 99.7, 100., 98.)),
    ('偏多', (102., 101., 99.7, 100.)),
    ('偏空', (98., 100.3, 100., 102.)),
    ('偏空', (98., 99., 100.3, 100.)),
])
def test_every_adjacent_ma_pair_can_exit(previous, values):
    d = DecisionEngine(HYSTERESIS_ONLY).evaluate(observation(*values), {'signal_state': previous})
    assert d.final_action_state == '觀望'
    assert not d.hysteresis_held


@pytest.mark.parametrize('previous,values,expected', [
    ('偏多', (97., 98., 99., 100.), '偏空'),
    ('偏空', (103., 102., 101., 100.), '偏多'),
])
def test_strong_reversal_uses_original_classification(previous, values, expected):
    assert DecisionEngine(HYSTERESIS_ONLY).evaluate(observation(*values), {'signal_state': previous}).final_action_state == expected


def test_configurable_exit_and_no_new_confirmation_or_score_changes():
    c = observation(99.85)
    engine = DecisionEngine(DecisionConfig(signal_confirmation_required=1, signal_bullish_exit_pct=.1, signal_bearish_exit_pct=.3))
    assert engine.evaluate(c, {'signal_state': '偏多'}).final_action_state == '觀望'
    assert engine.evaluate(observation(100.25, 100., 101., 102.), {'signal_state': '偏空'}).final_action_state == '偏空'
    baseline = asdict(DecisionEngine(HYSTERESIS_ONLY).evaluate(c))
    held = asdict(DecisionEngine(HYSTERESIS_ONLY).evaluate(c, {'signal_state': '偏多'}))
    for key in ('previous_action_state', 'raw_action_state', 'final_action_state', 'hysteresis_held', 'signal_hysteresis',
                'hysteresis_candidate_state', 'pending_candidate_state', 'signal_confirmation_count',
                'required_confirmations', 'persistence_held', 'signal_persistence',
                'critical_event', 'bypass_persistence', 'bypass_hysteresis', 'critical_event_rules',
                'pre_bypass_candidate_state', 'persistence_cleared', 'critical_event_details'):
        baseline.pop(key)
        held.pop(key)
    assert baseline == held
    # Same unfinished bar: no new close, count, or cooldown is required.
    engine = DecisionEngine(HYSTERESIS_ONLY)
    c = replace(observation(101.), observation_complete=False)
    d, state = stabilize(engine.evaluate(c), c, {'signal_state': '觀望'}, engine.config)
    assert d.final_action_state == '偏多'
    weaker = replace(observation(99.7), observation_complete=False)
    assert stabilize(engine.evaluate(weaker), weaker, state, engine.config,
                     signal_observation_time='2026-09-24T01:05:00+00:00')[0].final_action_state == '觀望'


@pytest.mark.parametrize('value', [0, -1, True, float('nan'), float('inf')])
@pytest.mark.parametrize('key', ['signal_bullish_exit_pct', 'signal_bearish_exit_pct'])
def test_invalid_thresholds(key, value):
    with pytest.raises(ValueError):
        DecisionConfig(**{key: value})


@pytest.mark.parametrize('value', [None, 0, -1, True, float('nan'), float('inf')])
def test_missing_invalid_ma_does_not_claim_hysteresis(value):
    c = observation(99.9)
    c = replace(c, signal_ma_values={**c.signal_ma_values, 'ma20': value})
    d = DecisionEngine(HYSTERESIS_ONLY).evaluate(c, {'signal_state': '偏多'})
    assert not d.hysteresis_held and not d.signal_hysteresis['valid']


def monitor(connection, c, **kwargs):
    data = dict(decision_context=asdict(c), timeframe_analysis={}, analysis={'signal': c.raw_signal_state})
    kwargs.setdefault('config', HYSTERESIS_ONLY)
    update_monitor_decision(data, connection, **kwargs)
    return data


def test_restart_replay_history_stale_and_symbol_isolation(tmp_path):
    path = tmp_path / 'hysteresis.db'
    with sqlite3.connect(path) as db:
        assert monitor(db, observation(101.))['analysis']['signal'] == '偏多'
    with sqlite3.connect(path) as db:
        c = observation(99.9)
        held = monitor(db, c)
        assert held['analysis'] == {'raw_signal': '觀望', 'signal': '偏多'}
        d = held['trading_decision']
        assert d['previous_action_state']['signal_state'] == '偏多'
        assert d['hysteresis_held']
        count = db.execute('SELECT COUNT(*) FROM trading_decision_history').fetchone()[0]
        assert monitor(db, c)['trading_decision']['hysteresis_held']
        assert db.execute('SELECT COUNT(*) FROM trading_decision_history').fetchone()[0] == count
        payload = json.loads(db.execute('SELECT decision_json FROM trading_decision_history ORDER BY id DESC').fetchone()[0])
        assert payload['decision']['final_action_state'] == '偏多'
        assert payload['decision']['raw_action_state'] == '觀望'
        assert payload['decision']['hysteresis_held']
        assert monitor(db, replace(c, symbol='OTHER'))['analysis']['signal'] == '觀望'
        stale = monitor(db, replace(observation(99.7), observation_time='2026-09-23'))
        assert stale['analysis']['signal'] == '偏多'
        assert not stale['trading_decision']['hysteresis_held']
        assert monitor(db, observation(99.7))['analysis']['signal'] == '觀望'


def test_legacy_signal_seed_then_persisted_state_wins():
    with sqlite3.connect(':memory:') as db:
        seed = {'signal_state': '偏多'}
        assert monitor(db, observation(99.9), previous_action_state=seed)['analysis']['signal'] == '偏多'
        assert monitor(db, observation(99.7))['analysis']['signal'] == '觀望'
        assert monitor(db, observation(99.9), previous_action_state=seed)['analysis']['signal'] == '觀望'


def test_changing_config_on_same_input_persists_new_final_state():
    with sqlite3.connect(':memory:') as db:
        c = observation(99.85)
        assert monitor(db, c, previous_action_state={'signal_state': '偏多'})['analysis']['signal'] == '偏多'
        config = DecisionConfig(signal_confirmation_required=1, signal_bullish_exit_pct=.1)
        assert monitor(db, c, config=config)['analysis']['signal'] == '觀望'
        assert db.execute('SELECT signal_state FROM decision_state').fetchone()[0] == '觀望'
        assert monitor(db, c)['analysis']['signal'] == '觀望'


def test_adapter_preserves_full_precision_inputs():
    bars = pd.DataFrame([dict(Close=100.0001, ma5=100., ma20=99., ma60=98.)])
    result = dict(close=100., ma5=100., ma20=99., ma60=98., analysis=analyze_ma_strategy(bars))
    context = build_decision_context(result, bars)
    assert context.raw_signal_state == '偏多'
    assert context.signal_ma_values['close'] == 100.0001


@pytest.mark.parametrize('close', [99.9, 99.7])
def test_scheduler_rules_and_saved_signal_use_final_state(tmp_path, monkeypatch, close):
    from app import database, scheduler
    monkeypatch.setattr(database, 'DATABASE_PATH', tmp_path / 'monitor.db')
    database.create_tables()
    c = observation(close)
    timeframe = {key: dict(label='中性', score=0, score_min=None, score_max=None,
                          bullish_factors=[], bearish_factors=[], warnings=[], summary='均線糾結')
                 for key in ('short_term', 'medium_term')}
    timeframe.update(overall_summary='均線糾結', overall_warnings=[])
    data = dict(decision_context=asdict(c), timeframe_analysis=timeframe, close=close,
                analysis=dict(signal='觀望', trend='均線糾結', reasons=[]))
    monkeypatch.setattr(scheduler, 'get_all_stocks', lambda: [dict(
        stock_code='TEST', last_signal='偏多', last_trend='均線糾結')])
    monkeypatch.setattr(scheduler, 'analyze_watchlist', lambda stocks: [dict(
        stock_code='TEST', status='success', data=data)])
    monkeypatch.setattr(scheduler, 'assess_analysis_quality', lambda data: dict(is_valid=True, issues=[]))
    captured, saved = [], []
    evaluate = scheduler.evaluate_notification
    def capture(**kwargs):
        captured.append(kwargs['current_signal'])
        return evaluate(**kwargs)
    monkeypatch.setattr(scheduler, 'evaluate_notification', capture)
    monkeypatch.setattr(scheduler, 'update_stock_state', lambda **kwargs: saved.append(kwargs['signal']))
    def no_delivery(items):
        return dict(matched_count=len(items), success_count=len(items), failed_count=0,
                    failed_items=[], item_results=[dict(stock_code=item['stock_code'], success=True) for item in items])
    monkeypatch.setattr(scheduler, 'send_stock_notifications', no_delivery)
    scheduler.run_monitor_job()
    assert captured == saved == ['偏多']
    assert data['trading_decision']['hysteresis_held'] == (close == 99.9)
    assert data['trading_decision']['persistence_held'] == (close == 99.7)
    scheduler.run_monitor_job()
    assert captured == saved == ['偏多', '偏多' if close == 99.9 else '觀望']
