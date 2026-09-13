"""Independent walk-forward support research. No live application imports this module."""
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import argparse
import json
from pathlib import Path
import re
from time import perf_counter
from uuid import uuid4

import numpy as np
import pandas as pd

from app.support_resistance_analysis import SupportResistanceEngine
from app.volatility import calculate_atr
from .config import SupportEventConfig
from .support_event import (EVENT_COLUMNS, SupportEvent, evaluate_support_event,
                            event_features, finite, is_support_touch, valid_bar,
                            advance_exit_count, zones_match, PREDICTOR_COLUMNS,
                            OUTCOME_COLUMNS, AUDIT_COLUMNS)
from .touch_analysis import TouchTracker
from .volume_analysis import precompute_volume, volume_at, post_event_volume, zone_volume_evidence
from .support_stats import (summarize_support_events, summarize_by_volatility,
                            summarize_by_atr_distance, summarize_touch_statistics,
                            summarize_volume_statistics, summarize_volume_ratio_bins,
                            summarize_touch_volume_cross)


def prepare_history(df: pd.DataFrame | None) -> pd.DataFrame:
    """Preserve every supplied bar, including missing HLC, to keep N-bar windows honest.

    Require a sorted, unique DatetimeIndex and flat columns. Reject ambiguous
    ordering instead of silently sorting/dropping dates. Open/Volume are optional.
    """
    if df is None or df.empty:
        return pd.DataFrame(columns=['Open', 'High', 'Low', 'Close', 'Volume'],
                            index=pd.DatetimeIndex([]))
    if (not isinstance(df.index, pd.DatetimeIndex) or df.index.hasnans
            or not df.index.is_unique or not df.index.is_monotonic_increasing):
        raise ValueError('Backtest requires a sorted, unique DatetimeIndex without NaT')
    if isinstance(df.columns, pd.MultiIndex) or not df.columns.is_unique:
        raise ValueError('Backtest requires unique, single-level OHLCV columns')
    data = df.copy(deep=True)
    for column in ('Open', 'High', 'Low', 'Close', 'Volume'):
        values = data[column] if column in data else pd.Series(np.nan, index=data.index)
        data[column] = values.map(finite).astype(float)
    return data


@dataclass
class _Visit:
    low: float
    high: float
    atr: float
    exit_count: int = 0

    def matches(self, low, high, tolerance):
        # Compare to frozen bounds, not yesterday's moving bounds, so matching
        # cannot drift transitively across the entire price axis.
        return zones_match(low, high, self.low, self.high, self.atr, tolerance)


def backtest_support_events(df, symbol, *, config=None, detector=None,
                            timeframe='1d', debug=False) -> pd.DataFrame:
    """Detect at t-1, test at t, label using t+1..t+N; download nothing.

    detector may be injected for verification/optimization and must implement
    detect(prefix) -> the existing engine result shape. Outcome evaluation never
    controls visit cooldown. Disappearing/reclassified zones remain suppressed
    until two consecutive closes strictly above their frozen exit threshold.
    """
    started = perf_counter()
    config = config or SupportEventConfig()
    detector = detector or SupportResistanceEngine()
    data = prepare_history(df)
    atr_values = calculate_atr(data).to_numpy()
    evidence_started = perf_counter()
    volume_data = precompute_volume(data, config.evidence)
    touch_tracker = TouchTracker(data, atr_values, volume_data, config)
    evidence_seconds = perf_counter() - evidence_started
    valid = np.array([valid_bar(*row) for row in data[['Low', 'High', 'Close']]
                      .itertuples(index=False, name=None)], dtype=bool)
    visits, records = [], []
    skipped, warnings = Counter(), Counter()
    for i in range(config.min_history, len(data)):
        row = data.iloc[i]
        # Keep a released visit through this bar's detection, so re-entry is
        # possible starting on the NEXT bar, not on the second exit bar itself.
        for visit in visits:
            visit.exit_count = advance_exit_count(row.Close if valid[i] else None,
                                                  visit.low, visit.high, visit.atr,
                                                  config.exit_atr, visit.exit_count)
        try:
            snapshot = detector.detect(data.iloc[:i].copy())
        except Exception as error:
            skipped['detector_error_bars'] += 1
            if debug:
                print(f'[{data.index[i].isoformat()}] detector unavailable: {type(error).__name__}: {error}')
            visits = [v for v in visits if v.exit_count < config.exit_bars]
            evidence_started = perf_counter()
            touch_tracker.observe(i, [])
            evidence_seconds += perf_counter() - evidence_started
            continue
        warnings.update(snapshot.get('warnings') or [])
        stale = snapshot.get('as_of') is not None and pd.Timestamp(snapshot['as_of']) != data.index[i - 1]
        if snapshot.get('error') or stale or any('unavailable' in w for w in snapshot.get('warnings', [])):
            skipped['unavailable_snapshot_bars'] += 1
            zones = []
        elif not valid[i] or finite(atr_values[i]) is None or atr_values[i] <= 0:
            skipped['invalid_entry_or_atr_bars'] += 1
            zones = []
        else:
            zones = snapshot.get('support_zones') or []
        evidence_started = perf_counter()
        touch_mapping = touch_tracker.observe(i, zones)
        evidence_seconds += perf_counter() - evidence_started
        for zone in zones:
            low, high = finite(zone.get('low')), finite(zone.get('high'))
            if low is None or high is None or not 0 < low <= high:
                skipped['invalid_zones'] += 1
                continue
            if any(visit.matches(low, high, config.zone_match_atr) for visit in visits):
                continue
            atr = float(atr_values[i])
            if not is_support_touch(row.Low, row.High, low, high, atr, config.touch_atr_threshold):
                continue
            # Start the visit even if its outcome window will be excluded.
            visits.append(_Visit(low, high, atr))
            future = data.iloc[i + 1:i + 1 + config.lookahead_bars]
            if len(future) < config.lookahead_bars:
                skipped['incomplete_events'] += 1
                continue
            outcome = evaluate_support_event(future, entry_close=row.Close, support_low=low,
                                             atr=atr, event_index=i, config=config)
            if outcome is None:
                skipped['invalid_future_events'] += 1
                continue
            features = event_features(symbol, data, i, zone, atr, timeframe)
            features['label_available_date'] = data.index[i + config.lookahead_bars].isoformat()
            evidence_started = perf_counter()
            features['detector_touch_count'] = features['support_touch_count']
            features.update(touch_tracker.features(touch_mapping[(low, high)], i))
            features.update(touch_close=float(row.Close), touch_low=float(row.Low), touch_high=float(row.High))
            features.update(volume_at(volume_data, i, config.evidence))
            features.update(zone_volume_evidence(snapshot.get('volume_profile'), low, high))
            features.update(post_event_volume(data, volume_data, i, atr, outcome['label'],
                                              outcome['failure_bar'], config.evidence))
            evidence_seconds += perf_counter() - evidence_started
            event = SupportEvent(**features, **outcome)
            records.append(event.to_dict())
            if debug:
                print(f'[{event.event_date}] Support {low:g}–{high:g}; Touch Close {row.Close:g}; '
                      f'ATR {atr:.4f}; Future success day {event.bars_to_success}, '
                      f'failure day {event.bars_to_failure}; Label {event.label.upper()}')
                print(f'Historical Touch Count {event.historical_touch_count}; Current Touch #{event.current_touch_number}; '
                      f'Touch Trend {event.touch_rebound_trend}; Touch Volume {event.touch_volume}; '
                      f'20D Avg {event.touch_volume_ma20}; Volume Ratio {event.touch_volume_ratio}; '
                      f'Volume Level {event.touch_volume_level}')
                print('Previous Touch Rebounds:', [e['reaction']['touch_max_rebound_atr']
                      for e in event.historical_touch_episodes if e['reaction'] is not None])
        visits = [v for v in visits if v.exit_count < config.exit_bars]
    result = pd.DataFrame(records, columns=EVENT_COLUMNS)
    for column in ('event_index', 'support_touch_count', 'support_source_count', 'success_bar',
                   'failure_bar', 'bars_to_success', 'bars_to_failure', 'detector_touch_count',
                   'historical_touch_count', 'current_touch_number', 'bars_since_last_touch',
                   'support_age_bars', 'historical_touch_reaction_count',
                   'historical_touch_success_count', 'historical_touch_failure_count'):
        result[column] = pd.array(result[column], dtype='Int64')
    result.attrs.update(symbol=str(symbol), timeframe=timeframe, config=asdict(config),
                        detector=type(detector).__name__,
                        detector_config=asdict(detector.config) if hasattr(detector, 'config') else None,
                        bar_count=len(data), start=None if data.empty else data.index[0].isoformat(),
                        end=None if data.empty else data.index[-1].isoformat(),
                        skipped=dict(skipped), detector_warnings=dict(warnings),
                        elapsed_seconds=perf_counter() - started, schema_version=3,
                        evidence_seconds=evidence_seconds,
                        feature_groups=dict(predictor=PREDICTOR_COLUMNS, outcome=OUTCOME_COLUMNS, audit=AUDIT_COLUMNS),
                        touch_history_basis='observed zones since walk-forward start; no retrospective backfill',
                        historical_reaction_cutoff='strictly before event_date', volume_baseline='previous N bars excluding current',
                        support_cutoff='t-1', atr_cutoff='t', outcome_window='t+1..t+N',
                        return_units='percent_points', rate_units='fraction')
    return result


def export_support_events(events_df, path) -> Path:
    """Exclusive creation: existing data is never overwritten. Sources use JSON."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    output = events_df.copy(deep=True)
    for column in ('support_sources', 'historical_touch_episodes'):
        if column in output:
            output[column] = output[column].map(lambda value: json.dumps(value, ensure_ascii=False, allow_nan=False))
    output.to_csv(path, index=False, encoding='utf-8-sig', mode='x')
    return path


def write_backtest_report(events_df, directory) -> Path:
    """Write events, grouped statistics, definitions and audit counts together."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=False)
    export_support_events(events_df, directory / 'support_events.csv')
    for name, table in (('by_volatility', summarize_by_volatility(events_df)),
                        ('by_atr_distance', summarize_by_atr_distance(events_df)),
                        ('support_touch_stats', summarize_touch_statistics(events_df)),
                        ('support_volume_stats', summarize_volume_statistics(events_df)),
                        ('support_volume_ratio_bins', summarize_volume_ratio_bins(events_df)),
                        ('support_touch_volume_cross', summarize_touch_volume_cross(events_df))):
        table.to_csv(directory / (name + '.csv'), index=False, encoding='utf-8-sig', mode='x')
    report = dict(metadata=events_df.attrs, summary=summarize_support_events(events_df))
    with (directory / 'report.json').open('x', encoding='utf-8') as file:
        json.dump(report, file, ensure_ascii=False, indent=2, allow_nan=False)
    return directory


def print_report(events):
    summary = summarize_support_events(events)
    print('Backtest Support Events')
    print(f"Symbol: {events.attrs.get('symbol')}\nPeriod: {events.attrs.get('start')} ~ {events.attrs.get('end')}")
    for key in ('total_events', 'success_count', 'failure_count', 'neutral_count',
                'success_rate', 'failure_rate', 'neutral_rate', 'resolved_success_rate'):
        value = summary[key]
        display = f'{value:.2%}' if 'rate' in key and value is not None else str(value)
        print(f'{key}: {display}')
    print('Latest 5 events:')
    for event in events.tail(5).itertuples():
        print(f'{event.event_date} Support {event.support_low:.2f}–{event.support_high:.2f} '
              f'ATR {event.atr:.4f} Result {event.label} '
              f'Max Rebound {event.max_rebound_atr:+.2f} ATR '
              f'Max Breakdown {event.max_breakdown_atr:+.2f} ATR')
        print(f'  Touch #{event.current_touch_number}; Historical {event.historical_touch_count}; '
              f'Volume Ratio {event.touch_volume_ratio}; Level {event.touch_volume_level}; Trend {event.touch_rebound_trend}')
    print('Touch Statistics:\n' + summarize_touch_statistics(events).to_string(index=False))
    print('Volume Statistics:\n' + summarize_volume_statistics(events).to_string(index=False))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('symbol')
    parser.add_argument('--input', type=Path, help='Offline CSV with Date, Open, High, Low, Close, Volume')
    parser.add_argument('--period', default='10y')
    parser.add_argument('--lookahead', type=int, default=SupportEventConfig().lookahead_bars)
    parser.add_argument('--output', type=Path, default=Path('data/backtests'))
    parser.add_argument('--debug', action='store_true')
    parser.add_argument('--ca-bundle', type=Path, help='Optional trusted CA PEM bundle for this download only')
    args = parser.parse_args(argv)
    if not re.fullmatch(r'[A-Za-z0-9._-]+', args.symbol):
        parser.error('symbol may contain only letters, digits, dot, underscore and dash')
    config = SupportEventConfig(lookahead_bars=args.lookahead)
    source = str(args.input) if args.input else 'yfinance daily auto_adjust=False'
    if args.input:
        data = pd.read_csv(args.input, index_col=0, parse_dates=True)
    else:
        # Network I/O only here, before the loop. TW -> TWO is a market fallback.
        import yfinance as yf
        yf.set_tz_cache_location(str(args.output / '.yfinance_cache'))
        ticker_options = {}
        if args.ca_bundle:
            from curl_cffi import requests
            ticker_options['session'] = requests.Session(impersonate='chrome', verify=str(args.ca_bundle.resolve()))
        symbols = [args.symbol] if '.' in args.symbol else [args.symbol + '.TW', args.symbol + '.TWO']
        data = pd.DataFrame()
        failures = []
        for yahoo_symbol in symbols:
            try:
                data = yf.Ticker(yahoo_symbol, **ticker_options).history(period=args.period, interval='1d', auto_adjust=False)
            except Exception as error:
                failures.append(f'{yahoo_symbol}: {type(error).__name__}: {error}')
                continue
            if not data.empty:
                source += '; symbol=' + yahoo_symbol
                break
        if data.empty and failures:
            parser.error('Historical download failed: ' + '; '.join(failures))
        # Research uses completed daily bars; drop today's bar conservatively.
        if not data.empty:
            today = pd.Timestamp.now(tz='Asia/Taipei').date()
            data = data.loc[data.index.date < today]
    if data.empty:
        parser.error('No historical bars available')
    events = backtest_support_events(data, args.symbol, config=config, debug=args.debug)
    events.attrs.update(source=source, retrieved_at=datetime.now(timezone.utc).isoformat())
    run_name = 'support_events_' + args.symbol + '_' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '_' + uuid4().hex[:8]
    directory = write_backtest_report(events, args.output / run_name)
    data.to_csv(directory / 'history.csv', index_label='Date', encoding='utf-8-sig', mode='x')
    print_report(events)
    print(f"Elapsed: {events.attrs['elapsed_seconds']:.3f}s\nOutput: {directory}")


if __name__ == '__main__':
    main()
