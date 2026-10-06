from copy import deepcopy
from dataclasses import asdict, replace
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app.decision_context import build_decision_context
from app.decision_engine import DecisionEngine
from app.decision_formatter import format_trade_recommendation
from app.decision_state import stabilize
from tests.test_decision_context import inputs
from tests.test_trade_actions import BASE


def render(context):
    decision = DecisionEngine().evaluate(context)
    return decision, '\n'.join(format_trade_recommendation(asdict(decision)))


@pytest.mark.parametrize('hour,minute,complete', [(13, 29, False), (13, 30, True)])
def test_intraday_with_complete_price_and_cost_is_not_missing_data(hour, minute, complete):
    result, bars, _ = inputs()
    result.update(history_date='2026-09-30', date='2026-09-29', position_status='HOLDING', average_cost=80)
    context = build_decision_context(result, bars, now=datetime(2026, 9, 30, hour, minute,
        tzinfo=ZoneInfo('Asia/Taipei')))
    decision, text = render(context)
    assert context.observation_time == '2026-09-30'
    assert context.observation_complete is complete
    assert decision.trade_evidence['checks']['valid'] is True
    assert decision.trade_evidence['contributions']
    assert decision.trade_evidence['cost_context']['return_percent'] == 25
    assert '持倉成本或本輪行情資料不足' not in text
    assert '本輪缺少有效決策資料' not in text
    assert ('盤中評估' in text) is not complete


@pytest.mark.parametrize('stamp,expected', [(None, '缺少日線資料日期'),
    ('not-a-date', '日線資料日期格式無效'), ('2026-10-01', '晚於本輪日期')])
def test_timestamp_failures_are_distinct(stamp, expected):
    result, bars, _ = inputs()
    result.update(history_date=stamp, date=stamp)
    context = build_decision_context(result, bars, now=datetime(2026, 9, 30, 14,
        tzinfo=ZoneInfo('Asia/Taipei')))
    decision, text = render(context)
    assert not decision.trade_evidence['checks']['valid']
    assert expected in text
    assert '持倉狀態' not in text


@pytest.mark.parametrize('field,value,expected', [('atr', None, 'ATR 缺失或無效'),
    ('atr', float('inf'), 'ATR 缺失或無效'), ('atr', float('nan'), 'ATR 缺失或無效'),
    ('volatility_level', 'UNKNOWN', '波動度分類缺失'),
    ('current_price', None, '日線價格缺失或無效')])
def test_each_required_field_has_its_own_blocker(field, value, expected):
    decision, text = render(replace(BASE, **{field: value}, position_status='HOLDING', average_cost=80))
    assert not decision.trade_evidence['checks']['valid']
    assert expected in text
    assert '持倉成本或本輪行情資料不足' not in text
    assert '中期趨勢偏多' not in text


def test_present_trend_object_without_score_is_really_invalid():
    result, bars, _ = inputs()
    del result['timeframe_analysis']['medium_term']['score']
    context = build_decision_context(result, bars)
    decision, text = render(context)
    assert not context.data_valid
    assert decision.reasons == ['中期趨勢分析缺失或無效']
    assert '中期趨勢分析缺失或無效' in text


def test_stale_means_older_than_committed_bar_not_missing_cost():
    context = replace(BASE, position_status='HOLDING', average_cost=80)
    d, _ = stabilize(DecisionEngine().evaluate(context), context,
        dict(last_observation_time='2026-09-16', entry_state='WAIT', holder_state='HOLD'))
    text = '\n'.join(format_trade_recommendation(asdict(d)))
    assert d.state_basis == 'STALE_DATA'
    assert '2026-09-15 早於已採用的日線' in text
    assert '持倉成本或本輪行情資料不足' not in text


def test_missing_legacy_validity_metadata_preserves_recorded_decision_reason():
    payload = dict(decision='OBSERVE', reasons=['上方壓力測試受阻'], follow_up=['觀察壓力區是否突破'])
    original = deepcopy(payload)
    text = '\n'.join(format_trade_recommendation(payload))
    assert '上方壓力測試受阻' in text
    assert '資料不足' not in text
    assert payload == original


def test_missing_institutional_data_that_blocks_hold_is_primary_reason():
    decision, text = render(replace(BASE, institutional_level='UNKNOWN', position_status='HOLDING'))
    assert decision.decision == 'OBSERVE'
    assert decision.trade_evidence['checks']['valid']
    assert '缺少有效法人資料' in text
    assert '風險提醒' not in text


def test_atr_positive_contribution_without_action_change_is_not_displayed():
    context = replace(BASE, position_status='HOLDING', current_price=90,
        support_status='CONFIRMED_BREAK', medium_term_direction=-1,
        macd_momentum='bearish_strengthening', atr_percent=9)
    decision, text = render(context)
    assert decision.decision == 'EXIT'
    assert any(x['code'] == 'ATR_RISK_AMPLIFIER' for x in decision.trade_evidence['contributions'])
    assert not decision.trade_evidence['volatility_effect']['affects_action']
    assert 'ATR' not in text
    assert not any('ATR' in x for x in decision.reasons + decision.warnings)


@pytest.mark.parametrize('cost', [None, 80, 100, 120])
def test_cost_does_not_add_sections_or_education(cost):
    d, text = render(replace(BASE, position_status='HOLDING', average_cost=cost))
    assert d.decision == 'HOLD'
    for unwanted in ('持倉狀態', '風險提醒', '成本不是技術支撐', '不因虧損攤平', '資料不足或時間過舊'):
        assert unwanted not in text
