from dataclasses import replace
import json
import subprocess
import sys

import numpy as np
import pandas as pd
import pytest

from app.backtest.config import SupportEventConfig
from app.backtest.support_event import evaluate_support_event, is_support_touch
from app.backtest.support_backtest import (backtest_support_events, export_support_events,
                                           prepare_history, write_backtest_report)
from app.backtest.support_stats import (summarize_support_events, summarize_by_volatility,
                                       summarize_by_atr_distance)


def history(n=40):
    return pd.DataFrame(dict(Open=102., Low=100., High=104., Close=102., Volume=1000.),
                        index=pd.date_range('2025-01-01', periods=n))


def set_bar(df, i, low, high, close):
    df.loc[df.index[i], ['Open', 'Low', 'High', 'Close']] = [close, low, high, close]


class FixedDetector:
    def __init__(self, drift=False, disappear=False):
        self.calls = []
        self.drift = drift
        self.disappear = disappear

    def detect(self, prefix):
        self.calls.append(prefix.copy())
        shift = 0.03 * (len(prefix) % 3) if self.drift else 0
        return {'as_of': prefix.index[-1].isoformat(), 'support_zones': []
                if self.disappear and len(prefix) % 2 == 0 else [
                    dict(low=98 + shift, high=100 + shift, methods=['swing_low', 'rolling_vwap'],
                         touch_count=2)]}


def run(data, **kwargs):
    return backtest_support_events(data, '2408', config=SupportEventConfig(min_history=15),
                                    detector=kwargs.pop('detector', FixedDetector()), **kwargs)


@pytest.mark.parametrize('first,second,label', [
    ('success', None, 'success'), ('failure', None, 'failure'),
    ('failure', 'success', 'failure'), ('success', 'failure', 'success'),
    (None, None, 'neutral')])
def test_first_hit_labels(first, second, label):
    future = history(10)
    # Frozen entry 100, ATR 4: target 108, breakdown close strictly <96.
    for i, kind in ((1, first), (5, second)):
        if kind == 'success':
            set_bar(future, i, 102, 108, 104)
        if kind == 'failure':
            set_bar(future, i, 94, 100, 95)
    outcome = evaluate_support_event(future, entry_close=100, support_low=98,
                                     atr=4, event_index=20, config=SupportEventConfig())
    assert outcome['label'] == label
    for kind in ('success', 'failure'):
        expected = 2 if first == kind else 6 if second == kind else None
        assert outcome['bars_to_' + kind] == expected
        assert outcome[kind + '_bar'] == (20 + expected if expected else None)


def test_same_bar_policy_and_shadow_not_failure():
    data = history(10)
    set_bar(data, 0, 90, 108, 95)
    args = dict(entry_close=100, support_low=98, atr=4, event_index=0)
    result = evaluate_support_event(data, **args, config=SupportEventConfig())
    assert result['label'] == 'failure' and result['same_bar_ambiguous']
    assert evaluate_support_event(data, **args, config=SupportEventConfig(same_bar_policy='success'))['label'] == 'success'
    set_bar(data, 0, 90, 104, 96)  # Equal to threshold is not a close breakdown.
    result = evaluate_support_event(data, **args, config=SupportEventConfig())
    assert result['label'] == 'neutral' and result['failure_bar'] is None
    assert result['max_breakdown_atr'] == -2


@pytest.mark.parametrize('atr', [None, np.nan, 0, -1, np.inf])
def test_invalid_atr(atr):
    assert evaluate_support_event(history(10), entry_close=100, support_low=98, atr=atr,
                                  event_index=0, config=SupportEventConfig()) is None
    assert not is_support_touch(99, 102, 98, 100, atr, .25)


def test_touch_overlap_proximity_and_gap():
    assert is_support_touch(99, 102, 98, 100, 4, 0)
    assert is_support_touch(101, 103, 98, 100, 4, .25)
    assert not is_support_touch(101.01, 103, 98, 100, 4, .25)
    assert is_support_touch(90, 97, 98, 100, 4, .25)  # New symmetric lower buffer.
    assert not is_support_touch(90, 96.99, 98, 100, 4, .25)


def test_continuous_visit_and_moving_zones_only_one_event():
    result = run(history(), detector=FixedDetector(drift=True, disappear=True))
    assert len(result) == 1
    assert result.iloc[0].event_index == 15
    assert result.iloc[0].support_sources == ['rolling_vwap', 'swing_low']
    assert result.iloc[0].support_touch_count == 0
    assert result.iloc[0].detector_touch_count == 2  # Preserve legacy confirmed-reaction count.


def test_two_exit_closes_allow_new_visit():
    data = history(50)
    set_bar(data, 20, 104, 107, 106)
    set_bar(data, 21, 104, 107, 106)
    result = run(data)
    assert result.event_index.tolist() == [15, 22]


def test_one_exit_bar_or_exact_threshold_does_not_rearm():
    data = history()
    set_bar(data, 20, 104, 107, 106)
    set_bar(data, 21, 102, 105, 104)  # frozen high=100 + entry ATR=4
    assert len(run(data)) == 1


def test_released_visit_not_recreated_on_same_bar():
    data = history(50)
    set_bar(data, 20, 99, 107, 106)
    set_bar(data, 21, 99, 107, 106)
    assert run(data).event_index.tolist() == [15, 22]


def test_tail_incomplete_and_invalid_future_excluded():
    result = run(history(19))
    assert result.empty and result.attrs['skipped']['incomplete_events'] == 1
    data = history()
    data.loc[data.index[20], 'Close'] = np.nan
    result = run(data)
    assert result.empty and result.attrs['skipped']['invalid_future_events'] == 1
    assert len(prepare_history(data)) == len(data)  # Do not bridge over bad bars.


def test_missing_columns_and_flat_zero_atr_are_safe():
    assert run(history().drop(columns='High')).empty
    data = history()
    data[['Low', 'High', 'Close']] = 100
    assert run(data).empty
    assert backtest_support_events(None, '2408').empty


def test_walk_forward_input_and_frozen_features():
    data = history()
    original = data.copy(deep=True)
    detector = FixedDetector()
    result = run(data, detector=detector)
    for i, prefix in enumerate(detector.calls, 15):
        pd.testing.assert_frame_equal(prefix, data.iloc[:i])
        assert prefix.index.max() < data.index[i]
    event = result.iloc[0]
    assert event.support_as_of == data.index[14].isoformat()
    assert event.event_date == data.index[15].isoformat()
    changed = data.copy()
    set_bar(changed, 20, 110, 120, 115)
    other = run(changed).iloc[0]
    feature_columns = list(result.columns)[:list(result.columns).index('future_max_price')]
    for column in feature_columns:
        assert event[column] == other[column], column
    assert event.label == 'neutral' and other.label == 'success'
    pd.testing.assert_frame_equal(data, original)


def test_event_day_high_is_not_a_future_success():
    data = history()
    set_bar(data, 15, 99, 150, 102)
    event = run(data).iloc[0]
    assert event.label == 'neutral' and pd.isna(event.success_bar)
    assert event.future_max_price == 104


def test_horizon_includes_nth_bar_excludes_n_plus_one():
    data = history()
    set_bar(data, 25, 105, 112, 110)
    assert run(data).iloc[0].bars_to_success == 10
    data = history()
    set_bar(data, 26, 105, 112, 110)
    assert run(data).iloc[0].label == 'neutral'


def test_stats_neutral_empty_and_invalid_exclusion():
    events = pd.DataFrame({'label': ['success'] * 65 + ['failure'] * 35 + ['neutral'] * 20 + ['incomplete']})
    summary = summarize_support_events(events)
    assert summary['total_events'] == 120 and summary['excluded_count'] == 1
    assert summary['success_rate'] == 65 / 120
    assert summary['resolved_success_rate'] == .65
    assert summarize_support_events(events.iloc[:0])['success_rate'] is None
    assert summarize_support_events(pd.DataFrame({'label': ['neutral']}))['resolved_success_rate'] is None


def test_group_stats_and_distance_boundaries():
    events = pd.DataFrame(dict(label=['success'] * 7, distance_to_support_atr=[0, .25, .5, 1, 1.01, -1, np.nan],
                               volatility_level=['低波動'] * 4 + ['高波動'] * 3))
    grouped = summarize_by_atr_distance(events).set_index('distance_bucket')
    assert grouped.loc['0–0.25 ATR', 'total_events'] == 2
    assert grouped.loc['(0.25–0.5] ATR', 'total_events'] == 1
    assert grouped.loc['(0.5–1] ATR', 'total_events'] == 1
    assert grouped.loc['>1 ATR', 'total_events'] == 1
    assert grouped.loc['資料不足', 'total_events'] == 2
    assert summarize_by_volatility(events).total_events.sum() == 7


def test_csv_bom_roundtrip_no_overwrite_and_report(tmp_path):
    events = run(history())
    target = export_support_events(events, tmp_path / 'nested' / 'events.csv')
    before = target.read_bytes()
    assert before.startswith(b'\xef\xbb\xbf')
    loaded = pd.read_csv(target, encoding='utf-8-sig')
    assert loaded.iloc[0].volatility_level == events.iloc[0].volatility_level
    assert json.loads(loaded.iloc[0].support_sources) == events.iloc[0].support_sources
    with pytest.raises(FileExistsError):
        export_support_events(events, target)
    assert target.read_bytes() == before
    report = write_backtest_report(events, tmp_path / 'report')
    assert json.loads((report / 'report.json').read_text(encoding='utf-8'))['summary']['total_events'] == 1


def test_debug_default_quiet_and_enabled(capsys):
    run(history())
    assert capsys.readouterr().out == ''
    run(history(), debug=True)
    assert 'Label NEUTRAL' in capsys.readouterr().out


@pytest.mark.parametrize('kwargs', [dict(lookahead_bars=0), dict(exit_bars=True), dict(min_history=-1),
    dict(zone_match_atr=np.nan), dict(success_rebound_atr=0), dict(same_bar_policy='random')])
def test_bad_config(kwargs):
    with pytest.raises(ValueError):
        SupportEventConfig(**kwargs)


def test_bad_indices_rejected():
    with pytest.raises(ValueError):
        run(history().iloc[::-1])
    data = history()
    data.index = [data.index[0]] * len(data)
    with pytest.raises(ValueError):
        run(data)


def test_real_engine_prefix_invariance():
    data = history(95)
    close = 100 + np.sin(np.arange(95) / 3) * 7
    data[['Open', 'Close']] = np.column_stack([close, close])
    data['Low'], data['High'] = close - 2, close + 2
    config = SupportEventConfig(min_history=30)
    full = backtest_support_events(data, '2408', config=config)
    prefix = backtest_support_events(data.iloc[:80], '2408', config=config)
    expected = full.loc[full.event_index + config.lookahead_bars < 80].reset_index(drop=True)
    pd.testing.assert_frame_equal(prefix, expected)


def test_live_import_does_not_load_backtest():
    code = "import sys; import app.stock, app.scheduler; assert not any(k.startswith('app.backtest') for k in sys.modules)"
    subprocess.run([sys.executable, '-c', code], check=True, capture_output=True)


def test_detector_error_is_audited_and_cooldown_survives():
    class BrokenDetector(FixedDetector):
        def detect(self, prefix):
            if len(prefix) == 17:
                raise RuntimeError('test outage')
            return super().detect(prefix)
    events = run(history(), detector=BrokenDetector())
    assert len(events) == 1
    assert events.attrs['skipped']['detector_error_bars'] == 1


def test_sources_change_does_not_create_another_event():
    class ChangingDetector(FixedDetector):
        def detect(self, prefix):
            result = super().detect(prefix)
            result['support_zones'][0]['methods'] = ['swing_low'] if len(prefix) % 2 else ['kmeans']
            return result
    assert len(run(history(), detector=ChangingDetector())) == 1


def test_cli_offline_roundtrip(tmp_path, capsys):
    from app.backtest.support_backtest import main
    data = history(80)
    path = tmp_path / 'history.csv'
    data.to_csv(path, index_label='Date', encoding='utf-8-sig')
    main(['2408', '--input', str(path), '--output', str(tmp_path / 'runs')])
    assert 'Backtest Support Events' in capsys.readouterr().out
    assert len(list((tmp_path / 'runs').glob('*/report.json'))) == 1
