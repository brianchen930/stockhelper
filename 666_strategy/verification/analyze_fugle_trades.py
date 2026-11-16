"""Offline descriptive analysis of the existing, unchanged annual backtests.

No alternative strategy, parameter search, downloads or order execution.
Run: python -B -X utf8 -m 666_strategy.verification.analyze_fugle_trades
"""
from collections import Counter, defaultdict
import csv
from decimal import Decimal
import hashlib
import json
from pathlib import Path
from statistics import mean, median

import numpy as np
import pandas as pd

from ..backtest import fetch_backtest_data
from ..fugle_provider import FugleSnapshotProvider

HERE = Path(__file__).resolve().parent
OUTPUT = HERE/'fugle_trade_analysis_20261101'


def stamp(value):
    return pd.Timestamp(value)


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def performance(rows):
    wins = [r for r in rows if r['net_profit'] > 0]
    losses = [r for r in rows if r['net_profit'] < 0]
    total_win = sum(r['net_profit'] for r in wins)
    total_loss = sum(r['net_profit'] for r in losses)
    return dict(count=len(rows), wins=len(wins), losses=len(losses),
        win_rate_percent=100*len(wins)/len(rows) if rows else None,
        net_profit=round(sum(r['net_profit'] for r in rows), 2),
        average_win=mean(r['net_profit'] for r in wins) if wins else None,
        average_loss=mean(r['net_profit'] for r in losses) if losses else None,
        median_win=median(r['net_profit'] for r in wins) if wins else None,
        median_loss=median(r['net_profit'] for r in losses) if losses else None,
        average_win_return_percent=mean(r['return_percent'] for r in wins) if wins else None,
        average_loss_return_percent=mean(r['return_percent'] for r in losses) if losses else None,
        payoff_ratio=total_win/len(wins)/(-total_loss/len(losses)) if wins and losses else None,
        profit_factor=total_win/-total_loss if losses else None,
        average_net_profit=mean(r['net_profit'] for r in rows) if rows else None,
        total_winning_profit=round(total_win, 2), total_losing_profit=round(total_loss, 2),
        gross_price_profit=round(sum(r['gross_price_profit'] for r in rows), 2),
        total_costs=round(sum(r['total_costs'] for r in rows), 2))


def write_csv(path, rows):
    if not rows:
        path.write_text('no_records\n', encoding='utf-8-sig')
        return
    fields = list(dict.fromkeys(key for row in rows for key in row))
    with path.open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows({key: json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list))
                         else value for key, value in row.items()} for row in rows)


def analyze_fills(saved, data):
    bars = data.history.bars
    close_events = {event['at']: event for event in saved['events'] if event['phase'] == 'CLOSE'}
    raw = {event['signal_time']: event for event in saved['raw_signals']}
    records = []
    for fill in saved['fills']:
        decisions = [event for event in close_events.values() if event.get('pending_decision')
                     and event['pending_decision']['decision_id'] == fill['decision_id']]
        assert decisions
        event = decisions[0]
        pending = event['pending_decision']
        trigger = stamp(pending['created_at'])
        i = bars.index.get_loc(stamp(event['bar_start']))
        row, previous = bars.iloc[i], bars.iloc[i-1]
        execution = stamp(fill['execution_time'])
        known = bars.loc[bars.bar_end <= execution].iloc[-1]
        daily = data.daily.snapshot_at(execution)
        condition_names = ('close_above_ma60', 'ma60_above_ma120', 'ma60_rising_5_sessions')
        failed = [name for name in condition_names if event['trend'][name] is False]
        if fill['action'] == 'BUY':
            assert previous.Close <= previous.MA60 and row.Close > row.MA60
            assert event['trend']['status'] == daily['status'] == 'BULL'
            assert pending['reason'] == 'RAW_BUY_AND_DAILY_BULL'
        elif fill['action'] == 'SELL':
            assert previous.Close >= previous.MA60 and row.Close < row.MA60
            assert pending['reason'] == 'RAW_SELL'
        else:
            assert fill['action'] == 'EXIT' and pending['reason'] == 'DAILY_TREND_INVALIDATED'
            assert event['trend']['status'] == 'NON_BULL' and failed
        assert execution == bars.index[bars.index >= trigger][0]
        assert fill['price'] == bars.loc[execution, 'Open']
        assert row.bar_end <= execution and known.bar_end <= execution
        records.append(dict(trade_id=fill['trade_id'], symbol=saved['symbol'], action=fill['action'],
            decision_reason=pending['reason'], trigger_time=trigger.isoformat(),
            signal_bar_start=bars.index[i].isoformat(), signal_bar_end=row.bar_end.isoformat(),
            previous_close=float(previous.Close), previous_ma60=float(previous.MA60),
            signal_close=float(row.Close), signal_ma60=float(row.MA60),
            signal_close_vs_ma60_percent=float((row.Close/row.MA60-1)*100),
            signal_ma60_slope_percent=float((row.MA60/previous.MA60-1)*100),
            signal_ma60_5_bar_slope_percent=(float((row.MA60/bars.MA60.iloc[i-5]-1)*100)
                if i >= 5 and pd.notna(bars.MA60.iloc[i-5]) else None),
            execution_time=execution.isoformat(), execution_open=fill['price'], shares=fill['shares'],
            execution_known_ma60=float(known.MA60),
            execution_open_vs_known_ma60_percent=float((fill['price']/known.MA60-1)*100),
            execution_gap_vs_signal_close_percent=float((fill['price']/row.Close-1)*100),
            signal_daily_status=event['trend']['status'], signal_daily_close_time=event['trend']['daily_close_time'],
            daily_close=event['trend']['close'], daily_ma60=event['trend']['daily_ma60'],
            daily_ma120=event['trend']['daily_ma120'], daily_ma60_5_sessions_ago=event['trend']['daily_ma60_5_sessions_ago'],
            daily_conditions={name:event['trend'][name] for name in condition_names},
            failed_daily_conditions=failed, execution_daily_status=daily['status'],
            raw_crossing_at_trigger=raw.get(trigger.isoformat(), {}).get('signal_type'),
            commission=fill['commission'], transaction_tax=fill['transaction_tax'],
            net_profit=fill['net_profit'], decision_id=fill['decision_id']))
    outcomes = []
    for signal in saved['raw_signals']:
        event = close_events[signal['signal_time']]
        outcomes.append(dict(**signal, reason=event['reason'], daily_status=event['trend']['status'],
            holding_after_review=event['holding'], decision_action=(event.get('pending_decision') or {}).get('action'),
            resulted_in_fill=any(row['trigger_time']==signal['signal_time'] and row['action']==signal['signal_type']
                                 for row in records)))
    return records, outcomes


def analyze_trades(saved, data, fills):
    bars = data.history.bars
    buys = {row['trade_id']:row for row in fills if row['action']=='BUY'}
    exits = {row['trade_id']:row for row in fills if row['action']!='BUY'}
    sessions = list(data.window.calendar.sessions)
    details = []
    for trade in saved['trades']:
        trade = dict(trade)
        entry, exit_time = stamp(trade['entry_time']), stamp(trade['exit_time'])
        held = bars.loc[(bars.index >= entry) & (bars.index < exit_time)]
        assert not held.empty
        observations = []
        for when, row in held.iterrows():
            observations.extend([(when.isoformat(),'OPEN',float(row.Open)),
                                 (row.bar_end.isoformat(),'CLOSE',float(row.Close))])
        observations.append((exit_time.isoformat(),'EXIT_OPEN',trade['exit_price']))
        peak = max(observations,key=lambda row:row[2])
        trough = min(observations,key=lambda row:row[2])
        peak_profit = round(peak[2]*trade['shares']-trade['entry_cost'],2)
        trough_profit = round(trough[2]*trade['shares']-trade['entry_cost'],2)
        costs = trade['entry_commission']+trade['exit_commission']+trade['transaction_tax']
        gross = round((trade['exit_price']-trade['entry_price'])*trade['shares'],2)
        assert abs(gross-costs-trade['net_profit']) < 0.00001
        entry_info, exit_info = buys[trade['trade_id']], exits[trade['trade_id']]
        previous = details[-1] if details else None
        gap_sessions = sessions.index(entry.date())-sessions.index(stamp(previous['exit_time']).date()) if previous else None
        flat_gap_bars = int(bars.index.get_loc(entry)-bars.index.get_loc(stamp(previous['exit_time']))) if previous else None
        trade.update(holding_calendar_hours=(exit_time-entry).total_seconds()/3600,
            holding_bar_count=len(held), holding_trading_sessions=len(set(held.index.date)),
            holding_market_minutes=int(held.duration_minutes.sum()),
            same_calendar_day=entry.date()==exit_time.date(),
            entry_reason=entry_info['decision_reason'], exit_reason=exit_info['decision_reason'],
            entry_signal_close=entry_info['signal_close'], entry_signal_ma60=entry_info['signal_ma60'],
            entry_signal_gap_percent=entry_info['signal_close_vs_ma60_percent'],
            entry_execution_open_vs_known_ma60_percent=entry_info['execution_open_vs_known_ma60_percent'],
            entry_execution_gap_vs_signal_close_percent=entry_info['execution_gap_vs_signal_close_percent'],
            entry_ma60_slope_percent=entry_info['signal_ma60_slope_percent'],
            entry_ma60_5_bar_slope_percent=entry_info['signal_ma60_5_bar_slope_percent'],
            exit_signal_close=exit_info['signal_close'], exit_signal_ma60=exit_info['signal_ma60'],
            exit_signal_gap_percent=exit_info['signal_close_vs_ma60_percent'],
            gross_price_profit=gross,total_costs=round(costs,2),
            peak_mark_price=peak[2], peak_mark_at=peak[0], peak_mark_phase=peak[1],
            trough_mark_price=trough[2], trough_mark_at=trough[0],
            peak_mark_profit=peak_profit, maximum_favorable_mark_percent=max(0,peak_profit)/trade['entry_cost']*100,
            maximum_adverse_mark_percent=min(0,trough_profit)/trade['entry_cost']*100,
            price_giveback=round((peak[2]-trade['exit_price'])*trade['shares'],2),
            peak_to_realized_giveback=round(peak_profit-trade['net_profit'],2),
            profit_giveback_percent=(peak_profit-trade['net_profit'])/peak_profit*100 if peak_profit>0 else None,
            positive_mark_profit_to_net_loss=peak_profit>0 and trade['net_profit']<0,
            never_positive_mark_profit=peak_profit<=0,
            previous_trade_id=previous['trade_id'] if previous else None,
            reentry_gap_sessions=gap_sessions, reentry_gap_bars=flat_gap_bars,
            reentry_gap_calendar_hours=(entry-stamp(previous['exit_time'])).total_seconds()/3600 if previous else None,
            same_day_reentry=bool(previous and entry.date()==stamp(previous['exit_time']).date()),
            rapid_reentry=previous is not None and gap_sessions<=3,
            reentry_price_vs_previous_exit_percent=(trade['entry_price']/previous['exit_price']-1)*100 if previous else None)
        assert abs(trade['holding_days']-(exit_time-entry)/pd.Timedelta(days=1))<1e-10
        details.append(trade)
    return details


def equity_analysis(saved, trades):
    curve = saved['equity_curve']
    fill_map = {fill['execution_time']:fill for fill in saved['fills']}
    ledger, per_trade = [], defaultdict(Decimal)
    active, previous = None, Decimal(str(saved['config']['initial_cash']))
    for i, point in enumerate(curve):
        fill = fill_map.get(point['at']) if point['phase']=='OPEN' else None
        assigned = active
        if fill:
            assigned = fill['trade_id']
            active = fill['trade_id'] if fill['action']=='BUY' else None
        change = Decimal(str(point['equity']))-previous
        previous = Decimal(str(point['equity']))
        if assigned is not None:
            per_trade[assigned] += change
        else:
            assert change == 0
        ledger.append(dict(index=i,**point,trade_id=assigned,equity_change=float(change)))
    for trade in trades:
        assert abs(float(per_trade[trade['trade_id']])-trade['net_profit'])<0.00001
    peak, peak_i, biggest, peak_at_biggest, trough_i = saved['config']['initial_cash'], None, 0, None, None
    for i, point in enumerate(curve):
        if point['equity']>peak:
            peak,peak_i=point['equity'],i
        dd=(peak-point['equity'])/peak*100
        if dd>biggest:
            biggest,peak_at_biggest,trough_i=dd,peak_i,i
    assert abs(biggest-saved['summary']['max_drawdown_percent'])<1e-9
    peak_point=curve[peak_at_biggest] if peak_at_biggest is not None else dict(
        at=curve[0]['at'],phase='INITIAL',equity=saved['config']['initial_cash'],cash=saved['config']['initial_cash'],shares=0)
    trough=curve[trough_i]
    contribution=defaultdict(Decimal)
    for row in ledger[(peak_at_biggest+1 if peak_at_biggest is not None else 0):trough_i+1]:
        if row['trade_id'] is not None:
            contribution[row['trade_id']]+=Decimal(str(row['equity_change']))
    assert abs(float(sum(contribution.values()))-(trough['equity']-peak_point['equity']))<0.00001
    recovery=next((point for point in curve[trough_i+1:] if point['equity']>=peak_point['equity']),None)
    monthly={}
    for point in curve:
        monthly[point['at'][:7]]=point
    annual_peak=max(curve,key=lambda point:point['equity'])
    return dict(max_drawdown_percent=biggest,peak=peak_point,trough=trough,
        peak_to_trough_loss=round(peak_point['equity']-trough['equity'],2),
        elapsed_calendar_days=float((stamp(trough['at'])-stamp(peak_point['at']))/pd.Timedelta(days=1)),
        recovery=recovery, recovered_by_end=recovery is not None,
        drawdown_trade_contributions=[dict(trade_id=k,equity_change=float(v),
            full_trade_net_profit=next(t['net_profit'] for t in trades if t['trade_id']==k))
            for k,v in sorted(contribution.items(),key=lambda item:item[1])],
        month_end_equity=[dict(month=month,at=point['at'],equity=point['equity'],
            return_from_initial_percent=(point['equity']/saved['config']['initial_cash']-1)*100,
            realized_profit=round(sum(t['net_profit'] for t in trades if t['exit_time'].startswith(month)),2))
            for month,point in monthly.items()],
        annual_equity_peak=annual_peak,peak_profit=annual_peak['equity']-saved['config']['initial_cash'],
        peak_to_end_giveback=round(annual_peak['equity']-saved['summary']['ending_equity'],2),
        ledger=ledger)


def summarize(saved, data, trades, fills, raw_outcomes):
    distribution=[]
    for lo,hi,label in [(0,1,'<1'),(1,3,'1-<3'),(3,7,'3-<7'),(7,14,'7-<14'),(14,30,'14-<30'),(30,float('inf'),'>=30')]:
        rows=[t for t in trades if lo<=t['holding_days']<hi]
        distribution.append(dict(calendar_days=label,**performance(rows)))
    session_distribution=[]
    for lo,hi,label in [(1,2,'1'),(2,4,'2-3'),(4,6,'4-5'),(6,11,'6-10'),(11,21,'11-20'),(21,float('inf'),'>20')]:
        session_distribution.append(dict(exposed_sessions=label,**performance([t for t in trades if lo<=t['holding_trading_sessions']<hi])))
    streaks,current=[],[]
    for trade in trades:
        if trade['net_profit']<0:
            current.append(trade)
        elif current:
            streaks.append(current)
            current=[]
    if current:
        streaks.append(current)
    loss_streaks=[dict(first_trade_id=s[0]['trade_id'],last_trade_id=s[-1]['trade_id'],
        first_entry=s[0]['entry_time'],last_exit=s[-1]['exit_time'],**performance(s)) for s in streaks]
    daily_actions=defaultdict(list)
    for row in fills:
        daily_actions[row['execution_time'][:10]].append(row)
    busy_days=[dict(date=day,fill_count=len(rows),buys=sum(r['action']=='BUY' for r in rows),
        sells=sum(r['action']=='SELL' for r in rows),exits=sum(r['action']=='EXIT' for r in rows),
        actions=[dict(action=r['action'],time=r['execution_time'],price=r['execution_open'],trade_id=r['trade_id']) for r in rows])
        for day,rows in sorted(daily_actions.items()) if len(rows)>=3]
    clusters,current=[],[]
    for trade in trades:
        if current and not trade['rapid_reentry']:
            if len(current)>1:
                clusters.append(current)
            current=[]
        current.append(trade)
    if len(current)>1:
        clusters.append(current)
    groups={
        'same_calendar_day':[t for t in trades if t['same_calendar_day']],
        'up_to_3_exposed_sessions':[t for t in trades if t['holding_trading_sessions']<=3],
        'over_3_exposed_sessions':[t for t in trades if t['holding_trading_sessions']>3],
        'same_day_reentry':[t for t in trades if t['same_day_reentry']],
        'rapid_reentry_within_3_session_gaps':[t for t in trades if t['rapid_reentry']],
        'other_entries':[t for t in trades if not t['rapid_reentry']],
        'entry_ma60_nonrising':[t for t in trades if t['entry_ma60_slope_percent']<=0],
        'entry_ma60_rising':[t for t in trades if t['entry_ma60_slope_percent']>0],
        'entry_open_at_or_below_last_completed_ma60':[t for t in trades if t['entry_execution_open_vs_known_ma60_percent']<=0],
        'positive_mark_profit_to_net_loss':[t for t in trades if t['positive_mark_profit_to_net_loss']],
        'never_positive_mark_profit':[t for t in trades if t['never_positive_mark_profit']],
        'gross_price_winner_but_net_loser':[t for t in trades if t['gross_price_profit']>=0 and t['net_profit']<0],
        'exit_sell':[t for t in trades if t['exit_action']=='SELL'],
        'exit_daily_trend':[t for t in trades if t['exit_action']=='EXIT']}
    reentry_buckets=[]
    for label,test in [('same_session',lambda x:x==0),('next_session',lambda x:x==1),
                       ('2-3_sessions',lambda x:2<=x<=3),('4-10_sessions',lambda x:4<=x<=10),
                       ('over_10_sessions',lambda x:x>10)]:
        rows=[t for t in trades if t['reentry_gap_sessions'] is not None and test(t['reentry_gap_sessions'])]
        reentry_buckets.append(dict(gap=label,**performance(rows)))
    best=max(trades,key=lambda t:t['net_profit'])
    return dict(original_summary=saved['summary'],all_trades=performance(trades),
        holding=dict(mean_calendar_days=mean(t['holding_days'] for t in trades),
            median_calendar_days=median(t['holding_days'] for t in trades),
            min_calendar_days=min(t['holding_days'] for t in trades),max_calendar_days=max(t['holding_days'] for t in trades),
            calendar_day_quantiles={str(q):float(np.quantile([t['holding_days'] for t in trades],q)) for q in (.25,.5,.75,.9)},
            median_exposed_sessions=median(t['holding_trading_sessions'] for t in trades),
            median_bars=median(t['holding_bar_count'] for t in trades),
            market_exposure_percent=sum(t['holding_market_minutes'] for t in trades)/(len(data.window.sessions)*270)*100,
            calendar_day_distribution=distribution,session_distribution=session_distribution),
        groups={name:performance(rows) for name,rows in groups.items()},
        reentry_gap_distribution=reentry_buckets,
        counts=dict(filled_actions=dict(Counter(row['action'] for row in fills)),
            raw_actions=dict(Counter(row['signal_type'] for row in raw_outcomes)),
            raw_outcome_reasons=dict(Counter(row['reason'] for row in raw_outcomes)),
            days_with_3_or_more_fills=len(busy_days),days_with_multiple_buys=sum(v['buys']>=2 for v in busy_days),
            same_day_round_trips=len(groups['same_calendar_day']),same_day_reentry_pairs=len(groups['same_day_reentry']),
            rapid_reentry_pairs=len(groups['rapid_reentry_within_3_session_gaps'])),
        busy_days=busy_days,loss_streaks=loss_streaks,
        longest_loss_streak=max(loss_streaks,key=lambda s:s['count']),
        worst_loss_streak=max(loss_streaks,key=lambda s:-s['net_profit']),
        rapid_reentry_clusters=[dict(first_trade_id=s[0]['trade_id'],last_trade_id=s[-1]['trade_id'],
            entry=s[0]['entry_time'],exit=s[-1]['exit_time'],**performance(s),
            reentries_only=performance(s[1:])) for s in clusters],
        best_trade=best,best_return_trade=max(trades,key=lambda t:t['return_percent']),
        worst_trade=min(trades,key=lambda t:t['net_profit']),
        worst_return_trade=min(trades,key=lambda t:t['return_percent']),
        largest_giveback_trade=max(trades,key=lambda t:t['peak_to_realized_giveback']),
        others_excluding_best=performance([t for t in trades if t['trade_id']!=best['trade_id']]),
        total_bought_notional=sum(row['execution_open']*row['shares'] for row in fills if row['action']=='BUY'),
        buy_turnover_multiple=sum(row['execution_open']*row['shares'] for row in fills if row['action']=='BUY')/saved['config']['initial_cash'])


def render_report(artifact):
    symbols=artifact['symbols']
    def number(value,places=2):
        return 'N/A' if value is None else f'{value:,.{places}f}'
    def when(value):
        return stamp(value).strftime('%Y-%m-%d %H:%M')
    def table(headers,rows):
        return ['| '+' | '.join(headers)+' |','|'+'|'.join('---' for _ in headers)+'|',
                *['| '+' | '.join(map(str,row))+' |' for row in rows],'']
    lines=['# 666_strategy 完整一年 Fugle 交易行為分析', '',
        '期間：2025-10-30～2026-10-29。依已保存的兩份真實 Fugle 年度回測、行情快照與決策事件分析；'
        '不修改策略、參數、Provider、成交機制、回測功能或原台股助手，不執行替代策略或參數搜尋。', '',
        '## 主要判斷', '',
        '現有行為較接近「日線多頭過濾下，依 60 分 K 均線反覆進出的混合策略」。它能抓住少數持續趨勢，'
        '但大多數交易僅曝險數根至數日，與穩定持有一段波段的目標並不一致。問題不是沒有大賺交易，'
        '而是短持倉、重新進場和浮盈回吐反覆消耗少數大趨勢獲利。以下分組是描述性診斷，不等於已證明修改某條規則會改善績效。', '',
        '## 口徑與檔案', '',
        '- 每檔獨立本金 100 萬元；保留原費稅、整數股數及下一根實際交易棒 Open 成交。損益均為扣費稅後金額，沒有合併投資組合績效。',
        '- 持倉曆日包含隔夜及休市；曝險交易日只計實際持有至少一根完整交易棒的日期，若次日 09:00 賣出，次日不計曝險交易日。另列實際持有棒數與市場分鐘。',
        '- 同日來回：買、賣成交在同一台北日期。短持倉：曝險不超過 3 個交易日。快速再進場：下一筆 BUY 與上筆出場日期最多跨 3 個已確認交易日，同日算 0。這些固定分組只用於統計，不是策略參數。',
        '- 同日再進場不一定等於當日有三次以上成交：可能先賣出隔日持倉，再買一次。分組彼此重疊，不能把各組損益相加當成互斥的因果歸因。',
        '- 浮盈／不利變動取持有期間原生棒 Open、完成 Close 與實際出場 Open 的價格標記，扣已付買進費用；峰值標記不預扣假想賣出費稅。實現損益已扣全部費稅，因此峰值至實現回吐包含賣出費稅。不是棒內 High/Low，也不代表能在峰值成交。',
        '- 訊號 MA60 使用當根完成棒；成交時僅比較當時最後一根已完成棒的 MA60，不使用成交棒未完成的 MA。所有時間為 Asia/Taipei。', '',
        '[完整統計 JSON](analysis.json) · [驗證記錄](validation.json) · [既有檔案 SHA-256 基準](protected_baseline.json)', '']
    for symbol in symbols:
        lines += [f"{symbol}：[逐筆交易診斷]({symbol}_trade_diagnostics.csv) · [全部成交觸發]({symbol}_fill_triggers.csv) · "
            f"[含未成交原始訊號]({symbol}_raw_signal_outcomes.csv) · [權益逐點歸屬]({symbol}_equity_attribution.csv) · [月末權益]({symbol}_monthly_equity.csv)", '']
    labels=[('交易數','count'),('獲利筆數','wins'),('虧損筆數','losses'),('勝率（%）','win_rate_percent'),
        ('平均獲利（元）','average_win'),('平均虧損（元）','average_loss'),('獲利中位數（元）','median_win'),
        ('虧損中位數（元）','median_loss'),('平均獲利單筆報酬（%）','average_win_return_percent'),
        ('平均虧損單筆報酬（%）','average_loss_return_percent'),('平均盈虧比','payoff_ratio'),
        ('Profit factor：獲利總額／虧損總額絕對值','profit_factor'),('每筆平均淨損益（元）','average_net_profit'),
        ('固定實際股數的價差損益（元）','gross_price_profit'),('全部費稅（元）','total_costs'),('年度淨損益（元）','net_profit')]
    lines += ['## 一、持倉分布、交易頻率與平均盈虧', '']
    lines += table(['指標','2330','2408'],[[label,*[number(symbols[s]['summary']['all_trades'][key],4 if key=='profit_factor' else 2) for s in symbols]] for label,key in labels])
    lines += ['價差損益減費稅精確等於保存的淨損益。這不是零費率重跑績效：若真改費率，後續現金與股數也會改變。'
        '2330 費稅大於全部價差淨收益；2408 的費稅消耗約 95.25% 價差淨收益，最後僅剩 10,292.89 元。', '',
        '### 持倉時間', '']
    rows=[]
    for label,key in [('平均持倉曆日','mean_calendar_days'),('持倉曆日中位數','median_calendar_days'),
                      ('最短持倉曆日','min_calendar_days'),('最長持倉曆日','max_calendar_days'),
                      ('曝險交易日中位數','median_exposed_sessions'),('持有棒數中位數','median_bars'),
                      ('交易時段曝險比例（%）','market_exposure_percent')]:
        rows.append([label,*[number(symbols[s]['summary']['holding'][key],4) for s in symbols]])
    lines+=table(['指標','2330','2408'],rows)
    rows=[]
    for i in range(6):
        a,b=[symbols[s]['summary']['holding']['calendar_day_distribution'][i] for s in symbols]
        rows.append([a['calendar_days'],a['count'],number(a['net_profit']),b['count'],number(b['net_profit'])])
    lines+=table(['持倉曆日區間','2330 筆數','2330 淨損益','2408 筆數','2408 淨損益'],rows)
    lines+=['小於 1 個曆日包含隔夜但未滿 24 小時的交易，不能直接當成同日當沖。平均值被少數長抱交易拉高，應同時看中位數。', '']
    rows=[]
    for i in range(6):
        a,b=[symbols[s]['summary']['holding']['session_distribution'][i] for s in symbols]
        rows.append([a['exposed_sessions'],a['count'],number(a['net_profit']),b['count'],number(b['net_profit'])])
    lines+=table(['實際曝險交易日','2330 筆數','2330 淨損益','2408 筆數','2408 淨損益'],rows)
    rows=[]
    for label,key in [('同日完成來回交易','same_day_round_trips'),('出場後同日重新進場','same_day_reentry_pairs'),
                      ('3 個交易日間隔內重新進場','rapid_reentry_pairs'),('同日有至少 3 筆成交的日期','days_with_3_or_more_fills'),
                      ('同日有至少 2 次 BUY 的日期','days_with_multiple_buys')]:
        rows.append([label,*[symbols[s]['summary']['counts'][key] for s in symbols]])
    lines+=table(['反覆交易定義','2330 次／日數','2408 次／日數'],rows)
    lines+=table(['固定分組（可重疊）','2330 筆數／虧損筆數','2330 淨損益','2408 筆數／虧損筆數','2408 淨損益'],
        [[label,*[value for s in symbols for g in [symbols[s]['summary']['groups'][key]]
                     for value in [f"{g['count']}／{g['losses']}",number(g['net_profit'])]]]
         for label,key in [('同日來回','same_calendar_day'),('曝險 ≤3 交易日','up_to_3_exposed_sessions'),
            ('曝險 >3 交易日','over_3_exposed_sessions'),('同日重新進場','same_day_reentry'),
            ('3 交易日間隔內重新進場','rapid_reentry_within_3_session_gaps'),
            ('曾有正浮盈、最後淨虧損','positive_mark_profit_to_net_loss'),
            ('觀察標記從未有正浮盈','never_positive_mark_profit')]])
    lines+=['這裡的「短持倉虧損較多」有事後選擇偏差：失敗交易本來就可能因 SELL 很快結束，'
        '不能據此推論強迫持滿數日就會變好。兩檔都有快速再進場後獲利的案例，不能把所有重新進場視為錯誤。', '',
        '## 二、全部 BUY／SELL／EXIT 的觸發解讀', '',
        'BUY：前一完成棒 Close ≤ 前一 MA60，當根完成 Close > 當根 MA60，且當時完成日線三條件全為真；'
        '成交前再次檢查日線 BULL。SELL：前一 Close ≥ 前一 MA60，當根 Close < 當根 MA60，持倉才可賣出，不要求日線轉弱。'
        'EXIT：已完成日線變成 NON_BULL，優先於同時的 SELL。所有成交仍在下一根實際交易棒 Open，跨休市也不提前。', '']
    lines+=table(['行為','2330','2408'],[
        ['實際 BUY',32,26],['實際 SELL',31,26],['實際 EXIT',1,0],
        ['日線 NON_BULL 阻擋的原始 BUY',7,9],['持倉中忽略的原始 BUY',1,0],['空手時的原始 SELL',8,9]])
    lines+=['2330 唯一 EXIT：2026-09-02 13:30，日收盤 2,385 低於日 MA60 2,386.75；'
        '日 MA60 仍高於日 MA120 2,243.7917，且仍高於五個交易日前 2,383.25。只第一個多頭條件失效，'
        '並不需要三條件全部失效。當時分 K Close 2,385 也低於 MA60 2,393.1667，原始 SELL 同時成立，'
        '但由 EXIT 優先；9/3 09:00 以 2,385 元出場，該筆淨虧損 8,723.81 元。2408 全年沒有實際 EXIT，出場全由分 K SELL 主導。', '',
        '### 確認價與成交價不同的進場', '']
    lines+=table(['指標','2330','2408'],[
        ['BUY Open 已在最後已完成 MA60 以下或相等的筆數',*[symbols[s]['summary']['groups']['entry_open_at_or_below_last_completed_ma60']['count'] for s in symbols]],
        ['這組淨損益',*[number(symbols[s]['summary']['groups']['entry_open_at_or_below_last_completed_ma60']['net_profit']) for s in symbols]]])
    lines+=['例如 2408 #24 在 9/24 13:30 以 Close 503 > MA60 501.75 確認 BUY，休市後 9/29 09:00 實際 Open 491，'
        '已低於當時已知 MA60。既有規則重查的是日線多頭，而不是要求成交 Open 仍站上分 K MA60，所以照常買進。'
        '此筆最後反而獲利 17,458.66 元；此現象值得定義風險，但不能直接認定應增加價格重驗條件。', '',
        '## 三、頻繁進出虧損：進場、出場與重新進場', '',
        '### A. 進場問題：單次穿越可以只是震盪反彈', '',
        '2330 有 14 筆、2408 有 5 筆在持倉期間觀察標記從未出現扣買進費後的正浮盈，'
        '分別合計虧損 181,068.24 與 177,935.19 元。這些較像進場後沒有趨勢延續，'
        '但只使用 Open／Close 標記，不能說棒內絕對從未浮盈。日線 BULL 是較慢的方向濾網，並不保證短線每次突破有效。', '',
        '具體案例：2330 2026-03-25。日線仍是 BULL，但分 K 在 MA60 附近來回穿越：', '']
    cases=[row for row in symbols['2330']['fills'] if row['trade_id'] in (12,13)]
    lines+=table(['交易／動作','確認時間','前棒 Close／MA60','確認 Close／MA60','成交時間／Open'],[
        [f"#{r['trade_id']} {r['action']}",when(r['trigger_time']),f"{r['previous_close']:g}／{r['previous_ma60']:.4f}",
         f"{r['signal_close']:g}／{r['signal_ma60']:.4f}",f"{when(r['execution_time'])}／{r['execution_open']:g}"] for r in cases])
    lines+=['#12：1,875→1,860、462 股、虧損 11,966.90 元；#13：1,865→1,860、458 股、虧損 7,276.76 元。'
        '兩筆合計價差虧損 9,220 元、費稅 10,023.66 元，淨虧損 19,243.66 元。'
        '第二次買進是一根新完成棒再次上穿，並非重複執行同一訊號；但市場延續性沒有改善。', '',
        '### B. 出場問題：保留趨勢的同時也容許大量浮盈回吐', '',
        '2330 的 12 筆、2408 的 14 筆曾有正標記浮盈，最後轉成淨虧損，合計各 −197,121.06、−979,783.72 元。'
        '這些與快速再進場組可能重疊；浮盈很小的交易也可能純粹被費稅翻成虧損。不能將全部金額都歸因於「賣得太晚」。', '']
    rows=[]
    for s in symbols:
        t=symbols[s]['summary']['largest_giveback_trade']
        rows.append([f"{s} #{t['trade_id']}",when(t['entry_time']),t['entry_price'],
            f"{when(t['peak_mark_at'])}／{t['peak_mark_price']:g}",number(t['peak_mark_profit']),
            f"{when(t['exit_time'])}／{t['exit_price']:g}",number(t['net_profit']),number(t['profit_giveback_percent'])+'%'])
    lines+=table(['最大金額回吐案例','進場時間','買價','峰值標記時間／價格','峰值浮盈','出場時間／價格','實現損益','浮盈回吐比例'],rows)
    lines+=['2330 #18 出場確認時 Close 2,355 < MA60 2,371.50，仍依原 SELL 出場；先前標記浮盈 64,445.72 元並未觸發任何獨立保護。'
        '2408 #10 在 430 元標記後，直到 6/8 Close 338.5 < MA60 346.7417 才確認 SELL，以 339 元成交；'
        '雖最後仍賺 75,958.54 元，已回吐 82.92% 峰值浮盈。這描述風險取捨，並非宣稱能事先知道 430 元是頂部。', '',
        '### C. 重新進場問題：前次失敗不會降低下一次有效 BUY 的部位', '',
        '原策略沒有冷卻、失敗次數或虧損後減少部位的條件；只要新的穿越成立且日線 BULL，就依可用現金買入。'
        '快速再進場 2330 為 22 筆／18 虧、淨 −199,174.89 元；2408 為 18 筆／12 虧、淨 −401,324.11 元。', '',
        '2408 連續案例：', '']
    lines+=table(['交易','買進','賣出','價位','股數','淨損益'],[
        [f"#{t['trade_id']}",when(t['entry_time']),when(t['exit_time']),f"{t['entry_price']:g}→{t['exit_price']:g}",t['shares'],number(t['net_profit'])]
        for t in symbols['2408']['trades'] if t['trade_id'] in (4,5,6)])
    lines+=['#4 於 2/26 11:00 賣出後，13:00 以更高價格再買 #5；#5 在 3/2 10:00 以 281 元賣出後，'
        '11:00 即以 287.5 元買回 #6，比前次賣價高 2.31%。#6 次日 262 元平倉，損失 156,567.31 元，'
        '是 2408 全年最大金額虧損。#6 的 BUY 確認 287.5 > MA60 282.2333，SELL 確認 262 < MA60 281.3583，'
        '不是條件算錯，而是同一多頭背景內的短線突破再次失敗。', '',
        '反例也必須保留：2408 #25 在 9/29 同日買回，後來獲利 34,246.92 元。'
        '此外，兩檔最大獲利交易的進場時，分 K MA60 單棒斜率都不是上升；不能僅憑虧損案例就主張加「MA60 上彎才買」一定有效。', '',
        '## 四、最大獲利、連虧與最大回撤', '', '### 最大獲利交易與集中度', '']
    rows=[]
    for s in symbols:
        t=symbols[s]['summary']['best_trade']
        rows.append([f"{s} #{t['trade_id']}",when(t['entry_time']),when(t['exit_time']),
            f"{t['entry_price']:g}→{t['exit_price']:g}",t['shares'],number(t['holding_days'],4),
            number(t['net_profit']),number(t['return_percent'],4)+'%',number(symbols[s]['summary']['others_excluding_best']['net_profit'])])
    lines+=table(['交易','買進','賣出','買賣價','股數','持倉曆日','淨獲利','單筆報酬','其餘交易合計損益'],rows)
    lines+=['「其餘交易合計」只是將原記錄相加，沒有刪除交易後重跑資金路徑。'
        '2408 的平均獲利 166,858.83 元受到首筆 839,170.19 元明顯拉高；其餘 25 筆合計 −828,877.30 元。'
        '這顯示獲利高度集中，但兩檔一個年度的樣本不足以證明長期沒有趨勢交易價值。', '',
        '### 最長連續虧損', '']
    lines+=table(['股票','連虧交易編號','首次進場至最後出場','連虧筆數','合計淨虧損'],[
        [s,f"#{v['first_trade_id']}～#{v['last_trade_id']}",f"{when(v['first_entry'])}～{when(v['last_exit'])}",v['count'],number(v['net_profit'])]
        for s in symbols for v in [symbols[s]['summary']['longest_loss_streak']]])
    lines+=['連虧是依完成交易的先後次序計，不代表每天都有交易。全部連虧段與快速再進場群集列在 analysis.json。', '',
        '### 最大回撤：區分整筆交易獲利與峰值後損失', '']
    lines+=table(['股票','權益峰值時間／金額','谷底時間／金額','峰谷損失','最大回撤','期末恢復原峰值'],[
        [s,f"{when(e['peak']['at'])}／{number(e['peak']['equity'])}",
         f"{when(e['trough']['at'])}／{number(e['trough']['equity'])}",number(e['peak_to_trough_loss']),
         number(e['max_drawdown_percent'],4)+'%','是' if e['recovered_by_end'] else '否'] for s in symbols for e in [symbols[s]['equity']]])
    lines+=['2330：最大獲利 #14 在 5/7 曾使帳戶達 1,077,851.20 元；其後到該筆出場就減少 55,248.84 元，'
        '再加後續交易合計 −173,313.69 元，到 10/14 降至 849,288.67 元。'
        '#14 整筆仍賺 174,045.70 元，所以不能把整筆淨獲利當成峰值後也在賺錢。', '',
        '2408：1/30 仍持有第一筆 6,657 股，以 326.5 元標記時帳戶達 2,173,537.57 元；'
        '到 2/3 以 277.5 元出場，先回落 334,367.38 元，現金剩 1,839,170.19 元。'
        '後續 25 筆再淨減少 828,877.30 元，10/13 到 1,010,292.89 元，構成 53.52% 最大回撤。'
        '帳戶全年仍有 +1.03% 與曾承受 −53.52% 回撤可以同時成立，兩者分母不同。', '']
    rows=[]
    for s in symbols:
        for r in symbols[s]['equity']['drawdown_trade_contributions'][:5]:
            rows.append([s,f"#{r['trade_id']}",number(r['equity_change']),number(r['full_trade_net_profit'])])
    lines+=table(['股票','最大回撤區段主要拖累交易','只計峰值後至谷底的權益變化','該筆完整淨損益'],rows)
    lines+=['上表只列各檔五項主要拖累，完整歸屬及精確加總在 JSON／權益逐點 CSV。'
        '回撤計算沿用保存的初始資金、每根 Open 成交後及完成 Close 權益，不推測棒內路徑。', '',
        '### 月末權益軌跡', '']
    monthly={s:{r['month']:r for r in symbols[s]['equity']['month_end_equity']} for s in symbols}
    lines+=table(['月份','2330 月末權益','2408 月末權益'],[[m,*[number(monthly[s][m]['equity']) for s in symbols]] for m in monthly['2330']])
    lines+=['## 五、是否符合波段定位，以及只供後續研究的方向', '',
        '60 根 60 分 K 在每天五根的設定下約為 12 個交易日的均線，不是 60 日均線。'
        '日線 MA60／MA120 是方向背景，實際出場主要由更短週期穿越主導。'
        '2330 有 24/32（75%）、2408 有 19/26（73.08%）交易曝險不超過三個交易日；'
        '中位持倉分別約 22.5 小時與 2.5 曆日，曝險交易日中位數均為 2。'
        '這些觀察不支持把目前行為描述成穩定、低換手的數週波段系統。少數 35 日／63 日的成功交易說明它能抓波段，'
        '但策略還沒有可靠區分「值得持有的趨勢」與「均線附近反覆穿越」。', '',
        '| 後續研究方向（本次均未實作） | 對應證據 | 必須同時檢查的代價 |',
        '|---|---|---|',
        '| 先定義波段目標及可接受持倉／換手行為，再檢驗進出場週期是否一致 | 多數只持有數根至數日；長線日線濾網仍容許短線反覆交易 | 不能只為了拉長持倉而延後必要的風險出場 |',
        '| 研究震盪盤識別、突破確認或訊號與次棒成交價的關係 | 3/25 雙重穿越；7 筆 BUY 成交價已不在已知 MA60 上方 | 增加限制可能漏掉趨勢起點；成交價低於 MA 的交易也有獲利 |',
        '| 將重新進場視為獨立研究問題，而非每次上穿都與首次進場同等處理 | 快速再進場組淨損益均為負；2408 #5→#6 高價買回 | 9/29 同日再進場有成功案例，硬性冷卻可能漏掉延續行情 |',
        '| 評估浮盈保護與容忍回檔的取捨 | 2330 #18 由正浮盈轉虧；2408 #10 回吐 82.92% 峰值浮盈 | 事後峰值不可預知；更早出場可能砍掉 35／63 日的大贏家 |',
        '| 研究連虧與集中部位的風險承擔是否符合定位 | 連虧 10／8 筆；2408 回撤 53.52%，原模型每次使用大部分可用現金 | 降低風險也可能降低大贏家貢獻；不能只看回撤下降而忽略獲利下降 |',
        '| 以其他期間與股票作獨立驗證，納入真實可成交性與成本檢查 | 此處只有兩檔、單一年，且現有模型滑價為 0 | 不可拿本年度反覆挑條件，再把同年度改善當成泛化能力 |', '',
        '這些是依觀察提出的研究假設，不是推薦參數、買賣建議或已驗證改良。本次未指定新門檻，'
        '未計算任何修改後策略績效，也未移除虧損交易後重新回測。', '',
        '## 六、逐筆觸發附錄', '',
        '完整 CSV 含股數、前棒與當棒 MA、確認價與成交價差、日線三條件、決策理由、費稅與淨損益。'
        '下表覆蓋全部 116 筆實際成交；確認時間指訊號／日線觸發時間，不是成交棒尚未完成的收盤。'
        '理由簡寫：BUY=原始上穿且日線 BULL；SELL=持倉中的原始下穿；EXIT=日線失效。', '']
    for s in symbols:
        lines += [f'### {s}', '']
        lines += table(['交易／動作','確認時間','前棒 Close／MA60','確認 Close／MA60','日線狀態','成交時間／Open'],[
            [f"#{r['trade_id']} {r['action']}",when(r['trigger_time']),
             f"{r['previous_close']:g}／{r['previous_ma60']:.4f}",f"{r['signal_close']:g}／{r['signal_ma60']:.4f}",
             r['signal_daily_status'],f"{when(r['execution_time'])}／{r['execution_open']:g}"] for r in symbols[s]['fills']])
    lines += ['## 七、驗證與重現', '',
        '核對全部 58 筆來回交易、116 筆實際成交、150 筆含未成交的原始訊號；逐筆檢查穿越、日線條件、'
        '次棒 Open、成交當時可用 MA、費稅及持倉時間。每一個權益增減精確歸屬交易，'
        '各筆加總等於保存淨損益，回撤區段貢獻也等於峰谷權益差。198 個既有檔案 SHA-256 核對不變，'
        '包含 Fugle／Yahoo Provider、策略與回測程式、原助手、原始回測成果與測試。此次新增的是獨立分析腳本與此目錄產物。', '',
        '在台股目錄離線重現：', '', '```powershell',
        'python -B -X utf8 -m 666_strategy.verification.analyze_fugle_trades', '```', '',
        '原始年度回測： [2330](../fugle_backtest_2330_20251030_20261029.json)、[2408](../fugle_backtest_2408_20251030_20261029.json)。'
        '本次不讀取金鑰、不下載行情、不觸發下單。']
    (OUTPUT/'ANALYSIS_REPORT.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')


def main():
    OUTPUT.mkdir(parents=True,exist_ok=True)
    provider=FugleSnapshotProvider(HERE/'fugle_history_20251030_20261029.snapshot.json')
    results={}
    for symbol in ('2330','2408'):
        path=HERE/f'fugle_backtest_{symbol}_20251030_20261029.json'
        saved=json.loads(path.read_text(encoding='utf-8'))
        assert saved['start']=='2025-10-30' and saved['end']=='2026-10-29'
        assert saved['metadata']['source_sha256']==provider.metadata['source_sha256']
        data=fetch_backtest_data(symbol,saved['start'],saved['end'],provider=provider)
        fills,raw=analyze_fills(saved,data)
        trades=analyze_trades(saved,data,fills)
        summary=summarize(saved,data,trades,fills,raw)
        equity=equity_analysis(saved,trades)
        assert summary['all_trades']['net_profit']==saved['summary']['total_net_profit']
        assert summary['all_trades']['count']==saved['summary']['completed_trades']
        assert summary['all_trades']['wins']==saved['summary']['winning_trades']
        for distribution in (summary['holding']['calendar_day_distribution'],summary['holding']['session_distribution']):
            assert sum(row['count'] for row in distribution)==len(trades)
            assert abs(sum(row['net_profit'] for row in distribution)-saved['summary']['total_net_profit'])<0.00001
        write_csv(OUTPUT/f'{symbol}_fill_triggers.csv',fills)
        write_csv(OUTPUT/f'{symbol}_raw_signal_outcomes.csv',raw)
        write_csv(OUTPUT/f'{symbol}_trade_diagnostics.csv',trades)
        write_csv(OUTPUT/f'{symbol}_equity_attribution.csv',equity.pop('ledger'))
        write_csv(OUTPUT/f'{symbol}_monthly_equity.csv',equity['month_end_equity'])
        results[symbol]=dict(source_file=path.name,source_sha256=digest(path),summary=summary,
            equity=equity,trades=trades,fills=fills,raw_signals=raw)
        print(symbol,json.dumps(dict(trades=len(trades),fills=len(fills),
            median_holding_days=summary['holding']['median_calendar_days'],
            same_day_trades=summary['counts']['same_day_round_trips'],
            rapid_reentries=summary['counts']['rapid_reentry_pairs']),ensure_ascii=False),flush=True)
    artifact=dict(period=dict(start='2025-10-30',end='2026-10-29'),source='saved real Fugle data',
        snapshot_sha256=provider.metadata['source_sha256'],symbols=results,
        definitions=dict(short_holding='up to 3 exposed trading sessions',
            rapid_reentry='next BUY date minus previous exit date <=3 verified session transitions',
            same_day='same Asia/Taipei calendar date',
            mark_profit='held-bar Open/Close and actual exit Open, entry fee included; exit fees excluded until realization',
            overlapping_groups='descriptive groups overlap; do not add them as causal attribution',
            no_optimization=True,no_alternative_strategy_backtest=True,network_requests=0))
    (OUTPUT/'analysis.json').write_text(json.dumps(artifact,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
    # Exact identities validate attribution, costs and every actual trigger.
    checks=dict(passed=True,actual_trades_checked=sum(len(v['trades']) for v in results.values()),
        actual_fills_checked=sum(len(v['fills']) for v in results.values()),
        raw_signals_documented=sum(len(v['raw_signals']) for v in results.values()),
        checks=['all executed BUY/SELL crossings and daily conditions',
            'EXIT daily invalidation and next-bar Open', 'no future-bar MA at execution',
            'per-trade gross minus costs equals saved net profit',
            'holding duration matches saved results', 'full equity deltas reconcile to each trade',
            'drawdown and trade contributions reconcile to saved equity',
            'original artifact/snapshot identity and full required window'])
    baseline=OUTPUT/'protected_baseline.json'
    if baseline.exists():
        before=json.loads(baseline.read_text(encoding='utf-8'))
        changed=[name for name,expected in before.items() if digest(Path(__file__).resolve().parents[2]/name)!=expected]
        assert not changed,changed
        checks['protected_existing_files_unchanged']=len(before)
    (OUTPUT/'validation.json').write_text(json.dumps(checks,ensure_ascii=False,indent=2),encoding='utf-8')
    render_report(artifact)
    print('Analysis validation:',json.dumps(checks))


if __name__=='__main__':
    main()
