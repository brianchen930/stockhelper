from dataclasses import replace
import json
import math

import numpy as np
import pandas as pd
import pytest

from app.bayesian_support.config import BayesConfig, FORBIDDEN_OUTCOME_COLUMNS, validate_predictor_columns
from app.bayesian_support.features import attach_label_availability, bucket_value, validate_events
from app.bayesian_support.model import BayesianSupportModel, explain_prediction, stable_logistic
from app.bayesian_support.backtest import backtest_bayesian_support
from app.bayesian_support.evaluation import (probability_metrics, calibration_summary, evaluate_predictions,
                                            source_statistics, yearly_summary)


def events(s=100, f=100, neutral=0):
    n = s + f + neutral
    dates = pd.date_range('2020-01-01', periods=n, tz='Asia/Taipei')
    return pd.DataFrame(dict(symbol='2408', timeframe='1d', event_date=dates,
        label_available_date=dates + pd.Timedelta(days=10), support_low=np.arange(n) + 100.,
        support_high=np.arange(n) + 101., label=['success'] * s + ['failure'] * f + ['neutral'] * neutral,
        touch_volume_level='normal', current_touch_number=1, touch_rebound_trend='stable',
        volatility_level='中等波動', support_source_count=2, distance_to_support_atr=.2,
        zone_width_atr=.5, bars_since_last_touch=10))


def example(**kwargs):
    return dict(event_date='2030-01-01', **kwargs)


def single_feature_model(data):
    return BayesianSupportModel(BayesConfig(active_features=('touch_volume_level',))).fit(data)


def test_prior_and_neutral_exclusion():
    model = BayesianSupportModel().fit(events(neutral=20))
    assert model.prior == .5
    assert model.metadata['training_sample_count'] == 200
    assert model.metadata['neutral_count'] == 20
    assert BayesianSupportModel().fit(events(120, 80)).prior == pytest.approx(121 / 202)


def test_smoothing_no_zero_and_lr_two_positive_negative():
    data = events(96, 96)
    data.loc[:58, 'touch_volume_level'] = 'elevated'
    data.loc[96:124, 'touch_volume_level'] = 'elevated'
    model = single_feature_model(data)
    positive = model.predict(example(touch_volume_level='elevated'))
    evidence = positive.evidence_details[0]
    assert evidence['p_given_success'] == .6
    assert evidence['p_given_failure'] == .3
    assert evidence['likelihood_ratio'] == 2
    assert positive.posterior_success_probability == pytest.approx(2 / 3)
    assert model.predict(example(touch_volume_level='normal')).posterior_success_probability < model.prior
    assert model.tables['touch_volume_level']['spike']['p_given_failure'] > 0
    assert model.tables['touch_volume_level']['spike']['low_sample_warning']


def test_neutral_lr_and_all_missing_prior():
    model = single_feature_model(events())
    result = model.predict(example(touch_volume_level='normal'))
    assert result.evidence_details[0]['likelihood_ratio'] == 1
    assert result.posterior_success_probability == .5
    missing = model.predict(example())
    assert missing.posterior_success_probability == model.prior
    assert missing.used_evidence_count == 0 and missing.skipped_evidence_count == 1


@pytest.mark.parametrize('value', [None, np.nan, pd.NA, 'insufficient_data', '資料不足', 'unknown'])
def test_missing_skipped(value):
    model = single_feature_model(events())
    assert model.predict(example(touch_volume_level=value)).used_evidence_count == 0


def test_missing_training_denominator_only_observed_rows():
    data = events(2, 2)
    data.loc[0, 'touch_volume_level'] = None
    table = single_feature_model(data).tables['touch_volume_level']
    assert table['normal']['observed_success_count'] == 1
    assert sum(row['p_given_success'] for row in table.values()) == pytest.approx(1)


@pytest.mark.parametrize('column', sorted(FORBIDDEN_OUTCOME_COLUMNS))
def test_forbidden_columns_raise(column):
    with pytest.raises(ValueError, match='Forbidden outcome'):
        validate_predictor_columns([column])
    with pytest.raises(ValueError):
        BayesConfig(active_features=(column,))


def test_redundant_or_duplicate_evidence_rejected():
    for columns in [('historical_touch_count',), ('touch_volume_ratio',), ('touch_volume_level', 'touch_volume_level')]:
        with pytest.raises(ValueError):
            BayesConfig(active_features=columns)


def test_small_and_empty_model_status():
    model = BayesianSupportModel().fit(events(10, 10))
    result = model.predict(example())
    assert result.model_status == 'insufficient_training_data'
    assert result.confidence_percent is None
    assert '資料不足' in explain_prediction(result)
    empty = BayesianSupportModel().fit(events(0, 0))
    assert empty.predict(example()).posterior_success_probability is None
    predictions = backtest_bayesian_support(events(10, 10))
    assert predictions.model_status.eq('warmup').all()
    assert predictions.predicted_probability.isna().all()


@pytest.mark.parametrize('value', [-10000, -1000, 0, 1000, 10000])
def test_log_odds_stability(value):
    probability = stable_logistic(value)
    assert math.isfinite(probability) and 0 <= probability <= 1


def test_multiple_evidence_log_sum_matches_odds():
    data = events()
    data.loc[:79, ['touch_volume_level', 'touch_rebound_trend']] = ['elevated', 'strengthening']
    model = BayesianSupportModel().fit(data)
    result = model.predict(example(touch_volume_level='elevated', touch_rebound_trend='strengthening'))
    log_odds = math.log(model.prior / (1 - model.prior)) + sum(e['log_likelihood_ratio'] for e in result.evidence_details)
    assert result.posterior_success_probability == pytest.approx(stable_logistic(log_odds))


@pytest.mark.parametrize('feature,value,expected', [
    ('current_touch_number_bucket', 4, '4+'), ('current_touch_number_bucket', 0, None),
    ('distance_to_support_atr_bucket', .25, '<=0.25'), ('distance_to_support_atr_bucket', .5, '(0.25,0.5]'),
    ('distance_to_support_atr_bucket', 1, '(0.5,1]'), ('zone_width_atr_bucket', .5, '[0.5,1)'),
    ('zone_width_atr_bucket', 1.5, '>=1.5'), ('bars_since_last_touch_bucket', 5, '0-5'),
    ('bars_since_last_touch_bucket', 6, '6-20'), ('bars_since_last_touch_bucket', 61, '>60'),
    ('support_age_bars_bucket', 120, '61-120'), ('support_age_bars_bucket', 121, '>120')])
def test_bins(feature, value, expected):
    assert bucket_value(feature, value) == expected


def test_walk_forward_maturity_same_date_and_future_mutation():
    data = events(5, 5, 2)
    data.label = ['success', 'failure'] * 5 + ['neutral', 'neutral']
    data['label_available_date'] = data.event_date + pd.Timedelta(days=2)
    data.loc[7, 'event_date'] = data.event_date.iloc[6]
    config = BayesConfig(min_training_events=2)
    result = backtest_bayesian_support(data, config)
    for prediction in result.itertuples():
        date = pd.to_datetime(prediction.event_date, utc=True)
        known = data.loc[(data.event_date < date) & (data.label_available_date < date) & data.label.isin(['success', 'failure'])]
        assert prediction.training_sample_count == len(known)
        if prediction.model_status == 'ready':
            assert pd.Timestamp(prediction.training_available_until) < date
    same_day = result.loc[result.event_date == result.event_date.iloc[6]]
    assert same_day.training_sample_count.nunique() == 1
    changed = data.copy()
    changed.loc[9:, 'label'] = 'failure'
    changed.loc[9:, 'touch_volume_level'] = 'spike'
    other = backtest_bayesian_support(changed, config)
    pd.testing.assert_frame_equal(result.iloc[:9], other.iloc[:9])
    reversed_result = backtest_bayesian_support(data.iloc[::-1], config)
    pd.testing.assert_frame_equal(result, reversed_result)


def test_full_fit_cannot_predict_its_past():
    model = BayesianSupportModel().fit(events())
    with pytest.raises(ValueError, match='Look-ahead'):
        model.predict(dict(event_date='2020-03-01'))
    with pytest.raises(ValueError):
        model.predict(dict(event_date=pd.NaT))


def test_calendar_maturity_migration_and_mismatch():
    history = pd.DataFrame(index=pd.bdate_range('2020-01-01', periods=30, tz='Asia/Taipei'))
    data = events(1, 0)
    data['event_index'] = 0
    data = data.drop(columns='label_available_date')
    result = attach_label_availability(data, {'2408': history}, 10)
    assert pd.Timestamp(result.label_available_date.iloc[0]) == history.index[10]
    with pytest.raises(ValueError):
        validate_events(data)
    data['event_index'] = 1
    with pytest.raises(ValueError):
        attach_label_availability(data, {'2408': history}, 10)


def test_duplicates_dedup_and_conflicts_rejected():
    data = events(2, 2)
    duplicate = pd.concat([data, data.iloc[:1]], ignore_index=True)
    cleaned = validate_events(duplicate)
    assert len(cleaned) == 4 and cleaned.attrs['duplicate_count'] == 1
    duplicate.loc[4, 'label'] = 'failure'
    with pytest.raises(ValueError, match='Conflicting duplicate'):
        validate_events(duplicate)


def test_model_json_roundtrip_corruption_and_no_overwrite(tmp_path):
    model = BayesianSupportModel().fit(events())
    path = tmp_path / 'model.json'
    model.save(path)
    loaded = BayesianSupportModel.load(path)
    assert model.predict(example(touch_volume_level='normal')).to_dict() == loaded.predict(example(touch_volume_level='normal')).to_dict()
    with pytest.raises(FileExistsError):
        model.save(path)
    stored = json.loads(path.read_text(encoding='utf-8'))
    stored['likelihoods']['touch_volume_level']['normal']['p_given_success'] = 0
    path.write_text(json.dumps(stored), encoding='utf-8')
    with pytest.raises(ValueError):
        BayesianSupportModel.load(path)


def test_metrics_calibration_and_baseline_same_eligible_rows():
    data = pd.DataFrame(dict(event_date=['2025-01-02'] * 4, actual_label=['success', 'failure', 'neutral', 'success'],
        predicted_probability=[1., 0., .4, None], baseline_probability=[.5, .5, .5, None],
        model_status=['ready', 'ready', 'ready', 'warmup']))
    result = evaluate_predictions(data)
    assert result['evaluated_count'] == 2
    assert result['bayesian']['brier_score'] == 0
    assert result['baseline']['brier_score'] == .25
    assert math.isfinite(result['bayesian']['log_loss'])
    calibration = calibration_summary(data)
    assert calibration.prediction_count.sum() == 2
    assert calibration.iloc[-1].actual_success_rate == 1
    assert pd.isna(calibration.iloc[5].actual_success_rate)
    assert yearly_summary(data).iloc[0].event_count == 2


def test_source_families_are_descriptive_only():
    data = events(2, 2)
    data['support_sources'] = ['["swing_low", "rolling_vwap"]', '["volume_profile_poc"]', '["kmeans"]', '[]']
    stats = source_statistics(data)
    assert stats.loc[(stats.kind == 'family') & (stats.source == 'vwap')].iloc[0].success_given_source == 1


def test_live_cached_inference_does_not_fit_or_change_score(tmp_path, monkeypatch):
    from dataclasses import asdict
    from app.backtest.config import SupportEventConfig
    from app.bayesian_support import integration
    from app.support_resistance_analysis import SupportResistanceEngine
    model = BayesianSupportModel().fit(events(), event_definition={
        'event_config': asdict(SupportEventConfig())})
    model.save(tmp_path / '2408.json')
    monkeypatch.setattr(integration, 'MODEL_DIRECTORY', tmp_path)
    integration._load_model.cache_clear()
    def forbidden(*args, **kwargs):
        raise AssertionError('Live inference must never fit')
    monkeypatch.setattr(BayesianSupportModel, 'fit', forbidden)
    monkeypatch.setattr(SupportResistanceEngine, 'detect', lambda self, data: {
        'support_zones': [dict(low=99, high=101, methods=['swing_low', 'rolling_vwap'])]})
    history = pd.DataFrame(dict(Low=99., High=102., Close=100., Volume=100.),
                           index=pd.date_range('2021-01-01', periods=25, tz='Asia/Taipei'))
    for _ in range(2):
        result = dict(stock_code='2408', atr=2., volatility_level='中等波動', score=17,
                      support_resistance={}, support_resistance_text='原有分析')
        integration.attach_bayesian_support(result, history)
        prediction = result['support_resistance']['bayesian_support'][0]['result']
        assert prediction['used_evidence_count'] == 5
        assert prediction['skipped_evidence_count'] == 3
        assert result['score'] == 17
        assert result['support_resistance_text'].startswith('原有分析')
    assert integration._load_model.cache_info().misses == 1
    assert integration._load_model.cache_info().hits == 1
    integration._load_model.cache_clear()
