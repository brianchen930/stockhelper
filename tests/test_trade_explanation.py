from copy import deepcopy
from dataclasses import asdict, replace

import pytest

from app.decision_engine import DecisionEngine
from app.decision_formatter import format_trade_recommendation
from tests.test_trade_actions import BASE
from tests.test_trade_presentation_layers import sections, decision


def entry_path(path='BREAKOUT_ENTRY', *, ready=False, missing=None, blockers=None):
    return dict(path=path, zone=dict(low=109., high=111.),
        status='READY' if ready else 'WAITING_CONFIRMATION', ready=ready,
        missing=[] if ready else (missing if missing is not None else ['尚未完成有效突破確認']),
        risk_blockers=blockers or [], confirmation_count=2 if ready else 0, confirmation_required=2)


def test_hold_reasons_constraints_and_conditional_actions_are_separate():
    d = decision(macd_momentum='bullish_weakening', distance_to_resistance=.5)
    assert d['decision'] == 'HOLD'
    s = sections(d)
    assert list(s) == ['主要原因', '限制因素', '後續觀察']
    assert s['主要原因'] == ['結構防守 98～100仍守穩', '中期趨勢偏多']
    assert any('MACD 動能轉弱' in item and '暫不提高曝險' in item for item in s['限制因素'])
    assert any('接近上方壓力' in item for item in s['限制因素'])
    assert '98.00～100.00 結構防守持續守穩 → 維持持有' in s['後續觀察']
    assert any('確認跌破且中期趨勢轉弱 → 評估減碼' in item for item in s['後續觀察'])
    assert not any('KD' in item or '短期趨勢' in item for item in s['主要原因'])


def test_hold_without_effective_restriction_omits_entire_block():
    d = decision()
    assert d['entry_paths']['pullback']['ready']
    s = sections(d)
    assert '限制因素' not in s
    assert any('→ 評估加碼' in item for item in s['後續觀察'])


def test_non_holder_with_unconfirmed_breakout_explains_observation():
    d = asdict(DecisionEngine().evaluate(BASE))
    d['entry_paths'] = dict(primary_path='breakout', breakout=entry_path(), pullback={})
    s = sections(d)
    assert s['主要原因'] == ['突破型：尚未完成有效突破確認']
    assert '限制因素' not in s  # Do not repeat the same blocker twice.
    assert any('109.00～111.00' in item and '→ 評估進場' in item for item in s['後續觀察'])
    assert not any('持有' in item or '減碼' in item for item in s['後續觀察'])


def test_ready_alternative_is_not_blocked_by_other_path():
    d = decision()
    d['entry_paths'] = dict(primary_path='breakout',
        breakout=entry_path(missing=['尚未完成有效突破確認'], blockers=['OVEREXTENDED']),
        pullback=entry_path('PULLBACK_ENTRY', ready=True))
    s = sections(d)
    assert '限制因素' not in s
    text = '\n'.join(s['後續觀察'])
    assert '回檔型條件' in text
    assert '正乖離' not in text and '壓力突破' not in text


def test_explicit_path_gate_is_a_real_restriction_even_without_missing_fields():
    d = decision()
    d['entry_paths'] = dict(primary_path='pullback', breakout={},
        pullback=entry_path('PULLBACK_ENTRY', missing=[], blockers=['HIGH_VOLATILITY']))
    s = sections(d)
    assert any('高波動限制回檔型進場' in item for item in s['限制因素'])
    assert any('高波動的回檔路徑限制解除' in item for item in s['後續觀察'])


def test_no_invented_macd_cross_or_insufficient_volume():
    d = decision(macd_momentum='bearish_weakening', volume_ratio=.5, volume_state='NORMAL')
    assert d['entry_paths']['pullback']['ready']
    text = '\n'.join(format_trade_recommendation(d))
    assert '尚未正式翻多' not in text
    assert '量能不足' not in text


def test_recovery_does_not_jump_from_reduce_to_hold_on_support_alone():
    d = decision()
    d['decision'] = 'REDUCE'
    d['trade_evidence']['recovery'] = dict(pending=True, count=1, required=2, candidate='HOLD')
    s = sections(d)
    assert '改善尚未完成連續收盤確認' in s['主要原因'][0]
    assert any('1/2' in item and '→ 評估恢復持有' in item for item in s['後續觀察'])
    assert not any('→ 維持持有' in item or '→ 評估加碼' in item for item in s['後續觀察'])


def test_broken_support_is_a_repair_condition_not_an_intact_defense():
    d = decision(current_price=90, support_status='CONFIRMED_BREAK',
        macd_momentum='bearish_strengthening', institutional_level='BEARISH')
    assert d['decision'] == 'OBSERVE'  # Medium trend is still bullish.
    s = sections(d)
    assert any('結構防守失守後未能站回' in item for item in s['後續觀察'])
    assert not any('→ 維持持有' in item for item in s['後續觀察'])


def test_invalid_data_has_only_concrete_repair_conditions():
    d = decision(observation_complete=False, observation_status='INVALID', observation_time='invalid')
    s = sections(d)
    assert '限制因素' not in s
    assert all('→' in item for item in s['後續觀察'])
    assert any('日期有效' in item for item in s['後續觀察'])
    assert not any('加碼' in item or '減碼' in item for item in s['後續觀察'])


def test_absent_evidence_does_not_pad_sections():
    payload = dict(decision='OBSERVE', trade_evidence={'checks': {'valid': True}})
    assert sections(payload) == {}
    assert '泛用' not in '\n'.join(format_trade_recommendation(payload))


@pytest.mark.parametrize('changes', [{}, {'macd_momentum': 'bullish_weakening'},
    {'data_valid': False}, {'current_price': 90, 'support_status': 'CONFIRMED_BREAK',
                          'medium_term_direction': -1, 'macd_momentum': 'bearish_strengthening'}])
def test_render_is_pure_and_all_follow_up_lines_are_condition_action(changes):
    d = decision(**changes)
    before = deepcopy(d)
    s = sections(d)
    assert d == before
    assert set(s) <= {'主要原因', '限制因素', '後續觀察'}
    assert all(' → ' in item for item in s.get('後續觀察', []))
