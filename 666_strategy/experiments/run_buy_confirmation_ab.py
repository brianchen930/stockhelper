"""Reproducible offline A/B study; no production strategy or broker mutations.

python -B -X utf8 -m 666_strategy.experiments.run_buy_confirmation_ab
"""
import argparse
from bisect import bisect_right
from collections import Counter
from decimal import Decimal
import json
import math
from pathlib import Path
from statistics import mean, median

import numpy as np
import pandas as pd

from ..backtest import BacktestConfig, export_backtest, fetch_backtest_data, run_backtest
from ..fugle_provider import FugleSnapshotProvider
from ..verification.analyze_fugle_trades import write_csv
from ..verification.verify_backtest_snapshot import currency
from ..verification.verify_continuous_fugle import ROOT as ORIGINAL, SNAPSHOT
from ..verification.verify_fugle_integration import independent_signals
from ..verification.verify_previous_fugle_year import sha, write_json
from .buy_confirmation import run_confirmed_backtest

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parent.parent
ROOT = HERE.parent/'verification'/'fugle_ab_confirmation_20241030_20261029'
START, END = '2024-10-30', '2026-10-29'
SYMBOLS = ('2330', '2408')


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def path(symbol, variant):
    return ROOT/f'{symbol}_{variant}_20241030_20261029.json'


def run(symbols=SYMBOLS):
    provider = FugleSnapshotProvider(SNAPSHOT)
    assert sha(SNAPSHOT) == read(ORIGINAL/'validation.json')['snapshot_sha256']
    for symbol in symbols:
        data = fetch_backtest_data(symbol, START, END, provider=provider)
        assert len(data.window.sessions)==484 and len(data.history.bars)==2480 and len(data.daily.bars)==604
        baseline = read(ORIGINAL/f'fugle_backtest_{symbol}_20241030_20261029.json')
        config = BacktestConfig(**baseline['config'])
        assert config.initial_cash == 1000000
        for variant, runner in [('A',run_backtest), ('B',run_confirmed_backtest)]:
            result = runner(data, config=config)
            if variant=='A':
                assert result.to_dict()==baseline, 'A must exactly reproduce the untouched original two-year result'
            export_backtest(result,path(symbol,variant))
            print(symbol+' '+variant+' '+json.dumps(result.summary),flush=True)


def audit_one(data, saved, variant, provider):
    """Independently reconcile actual cash and causality, with B's two-bar index."""
    assert independent_signals(data) == [(s['signal_time'],s['signal_type']) for s in saved['raw_signals']]
    bars = data.history.bars
    signals = {s['signal_time']:s for s in saved['raw_signals']}
    close_events = {e['at']:e for e in saved['events'] if e['phase']=='CLOSE'}
    cash, shares, cost = currency(saved['config']['initial_cash']), 0, None
    timing = []
    for f in saved['fills']:
        at = pd.Timestamp(f['execution_time']); ei = bars.index.get_loc(at)
        assert f['price']==bars.loc[at,'Open']
        if f['action']=='EXIT':
            assert data.daily.snapshot_at(f['trend_close_time'])['status']=='NON_BULL'
            event = next(e for e in close_events.values() if e['pending_decision'] and e['pending_decision']['decision_id']==f['decision_id'])
            si = bars.index.get_loc(pd.Timestamp(event['bar_start']))
            trigger = pd.Timestamp(event['at'])
            assert ei==si+1
        else:
            raw = signals[f['signal_time']]
            assert raw['signal_type']==f['action']
            si = bars.index.get_loc(pd.Timestamp(raw['bar_start']))
            extra = variant=='B' and f['action']=='BUY'
            assert ei==si+(2 if extra else 1)
            trigger = bars.iloc[si+1].bar_end if extra else pd.Timestamp(raw['bar_end'])
            if extra:
                c = bars.iloc[si+1]
                assert c.Close>c.MA60 and pd.Timestamp(f['confirmation_time'])==trigger
                assert data.daily.snapshot_at(trigger)['status']=='BULL'
                assert pd.Timestamp(data.daily.snapshot_at(trigger)['daily_close_time'])<=trigger
        assert bars.index[bars.index>=trigger][0]==at
        assert trigger<=at
        gross = currency(Decimal(str(f['price']))*f['shares'])
        fee = currency(max(Decimal(str(saved['config']['minimum_commission'])),gross*Decimal(str(saved['config']['commission_rate']))))
        selling = f['action']!='BUY'
        tax = currency(gross*Decimal(str(saved['config']['sell_tax_rate']))) if selling else currency(0)
        assert (currency(f['gross_amount']),currency(f['commission']),currency(f['transaction_tax']))==(gross,fee,tax)
        if not selling:
            assert shares==0 and data.daily.snapshot_at(at)['status']=='BULL'
            assert data.daily.snapshot_at(f['signal_time'])['status']=='BULL'
            shares,cost=f['shares'],gross+fee
            # Independently verify largest affordable whole-share sizing.
            unit=saved['config']['share_unit']
            larger=gross+Decimal(str(f['price']))*unit
            larger_fee=currency(max(Decimal(str(saved['config']['minimum_commission'])),larger*Decimal(str(saved['config']['commission_rate']))))
            assert cost<=cash<larger+larger_fee
            cash-=cost
        else:
            assert f['shares']==shares
            assert currency(f['net_profit'])==gross-fee-tax-cost
            shares=0
            cash+=gross-fee-tax
        assert currency(f['cash_after'])==cash and cash>=0
        timing.append(dict(**f, trigger_confirmed_at=trigger.isoformat(), signal_bar_index=si,
            execution_bar_index=ei, index_gap=ei-si, next_bar_after_confirmation=True,
            overnight_confirmation_to_fill=trigger.date()!=at.date()))
    value=currency(Decimal(str(bars.iloc[-1].Close))*shares)
    s=saved['summary']
    assert currency(s['ending_cash'])==cash
    assert currency(s['ending_equity'])==cash+value
    assert currency(s['total_net_profit'])==cash+value-currency(s['initial_cash'])
    assert math.isclose(s['total_return_percent'],float((cash+value)/currency(s['initial_cash'])-1)*100,abs_tol=1e-10)
    assert currency(s['commissions'])==sum((currency(f['commission']) for f in saved['fills']),currency(0))
    assert currency(s['transaction_taxes'])==sum((currency(f['transaction_tax']) for f in saved['fills']),currency(0))
    assert currency(s['realized_net_profit'])==sum((currency(t['net_profit']) for t in saved['trades']),currency(0))
    for t in saved['trades']:
        entry,exit_fill=[f for f in saved['fills'] if f['trade_id']==t['trade_id']]
        assert entry['action']=='BUY' and exit_fill['action']==t['exit_action']
        assert entry['execution_time']==t['entry_time'] and exit_fill['execution_time']==t['exit_time']
        assert entry['shares']==exit_fill['shares']==t['shares']
        assert currency(t['entry_cost'])==currency(entry['gross_amount'])+currency(entry['commission'])
        assert currency(t['net_profit'])==currency(exit_fill['net_profit'])
        assert math.isclose(t['return_percent'],t['net_profit']/t['entry_cost']*100,abs_tol=1e-10)
    assert s['completed_trades']==len(saved['trades'])
    wins=sum(t['net_profit']>0 for t in saved['trades'])
    assert s['winning_trades']==wins
    assert math.isclose(s['win_rate_percent'],wins/len(saved['trades'])*100,abs_tol=1e-10)
    # Reconstruct each OPEN/CLOSE mark from independently verified fills.
    cash,shares=currency(s['initial_cash']),0
    fmap={f['execution_time']:f for f in saved['fills']}
    for p in saved['equity_curve']:
        if p['phase']=='OPEN' and p['at'] in fmap:
            f=fmap[p['at']]
            cash=currency(f['cash_after']);shares=f['shares'] if f['action']=='BUY' else 0
        at=pd.Timestamp(p['at'])
        bar=bars.loc[at] if p['phase']=='OPEN' else bars.loc[bars.bar_end==at].iloc[0]
        mark=float(bar.Open if p['phase']=='OPEN' else bar.Close)
        assert p['mark_price']==mark and p['shares']==shares and currency(p['cash'])==cash
        assert currency(p['equity'])==cash+currency(Decimal(str(mark))*shares)
    equity=np.array([s['initial_cash'],*[p['equity'] for p in saved['equity_curve']]])
    assert math.isclose(s['max_drawdown_percent'],float(np.max((np.maximum.accumulate(equity)-equity)/np.maximum.accumulate(equity))*100),abs_tol=1e-9)
    if variant=='B':
        for c in saved['candidates']:
            i=bars.index.get_loc(pd.Timestamp(c['signal_bar_start']))
            r,p=bars.iloc[i],bars.iloc[i-1]
            assert p.Close<=p.MA60 and r.Close>r.MA60 and c['signal_daily_status']=='BULL'
            if 'confirmation_time' not in c:
                assert i==len(bars)-1 and c['status']=='WAITING'
                continue
            next_bar=bars.iloc[i+1]
            assert pd.Timestamp(c['confirmation_time'])==next_bar.bar_end
            assert c['confirmation_close']==next_bar.Close and c['confirmation_ma60']==next_bar.MA60
            if c['status']=='CANCELLED_CLOSE_NOT_ABOVE_MA60':
                assert next_bar.Close<=next_bar.MA60
            elif c['status']=='CANCELLED_CONFIRMATION_TREND':
                assert c['confirmation_daily_status']!='BULL'
            elif c['status'] in ('FILLED','CONFIRMED','CANCELLED_AT_EXECUTION','INSUFFICIENT_CASH'):
                assert next_bar.Close>next_bar.MA60 and c['confirmation_daily_status']=='BULL'
            else:
                assert c['status']=='CANCELLED_INTERVENING_TREND'
    reference_decisions(data,saved,variant)
    write_csv(ROOT/f'{data.symbol[:4]}_{variant}_timing_audit.csv',timing)
    return dict(passed=True, fills_checked=len(timing), raw_signals_checked=len(saved['raw_signals']),
                independent_costs_cash_equity_drawdown=True, independent_position_lifecycle=True,
                candidate_checks=len(saved.get('candidates',[])))


def reference_decisions(data,saved,variant):
    """Second, small state machine derives ALL fills without the B runner/decisions.

    Bisect completed daily close timestamps, so later daily values cannot leak
    into an earlier event. Prices and size are audited separately above.
    """
    days=list(data.daily.bars.itertuples())
    times=[pd.Timestamp(r.daily_close_time).value for r in days]
    def trend(at):
        k=bisect_right(times,pd.Timestamp(at).value)-1
        assert k>=0
        return days[k].trend_status
    signals={r['signal_time']:r for r in saved['raw_signals']}
    bars=data.history.bars.loc[(data.history.bars.index.date>=data.start)&(data.history.bars.index.date<=data.end)]
    holding=False;candidate=None;pending=None;expected=[]
    for i,(at,r) in enumerate(bars.iterrows()):
        if pending is not None:
            action,origin=pending
            if action!='BUY' or trend(at)=='BULL':
                expected.append((at.isoformat(),action,origin))
                holding=action=='BUY'
            pending=None
        event=signals.get(r.bar_end.isoformat())
        kind=event['signal_type'] if event else None
        status=trend(r.bar_end)
        if holding:
            if status=='NON_BULL':pending=('EXIT',None)
            elif kind=='SELL':pending=('SELL',r.bar_end.isoformat())
        elif candidate is not None:
            origin,previous_i=candidate
            assert i==previous_i+1
            if r.Close>r.MA60 and status=='BULL':pending=('BUY',origin)
            candidate=None
        elif kind=='BUY' and status=='BULL':
            if variant=='A':pending=('BUY',r.bar_end.isoformat())
            else:candidate=(r.bar_end.isoformat(),i)
    actual=[(f['execution_time'],f['action'],f['signal_time']) for f in saved['fills']]
    assert actual==expected, 'Independent lifecycle differs from actual fills'


def holding(data, saved):
    rows=[]
    sessions=list(data.window.calendar.sessions)
    for t in saved['trades']:
        entry,exit_time=pd.Timestamp(t['entry_time']),pd.Timestamp(t['exit_time'])
        held=data.history.bars.loc[(data.history.bars.index>=entry)&(data.history.bars.index<exit_time)]
        previous=rows[-1] if rows else None
        gap=sessions.index(entry.date())-sessions.index(pd.Timestamp(previous['exit_time']).date()) if previous else None
        costs=round(t['entry_commission']+t['exit_commission']+t['transaction_tax'],2)
        assert math.isclose((t['exit_price']-t['entry_price'])*t['shares']-costs,t['net_profit'],abs_tol=1e-6)
        rows.append(dict(**t,holding_trading_sessions=len(set(held.index.date)),holding_bars=len(held),
            holding_market_minutes=int(held.duration_minutes.sum()),same_day=entry.date()==exit_time.date(),
            reentry_gap_sessions=gap,rapid_reentry=gap is not None and gap<=3,total_costs=costs))
    def group(subset):
        return dict(count=len(subset),wins=sum(t['net_profit']>0 for t in subset),
                    net_profit=round(sum(t['net_profit'] for t in subset),2))
    distribution=[dict(bin=label,**group([t for t in rows if lo<=t['holding_days']<hi]))
                  for lo,hi,label in [(0,1,'<1'),(1,3,'1-<3'),(3,7,'3-<7'),(7,14,'7-<14'),(14,30,'14-<30'),(30,float('inf'),'>=30')]]
    exposed=[dict(bin=label,**group([t for t in rows if lo<=t['holding_trading_sessions']<hi]))
             for lo,hi,label in [(1,2,'1'),(2,4,'2-3'),(4,6,'4-5'),(6,11,'6-10'),(11,21,'11-20'),(21,float('inf'),'>20')]]
    assert sum(r['count'] for r in distribution)==len(rows)
    return dict(mean_days=mean(t['holding_days'] for t in rows),median_days=median(t['holding_days'] for t in rows),
        calendar_distribution=distribution,session_distribution=exposed,
        same_day=group([t for t in rows if t['same_day']]),short_up_to_3_sessions=group([t for t in rows if t['holding_trading_sessions']<=3]),
        rapid_reentry=group([t for t in rows if t['rapid_reentry']]),open_position=saved['open_position']),rows


def differences(a,b):
    """Match entries by original crossing, never by ordinal trade number."""
    at={t['entry_signal_time']:t for t in a['trades']}
    bt={t['entry_signal_time']:t for t in b['trades']}
    bf={f['signal_time']:f for f in b['fills'] if f['action']=='BUY'}
    af={f['signal_time']:f for f in a['fills'] if f['action']=='BUY'}
    candidates={c['signal_time']:c for c in b['candidates']}
    rows=[]
    for signal in sorted(af.keys()|bf.keys()):
        left,right=at.get(signal),bt.get(signal)
        f,g=af.get(signal),bf.get(signal)
        c=candidates.get(signal)
        status=('DELAYED_MATCHED_ENTRY' if f and g else 'A_ONLY' if f else 'B_ONLY')
        rows.append(dict(signal_time=signal,status=status,
            b_candidate_status=c['status'] if c else 'NO_ELIGIBLE_B_CANDIDATE',
            a_entry_time=f['execution_time'] if f else None,b_entry_time=g['execution_time'] if g else None,
            a_entry_price=f['price'] if f else None,b_entry_price=g['price'] if g else None,
            price_change_percent=(g['price']/f['price']-1)*100 if f and g else None,
            a_shares=f['shares'] if f else None,b_shares=g['shares'] if g else None,
            a_exit_time=left['exit_time'] if left else None,b_exit_time=right['exit_time'] if right else None,
            a_exit_action=left['exit_action'] if left else None,b_exit_action=right['exit_action'] if right else None,
            a_net_profit=left['net_profit'] if left else None,b_net_profit=right['net_profit'] if right else None,
            a_return_percent=left['return_percent'] if left else None,b_return_percent=right['return_percent'] if right else None,
            actual_net_profit_difference=(right['net_profit']-left['net_profit']) if left and right else None,
            b_other_entries_during_A_trade=[dict(signal_time=q['signal_time'],entry_time=q['execution_time'],
                entry_price=q['price']) for q in b['fills'] if q['action']=='BUY' and left and
                left['entry_time']<=q['execution_time']<left['exit_time'] and q['signal_time']!=signal],
            # Price-only diagnostic avoids attributing changed capital to entry quality.
            equal_one_share_gross_price_difference=((right['exit_price']-right['entry_price'])-(left['exit_price']-left['entry_price'])) if left and right else None))
    fill_a={(f['execution_time'],f['action']):f for f in a['fills']}
    fill_b={(f['execution_time'],f['action']):f for f in b['fills']}
    fills=[dict(execution_time=k[0],action=k[1],presence='BOTH' if k in fill_a and k in fill_b else 'A_ONLY' if k in fill_a else 'B_ONLY',
                A=fill_a.get(k),B=fill_b.get(k)) for k in sorted(fill_a.keys()|fill_b.keys())]
    # All A profitable trades are retained in CSV; the five largest are a display choice.
    winners=sorted([r for r in rows if r['a_net_profit'] is not None and r['a_net_profit']>0],key=lambda r:r['a_net_profit'],reverse=True)
    return rows,fills,winners


def audit(symbols=SYMBOLS):
    provider=FugleSnapshotProvider(SNAPSHOT)
    output=(read(ROOT/'comparison.json') if (ROOT/'comparison.json').exists() else
            dict(period=[START,END],snapshot_sha256=sha(SNAPSHOT),initial_cash_per_symbol_per_arm=1000000,symbols={}))
    assert output['snapshot_sha256']==sha(SNAPSHOT) and output['period']==[START,END]
    for symbol in symbols:
        print(symbol+': auditing A/B ledger and causal prefixes',flush=True)
        data=fetch_backtest_data(symbol,START,END,provider=provider)
        a,b=read(path(symbol,'A')),read(path(symbol,'B'))
        assert a['config']==b['config'] and a['benchmark']==b['benchmark'] and a['raw_signals']==b['raw_signals']
        assert a==read(ORIGINAL/f'fugle_backtest_{symbol}_20241030_20261029.json')
        audits={v:audit_one(data,saved,v,provider) for v,saved in [('A',a),('B',b)]}
        prefix_checks=[]
        for day in (data.window.sessions[0],data.window.sessions[242],data.window.sessions[-2]):
            prefix_data=fetch_backtest_data(symbol,START,day,provider=provider)
            prefix=run_confirmed_backtest(prefix_data,config=BacktestConfig(**b['config'])).to_dict()
            within=lambda rows,key:[r for r in rows if r[key][:10]<=str(day)]
            assert prefix['fills']==within(b['fills'],'execution_time')
            assert prefix['trades']==within(b['trades'],'exit_time')
            assert prefix['events']==within(b['events'],'at')
            assert prefix['equity_curve']==within(b['equity_curve'],'at')
            assert prefix['raw_signals']==within(b['raw_signals'],'signal_time')
            prefix_checks.append(str(day))
            print(symbol+': B causal prefix '+str(day)+' passed',flush=True)
        write_json(ROOT/f'{symbol}_audit_checkpoint.json',dict(audits=audits,prefix_checks=prefix_checks,
            snapshot_sha256=sha(SNAPSHOT),result_sha256={v:sha(path(symbol,v)) for v in ('A','B')}))
        stats_a,trades_a=holding(data,a);stats_b,trades_b=holding(data,b)
        rows,fills,winners=differences(a,b)
        write_csv(ROOT/f'{symbol}_signal_trade_differences.csv',rows)
        write_csv(ROOT/f'{symbol}_execution_differences.csv',fills)
        write_csv(ROOT/f'{symbol}_A_profitable_trade_outcomes.csv',winners)
        write_csv(ROOT/f'{symbol}_B_candidates.csv',b['candidates'])
        write_csv(ROOT/f'{symbol}_A_holding.csv',trades_a)
        write_csv(ROOT/f'{symbol}_B_holding.csv',trades_b)
        output['symbols'][symbol]=dict(A=dict(summary=a['summary'],holding=stats_a),B=dict(summary=b['summary'],holding=stats_b),
            original_A_exact=True,audits=audits,b_prefix_checks=prefix_checks,
            b_candidate_statuses=dict(Counter(c['status'] for c in b['candidates'])),
            signal_trade_differences=rows,profitable_A_trade_outcomes=winners,
            identical_raw_signals=True,identical_benchmark=True,
            output_sha256={v:sha(path(symbol,v)) for v in ('A','B')})
        write_json(ROOT/'comparison.json',output)
        print(symbol+': passed all A/B audits',flush=True)


def report():
    # Implemented below alongside the saved, audited results.
    from .report_buy_confirmation_ab import render
    render()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stage',choices=('all','run','audit','report'),default='all')
    parser.add_argument('--symbol',choices=SYMBOLS,help='Run/audit one symbol; a final report still requires both')
    args=parser.parse_args();ROOT.mkdir(exist_ok=True)
    symbols=(args.symbol,) if args.symbol else SYMBOLS
    try:
        if args.stage in ('all','run'):run(symbols)
        if args.stage in ('all','audit'):audit(symbols)
        if args.stage in ('all','report'):report()
    except Exception as error:
        write_json(ROOT/'failure.json',dict(stage=args.stage,error_type=type(error).__name__,detail=str(error),
                   period=[START,END],fabricated=False,shortened=False))
        raise


if __name__=='__main__':main()
