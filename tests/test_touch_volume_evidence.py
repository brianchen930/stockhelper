from dataclasses import replace
import json

import numpy as np
import pandas as pd
import pytest

from app.backtest.config import SupportEventConfig, TouchVolumeConfig
from app.backtest.support_event import (PREDICTOR_COLUMNS, OUTCOME_COLUMNS, AUDIT_COLUMNS,
    EVENT_COLUMNS, is_support_touch, select_predictor_features)
from app.backtest.touch_analysis import TouchTracker, evaluate_touch_reaction, touch_rebound_trend
from app.backtest.volume_analysis import (calculate_volume_ratio, classify_volume_level,
    precompute_volume, volume_at, post_event_volume, rebound_confirmation, zone_volume_evidence)
from app.backtest.support_backtest import backtest_support_events, write_backtest_report
from app.backtest.support_stats import (summarize_touch_statistics, summarize_volume_statistics,
    summarize_volume_ratio_bins, summarize_touch_volume_cross)


def bars(n=70):
    return pd.DataFrame(dict(Open=105., Low=104., High=106., Close=105., Volume=100000.),
                        index=pd.date_range('2025-01-01', periods=n))


def set_bar(data, i, low, high, close):
    data.loc[data.index[i], ['Open', 'Low', 'High', 'Close']] = [close, low, high, close]


def make_tracker(data, config=None):
    config = config or SupportEventConfig(min_history=15)
    return TouchTracker(data, np.full(len(data), 4.), precompute_volume(data, config.evidence), config)


ZONE = dict(low=98., high=100., methods=['swing_low'], touch_count=7)


def test_first_and_third_touch_numbers():
    data = bars()
    for i in (22, 32, 42):
        set_bar(data, i, 99, 102, 100)
    tracker = make_tracker(data)
    features = {}
    for i in range(20, 43):
        mapping = tracker.observe(i, [ZONE])
        if i in (22, 32, 42):
            features[i] = tracker.features(mapping[(98., 100.)], i)
    assert features[22]['historical_touch_count'] == 0
    assert features[22]['current_touch_number'] == 1
    assert features[22]['last_touch_date'] is None
    assert features[22]['historical_touch_avg_rebound_atr'] is None
    assert features[42]['historical_touch_count'] == 2
    assert features[42]['support_touch_count'] == 2
    assert features[42]['current_touch_number'] == 3
    assert features[42]['last_touch_date'] == data.index[32].isoformat()
    assert features[42]['bars_since_last_touch'] == 10
    assert features[42]['support_age_bars'] == 42 - 19
    assert features[42]['historical_touch_reaction_count'] == 2
    assert features[42]['historical_touch_episodes'][0]['touch_close'] == 100
    assert features[42]['historical_touch_episodes'][0]['touch_volume_ratio'] == 1


def test_consecutive_four_bars_and_two_exit_bars():
    data = bars()
    for i in range(22, 26):
        set_bar(data, i, 99, 102, 100)
    # > 100 + .75 * 4, but below the event exit (104).
    set_bar(data, 26, 102, 104, 103.5)
    set_bar(data, 27, 102, 104, 103.5)
    set_bar(data, 28, 99, 102, 100)
    tracker = make_tracker(data)
    for i in range(20, 29):
        tracker.observe(i, [ZONE])
    assert [e.index for e in tracker.tracks[0].episodes] == [22, 28]


def test_lower_exit_and_exact_exit_threshold():
    data = bars()
    for i in range(22, 31):
        set_bar(data, i, 99, 102, 100)
    set_bar(data, 23, 102, 104, 103)  # Exact .75 ATR does not release.
    set_bar(data, 24, 102, 104, 103)
    set_bar(data, 27, 92, 95, 94)  # Below low - .75 ATR for two bars.
    set_bar(data, 28, 92, 95, 94)
    tracker = make_tracker(data)
    for i in range(20, 31):
        tracker.observe(i, [ZONE])
    assert [e.index for e in tracker.tracks[0].episodes] == [22, 29]


def test_tracker_rejects_skipped_or_repeated_bars():
    tracker = make_tracker(bars())
    tracker.observe(20, [ZONE])
    with pytest.raises(ValueError):
        tracker.observe(20, [ZONE])
    with pytest.raises(ValueError):
        tracker.observe(22, [ZONE])


def test_historical_reaction_must_end_before_event_and_skip_incomplete_pair():
    data = bars()
    set_bar(data, 22, 99, 102, 100)
    set_bar(data, 27, 99, 110, 100)
    tracker = make_tracker(data)
    for i in range(20, 28):
        mapping = tracker.observe(i, [ZONE])
    evidence = tracker.features(mapping[(98., 100.)], 27)
    assert evidence['historical_touch_count'] == 1
    assert evidence['historical_touch_reaction_count'] == 0  # End == today is excluded.
    assert evidence['historical_touch_episodes'][0]['reaction'] is None
    assert evidence['touch_rebound_trend'] == 'insufficient_data'


@pytest.mark.parametrize('previous,recent,expected', [(2, 1, 'weakening'),
    (2, 1.4, 'stable'), (1, 2, 'strengthening'), (2, 2, 'stable'),
    (None, 1, 'insufficient_data'), (0, 1, 'insufficient_data'), (-1, -2, 'insufficient_data')])
def test_trend(previous, recent, expected):
    assert touch_rebound_trend(previous, recent) == expected


def test_touch_reaction_first_hit_uses_low_and_reference_close():
    future = bars(5)
    set_bar(future, 0, 97, 102, 100)  # Low <= close_ref - .5 ATR; close itself not broken.
    result = evaluate_touch_reaction(future, 100, 4, TouchVolumeConfig())
    assert result['touch_label'] == 'failure'
    assert result['failure_offset'] == 1 and result['success_offset'] == 2
    assert result['touch_max_rebound_atr'] == 1.5
    assert result['touch_max_breakdown_atr'] == -.75
    assert evaluate_touch_reaction(future.iloc[:4], 100, 4, TouchVolumeConfig()) is None


def test_volume_ratio_prior_ma_and_zscore():
    data = bars()
    data.loc[data.index[20], 'Volume'] = 150000
    volume = precompute_volume(data)
    result = volume_at(volume, 20)
    assert result['touch_volume_ma20'] == 100000
    assert result['touch_volume_ratio'] == 1.5
    assert result['touch_volume_level'] == 'elevated'
    assert result['touch_volume_zscore'] is None  # Constant previous 20.
    data.loc[data.index[:20], 'Volume'] = np.arange(1, 21) * 1000
    volume = precompute_volume(data)
    expected = (150000 - 10500) / np.std(np.arange(1, 21) * 1000, ddof=0)
    assert volume.volume_zscore.iloc[20] == pytest.approx(expected)


@pytest.mark.parametrize('value', [None, np.nan, np.inf, -1, 'bad'])
def test_invalid_volume(value):
    assert calculate_volume_ratio(value, 100000) is None
    assert calculate_volume_ratio(100000, value) is None
    data = bars()
    data['Volume'] = data.Volume.astype(object)
    data.loc[data.index[20], 'Volume'] = value
    result = volume_at(precompute_volume(data), 20)
    assert result['touch_volume_ratio'] is None
    assert result['touch_volume_level'] is None


def test_zero_volume_distinct_from_missing_and_zero_denominator():
    assert calculate_volume_ratio(0, 100000) == 0
    assert calculate_volume_ratio(100000, 0) is None
    data = bars()
    data['Volume'] = 0
    result = volume_at(precompute_volume(data), 30)
    assert result['touch_volume'] == 0
    assert result['touch_volume_ratio'] is None
    assert result['touch_volume_zscore'] is None


@pytest.mark.parametrize('value,label', [(0, 'low'), (.799, 'low'), (.8, 'normal'),
    (1.199, 'normal'), (1.2, 'elevated'), (1.799, 'elevated'), (1.8, 'spike')])
def test_volume_level_boundaries(value, label):
    assert classify_volume_level(value) == label


def test_volume_prefix_invariance_alignment_and_20_prior_bars():
    data = bars()
    data.Volume = np.arange(len(data)) * 1000.
    result = precompute_volume(data)
    for end in (19, 20, 21, 40, 55):
        pd.testing.assert_frame_equal(result.iloc[:end], precompute_volume(data.iloc[:end]))
    assert result.volume_ma_20.iloc[:20].isna().all()
    assert result.volume_ma_20.iloc[20] == np.mean(data.Volume.iloc[:20])
    assert result.index.equals(data.index)


def test_future_volume_and_failure_bar_baseline():
    data = bars()
    data.loc[data.index[30], 'Volume'] = 999999  # Excluded from pre baselines/post mean.
    data.loc[data.index[31:34], 'Volume'] = [150000, 180000, 120000]
    set_bar(data, 31, 104, 110, 109)
    volume = precompute_volume(data)
    result = post_event_volume(data, volume, 30, 4, 'failure', 33)
    assert result['future_3bar_avg_volume'] == 150000
    assert result['future_volume_ratio'] == 1.5
    assert result['post_pre_volume_ratio'] == 1.5
    assert result['rebound_volume_confirmation'] == 'strong'
    assert result['breakdown_volume'] == 120000
    assert result['breakdown_volume_ma20'] == data.Volume.iloc[13:33].mean()
    assert result['breakdown_volume_ratio'] == pytest.approx(120000 / data.Volume.iloc[13:33].mean())
    success = post_event_volume(data, volume, 30, 4, 'success', 33)
    assert success['breakdown_volume_ratio'] is None
    assert post_event_volume(data.iloc[:33], volume.iloc[:33], 30, 4, 'neutral', None)['future_volume_ratio'] is None


def test_rebound_volume_confirmation():
    assert rebound_confirmation(1, 1.2) == 'strong'
    assert rebound_confirmation(1, .9) == 'weak'
    assert rebound_confirmation(1, 1.1) == 'none'
    assert rebound_confirmation(.99, 3) == 'none'
    assert rebound_confirmation(2, None) is None


class FixedDetector:
    def detect(self, prefix):
        return dict(support_zones=[dict(ZONE)], as_of=prefix.index[-1].isoformat(), volume_profile={})


def run(data):
    return backtest_support_events(data, '2408', detector=FixedDetector(), config=SupportEventConfig(min_history=20))


def test_all_predictors_unchanged_when_future_changes():
    data = bars(80)
    for i in (22, 35, 49):
        set_bar(data, i, 99, 102, 100)
    before = run(data)
    changed = data.copy()
    changed.loc[changed.index[50:], 'Volume'] = 1000000
    changed.loc[changed.index[50:], ['Low', 'High', 'Close']] = [90, 95, 92]
    after = run(changed)
    for index in before.event_index[before.event_index <= 49]:
        left = before.loc[before.event_index == index].reset_index(drop=True)
        right = after.loc[after.event_index == index].reset_index(drop=True)
        pd.testing.assert_frame_equal(select_predictor_features(left), select_predictor_features(right))
        assert left.iloc[0].historical_touch_episodes == right.iloc[0].historical_touch_episodes
    assert before.loc[before.event_index == 49].iloc[0].future_volume_ratio != after.loc[after.event_index == 49].iloc[0].future_volume_ratio
    assert set(PREDICTOR_COLUMNS).isdisjoint(OUTCOME_COLUMNS)
    assert set(PREDICTOR_COLUMNS + OUTCOME_COLUMNS + AUDIT_COLUMNS) == set(EVENT_COLUMNS)


def test_observed_history_not_backfilled_from_later_discovered_zone():
    data = bars()
    for i in (5, 10, 22):
        set_bar(data, i, 99, 102, 100)
    result = run(data)
    assert result.iloc[0].historical_touch_count == 0  # Zone first observed at t=19.
    assert result.iloc[0].detector_touch_count == 7


def test_stats_bins_cross_and_unknown_rows():
    events = pd.DataFrame(dict(current_touch_number=[1, 2, 3, 4, 7, None, 1],
        touch_volume_ratio=[.79, .8, 1, 1.2, 1.5, 2, None],
        touch_volume_level=['low', 'normal', 'normal', 'elevated', 'elevated', 'spike', None],
        label=['success', 'failure', 'neutral', 'success', 'failure', 'neutral', 'invalid']))
    touches = summarize_touch_statistics(events).set_index('touch_number')
    assert touches.loc['#4+', 'event_count'] == 2
    assert touches.loc['#4+', 'resolved_success_rate'] == .5
    assert touches.loc['#1', 'excluded_count'] == 1
    bins = summarize_volume_ratio_bins(events).set_index('volume_ratio_bin')
    for label in ('<0.8', '[0.8,1.0)', '[1.0,1.2)', '[1.2,1.5)', '[1.5,2.0)', '>=2.0'):
        assert bins.loc[label, 'event_count'] == 1
    assert summarize_volume_statistics(events).event_count.sum() == 6
    assert summarize_touch_volume_cross(events).event_count.sum() == 6
    assert summarize_touch_volume_cross(events.iloc[:0]).empty


def test_csv_schema_json_history_and_groups(tmp_path):
    data = bars()
    for i in (22, 35, 49):
        set_bar(data, i, 99, 102, 100)
    events = run(data)
    directory = write_backtest_report(events, tmp_path / 'report')
    loaded = pd.read_csv(directory / 'support_events.csv')
    assert 'touch_volume_zscore' in loaded and 'breakdown_volume_ratio' in loaded
    episodes = json.loads(loaded.iloc[-1].historical_touch_episodes)
    assert len(episodes) == loaded.iloc[-1].historical_touch_count
    assert all(e['reaction_end_index'] < loaded.iloc[-1].event_index for e in episodes if e['reaction'] is not None)
    for path in directory.glob('*.csv'):
        assert path.read_bytes().startswith(b'\xef\xbb\xbf')
    assert (directory / 'support_touch_volume_cross.csv').exists()


def test_zone_volume_share_known_profile():
    result = zone_volume_evidence({'bins': [90, 100, 110], 'volumes': [100, 300]}, 100, 105)
    assert result['support_volume_share'] == .375
    assert result['support_volume_density_ratio'] == 1.5
    assert zone_volume_evidence({}, 100, 105)['support_volume_share'] is None


@pytest.mark.parametrize('kwargs', [dict(volume_ma_period=0), dict(weakening_ratio=1),
    dict(volume_ratio_spike=1), dict(touch_exit_bars=2.5), dict(reaction_lookahead=True)])
def test_config_validation(kwargs):
    with pytest.raises(ValueError):
        TouchVolumeConfig(**kwargs)
