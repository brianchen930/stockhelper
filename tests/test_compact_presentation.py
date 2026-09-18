from copy import deepcopy
import pandas as pd
import pytest

from app.support_resistance_analysis.formatting import format_support_resistance_output
from app.rules.support_resistance_rule import format_support_resistance_events
from app.analysis_engine import build_technical_summary, generate_analysis
from app.strategies import analyze_ma_strategy


def zone(low, distance):
    return dict(low=low, high=low+1, center=low+0.5, distance_pct=distance,
                strength_label='strong', strength_score=8, methods=['swing_low'],
                touch_count=7, last_touch_date='2026-09-03T00:00:00+08:00')


def test_compact_keeps_full_model_and_chooses_nearest_not_first():
    sr = dict(current_price=100, active_zones=[zone(100, 0.2), zone(99, 0.1)],
              support_zones=[zone(80, -20), zone(95, -5), zone(90, -10)],
              resistance_zones=[zone(120, 20), zone(105, 5), zone(110, 10)])
    before = deepcopy(sr)
    text = format_support_resistance_output(sr)
    assert text.count('・') == 2
    assert '目前測試區' not in text  # Unassigned detector zones have no confirmed trading role.
    assert '最近支撐：95.00～96.00' in text
    assert '距支撐區約 -4.00%' in text  # Nearest boundary, not center/legacy distance.
    assert '最近壓力：105.00～106.00' in text
    assert '距壓力區約 +5.00%' in text
    for hidden in ('第二', '第三', '中心', '測試：7', '2026-', '8.00/10'):
        assert hidden not in text
    assert sr == before


def event(category, message):
    return dict(category=category, message=message, zone=zone(99, 1), distance_pct=1)


@pytest.mark.parametrize('winner,loser', [
    ('RESISTANCE_BREAKOUT', 'ENTER_SUPPORT_ZONE'),
    ('SUPPORT_BREAKDOWN', 'SUPPORT_BOUNCE'),
    ('ENTER_RESISTANCE_ZONE', 'RESISTANCE_REJECTION'),
    ('SUPPORT_BOUNCE', 'NEAR_SUPPORT'),
    ('RESISTANCE_REJECTION', 'NEAR_RESISTANCE'),
])
def test_event_display_priority_preserves_list(winner, loser):
    events = [event(loser, '次要事件'), event(winner, '主要事件')]
    before = deepcopy(events)
    text = '\n'.join(format_support_resistance_events(events))
    assert '主要事件' in text and '次要事件' not in text
    assert winner not in text and loser not in text
    assert events == before


def test_active_is_not_synthesized_and_real_events_remain():
    sr = {'active_zones': [zone(100, 0)]}
    near = event('NEAR_SUPPORT', '接近強支撐')
    text = '\n'.join(format_support_resistance_events([near], sr))
    assert '正在測試重要價格區' not in text and '接近強支撐' in text
    breakout = event('RESISTANCE_BREAKOUT', '突破重要壓力')
    text = '\n'.join(format_support_resistance_events([near, breakout], sr))
    assert '突破重要壓力' in text and '正在測試' not in text


@pytest.mark.parametrize('close,ma5,ma20,ma60,expected', [
    (99, 110, 105, 100, '均線糾結'),
    (111, 110, 105, 100, '多頭排列'),
    (111, 90, 95, 100, '均線糾結'),
    (89, 90, 95, 100, '空頭排列'),
])
def test_daily_summary_uses_same_strategy_state(close, ma5, ma20, ma60, expected):
    strategy = analyze_ma_strategy(pd.DataFrame([dict(Close=close, ma5=ma5, ma20=ma20, ma60=ma60)]))
    summary = build_technical_summary(dict(analysis=strategy, close=close, ma5=ma5, ma20=ma20, ma60=ma60))
    assert strategy['trend'] == expected
    assert f'日線趨勢：{expected}' in summary
    base = generate_analysis(strategy['trend'], strategy['signal'], 0, [])
    if expected == '均線糾結':
        assert '糾結' in base['summary']


def test_missing_strategy_does_not_invent_a_second_trend():
    assert '日線趨勢：資料不足' in build_technical_summary(dict(ma5=110, ma20=105, ma60=100))
