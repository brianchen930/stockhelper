"""Annual availability audit; incomplete inputs produce diagnostics, never P&L."""
import argparse
import csv
import hashlib
import importlib
import json
from pathlib import Path
import sys

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
bt = importlib.import_module('666_strategy.backtest')
calendar_module = importlib.import_module('666_strategy.calendar')
data_module = importlib.import_module('666_strategy.backtest.data')
strategy = importlib.import_module('666_strategy')


def audit(source):
    provider = bt.SnapshotProvider(source)
    start, end = pd.Timestamp('2025-10-30').date(), pd.Timestamp('2026-10-29').date()
    window = data_module.data_window(start, end)
    daily_calendar, calendar_meta = calendar_module.bundled_calendar(window.daily_start.date(), end)
    reference = calendar_module.validate_yahoo_reference(provider.frames['^TWII','1d'], daily_calendar, window.cutoff)
    reports, gap_rows = [], []
    for symbol in ('2330.TW','2408.TW'):
        raw = provider.frames[symbol, '60m']
        history = strategy.analyze_history(raw, symbol=symbol, calendar=window.calendar,
            request_start=window.minute_start, as_of=window.cutoff)
        daily = strategy.analyze_daily_trend(provider.frames[symbol,'1d'], symbol=symbol,
            calendar=window.calendar, request_start=window.daily_start, as_of=window.cutoff)
        first_open=pd.Timestamp(window.sessions[0],tz='Asia/Taipei')+pd.Timedelta(hours=9)
        first_known=next((stamp.isoformat() for stamp in daily.bars.daily_close_time
                          if stamp>=first_open and daily.snapshot_at(stamp)['status']!='UNKNOWN'),None)
        missing_daily=[stamp.date().isoformat() for stamp in daily.bars.index[~daily.bars.is_valid]]
        in_range = history.bars.loc[history.bars.index.date >= start]
        warmup = history.bars.loc[history.bars.index.date < start]
        bad = history.bars.loc[~history.bars.is_valid]
        for stamp, row in bad.iterrows():
            gap_rows.append(dict(symbol=symbol, bar_start=stamp.isoformat(), bar_end=row.bar_end.isoformat(),
                issue='INVALID_OHLCV', Open=None if pd.isna(row.Open) else float(row.Open),
                High=None if pd.isna(row.High) else float(row.High), Low=None if pd.isna(row.Low) else float(row.Low),
                Close=None if pd.isna(row.Close) else float(row.Close), Volume=None if pd.isna(row.Volume) else float(row.Volume)))
        monthly = []
        for month in sorted(set(stamp.strftime('%Y-%m') for stamp in in_range.index)):
            subset = in_range.loc[in_range.index.strftime('%Y-%m') == month]
            monthly.append(dict(month=month, expected_bars=len(subset), valid_bars=int(subset.is_valid.sum()),
                                invalid_bars=int((~subset.is_valid).sum())))
        failure = None
        try:
            prepared = bt.fetch_backtest_data(symbol, start, end, provider=provider)
        except bt.BacktestDataError as error:
            failure = dict(code=error.code, message=str(error), details=error.details)
        # This script never calls run_backtest on an invalid or truncated window.
        if failure is None:
            raise AssertionError('Inputs are now complete; run the requested full-year backtest explicitly')
        reports.append(dict(symbol=symbol, status='BLOCKED_INCOMPLETE_DATA', requested_start=str(start), requested_end=str(end),
            expected_run_bars=len(in_range), valid_run_bars=int(in_range.is_valid.sum()),
            invalid_run_bars=int((~in_range.is_valid).sum()), valid_warmup_60m_bars=int(warmup.is_valid.sum()),
            expected_total_60m_bars=len(history.bars), valid_total_60m_bars=int(history.bars.is_valid.sum()),
            daily_closes=len(daily.bars), valid_daily_closes=int(daily.bars.is_valid.sum()), daily_status=daily.status,
            daily_gap_dates=missing_daily, first_open_daily_status=daily.snapshot_at(first_open)['status'],
            first_known_daily_close=first_known,
            daily_issues=daily.issues, gap_dates=sorted(set(stamp.date().isoformat() for stamp in bad.index)),
            issues=history.issues, monthly=monthly, failure=failure,
            annual_metrics=dict(completed_trades=None, win_rate_percent=None, total_net_profit=None,
                total_return_percent=None, max_drawdown_percent=None, average_holding_days=None,
                buy_and_hold_return_percent=None), trades=None))
    return dict(source=provider.metadata, requested_start=str(start), requested_end=str(end),
        period_calendar_days=(end-start).days+1, sessions=len(window.sessions),
        minute_warmup_start=window.minute_start.isoformat(), daily_warmup_start=window.daily_start.isoformat(),
        cutoff=window.cutoff.isoformat(), reference_validation=reference, calendar_metadata=calendar_meta,
        calendar_files={str(Path('666_strategy/calendars')/f'{year}.json'):
            hashlib.sha256((Path('666_strategy/calendars')/f'{year}.json').read_bytes()).hexdigest() for year in (2025,2026)},
        reports=reports, gaps=gap_rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source',type=Path)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--gaps-csv',type=Path,required=True)
    parser.add_argument('--protected-baseline',type=Path)
    args = parser.parse_args()
    result = audit(args.source)
    if args.protected_baseline:
        before = json.loads(args.protected_baseline.read_text(encoding='utf-8'))
        changed = [name for name,digest in before.items() if hashlib.sha256(Path(name).read_bytes()).hexdigest()!=digest]
        assert not changed, changed
        result['protected_files']=dict(checked=len(before),unchanged=True,sha256=before)
    args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
    with args.gaps_csv.open('w',encoding='utf-8-sig',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=list(result['gaps'][0]))
        writer.writeheader()
        writer.writerows(result['gaps'])
    print(json.dumps({key:value for key,value in result.items() if key not in ('reports','gaps','protected_files')},ensure_ascii=False,indent=2))
    for report in result['reports']:
        print(json.dumps({key:report[key] for key in ('symbol','status','expected_run_bars','valid_run_bars',
            'invalid_run_bars','valid_warmup_60m_bars','valid_daily_closes','gap_dates')},ensure_ascii=False,indent=2))


if __name__ == '__main__':
    main()
