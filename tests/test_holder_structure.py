from dataclasses import asdict, replace
from datetime import datetime
import json
import sqlite3
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from app.decision_context import build_decision_context
from app.decision_engine import DecisionConfig, DecisionEngine
from app.decision_formatter import format_operation_reference, format_trade_recommendation
from app.decision_state import stabilize, update_monitor_decision
from app.holder_structure import detect_breakout, structure_from_history
from tests.test_trade_actions import BASE


def bars(scale=1.):
    frame = pd.DataFrame(dict(Open=[98.] * 25, High=[100.] * 25, Low=[96.] * 25,
                              Close=[99.] * 25, Volume=[1000.] * 25),
                         index=pd.bdate_range('2026-08-03', periods=25))
    frame.loc[pd.Timestamp('2026-09-07')] = [99., 104., 98., 103., 2000.]
    frame[['Open', 'High', 'Low', 'Close']] *= scale
    return frame


def event_context(scale=1.):
    frame = bars(scale)
    event = detect_breakout(frame, '2026-09-07', DecisionConfig())
    return replace(BASE, symbol='STRUCTURE', observation_time='2026-09-07',
        position_status='HOLDING', current_price=103 * scale, previous_close=99 * scale,
        atr=2 * scale, breakout_event=event, active_support_zone=dict(low=101*scale, high=102*scale),
        support_status='HOLDING', support_strength=2)


def run(c, state=None):
    return stabilize(DecisionEngine().evaluate(c), c, state or {})


@pytest.mark.parametrize('scale', [.7, 1., 2.3])
def test_causal_platform_event_records_prices_without_symbol_constants(scale):
    c = event_context(scale)
    e = c.breakout_event
    assert e is not None
    assert e['breakout_level'] == 100 * scale
    assert e['breakout_candle_open'] == 99 * scale
    assert e['breakout_candle_low'] == 98 * scale
    assert e['breakout_date'] == '2026-09-07'
    d, _ = run(c)
    assert d.trade_evidence['structural_support_zone']['low'] == 98 * scale
    assert d.trade_evidence['structural_support_zone']['high'] == 100 * scale


@pytest.mark.parametrize('change', ['volume', 'open_missing', 'single_peak', 'wide_platform', 'bad_ohlc'])
def test_unqualified_breakouts_are_not_events(change):
    frame = bars()
    if change == 'volume':
        frame.loc[frame.index[-1], 'Volume'] = 1000
    elif change == 'open_missing':
        frame = frame.drop(columns='Open')
    elif change == 'single_peak':
        frame.loc[frame.index[:-2], 'High'] = 99
    elif change == 'wide_platform':
        frame.loc[frame.index[-10], 'Low'] = 50
    else:
        frame.loc[frame.index[-1], 'Low'] = 110
    assert detect_breakout(frame, '2026-09-07', DecisionConfig()) is None


def test_forming_and_future_bars_cannot_create_breakout():
    frame = bars()
    for stamp, complete in [('2026-09-07', False), ('2026-09-04', True)]:
        result = structure_from_history(frame, {}, stamp, complete, DecisionConfig())
        assert result['breakout_event'] is None
    assert structure_from_history(frame, {}, '2026-09-07', True, DecisionConfig())['breakout_event']


def test_adapter_detects_breakout_and_keeps_input_immutable():
    frame = bars()
    original = frame.copy(deep=True)
    result = dict(stock_code='AUTO', history_date='2026-09-07', close=103., atr=2.,
        volatility_level='NORMAL', timeframe_analysis=dict(short_term={'score': 3}, medium_term={'score': 4}),
        support_resistance=dict(nearest_support=dict(low=101., high=102., strength_score=8)))
    c = build_decision_context(result, frame, now=datetime(2026, 9, 7, 14, tzinfo=ZoneInfo('Asia/Taipei')))
    assert c.breakout_event['breakout_level'] == 100
    assert c.structural_support_zone['low'] == 98
    pd.testing.assert_frame_equal(frame, original)


def test_nearest_support_break_with_bearish_trend_is_caution_while_structure_holds():
    c = replace(event_context(), observation_time='2026-09-08', current_price=108,
        active_support_zone=dict(low=110., high=112., strength_score=9.),
        support_strength=9, support_status='CONFIRMED_BREAK', medium_term_direction=-1,
        macd_momentum='bearish_strengthening', institutional_level='STRONG_PRESSURE')
    d, _ = run(c)
    assert d.decision == 'OBSERVE'
    assert d.holder_action not in ('REDUCE_EXPOSURE', 'EXIT_CONDITION_APPROACHING')
    assert not d.trade_evidence['reduction_eligible']
    assert d.trade_evidence['short_term_support_broken']
    assert d.trade_evidence['structural_support_zone']['low'] == 98
    text = '\n'.join(format_trade_recommendation(asdict(d)))
    assert '110.00～112.00 短線支撐失守 → 短線轉弱' in text
    assert '98.00～100.00 結構防守確認跌破且中期趨勢轉弱 → 評估減碼' in text
    assert '110.00～112.00 支撐確認跌破且中期趨勢轉弱 → 評估減碼' not in text
    assert '→ 評估加碼' not in text
    assert '觀察是否站回' in format_operation_reference(d)['for_holder']


@pytest.mark.parametrize('price,complete,trend,can_reduce', [
    (99, True, -1, False), (96, False, -1, False),
    (96, True, 1, False), (96, True, -1, True),
])
def test_reduction_needs_completed_structural_failure_and_medium_deterioration(price, complete, trend, can_reduce):
    c = replace(event_context(), observation_time='2026-09-08', current_price=price,
        observation_complete=complete, medium_term_direction=trend,
        macd_momentum='bearish_strengthening', institutional_level='STRONG_PRESSURE')
    d, _ = run(c)
    assert (d.decision in ('CONSIDER_REDUCE', 'REDUCE', 'EXIT')) is can_reduce
    assert d.trade_evidence['reduction_eligible'] is can_reduce
    if can_reduce:
        assert d.current_trigger_evidence['trigger_zone']['low'] == 98
        assert d.current_trigger_evidence['primary_trigger']['support_scope'] == 'STRUCTURAL'


def test_no_structure_does_not_promote_a_weak_nearest_zone_to_reduction_line():
    c = replace(BASE, position_status='HOLDING', active_support_zone=dict(low=98, high=100),
        support_strength=2, support_status='CONFIRMED_BREAK', current_price=95,
        medium_term_direction=-1, macd_momentum='bearish_strengthening')
    d, _ = run(c)
    assert d.decision == 'OBSERVE'
    assert d.price_context['structural_support_zone'] is None
    text = '\n'.join(format_trade_recommendation(asdict(d)))
    assert '→ 評估減碼' not in text
    assert '短線轉弱' in text


def test_swing_and_strong_zone_fallbacks_are_independent_of_nearest_distance():
    weak = dict(low=99., high=100., strength_score=2.)
    strong = dict(low=90., high=92., strength_score=8.)
    swing = dict(low=87., high=88., strength_score=4., methods=['swing_low'])
    cfg = DecisionConfig()
    selected = structure_from_history(None, dict(support_zones=[weak, strong]), '2026-09-07', True, cfg)
    assert selected['structural_support_zone']['low'] == 90
    selected = structure_from_history(None, dict(support_zones=[weak, strong, swing]), '2026-09-07', True, cfg)
    assert selected['structural_support_zone']['low'] == 87
    resistance = dict(low=110., high=112., strength_score=10., status='BROKEN_RESISTANCE', previous_role='RESISTANCE')
    selected = structure_from_history(None, dict(support_zones=[weak], historical_zones=[resistance]), '2026-09-07', True, cfg)
    assert selected['structural_support_zone'] is None


def test_saved_event_survives_restart_missing_history_and_nearest_zone_replacement(tmp_path):
    path = tmp_path / 'structure.db'
    def monitor(c):
        payload = dict(decision_context=asdict(c), timeframe_analysis={})
        with sqlite3.connect(path) as db:
            update_monitor_decision(payload, db, signal_observation_time=c.observation_time + 'T06:00:00+00:00')
        return payload
    original = event_context()
    first = monitor(original)
    changed = replace(original, observation_time='2026-09-08', breakout_event=None,
        current_price=108, active_support_zone=dict(low=110, high=112), support_status='CONFIRMED_BREAK',
        medium_term_direction=-1, macd_momentum='bearish_strengthening')
    second = monitor(changed)
    assert second['decision_context']['breakout_event'] == first['decision_context']['breakout_event']
    assert second['trading_decision']['decision'] == 'OBSERVE'
    assert second['trading_decision']['price_context']['structural_support_zone']['low'] == 98
    with sqlite3.connect(path) as db:
        before = db.execute('SELECT holder_structure_memory FROM decision_state').fetchone()[0]
        rows = db.execute('SELECT count(*) FROM trading_decision_history').fetchone()[0]
    monitor(changed)
    monitor(replace(original, observation_time='2026-09-04'))
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT holder_structure_memory FROM decision_state').fetchone()[0] == before
        assert db.execute('SELECT count(*) FROM trading_decision_history').fetchone()[0] == rows
        assert json.loads(before)['breakout_event']['breakout_candle_open'] == 99


def test_structural_failure_recovery_still_requires_two_completed_closes():
    bad = replace(event_context(), observation_time='2026-09-08', current_price=96,
        medium_term_direction=-1, macd_momentum='bearish_strengthening', institutional_level='STRONG_PRESSURE')
    d, state = run(bad)
    assert d.decision == 'EXIT'
    good = replace(event_context(), observation_time='2026-09-09')
    d, state = run(good, state)
    assert d.decision == 'REDUCE' and d.trade_confirmation_count == 1
    intraday, unchanged = run(replace(good, observation_time='2026-09-10', observation_complete=False), state)
    assert intraday.decision == 'REDUCE'
    assert unchanged['holder_structure_memory'] == state['holder_structure_memory']
    d, _ = run(replace(good, observation_time='2026-09-10'), unchanged)
    assert d.decision == 'HOLD'


def test_expired_event_is_not_used_as_current_breakout_structure():
    c = event_context()
    d, state = run(c)
    expired, _ = run(replace(c, observation_time='2027-01-01'), state)
    assert expired.price_context['breakout_event'] is None
    assert expired.price_context['structural_support_zone'] is None


def test_holder_transition_attributes_structural_loss_to_original_breakout():
    c = event_context()
    with sqlite3.connect(':memory:') as db:
        payload = dict(decision_context=asdict(c), timeframe_analysis={})
        update_monitor_decision(payload, db)
        broken = replace(c, observation_time='2026-09-08', current_price=96,
                         medium_term_direction=-1, macd_momentum='bearish_strengthening')
        payload = dict(decision_context=asdict(broken), timeframe_analysis={})
        update_monitor_decision(payload, db)
    event = next(e for e in payload['trading_decision']['transition_events'] if e['role'] == 'HOLDER')
    assert event['primary_transition_reason']['code'] == 'STRUCTURAL_SUPPORT_BREAK'
    assert '98～100 結構防守' in event['primary_transition_reason']['text']


def test_intraday_first_scan_saves_only_the_previously_completed_event():
    c = replace(event_context(), observation_time='2026-09-08', observation_complete=False, current_price=96)
    d, state = run(c)
    saved = json.loads(state['holder_structure_memory'])
    assert saved['breakout_event']['breakout_date'] == '2026-09-07'
    assert saved['last_close'] == 103
    assert saved['structural_support_status'] == 'HOLDING'
    assert d.decision not in ('REDUCE', 'EXIT', 'CONSIDER_REDUCE')


def test_old_nearest_support_reduction_memory_is_recomputed_but_new_policy_is_debounced():
    c = event_context()
    old = dict(trade_memory=json.dumps(dict(version=4, decision='REDUCE')))
    fresh, _ = run(c, old)
    assert fresh.decision == 'HOLD'
    current = dict(trade_memory=json.dumps(dict(version=4, decision='REDUCE', support_policy='STRUCTURAL_V1')))
    retained, _ = run(c, current)
    assert retained.decision == 'REDUCE' and retained.trade_confirmation_count == 1
