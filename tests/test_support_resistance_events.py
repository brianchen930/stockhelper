from copy import deepcopy
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import json

import pytest
from app.rules.support_resistance_rule import (SupportResistanceRule, acknowledge_events,
    format_support_resistance_events, SupportResistanceNotificationConfig)
from app.rules import evaluate_notification
from app.scheduler import support_resistance_bar_closed
from app.support_resistance_analysis.formatting import format_support_resistance_output

NOW = datetime(2026, 9, 1, 15, tzinfo=ZoneInfo('Asia/Taipei'))


def zone(role='support', low=95, high=100, strength=8):
    return dict(type=role, low=low, high=high, center=(low+high)/2,
                strength_score=strength, strength_label='strong', methods=['swing_low', 'kmeans'],
                touch_count=3, last_touch_date=None, distance_pct=0)


def context(price, zones, state=None, day=0, **kwargs):
    now = NOW+timedelta(days=day)
    sr = dict(current_price=price, as_of=now.date().isoformat(), support_zones=[],
              resistance_zones=[], active_zones=[])
    for z in zones:
        sr[z['type']+'_zones'].append(z)
    return dict(support_resistance=sr, support_resistance_state=state, now=now,
                support_resistance_bar_closed=True, **kwargs)


def evaluate(price, zones, state=None, day=0):
    return SupportResistanceRule().evaluate(context(price, zones, state, day))


def receipt(result, day=0):
    return acknowledge_events(result['state'], result['notifications'], NOW+timedelta(days=day))


def kinds(result):
    return [e['category'] for e in result['notifications']]


def test_far_support_no_notification():
    assert not evaluate(110, [zone()])['should_notify']


@pytest.mark.parametrize('role,price,event', [
    ('support', 101, 'NEAR_SUPPORT'), ('resistance', 94, 'NEAR_RESISTANCE')])
def test_near_strong_zone(role, price, event):
    assert kinds(evaluate(price, [zone(role)])) == [event]


@pytest.mark.parametrize('role,initial,price,event', [
    ('support', 105, 99, 'ENTER_SUPPORT_ZONE'),
    ('support', 105, 94, 'SUPPORT_BREAKDOWN'),
    ('resistance', 90, 96, 'ENTER_RESISTANCE_ZONE'),
    ('resistance', 90, 101, 'RESISTANCE_BREAKOUT')])
def test_transitions_use_original_zone(role, initial, price, event):
    first = evaluate(initial, [zone(role)])
    # New detector may classify it as active or opposite role. Original wins.
    new_role = 'active' if 95 <= price <= 100 else 'resistance' if role == 'support' else 'support'
    second = evaluate(price, [zone(new_role)], first['state'], 1)
    assert kinds(second) == [event]
    assert second['notifications'][0]['role'] == role


@pytest.mark.parametrize('role,initial,entry,exit,event', [
    ('support', 105, 99, 102, 'SUPPORT_BOUNCE'),
    ('resistance', 90, 96, 93, 'RESISTANCE_REJECTION')])
def test_reaction_requires_observed_entry(role, initial, entry, exit, event):
    first = evaluate(initial, [zone(role)])
    second = evaluate(entry, [zone('active')], first['state'], 1)
    third = evaluate(exit, [zone(role)], receipt(second, 1), 2)
    assert kinds(third) == [event]
    repeated = evaluate(exit, [zone(role)], receipt(third, 2), 2)
    assert not repeated['should_notify']


def test_repeat_cooldown_reentry_and_drift():
    first = evaluate(101, [zone()])
    state = receipt(first)
    second = evaluate(101, [zone(low=95.1, high=100.1)], state, 1)
    assert not second['should_notify']
    assert evaluate(101, [zone()], second['state'], 3)['should_notify']
    left = evaluate(110, [zone()], state, 1)
    assert evaluate(101, [zone()], left['state'], 2)['should_notify']


def test_changed_zone_can_notify():
    first = evaluate(101, [zone()])
    second = evaluate(106, [zone(low=101, high=105)], receipt(first), 1)
    assert kinds(second) == ['NEAR_SUPPORT']


def test_failed_delivery_retries_same_event_after_restart():
    first = evaluate(105, [zone()])
    broken = evaluate(94, [zone('resistance')], first['state'], 1)
    restarted_state = json.dumps(broken['state'])
    retry = evaluate(94, [zone('resistance')], restarted_state, 1)
    assert retry['notifications'][0]['id'] == broken['notifications'][0]['id']
    assert not evaluate(94, [zone('resistance')], receipt(retry, 1), 1)['should_notify']


def test_close_tolerance_and_forming_bar():
    first = evaluate(105, [zone()])
    assert not evaluate(94.9, [zone('resistance')], first['state'], 1)['should_notify']
    ctx = context(94, [zone('resistance')], first['state'], 1)
    ctx['support_resistance_bar_closed'] = False
    result = SupportResistanceRule().evaluate(ctx)
    assert not result['should_notify'] and result['state'] == first['state']
    assert not support_resistance_bar_closed({'as_of': '2026-09-01'}, NOW.replace(hour=10))
    assert support_resistance_bar_closed({'as_of': '2026-08-31'}, NOW.replace(hour=10))


def test_weak_events_display_only():
    result = evaluate(101, [zone(strength=3)])
    assert result['events'] and not result['should_notify']


@pytest.mark.parametrize('sr', [{}, {'error': 'unavailable'}, None])
def test_missing_or_failed_sr_does_not_change_technical_rules(sr):
    kwargs = dict(current_signal='偏多', current_trend='多頭排列', previous_signal='觀望',
                  previous_trend='均線糾結', reasons=[], rsi=65)
    baseline = evaluate_notification(**kwargs)
    result = evaluate_notification(**kwargs, support_resistance=sr)
    assert result['score'] == baseline['score']
    assert result['technical_score'] == baseline['technical_score']
    assert result['should_notify'] == baseline['should_notify']


def test_rule_engine_reads_sr_without_scoring():
    ctx = context(101, [zone()])
    result = evaluate_notification(current_signal='觀望', current_trend='均線糾結',
        previous_signal='觀望', previous_trend='均線糾結', reasons=[], **ctx)
    assert result['should_notify'] and result['support_resistance_notify']
    assert result['technical_score'] == result['score'] == 0
    assert result['level'] == '一般通知'
    assert result['technical_matched_rules'] == []


def test_formatter_active_empty_and_notification():
    text = format_support_resistance_output({})
    assert '【支撐 / 壓力】' in text and '目前沒有偵測到' in text
    text = format_support_resistance_output(context(98, [zone('active')])['support_resistance'])
    assert '目前測試區' in text and 'Swing Low' in text
    event_text = '\n'.join(format_support_resistance_events(evaluate(101, [zone()])['notifications']))
    assert '接近強支撐' in event_text and '95.00～100.00' in event_text
    assert 'NEAR_SUPPORT' not in event_text


def test_sr_failure_isolated_at_stock_boundary(monkeypatch):
    import app.stock as stock
    def fail(*args):
        raise RuntimeError('detector failure')
    monkeypatch.setattr(stock.SupportResistanceEngine, 'detect', fail)
    result = {'rsi': 50, 'analysis': {'signal': '觀望'}}
    stock._attach_support_resistance(result, None)
    assert result['rsi'] == 50 and result['analysis']['signal'] == '觀望'
    assert result['support_resistance']['error']


def test_database_migration_and_state(tmp_path, monkeypatch):
    import app.database as database
    monkeypatch.setattr(database, 'DATABASE_PATH', tmp_path/'stocks.db')
    database.create_tables()
    database.create_tables()
    database.add_stock('TEST')
    state = receipt(evaluate(101, [zone()]))
    database.save_support_resistance_state('TEST', state)
    assert json.loads(database.get_all_stocks()[0]['support_resistance_state']) == state


def test_scheduler_delivery_retry_and_no_duplicate(tmp_path, monkeypatch, capsys):
    import app.database as database
    import app.scheduler as scheduler
    from app.notifier import send_stock_notifications
    monkeypatch.setattr(database, 'DATABASE_PATH', tmp_path/'stocks.db')
    database.create_tables()
    database.add_stock('TEST', 'Fixture')
    data = dict(close=101, realtime_price=101, price_change_percent=0,
                analysis={'signal': '觀望', 'trend': '均線糾結'}, technical_summary='Fixture technical summary',
                support_resistance=context(101, [zone()])['support_resistance'])
    monkeypatch.setattr(scheduler, 'analyze_watchlist', lambda stocks: [
        dict(stock_code='TEST', stock_name='Fixture', status='success', data=data)])
    messages = []
    success = False
    def sender(message):
        messages.append(message)
        return dict(success=success, status_code=200 if success else 503, error=None if success else 'test failure')
    monkeypatch.setattr(scheduler, 'send_stock_notifications',
                        lambda items: send_stock_notifications(items, sender=sender))
    scheduler.run_monitor_job()
    assert len(messages) == 1
    assert json.loads(database.get_all_stocks()[0]['support_resistance_state'])['pending']
    success = True
    scheduler.run_monitor_job()
    assert len(messages) == 2 and '接近強支撐' in messages[-1]
    assert '【支撐 / 壓力】' in messages[-1]
    assert not json.loads(database.get_all_stocks()[0]['support_resistance_state'])['pending']
    scheduler.run_monitor_job()
    assert len(messages) == 2
    assert '【支撐 / 壓力】' in capsys.readouterr().out


def test_first_seen_active_zone_has_no_invented_entry():
    result = evaluate(98, [zone('active')])
    assert not result['events'] and not result['should_notify']


def test_out_of_order_close_preserves_state():
    first = evaluate(101, [zone()], day=2)
    state = receipt(first, 2)
    result = evaluate(94, [zone('resistance')], state, 1)
    assert result['state'] == state and not result['should_notify']
