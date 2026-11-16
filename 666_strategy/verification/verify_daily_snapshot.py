"""Offline daily-MA/source audit and isolated decisions; no simulated portfolio."""
import argparse
import csv
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
daily_module = importlib.import_module('666_strategy.daily')


def frame_from_rows(rows):
    frame = pd.DataFrame(rows).set_index('bar_start')
    frame.index = pd.DatetimeIndex(pd.to_datetime(frame.index, utc=True)).tz_convert('Asia/Taipei')
    return frame


def audit(path):
    path = Path(path)
    saved = json.loads(path.read_text(encoding='utf-8'))
    sources = {(item['symbol'], item['interval']): frame_from_rows(item['rows'])
               for item in saved['source_snapshots'] if item['rows']}
    reports = []
    for item in saved['results']:
        symbol = item['symbol']
        start = pd.Timestamp(item['daily_trend']['metadata']['request_start'])
        cutoff = pd.Timestamp(saved['as_of'])
        calendar, calendar_metadata = calendar_module.bundled_calendar(start.date(), cutoff.date())
        execution_calendar, _ = calendar_module.bundled_calendar(start.date(),
                                                               (cutoff + pd.Timedelta(days=14)).date())
        raw = sources[symbol, '1d']
        daily = strategy.analyze_daily_trend(raw, symbol=symbol, calendar=calendar,
                                            request_start=start, as_of=cutoff)
        checked = 0
        for i, row in enumerate(daily.bars.itertuples()):
            for column, window, lag in [('daily_ma60', 60, 0), ('daily_ma120', 120, 0),
                                         ('daily_ma60_5_sessions_ago', 60, 5)]:
                value = getattr(row, column)
                if pd.isna(value):
                    continue
                end = i + 1 - lag
                closes = daily.bars.Close.iloc[end-window:end]
                assert len(closes) == window and closes.notna().all()
                expected = math.fsum(float(v) for v in closes) / window
                assert math.isclose(value, expected, rel_tol=1e-12, abs_tol=1e-10)
                checked += 1
        minute_start = pd.Timestamp(item['metadata']['request_start'])
        history = strategy.analyze_history(sources[symbol, '60m'], symbol=symbol,
            calendar=calendar, request_start=minute_start, as_of=cutoff)
        signals = strategy.generate_signals(history)
        reference_calendar, _ = calendar_module.bundled_calendar(minute_start.date(), cutoff.date())
        reference_check = calendar_module.validate_yahoo_reference(sources['^TWII', '1d'], reference_calendar, cutoff)
        assert signals.signals == item['signal_analysis']['signals']
        reviews, prefix_checks = [], 0
        for event in signals.signals:
            at = pd.Timestamp(event['signal_time'])
            prefix = strategy.analyze_daily_trend(raw, symbol=symbol, calendar=calendar,
                                                request_start=start, as_of=at)
            assert daily.snapshot_at(at) == prefix.snapshot_at(at)
            decision = strategy.evaluate_decision(strategy.PositionState(symbol), at=at,
                daily=daily, calendar=execution_calendar, signal=event)
            reviews.append(dict(raw_signal=event, daily=daily.snapshot_at(at),
                                flat_position_review=decision.to_dict()))
            prefix_checks += 1
        # Validate every expected daily close, even after a source/calendar conflict.
        for stamp in daily.bars.daily_close_time:
            prefix = strategy.analyze_daily_trend(raw, symbol=symbol, calendar=calendar,
                                                request_start=start, as_of=stamp)
            assert prefix.snapshot_at(stamp) == daily.snapshot_at(stamp)
            prefix_checks += 1
        examples = {}
        for stamp in daily.bars.daily_close_time:
            snap = daily.snapshot_at(stamp)
            if snap['status'] in ('BULL', 'NON_BULL'):
                examples[snap['status']] = snap  # latest actual example of each
        examples['LATEST'] = daily.snapshot_at(cutoff)
        exit_example = None
        if 'NON_BULL' in examples:
            at = pd.Timestamp(examples['NON_BULL']['daily_close_time'])
            exit_example = strategy.evaluate_decision(strategy.PositionState(symbol, holding=True),
                at=at, daily=daily, calendar=execution_calendar).to_dict()
        recent = []
        for stamp in daily.bars.loc[daily.bars.is_valid].tail(10).daily_close_time:
            snap = daily.snapshot_at(stamp)
            failed = [key for key in daily_module.CONDITIONS if snap[key] is False]
            snap['explanation'] = (snap['reason'] if snap['status'] == 'UNKNOWN' else
                'ALL_THREE_CONDITIONS_TRUE' if snap['status'] == 'BULL' else 'FAILED: ' + ', '.join(failed))
            recent.append(snap)
        reports.append(dict(symbol=symbol, expected_daily_sessions=len(daily.bars),
            valid_daily_closes=int(daily.bars.is_valid.sum()),
            independent_ma_values_checked=checked, temporal_prefix_checks=prefix_checks,
            original_crossings_unchanged=True, examples=examples, issues=daily.issues,
            isolated_signal_reviews=reviews, isolated_holding_exit_example=exit_example,
            recent_10_valid_sessions=recent, calendar_metadata=calendar_metadata,
            reference_check=reference_check))
    return dict(source_file=path.name, source_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        passed=True, method='Independent math.fsum and point-in-time prefixes; no portfolio replay or P&L',
        reports=reports)


def audit_historical_csv(path):
    """Read a previously saved Yahoo CSV only; never import its original app."""
    path = Path(path)
    raw = pd.read_csv(path, index_col='Date', parse_dates=True)
    raw.index = pd.DatetimeIndex(raw.index)
    start = pd.Timestamp('2026-01-01T00:00:00+08:00')
    cutoff = pd.Timestamp('2026-07-09T13:30:00+08:00')
    raw = raw.loc[(raw.index >= start) & (raw.index <= cutoff), ['Close']].copy()
    calendar, _ = calendar_module.bundled_calendar(start.date(), cutoff.date())
    daily = strategy.analyze_daily_trend(raw, symbol='2408.TW', calendar=calendar,
                                        request_start=start, as_of=cutoff)
    snap = daily.snapshot_at(cutoff)
    assert snap['status'] == 'BULL' and not daily.issues
    means = {}
    for name, count, lag in [('daily_ma60', 60, 0), ('daily_ma120', 120, 0),
                             ('daily_ma60_5_sessions_ago', 60, 5)]:
        end = len(raw) - lag
        values = raw.Close.iloc[end-count:end]
        assert len(values) == count
        means[name] = math.fsum(float(v) for v in values) / count
        assert math.isclose(means[name], snap[name], rel_tol=1e-12, abs_tol=1e-10)
    prefix_checks = 0
    for stamp in daily.bars.daily_close_time:
        prefix = strategy.analyze_daily_trend(raw, symbol='2408.TW', calendar=calendar,
                                            request_start=start, as_of=stamp)
        assert prefix.snapshot_at(stamp) == daily.snapshot_at(stamp)
        prefix_checks += 1
    return dict(source_file=str(path), source_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        note='Previously saved Yahoo daily quotes, unadjusted Close; historical example, not current trend',
        request_start=start.isoformat(), as_of=cutoff.isoformat(), raw_daily_closes=strategy.HistoryResult.records(raw),
        completed_sessions=len(daily.bars), independent_latest_means=means,
        temporal_prefix_checks=prefix_checks, latest=snap, issues=daily.issues)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('snapshot', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--historical-csv', type=Path, help='Optional previously saved 2408 Yahoo daily CSV')
    parser.add_argument('--recent-csv', type=Path, help='Write the latest ten valid daily sessions per stock')
    parser.add_argument('--protected-baseline', type=Path, help='Compare saved SHA256 hashes of untouched files')
    args = parser.parse_args()
    result = audit(args.snapshot)
    if args.historical_csv:
        result['historical_daily_example'] = audit_historical_csv(args.historical_csv)
    if args.protected_baseline:
        before = json.loads(args.protected_baseline.read_text(encoding='utf-8'))
        changed = [name for name, digest in before.items()
                   if hashlib.sha256(Path(name).read_bytes()).hexdigest() != digest]
        assert not changed, changed
        result['protected_files'] = dict(checked=len(before), unchanged=True, sha256=before)
    if args.recent_csv:
        rows = [row for report in result['reports'] for row in report['recent_10_valid_sessions']]
        with args.recent_csv.open('w', encoding='utf-8-sig', newline='') as stream:
            writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    for report in result['reports']:
        print(json.dumps({key: value for key, value in report.items()
                          if key not in ('isolated_signal_reviews', 'isolated_holding_exit_example',
                                         'recent_10_valid_sessions', 'calendar_metadata')},
                         ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
