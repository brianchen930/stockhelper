"""Save real Yahoo full-year inputs and TWSE calendar evidence; never backfill."""
import argparse
from datetime import datetime
import importlib
import json
from pathlib import Path
import sys

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
strategy = importlib.import_module('666_strategy')
history_module = importlib.import_module('666_strategy.history')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ca-bundle', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--calendar-only', action='store_true', help='Append calendar evidence without downloading quotes again')
    parser.add_argument('--probe-source', type=Path, help='Retry every invalid 60m session in a saved annual source; do not merge/fill')
    parser.add_argument('--daily-probes-only', action='store_true', help='Keep existing hourly probes and additionally retry missing daily closes')
    args = parser.parse_args()
    from curl_cffi import requests
    import yfinance as yf
    yf.set_tz_cache_location(str(Path(__file__).resolve().parents[1]/'.cache'))
    cutoff = pd.Timestamp('2026-10-29T13:30:00+08:00')
    end = pd.Timestamp('2026-10-30T00:00:00+08:00')
    output = dict(as_of=cutoff.isoformat(), requested_run_start='2025-10-30', requested_run_end='2026-10-29',
        received_at=datetime.now(history_module.TAIPEI).isoformat(), source_snapshots=[], errors=[], calendar_evidence=[])
    if args.calendar_only:
        output = json.loads(args.output.read_text(encoding='utf-8'))
    with requests.Session(impersonate='chrome', verify=str(args.ca_bundle)) as session:
        provider = strategy.YFinanceProvider(session=session)
        if args.probe_source:
            bt = importlib.import_module('666_strategy.backtest')
            data_module = importlib.import_module('666_strategy.backtest.data')
            saved = bt.SnapshotProvider(args.probe_source)
            window = data_module.data_window(pd.Timestamp('2025-10-30').date(), cutoff.date())
            probes = (json.loads(args.output.read_text(encoding='utf-8'))['probes']
                      if args.daily_probes_only and args.output.is_file() else [])
            for symbol in ('2330.TW', '2408.TW'):
                history = strategy.analyze_history(saved.frames[symbol, '60m'], symbol=symbol,
                    calendar=window.calendar, request_start=window.minute_start, as_of=cutoff)
                for day in ([] if args.daily_probes_only else sorted(set(history.bars.index[~history.bars.is_valid].date))):
                    for interval in ('60m', '1h'):
                        begin = pd.Timestamp(day, tz='Asia/Taipei')
                        probe = dict(symbol=symbol, date=day.isoformat(), interval=interval,
                                     request_start=begin.isoformat(), request_end_exclusive=(begin+pd.Timedelta(days=1)).isoformat())
                        try:
                            frame = provider.history(symbol, start=begin, end=begin+pd.Timedelta(days=1), interval=interval)
                            checked = strategy.analyze_history(frame, symbol=symbol, calendar=window.calendar,
                                request_start=begin+pd.Timedelta(hours=9), as_of=begin+pd.Timedelta(hours=13,minutes=30))
                            probe.update(rows=strategy.HistoryResult.records(frame), status=checked.status,
                                valid_bars=0 if checked.bars.empty else int(checked.bars.is_valid.sum()), issues=checked.issues)
                        except Exception as error:
                            probe.update(error_type=type(error).__name__, error=str(error), valid_bars=0)
                        probes.append(probe)
                        print(json.dumps({key: value for key,value in probe.items() if key not in ('rows','issues')},ensure_ascii=False),flush=True)
                daily = strategy.analyze_daily_trend(saved.frames[symbol,'1d'],symbol=symbol,calendar=window.calendar,
                    request_start=window.daily_start,as_of=cutoff)
                for day in sorted(set(daily.bars.index[~daily.bars.is_valid].date)):
                    begin=pd.Timestamp(day,tz='Asia/Taipei')
                    probe=dict(symbol=symbol,date=day.isoformat(),interval='1d',request_start=begin.isoformat(),
                               request_end_exclusive=(begin+pd.Timedelta(days=1)).isoformat())
                    try:
                        frame=provider.history(symbol,start=begin,end=begin+pd.Timedelta(days=1),interval='1d')
                        checked=strategy.analyze_daily_trend(frame,symbol=symbol,calendar=window.calendar,
                            request_start=begin,as_of=begin+pd.Timedelta(hours=13,minutes=30))
                        probe.update(rows=strategy.HistoryResult.records(frame),
                                     valid_daily_closes=0 if checked.bars.empty else int(checked.bars.is_valid.sum()),issues=checked.issues)
                    except Exception as error:
                        probe.update(error_type=type(error).__name__,error=str(error),valid_daily_closes=0)
                    probes.append(probe)
                    print(json.dumps({key:value for key,value in probe.items() if key not in ('rows','issues')},ensure_ascii=False),flush=True)
            args.output.write_text(json.dumps(dict(source=str(args.probe_source), probes=probes),
                ensure_ascii=False, indent=2, allow_nan=False),encoding='utf-8')
            return 0
        requests_to_make = [] if args.calendar_only else [
                ('2330.TW','60m','2025-10-01'), ('2408.TW','60m','2025-10-01'),
                ('2330.TW','1d','2025-03-01'), ('2408.TW','1d','2025-03-01'),
                ('^TWII','1d','2025-03-01')]
        for symbol, interval, start in requests_to_make:
            beginning = pd.Timestamp(start, tz='Asia/Taipei')
            try:
                frame = provider.history(symbol, start=beginning, end=end, interval=interval)
                rows = strategy.HistoryResult.records(frame)
                output['source_snapshots'].append(dict(symbol=symbol, interval=interval,
                    request_start=beginning.isoformat(), request_end_exclusive=end.isoformat(),
                    received_at=datetime.now(history_module.TAIPEI).isoformat(), rows=rows))
                print(json.dumps(dict(symbol=symbol, interval=interval, rows=len(rows),
                    first=None if not rows else rows[0]['bar_start'],
                    last=None if not rows else rows[-1]['bar_start']), ensure_ascii=False), flush=True)
            except Exception as error:
                issue = dict(symbol=symbol, interval=interval, error_type=type(error).__name__, message=str(error))
                output['errors'].append(issue)
                print(json.dumps(issue,ensure_ascii=False), flush=True)
        for year in (2025, 2026):
            url = f'https://www.twse.com.tw/holidaySchedule/holidaySchedule?queryYear={year}&response=json'
            try:
                response = session.get(url, timeout=30)
                response.raise_for_status()
                body = response.json()
                output['calendar_evidence'].append(dict(url=url, query_year=year, response=body))
                print(json.dumps(dict(calendar_year=year, status=body.get('stat'),
                    title=body.get('title'), keys=list(body)),ensure_ascii=False), flush=True)
            except Exception as error:
                output['calendar_evidence'].append(dict(url=url, query_year=year, error=str(error)))
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2, allow_nan=False),encoding='utf-8')
    return 0 if not output['errors'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
