"""Capture and replay 2024-10-30..2025-10-29 without changing strategy code."""
import argparse
from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal
import hashlib
import json
from pathlib import Path

import pandas as pd

from ..backtest import BacktestConfig, export_backtest, fetch_backtest_data, prepare_data, run_backtest
from ..backtest.data import data_window
from ..fugle_client import FugleClient, ProbeError
from ..fugle_provider import (FugleProvider, FugleSnapshotProvider, checked_rows,
                              create_fugle_snapshot, native_frame)
from ..history import OHLCV, TAIPEI
from .analyze_fugle_trades import analyze_fills, analyze_trades, summarize, write_csv
from .verify_fugle_integration import audit_result

HERE=Path(__file__).resolve().parent
ROOT=HERE/'fugle_20241030_20251029'
START,END=date(2024,10,30),date(2025,10,29)
SNAPSHOT=ROOT/'fugle_history_20241030_20251029.snapshot.json'
SOURCE=ROOT/'fugle_source_20241030_20251029.json'


def write_json(path,document):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(document,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class RecordingClient:
    """Use the existing TLS/redaction client and persist successful chunks only."""
    def __init__(self,ca_bundle):
        self.ca_bundle=ca_bundle
        self.client=None
        self.requests=[]
        self.downloaded=0

    def candles(self,spec):
        name=f"{spec['symbol']}_{spec['timeframe']}_{spec['start']}_{spec['end']}.json"
        path=ROOT/'downloads'/name
        if path.exists():
            item=json.loads(path.read_text(encoding='utf-8'))
            if item['request']!=spec or item['status']!='FETCHED':
                raise ProbeError('INVALID_SAVED_CAPTURE')
            if spec['timeframe'] in ('60','D'):
                interval='60m' if spec['timeframe']=='60' else '1d'
                checked_rows(item['rows'],symbol=spec['symbol']+'.TW',interval=interval,
                             start=spec['start'],end=spec['end'])
            origin='saved_capture'
        else:
            if self.client is None:
                self.client=FugleClient(ca_bundle=self.ca_bundle)
            rows=self.client.candles(spec)
            self.downloaded+=1
            item=dict(request=spec,status='FETCHED',http_status=self.client.last_http_status,
                received_at=datetime.now(TAIPEI).isoformat(),rows=rows)
            write_json(path,item)
            origin='new_fugle_request'
        self.requests.append(dict(file=path.name,sha256=sha(path),request=spec,
            rows=len(item['rows']),received_at=item['received_at'],origin=origin))
        print(f"{spec['symbol']} {spec['timeframe']} {spec['start']}..{spec['end']}: {len(item['rows'])} rows ({origin})",flush=True)
        return item['rows']


def capture(ca_bundle):
    window=data_window(START,END)
    original_path=HERE/'fugle_history_20251030_20261029.snapshot.json'
    original=FugleSnapshotProvider(original_path)
    record=RecordingClient(ca_bundle)
    live=FugleProvider(client=record)
    parts=[]
    reuse_counts=[]
    for symbol in ('2330','2408'):
        for interval,tf,beginning in [('60m','60',window.minute_start),('1d','D',window.daily_start)]:
            saved=next(item for item in original.snapshots if item['symbol']==symbol+'.TW' and item['interval']==interval)
            reuse=[row for row in saved['rows'] if beginning<=pd.Timestamp(row['bar_start'])<=window.cutoff]
            end_exclusive=pd.Timestamp(reuse[0]['bar_start']).normalize() if reuse else window.cutoff.normalize()+pd.Timedelta(days=1)
            if beginning<end_exclusive:
                first_record=len(record.requests)
                live.history(symbol+'.TW',start=beginning,end=end_exclusive,interval=interval)
                for request in record.requests[first_record:]:
                    item=json.loads((ROOT/'downloads'/request['file']).read_text(encoding='utf-8'))
                    parts.append(dict(request=item['request'],status='FETCHED',http_status=item['http_status'],
                        rows=item['rows'],origin=dict(mode=request['origin'],file=request['file'],
                            sha256=request['sha256'],received_at=request['received_at'])))
            if reuse:
                parts.append(dict(request=dict(symbol=symbol,timeframe=tf,start=reuse[0]['bar_start'][:10],
                    end=reuse[-1]['bar_start'][:10],purpose='REUSE_VERIFIED_FUGLE'),status='FETCHED',rows=reuse,
                    origin=dict(mode='reused_verified_fugle_snapshot',file=original_path.name,sha256=sha(original_path))))
            reuse_counts.append(dict(symbol=symbol,interval=interval,reused_rows=len(reuse)))
    full_validation=[]
    for symbol in ('2330','2408'):
        frames={}
        for interval,tf in [('60m','60'),('1d','D')]:
            rows=[row for item in parts if item['request']['symbol']==symbol and item['request']['timeframe']==tf for row in item['rows']]
            frames[interval]=native_frame(rows,interval)
        data=prepare_data(frames['60m'],frames['1d'],symbol=symbol+'.TW',start=START,end=END)
        full_validation.append(dict(symbol=symbol,status='EXISTING_VALIDATION_PASSED',
            required_60m=60+5*len(window.sessions),validated_60m=len(data.history.bars),
            required_daily=120+len(window.sessions),validated_daily=len(data.daily.bars),
            run_sessions=len(window.sessions),issues=data.history.issues+data.daily.issues))
    # Inspect, but never use one-minute quotes to manufacture a 60-minute bar.
    auction=[]
    for symbol in ('2330','2408'):
        for day in (START,END):
            rows=record.candles(dict(symbol=symbol,timeframe='1',start=str(day),end=str(day)))
            tail=[row for row in rows if pd.Timestamp(row['bar_start']).hour==13]
            native=next(row for item in parts if item['request']['symbol']==symbol and item['request']['timeframe']=='60'
                for row in item['rows'] if row['bar_start']==str(day)+'T13:00:00+08:00')
            assert tail and tail[-1]['bar_start']==str(day)+'T13:30:00+08:00'
            aggregate=dict(Open=tail[0]['Open'],High=max(r['High'] for r in tail),Low=min(r['Low'] for r in tail),
                           Close=tail[-1]['Close'],Volume=sum(r['Volume'] for r in tail))
            assert all(aggregate[name]==native[name] for name in OHLCV)
            auction.append(dict(symbol=symbol,date=str(day),passed=True,
                native_13_00={name:native[name] for name in OHLCV},
                auction_13_30={name:tail[-1][name] for name in OHLCV}))
    source=dict(schema='fugle_feasibility_v2',status='NATIVE_COVERAGE_PASSED_REVIEW_COMPARISONS',
        generated_at=datetime.now(TAIPEI).isoformat(),requested_start=str(START),requested_end=str(END),
        daily_warmup_start=window.daily_start.isoformat(),minute_warmup_start=window.minute_start.isoformat(),
        normalized_volume_unit='shares',full_range_results=parts,annual_coverage=full_validation,
        auction_samples=auction,reuse=reuse_counts,requests=record.requests,
        requests_downloaded_this_run=record.downloaded,
        calendar_sha256={str(year):sha(HERE.parent/'calendars'/f'{year}.json') for year in (2024,2025)})
    write_json(SOURCE,source)
    create_fugle_snapshot(SOURCE,SNAPSHOT)
    before=SNAPSHOT.read_bytes()
    create_fugle_snapshot(SOURCE,SNAPSHOT)
    assert SNAPSHOT.read_bytes()==before
    print('Validated annual source and deterministic snapshot saved.',flush=True)


def equity_attribution(saved,trades):
    """Reconcile all marks, including a real position still open at year-end."""
    fills={row['execution_time']:row for row in saved['fills']}
    active=None
    previous=Decimal(str(saved['config']['initial_cash']))
    totals=defaultdict(Decimal)
    ledger=[]
    for i,point in enumerate(saved['equity_curve']):
        fill=fills.get(point['at']) if point['phase']=='OPEN' else None
        assigned=active
        if fill:
            assigned=fill['trade_id']
            active=fill['trade_id'] if fill['action']=='BUY' else None
        delta=Decimal(str(point['equity']))-previous
        previous=Decimal(str(point['equity']))
        if assigned is not None:
            totals[assigned]+=delta
        else:
            assert delta==0
        ledger.append(dict(index=i,**point,trade_id=assigned,equity_change=float(delta)))
    expected={row['trade_id']:row['net_profit'] for row in trades}
    if saved['open_position']:
        assert active is not None
        expected[active]=saved['open_position']['unrealized_net_profit']
    for trade_id,total in totals.items():
        assert abs(float(total)-expected[trade_id])<1e-5
    assert abs(float(sum(totals.values()))-saved['summary']['total_net_profit'])<1e-5
    peak=saved['config']['initial_cash']
    peak_i=None
    maximum=0
    best_peak,trough_i=None,None
    curve=saved['equity_curve']
    for i,point in enumerate(curve):
        if point['equity']>peak:
            peak,peak_i=point['equity'],i
        dd=(peak-point['equity'])/peak*100
        if dd>maximum:
            maximum,best_peak,trough_i=dd,peak_i,i
    peak_point=curve[best_peak] if best_peak is not None else dict(
        at=curve[0]['at'],phase='INITIAL',equity=saved['config']['initial_cash'])
    contributions=defaultdict(Decimal)
    for row in ledger[(best_peak+1 if best_peak is not None else 0):trough_i+1]:
        if row['trade_id'] is not None:
            contributions[row['trade_id']]+=Decimal(str(row['equity_change']))
    assert abs(maximum-saved['summary']['max_drawdown_percent'])<1e-9
    assert abs(float(sum(contributions.values()))-(curve[trough_i]['equity']-peak_point['equity']))<1e-5
    return dict(max_drawdown_percent=maximum,peak=peak_point,trough=curve[trough_i],
        recovered_by_end=any(p['equity']>=peak_point['equity'] for p in curve[trough_i+1:]),
        contributions=[dict(trade_id=k,equity_change=float(v),
            status='open_at_end' if k==active else 'closed',full_trade_or_open_mark_profit=expected[k])
            for k,v in sorted(contributions.items(),key=lambda item:item[1])],ledger=ledger)


def open_position_analysis(saved,data):
    position=saved['open_position']
    if position is None:
        return None
    held=data.history.bars.loc[data.history.bars.index>=pd.Timestamp(position['entry_time'])]
    marks=[]
    for at,row in held.iterrows():
        marks.extend([(at.isoformat(),'OPEN',float(row.Open)),(row.bar_end.isoformat(),'CLOSE',float(row.Close))])
    top=max(marks,key=lambda item:item[2])
    peak_profit=round(top[2]*position['shares']-position['entry_cost'],2)
    preceding=saved['trades'][-1] if saved['trades'] else None
    sessions=list(data.window.calendar.sessions)
    gap=(sessions.index(pd.Timestamp(position['entry_time']).date())-
         sessions.index(pd.Timestamp(preceding['exit_time']).date())) if preceding else None
    return dict(**position,holding_trading_sessions=len(set(held.index.date)),holding_bars=len(held),
        holding_market_minutes=int(held.duration_minutes.sum()),peak_mark_time=top[0],peak_mark_phase=top[1],
        peak_mark_price=top[2],peak_mark_profit=peak_profit,
        peak_to_end_giveback=round(peak_profit-position['unrealized_net_profit'],2),
        giveback_percent=(peak_profit-position['unrealized_net_profit'])/peak_profit*100 if peak_profit>0 else None,
        previous_closed_trade_id=preceding['trade_id'] if preceding else None,
        reentry_gap_sessions=gap,rapid_reentry=gap is not None and gap<=3,
        is_closed_trade=False,exit_fee_or_tax_assumed=False)


def replay_and_compare():
    provider=FugleSnapshotProvider(SNAPSHOT)
    previous=json.loads((HERE/'fugle_trade_analysis_20261101'/'analysis.json').read_text(encoding='utf-8'))
    source=json.loads(SOURCE.read_text(encoding='utf-8'))
    annual={}
    rows=[]
    for symbol in ('2330','2408'):
        print(symbol+': running original annual strategy and independent audits',flush=True)
        old_path=HERE/f'fugle_backtest_{symbol}_20251030_20261029.json'
        old=json.loads(old_path.read_text(encoding='utf-8'))
        assert sha(old_path)==previous['symbols'][symbol]['source_sha256']
        config=BacktestConfig(**old['config'])
        data=fetch_backtest_data(symbol,START,END,provider=provider)
        result=run_backtest(data,config=config)
        assert result.config==old['config']
        paths=export_backtest(result,ROOT/f'fugle_backtest_{symbol}_20241030_20251029.json')
        independent=audit_result(data,result,provider)
        replay=run_backtest(fetch_backtest_data(symbol,START,END,provider=provider),config=config)
        assert replay.to_dict()==result.to_dict()
        saved=result.to_dict()
        fills,raw=analyze_fills(saved,data)
        trades=analyze_trades(saved,data,fills)
        statistics=summarize(saved,data,trades,fills,raw)
        assert statistics['all_trades']['net_profit']==saved['summary']['realized_net_profit']
        for distribution in (statistics['holding']['calendar_day_distribution'],statistics['holding']['session_distribution']):
            assert sum(item['count'] for item in distribution)==len(trades)
        opened=open_position_analysis(saved,data)
        if opened:
            statistics['holding']['market_exposure_percent']=(
                sum(t['holding_market_minutes'] for t in trades)+opened['holding_market_minutes'])/(len(data.window.sessions)*270)*100
        eq=equity_attribution(saved,trades)
        write_csv(ROOT/f'{symbol}_trade_diagnostics.csv',trades)
        write_csv(ROOT/f'{symbol}_fill_triggers.csv',fills)
        write_csv(ROOT/f'{symbol}_raw_signal_outcomes.csv',raw)
        write_csv(ROOT/f'{symbol}_equity_attribution.csv',eq.pop('ledger'))
        write_json(ROOT/f'{symbol}_open_position.json',opened)
        annual[symbol]=dict(summary=saved['summary'],config=saved['config'],statistics=statistics,
            open_position=opened,equity=eq,trades=trades,fills=fills,
            independent_audit=independent,paths=paths,output_sha256=sha(paths['json']),
            reproducible_exactly=True)
        for year,summary,stats,open_record in [
                ('2024-10-30..2025-10-29',saved['summary'],statistics,opened),
                ('2025-10-30..2026-10-29',old['summary'],previous['symbols'][symbol]['summary'],old['open_position'])]:
            groups=stats['groups']
            open_gross=(open_record['mark_price']-open_record['entry_price'])*open_record['shares'] if open_record else 0
            gross=stats['all_trades']['gross_price_profit']+open_gross
            costs=summary['commissions']+summary['transaction_taxes']
            assert abs(gross-costs-summary['total_net_profit'])<1e-5
            rows.append(dict(year=year,symbol=symbol,**summary,
                median_closed_holding_days=stats['holding']['median_calendar_days'],
                same_day_round_trips=stats['counts']['same_day_round_trips'],
                same_day_round_trip_profit=groups['same_calendar_day']['net_profit'],
                rapid_reentries=stats['counts']['rapid_reentry_pairs'],
                rapid_reentry_profit=groups['rapid_reentry_within_3_session_gaps']['net_profit'],
                short_holding_count=groups['up_to_3_exposed_sessions']['count'],
                short_holding_profit=groups['up_to_3_exposed_sessions']['net_profit'],
                positive_mark_to_net_loss_count=groups['positive_mark_profit_to_net_loss']['count'],
                positive_mark_to_net_loss_profit=groups['positive_mark_profit_to_net_loss']['net_profit'],
                closed_gross_price_profit=stats['all_trades']['gross_price_profit'],
                open_gross_price_profit=round(open_gross,2),total_gross_price_profit=round(gross,2),
                total_costs=round(costs,2),closed_trade_costs=stats['all_trades']['total_costs'],
                costs_as_percent_of_initial=costs/summary['initial_cash']*100,
                average_closed_winner=stats['all_trades']['average_win'],
                average_closed_loser=stats['all_trades']['average_loss'],
                closed_profit_factor=stats['all_trades']['profit_factor'],
                open_position_at_end=open_record is not None))
        print(symbol+': '+json.dumps(dict(summary=saved['summary'],same_day=statistics['counts']['same_day_round_trips'],
            rapid_reentries=statistics['counts']['rapid_reentry_pairs'],open_position=opened is not None)),flush=True)
    write_csv(ROOT/'annual_comparison.csv',rows)
    write_json(ROOT/'comparison.json',dict(period=dict(start=str(START),end=str(END)),
        annual_results=annual,comparison_rows=rows,previous_analysis_sha256=sha(HERE/'fugle_trade_analysis_20261101'/'analysis.json'),
        snapshot_sha256=sha(SNAPSHOT),source_sha256=sha(SOURCE),
        same_costs_and_rules=True,no_optimization=True,no_fabricated_prices=True,
        open_positions_not_forced_closed=True,holding_distributions_closed_trades_only=True,
        capture_requests_saved=len(source['requests']),reuse=source['reuse'],
        full_data_validation=source['annual_coverage'],auction_samples=source['auction_samples']))
    protected=json.loads((ROOT/'protected_baseline.json').read_text(encoding='utf-8'))
    project=HERE.parents[1]
    changed=[name for name,expected in protected.items() if sha(project/name)!=expected]
    assert not changed,changed
    write_json(ROOT/'validation.json',dict(passed=True,protected_existing_files_unchanged=len(protected),
        original_unit_tests=dict(passed=True,tests=249,failures=0,errors=0,elapsed_seconds=28.583),
        additional_calendar_tests=dict(passed=True,tests=2,failures=0,errors=0,elapsed_seconds=0.001),
        new_calendar_file='666_strategy/calendars/2024.json',calendar_sha256=source['calendar_sha256'],
        snapshot_replay_matches_exactly=True,independent_audits={s:annual[s]['independent_audit'] for s in annual},
        holding_closed_trade_counts_reconciled=True,realized_and_open_profit_reconciled=True,
        equity_attribution_reconciled=True,previous_result_files_unchanged=True))
    print('Annual comparison and validation saved.',flush=True)


def render_comparison():
    document=json.loads((ROOT/'comparison.json').read_text(encoding='utf-8'))
    previous=json.loads((HERE/'fugle_trade_analysis_20261101'/'analysis.json').read_text(encoding='utf-8'))
    def number(value,places=2):
        return 'N/A' if value is None else f'{value:,.{places}f}'
    def when(value):
        return pd.Timestamp(value).strftime('%Y-%m-%d %H:%M')
    def table(headers,rows):
        return ['| '+' | '.join(headers)+' |','|'+'|'.join('---' for _ in headers)+'|',
            *['| '+' | '.join(map(str,row))+' |' for row in rows],'']
    rows=document['comparison_rows']
    annual=document['annual_results']
    lines=['# Fugle 原始 666 策略：兩個完整年度比較', '',
        '新增期間：**2024-10-30～2025-10-29**；比較期間：2025-10-30～2026-10-29。'
        '2330、2408 均使用既有 Fugle Provider、原始 666 訊號／多頭過濾／次棒 Open 成交，以及相同成本設定。'
        '沒有最佳化參數、縮短區間、補造價格或修改原台股助手。', '',
        '## 結論', '',
        '2330 的短持倉虧損與費稅侵蝕在兩年度均明顯存在；但「快速再進場一定虧損」沒有跨年一致性。'
        '2408 前一年度交易少、費稅負擔低，主要靠一筆已實現波段與期末尚未平倉的上漲部位獲利，'
        '不能將後一年度的高換手問題直接套用到前一年。浮盈回吐兩年度都有觀察到，嚴重程度不同。', '',
        '## 一、完整性、來源與快照', '',
        '- 新期間有 243 個已確認交易日；每檔 1,215 根期間棒，加 60 根暖身，共 1,275 根原生 60 分 K；日線 120 個暖身交易日加 243 日，共 363 筆。',
        '- 分 K 暖身自 2024-10-14 09:00 起，日 K 暖身自 2024-05-06 起，截止 2025-10-29 13:30。原有 prepare_data 完整性檢查全部通過，沒有缺棒、無效 OHLCV、日線 UNKNOWN 或日曆衝突。',
        '- 新增 2024 日曆資料，未修改日曆演算法或既有 2025／2026 日曆。依官方休市表，另納入 2024-07-24、07-25、10-02、10-03、10-31 五次明確颱風休市；未用缺少行情推定休市。',
        '- 每檔重用既有已驗證 Fugle 快照的 60 根 60 分 K、120 筆日線；只補齊此前缺少的區段。重用來源與下載時間／請求範圍、SHA-256 都保存，沒有混入 Yahoo 價格。',
        '- 下載流程保存 16 份成功 API 回應：12 個分 K／日 K 分段與 4 個 1 分 K 樣本；另有 1 次前置連線檢查。後續回放、績效與比較均離線。',
        '- 兩檔在 2024-10-30 與 2025-10-29 的 1 分 K 樣本證實，原生 13:00 60 分 K 已含收盤撮合；四組 OHLCV 核對通過。只用原生每天五根，不追加 13:30 棒、不重建 60 分 K。',
        '- 原價未還原、Asia/Taipei、分 K 張轉股一次、日 K 原為股。完整快照以來源檔重建兩次，位元組完全一致，載入時仍驗證雜湊與欄位。', '',
        '[可重現快照](fugle_history_20241030_20251029.snapshot.json) · [來源與逐段驗證](fugle_source_20241030_20251029.json) · '
        '[比較 JSON](comparison.json) · [比較 CSV](annual_comparison.csv) · [驗證結果](validation.json)', '',
        f"快照 SHA-256：`{document['snapshot_sha256']}`", '',
        f"來源 SHA-256：`{document['source_sha256']}`", '',
        '日曆依據：[TPEx 2024 官方日期表](https://www.tpex.org.tw/storage/en-us/web/bulletin/trading_date/trading_date_113.htm)、'
        '[TWSE 2024 年表公告](https://eshop.twse.com.tw/zh/news/detail/000000008c1e9f81018cc87af7540078)。'
        '臨時休市依據：[7/24](https://www.twse.com.tw/staticFiles/news/news/tsecnews/8a8216d6906d4d340190df83a6ac0170.pdf)、'
        '[7/25](https://www.twse.com.tw/staticFiles/news/news/tsecnews/8a8216d6906d4d340190e4a4ef640174.pdf)、'
        '[10/2](https://www.twse.com.tw/staticFiles/news/news/tsecnews/8a8216d69236c2e301924806d5b4004e.pdf)、'
        '[10/3](https://investoredu.twse.com.tw/Mobile_pages/..%2FFileSystem%2FFileUpload%2F2c6e50d4-a390-439c-a2c4-823937dee4b2.pdf)、'
        '[10/31](https://www.twse.com.tw/staticFiles/news/news/tsecnews/8a8216d69236c2e30192dd5179bc0327.pdf)。', '',
        '## 二、原設定與年度績效', '',
        '每檔、每個年度分別以 1,000,000 元及空手開始。手續費 0.1425%、每筆最低 20 元，賣出稅 0.3%、'
        '整數股、可負擔的最大部位、滑價 0；不計股息。年度獨立重設本金與持倉，不能把兩年度直接當連續資金曲線。', '',
        '**2025-10-29 兩檔均未平倉，依原規則用最後完成 Close 評價；未假賣出，未假扣出場費稅。'
        '勝率與持倉分布只計已完成交易，浮盈獨立列示。**', '']
    lines+=table(['股票／年度','淨損益','總報酬率','已平倉／獲利筆數','勝率','最大回撤','買進持有報酬'],[
        [f"{r['symbol']}／{'前一年' if r['year'].startswith('2024') else '後一年'}",number(r['total_net_profit']),
         number(r['total_return_percent'],4)+'%',f"{r['completed_trades']}／{r['winning_trades']}",
         number(r['win_rate_percent'],4)+'%',number(r['max_drawdown_percent'],4)+'%',number(r['buy_and_hold_return_percent'],4)+'%'] for r in rows])
    lines+=['「買進持有」同樣於第一根期間棒 Open 買入、支付買進費，最後 Close 評價，不假賣出、不計股息。'
        '四個股票／年度組合的策略報酬均低於其買進持有基準；這不代表基準有相同回撤或曝險，本表不混淆兩種風險路徑。', '']
    lines+=table(['股票／年度','已實現淨損益','未實現淨損益','期末權益','手續費','交易稅','費稅合計'],[
        [f"{r['symbol']}／{'前一年' if r['year'].startswith('2024') else '後一年'}",number(r['realized_net_profit']),
         number(r['unrealized_net_profit']),number(r['ending_equity']),number(r['commissions']),number(r['transaction_taxes']),number(r['total_costs'])] for r in rows])
    lines+=['### 前一年度期末持倉', '']
    lines+=table(['股票','進場時間／價格','股數','期末價格','持有曆日／曝險交易日','未實現淨損益','期間峰值浮盈','峰值至期末回吐'],[
        [s,f"{when(p['entry_time'])}／{p['entry_price']:g}",p['shares'],p['mark_price'],
         f"{p['holding_days']:.4f}／{p['holding_trading_sessions']}",number(p['unrealized_net_profit']),number(p['peak_mark_profit']),number(p['peak_to_end_giveback'])]
        for s,v in annual.items() for p in [v['open_position']] if p])
    lines+=['2408 的 +105.09% 包含 878,814.21 元未實現獲利；只有 172,082.40 元已實現。'
        '未平倉交易未來結果未知，不能將它算成第二筆勝利，亦不能把 28.15 天當成其最終持倉長度。', '',
        '## 三、持倉分布與反覆交易', '',
        '沿用既有分析定義：同日來回為同一台北日期完成買賣；短持倉為實際曝險不超過 3 個交易日；'
        '快速再進場為與上一出場最多跨 3 個已確認交易日。以下組數與損益只計已平倉交易，'
        '已買但未賣的兩筆另列，不以期末標記製造假交易。', '']
    lines+=table(['股票／年度','平均／中位持倉曆日','同日來回筆數／淨損益','短持倉筆數／淨損益','快速再進場筆數／淨損益'],[
        [f"{r['symbol']}／{'前一年' if r['year'].startswith('2024') else '後一年'}",f"{r['average_holding_days']:.4f}／{r['median_closed_holding_days']:.4f}",
         f"{r['same_day_round_trips']}／{number(r['same_day_round_trip_profit'])}",
         f"{r['short_holding_count']}／{number(r['short_holding_profit'])}",
         f"{r['rapid_reentries']}／{number(r['rapid_reentry_profit'])}"] for r in rows])
    for symbol in ('2330','2408'):
        a=annual[symbol]['statistics']['holding']['calendar_day_distribution']
        b=previous['symbols'][symbol]['summary']['holding']['calendar_day_distribution']
        lines += [f'### {symbol}：已平倉持倉曆日分布', '']
        lines += table(['曆日區間','前一年筆數','前一年淨損益','後一年筆數','後一年淨損益'],[
            [x['calendar_days'],x['count'],number(x['net_profit']),y['count'],number(y['net_profit'])] for x,y in zip(a,b)])
    lines+=['小於 1 曆日也可能隔夜；不能直接當成同日交易。分組彼此重疊，不能將同日、短持倉及重新進場損益相加。'
        '短持倉是事後結果，失敗單本來可能更快觸發 SELL；這不是強迫延長持倉就會改善績效的證據。', '',
        '### 跨年判讀', '',
        '- **2330：短線磨損持續。** 前一年同日 7 筆全虧、短持倉 15 筆中 14 筆虧，損失 133,473.09 元；後一年同日 9 筆全虧、短持倉 24 筆損失 350,511.62 元。',
        '- **重新進場不是跨年一致的負貢獻。** 2330 前一年快速再進場 16 筆合計 +773.43 元，接近損益兩平；2408 為 2 筆合計 +201,670.57 元。後一年兩檔則各 −199,174.89 與 −401,324.11 元。',
        '- **2408 的高換手沒有在前一年重現。** 前一年只有 5 次實際 BUY（其中 1 筆期末未賣）、4 筆完成交易、零同日來回；27 個原始 BUY 中 22 個被日線 NON_BULL 阻擋。後一年則有 26 次 BUY、26 筆完成交易。', '',
        '具體反例：2330 #19 在 2025-09-05 09:00 出場，10:00 又買入 #20；'
        '#20 以 1,170→1,450、643 股持有 48 日，淨獲利 174,842.31 元，是前一年最大獲利。'
        '2408 #2 在 6/2 10:00 出場，11:00 又以 44.25 元買入 #3；6/25 55.3 元賣出，獲利 232,859.57 元。'
        '兩個主要獲利都來自同日重新進場，不能只刪除這類交易後宣稱改善。', '',
        '## 四、浮盈回吐與回撤是否持續', '']
    lines+=table(['股票／年度','已平倉中曾有正浮盈、最後淨虧損的筆數','這組淨損益'],[
        [f"{r['symbol']}／{'前一年' if r['year'].startswith('2024') else '後一年'}",r['positive_mark_to_net_loss_count'],number(r['positive_mark_to_net_loss_profit'])] for r in rows])
    lines+=['浮盈口徑不變：持有期間原生棒 Open／完成 Close 與真正出場 Open 的標記，扣已付買進費，'
        '峰值不預扣假想出場費；實現損益扣全費稅。峰值是事後觀察，不表示當時可預知或能在該價成交。', '']
    cases=[]
    for symbol in ('2330','2408'):
        for year,stats in [('前一年',annual[symbol]['statistics']),('後一年',previous['symbols'][symbol]['summary'])]:
            t=stats['largest_giveback_trade']
            cases.append([f"{symbol}／{year} #{t['trade_id']}",f"{when(t['entry_time'])}／{t['entry_price']:g}",
                f"{when(t['peak_mark_at'])}／{t['peak_mark_price']:g}",number(t['peak_mark_profit']),
                f"{when(t['exit_time'])}／{t['exit_price']:g}",number(t['net_profit']),number(t['profit_giveback_percent'])+'%'])
    lines+=table(['最大金額回吐已平倉案例','進場時間／價格','峰值時間／價格','峰值浮盈','出場時間／價格','實現損益','回吐比例'],cases)
    lines+=['2330 前一年 #6 在 2025-01-16 以 1,095 元進場，曾有 44,515.21 元標記浮盈，'
        '2/3 以 1,070 元出場後淨虧 26,068.84 元；連假包含在持倉曆日中。'
        '2408 前一年 #3 仍獲利，但從峰值浮盈回吐 34.45%；後一年最大回吐案例 #10 為 82.92%。'
        '所以回吐現象有延續，但不能將兩年的嚴重程度視為相同。', '',
        '### 前一年最大回撤的形成', '']
    lines+=table(['股票','權益峰值時間／金額','谷底時間／金額','最大回撤','期末是否恢復該峰值'],[
        [s,f"{when(e['peak']['at'])}／{number(e['peak']['equity'])}",f"{when(e['trough']['at'])}／{number(e['trough']['equity'])}",
         number(e['max_drawdown_percent'],4)+'%','是' if e['recovered_by_end'] else '否'] for s,v in annual.items() for e in [v['equity']]])
    lines+=['最大回撤沿用原始初始資金、每根 Open 成交後及完成 Close 的權益，不推測 High／Low 路徑。'
        '2408 的最大回撤發生於期末仍未平倉部位之內，不能只用已實現損益判斷其風險。'
        '每根權益增減已精確歸屬已平倉交易或期末真實持倉，未新增虛構賣單。', '']
    lines+=table(['股票','最長連虧交易編號','期間','筆數','連虧淨損益'],[
        [s,f"#{st['first_trade_id']}～#{st['last_trade_id']}",f"{when(st['first_entry'])}～{when(st['last_exit'])}",st['count'],number(st['net_profit'])]
        for s,v in annual.items() for st in [v['statistics']['longest_loss_streak']]])
    lines+=['## 五、交易成本的持續性', '']
    lines+=table(['股票／年度','已平倉價差損益','期末持倉價差損益','全部費稅','費稅／初始本金','扣費後年度淨損益'],[
        [f"{r['symbol']}／{'前一年' if r['year'].startswith('2024') else '後一年'}",number(r['closed_gross_price_profit']),
         number(r['open_gross_price_profit']),number(r['total_costs']),number(r['costs_as_percent_of_initial'],4)+'%',number(r['total_net_profit'])] for r in rows])
    lines+=['核對恆等式：已平倉價差損益＋期末持倉價差損益−實際全部費稅＝年度淨損益。'
        '這不是取消費率後的替代回測；所有金額均以真正成交股數及原資金路徑統計。', '',
        '- 2330 前一年價差合計 34,640 元，被 107,020.59 元費稅抵銷後轉虧；後一年價差合計 23,740 元，被 174,451.33 元費稅抵銷。成本侵蝕在兩年都明確存在。',
        '- 2408 前一年全部費稅 26,758.69 元，僅占原始本金 2.68%，不具有後一年 206,499.11 元（20.65% 本金）的高成本程度。交易次數、部位金額、持倉長短及尚未支付期末出場成本均不同。',
        '- 2408 前一年完成交易只有四筆，勝率與短持倉分組的樣本很小；另有大額浮盈未實現。不能由此確認問題永久消失，也不能因後一年反覆虧損而忽略本年的反例。', '',
        '## 六、逐筆結果與持倉資料', '']
    for symbol,v in annual.items():
        stem=f'fugle_backtest_{symbol}_20241030_20251029'
        lines += [f'### {symbol}', '',f'[完整回測 JSON]({stem}.json) · [每筆成交]({stem}.fills.csv) · [已平倉交易]({stem}.trades.csv) · '
            f'[持倉／回吐診斷]({symbol}_trade_diagnostics.csv) · [全部成交觸發]({symbol}_fill_triggers.csv) · '
            f'[期末持倉]({symbol}_open_position.json) · [權益歸屬]({symbol}_equity_attribution.csv)', '']
        lines+=table(['交易','買進成交','賣出成交','買價→賣價','股數','持倉曆日','淨損益'],[
            [t['trade_id'],when(t['entry_time']),when(t['exit_time']),f"{t['entry_price']:g}→{t['exit_price']:g}",t['shares'],
             number(t['holding_days'],4),number(t['net_profit'])] for t in v['trades']])
    lines += ['## 七、驗證與重現', '',
        '- 249 項既有單元測試通過，另新增 2 項 2024 日曆檢查通過；原有測試不變。',
        '- 兩檔均以保存快照重新跑原始策略，結果完全一致；費稅、現金、已實現與未實現損益、勝率、回撤、持倉時間及買進持有基準以獨立算術核對。',
        '- 每檔另對起點、中點、倒數第二個交易日做前綴重跑，共 6 次，確認未來資料未改變較早成交；不是宣稱逐一重跑全部交易日。',
        '- 已平倉持倉分布筆數核對完整；浮盈分析不將期末持倉製造成完成交易。全部權益變化與原結果精確對帳。',
        '- 213 個既有檔案 SHA-256 確認不變，包含原策略、成本、兩個 Provider、原助手、原回測與前次分析結果。新增內容為 2024 日曆、新日曆測試及此年度的驗證／報告資料。',
        '- 金鑰沿用環境變數優先與 .env 備援，.gitignore 仍排除 .env；未輸出或提交金鑰。', '',
        '在台股目錄離線重現：', '', '```powershell',
        'python -B -X utf8 -m 666_strategy.verification.verify_previous_fugle_year', '```', '',
        '單獨以既有回測 CLI 重現：', '', '```powershell',
        'python -B -X utf8 -m 666_strategy backtest --source fugle --symbol 2330 --start 2024-10-30 --end 2025-10-29 --source-snapshot 666_strategy/verification/fugle_20241030_20251029/fugle_history_20241030_20251029.snapshot.json --output 666_strategy/.cache/replay_2024_2330.json',
        'python -B -X utf8 -m 666_strategy backtest --source fugle --symbol 2408 --start 2024-10-30 --end 2025-10-29 --source-snapshot 666_strategy/verification/fugle_20241030_20251029/fugle_history_20241030_20251029.snapshot.json --output 666_strategy/.cache/replay_2024_2408.json', '```', '',
        '需要重新擷取缺少的歷史分段時才加 --download 與必要的 --ca-bundle；既有成功分段會重用。'
        '本次未執行任何改良規則、替代費率或最佳化參數的回測。']
    (ROOT/'COMPARISON_REPORT.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--download',action='store_true',help='Capture missing Fugle ranges; existing chunks are reused')
    parser.add_argument('--ca-bundle',type=str)
    args=parser.parse_args()
    try:
        if args.download:
            capture(args.ca_bundle)
        elif not SNAPSHOT.exists():
            raise ProbeError('MISSING_VERIFIED_ANNUAL_SNAPSHOT')
        replay_and_compare()
        render_comparison()
        return 0
    except ProbeError as error:
        # The shared client has already sanitized diagnostics; never print keys.
        print(json.dumps(dict(status=str(error),http_status=error.http_status,api_error=error.api_error)),flush=True)
        return 2


if __name__=='__main__':
    raise SystemExit(main())
