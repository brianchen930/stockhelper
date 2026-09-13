"""Audit saved touch/volume evidence against raw historical bars, independently."""
import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd


def same(actual, expected):
    assert pd.isna(actual) if expected is None else np.isclose(actual, expected), (actual, expected)


def average(values, length):
    return float(values.mean()) if len(values) == length and values.notna().all() and values.ge(0).all() else None


def ratio(numerator, denominator):
    return numerator / denominator if numerator is not None and denominator is not None and denominator > 0 else None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    args = parser.parse_args()
    events = pd.read_csv(args.directory / 'support_events.csv')
    data = pd.read_csv(args.directory / 'history.csv', index_col=0, parse_dates=True)
    report = json.loads((args.directory / 'report.json').read_text(encoding='utf-8'))
    config = report['metadata']['config']['evidence']
    period, reaction = config['volume_ma_period'], config['volume_reaction_bars']
    for event in events.itertuples():
        i = event.event_index
        baseline = average(data.Volume.iloc[max(0, i - period):i], period)
        pre = average(data.Volume.iloc[max(0, i - config['pre_volume_bars']):i], config['pre_volume_bars'])
        post = average(data.Volume.iloc[i + 1:i + 1 + reaction], reaction)
        same(event.touch_volume_ma20, baseline)
        same(event.touch_volume_ratio, ratio(float(data.Volume.iloc[i]), baseline))
        same(event.future_volume_ratio, ratio(post, baseline))
        same(event.post_pre_volume_ratio, ratio(post, pre))
        if event.label == 'failure':
            failure = int(event.failure_bar)
            same(event.breakdown_volume, float(data.Volume.iloc[failure]))
            same(event.breakdown_volume_ratio, ratio(float(data.Volume.iloc[failure]),
                average(data.Volume.iloc[max(0, failure - period):failure], period)))
        else:
            assert pd.isna(event.breakdown_volume) and pd.isna(event.breakdown_volume_ratio)
        episodes = json.loads(event.historical_touch_episodes)
        assert len(episodes) == event.historical_touch_count == event.support_touch_count
        assert event.current_touch_number == len(episodes) + 1
        assert len({episode['touch_index'] for episode in episodes}) == len(episodes)
        rebounds, breakdowns = [], []
        for episode in episodes:
            j = episode['touch_index']
            assert j < i
            assert pd.Timestamp(episode['touch_date']) == data.index[j]
            same(episode['touch_close'], float(data.Close.iloc[j]))
            same(episode['touch_volume_ratio'], ratio(float(data.Volume.iloc[j]),
                 average(data.Volume.iloc[max(0, j - period):j], period)))
            if episode['reaction'] is not None:
                end = episode['reaction_end_index']
                assert end < i and end - j == config['reaction_lookahead']
                window = data.iloc[j + 1:end + 1]
                rebound = (float(window.High.max()) - episode['touch_close']) / episode['atr']
                breakdown = (float(window.Low.min()) - episode['touch_close']) / episode['atr']
                same(episode['reaction']['touch_max_rebound_atr'], rebound)
                same(episode['reaction']['touch_max_breakdown_atr'], breakdown)
                rebounds.append(rebound)
                breakdowns.append(breakdown)
        same(event.historical_touch_avg_rebound_atr, float(np.mean(rebounds)) if rebounds else None)
        same(event.historical_touch_avg_breakdown_atr, float(np.mean(breakdowns)) if breakdowns else None)
        assert len(rebounds) == event.historical_touch_reaction_count
    baseline = json.loads(Path('runtime_verification/touch_volume_source_baseline.json').read_text(encoding='utf-8'))
    changed = [name for name, digest in baseline.items() if 'backtest' not in Path(name).parts
               and hashlib.sha256(Path(name).read_bytes()).hexdigest() != digest]
    assert not changed, changed
    for name in ('support_touch_stats', 'support_volume_stats', 'support_volume_ratio_bins', 'support_touch_volume_cross'):
        table = pd.read_csv(args.directory / (name + '.csv'))
        assert table.event_count.sum() == len(events)
        assert (args.directory / (name + '.csv')).read_bytes().startswith(b'\xef\xbb\xbf')
    result = dict(audited_events=len(events), historical_reactions_before_event=True,
                  volume_baselines_and_outcomes_match=True, historical_counts_match=True,
                  live_source_files_unchanged=True, grouping_totals_match=True, csv_utf8_sig=True)
    with (args.directory / 'touch_volume_verification.json').open('x', encoding='utf-8') as file:
        json.dump(result, file, indent=2)
    print(json.dumps(result))


if __name__ == '__main__':
    main()
