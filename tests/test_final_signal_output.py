"""Exercise real decision storage, rules, formatter and notifier boundary."""
from copy import deepcopy
from dataclasses import asdict, replace
from datetime import datetime, timedelta

import pytest

from app import database, scheduler
from app.decision_state import load_decision_state, update_monitor_decision
from app.notifier import send_stock_notifications
from test_signal_hysteresis import observation


def payload(close):
    context = replace(observation(close), observation_complete=False)
    timeframe = {key: dict(label='中性', score=0, score_min=None, score_max=None,
                           bullish_factors=[], bearish_factors=[], warnings=[], summary='均線糾結')
                 for key in ('short_term', 'medium_term')}
    timeframe.update(overall_summary='均線糾結', overall_warnings=[])
    return dict(stock_code='TEST', close=close, price_change_percent=0,
                decision_context=asdict(context), timeframe_analysis=timeframe,
                analysis=dict(signal=context.raw_signal_state,
                              trend='多頭排列' if context.raw_signal_state == '偏多' else '均線糾結',
                              reasons=['本輪均線觀察']))


@pytest.fixture
def monitor(monkeypatch):
    database.create_tables()
    database.add_stock('TEST', '測試')
    # Existing confirmed state and delivery baseline, using the real storage.
    update_monitor_decision(payload(101), signal_observation_time='2026-09-24T01:00:00+00:00')
    database.update_stock_state('TEST', '偏多', '多頭排列', notified=True,
        notification_signature=scheduler.build_notification_signature('偏多', '多頭排列', 1, [], ''))
    clock = [datetime.fromisoformat('2026-09-24T09:01:00+08:00')]
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock[0].astimezone(tz) if tz else clock[0].replace(tzinfo=None)
    monkeypatch.setattr(scheduler, 'datetime', Clock)
    messages, queued, rules = [], [], []
    success = [True]
    def sender(message):
        messages.append(message)
        return dict(success=success[0], status_code=204 if success[0] else 503,
                    error=None if success[0] else 'test failure')
    def deliver(items):
        queued.extend(deepcopy(items))
        return send_stock_notifications(items, sender=sender)
    monkeypatch.setattr(scheduler, 'send_stock_notifications', deliver)
    evaluate = scheduler.evaluate_notification
    def capture(**kwargs):
        result = evaluate(**kwargs)
        rules.append(result)
        return result
    monkeypatch.setattr(scheduler, 'evaluate_notification', capture)
    def tick(close, **changes):
        data = payload(close)
        data.update(changes)
        monkeypatch.setattr(scheduler, 'analyze_watchlist', lambda stocks: [
            dict(stock_code='TEST', stock_name='測試', status='success', data=data)])
        scheduler.run_monitor_job()
        clock[0] += timedelta(seconds=30)
        return data
    return tick, messages, queued, rules, success


def test_intraday_hysteresis_blocks_raw_trend_and_indicator_notifications(monitor, capsys):
    tick, messages, queued, rules, _ = monitor
    # A simultaneous MACD cross would previously notify despite a held signal.
    weak = tick(99.9, macd=2, macd_signal=1, macd_histogram=1,
                previous_macd=0, previous_macd_signal=1, previous_macd_histogram=-1)
    assert weak['analysis']['raw_signal'] == '觀望'
    assert weak['trading_decision']['hysteresis_held']
    assert weak['analysis']['signal'] == weak['signal_summary']['final_action_state'] == '偏多'
    assert not rules[-1]['technical_notify']
    assert not any(e['category'] == 'trend_change' for e in rules[-1]['market_events'])
    assert not any(e['notify'] for e in rules[-1]['market_events'])
    assert tick(101)['analysis']['signal'] == '偏多'
    assert not messages and not queued
    assert load_decision_state('TEST')['signal_state'] == '偏多'
    output = capsys.readouterr().out
    assert '最終訊號：偏多' in output
    assert '最終訊號：觀望' not in output


def test_persistence_gates_delivery_and_recovery_then_deduplicates(monitor):
    tick, messages, queued, _, _ = monitor
    first = tick(99.7)
    assert first['trading_decision']['persistence_held'] and not messages
    assert first['analysis']['signal'] == '偏多'
    confirmed = tick(99.7)
    assert confirmed['trading_decision']['signal_persistence']['status'] == 'CONFIRMED'
    assert len(messages) == 1 and queued[-1]['signal'] == '觀望'
    assert '最終訊號：觀望' in messages[-1]
    assert '訊號由「偏多」變成「觀望」' in messages[-1]
    tick(99.7, rsi=85)
    assert len(messages) == 1
    assert tick(101)['analysis']['signal'] == '觀望'
    assert len(messages) == 1
    assert tick(101)['analysis']['signal'] == '偏多'
    assert len(messages) == 2 and queued[-1]['signal'] == '偏多'


def test_failed_delivery_retries_committed_state_without_reconfirming(monitor):
    tick, messages, _, _, success = monitor
    tick(99.7)
    success[0] = False
    tick(99.7)
    assert len(messages) == 1
    assert load_decision_state('TEST')['signal_state'] == '觀望'
    assert database.get_stock('TEST')['last_signal'] == '偏多'
    success[0] = True
    retry = tick(99.7)
    assert retry['trading_decision']['signal_persistence']['status'] == 'UNCHANGED'
    assert len(messages) == 2
    assert database.get_stock('TEST')['last_signal'] == '觀望'
    tick(99.7)
    assert len(messages) == 2


@pytest.mark.parametrize('close', [99.9, 99.7])
def test_read_only_output_uses_committed_memory_without_advancing_counts(monitor, monkeypatch, close):
    from app import decision_context
    from app.analysis_engine import attach_signal_summary
    tick, messages, _, _, _ = monitor
    tick(close)
    before = load_decision_state('TEST')
    for _ in range(3):
        data = payload(close)
        context = replace(observation(close), observation_complete=False)
        monkeypatch.setattr(decision_context, 'build_decision_context', lambda *a, **k: context)
        decision_context.attach_decision(data)
        attach_signal_summary(data)
        assert data['analysis']['signal'] == data['trading_decision']['final_action_state'] == '偏多'
        assert data['signal_summary']['final_action_state'] == '偏多'
        assert data['timeframe_analysis']['trading_decision']['final_action_state'] == '偏多'
        assert load_decision_state('TEST') == before
    assert not messages


def test_missing_decision_cannot_send_raw_signal_notification(monitor):
    tick, messages, _, rules, _ = monitor
    tick(99.7, decision_context=None, timeframe_analysis={})
    assert not messages and not rules[-1]['technical_notify']
    assert database.get_stock('TEST')['last_signal'] == '偏多'


def test_signature_ignores_raw_observation_changes():
    assert scheduler.build_notification_signature('偏多', '多頭排列', 1, [], '偏多') == (
        scheduler.build_notification_signature('偏多', '均線糾結', 9, ['MACD 交叉'], '觀望'))
