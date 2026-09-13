from copy import deepcopy
import json
import pytest
from app.analysis.price_context import enrich_price_context
from app.analysis.timeframe_summary import summarize_timeframes, build_operation_reference
from app.rules.support_resistance_rule import format_support_resistance_events


def baseline(short_score=-3, medium_score=4):
    def view(score):
        return dict(score=score, label='偏多' if score > 0 else '偏空', summary='原始指標解釋。',
                    warnings=[], bullish_factors=[], bearish_factors=[], score_min=-8, score_max=8)
    short, medium = view(short_score), view(medium_score)
    summary, warnings = summarize_timeframes(short, medium)
    return dict(short_term=short, medium_term=medium, overall_summary=summary,
                overall_warnings=warnings, operation_reference=build_operation_reference(short, medium))


def zone(low=95, high=97, distance=-4, label='strong'):
    return dict(low=low, high=high, distance_pct=distance, strength_label=label, strength_score=6,
                methods=['swing_low'])


def sr(active=False, support=True, resistance=False):
    return dict(current_price=100, active_zones=[zone(99, 101, 0)] if active else [],
                support_zones=[zone()] if support else [],
                resistance_zones=[zone(102, 103, 2.5)] if resistance else [])


def test_active_support_context_and_mention_limit_preserve_scores():
    original = baseline()
    data = sr(active=True)
    original_copy, data_copy = deepcopy(original), deepcopy(data)
    result = enrich_price_context(original, data)
    assert '重要價格區' in result['short_term']['summary']
    assert '95.00～97.00' not in result['medium_term']['summary']
    assert '99.00～101.00' in result['overall_summary'] and '95.00～97.00' in result['overall_summary']
    assert not result['overall_warnings']
    text = json.dumps(result, ensure_ascii=False)
    assert text.count('99.00～101.00') == 1 and text.count('95.00～97.00') == 1
    assert original == original_copy and data == data_copy
    for key in ('short_term', 'medium_term'):
        assert {k: v for k, v in original[key].items() if k != 'summary'} == {
            k: v for k, v in result[key].items() if k != 'summary'}


def test_active_resistance_bullish_context():
    result = enrich_price_context(baseline(3, 4), sr(True, False, True))
    assert '動能' in result['short_term']['summary']
    assert '102.00～103.00' in result['overall_summary']


def test_support_without_active_and_medium_strength_not_called_strong():
    data = sr()
    data['support_zones'][0]['strength_label'] = 'medium'
    result = enrich_price_context(baseline(-3, -4), data)
    assert '95.00～97.00' in result['overall_summary']
    assert '強支撐' not in json.dumps(result, ensure_ascii=False)
    assert 'None' not in json.dumps(result, ensure_ascii=False)


def test_near_resistance_only_relevant_when_bullish():
    data = sr(False, False, True)
    result = enrich_price_context(baseline(3, 4), data)
    assert '102.00～103.00' in result['overall_summary']
    assert enrich_price_context(baseline(), data) == baseline()
    data['resistance_zones'][0]['distance_pct'] = 20
    assert enrich_price_context(baseline(3, 4), data) == baseline(3, 4)


@pytest.mark.parametrize('data', [None, {}, {'error': 'unavailable'},
    {'current_price': 100, 'support_zones': [{'low': None, 'high': None}]},
    {'current_price': 100, 'support_zones': [zone(distance=-20)]},
    {'current_price': float('nan')}])
def test_unavailable_context_exact_fallback(data):
    assert enrich_price_context(baseline(), data) == baseline()


@pytest.mark.parametrize('other', [None, 'SUPPORT_BREAKDOWN', 'RESISTANCE_BREAKOUT'])
def test_filter_only_duplicate_testing_event(other):
    active = zone(99, 101, 0)
    events = [dict(category='CURRENTLY_TESTING_ZONE', message='正在測試重要價格區', zone=active, distance_pct=0)]
    if other:
        events.append(dict(category=other, message='重要價格變化', zone=zone(), distance_pct=-4))
    copy = deepcopy(events)
    lines = format_support_resistance_events(events, {'active_zones': [active]})
    assert events == copy
    if other:
        assert '重要價格變化' in '\n'.join(lines)
        assert '正在測試' not in '\n'.join(lines)
    else:
        assert lines == []
