"""Render and package the offline six-stock comparison without rewriting old studies."""
from collections import Counter
import csv
from pathlib import Path
import subprocess
import sys
import tempfile
import zipfile

from ..verification.analyze_fugle_trades import write_csv
from ..verification.verify_previous_fugle_year import sha, write_json
from .run_tpex_ab import PROJECT, ROOT, INPUT, START, END, SYMBOLS, ALL, RUNNERS, read, source, result_path, protect

NAMES = {'2330.TW':'台積電','2408.TW':'南亞科','2454.TW':'聯發科',
         '3105.TWO':'穩懋','6147.TWO':'頎邦','3211.TWO':'順達'}


def money(x):
    return '—' if x is None else f'{x:,.2f}'


def pct(x):
    return '—' if x is None else f'{x:.4f}%'


def table(lines, headers, rows):
    lines.extend(['','| '+' | '.join(headers)+' |','| '+' | '.join(['---']*len(headers))+' |'])
    lines.extend('| '+' | '.join(str(c) for c in row)+' |' for row in rows)
    lines.append('')


def report():
    protected = protect()
    study = read(ROOT/'comparison.json')
    audit = read(ROOT/'audit_validation.json')
    tests = read(ROOT/'tests_result.json')
    assert audit['status']=='PASSED' and sum(t['tests'] for t in tests)==275
    entries = study['symbols']
    assert set(entries)==set(ALL)
    for symbol in ALL:
        assert entries[symbol]['source_snapshot_sha256']==sha(source(symbol))
        for variant in RUNNERS:
            assert entries[symbol][variant]['result_sha256']==sha(result_path(symbol,variant))
    artifacts=[]
    for symbol in SYMBOLS:
        combined=[]
        for variant in RUNNERS:
            path=result_path(symbol,variant)
            saved=read(path)
            for kind in ('fills','trades'):
                csv_path=path.with_suffix('.'+kind+'.csv')
                with csv_path.open(encoding='utf-8-sig',newline='') as stream:
                    rows=list(csv.DictReader(stream))
                assert len(rows)==len(saved[kind])
                for row,expected in zip(rows,saved[kind]):
                    assert row=={key:'' if value is None else str(value) for key,value in expected.items()}
                artifacts.append(dict(file=csv_path.name,rows=len(rows),sha256=sha(csv_path),all_fields_match_json=True))
            with (ROOT/f'{symbol[:4]}_{variant}_holding.csv').open(encoding='utf-8-sig',newline='') as stream:
                enriched=list(csv.DictReader(stream))
            assert len(enriched)==len(saved['trades'])
            combined.extend(dict(variant=variant,**row) for row in enriched)
        write_csv(ROOT/f'{symbol[:4]}_AB_trades.csv',combined)
    write_json(ROOT/'artifact_validation.json',dict(status='PASSED',exports=artifacts))
    lines = ['# 666_strategy：TPEx 三檔 A/B 與六檔合併比較',
        '',f'固定期間 {START}～{END}；每檔、每組獨立本金新臺幣 1,000,000 元，連續兩年，不跨年重設，不合成投資組合。',
        '', '## 主要結果',
        '', 'A 為原始 666；B 只增加一根完成 BUY 確認。下列為扣除實際費稅後、期末按收盤價評價的報酬。上市股直接讀取既有結果，未重寫。']
    table(lines,['股票','A 報酬','B 報酬','B−A 百分點','A 最大回撤','B 最大回撤','買進持有'],[
        [s+' '+NAMES[s],pct(entries[s]['A']['summary']['total_return_percent']),pct(entries[s]['B']['summary']['total_return_percent']),
         f"{entries[s]['B']['summary']['total_return_percent']-entries[s]['A']['summary']['total_return_percent']:+.4f}",
         pct(entries[s]['A']['summary']['max_drawdown_percent']),pct(entries[s]['B']['summary']['max_drawdown_percent']),
         pct(entries[s]['A']['benchmark']['return_percent'])] for s in ALL])
    positive = [s for s in ALL if entries[s]['B']['summary']['total_return_percent']>entries[s]['A']['summary']['total_return_percent']]
    lines += [f'六檔中 B 的總報酬高於 A 者為 {len(positive)} 檔（'+ '、'.join(NAMES[s] for s in positive)+'）。這是同一時段、指定樣本的描述，不能據此推定 B 普遍優於 A。',
        '', '本期間六檔的 A 與 B 均落後同股買進持有；策略通常僅部分時間持倉，因此報酬差異同時包含曝險與進出時機，不能單以總報酬比較風險。',
        '', '## 規則、帳務與指標口徑',
        '', '原始 BUY 為前根 Close ≤ 前根 MA60 且本根 Close > 本根 MA60；SELL 為前根 Close ≥ 前根 MA60 且本根 Close < 本根 MA60。A 在原始有效 BUY 穿越完成後，於下一根原生 K 的 Open 成交；B 先等待下一根完整 K 的 Close 嚴格高於該根 MA60、日線仍為 BULL，再於其下一根 Open 買進。失敗確認須等待新的原始 BUY。SELL、日線 NON_BULL 的 EXIT、EXIT 優先順序與買進當下再確認日線皆沿用既有規則。',
        '', '日線 BULL：Close > MA60 > MA120，且 MA60 高於五個交易日前；每個判斷僅使用該時點已完成的日 K。時區 Asia/Taipei（UTC+08:00），每日 09:00、10:00、11:00、12:00、13:00 五根；13:00 尾棒在 13:30 完成。尾棒訊號的下一根為下個交易日 09:00。',
        '', '每次以可負擔的整數股數全額進出，share_unit=1；買賣手續費率 0.1425%、每筆最低 20 元，賣出稅率 0.3%，金額四捨五入至分。零滑價、不計股息與現金利息、期末不強制平倉。零股與整股皆以相同 Open 成交是既有理想化假設。',
        '', '最大回撤以初始本金及各根 Open／Close 權益計算，未推估 High／Low 之內的路徑；可能低估盤中最深回撤。交易次數與勝率只計已平倉回合，獲利必須淨利 > 0；費稅包括實際買進的未平倉部位。買進持有以第一根 Open 買入、最後 Close 評價，計買入手續費、無假設期末賣出費稅；並非含息總報酬。',
        '', '## 六檔完整績效與費稅']
    table(lines,['股票／組','期末權益','完成交易','勝／敗','勝率','手續費','交易稅','總費稅','持倉時間比'],[
        [s+'/'+v,money(entries[s][v]['summary']['ending_equity']),entries[s][v]['summary']['completed_trades'],
         f"{entries[s][v]['summary']['winning_trades']}/{entries[s][v]['summary']['completed_trades']-entries[s][v]['summary']['winning_trades']}",
         pct(entries[s][v]['summary']['win_rate_percent']),money(entries[s][v]['summary']['commissions']),
         money(entries[s][v]['summary']['transaction_taxes']),money(entries[s][v]['total_costs']),pct(entries[s][v]['exposure_percent'])]
        for s in ALL for v in RUNNERS])
    lines += ['## 持倉分布', '', '下表為已平倉交易實際覆蓋的交易日數；時間比的分母為 484 × 270 個交易分鐘，分子包括期末仍持有部位。日曆天數包含夜間及休市。完整分箱的筆數、勝筆數與淨利保留在 comparison.json；逐筆含 bars、分鐘、同日交易與三交易日內再進場。']
    table(lines,['股票／組','日曆天中位數','1 交易日','2–3','4–5','6–10','11–20','>20','≤3日淨利','快速再進場'],[
        [s+'/'+v,f"{entries[s][v]['holding']['median_days']:.3f}",
         *[r['count'] for r in entries[s][v]['holding']['session_distribution']],
         money(entries[s][v]['holding']['short_up_to_3_sessions']['net_profit']),entries[s][v]['holding']['rapid_reentry']['count']]
        for s in ALL for v in RUNNERS])
    table(lines,['股票／組','期末股數','未實現淨利','待成交決策','待確認 BUY'],[
        [s+'/'+v,entries[s][v]['open_position']['shares'] if entries[s][v]['open_position'] else 0,
         money(entries[s][v]['open_position']['unrealized_net_profit'] if entries[s][v]['open_position'] else 0),
         entries[s][v]['pending_decision']['action'] if entries[s][v]['pending_decision'] else '無',
         '有' if entries[s][v]['pending_candidate'] else '無'] for s in ALL for v in RUNNERS])
    lines += ['## 短期反覆穿越 MA60 與趨勢持續性',
        '', '固定描述窗口為 1／3／5 根與 3 交易日，沒有搜尋最佳門檻。BULL BUY 是所有日線 BULL 時的原始向上穿越，可能發生於持倉中；B 候選則必須符合空手等條件，兩者分母不同。下一根未站上包含等於 MA60；跨日仍以實際交易棒計算。右設限訊號不視為失敗。']
    table(lines,['股票','全 BUY／SELL','BULL BUY','下一根未站上','3棒內下穿','5棒內下穿','3日內下穿','至下穿棒數中位數','連續高於棒數中位數'],[
        [s,f"{entries[s]['crossing']['raw_signal_counts'].get('BUY',0)}/{entries[s]['crossing']['raw_signal_counts'].get('SELL',0)}",
         *[entries[s]['crossing']['BULL_BUY'][key] for key in ('count','next_close_not_above','sell_within_3_bars',
             'sell_within_5_bars','sell_within_3_sessions','median_bars_to_sell','median_consecutive_above_bars')]] for s in ALL])
    table(lines,['股票','B 候選狀態','A 專有買進','配對買進','B 專有買進','BULL BUY 右設限'],[
        [s,', '.join(f'{k}: {v}' for k,v in entries[s]['b_candidate_statuses'].items()),
         *[sum(r['status']==status for r in entries[s]['signal_trade_differences']) for status in ('A_ONLY','DELAYED_MATCHED_ENTRY','B_ONLY')],
         entries[s]['crossing']['BULL_BUY']['right_censored']] for s in ALL])
    lines += ['## 波動與跨股票解讀',
        '', '使用一致資料與公式：日 Close 對數報酬樣本標準差 × √252。前期固定為 2024-08-01～2024-10-29；正式期波動是事後描述，未用來換股、調參或決定 A/B。六檔均不保證為預先盲選樣本，TPEx 三檔明確為使用者指定，不承接已刪除候選的流動性、市值或波動篩選結論。']
    table(lines,['股票／產業','前期波動','正式期波動','日高低幅中位數','B−A 百分點','B−A 回撤百分點'],[
        [s+'/'+{'2330.TW':'晶圓代工','2408.TW':'DRAM','2454.TW':'IC 設計','3105.TWO':'砷化鎵晶圓代工','6147.TWO':'封裝測試','3211.TWO':'電池模組'}[s],
         pct(entries[s]['market']['preperiod_annualized_log_volatility_percent']),pct(entries[s]['market']['study_annualized_log_volatility_percent']),
         pct(entries[s]['market']['median_daily_range_percent']),
         f"{entries[s]['B']['summary']['total_return_percent']-entries[s]['A']['summary']['total_return_percent']:+.4f}",
         f"{entries[s]['B']['summary']['max_drawdown_percent']-entries[s]['A']['summary']['max_drawdown_percent']:+.4f}"] for s in ALL])
    lines += ['六檔前期均有 60 個、正式期均有 484 個日報酬觀測。正式期波動最低的 2330（32.1940%）卻有最多 BULL 向上穿越（55 次）、最短至下穿中位數（3 根）；3105（68.4393%）與 2408（77.7893%）均為 6 根。3211 波動 64.6307%，至下穿中位數卻達 17.5 根，兩個主要長波段貢獻大部分正淨利，B 仍因延後買價而落後。故總波動幅度與 MA60 附近的訊號噪音、延後代價，並非同一件事。',
        '', '6147 前期波動僅 20.4129%，正式期升至 58.5376%，但僅有四個 BULL BUY；A/B 持倉時間比分別只有 4.4077%／4.2470%。其大幅落後買進持有，與日線條件及進出時序造成的低曝險一致；僅憑四筆交易不能將結果歸因於上櫃市場特性。']
    lines += ['2330 的短期穿越較密集，B 取消部分候選並降低交易費用；2408 雖有少數取消，延後與資金路徑的代價使 B 總報酬低於 A。2454 的 B 候選全部成交，其差異主要是延後買價及後續股數，不是取消假突破的證據。以下對上櫃三檔逐一拆解，避免僅以波動高低解釋績效。',
        '', '## 主要獲利交易與上櫃 A/B 差異',
        '', '以原始 BUY signal_time 配對，不按第幾筆交易硬配。actual_net_profit_difference 包括先前損益造成的股數差；equal_one_share_gross_price_difference 才隔離每股進出價差，但仍未控制費用與交易時間風險。A 專有部位不一律代表確認失敗，仍需查看候選狀態。']
    table(lines,['股票／組','最大獲利淨利','前二獲利合計','全部正淨利合計','前二占正淨利','最大回撤峰→谷'],[
        [s+'/'+v,money(entries[s][v]['top_5_trades'][0]['net_profit']),money(entries[s][v]['top_2_profit']),
         money(entries[s][v]['positive_trade_profits']),pct(entries[s][v]['top_2_profit']/entries[s][v]['positive_trade_profits']*100),
         str(entries[s][v]['summary']['drawdown_peak_at'])+' → '+str(entries[s][v]['summary']['drawdown_trough_at'])]
        for s in ALL for v in RUNNERS])
    lines += ['「前二」只取正淨利回合，不足兩筆則取實際正淨利筆數。6147 僅一筆獲利即占全部正淨利，3211 B 的前二筆占 99.8338%；績效對少數長波段高度集中，勝率或平均持有天數不足以單獨描述風險。最大回撤逐筆貢獻及峰谷點完整保存在 comparison.json 與新三檔 *_equity_attribution.csv。']
    detailed = []
    for s in SYMBOLS:
        item=entries[s]
        a,b=item['A'],item['B']
        delta=b['summary']['total_net_profit']-a['summary']['total_net_profit']
        pairs=[r for r in item['signal_trade_differences'] if r['actual_net_profit_difference'] is not None]
        omitted=[r for r in item['signal_trade_differences'] if r['status']=='A_ONLY']
        extra=[r for r in item['signal_trade_differences'] if r['status']=='B_ONLY']
        matched=sum(r['actual_net_profit_difference'] for r in pairs)
        omittedprofit=sum(r['a_net_profit'] or 0 for r in omitted)
        extraprofit=sum(r['b_net_profit'] or 0 for r in extra)
        open_delta=(b['open_position']['unrealized_net_profit'] if b['open_position'] else 0)-(a['open_position']['unrealized_net_profit'] if a['open_position'] else 0)
        assert abs(delta-(matched-omittedprofit+extraprofit+open_delta))<1e-5
        attribution=dict(symbol=s,total_net_difference=round(delta,2),matched_closed_delta=round(matched,2),
            a_only_closed_net=round(omittedprofit,2),b_only_closed_net=round(extraprofit,2),open_mark_delta=round(open_delta,2))
        detailed.append(attribution)
        lines += ['',f'### {s} {NAMES[s]}', '',
            f"B−A 淨利為 {money(delta)} 元；配對已平倉差額 {money(matched)}，減去 A 專有已平倉淨利 {money(omittedprofit)}，加 B 專有淨利 {money(extraprofit)}，再加期末未實現差額 {money(open_delta)}，帳務完全相符。",
            '',f"B 候選狀態：{item['b_candidate_statuses']}。BULL 向上穿越 {item['crossing']['BULL_BUY']['count']} 次，其中下一根未站上 {item['crossing']['BULL_BUY']['next_close_not_above']} 次，3 根內下穿 {item['crossing']['BULL_BUY']['sell_within_3_bars']} 次。",
            '',f"總費稅 A {money(a['total_costs'])}、B {money(b['total_costs'])} 元；B−A 為 {money(b['total_costs']-a['total_costs'])} 元。費用節省與延後買價、不同資金複利須共同解讀。"]
        lines += ['',{
            '3105.TWO':'B 取消的六筆在 A 均為虧損，A 合計損失 267,295.15 元；然而配對交易淨利差額為 −300,047.95 元，超過避開損失的好處，最後 B 少賺 32,752.80 元。最大獲利段 2026-03-18～04-27，B 買價由 312.5 延至 322.5，同一出場價 492.5，單筆淨利少 67,392.86 元。確認確實消除了若干短促穿越，卻同時付出上升段延後成本。',
            '6147.TWO':'四個候選全數成交，沒有取消假突破的效果。B 多賺 9,866.31 元，其中第一筆跨週末由 A 66.5 延至 B 66.0 買入、同在 66.1 賣出，少虧 7,486.31 元，是最大差異。唯一正淨利交易為 2026-05-15～06-08，兩組同以 205 買、243 賣，B 淨利多 1,575.20 元來自先前資金差導致股數較多。只有四個完成回合，無法據以判定確認規則穩健。',
            '3211.TWO':'三個取消候選在 A 是兩虧一盈，合計淨損 112,373.23 元；配對已平倉交易卻少賺 452,576.92 元，最後 B 少賺 340,203.69 元。2026-05-22 的 BUY，A 於當日 13:00 以 388 買進，B 確認尾棒後延至 05-25 09:00 以 417.5 買入（高 7.6031%），同於 420.5 賣出，淨利從 199,563.00 降至 3,047.27 元。2026-04-17 也因跨週末延後買價，由小賺轉虧；這些延後成本超過過濾效果，且 B 的最大回撤反而較深。'
        }[s], '', '上述金額是兩條實際複利路徑的帳務分解，已含費稅；費用節省不可再加一次，也不能把被取消交易的 A 淨利當作保持其他資金路徑不變的因果估計。']
        table(lines,['原始 BUY','配對狀態／B候選','A 買價→B 買價','A 淨利','B 淨利','配對淨利差','每股毛價差改善'],[
            [r['signal_time'],r['status']+'/'+str(r['b_candidate_status']),str(r['a_entry_price'])+' → '+str(r['b_entry_price']),
             money(r['a_net_profit']),money(r['b_net_profit']),money(r['actual_net_profit_difference']),money(r['equal_one_share_gross_price_difference'])]
            for r in sorted(item['signal_trade_differences'],key=lambda r:abs(r['actual_net_profit_difference'] if r['actual_net_profit_difference'] is not None else r['a_net_profit'] or r['b_net_profit'] or 0),reverse=True)[:5]])
        for v in RUNNERS:
            table(lines,[v+' 主要獲利交易','進場','出場／動作','股數','淨利','報酬','持有交易日'],[
                [r['trade_id'],r['entry_time']+' @'+str(r['entry_price']),r['exit_time']+' @'+str(r['exit_price'])+'/'+r['exit_action'],
                 r['shares'],money(r['net_profit']),pct(r['return_percent']),r['holding_trading_sessions']] for r in item[v]['top_5_trades'][:3]])
        lines += [f"逐筆檔：[A/B 合併逐筆紀錄]({s[:4]}_AB_trades.csv)、[A 原始交易]({s[:4]}_A_20241030_20261029.trades.csv)、[B 原始交易]({s[:4]}_B_20241030_20261029.trades.csv)、[A 持倉明細]({s[:4]}_A_holding.csv)、[B 持倉明細]({s[:4]}_B_holding.csv)、[按訊號配對差異]({s[:4]}_signal_trade_differences.csv)、[B 候選全紀錄]({s[:4]}_B_candidates.csv)。"]
    write_csv(ROOT/'tpex_net_difference_attribution.csv',detailed)
    lines += ['## 流動性與零滑價限制',
        '', '日成交股數來自原始日 K；Close × Volume 僅為日成交金額代理，非實際逐筆成交金額。成交參與率以實際委託股數除以該成交棒整根成交股數，屬事後容量診斷；整根成交量在 Open 當下尚未知，也不代表開盤可成交深度，未被策略用於選股、下單或調整成交。']
    table(lines,['股票','日股數中位數','最低日股數','收盤×量代理中位數','A 最大整棒參與率','B 最大整棒參與率'],[
        [s,f"{entries[s]['market']['median_daily_volume_shares']:,.0f}",f"{entries[s]['market']['minimum_daily_volume_shares']:,.0f}",
         money(entries[s]['market']['median_daily_close_times_volume_proxy']),pct(entries[s]['A']['max_execution_bar_participation_percent']),
         pct(entries[s]['B']['max_execution_bar_participation_percent'])] for s in ALL])
    lines += ['新三檔的最大整棒參與率約 3.43%～7.50%，高於三檔上市股本次實際成交的整棒比例；其中穩懋 B 達 7.4977%，順達 B 達 5.7519%。即使以整小時量看似仍可容納，也無法據此宣稱 Open 全額成交合理，對上櫃結果應特別保留此執行假設。']
    lines += ['未建模買賣價差、委託簿深度、排隊、部分成交、價格衝擊、漲跌停鎖單、整零股撮合差異。60 分 Open 是該棒第一筆成交，不保證指定秒數或全額可成交；完整 1 分 K 只能確認原生棒範圍，無法證明策略訂單可按 Open 全數成交。三檔各組的 *_execution_liquidity.csv 保留每筆參與率，供後續研究。',
        '', '## 資料完整性與可重現性',
        '', '資料取自先前封存的 Fugle TPEx v2 快照。本次直接重驗 payload digest、檔案 SHA-256、TPEx/OTC 身分、完整 K 棒格線與暖身門檻，不重新下載、不補造、不縮短期間。三檔各 2,480 根原生 60 分 K（正式期 2,420＋暖身 60）、604 日 K（正式期 484＋暖身 120）；60 分暖身自 2024-10-14 09:00、日線暖身自 2024-05-06。',
        '', '既有資料驗證已用全期間 1 分來源逐根比對共 7,440 根原生 60 分 OHLCV；本次核對該驗證檔雜湊並沿用其證據，沒有以 1 分 K 改造策略價格。原生分 K 量單位為張，正規化乘 1,000 為股；日 K 原為股。所有時戳 UTC+08:00，13:00 尾棒含 13:30 收盤撮合；未出現超過 13:30 的來源晚收盤。',
        '', '官方年度暫停／恢復交易清單在指定三檔沒有命中，且原生格線完整；不等同排除盤中短暫延後撮合、處置措施或可成交性限制。日曆保留原先「使用者確認」的 2026-07-10、2026-10-23 臨時休市來源，尚無本次獨立 TPEx 緊急公告佐證，不能將此列為已由官方重新確認。此限制同樣保留於六檔比較。',
        '', '本次因果與重播檢查：獨立訊號重算、完成日線 bisect 狀態機重建全部成交，逐筆核對 A 的 i+1／B 的 i+2、SELL／EXIT 下一棒、Open 價、最大可負擔股數、費稅及現金；獨立重建每個 Open／Close 權益與最大回撤，核對候選生命週期及買進持有。',
        '', '各檔各組另以真正截斷的資料重跑 2024-10-30、2025-10-29、2026-10-28：比較該日前所有 fills、trades、events、raw_signals、equity_curve，共 18 次通過。再作 6 次完整離線重播，輸出字典（含 metadata）完全相等。這提供對既有邏輯與本期間的無前視偏差驗證，並非對所有未來輸入的形式證明。',
        '', f"回歸測試共 {sum(t['tests'] for t in tests)} 項通過（263 項策略／資料／Provider，12 項 B 確認合成案例）。保護基線核對 {protected['existing_files_checked']} 個既有檔案，變更 0；原策略、A/B 模組、Provider、台股助手及上市股既有輸出均未更動。僅新增 run_tpex_ab.py、report_tpex_ab.py 與本研究目錄。",
        '', '快照及結果 SHA-256：']
    table(lines,['股票','輸入快照 SHA-256','A 結果 SHA-256','B 結果 SHA-256'],[
        [s,entries[s]['source_snapshot_sha256'],entries[s]['A']['result_sha256'],entries[s]['B']['result_sha256']] for s in ALL])
    lines += ['## 樣本限制與下一步',
        '', '六檔、單一兩年窗口且多數曝險電子產業，市場及產業因子高度相依，不能把股票當成六次獨立試驗。TPEx 三檔為使用者指定，既有上市股亦是既有研究標的；沒有隨機抽樣、盲選或完整下市股宇宙，存在存活者與研究選樣限制。波動、穿越、前幾大獲利交易為同期間的描述，無法單獨建立因果或穩健性結論。',
        '', '下一步建議先補齊兩個臨時休市日的官方佐證與除權息／減資事件資料，再另立研究案（不覆寫本結果）檢驗含息基準、公司行動與可成交性；以報價／逐筆成交估計固定且事前公告的價差與衝擊情境，保留本次零滑價為對照。其後鎖定 A/B 原規則，在未見過的期間及預先登記的流動性分層股票宇宙做樣本外驗證，納入下市／停牌標的並採市場區塊抽樣評估不確定性。不得依本次報酬回頭挑股、門檻或調參。',
        '', '## 檔案與重跑',
        '', '[六檔摘要 CSV](six_stock_summary.csv)、[六檔持倉逐筆 CSV](six_stock_holding_trades.csv)、[連續月權益 CSV](six_stock_monthly_equity.csv)、[完整比較 JSON](comparison.json)、[入口驗證](entry_validation.json)、[因果／重播驗證](audit_validation.json)、[測試紀錄](tests_result.json)、[最終驗證](validation.json)。原始上市股檔案僅讀取；封裝另附其精確副本供重現。',
        '', '由台股目錄依序執行（無金鑰／無網路）：',
        '', '```powershell',
        *[f'python -B -X utf8 -m 666_strategy.experiments.run_tpex_ab --stage {s}' for s in ('preflight','run','audit','compare','tests','report','package')],
        '```', '', '完整 JSON 保存所有原始訊號、事件、權益曲線、成交、交易及候選；CSV 為方便稽核的輸出。reproduction_bundle.zip 包含執行所需模組、日曆、六檔快照、既有比較依賴檔與本次結果；SHA256SUMS.json 校驗封裝每個檔案。需使用 entry_validation.json 所載 Python／pandas／numpy 環境，於解壓後的台股目錄執行相同命令。']
    (ROOT/'SIX_STOCK_AB_REPORT.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    write_json(ROOT/'validation.json',dict(status='PASSED',period=[START,END],symbols=SYMBOLS,
        formal_backtest=True,strategy_changes=False,provider_changes=False,original_assistant_changes=False,prior_results_changes=False,
        parameter_optimization=False,network_requests=0,no_fabrication=True,no_shortening=True,
        independent_audits=6,exact_full_replays=6,causal_prefix_replays=18,tests=sum(t['tests'] for t in tests),
        data_gate='PASSED',remaining_evidence=['2026-07-10 and 2026-10-23 calendar provenance remains user-confirmed',
        'zero slippage, whole-share execution and unadjusted price/no-dividend assumptions unchanged',
        'user-designated sample; no sample-out inference'],protected=protect(),
        report_sha256=sha(ROOT/'SIX_STOCK_AB_REPORT.md'),comparison_sha256=sha(ROOT/'comparison.json'),
        audit_sha256=sha(ROOT/'audit_validation.json'),entry_sha256=sha(ROOT/'entry_validation.json'),
        artifact_sha256=sha(ROOT/'artifact_validation.json'),
        new_code_sha256={p.name:sha(p) for p in [Path(__file__),Path(__file__).with_name('run_tpex_ab.py')]}))
    print('REPORT COMPLETE: SIX_STOCK_AB_REPORT.md',flush=True)


def package():
    assert read(ROOT/'validation.json')['status']=='PASSED'
    protect()
    paths = {p for p in ROOT.iterdir() if p.is_file() and p.suffix not in ('.zip',) and p.name!='package_validation.json'}
    paths |= {p for p in (PROJECT/'666_strategy').rglob('*.py') if '.cache' not in p.parts and '__pycache__' not in p.parts}
    paths |= set((PROJECT/'666_strategy/calendars').glob('*.json'))
    for symbol in ALL:
        paths.add(source(symbol))
        for variant in RUNNERS:
            paths.add(result_path(symbol,variant))
    from .run_tpex_ab import OLD2454, ab
    paths |= {OLD2454/'comparison.json',ab.ROOT/'validation.json',INPUT/'validation.json'}
    paths |= {INPUT/name for name in ('data_validation.json','halt_validation.json','calendar_validation.json','research_sample.json')}
    paths |= set(INPUT.glob('*_closing_time_validation.json'))
    # Include the protected evidence so the same immutable-file gate runs after extraction.
    paths |= {PROJECT/p for p in read(ROOT/'protected_baseline.json')}
    paths |= {INPUT/'request_manifest.json'}
    archive = ROOT/'reproduction_bundle.zip'
    hashes = {p.relative_to(PROJECT.parent).as_posix():sha(p) for p in sorted(paths)}
    with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED,compresslevel=6) as z:
        for p in sorted(paths):
            z.write(p,p.relative_to(PROJECT.parent).as_posix())
        import json
        z.writestr('SHA256SUMS.json',json.dumps(hashes,ensure_ascii=False,indent=2))
    with zipfile.ZipFile(archive) as z:
        assert z.testzip() is None
        import hashlib
        for name,digest in hashes.items():
            assert hashlib.sha256(z.read(name)).hexdigest()==digest
        with tempfile.TemporaryDirectory(prefix='tpex_replay_') as temp:
            target=Path(temp).resolve()
            assert target.parent==Path(tempfile.gettempdir()).resolve() and target.name.startswith('tpex_replay_')
            z.extractall(target)
            done=subprocess.run([sys.executable,'-B','-X','utf8','-m','666_strategy.experiments.run_tpex_ab',
                '--stage','preflight'],cwd=target/PROJECT.name,stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
            assert done.returncode==0,done.stdout.decode('utf-8')
            extracted_preflight=read(target/PROJECT.name/ROOT.relative_to(PROJECT)/'entry_validation.json')
            assert extracted_preflight['status']=='PASSED' and extracted_preflight['network_disabled']
    write_json(ROOT/'package_validation.json',dict(status='PASSED',archive=archive.name,
        sha256=sha(archive),bytes=archive.stat().st_size,files=len(hashes),
        all_entries_hash_verified=True,extracted_independent_directory_preflight='PASSED',
        extracted_snapshot_grids_and_both_runners_checked=True,protected=protect()))
    print('PACKAGE COMPLETE '+str(archive.stat().st_size)+' bytes',flush=True)
