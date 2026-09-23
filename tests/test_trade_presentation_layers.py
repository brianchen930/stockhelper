from copy import deepcopy
from dataclasses import asdict, replace
import sqlite3

from app.decision_engine import DecisionEngine
from app.decision_formatter import format_trade_recommendation
from tests.test_trade_actions import BASE, monitor


def sections(d):
    result, current = {}, None
    for line in format_trade_recommendation(d):
        if line.endswith('：'):
            current = line[:-1]
            result[current] = []
        elif line.startswith('・') and current:
            result[current].append(line[1:])
    return result


def decision(**changes):
    return asdict(DecisionEngine().evaluate(replace(BASE, position_status='HOLDING', **changes)))


def test_retained_reduce_uses_final_action_not_bullish_candidate():
    d = decision(relative_market_strength=-3.85, average_cost=100 / 1.1505)
    assert d['decision'] == 'HOLD'
    d['decision'] = 'REDUCE'
    d['trade_evidence']['recovery'] = dict(pending=True, count=0, required=2)
    before = deepcopy(d)
    s = sections(d)
    assert s['主要原因'] == ['相對大盤落後 3.85 個百分點']
    assert '中期趨勢偏多' in s['保留部位理由']
    assert any('0/2' in line for line in s['後續觀察'])
    assert not any('0/2' in line for line in s['主要原因'])
    assert any('+15.05%' in line for line in s['持倉狀態'])
    assert any('可優先保護部分獲利' in line for line in s['持倉狀態'])
    assert not any('成本' in line for line in s['主要原因'])
    assert d == before  # Formatting cannot change the action, weights or memory.


def test_all_four_major_risks_are_not_displaced_by_protective_signals():
    d = decision(current_price=90, support_status='CONFIRMED_BREAK',
                 macd_momentum='bearish_strengthening', institutional_level='BEARISH',
                 relative_market_strength=-3.85)
    assert d['decision'] == 'REDUCE'
    s = sections(d)
    assert set(s['主要原因']) == {'主要支撐已確認失守', 'MACD 空方動能加速',
                                    '法人籌碼偏空', '相對大盤落後 3.85 個百分點'}
    assert '中期趨勢偏多' in s['保留部位理由']
    assert 'KD 尚未明顯轉空' in s['保留部位理由']
    assert not set(s['主要原因']) & set(s['保留部位理由'])
    assert any('支撐確認失守' in line for line in s['後續觀察'])
    assert not any('退出門檻' in line for line in s['後續觀察'])
    assert not any('同步賣超' in line for group in s.values() for line in group)


def test_no_new_risk_is_not_padded_with_bullish_or_historical_reasons():
    d = decision()
    d['decision'] = 'REDUCE'
    d['trade_evidence']['recovery'] = dict(pending=True, count=1, required=2)
    s = sections(d)
    assert s['主要原因'] == ['本輪沒有足以支持減碼的新風險訊號']
    assert any('先前風險警戒' in line for line in s['後續觀察'])
    assert all('偏多' not in line for line in s['主要原因'])


def test_live_recovery_progress_only_appears_in_follow_up():
    with sqlite3.connect(':memory:') as conn:
        bad = replace(BASE, position_status='HOLDING', current_price=90,
                      support_status='CONFIRMED_BREAK', medium_term_direction=-1,
                      macd_momentum='bearish_strengthening')
        assert monitor(conn, bad)['decision'] == 'EXIT'
        healthy = replace(BASE, position_status='HOLDING', observation_time='2026-09-16')
        d = monitor(conn, healthy)
        assert d['decision'] == 'REDUCE'
        assert not any('1/2' in reason for reason in d['reasons'])
        s = sections(d)
        assert not any('1/2' in line for line in s['主要原因'])
        assert any('1/2' in line for line in s['後續觀察'])
        assert any('支撐' in line for line in s['後續觀察'])
        assert monitor(conn, healthy)['trade_confirmation_count'] == 1
        recovered = monitor(conn, replace(healthy, observation_time='2026-09-17'))
        assert recovered['decision'] == 'HOLD'
        assert not any('/2' in line for line in sections(recovered)['後續觀察'])


def test_exit_does_not_present_opposing_signals_as_advice_to_keep_position():
    d = decision(current_price=90, support_status='CONFIRMED_BREAK',
                 medium_term_direction=-1, macd_momentum='bearish_strengthening')
    assert d['decision'] == 'EXIT'
    s = sections(d)
    assert all('尚不足以推翻退出判斷' in line for line in s['保留部位理由'])
    assert 'KD 偏多' not in s['主要原因']
    assert 'KD 尚未明顯轉空（尚不足以推翻退出判斷）' in s['保留部位理由']


def test_invalid_data_never_reuses_diagnostic_signals_as_current_evidence():
    d = decision(data_valid=False, institutional_level='STRONG_PRESSURE',
                 medium_term_direction=-1, average_cost=80)
    s = sections(d)
    assert not any('法人' in line or '偏空' in line for line in s['主要原因'])
    assert '保留部位理由' not in s
    assert not any('未實現報酬 +' in line for line in s['持倉狀態'])


def test_atr_and_position_management_do_not_displace_follow_up():
    d = decision(atr_percent=9, average_cost=80)
    s = sections(d)
    assert any('ATR' in line for line in s['風險提醒'])
    assert not any('ATR' in line for line in s['後續觀察'])
    assert any('支撐' in line for line in s['後續觀察'])
    assert any('成本不是技術支撐' in line for line in s['持倉狀態'])
    assert any('不因虧損攤平' in line for line in s['風險提醒'])


def test_high_weight_atr_never_displaces_directional_primary_reasons():
    d = decision(current_price=90, support_status='CONFIRMED_BREAK',
                 macd_momentum='bearish_strengthening', institutional_level='BEARISH',
                 relative_market_strength=-3.85, atr_percent=9)
    assert any(item['code'] == 'ATR_RISK_AMPLIFIER' for item in d['trade_evidence']['contributions'])
    before = deepcopy(d)
    s = sections(d)
    assert len(s['主要原因']) == 4
    assert all('ATR' not in text for name, group in s.items() if name != '風險提醒' for text in group)
    assert any('ATR' in text for text in s['風險提醒'])
    assert d == before


def test_neutral_technical_summary_does_not_become_bullish_kd():
    from app.analysis_engine import build_technical_summary
    from app.decision_context import build_decision_context
    from tests.test_decision_context import inputs
    result, bars, prior = inputs()
    result.update(kd_k=55., kd_d=50., kd_j=65., position_status='HOLDING')
    # This adapter fixture has a partial MACD record; let summary use its
    # existing missing-data path while checking the independent KD wording.
    assert 'KD J 65.00（中性）' in build_technical_summary({**result, 'macd_analysis': None})
    context = build_decision_context(result, bars, previous_zones=prior)
    assert context.kd_state == 'BULLISH'  # Existing K>D risk contribution is unchanged.
    d = asdict(DecisionEngine().evaluate(context))
    kd = next(item for item in d['trade_evidence']['contributions'] if item['code'] == 'KD')
    assert kd['points'] == -.25
    assert kd['text'] == 'KD 尚未明顯轉空'
    d['decision'] = 'REDUCE'
    s = sections(d)
    assert 'KD 尚未明顯轉空' in s['保留部位理由']
    assert not any('KD 偏多' in line for group in s.values() for line in group)


def test_legacy_kd_text_is_conservatively_rendered_without_mutation():
    d = decision(current_price=90, support_status='CONFIRMED_BREAK',
                 macd_momentum='bearish_strengthening', institutional_level='BEARISH',
                 relative_market_strength=-3.85)
    next(item for item in d['trade_evidence']['contributions'] if item['code'] == 'KD')['text'] = 'KD 偏多'
    before = deepcopy(d)
    assert 'KD 尚未明顯轉空' in sections(d)['保留部位理由']
    assert before == d


def test_compact_position_text_and_market_conditions_have_no_internal_score():
    d = decision(current_price=497.016, average_cost=432, relative_market_strength=-3.85)
    d['decision'] = 'REDUCE'
    s = sections(d)
    assert s['持倉狀態'] == [
        '平均成本 432，目前未實現報酬 +15.05%，已有獲利緩衝，可優先保護部分獲利',
        '成本不是技術支撐，不作為單獨買賣依據',
    ]
    assert all('門檻' not in text and 'score' not in text for group in s.values() for text in group)
    assert '若支撐確認失守，且動能、法人或中期趨勢同步惡化，再提高減碼或退出程度' in s['後續觀察']
    assert list(s) == ['主要原因', '保留部位理由', '持倉狀態', '後續觀察', '風險提醒']
