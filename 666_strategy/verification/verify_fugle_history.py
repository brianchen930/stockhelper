"""Read-only Fugle feasibility probes. Never used by the strategy or live provider.

Run from the project directory: python -B -X utf8 -m
666_strategy.verification.verify_fugle_history --output <report.json>
FUGLE_API_KEY comes from the environment, then the project-root .env.
Command-line keys are never accepted.
"""
import argparse
from datetime import date, timedelta
import hashlib
import importlib
import json
import math
import os
from pathlib import Path
import re
import ssl
import time
from urllib.error import HTTPError
from urllib.parse import quote, quote_plus, urlencode
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener

import pandas as pd
from dotenv import dotenv_values

from .. import fugle_client as _transport
from ..fugle_client import (BASE, FIELDS, TAIPEI, ERROR_BODY_LIMIT, ProbeError,
                            NoRedirect, safe_api_error, request_params, chunks, normalize)

HERE = Path(__file__).resolve().parent
PROJECT_ROOT = HERE.parent.parent
START, END = date(2025, 10, 30), date(2026, 10, 29)
SMOKE_DAY = '2026-04-21'


def read_api_key(environ=None):
    return _transport.read_api_key(environ, project_root=PROJECT_ROOT)


class FugleProbeClient(_transport.FugleClient):
    def __init__(self, *, environ=None, ca_bundle=None, opener=None, minimum_interval=1.05):
        super().__init__(environ=environ, ca_bundle=ca_bundle, opener=opener,
                         minimum_interval=minimum_interval, key_loader=read_api_key,
                         opener_factory=build_opener)


def diagnostics_plan():
    # First isolate the basic contract, then verify the exact original options.
    return [dict(symbol='2330', timeframe=tf, start=SMOKE_DAY, end=SMOKE_DAY,
                 purpose='MINIMAL_SMOKE' if mode == 'minimal' else 'ORIGINAL_OPTIONS_SMOKE',
                 request_mode=mode)
            for mode in ('minimal', 'original') for tf in ('D', '60')]


def plan():
    data = importlib.import_module('666_strategy.backtest.data')
    window = data.data_window(START, END)
    probes, full = [], []
    for symbol in ('2330', '2408'):
        for day in ('2026-02-02', '2026-04-20'):
            probes.append(dict(symbol=symbol, timeframe='60', start=day, end=day, purpose='YAHOO_GAP'))
        probes.append(dict(symbol=symbol, timeframe='D', start='2025-08-01', end='2025-08-01', purpose='YAHOO_GAP'))
    # Gap probes always run first. Fetch 1m to inspect the auction, never to fill prices.
    for symbol in ('2330', '2408'):
        for day in ('2026-02-03', '2026-04-21'):
            for tf in ('60', 'D', '1'):
                probes.append(dict(symbol=symbol, timeframe=tf, start=day, end=day, purpose='YAHOO_COMPARISON'))
        for day in ('2026-02-02', '2026-04-20'):
            probes.append(dict(symbol=symbol, timeframe='1', start=day, end=day, purpose='AUCTION_INSPECTION'))
        for tf, start in (('60', window.minute_start.date()), ('D', window.daily_start.date())):
            for first, last in chunks(start, END):
                full.append(dict(symbol=symbol, timeframe=tf, start=str(first), end=str(last), purpose='FULL_COVERAGE'))
    return window, probes, full


def load_yahoo(path):
    doc = json.loads(path.read_text(encoding='utf-8'))
    rows = {(item['symbol'].removesuffix('.TW'), item['interval']): item['rows']
            for item in doc['source_snapshots']}
    return rows, hashlib.sha256(path.read_bytes()).hexdigest()


def compare(spec, rows, yahoo):
    if spec['timeframe'] == '1':
        return dict(status='INSPECT_ONLY', native_tail=[row for row in rows
                    if pd.Timestamp(row['bar_start']).hour >= 13])
    interval = '1d' if spec['timeframe'] == 'D' else '60m'
    reference = {pd.Timestamp(row['bar_start']).isoformat(): row
                 for row in yahoo.get((spec['symbol'], interval), [])
                 if spec['start'] <= str(pd.Timestamp(row['bar_start']).date()) <= spec['end']}
    fugle = {row['bar_start']: row for row in rows}
    comparisons = []
    for stamp in sorted(set(reference) | set(fugle)):
        a, b = fugle.get(stamp), reference.get(stamp)
        deltas = {}
        for field in ('Open', 'High', 'Low', 'Close', 'Volume'):
            bv = b.get(field) if b else None
            deltas[field] = (a[field] - float(bv) if a and isinstance(bv, (int, float))
                             and math.isfinite(bv) else None)
        valid = all(value is not None for value in deltas.values())
        comparisons.append(dict(bar_start=stamp, fugle=a, yahoo=b,
            delta_fugle_minus_yahoo=deltas, status=('MATCH' if all(abs(v) < 1e-6 for v in deltas.values())
                else 'DIFFERENT') if valid else 'MISSING_OR_INVALID_REFERENCE'))
    stamps_equal = set(reference) == set(fugle)
    return dict(status='COMPARED', timestamps_equal=stamps_equal, rows=comparisons,
                native_tail=[row for row in rows if pd.Timestamp(row['bar_start']).hour >= 13],
                note='Daily totals include additional trading sessions; do not compare daily volume with minute sums.')


def coverage(window, specs):
    """Only native 60m can pass. Extra 13:30 bars must be resolved before use."""
    cal = importlib.import_module('666_strategy.calendar')
    bt = importlib.import_module('666_strategy.backtest.data')
    reports = []
    for symbol in ('2330', '2408'):
        frames = {}
        for tf in ('60', 'D'):
            relevant = [r for r in specs if r['request']['symbol'] == symbol and r['request']['timeframe'] == tf]
            if not relevant or any(r['status'] != 'FETCHED' for r in relevant):
                frames[tf] = None
                continue
            rows = [row for r in relevant for row in r['rows']]
            index = pd.DatetimeIndex([row['bar_start'] for row in rows])
            if index.has_duplicates:
                frames[tf] = None
                continue
            frames[tf] = pd.DataFrame([{k: row[k] for k in ('Open','High','Low','Close','Volume')}
                                       for row in rows], index=index).sort_index()
        if any(frame is None for frame in frames.values()):
            reports.append(dict(symbol=symbol, status='INCOMPLETE_REQUESTS'))
            continue
        native = frames['60']
        expected = {pd.Timestamp(day, tz=TAIPEI) + pd.Timedelta(hours=hour)
                    for day in window.calendar.sessions if window.minute_start.date() <= day <= END
                    for hour in (9,10,11,12,13)}
        got = set(native.index)
        closed_rows, conflicts = [], []
        for stamp, row in frames['D'].iterrows():
            if stamp.date() not in window.calendar.sessions:
                kind = cal.closed_session_row_kind(row)
                (closed_rows if kind else conflicts).append(dict(at=stamp.isoformat(), kind=kind))
        detail = dict(symbol=symbol, expected_60m=len(expected), returned_60m=len(got),
            missing_60m=sorted(stamp.isoformat() for stamp in expected-got),
            unexpected_60m=sorted(stamp.isoformat() for stamp in got-expected),
            expected_daily=sum(window.daily_start.date() <= day <= END for day in window.calendar.sessions),
            returned_daily=len(frames['D']),
            missing_daily=[str(day) for day in sorted(window.calendar.sessions)
                           if window.daily_start.date() <= day <= END
                           and pd.Timestamp(day, tz=TAIPEI) not in frames['D'].index],
            excluded_confirmed_closed_daily_rows=closed_rows, calendar_conflicts=conflicts)
        if got != expected or conflicts:
            detail['status'] = 'BAR_GRID_OR_CALENDAR_MISMATCH'
        else:
            try:
                bt.prepare_data(native, frames['D'], symbol=symbol+'.TW', start=START, end=END,
                                calendar=window.calendar, now=window.cutoff)
                detail['status'] = 'EXISTING_VALIDATION_PASSED'
            except bt.BacktestDataError as error:
                detail.update(status='EXISTING_VALIDATION_FAILED', error_code=error.code, details=error.details)
        reports.append(detail)
    return reports


def run(*, output, yahoo_source, full_range=False, diagnose_only=False, ca_bundle=None, environ=None, client=None):
    window, probes, full = plan()
    yahoo, digest = load_yahoo(yahoo_source)
    key_error = None
    try:
        key = read_api_key(environ)
    except ProbeError as error:
        key, key_error = '', error
    key_available = bool(key)
    report = dict(schema='fugle_feasibility_v2', generated_at=pd.Timestamp.now(tz=TAIPEI).isoformat(),
        requested_start=str(START), requested_end=str(END), api_key_present=key_available,
        minute_warmup_start=window.minute_start.isoformat(), daily_warmup_start=window.daily_start.isoformat(),
        expected_60m_bars=60+5*len(window.sessions), expected_daily_rows=120+len(window.sessions),
        yahoo_source_sha256=digest, timezone=TAIPEI, normalized_volume_unit='shares',
        diagnostics=[], diagnostics_only=diagnose_only,
        endpoint=BASE+'{symbol}', http_method='GET',
        probes=[], yahoo_baseline=[dict(request=p, rows=[row for row in yahoo.get(
            (p['symbol'], '1d' if p['timeframe']=='D' else '60m'), [])
            if p['start'] <= str(pd.Timestamp(row['bar_start']).date()) <= p['end']])
            for p in probes if p['timeframe'] != '1'],
        full_range_requested=full_range, full_range_plan=full,
        full_range_results=[], annual_coverage=None, production_provider_changed=False)
    if not key_available and key_error is None:
        report['status'] = 'NOT_RUN_MISSING_API_KEY'
        report['diagnostics'] = [dict(request=p, status='NOT_RUN_MISSING_API_KEY') for p in diagnostics_plan()]
        report['probes'] = [dict(request=p, status='NOT_RUN_MISSING_API_KEY') for p in probes]
        if full_range:
            report['full_range_status'] = 'NOT_RUN_MISSING_API_KEY'
    else:
        try:
            if key_error is not None:
                raise key_error
            client = client or FugleProbeClient(environ={'FUGLE_API_KEY': key}, ca_bundle=ca_bundle)
        except ProbeError as error:
            report['status'] = str(error)
            report['diagnostics'] = [dict(request=p, status='NOT_RUN_CLIENT_CONFIGURATION') for p in diagnostics_plan()]
            report['probes'] = [dict(request=p, status='NOT_RUN_CLIENT_CONFIGURATION') for p in probes]
            if full_range:
                report['full_range_status'] = 'NOT_RUN_CLIENT_CONFIGURATION'
        else:
            def fetch(spec):
                diagnostic = dict(request=spec, query_params=request_params(spec))
                try:
                    rows = client.candles(spec)
                    return dict(diagnostic, status='FETCHED', http_status=getattr(client, 'last_http_status', None),
                                rows=rows, comparison=compare(spec, rows, yahoo))
                except ProbeError as error:
                    return dict(diagnostic, status=str(error), http_status=error.http_status or
                                getattr(client, 'last_http_status', None), api_error=error.api_error)

            def fetch_stage(specs, target):
                blocked = False
                for spec in specs:
                    result = dict(request=spec, status='NOT_RUN_PREVIOUS_FAILURE') if blocked else fetch(spec)
                    target.append(result)
                    if result['status'] != 'FETCHED':
                        blocked = True
                return not blocked

            ready = fetch_stage(diagnostics_plan(), report['diagnostics'])
            report['status'] = 'DIAGNOSTICS_PASSED_REVIEW_COMPARISONS' if ready else 'DIAGNOSTIC_FAILURE'
            fetched = False
            if diagnose_only or not ready:
                reason = 'NOT_RUN_DIAGNOSE_ONLY' if diagnose_only and ready else 'NOT_RUN_DIAGNOSTICS_FAILED'
                report['probes'] = [dict(request=p, status=reason) for p in probes]
            else:
                fetched = fetch_stage(probes, report['probes'])
                report['status'] = 'PROBES_FETCHED_REVIEW_COMPARISONS' if fetched else 'PROBE_FAILURE'
            if full_range and fetched and not diagnose_only:
                fetch_stage(full, report['full_range_results'])
                report['annual_coverage'] = coverage(window, report['full_range_results'])
                report['status'] = ('NATIVE_COVERAGE_PASSED_REVIEW_COMPARISONS' if all(
                    r['status'] == 'EXISTING_VALIDATION_PASSED' for r in report['annual_coverage'])
                    else 'ANNUAL_COVERAGE_NOT_VALIDATED')
            elif full_range:
                report['full_range_status'] = 'NOT_RUN_PROBES_FAILED'
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=HERE/'fugle_diagnostic_20261030.json')
    parser.add_argument('--yahoo-source', type=Path, default=HERE/'annual_source_20251030_20261029.json')
    parser.add_argument('--ca-bundle', type=str)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--full-range', action='store_true', help='After diagnostics and all probes fetch successfully, audit the entire year + warmup')
    mode.add_argument('--diagnose-only', action='store_true', help='Only minimal D/60 then original-options D/60; stop at first failure')
    args = parser.parse_args()
    report = run(output=args.output, yahoo_source=args.yahoo_source, full_range=args.full_range,
                 diagnose_only=args.diagnose_only, ca_bundle=args.ca_bundle)
    print(json.dumps(dict(status=report['status'], diagnostics=[
        {k: r[k] for k in ('status', 'http_status', 'api_error') if k in r}
        for r in report['diagnostics']], probes=len(report['probes']), output=str(args.output)), ensure_ascii=False))
    return 0 if report['status'].startswith(('DIAGNOSTICS_PASSED_', 'PROBES_FETCHED_', 'NATIVE_COVERAGE_PASSED_')) else 2


if __name__ == '__main__':
    raise SystemExit(main())
