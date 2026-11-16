"""Render the fixed, audited 2454 replication and unchanged prior comparisons."""
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
import re
import zipfile

import pandas as pd

from ..history import TAIPEI
from ..verification.analyze_fugle_trades import write_csv
from ..verification.verify_previous_fugle_year import sha, write_json
from .run_mediatek_ab import (PROJECT, ROOT, PRIOR, SNAPSHOT, SOURCE, START, END, read,
                              result_path, protect, exposure_regimes)


def money(x):
    return '—' if x is None else f'{x:,.2f}'


def pct(x):
    return '—' if x is None else f'{x:.4f}%'


def table(lines, headers, rows):
    lines.extend(['', '| '+' | '.join(headers)+' |', '| '+' | '.join(['---']*len(headers))+' |'])
    lines.extend('| '+' | '.join(str(c) for c in row)+' |' for row in rows)
    lines.append('')


def test_records():
    records = []
    for name, path, directory, count in [('existing', ROOT/'existing_tests.log','666_strategy/tests',251),
                                          ('experiment', ROOT/'experiment_tests.log','666_strategy/experiments/tests',12)]:
        raw = path.read_bytes()
        text = raw.decode('utf-16') if raw.startswith((b'\xff\xfe',b'\xfe\xff')) else raw.decode('utf-8-sig')
        match = re.search(r'Ran (\d+) tests in ([\d.]+)s', text)
        assert match and int(match[1])==count and text.rstrip().endswith('OK'), name
        records.append(dict(name=name,command=f'python -B -X utf8 -m unittest discover -s {directory} -v',
            exit_code=0,tests=count,elapsed_seconds=float(match[2]),log=path.name,log_sha256=sha(path)))
    write_json(ROOT/'tests_result.json',records)
    return records


def independent_benchmark(saved):
    """Decimal accounting with independent whole-share search, no strategy methods."""
    c, b = saved['config'], saved['benchmark']
    D = lambda x: Decimal(str(x))
    cash, price, mark = D(c['initial_cash']), D(b['entry_price']), D(b['mark_price'])
    rounded = lambda x:x.quantize(D('.01'),rounding=ROUND_HALF_UP)
    shares = int(cash/price)
    while True:
        gross = rounded(shares*price)
        fee = rounded(max(D(c['minimum_commission']),gross*D(c['commission_rate'])))
        if gross+fee<=cash:
            break
        shares -= 1
    ending = cash-gross-fee+rounded(shares*mark)
    assert shares==b['shares'] and fee==D(b['entry_commission']) and ending==D(b['ending_equity'])
    assert abs(float((ending/cash-1)*100)-b['return_percent'])<1e-10
    return dict(passed=True,shares=shares,entry_commission=float(fee),ending_equity=float(ending))


def supplementary_exports(a,b):
    monthly = []
    for variant,saved in [('A',a),('B',b)]:
        previous = saved['config']['initial_cash']
        groups = {}
        for point in saved['equity_curve']:
            if point['phase']=='CLOSE':
                groups[point['at'][:7]] = point
        for month, point in groups.items():
            monthly.append(dict(variant=variant,month=month,at=point['at'],ending_equity=point['equity'],
                net_change=round(point['equity']-previous,2),return_percent=(point['equity']/previous-1)*100,
                cumulative_return_percent=(point['equity']/saved['config']['initial_cash']-1)*100))
            previous = point['equity']
        write_csv(ROOT/f'2454_{variant}_exposure_regimes.csv',exposure_regimes(saved))
    write_csv(ROOT/'2454_monthly_equity.csv',monthly)
    return monthly


def render():
    protected = protect()
    study = read(ROOT/'comparison.json')
    audit = read(ROOT/'audit_validation.json')
    assert audit['status']=='PASSED' and study['snapshot_sha256']==sha(SNAPSHOT)
    assert all(v['passed'] for v in study['audits'].values())
    assert set(study['symbols'])=={'2330','2408','2454'}
    a,b = [read(result_path(v)) for v in ('A','B')]
    for v in ('A','B'):
        assert sha(result_path(v))==study['symbols']['2454']['result_sha256'][v]
    tests = test_records()
    benchmark_audit = independent_benchmark(a)
    monthly = supplementary_exports(a,b)
    item = study['symbols']['2454']
    sa,sb = a['summary'],b['summary']
    ha,hb = item['A']['holding'],item['B']['holding']
    rows = item['signal_trade_differences']
    paired = [r for r in rows if r['status']=='DELAYED_MATCHED_ENTRY']
    omitted = [r for r in rows if r['status']=='A_ONLY']
    delta = sb['total_net_profit']-sa['total_net_profit']
    cost_a,cost_b = item['A']['costs'],item['B']['costs']
    biggest = max(paired,key=lambda r:r['actual_net_profit_difference'])
    assert len(paired)==16 and not omitted and item['b_candidate_statuses']=={'FILLED':16}
    assert all(r['a_exit_time']==r['b_exit_time'] and r['a_exit_action']==r['b_exit_action'] for r in paired)
    assert abs(sum(r['actual_net_profit_difference'] for r in paired)-delta)<1e-6
    lines = ['# 聯發科（2454）666_strategy 固定 A/B 回測驗證', '',
        f'研究產製日期：{datetime.now(TAIPEI).date()}（Asia/Taipei）。固定回測區間 **{START}～{END}**，'
        '每組初始新臺幣 1,000,000 元、空手起始，連續兩年不重設本金或持倉。', '',
        f'**結果：A 報酬 {pct(sa["total_return_percent"])}、B {pct(sb["total_return_percent"])}；'
        f'B 改善 {sb["total_return_percent"]-sa["total_return_percent"]:.6f} 個百分點、'
        f'最大回撤減少 {sa["max_drawdown_percent"]-sb["max_drawdown_percent"]:.6f} 個百分點。'
        '兩組同為 16 筆交易，B 的 16 次候選全部確認通過，沒有過濾掉交易。**', '',
        f'因此，本樣本的改善來自延後進場價格與後續資金路徑，無法當作「過濾短期假突破」的成功證據。'
        f'B 勝率反而由 {pct(sa["win_rate_percent"])} 降至 {pct(sb["win_rate_percent"])}；'
        f'兩組仍明顯落後買進持有 {pct(a["benchmark"]["return_percent"])}。', '',
        '## 固定規則、成本與成交口徑', '',
        '- A 直接呼叫原始 `666_strategy.backtest.runner.run_backtest`；B 直接呼叫既有 '
        '`666_strategy.experiments.buy_confirmation.run_confirmed_backtest`。未複製改寫進出場策略，也未進行參數最佳化。',
        '- 原始 BUY：前根 Close ≤ 前根 MA60，當根 Close > 當根 MA60；SELL：前根 Close ≥ 前根 MA60，當根 Close < 當根 MA60。'
        'MA60 為 60 根原生盤中棒的簡單平均，每根含尾盤短棒等權。',
        '- 日線 BULL 同時要求 Close > 日 MA60 > 日 MA120、日 MA60 > 五個交易日前的日 MA60。'
        '只使用當時已完成的日 K；13:30 前不使用當日日線收盤。',
        '- A：符合原有日線多頭過濾的上穿棒 i 完成後，在 i+1 Open 買進。B：保留相同日線過濾，'
        '等 i+1 收盤確認 Close 嚴格高於 i+1 自己的 MA60、日線仍 BULL，才在 i+2 Open 買進。'
        '相等或失敗即消耗訊號，必須等待新的上穿；成交前日線重查／取消機制完全沿用。',
        '- SELL、日線 NON_BULL 的 EXIT、EXIT 優先順序及下一根 Open 出場全部不變。'
        '跨夜使用下一個已確認交易棒；月底、跨年均不重設。期間末保留真正未平倉與 pending，不強制結清。',
        '- 成本設定直接讀自既有 2330 A：全可用現金買可負擔的整數股（share_unit=1）；'
        '手續費 0.1425%、單筆最低 20 元；所有賣出稅固定 0.3%，包括模型內當日買賣，'
        '不另套當沖優惠；金額以 Decimal 四捨五入至分。無滑價、融資、放空、股利再投資或股利收入。', '',
        '## Fugle 資料、完整性與快照', '',
        '使用既有 FugleProvider／FugleClient 從 Fugle 真實 API 分段查詢，單段不超過 180 個日曆日。'
        '保存成功回應的白名單欄位（來源時間、OHLCV、原始成交量單位）、請求日期、HTTP 狀態、收到時間與 SHA-256；'
        '不保存金鑰或認證標頭。第一次沙箱連線失敗的紀錄保留於 capture_failure.json，取得網路許可後成功下載並完整驗證。', '',
        '官方 [Fugle Historical Candles 文件](https://developer.fugle.tw/docs/data/http-api/historical/candles/) '
        '說明查詢區間須小於一年、分 K 與日 K 的成交量單位及範圍不同。本研究沿用未還原價；分 K 張數換為股數，'
        '日 K 保持股數，且不要求日量等於盤中量加總。']
    table(lines,['項目','驗證結果'],[
        ['回測交易日／盤中棒','484 日／2,420 根'],
        ['60 分 K 暖身與總筆數','2024-10-14 09:00 起，60 根暖身；共 2,480 根'],
        ['日 K 暖身與總筆數','2024-05-06 起，120 個先前交易日；共 604 筆'],
        ['截止時間','2026-10-29 13:30 Asia/Taipei'],
        ['缺棒／無效／重複／補造','0／0／0／0；完整格網驗證通過，沒有刪日縮短區間'],
        ['尾盤棒','每天 09、10、11、12、13 時共五根；13:00 棒於 13:30 完成'],
        ['集合競價抽查','2024-10-30、2026-10-29 的原生 13:00 棒，與當日 13 時後 1 分 K（含 13:30）OHLCV 逐欄一致'],
        ['快照重建','同一來源報告重建兩次位元組完全相同；payload digest 與檔案 SHA-256 通過']])
    lines += [f'快照 SHA-256：`{sha(SNAPSHOT)}`。來源報告 SHA-256：`{sha(SOURCE)}`。', '',
        '日曆完整沿用既有 2024／2025／2026 版（每年檔案雜湊保存於來源報告），'
        '包括原紀錄以使用者確認的 2026-07-10、2026-10-23 臨時休市；本研究未重新把這兩日標示成獨立官方驗證。'
        '2454 與既有兩檔的 484 個交易日序列完全相同，行情缺失不會被推論為休市。'
        '資料完整性結論以此既有日曆為條件。', '', '## 2454 A/B 完整績效']
    table(lines,['指標','A 原始 666','B 多一根確認','B−A'],[
        ['期末權益',money(sa['ending_equity']),money(sb['ending_equity']),money(delta)],
        ['兩年報酬',pct(sa['total_return_percent']),pct(sb['total_return_percent']),f'{sb["total_return_percent"]-sa["total_return_percent"]:+.6f} 百分點'],
        ['最大回撤',pct(sa['max_drawdown_percent']),pct(sb['max_drawdown_percent']),f'{sb["max_drawdown_percent"]-sa["max_drawdown_percent"]:+.6f} 百分點'],
        ['完成來回交易',sa['completed_trades'],sb['completed_trades'],sb['completed_trades']-sa['completed_trades']],
        ['勝／負',f'{sa["winning_trades"]}／{sa["completed_trades"]-sa["winning_trades"]}',f'{sb["winning_trades"]}／{sb["completed_trades"]-sb["winning_trades"]}','—'],
        ['勝率',pct(sa['win_rate_percent']),pct(sb['win_rate_percent']),f'{sb["win_rate_percent"]-sa["win_rate_percent"]:+.4f} 百分點'],
        ['手續費',money(sa['commissions']),money(sb['commissions']),money(sb['commissions']-sa['commissions'])],
        ['交易稅',money(sa['transaction_taxes']),money(sb['transaction_taxes']),money(sb['transaction_taxes']-sa['transaction_taxes'])],
        ['費稅合計',money(cost_a),money(cost_b),money(cost_b-cost_a)],
        ['含費稅淨利',money(sa['total_net_profit']),money(sb['total_net_profit']),money(delta)],
        ['毛價差損益',money(sa['total_net_profit']+cost_a),money(sb['total_net_profit']+cost_b),money(delta+cost_b-cost_a)],
        ['持股市場時間占比',pct(item['A']['exposure_percent']),pct(item['B']['exposure_percent']),f'{item["B"]["exposure_percent"]-item["A"]["exposure_percent"]:+.4f} 百分點'],
        ['期末持股／待成交／待確認',f'{a["open_position"]}／{a["pending_decision"]}／無',f'{b["open_position"]}／{b["pending_decision"]}／{b.get("pending_candidate")}','—']])
    lines += ['最大回撤按初始本金及每根 Open 成交後／完成 Close 權益計算，不推測棒內最高低價先後。'
        '勝率與持倉分布以完成來回交易計算，費稅包含所有實際模擬成交。'
        '持股時間占比以持倉棒的市場分鐘加總除以 484×270 分鐘；尾盤棒計 30 分鐘。', '',
        f'A 回撤高點／低點：{sa["drawdown_peak_at"]}／{sa["drawdown_trough_at"]}；'
        f'B：{sb["drawdown_peak_at"]}／{sb["drawdown_trough_at"]}。兩組回撤主要落在相同市場階段。', '',
        f'買進持有在 {a["benchmark"]["entry_time"]} 以 {money(a["benchmark"]["entry_price"])} 元買 '
        f'{a["benchmark"]["shares"]} 股，買入手續費 {money(a["benchmark"]["entry_commission"])} 元；'
        f'期末以 {money(a["benchmark"]["mark_price"])} 元評價，權益 {money(a["benchmark"]["ending_equity"])} 元、'
        f'報酬 {pct(a["benchmark"]["return_percent"])}；純價格報酬 {pct(a["benchmark"]["price_return_percent"])}。'
        '基準未假設賣出，因此不扣假設的出場費稅，也未計股利；此口徑與既有兩檔相同。', '',
        f'B 較 A 增加 {money(delta)} 元，由毛價差損益增加 {money(delta+cost_b-cost_a)} 元、'
        f'費稅節省 {money(cost_a-cost_b)} 元組成。兩組交易數相同，費稅差主要反映價格與可買股數差。'
        '毛價差損益包含部位股數與資金累積效應，不能全歸因於單筆確認價格。', '', '## 持倉分布']
    table(lines,['持倉指標','A','B'],[
        ['平均／中位日曆天數',f'{ha["mean_days"]:.4f}／{ha["median_days"]:.4f}',f'{hb["mean_days"]:.4f}／{hb["median_days"]:.4f}'],
        ['當日買賣筆數',ha['same_day']['count'],hb['same_day']['count']],
        ['持股 ≤3 交易日：筆數／淨損益',f'{ha["short_up_to_3_sessions"]["count"]}／{money(ha["short_up_to_3_sessions"]["net_profit"])}',f'{hb["short_up_to_3_sessions"]["count"]}／{money(hb["short_up_to_3_sessions"]["net_profit"])}'],
        ['前次出場後 ≤3 交易日再進場：筆數／淨損益',f'{ha["rapid_reentry"]["count"]}／{money(ha["rapid_reentry"]["net_profit"])}',f'{hb["rapid_reentry"]["count"]}／{money(hb["rapid_reentry"]["net_profit"])}']])
    for key,label in [('calendar_distribution','持有日曆天數'),('session_distribution','持股交易日數')]:
        table(lines,[label,'A 筆數','B 筆數','A 淨損益','B 淨損益'],[
            [x['bin'],x['count'],y['count'],money(x['net_profit']),money(y['net_profit'])]
            for x,y in zip(ha[key],hb[key])])
    lines += ['日曆天數包含夜間、假日；持股交易日數只計有持倉棒的日期，不含出場棒。'
        '短單與快速再進場群組可重疊，不能相加。16 筆共同交易均延後進場且出場相同；'
        '平均持有時間較短直接來自延後一根，沒有趨勢持有時間延長的證據。', '', '## 2454 全部逐筆交易與主要差異']
    table(lines,['原始 BUY 訊號時間（台北）','A 買進價','B 買進時間／價','共同出場時間','A 淨利','B 淨利','B−A'],[
        [r['signal_time'][:16],money(r['a_entry_price']),r['b_entry_time'][:16]+'／'+money(r['b_entry_price']),
         r['a_exit_time'][:16],money(r['a_net_profit']),money(r['b_net_profit']),money(r['actual_net_profit_difference'])] for r in rows])
    lines += [f'所有 16 筆按原始 BUY 訊號時間配對，出場動作均為 SELL；B 買價較高 '
        f'{sum(r["b_entry_price"]>r["a_entry_price"] for r in paired)} 筆、較低 '
        f'{sum(r["b_entry_price"]<r["a_entry_price"] for r in paired)} 筆、相同 '
        f'{sum(r["b_entry_price"]==r["a_entry_price"] for r in paired)} 筆。'
        '成交時點、股數、進出費用、報酬及取消明細見完整 CSV／JSON。', '',
        f'最大的正向差異為 {biggest["signal_time"][:16]}：A 在 {biggest["a_entry_time"][:16]} 以 '
        f'{money(biggest["a_entry_price"])} 買進，B 等尾盤確認後跨夜，在 {biggest["b_entry_time"][:16]} 以 '
        f'{money(biggest["b_entry_price"])} 買進；兩組於 {biggest["a_exit_time"][:16]} 同時出場，'
        f'淨利差 {money(biggest["actual_net_profit_difference"])} 元。'
        'B 確認時只知道前一日收棒，沒有使用隔日開盤後的資料；較低的次日 Open 是實現價格結果，不能預先保證。', '',
        f'這筆差額大於整體 B−A 的 {money(delta)} 元；其餘 15 筆配對差額合計 '
        f'{money(delta-biggest["actual_net_profit_difference"])} 元。此為既有實際路徑的帳務貢獻拆解，'
        '不是刪掉該交易後重新回測，也不是統計穩健性檢定。', '',
        '2025-02-13 與 2025-02-24 的微幅獲利交易，在 B 因較高買價轉為虧損，'
        '解釋勝率由 6/16 變為 4/16。確認通過不代表較佳的成交價格或較高勝率。', '',
        '## 與既有 2330、2408 的同條件比較', '',
        '直接讀取既有、雜湊核對通過的 2330／2408 A/B 結果，未重下載、改寫或替換舊資料。'
        '三檔日期、本金、成本與規則相同，每檔每組獨立帳戶，非三檔合併投資組合。'
        '2454 為本次 API 擷取，另外兩檔為既有快照；資料版本／取得時間差異仍列入限制。']
    summary_rows = []
    for symbol,s in study['symbols'].items():
        for variant in ('A','B'):
            v = s[variant]; z = v['summary']
            summary_rows.append(dict(symbol=symbol,variant=variant,ending_equity=z['ending_equity'],
                return_percent=z['total_return_percent'],max_drawdown_percent=z['max_drawdown_percent'],
                completed_trades=z['completed_trades'],winning_trades=z['winning_trades'],win_rate_percent=z['win_rate_percent'],
                commissions=z['commissions'],taxes=z['transaction_taxes'],costs=round(v['costs'],2),exposure_percent=v['exposure_percent'],
                buy_and_hold_return_percent=z['buy_and_hold_return_percent']))
    write_csv(ROOT/'three_stock_summary.csv',summary_rows)
    table(lines,['股票','組別','報酬','最大回撤','交易數','勝率','費稅','買進持有'],[
        [r['symbol'],r['variant'],pct(r['return_percent']),pct(r['max_drawdown_percent']),r['completed_trades'],
         pct(r['win_rate_percent']),money(r['costs']),pct(r['buy_and_hold_return_percent'])] for r in summary_rows])
    table(lines,['股票','B−A 報酬（百分點）','B−A 回撤（百分點）','交易數差','費稅差','B 取消／候選'],[
        [symbol,f'{s["B"]["summary"]["total_return_percent"]-s["A"]["summary"]["total_return_percent"]:+.6f}',
         f'{s["B"]["summary"]["max_drawdown_percent"]-s["A"]["summary"]["max_drawdown_percent"]:+.6f}',
         s['B']['summary']['completed_trades']-s['A']['summary']['completed_trades'],money(s['B']['costs']-s['A']['costs']),
         f'{sum(n for k,n in s["b_candidate_statuses"].items() if k.startswith("CANCELLED"))}／{sum(s["b_candidate_statuses"].values())}']
        for symbol,s in study['symbols'].items()])
    lines += ['### 短期反覆穿越 MA60', '',
        '下表以所有「原始 BUY 時刻日線 BULL」訊號為母體，並非只挑實際成交或虧損交易。'
        '從上穿棒 i 到下一個實際 SELL 下穿棒 j 的索引距離為 j−i；≤3 交易日為日期索引差 ≤3，'
        '包含同日，不等於 15 根棒或持股三天。遇等於 MA60 不算 SELL，但連續在上方的棒數於相等時結束。'
        '沒有後續 SELL 的訊號列為右設限，不當作快速反轉；固定 1／3／5 棒只是描述尺度，沒有用來調整策略。']
    table(lines,['股票','BULL 原始 BUY','次棒未高於 MA60','≤1 棒下穿','≤3 棒下穿','≤5 棒下穿','≤3 交易日下穿','下穿距離中位棒數','右設限'],[
        [symbol,c['count'],c['next_close_not_above'],c['sell_within_1_bar'],c['sell_within_3_bars'],
         c['sell_within_5_bars'],c['sell_within_3_sessions'],c['median_bars_to_sell'],c['right_censored']]
        for symbol,s in study['symbols'].items() for c in [s['crossing']['BULL_BUY']]])
    table(lines,['股票','全期間原始 BUY／SELL','其中日線 BULL BUY','其餘非 BULL BUY'],[
        [symbol,f'{s["crossing"]["raw_signal_counts"].get("BUY",0)}／{s["crossing"]["raw_signal_counts"].get("SELL",0)}',
         s['crossing']['BULL_BUY']['count'],s['crossing']['all_BUY']['count']-s['crossing']['BULL_BUY']['count']]
        for symbol,s in study['symbols'].items()])
    table(lines,['股票','次棒未高於 MA60 比率','≤3 棒下穿比率','≤5 棒下穿比率','實際 B 候選取消率'],[
        [symbol,pct(c['next_close_not_above']/c['count']*100),pct(c['sell_within_3_bars']/c['count']*100),
         pct(c['sell_within_5_bars']/c['count']*100),
         pct(sum(n for k,n in s['b_candidate_statuses'].items() if k.startswith('CANCELLED'))/sum(s['b_candidate_statuses'].values())*100)]
        for symbol,s in study['symbols'].items() for c in [s['crossing']['BULL_BUY']]])
    lines += ['前三項比率分母為原始日線 BULL BUY，包含已持倉時再次上穿；候選取消率分母為實際空手候選。'
        '2454 的 74 次原始 BUY 中，57 次被 NON_BULL 擋下、16 次空手建立 BUY、1 次發生於已持倉。'
        '因此原始 BULL BUY 為 17 次，實際候選及交易為 16 次，兩者沒有漏算。'
        '2454 的 BULL 上穿後至下穿中位數為 8 棒，長於 2330 的 3 棒與 2408 的 6 棒；'
        '仍有 5/17 在三棒內下穿，顯示沒有次棒失敗不等於沒有較晚的短期反覆穿越。', '']
    lines += ['2330 的確認取消 15 筆、實際交易減少 15 筆；既有逐筆配對顯示取消者多為 A 的虧損交易，'
        '所以短期反覆穿越的過濾與成本降低在此樣本較明顯，但 B 仍虧損且遠落後買進持有。'
        '2408 只取消 4 筆（A 原路徑中 2 勝、2 負），較少交易與省費稅不足以抵消趨勢交易的延後代價。'
        '2454 的可成交候選沒有次棒確認失敗，即使部分在接下來數棒下穿，B 仍會入場；多等一根的過濾範圍有限。', '',
        '### 趨勢持續性與主要獲利交易', '',
        '以下以固定持股期間分組、實際已完成交易的獲利集中度描述趨勢捕捉。'
        '這不是股價趨勢本身的因果估計；較少但較長的持倉可能只是短單被刪除的組成效果。']
    table(lines,['股票／組別','持股時間占比','≤3 交易日：筆／淨利','>20 交易日：筆／淨利','最大兩筆淨利','占全部正淨利'],[
        [symbol+'/'+variant,pct(v['exposure_percent']),
         f'{v["holding"]["short_up_to_3_sessions"]["count"]}／{money(v["holding"]["short_up_to_3_sessions"]["net_profit"])}',
         f'{v["holding"]["session_distribution"][-1]["count"]}／{money(v["holding"]["session_distribution"][-1]["net_profit"])}',
         money(v['top_2_profit']),pct(v['top_2_profit']/v['positive_trade_profits']*100)]
        for symbol,s in study['symbols'].items() for variant in ('A','B') for v in [s[variant]]])
    for symbol,s in study['symbols'].items():
        lines += [f'#### {symbol}：A 最大五筆獲利與 B 配對']
        table(lines,['原始 BUY','A 進場價','B 進場價／狀態','A 淨利','B 淨利','固定一股毛價差差額'],[
            [r['signal_time'][:16],money(r['a_entry_price']),money(r['b_entry_price']) if r['b_entry_price'] is not None else r['b_candidate_status'],
             money(r['a_net_profit']),money(r['b_net_profit']),money(r['equal_one_share_gross_price_difference'])]
            for r in s['profitable_A_trade_outcomes'][:5]])
    lines += ['2330 的兩筆最大贏單在 B 都保留，實際淨利較大仍受到前面較少虧損、可買股數增加影響；'
        '不能直接當作延遲進場改善價格的證據。2408 的主要大趨勢贏單也保留，但如 2025-12-02 原訊號，'
        'B 從 150 延至隔日 153 買進，主要獲利交易的價格代價與後續股數使報酬下降。'
        '2454 最大贏單為 2026-05-21，兩組買價同為 3,550，差異來自股數；'
        '第二大為 2026-09-18，B 買價 4,615 高於 A 的 4,590，兩筆在 B 淨利都較少。'
        '本次 B 的整體小幅優勢主要來自另一筆虧損交易在跨夜後取得較低進場價。', '',
        '### 為何 2454 遠落後買進持有', '',
        f'A／B 持股時間僅 {pct(item["A"]["exposure_percent"])}／{pct(item["B"]["exposure_percent"])}。'
        '原策略需要「新的上穿」與當時日線 BULL 同時成立；日線恢復 BULL 後若未出現新上穿，仍保持空手。'
        '因此股票存在大幅上漲，不代表策略能在整段行情持有；這是原規則的時機限制，本研究未改寫。']
    regimes = [r for r in exposure_regimes(a) if r['state']=='FLAT']
    table(lines,['A 實際空手區間起點','終點','盤中棒數','標的起／迄價','標的價格變化','期間原始 BUY'],[
        [r['start'][:16],r['end'][:16],r['bars'],f'{money(r["start_price"])}／{money(r["end_price"])}',
         pct(r['underlying_price_return_percent']),r['raw_buy_signals']]
        for r in sorted(regimes,key=lambda r:r['underlying_price_return_percent'],reverse=True)[:5]])
    lines += ['空手區間終點使用下一筆買進 Open，最後區間使用末根 Close；表中是未持有標的的價格變化，'
        '不含費稅、股利，也不能與策略報酬相加。原始 BUY 可能被當時日線過濾；所有日線狀態與上穿事件已另存 CSV 可逐時核對。', '',
        '## 連續資金路徑與月份輸出', '',
        '以下分段只切取同一次連續回測的權益，不是每年空手重設的獨立回測；跨界交易的未實現損益留在當時權益內。']
    segment_rows = []
    for variant,saved in [('A',a),('B',b)]:
        bound = [p for p in saved['equity_curve'] if p['at'][:10]<='2025-10-29'][-1]
        for label,initial,ending in [('第一年',1000000,bound['equity']),('第二年（延續）',bound['equity'],saved['summary']['ending_equity'])]:
            segment_rows.append([variant,label,money(initial),money(ending),pct((ending/initial-1)*100)])
    table(lines,['組別','分段','期初權益','期末權益','分段報酬'],segment_rows)
    lines += ['月末權益、月度損益與月報酬完整列於 [2454_monthly_equity.csv](2454_monthly_equity.csv)，'
        '首月僅 2024-10-30 起、末月僅至 2026-10-29，不是完整月份比較。', '',
        '## 稽核與可重現性', '',
        '- 原始信號另用獨立計算核對；A/B 每筆次棒／再下一棒 Open、日線已完成時間及候選確認均驗證。'
        '第二個獨立持倉狀態機不呼叫 B runner 或原決策函式，重建全部應有成交並與實際序列一致，避免只檢查已成交的選擇偏誤。',
        '- Decimal 獨立核對每筆整數股數可負擔上限、手續費、交易稅、現金、逐筆淨利；由成交重新重建所有 Open／Close 權益及最大回撤。'
        '買進持有的股數、費用、期末權益另以獨立 Decimal 算法核對通過。',
        '- A/B 各自由快照完整重跑，全部 JSON 欄位精確相同。各自另截斷來源至 2024-10-30、2025-10-29、2026-10-28 重跑，'
        '成交、完成交易、事件、原始訊號與權益皆等於完整結果之前綴；未借用截斷日之後行情。',
        f'- 既有策略測試 251 項與既有 A/B 測試 12 項通過，詳細命令、紀錄與雜湊見 tests_result.json。'
        f'執行前後核對 {protected["existing_files_checked"]} 個既有檔案，變更數 0；包含原策略、Provider、A/B 規則、日曆與原台股助手程式。'
        '本次只新增研究執行／報告腳本及新研究輸出目錄。', '',
        '在 `台股` 目錄重現（capture 會讀既有 Fugle 金鑰；其餘階段離線）：', '',
        '```powershell',
        'python -B -X utf8 -m 666_strategy.experiments.run_mediatek_ab --stage capture',
        'python -B -X utf8 -m 666_strategy.experiments.run_mediatek_ab --stage run',
        'python -B -X utf8 -m 666_strategy.experiments.run_mediatek_ab --stage audit',
        'python -B -X utf8 -m 666_strategy.experiments.run_mediatek_ab --stage report',
        '```', '',
        '已有成功分段會先驗證並重用，沒有背景改寫舊快照；重下載須另開資料版本，不應手動覆蓋此封存。'
        'capture_failure.json 為第一次網路受限的歷史紀錄；最終 data_validation.json、audit_validation.json、validation.json 為成功判定。', '',
        '## 樣本限制與下一步研究', '',
        '1. 三檔皆為本次選定的臺灣半導體相關股票，且共用同一個兩年市場階段；不是三個獨立市場樣本。'
        '2454 每組只有 16 筆完成交易，B 的單一跨夜案例對差額影響很大，不能宣稱確認規則普遍有效或已具統計顯著性。'
        '未進行多組參數搜尋，但既有兩檔結果已被觀察，因此 2454 也不是完全盲測；不能排除研究選擇偏誤。',
        '2. 行情為當次取得的 Fugle 未還原歷史價，資料可能修訂；不同股票的取得版本不同。'
        '本次未計股利，除息價差仍可能影響 MA 與訊號；原策略與基準皆為此固定口徑，不是總報酬比較。'
        '日曆含既有使用者確認休市，尾盤集合競價只抽查兩日；通過格網不代表每個原始報價都由另一獨立來源複核。',
        '3. 全現金整數股數與一般盤 Open 成交為模型假設；未模擬零股成交價差、滑價、委託排隊、漲跌停、流動性與資金交割限制。'
        '同時刻收棒訊號立即用次棒 Open 成交亦是理想化假設；實際訊號計算／傳輸延遲可能錯過該價。'
        '棒內風險未計入 Open／Close 回撤，實際最大回撤可能更大。',
        '4. 下一步先預先登錄相同 A/B、成本與評價口徑，擴展至不同產業、不同多空區間及預先保留的樣本，'
        '以跨股票、跨時期一致性檢驗外推性，不依本次勝負挑選標的或調整確認根數。',
        '5. 保持策略凍結，將「確認取消率」「短期再下穿率」「配對進場價差」「大贏單集中度」持續作為研究診斷。'
        '可另以區塊重抽樣／按股票與市場階段分群的方式估計不確定性，避免假設各筆交易獨立；'
        '此回測未執行該推論程序，也未宣稱單筆差額移除等於新的策略結果。',
        '6. 另立研究版本評估含股利總報酬、現實成交延遲與成本敏感度，保留本次結果作固定基線。'
        '這些屬於後續獨立研究，不包含於本次 A/B，也不應直接據此替換原策略。', '',
        '## 交付檔案', '',
        '- [A 完整回測 JSON](2454_A_20241030_20261029.json)／[B 完整回測 JSON](2454_B_20241030_20261029.json)：'
        '完整設定、事件、信號、成交、逐筆來回交易、權益及期末狀態。',
        '- [A 逐筆交易](2454_A_20241030_20261029.trades.csv)／[B 逐筆交易](2454_B_20241030_20261029.trades.csv)；'
        '[A 所有成交](2454_A_20241030_20261029.fills.csv)／[B 所有成交](2454_B_20241030_20261029.fills.csv)。',
        '- [訊號配對差異](2454_signal_trade_differences.csv)、[成交逐時差異](2454_execution_differences.csv)、'
        '[B 全部候選](2454_B_candidates.csv)、[A 獲利交易的 B 結果](2454_A_profitable_trade_outcomes.csv)。',
        '- [A 持倉](2454_A_holding.csv)／[B 持倉](2454_B_holding.csv)、兩組 timing_audit、equity_attribution、exposure_regimes CSV。',
        '- [三檔摘要](three_stock_summary.csv)、[全部比較 JSON](comparison.json)、三檔 ma60_crossing_diagnostics CSV。',
        '- [已驗證 60 分 K 與 MA60](2454_60m_validated.csv)、[日 K 指標與趨勢](2454_daily_validated.csv)、'
        '[Fugle 封存快照](2454_fugle_history.snapshot.json)、[來源報告](2454_fugle_source.json)、downloads 中的成功擷取紀錄。',
        '- [完整性驗證](data_validation.json)、[回測稽核](audit_validation.json)、[最終驗證與雜湊](validation.json)、'
        '測試紀錄與 protected_baseline.json。',
        '- [既有 2330／2408 詳細報告](../fugle_ab_confirmation_20241030_20261029/AB_ANALYSIS_REPORT.md)。', '']
    report = ROOT/'AB_2454_COMPARISON_REPORT.md'
    report.write_text('\n'.join(lines),encoding='utf-8')
    write_json(ROOT/'validation.json',dict(status='PASSED',period=[START,END],snapshot_sha256=sha(SNAPSHOT),
        source_sha256=sha(SOURCE),protected=protect(),tests=tests,benchmark_audit=benchmark_audit,
        causal_prefixes=study['causal_prefixes'],independent_audits=study['audits'],no_parameter_optimization=True,
        no_strategy_provider_app_changes=True,no_fabrication=True,no_range_shortening=True,
        report_sha256=sha(report),research_code_sha256={p.name:sha(p) for p in [Path(__file__),Path(__file__).with_name('run_mediatek_ab.py')]},
        prior_results_sha256={s:v['result_sha256'] for s,v in study['symbols'].items() if s!='2454'},
        artifacts_sha256={p.relative_to(ROOT).as_posix():sha(p) for p in sorted(ROOT.rglob('*')) if p.is_file()
            and p.name not in ('validation.json','2454_AB_research_bundle.zip','bundle_sha256.txt')}))
    # Include the existing comparison evidence as copies inside the bundle only.
    bundle = ROOT/'2454_AB_research_bundle.zip'
    with zipfile.ZipFile(bundle,'w',compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(ROOT.rglob('*')):
            if path.is_file() and path not in (bundle,ROOT/'bundle_sha256.txt'):
                archive.write(path,'2454/'+path.relative_to(ROOT).as_posix())
        for path in sorted(PRIOR.glob('*')):
            if path.is_file() and path.suffix in ('.json','.csv','.md'):
                archive.write(path,PRIOR.name+'/'+path.name)
        for path in [Path(__file__),Path(__file__).with_name('run_mediatek_ab.py')]:
            archive.write(path,'research_code/'+path.name)
    (ROOT/'bundle_sha256.txt').write_text(sha(bundle)+'  '+bundle.name+'\n',encoding='ascii')
    print('Report, exports, final validation and research bundle saved.',flush=True)


if __name__=='__main__':
    render()
