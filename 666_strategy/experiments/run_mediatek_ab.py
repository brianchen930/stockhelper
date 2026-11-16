"""Fixed 2454 replication of the existing 666 A/B study; no rule changes.

Run from 台股: python -B -X utf8 -m 666_strategy.experiments.run_mediatek_ab --stage capture
Stages: capture (network), run, audit, report (offline).
"""
import argparse
from bisect import bisect_right
from collections import Counter
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import date, datetime
import json
from pathlib import Path

import pandas as pd

from ..backtest import BacktestConfig, export_backtest, fetch_backtest_data, run_backtest
from ..backtest.data import data_window
from ..fugle_client import FugleClient, ProbeError
from ..fugle_provider import FugleProvider, FugleSnapshotProvider, checked_rows, create_fugle_snapshot
from ..history import TAIPEI, OHLCV
from ..verification.analyze_fugle_trades import write_csv
from ..verification.verify_previous_fugle_year import sha, write_json, equity_attribution, open_position_analysis
from .buy_confirmation import run_confirmed_backtest
from . import run_buy_confirmation_ab as ab

PROJECT = Path(__file__).resolve().parents[2]
ROOT = PROJECT/'666_strategy/verification/fugle_ab_2454_20241030_20261029'
PRIOR = ab.ROOT
SNAPSHOT = ROOT/'2454_fugle_history.snapshot.json'
SOURCE = ROOT/'2454_fugle_source.json'
START, END = '2024-10-30', '2026-10-29'
RUNNERS = {'A': run_backtest, 'B': run_confirmed_backtest}


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def result_path(variant, symbol='2454', root=ROOT):
    return root/f'{symbol}_{variant}_20241030_20261029.json'


def protect():
    baseline_path = ROOT/'protected_baseline.json'
    if not baseline_path.exists():
        paths = [p for p in (PROJECT/'666_strategy').rglob('*') if p.is_file()
                 and ROOT not in p.parents and '.cache' not in p.parts and '__pycache__' not in p.parts
                 and p.name not in ('run_mediatek_ab.py', 'report_mediatek_ab.py')]
        paths += list((PROJECT/'app').rglob('*.py')) + list(PROJECT.glob('*.py'))
        write_json(baseline_path, {p.relative_to(PROJECT).as_posix(): sha(p) for p in sorted(set(paths))})
    baseline = read(baseline_path)
    changed = [p for p, h in baseline.items() if not (PROJECT/p).is_file() or sha(PROJECT/p) != h]
    assert not changed, changed
    return dict(existing_files_checked=len(baseline), changed_existing_files=changed)


class RecordingClient:
    """Save allowlisted normalized responses from the unchanged authenticated client."""
    def __init__(self):
        self.client = None
        self.requests = []

    def candles(self, spec):
        path = ROOT/'downloads'/f"{spec['symbol']}_{spec['timeframe']}_{spec['start']}_{spec['end']}.json"
        if path.exists():
            item = read(path)
            assert item['request'] == spec and item['status'] == 'FETCHED'
            origin = 'saved_capture'
        else:
            if self.client is None:
                self.client = FugleClient(ca_bundle=str(PROJECT/'666_strategy/.cache/windows_current_roots.pem'))
            rows = self.client.candles(spec)
            item = dict(request=spec, status='FETCHED', http_status=self.client.last_http_status,
                        received_at=datetime.now(TAIPEI).isoformat(), rows=rows)
            write_json(path, item)
            origin = 'new_fugle_request'
        if spec['timeframe'] in ('60', 'D'):
            checked_rows(item['rows'], symbol='2454.TW', interval='60m' if spec['timeframe']=='60' else '1d',
                         start=spec['start'], end=spec['end'])
        self.requests.append(dict(file=path.name, sha256=sha(path), origin=origin,
                                  request=spec, received_at=item['received_at'], rows=len(item['rows'])))
        print(f"2454 {spec['timeframe']} {spec['start']}..{spec['end']}: {len(item['rows'])} ({origin})", flush=True)
        return item['rows']


def capture():
    protect()
    record = RecordingClient()
    live = FugleProvider(client=record)
    data = fetch_backtest_data('2454', START, END, provider=live)
    assert len(data.window.sessions)==484 and len(data.history.bars)==2480 and len(data.daily.bars)==604
    # Check actual 13:30 auction against native 13:00 bars, never manufacture bars.
    auctions = []
    native = next(s['rows'] for s in live.snapshots if s['interval']=='60m')
    for day in (START, END):
        rows = record.candles(dict(symbol='2454', timeframe='1', start=day, end=day))
        tail = [r for r in rows if pd.Timestamp(r['bar_start']).hour==13]
        assert tail and tail[-1]['bar_start']==day+'T13:30:00+08:00'
        aggregate = dict(Open=tail[0]['Open'], High=max(r['High'] for r in tail),
                         Low=min(r['Low'] for r in tail), Close=tail[-1]['Close'], Volume=sum(r['Volume'] for r in tail))
        bar = next(r for r in native if r['bar_start']==day+'T13:00:00+08:00')
        assert all(aggregate[k]==bar[k] for k in OHLCV)
        auctions.append(dict(date=day, passed=True, native_13_00=bar, auction_13_30=tail[-1]))
    parts = []
    for request in record.requests:
        if request['request']['timeframe']=='1':
            continue
        item = read(ROOT/'downloads'/request['file'])
        parts.append(dict(request=item['request'], status='FETCHED', http_status=item['http_status'],
                          rows=item['rows'], origin=request))
    source = dict(schema='fugle_feasibility_v2', status='NATIVE_COVERAGE_PASSED_REVIEW_COMPARISONS',
        generated_at=datetime.now(TAIPEI).isoformat(), requested_start=START, requested_end=END,
        daily_warmup_start=data.window.daily_start.isoformat(), minute_warmup_start=data.window.minute_start.isoformat(),
        normalized_volume_unit='shares', full_range_results=parts, requests=record.requests, auction_samples=auctions,
        coverage=dict(run_sessions=484, run_60m_bars=2420, validated_60m_bars=2480, validated_daily_bars=604,
            minute_warmup_bars=60, daily_warmup_sessions=120, minute_issues=data.history.issues,
            daily_issues=data.daily.issues, missing_bars=0, invalid_bars=0, synthetic_bars=0),
        calendar_sha256={str(y):sha(PROJECT/f'666_strategy/calendars/{y}.json') for y in (2024,2025,2026)})
    write_json(SOURCE, source)
    create_fugle_snapshot(SOURCE, SNAPSHOT)
    before = SNAPSHOT.read_bytes()
    create_fugle_snapshot(SOURCE, SNAPSHOT)
    assert SNAPSHOT.read_bytes()==before
    FugleSnapshotProvider(SNAPSHOT)
    write_json(ROOT/'data_validation.json',dict(status='PASSED', **source['coverage'],
        source_sha256=sha(SOURCE), snapshot_sha256=sha(SNAPSHOT), deterministic_snapshot=True,
        auctions=auctions, protected=protect()))
    print('2454 COMPLETE: 2480 native 60m + 604 daily; snapshot sealed.', flush=True)


def run():
    protect()
    provider = FugleSnapshotProvider(SNAPSHOT)
    config = BacktestConfig(**read(result_path('A', '2330', PRIOR))['config'])
    assert config.initial_cash==1000000
    for variant, runner in RUNNERS.items():
        data = fetch_backtest_data('2454', START, END, provider=provider)
        result = runner(data, config=config)
        export_backtest(result, result_path(variant))
        print('2454 '+variant+' '+json.dumps(result.summary), flush=True)


def crossing_diagnostics(data, saved):
    """Descriptive fixed horizons; no threshold selection or simulated rule changes."""
    bars = data.history.bars
    sell_indices = sorted(bars.index.get_loc(pd.Timestamp(s['bar_start'])) for s in saved['raw_signals'] if s['signal_type']=='SELL')
    sessions = list(data.window.calendar.sessions)
    rows = []
    for s in saved['raw_signals']:
        if s['signal_type']!='BUY':
            continue
        i = bars.index.get_loc(pd.Timestamp(s['bar_start']))
        k = bisect_right(sell_indices, i)
        j = sell_indices[k] if k<len(sell_indices) else None
        stop = i
        while stop<len(bars) and bars.iloc[stop].Close>bars.iloc[stop].MA60:
            stop += 1
        nextbar = bars.iloc[i+1] if i+1<len(bars) else None
        rows.append(dict(signal_time=s['signal_time'], bar_start=s['bar_start'],
            daily_status=data.daily.snapshot_at(s['signal_time'])['status'],
            next_sell_time=bars.iloc[j].bar_end.isoformat() if j is not None else None,
            bars_to_next_sell=j-i if j is not None else None,
            sessions_to_next_sell=sessions.index(bars.index[j].date())-sessions.index(bars.index[i].date()) if j is not None else None,
            consecutive_above_bars=stop-i, above_run_right_censored=stop==len(bars),
            next_close_above=bool(nextbar.Close>nextbar.MA60) if nextbar is not None else None))
    def stats(items):
        complete = [r for r in items if r['bars_to_next_sell'] is not None]
        return dict(count=len(items), next_sell_observed=len(complete), right_censored=len(items)-len(complete),
            next_close_not_above=sum(r['next_close_above'] is False for r in items),
            next_close_unobserved=sum(r['next_close_above'] is None for r in items),
            sell_within_1_bar=sum(r['bars_to_next_sell']<=1 for r in complete),
            sell_within_3_bars=sum(r['bars_to_next_sell']<=3 for r in complete),
            sell_within_5_bars=sum(r['bars_to_next_sell']<=5 for r in complete),
            sell_within_3_sessions=sum(r['sessions_to_next_sell']<=3 for r in complete),
            median_bars_to_sell=float(pd.Series([r['bars_to_next_sell'] for r in complete]).median()),
            median_consecutive_above_bars=float(pd.Series([r['consecutive_above_bars'] for r in items]).median()))
    return dict(all_BUY=stats(rows), BULL_BUY=stats([r for r in rows if r['daily_status']=='BULL']),
                raw_signal_counts=dict(Counter(s['signal_type'] for s in saved['raw_signals']))), rows


def exposure_regimes(saved):
    """Price changes during actual held/flat spans; descriptive opportunity costs."""
    opens = [r for r in saved['equity_curve'] if r['phase']=='OPEN']
    groups = []
    i = 0
    while i<len(opens):
        held = opens[i]['shares']>0
        j = i+1
        while j<len(opens) and (opens[j]['shares']>0)==held:
            j += 1
        end = opens[j] if j<len(opens) else saved['equity_curve'][-1]
        start = opens[i]
        groups.append(dict(state='HOLDING' if held else 'FLAT', start=start['at'], end=end['at'],
            bars=j-i, start_price=start['mark_price'], end_price=end['mark_price'],
            underlying_price_return_percent=(end['mark_price']/start['mark_price']-1)*100,
            raw_buy_signals=sum(s['signal_type']=='BUY' and start['at']<=s['signal_time']<end['at'] for s in saved['raw_signals'])))
        i = j
    return groups


def replay_check(variant, day):
    """Independent process, unchanged runner; compare real truncated source input."""
    saved = read(result_path(variant))
    provider = FugleSnapshotProvider(SNAPSHOT)
    prefix = RUNNERS[variant](fetch_backtest_data('2454', START, day, provider=provider),
                             config=BacktestConfig(**saved['config'])).to_dict()
    if day==END:
        assert prefix==saved
    else:
        for field, key in [('fills','execution_time'),('trades','exit_time'),('events','at'),
                           ('raw_signals','signal_time'),('equity_curve','at')]:
            assert prefix[field]==[r for r in saved[field] if r[key][:10]<=day], (variant,day,field)
    return variant,day


def audit():
    protect()
    provider = FugleSnapshotProvider(SNAPSHOT)
    data = fetch_backtest_data('2454', START, END, provider=provider)
    data.history.bars.to_csv(ROOT/'2454_60m_validated.csv',encoding='utf-8-sig')
    data.daily.bars.to_csv(ROOT/'2454_daily_validated.csv',encoding='utf-8-sig')
    a, b = [read(result_path(v)) for v in ('A','B')]
    assert a['config']==b['config'] and a['benchmark']==b['benchmark'] and a['raw_signals']==b['raw_signals']
    # The reused helper writes one CSV through ROOT; redirect only its output path.
    old_output = ab.ROOT
    try:
        ab.ROOT = ROOT
        audits = {v:ab.audit_one(data, saved, v, provider) for v, saved in [('A',a),('B',b)]}
    finally:
        ab.ROOT = old_output
    prefixes = {'A': [], 'B': []}
    with ProcessPoolExecutor(max_workers=3) as pool:
        jobs = [pool.submit(replay_check,v,day) for v in RUNNERS
                for day in (START,'2025-10-29','2026-10-28',END)]
        for job in as_completed(jobs):
            variant,day = job.result()
            if day!=END:
                prefixes[variant].append(day)
            print(variant+' '+('exact replay ' if day==END else 'causal prefix ')+day+' passed',flush=True)
    prefixes = {v:sorted(days) for v,days in prefixes.items()}
    differences, fills, winners = ab.differences(a,b)
    for name, rows in [('2454_signal_trade_differences',differences),('2454_execution_differences',fills),
                       ('2454_A_profitable_trade_outcomes',winners),('2454_B_candidates',b['candidates'])]:
        write_csv(ROOT/(name+'.csv'), rows)
    output = dict(period=[START,END], initial_cash_per_symbol_per_arm=1000000,
        combined_portfolio=False, snapshot_sha256=sha(SNAPSHOT), data_validation=read(ROOT/'data_validation.json'),
        audits=audits, causal_prefixes=prefixes, exact_offline_replays=True,
        identical_config_signals_benchmark=True, symbols={})
    prior_validation = read(PRIOR/'validation.json')
    assert prior_validation['status']=='PASSED'
    assert sha(ab.SNAPSHOT)==prior_validation['source_snapshot_sha256']
    old_provider = FugleSnapshotProvider(ab.SNAPSHOT)
    prior_comparison = read(PRIOR/'comparison.json')
    for symbol in ('2330','2408','2454'):
        source_root = ROOT if symbol=='2454' else PRIOR
        series = data if symbol=='2454' else fetch_backtest_data(symbol, START, END, provider=old_provider)
        assert series.window.sessions==data.window.sessions
        arms = {v:read(result_path(v,symbol,source_root)) for v in ('A','B')}
        entry = {}
        for variant, saved in arms.items():
            assert saved['config']==a['config']
            if symbol!='2454':
                assert sha(result_path(variant,symbol,source_root))==prior_comparison['symbols'][symbol]['output_sha256'][variant]
            stats, trades = ab.holding(series,saved)
            open_analysis = open_position_analysis(saved,series)
            exposure = (sum(t['holding_market_minutes'] for t in trades)+(open_analysis['holding_market_minutes'] if open_analysis else 0)) / (484*270)*100
            attribution = equity_attribution(saved,trades)
            ledger = attribution.pop('ledger')
            ordered = sorted(trades,key=lambda t:t['net_profit'],reverse=True)
            entry[variant] = dict(summary=saved['summary'], benchmark=saved['benchmark'], holding=stats,
                exposure_percent=exposure, open_position=open_analysis, pending_decision=saved['pending_decision'],
                pending_candidate=saved.get('pending_candidate'), equity_attribution=attribution,
                top_5_trades=ordered[:5], top_2_profit=sum(t['net_profit'] for t in ordered[:2]),
                positive_trade_profits=sum(t['net_profit'] for t in trades if t['net_profit']>0),
                costs=saved['summary']['commissions']+saved['summary']['transaction_taxes'])
            if symbol=='2454':
                write_csv(ROOT/f'2454_{variant}_holding.csv',trades)
                write_csv(ROOT/f'2454_{variant}_equity_attribution.csv',ledger)
                regimes = exposure_regimes(saved)
                write_csv(ROOT/f'2454_{variant}_exposure_regimes.csv',regimes)
                entry[variant]['largest_flat_price_rises'] = sorted([r for r in regimes if r['state']=='FLAT'],
                    key=lambda r:r['underlying_price_return_percent'],reverse=True)[:5]
        cross, rows = crossing_diagnostics(series,arms['A'])
        write_csv(ROOT/f'{symbol}_ma60_crossing_diagnostics.csv',rows)
        d, f, w = ab.differences(arms['A'],arms['B'])
        entry.update(crossing=cross,signal_trade_differences=d,profitable_A_trade_outcomes=w,
            b_candidate_statuses=dict(Counter(c['status'] for c in arms['B']['candidates'])),
            result_sha256={v:sha(result_path(v,symbol,source_root)) for v in ('A','B')},
            source_directory=str(source_root.relative_to(PROJECT)))
        output['symbols'][symbol] = entry
    write_json(ROOT/'comparison.json',output)
    write_json(ROOT/'audit_validation.json',dict(status='PASSED',audits=audits,causal_prefixes=prefixes,
        snapshot_sha256=sha(SNAPSHOT),protected=protect(),prior_validation_sha256=sha(PRIOR/'validation.json')))
    print('2454 A/B independent audits and three-stock diagnostics PASSED.',flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stage',choices=('capture','run','audit','report'),required=True)
    args = parser.parse_args()
    ROOT.mkdir(parents=True,exist_ok=True)
    try:
        if args.stage=='report':
            from .report_mediatek_ab import render
            render()
        else:
            globals()[args.stage]()
    except Exception as error:
        # Existing network client exposes fixed codes only, never headers or keys.
        write_json(ROOT/f'{args.stage}_failure.json',dict(stage=args.stage,error_type=type(error).__name__,
            detail=str(error),period=[START,END],fabricated=False,shortened=False))
        raise


if __name__=='__main__':
    main()
