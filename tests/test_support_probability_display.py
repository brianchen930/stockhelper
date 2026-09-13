from copy import deepcopy
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from app.bayesian_support.rating import (analyze_posterior_distribution, build_rating_reference,
                                        rate_support_probability, validate_thresholds)
from app.bayesian_support.presentation import format_probability_block, zone_overlap_ratio
from app.support_resistance_analysis.formatting import format_support_resistance_output


def predictions(n=100):
    return pd.DataFrame(dict(symbol='2408', timeframe='1d', support_low=100., support_high=101.,
        event_date=pd.date_range('2020-01-02', periods=n, tz='UTC'),
        training_available_until=pd.date_range('2020-01-01', periods=n, tz='UTC'),
        predicted_probability=np.linspace(.1, .9, n), model_status='ready'))


def candidate(p=.46, status='ready'):
    result = dict(posterior_success_probability=p, prior_success_probability=.36,
        model_status=status, training_sample_count=295, used_evidence_count=5, skipped_evidence_count=3,
        evidence_details=[dict(feature='touch_volume_level', bucket='low', likelihood_ratio=1.49,
                               direction='positive', bucket_count=30)], skipped_evidence=['touch_rebound_trend'])
    display = rate_support_probability(result, dict(thresholds=[.30,.39,.48,.59], rating_strategy='distribution'))
    return dict(support_low=482.55, support_high=490.53, result=result, display=display.to_dict())


@pytest.mark.parametrize('p,expected', [(.46,'中'),(.65,'極高'),(.25,'極低'),(.30,'低'),(.39,'中'),(.48,'高'),(.59,'極高'),(0,'極低'),(1,'極高')])
def test_rating(p, expected):
    assert candidate(p)['display']['rating'] == expected


@pytest.mark.parametrize('p,status', [(None,'ready'),(np.nan,'ready'),(-.1,'ready'),(1.1,'ready'),(.9,'insufficient_training_data')])
def test_insufficient(p, status):
    assert '支撐成功機率：資料不足' in format_probability_block(candidate(p,status))


def test_distribution_fallback_ties_and_audit():
    assert build_rating_reference(predictions(99))['rating_strategy'] == 'fixed_fallback'
    reference = build_rating_reference(predictions())
    assert reference['rating_strategy'] == 'distribution'
    assert reference['thresholds'] == pytest.approx([.26,.42,.58,.74])
    assert build_rating_reference(predictions(), strategy='fixed')['thresholds'] == [.3,.4,.5,.6]
    data = predictions()
    data.predicted_probability = .46
    assert build_rating_reference(data)['fallback_reason'] == 'degenerate_quantiles'
    data.loc[0,'training_available_until'] = data.event_date.iloc[0]
    with pytest.raises(ValueError, match='in-sample'):
        analyze_posterior_distribution(data)
    with pytest.raises(ValueError, match='Duplicate'):
        analyze_posterior_distribution(pd.concat([predictions(), predictions().iloc[:1]]))


def test_distribution_excludes_warmup_invalid_and_empty():
    data = predictions()
    data.loc[0,'model_status'] = 'warmup'
    data.loc[1,'predicted_probability'] = np.nan
    summary = analyze_posterior_distribution(data)
    assert summary['count'] == 98
    assert all('p' + str(q) in summary for q in (10,20,25,40,50,60,75,80,90))
    assert analyze_posterior_distribution(data.iloc[:0])['mean'] is None


def test_normal_research_and_percent_preserve_payload():
    item = candidate()
    before = deepcopy(item)
    normal = format_probability_block(item)
    assert len(normal.splitlines()) == 3
    for word in ['Prior','Posterior','LR','Evidence','Training','Bayesian','46%','uncertain']:
        assert word not in normal
    assert '中（46%）' in format_probability_block(item, show_percent=True)
    research = format_probability_block(item, research_mode=True)
    for word in ['Prior','Posterior','LR','Evidence','Training','bucket_count','rating', 'distribution','uncalibrated']:
        if word != 'rating':
            assert word in research
    assert item == before


@pytest.mark.parametrize('low,high,merge', [(482.,490.,True),(479.,486.,False)])
def test_merge_only_overlapping_support(low,high,merge):
    item = candidate()
    sr = dict(support_zones=[dict(low=low,high=high,distance_pct=-1.,strength_label='strong',methods=['kmeans'])],
              bayesian_support_selected=item)
    before = deepcopy(sr)
    output = format_support_resistance_output(sr)
    assert ('模型評估支撐' not in output) == merge
    assert output.count('支撐成功機率') == 1
    assert '模型評估支撐' in format_support_resistance_output(sr, research_mode=True)
    assert sr == before
    assert (zone_overlap_ratio(dict(low=low,high=high),dict(low=482.55,high=490.53)) >= .7) == merge


def test_no_merging_resistance_and_invalid_thresholds():
    sr = dict(resistance_zones=[dict(low=482.,high=490.,distance_pct=1.)],bayesian_support_selected=candidate())
    assert '模型評估支撐' in format_support_resistance_output(sr)
    for values in [(0,.4,.5,.6),(.3,.3,.5,.6),(.3,.4,np.nan,.6),(.3,.4,.5)]:
        with pytest.raises(ValueError):
            validate_thresholds(values)


def test_actual_reference():
    root = Path(__file__).resolve().parents[1]
    ref = json.loads((root/'data/models/bayesian_support/2408.rating.json').read_text(encoding='utf-8'))
    assert ref['distribution']['count'] == 230
    assert ref['rating_strategy'] == 'distribution'
