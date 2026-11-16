"""Offline source replay and all-prefix crossing audit; no downloads or fills."""
import argparse
import csv
from datetime import datetime
import hashlib
import importlib
import json
import math
from pathlib import Path
import sys

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
strategy = importlib.import_module('666_strategy')
calendar_module = importlib.import_module('666_strategy.calendar')


def source_frames(snapshot):
    frames = {}
    for source in snapshot['source_snapshots']:
        frame = pd.DataFrame(source['rows']).set_index('bar_start')
        frame.index = pd.DatetimeIndex(pd.to_datetime(frame.index, utc=True)).tz_convert('Asia/Taipei')
        frames[(source['symbol'], source['interval'])] = frame
    return frames


class OfflineProvider:
    def __init__(self, frames):
        self.frames = frames

    def history(self, symbol, *, interval, **options):
        return self.frames[(symbol, interval)].copy(deep=True)


def independent_crossings(history):
    """Scalar specification, independent of the signal layer's shift masks."""
    bars, events, pairs = history.bars, [], 0
    for i in range(1, len(bars)):
        before, current = bars.iloc[i-1], bars.iloc[i]
        if not all(bool(row.is_valid) and bool(row.ma60_valid) for row in (before, current)):
            continue
        pairs += 1
        kind = None
        if float(before.Close) <= float(before.MA60) and float(current.Close) > float(current.MA60):
            kind = 'BUY'
        elif float(before.Close) >= float(before.MA60) and float(current.Close) < float(current.MA60):
            kind = 'SELL'
        if kind:
            events.append(dict(symbol=history.symbol, bar_start=bars.index[i].isoformat(),
                bar_end=current.bar_end.isoformat(), signal_time=current.bar_end.isoformat(),
                close=float(current.Close), ma60=float(current.MA60), signal_type=kind))
    return events, pairs


def audit(path):
    path = Path(path)
    snapshot = json.loads(path.read_text(encoding='utf-8'))
    frames = source_frames(snapshot)
    provider = OfflineProvider(frames)
    reports, all_events = [], []
    for saved in snapshot['results']:
        history = strategy.fetch_history(saved['symbol'], provider=provider,
            as_of=datetime.fromisoformat(snapshot['as_of']),
            lookback_days=saved['metadata']['lookback_days'],
            include_short_bar=saved['metadata']['include_short_session_bar'])
        assert history.status == 'OK', history.issues
        result = strategy.generate_signals(history)
        expected, pair_count = independent_crossings(history)
        assert result.signals == expected == saved['signal_analysis']['signals']
        assert strategy.HistoryResult.records(result.bars) == saved['bars']
        ma_checked = 0
        for i, row in enumerate(history.bars.itertuples()):
            if row.ma60_valid:
                window = history.bars.iloc[i-59:i+1]
                assert len(window) == 60 and window.is_valid.all()
                mean = math.fsum(float(v) for v in window.Close) / 60
                assert math.isclose(row.MA60, mean, rel_tol=1e-12, abs_tol=1e-10)
                ma_checked += 1
        start = pd.Timestamp(history.metadata['request_start'])
        cutoff = pd.Timestamp(history.metadata['as_of'])
        calendar, _ = calendar_module.bundled_calendar(start.date(), cutoff.date())
        raw = frames[(history.symbol, '60m')]
        prefix_checks = 0
        for i, end in enumerate(history.bars.bar_end):
            prefix = strategy.analyze_history(raw, symbol=history.symbol, calendar=calendar,
                request_start=start, as_of=end,
                include_short_bar=history.metadata['include_short_session_bar'])
            prefix_signals = strategy.generate_signals(prefix)
            expected_prefix = [s for s in expected if pd.Timestamp(s['signal_time']) <= end]
            assert prefix_signals.signals == expected_prefix
            pd.testing.assert_series_equal(prefix_signals.bars.signal, result.bars.signal.iloc[:i+1])
            pd.testing.assert_series_equal(prefix_signals.bars.signal_valid, result.bars.signal_valid.iloc[:i+1])
            prefix_checks += 1
        midpoint = history.bars.bar_end.iloc[len(history.bars) // 2]
        poisoned = raw.copy(deep=True)
        poisoned.loc[poisoned.index >= midpoint, ['Open', 'High', 'Low', 'Close', 'Volume']] = float('nan')
        # Even a duplicate future timestamp must not change earlier signals.
        poisoned = pd.concat([poisoned, poisoned.iloc[[-1]]])
        baseline = strategy.generate_signals(strategy.analyze_history(raw, symbol=history.symbol,
            calendar=calendar, request_start=start, as_of=midpoint))
        changed = strategy.generate_signals(strategy.analyze_history(poisoned, symbol=history.symbol,
            calendar=calendar, request_start=start, as_of=midpoint))
        assert changed.signals == baseline.signals
        pd.testing.assert_frame_equal(changed.bars, baseline.bars)
        reports.append(dict(symbol=history.symbol, status=result.status,
            valid_closed_bars=len(history.bars), ma60_values_checked=ma_checked,
            signal_pairs_checked=pair_count, all_prefix_checks=prefix_checks,
            future_poison_check=True, source_replay_identical=True,
            signal_count=len(expected), buy_count=sum(s['signal_type'] == 'BUY' for s in expected),
            sell_count=sum(s['signal_type'] == 'SELL' for s in expected), passed=True))
        all_events.extend(expected)
    return dict(snapshot=str(path), snapshot_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        as_of=snapshot['as_of'], network_used=False, results=reports,
        total_prefix_checks=sum(r['all_prefix_checks'] for r in reports),
        total_signal_pairs_checked=sum(r['signal_pairs_checked'] for r in reports),
        total_ma60_values_checked=sum(r['ma60_values_checked'] for r in reports), passed=True), all_events


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('snapshot', type=Path)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--signals-csv', type=Path)
    args = parser.parse_args()
    report, events = audit(args.snapshot)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    if args.signals_csv:
        args.signals_csv.parent.mkdir(parents=True, exist_ok=True)
        columns = ['symbol', 'bar_start', 'bar_end', 'signal_time', 'close', 'ma60', 'signal_type']
        with args.signals_csv.open('w', encoding='utf-8-sig', newline='') as stream:
            writer = csv.DictWriter(stream, fieldnames=columns)
            writer.writeheader()
            writer.writerows(events)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
