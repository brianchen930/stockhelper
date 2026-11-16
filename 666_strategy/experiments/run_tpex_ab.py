"""Offline TPEx entry adapter; existing strategy, provider and studies are immutable.

python -B -X utf8 -m 666_strategy.experiments.run_tpex_ab --stage preflight
Stages: preflight, run, audit, compare, tests, report, package.
"""
import argparse
from collections import Counter
from concurrent.futures import ProcessPoolExecutor, as_completed
from contextlib import contextmanager
import json
from importlib.metadata import version
from pathlib import Path
import platform
import re
import subprocess
import sys
from unittest.mock import patch

import numpy as np
import pandas as pd

from ..backtest import BacktestConfig, export_backtest, fetch_backtest_data, run_backtest
from ..fugle_provider import FugleSnapshotProvider, SCHEMA_V2
from ..fugle_client import FugleClient
from ..verification.analyze_fugle_trades import write_csv
from ..verification.verify_previous_fugle_year import sha, write_json, equity_attribution, open_position_analysis
from .buy_confirmation import run_confirmed_backtest
from . import run_buy_confirmation_ab as ab
from .run_mediatek_ab import crossing_diagnostics, exposure_regimes
from .report_mediatek_ab import independent_benchmark

PROJECT = Path(__file__).resolve().parents[2]
ROOT = PROJECT/'666_strategy/verification/fugle_tpex_ab_20241030_20261029'
INPUT = PROJECT/'666_strategy/verification/tpex_integration_3105_6147_3211_20241030_20261029'
OLD2454 = PROJECT/'666_strategy/verification/fugle_ab_2454_20241030_20261029'
START, END = '2024-10-30', '2026-10-29'
SYMBOLS = ('3105.TWO', '6147.TWO', '3211.TWO')
ALL = ('2330.TW', '2408.TW', '2454.TW') + SYMBOLS
RUNNERS = {'A': run_backtest, 'B': run_confirmed_backtest}


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def source(symbol):
    if symbol in SYMBOLS:
        return INPUT/f'{symbol[:4]}_20241030_20261029.snapshot.json'
    return OLD2454/'2454_fugle_history.snapshot.json' if symbol=='2454.TW' else ab.SNAPSHOT


def result_path(symbol, variant):
    directory = ROOT if symbol in SYMBOLS else OLD2454 if symbol=='2454.TW' else ab.ROOT
    return directory/f'{symbol[:4]}_{variant}_20241030_20261029.json'


def protect():
    path = ROOT/'protected_baseline.json'
    if not path.exists():
        paths = [p for p in (PROJECT/'666_strategy').rglob('*') if p.is_file()
                 and ROOT not in p.parents and '.cache' not in p.parts and '__pycache__' not in p.parts
                 and p.name not in ('run_tpex_ab.py', 'report_tpex_ab.py')]
        paths += list((PROJECT/'app').rglob('*.py')) + list(PROJECT.glob('*.py'))
        write_json(path, {p.relative_to(PROJECT).as_posix(): sha(p) for p in sorted(set(paths))})
    baseline = read(path)
    changed = [p for p, h in baseline.items() if not (PROJECT/p).is_file() or sha(PROJECT/p)!=h]
    assert not changed, changed
    return dict(existing_files_checked=len(baseline), changed_existing_files=changed)


@contextmanager
def offline():
    with patch('socket.create_connection', side_effect=AssertionError('NETWORK_FORBIDDEN')), \
         patch.object(FugleClient, '__init__', side_effect=AssertionError('LIVE_FUGLE_FORBIDDEN')):
        yield


def load(symbol, end=END):
    provider = FugleSnapshotProvider(source(symbol))
    return provider, fetch_backtest_data(symbol, START, end, provider=provider)


def config():
    return BacktestConfig(**read(result_path('2330.TW','A'))['config'])


def preflight():
    protected = protect()
    validation = read(INPUT/'validation.json')
    assert validation['status']=='PASSED_DATA_INTEGRATION'
    old = read(OLD2454/'comparison.json')
    for symbol in ALL[:3]:
        for variant in RUNNERS:
            assert sha(result_path(symbol,variant))==old['symbols'][symbol[:4]]['result_sha256'][variant]
        assert sha(source(symbol))==(old['snapshot_sha256'] if symbol=='2454.TW' else
                                    read(ab.ROOT/'validation.json')['source_snapshot_sha256'])
    checks = {}
    with offline():
        for symbol in SYMBOLS:
            assert sha(source(symbol))==validation['source_snapshots'][symbol]['sha256']
            provider, data = load(symbol)
            assert provider.schema==SCHEMA_V2 and data.symbol==symbol
            assert (provider.metadata['exchange'],provider.metadata['market'])==('TPEx','OTC')
            assert provider.research_sample['origin']=='user_designated'
            assert len(data.window.sessions)==484 and len(data.history.bars)==2480 and len(data.daily.bars)==604
            assert not data.history.issues and not data.daily.issues
            closing = read(INPUT/f'{symbol[:4]}_closing_time_validation.json')
            assert closing['completion_at_1330_verified'] and not closing['late_closes']
            _, tiny = load(symbol, START)
            for runner in RUNNERS.values():
                out = runner(tiny,config=config()).to_dict()
                assert out['symbol']==symbol and out['config']['initial_cash']==1000000
            checks[symbol] = dict(schema=provider.schema, exchange='TPEx',market='OTC',
                snapshot_sha256=sha(source(symbol)), sessions=484, validated_60m=2480, validated_daily=604,
                minute_warmup=60, daily_warmup=120, synthetic_bars=0, first_day_both_entries_passed=True,
                closing_validation_sha256=sha(INPUT/f'{symbol[:4]}_closing_time_validation.json'))
    write_json(ROOT/'entry_validation.json',dict(status='PASSED', period=[START,END], samples=checks,
        entry_change='New explicit-symbol offline adapter only; no existing source edits',
        config=read(result_path('2330.TW','A'))['config'], network_disabled=True, protected=protected,
        input_validation_sha256=sha(INPUT/'validation.json'),
        environment=dict(python=sys.version, platform=platform.platform(), pandas=pd.__version__,numpy=np.__version__,
                         tzdata=version('tzdata'),python_dotenv=version('python-dotenv'),yfinance=version('yfinance'))))
    (ROOT/'requirements-replay.txt').write_text('\n'.join(f'{name}=={version(name)}' for name in
        ('numpy','pandas','tzdata','python-dotenv','yfinance'))+'\n',encoding='utf-8')
    print('PREFLIGHT PASSED: explicit .TWO v2, full grids, both original runners, zero network.',flush=True)


def run_one(symbol, variant):
    with offline():
        _, data = load(symbol)
        result = RUNNERS[variant](data,config=config())
        export_backtest(result,result_path(symbol,variant))
        return symbol,variant,result.summary


def run():
    protect()
    assert read(ROOT/'entry_validation.json')['status']=='PASSED'
    with ProcessPoolExecutor(max_workers=3) as pool:
        jobs = [pool.submit(run_one,symbol,v) for symbol in SYMBOLS for v in RUNNERS]
        for job in as_completed(jobs):
            symbol,v,summary = job.result()
            print(symbol+' '+v+' '+json.dumps(summary),flush=True)
    protect()


def replay_one(symbol, variant, end):
    saved = read(result_path(symbol,variant))
    with offline():
        _, data = load(symbol,end)
        replay = RUNNERS[variant](data,config=BacktestConfig(**saved['config'])).to_dict()
    if end==END:
        assert replay==saved, (symbol,variant,'full replay')
    else:
        for field,key in [('fills','execution_time'),('trades','exit_time'),('events','at'),
                          ('raw_signals','signal_time'),('equity_curve','at')]:
            assert replay[field]==[r for r in saved[field] if r[key][:10]<=end],(symbol,variant,end,field)
    return dict(symbol=symbol,variant=variant,end=end,passed=True,
                exact_full_result=end==END,truncated_actual_input=end!=END)


def audit():
    protect()
    audits = {}
    old_root = ab.ROOT
    try:
        ab.ROOT = ROOT  # Only redirect this helper's timing CSV output.
        with offline():
            for symbol in SYMBOLS:
                provider,data = load(symbol)
                a,b = [read(result_path(symbol,v)) for v in RUNNERS]
                assert a['config']==b['config'] and a['raw_signals']==b['raw_signals'] and a['benchmark']==b['benchmark']
                audits[symbol] = {v:ab.audit_one(data,saved,v,provider) for v,saved in [('A',a),('B',b)]}
                for v,saved in [('A',a),('B',b)]:
                    audits[symbol][v]['benchmark'] = independent_benchmark(saved)
                print(symbol+' independent costs, lifecycle, signals, timing PASSED',flush=True)
    finally:
        ab.ROOT = old_root
    prefixes = []
    with ProcessPoolExecutor(max_workers=3) as pool:
        jobs = [pool.submit(replay_one,s,v,day) for s in SYMBOLS for v in RUNNERS
                for day in (START,'2025-10-29','2026-10-28',END)]
        for job in as_completed(jobs):
            item = job.result()
            prefixes.append(item)
            print('REPLAY '+json.dumps(item),flush=True)
    write_json(ROOT/'audit_validation.json',dict(status='PASSED',audits=audits,
        replays=sorted(prefixes,key=lambda x:(x['symbol'],x['variant'],x['end'])),
        no_lookahead_checks='independent completed-daily state machine + real input truncation',
        exact_offline_full_replays=6, causal_truncations=18, protected=protect()))


def market_stats(data, provider):
    daily = data.daily.bars
    logret = np.log(daily.Close/daily.Close.shift(1))
    pre = logret.loc[(logret.index.date>=pd.Timestamp('2024-08-01').date()) &
                     (logret.index.date<pd.Timestamp(START).date())].dropna()
    run = logret.loc[(logret.index.date>=data.start)&(logret.index.date<=data.end)].dropna()
    native_daily = provider.frames[(data.symbol,'1d')]
    window = native_daily.loc[(native_daily.index.date>=data.start)&(native_daily.index.date<=data.end)]
    assert np.array_equal(window.Close.to_numpy(),daily.loc[(daily.index.date>=data.start)&(daily.index.date<=data.end),'Close'].to_numpy())
    return dict(preperiod=['2024-08-01','2024-10-29'], preperiod_observations=len(pre),
        preperiod_annualized_log_volatility_percent=float(pre.std(ddof=1)*np.sqrt(252)*100),
        study_annualized_log_volatility_percent=float(run.std(ddof=1)*np.sqrt(252)*100),
        study_observations=len(run), median_daily_range_percent=float(((window.High-window.Low)/window.Close*100).median()),
        median_daily_volume_shares=float(window.Volume.median()), minimum_daily_volume_shares=float(window.Volume.min()),
        median_daily_close_times_volume_proxy=float((window.Close*window.Volume).median()))


def compare():
    protect()
    # Descriptive exports can run alongside replay audits; report requires their PASS.
    assert read(ROOT/'entry_validation.json')['status']=='PASSED'
    study = dict(period=[START,END],initial_cash_per_symbol_per_arm=1000000,combined_portfolio=False,symbols={})
    summaries,holdings,monthly = [],[],[]
    with offline():
        for symbol in ALL:
            provider,data = load(symbol)
            assert len(data.window.sessions)==484
            arms = {v:read(result_path(symbol,v)) for v in RUNNERS}
            assert arms['A']['config']==arms['B']['config']==read(result_path('2330.TW','A'))['config']
            assert arms['A']['raw_signals']==arms['B']['raw_signals']
            item = dict(market=market_stats(data,provider),source_snapshot_sha256=sha(source(symbol)))
            for v,saved in arms.items():
                run_bars=data.history.bars.loc[(data.history.bars.index.date>=data.start)&(data.history.bars.index.date<=data.end)]
                assert saved['benchmark']['entry_time']==run_bars.index[0].isoformat()
                assert saved['benchmark']['entry_price']==run_bars.iloc[0].Open
                assert saved['benchmark']['mark_time']==run_bars.iloc[-1].bar_end.isoformat()
                assert saved['benchmark']['mark_price']==run_bars.iloc[-1].Close
                stats,trades = ab.holding(data,saved)
                pos = open_position_analysis(saved,data)
                attribution = equity_attribution(saved,trades)
                ledger = attribution.pop('ledger')
                exposure = (sum(t['holding_market_minutes'] for t in trades)+(pos['holding_market_minutes'] if pos else 0))/(484*270)*100
                ranked = sorted(trades,key=lambda t:t['net_profit'],reverse=True)
                participation = []
                for f in saved['fills']:
                    bar = data.history.bars.loc[pd.Timestamp(f['execution_time'])]
                    participation.append(dict(**f, execution_bar_volume_shares=float(bar.Volume),
                        shares_to_full_execution_bar_volume_percent=f['shares']/float(bar.Volume)*100 if bar.Volume else None))
                item[v] = dict(summary=saved['summary'],benchmark=saved['benchmark'],holding=stats,open_position=pos,
                    pending_decision=saved['pending_decision'],pending_candidate=saved.get('pending_candidate'),
                    equity_attribution=attribution,exposure_percent=exposure,top_5_trades=[t for t in ranked if t['net_profit']>0][:5],
                    top_2_profit=round(sum(t['net_profit'] for t in [t for t in ranked if t['net_profit']>0][:2]),2),
                    positive_trade_profits=round(sum(t['net_profit'] for t in trades if t['net_profit']>0),2),
                    total_costs=round(saved['summary']['commissions']+saved['summary']['transaction_taxes'],2),
                    max_execution_bar_participation_percent=max(r['shares_to_full_execution_bar_volume_percent'] for r in participation),
                    benchmark_audit=dict(**independent_benchmark(saved),source_endpoint_prices_checked=True),
                    result_sha256=sha(result_path(symbol,v)))
                summaries.append(dict(symbol=symbol,variant=v,**saved['summary'],total_costs=item[v]['total_costs'],
                    exposure_percent=exposure,buy_hold_return_percent=saved['benchmark']['return_percent']))
                holdings.extend(dict(variant=v,**t) for t in trades)
                previous = saved['config']['initial_cash']
                ends = {p['at'][:7]:p for p in saved['equity_curve'] if p['phase']=='CLOSE'}
                for month,p in ends.items():
                    monthly.append(dict(symbol=symbol,variant=v,month=month,ending_equity=p['equity'],
                        net_change=round(p['equity']-previous,2),return_percent=(p['equity']/previous-1)*100))
                    previous = p['equity']
                if symbol in SYMBOLS:
                    for name,rows in [('holding',trades),('equity_attribution',ledger),
                                      ('exposure_regimes',exposure_regimes(saved)),('execution_liquidity',participation)]:
                        write_csv(ROOT/f'{symbol[:4]}_{v}_{name}.csv',rows)
            cross,rows = crossing_diagnostics(data,arms['A'])
            diff,fills,winners = ab.differences(arms['A'],arms['B'])
            item.update(crossing=cross,signal_trade_differences=diff,profitable_A_trade_outcomes=winners,
                        b_candidate_statuses=dict(Counter(c['status'] for c in arms['B']['candidates'])))
            write_csv(ROOT/f'{symbol[:4]}_ma60_crossing_diagnostics.csv',rows)
            if symbol in SYMBOLS:
                for name,rows in [('signal_trade_differences',diff),('execution_differences',fills),
                                  ('A_profitable_trade_outcomes',winners),('B_candidates',arms['B']['candidates'])]:
                    write_csv(ROOT/f'{symbol[:4]}_{name}.csv',rows)
            study['symbols'][symbol] = item
            print(symbol+' comparison complete',flush=True)
    write_json(ROOT/'comparison.json',study)
    write_csv(ROOT/'six_stock_summary.csv',summaries)
    write_csv(ROOT/'six_stock_holding_trades.csv',holdings)
    write_csv(ROOT/'six_stock_monthly_equity.csv',monthly)
    protect()


def tests():
    records = []
    for label,directory in [('existing','666_strategy/tests'),('experiment','666_strategy/experiments/tests')]:
        command = [sys.executable,'-B','-X','utf8','-m','unittest','discover','-s',directory,'-v']
        done = subprocess.run(command,cwd=PROJECT,stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
        path = ROOT/f'{label}_tests.log'
        path.write_bytes(done.stdout)
        raw = done.stdout.decode('utf-8')
        count = re.search(r'Ran (\d+) tests in ([\d.]+)s',raw)
        assert done.returncode==0 and count and raw.rstrip().endswith('OK'),raw[-5000:]
        records.append(dict(command=command[1:],tests=int(count[1]),elapsed_seconds=float(count[2]),
            exit_code=done.returncode,log=path.name,sha256=sha(path)))
    write_json(ROOT/'tests_result.json',records)
    print(json.dumps(records),flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stage',choices=('preflight','run','audit','compare','tests','report','package'),required=True)
    args = parser.parse_args()
    ROOT.mkdir(parents=True,exist_ok=True)
    if args.stage in ('report','package'):
        from . import report_tpex_ab
        getattr(report_tpex_ab,args.stage)()
    else:
        globals()[args.stage]()


if __name__=='__main__':
    main()
