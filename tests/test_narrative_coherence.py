from copy import deepcopy
import json
import pytest
from test_price_context import baseline, sr
from app.analysis.price_context import enrich_price_context


@pytest.mark.parametrize('short,medium,phrase', [
    (3, 4, '同步偏多'), (-3, 4, '波段上升中的整理'),
    (3, -4, '弱勢結構中的修復'), (-3, -4, '同步偏弱'),
    (0, 0, '整理階段'), (0, 4, '中期多頭架構'),
    (0, -4, '中期結構仍偏弱'), (3, 0, '中期方向仍未確認'),
    (-3, 0, '中期則仍在整理'),
])
@pytest.mark.parametrize('active', [True, False])
def test_narrative_roles_and_unchanged_nontext(short, medium, phrase, active):
    original = baseline(short, medium)
    data = sr(active, True, True)
    before, zones_before = deepcopy(original), deepcopy(data)
    result = enrich_price_context(original, data)
    assert phrase in result['overall_summary']
    assert '若' in result['overall_summary']
    assert ('失守' if active or short < 0 else '突破失敗') in result['overall_summary']
    for key in ('short_term', 'medium_term'):
        assert {k: v for k, v in result[key].items() if k != 'summary'} == {
            k: v for k, v in original[key].items() if k != 'summary'}
    narrative = json.dumps(result, ensure_ascii=False)
    for price in ('99.00～101.00', '95.00～97.00', '102.00～103.00'):
        assert narrative.count(price) <= 1
        assert price not in result['short_term']['summary'] + result['medium_term']['summary']
        assert price not in json.dumps(result['operation_reference'], ensure_ascii=False)
    assert result['operation_reference']['observation_conditions'] == original['operation_reference']['observation_conditions']
    assert original == before and data == zones_before


def test_extra_warning_survives_only_duplicate_is_removed():
    original = baseline(-3, 4)
    original['overall_warnings'].extend(['高檔乖離過大', '成交量異常', '指標背離'])
    result = enrich_price_context(original, sr(True, True, True))
    assert result['overall_warnings'] == ['高檔乖離過大', '成交量異常', '指標背離']


def test_no_context_preserves_original_wording_exactly():
    for a, b in [(3,4), (-3,4), (3,-4), (-3,-4), (0,0)]:
        original = baseline(a,b)
        assert enrich_price_context(original, sr(False, False, False)) == original
