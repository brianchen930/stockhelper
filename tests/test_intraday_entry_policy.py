"""Intraday evaluation, event-scoped closes, and soft entry risk regression."""
from dataclasses import asdict, replace
import json
import sqlite3

import pytest

from app.decision_engine import DecisionEngine, ActionState as A
from app.decision_formatter import format_trade_recommendation
from app.decision_state import stabilize, update_monitor_decision
from app.trade_explanation import entry_conditions
from tests.test_entry_paths import context, pullback
from tests.test_trade_actions import BASE


def run(c, previous=None):
    return stabilize(DecisionEngine().evaluate(c), c, previous or {})


@pytest.mark.parametrize('changes,expected', [
    ({}, 'HOLD'),
    (dict(current_price=90, support_status='CONFIRMED_BREAK', medium_term_direction=-1,
          macd_momentum='bearish_strengthening', institutional_level='STRONG_PRESSURE',
          relative_market_strength=-5), 'EXIT'),
])
def test_intraday_evaluates_every_dimension_and_holding_action(changes, expected):
    c = replace(BASE, position_status='HOLDING', institutional_level='BULLISH',
                relative_market_strength=3)
    c = replace(c, **changes)
    closed, state = run(c)
    # Established structural failure survives the next forming observation.
    intraday, _ = run(replace(c, observation_complete=False, observation_status='INCOMPLETE'), state)
    assert intraday.decision == closed.decision == expected
    assert intraday.trade_evidence['checks']['valid']
    assert intraday.trade_evidence['weighted_score'] == closed.trade_evidence['weighted_score']
    assert set(intraday.trade_evidence['dimension_scores']) >= {
        'structure', 'trend', 'momentum', 'institutional', 'relative_market'}
    text = '\n'.join(format_trade_recommendation(asdict(intraday)))
    assert '盤中評估' in text
    assert '中期趨勢' in text
    assert '尚未完成收盤確認（台北時間 13:30）' not in text


@pytest.mark.parametrize('factory', [context, pullback])
@pytest.mark.parametrize('changes', [dict(volatility_level='EXTREME'), dict(atr_percent=8),
    dict(overextended=True), dict(overextended=True, volatility_level='EXTREME')])
def test_soft_risks_allow_probe_after_confirmation_without_cooling(factory, changes):
    c = factory(**changes)
    first, state = run(c)
    assert not first.risk_gate
    assert first.entry_action == A.WATCH_FOR_CONFIRMATION
    key = 'breakout' if factory is context else 'pullback'
    assert first.entry_paths[key]['confirmation_required'] == 2
    assert first.entry_paths[key]['risk_modifiers']
    repeat, same = run(c, state)
    assert same == state
    assert repeat.entry_paths[key]['confirmation_count'] == 1
    second, _ = run(replace(c, observation_time='2026-09-15'), state)
    assert second.entry_action == A.ALLOW_PROBE_ENTRY
    assert second.entry_paths[key]['ready']
    text = '\n'.join(format_trade_recommendation(asdict(second)))
    assert '進場上限為小幅試單' in text
    assert '限制解除' not in text and '乖離回到' not in text


@pytest.mark.parametrize('price', [119, 121])
def test_intraday_does_not_erase_or_advance_closed_confirmation(price):
    c = context(overextended=True)
    _, state = run(c)
    memory = state['entry_path_memory']
    intraday, state = run(replace(c, observation_time='2026-09-15',
                                 observation_complete=False, current_price=price), state)
    assert state['entry_path_memory'] == memory
    assert not intraday.entry_paths['entry_ready']
    assert intraday.entry_paths['breakout']['confirmation_count'] == 1
    closed, state = run(replace(c, observation_time='2026-09-15'), state)
    assert closed.entry_action == A.ALLOW_PROBE_ENTRY
    # A failed completed bar, unlike an intraday dip, does reset confirmation.
    _, state = run(replace(c, observation_time='2026-09-16', current_price=119), state)
    next_close, _ = run(replace(c, observation_time='2026-09-17'), state)
    assert next_close.entry_paths['breakout']['confirmation_count'] == 1
    assert not next_close.entry_paths['entry_ready']


def test_ordinary_strong_breakout_needs_one_close_and_no_short_trend_gate():
    c = context(short_term_direction=-1, short_term_score=-1)
    d, _ = run(c)
    assert d.entry_paths['breakout']['confirmation_required'] == 1
    assert d.entry_action == A.ENTRY_CONDITION_MET
    assert not entry_conditions(d.entry_paths['breakout'])[0].endswith('日線符合條件')
    intraday, _ = run(replace(c, observation_complete=False))
    assert intraday.trade_evidence['checks']['valid']
    assert not intraday.entry_paths['entry_ready']
    conditions = entry_conditions(intraday.entry_paths['breakout'])
    assert len(conditions) == 1 and '本根日線收盤確認' in conditions[0]
    assert '短中期' not in conditions[0]


def test_follow_up_reports_only_current_obstacles_and_no_soft_risk_repairs():
    d, _ = run(context(current_price=119, resistance_status='TESTING',
                        volatility_level='EXTREME', overextended=True))
    conditions = entry_conditions(d.entry_paths['breakout'])
    assert len(conditions) == 1
    assert '0.5 ATR' in conditions[0] and '或 0.25 ATR' in conditions[0]
    for obsolete in ('限制解除', '乖離回到', '短中期趨勢', '連續 2 根'):
        assert obsolete not in conditions[0]
    blocked, _ = run(context(institutional_level='STRONG_PRESSURE',
        support_probability='VERY_LOW', medium_term_direction=-1))
    assert 1 <= len(entry_conditions(blocked.entry_paths['breakout'])) <= 3


def test_intraday_does_not_release_retained_holder_risk():
    bad = replace(BASE, position_status='HOLDING', current_price=90,
                  support_status='CONFIRMED_BREAK', medium_term_direction=-1,
                  macd_momentum='bearish_strengthening')
    _, state = run(bad)
    good = replace(BASE, position_status='HOLDING', observation_time='2026-09-16')
    closed, state = run(good, state)
    assert closed.trade_evidence['recovery']['count'] == 1
    intraday, state = run(replace(good, observation_time='2026-09-17', observation_complete=False), state)
    assert intraday.decision == 'REDUCE'
    assert intraday.trade_evidence['recovery']['count'] == 1
    complete, _ = run(replace(good, observation_time='2026-09-17'), state)
    assert complete.decision == 'HOLD'


def test_sqlite_restart_preserves_intraday_entry_counts_and_signal_persistence(tmp_path):
    c = context(symbol='INTRADAY', overextended=True, raw_signal_state='偏多',
                signal_ma_values=dict(close=121, ma5=120, ma20=119, ma60=118))
    path = tmp_path / 'decision.db'
    def tick(context, stamp):
        data = dict(decision_context=asdict(context), timeframe_analysis={})
        with sqlite3.connect(path) as connection:
            update_monitor_decision(data, connection, previous_action_state={'signal_state': '觀望'},
                                    signal_observation_time=stamp)
        return data['trading_decision']
    first = tick(c, '2026-09-14T06:00:00+00:00')
    assert first['final_action_state'] == '觀望'
    assert first['signal_confirmation_count'] == 1
    intraday = replace(c, observation_time='2026-09-15', observation_complete=False)
    second = tick(intraday, '2026-09-15T02:00:00+00:00')
    assert second['final_action_state'] == '偏多'
    assert second['entry_paths']['breakout']['confirmation_count'] == 1
    assert not second['entry_paths']['entry_ready']
    repeat = tick(intraday, '2026-09-15T02:00:00+00:00')
    assert repeat['signal_persistence']['status'] == 'REPLAY'
    closed = tick(replace(c, observation_time='2026-09-15'), '2026-09-15T06:00:00+00:00')
    assert closed['entry_action'] == A.ALLOW_PROBE_ENTRY
    with sqlite3.connect(path) as connection:
        memory = json.loads(connection.execute('SELECT entry_path_memory FROM decision_state').fetchone()[0])
    assert memory['breakout']['count'] == 2
