"""Manual verification CLI. Does not start FastAPI, scheduler or notifier."""
import argparse
from datetime import datetime, timedelta
import json
import math
import sys
from pathlib import Path

from .history import HistoryResult, MA_WINDOW, TAIPEI
from .provider import YFinanceProvider, fetch_history
from .signals import generate_signals
from .daily import fetch_daily_trend
from .calendar import bundled_calendar
from .decisions import PositionState, evaluate_strategy


class SnapshotProvider(YFinanceProvider):
    """Keep exact returned rows in the optional verification artifact."""
    def __init__(self, **options):
        super().__init__(**options)
        self.snapshots = []

    def history(self, symbol, **options):
        frame = super().history(symbol, **options)
        self.snapshots.append(dict(symbol=symbol, interval=options['interval'],
            received_at=datetime.now(TAIPEI).isoformat(), rows=HistoryResult.records(frame)))
        return frame


def verify_sma(result):
    """Independent math.fsum reference, rather than another rolling mean."""
    checked = 0
    bars = result.bars
    for i, row in enumerate(bars.itertuples()):
        if not row.ma60_valid:
            continue
        window = bars.iloc[i - MA_WINDOW + 1:i + 1]
        if len(window) != MA_WINDOW or not window.is_valid.all():
            raise AssertionError('A valid MA60 contains missing or invalid bars')
        expected = math.fsum(float(value) for value in window.Close) / MA_WINDOW
        if not math.isclose(float(row.MA60), expected, rel_tol=1e-12, abs_tol=1e-10):
            raise AssertionError(f'MA60 mismatch at {row.Index}')
        checked += 1
    return dict(method='math.fsum(last 60 validated closes) / 60', checked_values=checked,
                passed=checked > 0)


def main(argv=None):
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments and arguments[0] == 'backtest':
        from .backtest.__main__ import main as backtest_main
        return backtest_main(arguments[1:])
    parser = argparse.ArgumentParser(description='Yahoo 60m/SMA60 and optional basic crossings; no fills or returns')
    parser.add_argument('symbols', nargs='+')
    parser.add_argument('--source', choices=('yahoo', 'fugle'), default='yahoo')
    parser.add_argument('--source-snapshot', type=Path, help='Offline replay of the selected source')
    parser.add_argument('--as-of', help='Timezone-aware ISO timestamp; default: current Taipei time')
    parser.add_argument('--lookback-days', type=int, default=60)
    parser.add_argument('--exclude-short-bar', action='store_true')
    parser.add_argument('--signals', action='store_true', help='List all closed-bar BUY/SELL crossings and annotate bars')
    parser.add_argument('--decisions', action='store_true', help='Include stock daily trend and latest position decision; implies --signals')
    parser.add_argument('--holding', action='store_true', help='With --decisions, evaluate each symbol as an existing holding (default: flat)')
    parser.add_argument('--ca-bundle', type=Path, help='Optional trusted CA PEM; TLS verification stays enabled')
    parser.add_argument('--cache-dir', type=Path, default=Path(__file__).resolve().parent / '.cache')
    parser.add_argument('--output', type=Path, help='Optional full JSON snapshot, including quality and OHLCV')
    args = parser.parse_args(arguments)
    if args.holding and not args.decisions:
        parser.error('--holding requires --decisions')
    cutoff = datetime.fromisoformat(args.as_of) if args.as_of else datetime.now(TAIPEI)
    session = None
    try:
        if args.source == 'fugle':
            from .fugle_provider import FugleProvider, FugleSnapshotProvider
            provider = (FugleSnapshotProvider(args.source_snapshot) if args.source_snapshot else
                        FugleProvider(ca_bundle=args.ca_bundle))
        elif args.source_snapshot:
            from .backtest.data import SnapshotProvider as SavedYahooProvider
            provider = SavedYahooProvider(args.source_snapshot)
            provider.snapshots = json.loads(args.source_snapshot.read_text(encoding='utf-8'))['source_snapshots']
        else:
            provider = None
        if provider is None and args.ca_bundle:
            if not args.ca_bundle.is_file():
                parser.error('--ca-bundle must be an existing trusted PEM file')
            from curl_cffi import requests
            # Preserve relative ASCII paths: some Windows libcurl builds cannot
            # open absolute CA paths containing non-ASCII directory names.
            session = requests.Session(impersonate='chrome', verify=str(args.ca_bundle))
        if provider is None:
            import yfinance as yf
            # This CLI process only; original application settings are untouched.
            yf.set_tz_cache_location(str(args.cache_dir))
            provider = SnapshotProvider(session=session)
        snapshots, success = [], True
        for symbol in args.symbols:
            result = fetch_history(symbol, as_of=cutoff, lookback_days=args.lookback_days,
                include_short_bar=not args.exclude_short_bar, provider=provider)
            verification = verify_sma(result)
            snapshot = dict(**result.to_dict(), sma_verification=verification)
            signal_result = generate_signals(result) if args.signals or args.decisions else None
            if signal_result is not None:
                analysis = signal_result.to_dict()
                snapshot['bars'] = analysis.pop('bars')
                snapshot['recent_10'] = analysis['recent_10']
                snapshot['signal_analysis'] = analysis
            if args.decisions:
                daily = fetch_daily_trend(result.symbol, decision_start=result.metadata['request_start'],
                                         as_of=cutoff, provider=provider)
                if args.source == 'fugle':
                    daily.metadata.update(provider.metadata, interval='1d')
                snapshot['daily_trend'] = daily.to_dict()
                try:
                    execution_calendar, _ = bundled_calendar(daily.calendar.start_date,
                                                            cutoff.date() + timedelta(days=14))
                except ValueError:
                    execution_calendar = daily.calendar
                decision = evaluate_strategy(signal_result, daily,
                    PositionState(result.symbol, holding=args.holding), at=cutoff, calendar=execution_calendar)
                snapshot['decision_analysis'] = decision.to_dict()
                snapshot['decision_scope'] = 'Latest event only; supplied holding flag; no historical fills'
                print('Daily trend:', json.dumps(daily.snapshot_at(cutoff), ensure_ascii=False))
                print('Position decision:', json.dumps(decision.to_dict(), ensure_ascii=False))
                success &= daily.status != 'UNKNOWN'
            snapshots.append(snapshot)
            print(f'\n{result.symbol}: {result.status}; MA60={result.ma60}')
            print(json.dumps(result.metadata, ensure_ascii=False, indent=2))
            if signal_result is not None:
                print(f'All signals ({len(signal_result.signals)}); time is confirmed bar_end:')
                for item in signal_result.signals:
                    print(f"{item['symbol']} {item['signal_time']} {item['signal_type']} "
                          f"Close={item['close']:.8g} MA60={item['ma60']:.10g} "
                          f"bar_start={item['bar_start']}")
                if not signal_result.signals:
                    print('No valid BUY/SELL crossings in available data.')
                print('Recent 10 completed valid bars:')
            recent = signal_result.recent() if signal_result is not None else result.recent()
            if not recent.empty:
                columns = ['bar_end', 'duration_minutes', 'Close', 'MA60']
                if signal_result is not None:
                    columns += ['signal', 'signal_valid', 'signal_issue']
                print(recent[columns].to_string())
            for issue in result.issues[:12]:
                print('ISSUE:', json.dumps(issue, ensure_ascii=False))
            if result.status in ('SOURCE_ERROR', 'CALENDAR_ERROR'):
                for source in provider.snapshots[-2:]:
                    print('Source diagnostics:', json.dumps(dict(symbol=source['symbol'],
                        interval=source['interval'], count=len(source['rows']),
                        recent_rows=source['rows'][-6:]), ensure_ascii=False))
            if len(result.issues) > 12:
                print(f'... {len(result.issues)} issues total; full list in JSON output')
            print('SMA verification:', json.dumps(verification))
            success &= result.status == 'OK' and verification['passed']
            if signal_result is not None:
                print('Signal status:', signal_result.status)
                success &= signal_result.status == 'OK'
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(dict(as_of=cutoff.isoformat(), results=snapshots,
                source_snapshots=provider.snapshots),
                ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
        return 0 if success else 1
    except (ValueError, AssertionError) as error:
        parser.exit(1, f'Validation failed: {error}\n')
    finally:
        if session is not None:
            session.close()


if __name__ == '__main__':
    raise SystemExit(main())
