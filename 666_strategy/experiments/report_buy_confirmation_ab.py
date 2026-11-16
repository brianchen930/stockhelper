"""Render audited experimental artifacts without rerunning or tuning a strategy."""
import json
from pathlib import Path

from ..verification.verify_previous_fugle_year import sha, write_json
from .run_buy_confirmation_ab import ROOT, ORIGINAL, PROJECT, SNAPSHOT, START, END, SYMBOLS, read, path


def money(value):
    return '—' if value is None else f'{value:,.2f}'


def render():
    artifact=read(ROOT/'comparison.json')
    assert set(artifact['symbols'])==set(SYMBOLS)
    assert artifact['period']==[START,END]
    assert artifact['snapshot_sha256']==sha(SNAPSHOT)
    baseline=read(ROOT/'protected_baseline.json')
    changed=[p for p,h in baseline.items() if not (PROJECT/p).is_file() or sha(PROJECT/p)!=h]
    assert not changed,changed
    tests=read(ROOT/'tests_result.json')
    assert {r['name'] for r in tests}=={'existing','experiment'} and all(r['exit_code']==0 for r in tests)
    for symbol,item in artifact['symbols'].items():
        assert item['original_A_exact'] and item['identical_raw_signals'] and item['identical_benchmark']
        assert all(a['passed'] for a in item['audits'].values())
        for variant in ('A','B'):
            assert sha(path(symbol,variant))==item['output_sha256'][variant]
    validation=dict(status='PASSED',period=[START,END],source_snapshot_sha256=sha(SNAPSHOT),
        source_validation_sha256=sha(ORIGINAL/'validation.json'),source_coverage=read(ORIGINAL/'validation.json')['full_coverage'],
        protected_existing_files=len(baseline),changed_existing_files=changed,
        tests=tests,independent_audits={s:v['audits'] for s,v in artifact['symbols'].items()},
        b_causal_prefixes={s:v['b_prefix_checks'] for s,v in artifact['symbols'].items()},
        source_files_sha256={p.relative_to(Path(__file__).parent).as_posix():sha(p)
                             for p in sorted(Path(__file__).parent.rglob('*.py'))},
        artifacts_sha256={p.name:sha(p) for p in sorted(ROOT.glob('*.csv'))},
        no_parameter_optimization=True,no_live_orders=True,no_fabrication=True,no_range_shortening=True)
    write_json(ROOT/'validation.json',validation)
    lines=['# 666_strategy 獨立 A/B：BUY 多一根收棒確認', '',
        f'固定期間 **{START}～{END}**；2330 台積電、2408 南亞科。每檔、每組各自從 100 萬元空手開始，'
        '兩年內資金、持股及均線歷史均持續累積，不跨年重設。實驗結果未回寫原策略。', '',
        '## 固定實驗定義', '',
        '- **A：原始 666**。直接呼叫既有 run_backtest，所有輸出欄位與前次連續兩年結果精確相同。',
        '- **B：一根確認**。原始 BUY 上穿棒記為 i，先保留原本日線多頭過濾；等 i+1 棒完成，'
        '要求 i+1 的 Close **嚴格大於 i+1 自己的 MA60**，且該時刻日線仍為 BULL，才能在 i+2 棒 Open 買進。',
        '- 確認失敗立即消耗該次訊號，等於 MA60 也失敗；後續須有新的原始上穿，不能延用舊訊號。'
        '確認通過後，成交前仍沿用原本日線重查／取消機制。SELL／EXIT 沿用原決策函式與優先順序。',
        '- 期間末若尚未有確認棒或下一個成交 Open，保留 pending 狀態，不讀區間外行情，不強制平倉。',
        '- 使用同一份既有 Fugle 合併快照。每檔 484 個交易日，60 分 K 含暖身共 2,480 根，日 K 含暖身共 604 筆；'
        '暖身為 60 根 60 分 K 及 120 個日 K，原先兩年度重疊資料已逐欄驗證一致。',
        '- Asia/Taipei；原生每天五根 60 分 K；13:00 棒至 13:30 完成，保留 Fugle 集合競價，不拼接 Yahoo 或補造行情。',
        '- 兩組均為全可用資金買整股，手續費 0.1425%、每筆最低 20 元，賣出稅 0.3%，滑價 0、未計股利。'
        '唯一實驗變因是 BUY 的確認延遲；未搜尋或最佳化參數。', '',
        f"行情快照 SHA-256：`{sha(SNAPSHOT)}`。本次完全離線，未讀取金鑰或接入券商。", '',
        '## 兩年績效', '',
        '| 股票 | 組別 | 期末權益 | 報酬率 | 最大回撤 | 完成交易／勝 | 勝率 | 手續費 | 交易稅 | 費稅合計 |',
        '|---|---|---:|---:|---:|---:|---:|---:|---:|---:|']
    for symbol,v in artifact['symbols'].items():
        for variant in ('A','B'):
            s=v[variant]['summary']
            lines.append(f"| {symbol} | {variant} | {money(s['ending_equity'])} | {s['total_return_percent']:.6f}% | {s['max_drawdown_percent']:.6f}% | "
                f"{s['completed_trades']}／{s['winning_trades']} | {s['win_rate_percent']:.4f}% | {money(s['commissions'])} | {money(s['transaction_taxes'])} | {money(s['commissions']+s['transaction_taxes'])} |")
    lines += ['', '勝率、持倉分布以已完成來回交易計算；期末未實現部位另列。最大回撤以初始本金及每根 Open 成交後／完成 Close 淨值計算，'
        '未推測棒內 High／Low 路徑。比較差值以下均為 B−A；回撤降低表示改善。', '',
        '| 股票 | 報酬差（百分點） | 最大回撤差（百分點） | 交易數差 | 費稅差（元） | 買進持有報酬（兩組共用） |',
        '|---|---:|---:|---:|---:|---:|']
    for symbol,v in artifact['symbols'].items():
        a,b=v['A']['summary'],v['B']['summary']
        lines.append(f"| {symbol} | {b['total_return_percent']-a['total_return_percent']:+.6f} | {b['max_drawdown_percent']-a['max_drawdown_percent']:+.6f} | "
            f"{b['completed_trades']-a['completed_trades']:+d} | {money(b['commissions']+b['transaction_taxes']-a['commissions']-a['transaction_taxes'])} | {a['buy_and_hold_return_percent']:.6f}% |")
    lines += ['', '買進持有於首根 Open 以相同本金買可負擔整股，含買入費用，末根 Close 評價，無假設賣出費稅。'
        '交易減少不必然讓絕對費稅降低：若 B 保留更多資金，後續交易部位可能較大。', '', '## 各檔交易路徑、持倉與大獲利機會']
    for symbol,v in artifact['symbols'].items():
        a,b=v['A'],v['B']; sa,sb=a['summary'],b['summary']; ha,hb=a['holding'],b['holding']
        diff=v['signal_trade_differences']
        paired=[r for r in diff if r['status']=='DELAYED_MATCHED_ENTRY']
        missed=[r for r in diff if r['status']=='A_ONLY']
        gained=[r for r in diff if r['status']=='B_ONLY']
        missed_wins=[r for r in missed if (r['a_net_profit'] or 0)>0]
        missed_losses=[r for r in missed if (r['a_net_profit'] or 0)<0]
        same_exits=sum(r['a_exit_time']==r['b_exit_time'] and r['a_exit_action']==r['b_exit_action'] for r in paired)
        cost_a=sa['commissions']+sa['transaction_taxes'];cost_b=sb['commissions']+sb['transaction_taxes']
        gross_a=sa['total_net_profit']+cost_a;gross_b=sb['total_net_profit']+cost_b
        lines += ['',f'### {symbol}', '',
            f"B 報酬 {sb['total_return_percent']:.6f}%，相對 A 改變 {sb['total_return_percent']-sa['total_return_percent']:+.6f} 個百分點；"
            f"最大回撤從 {sa['max_drawdown_percent']:.6f}% 變為 {sb['max_drawdown_percent']:.6f}%。"
            f"來回交易由 {sa['completed_trades']} 變為 {sb['completed_trades']}，費稅差 "
            f"{money(sb['commissions']+sb['transaction_taxes']-sa['commissions']-sa['transaction_taxes'])} 元。", '',
            f"帳務拆解：毛價差損益 A {money(gross_a)}／B {money(gross_b)} 元，差額 {money(gross_b-gross_a)} 元；"
            f"費稅節省 {money(cost_a-cost_b)} 元，兩者合計淨損益差 {money(sb['total_net_profit']-sa['total_net_profit'])} 元。"
            '毛價差差異仍包含歷次資金累積與股數變化，這是帳務恆等式，不是單一確認條件的因果估計。', '',
            f"依原始上穿時間配對，延後且兩組都有進場 {len(paired)} 筆，A 有而 B 無 {len(missed)} 筆，B 額外進場 {len(gained)} 筆。"
            f"A 有而 B 無的交易包含原先獲利 {len(missed_wins)} 筆、原先虧損 {len(missed_losses)} 筆。"
            f"B 候選訊號狀態：`{json.dumps(v['b_candidate_statuses'],ensure_ascii=False)}`。", '',
            f"這些未進場交易在 A 原路徑的獲利合計 {money(sum(r['a_net_profit'] for r in missed_wins))} 元，"
            f"虧損合計 {money(sum(r['a_net_profit'] for r in missed_losses))} 元。"
            '這是事後分類，不能直接從 A 扣掉便當成 B；B 的現金、後續股數與費稅會隨之改變。', '',
            f"A 最大回撤高點／低點：{sa['drawdown_peak_at']}／{sa['drawdown_trough_at']}；"
            f"B：{sb['drawdown_peak_at']}／{sb['drawdown_trough_at']}。", '',
            '逐筆配對使用原始 BUY 訊號時間，不能用交易編號配對。A_ONLY 表示錯過該次進場；'
            '若 B 後來以另一個新訊號買進同一趨勢，會在 CSV 的 b_other_entries_during_A_trade 額外列出，不將其誤判成整段行情完全錯過。', '',
            f"共同進場的 {len(paired)} 筆中，B 買價較高 {sum(r['price_change_percent']>0 for r in paired)} 筆、"
            f"較低 {sum(r['price_change_percent']<0 for r in paired)} 筆、相同 {sum(r['price_change_percent']==0 for r in paired)} 筆；"
            f"出場時間與動作一致 {same_exits} 筆。平均持倉若增加，須同時考慮短單被移除的組成變化；"
            '對出場相同的配對交易，B 只是較晚進場，持有時間反而少一根棒，並非延後出場。', '',
            '| 持倉統計 | A | B |','|---|---:|---:|',
            f"| 平均日曆天數 | {ha['mean_days']:.4f} | {hb['mean_days']:.4f} |",
            f"| 中位日曆天數 | {ha['median_days']:.4f} | {hb['median_days']:.4f} |",
            f"| 當日買賣筆數 | {ha['same_day']['count']} | {hb['same_day']['count']} |",
            f"| ≤3 個持股交易日筆數 | {ha['short_up_to_3_sessions']['count']} | {hb['short_up_to_3_sessions']['count']} |",
            f"| ≤3 個持股交易日淨損益 | {money(ha['short_up_to_3_sessions']['net_profit'])} | {money(hb['short_up_to_3_sessions']['net_profit'])} |",
            f"| 距前次出場 ≤3 個交易日再進場筆數 | {ha['rapid_reentry']['count']} | {hb['rapid_reentry']['count']} |", '',
            '| 持有日曆天數 | A 筆數 | B 筆數 | A 淨損益 | B 淨損益 |','|---|---:|---:|---:|---:|']
        for left,right in zip(ha['calendar_distribution'],hb['calendar_distribution']):
            lines.append(f"| {left['bin']} | {left['count']} | {right['count']} | {money(left['net_profit'])} | {money(right['net_profit'])} |")
        lines += ['', '| 持股交易日數（不含出場棒） | A 筆數 | B 筆數 | A 淨損益 | B 淨損益 |','|---|---:|---:|---:|---:|']
        for left,right in zip(ha['session_distribution'],hb['session_distribution']):
            lines.append(f"| {left['bin']} | {left['count']} | {right['count']} | {money(left['net_profit'])} | {money(right['net_profit'])} |")
        lines += ['', '短期持有與快速再進場群組可重疊，不能相加。日曆天數與持股交易日數不是同一口徑。', '',
            'A 期末持倉：'+json.dumps(ha['open_position'],ensure_ascii=False)+'；B 期末持倉：'+json.dumps(hb['open_position'],ensure_ascii=False)+'。', '',
            '#### A 大獲利交易：延後與錯過', '',
            '下表顯示 A 淨利最大的五筆，另納入其餘所有被 B 錯過的獲利交易。這只是結果展示排序，未作為策略條件或參數挑選。'
            '完整 A 獲利交易均保存在 profitable_trade_outcomes CSV。', '',
            '| 原始 BUY 確認時間 | A 買進 | B 買進／取消 | A 淨利 | B 淨利 | A 報酬 | B 報酬 |',
            '|---|---|---|---:|---:|---:|---:|']
        winners=v['profitable_A_trade_outcomes']
        cases=winners[:5]+[r for r in winners[5:] if r['status']=='A_ONLY']
        for r in cases:
            a_entry=f"{r['a_entry_time'][:16]} @ {r['a_entry_price']:g}"
            b_entry=f"{r['b_entry_time'][:16]} @ {r['b_entry_price']:g}" if r['b_entry_time'] else r['b_candidate_status']
            br=f"{r['b_return_percent']:.4f}%" if r['b_return_percent'] is not None else '—'
            lines.append(f"| {r['signal_time'][:16]} | {a_entry} | {b_entry} | {money(r['a_net_profit'])} | {money(r['b_net_profit'])} | {r['a_return_percent']:.4f}% | {br} |")
        lines += ['', '配對交易的淨利差同時受進場價、股數與先前交易的資金累積影響，不能全歸因於多等一根 K。'
            'CSV 同列每股毛價差差額（不含費稅的價格診斷），用來區分延遲價格代價與部位大小。']
        for r in cases:
            if r['b_entry_time']:
                lines += ['', f"案例 {r['signal_time']}：B 買價相對 A 改變 {r['price_change_percent']:+.4f}%，"
                    f"股數 A {r['a_shares']:,}／B {r['b_shares']:,}；出場 A {r['a_exit_time']}／B {r['b_exit_time']}。"
                    f"固定一股、不含費稅的整筆毛價差差額 {money(r['equal_one_share_gross_price_difference'])} 元；"
                    f"實際淨利差 {money(r['actual_net_profit_difference'])} 元。"]
            else:
                later=r['b_other_entries_during_A_trade']
                lines += ['',f"錯過案例 {r['signal_time']}：{r['b_candidate_status']}。"
                    +('B 在 A 原持倉期間有其他新訊號進場：'+json.dumps(later,ensure_ascii=False) if later else 'B 在 A 原持倉期間沒有其他進場。')]
        lines += ['', '#### 逐筆差異與原始紀錄', '',
            f'[訊號／交易逐筆差異]({symbol}_signal_trade_differences.csv) · '
            f'[所有成交逐時點差異]({symbol}_execution_differences.csv) · '
            f'[B 全部候選及取消原因]({symbol}_B_candidates.csv) · '
            f'[A 所有獲利交易的 B 結果]({symbol}_A_profitable_trade_outcomes.csv)', '',
            f'[A 成交]({symbol}_A_20241030_20261029.fills.csv) · [A 來回交易]({symbol}_A_20241030_20261029.trades.csv) · '
            f'[B 成交]({symbol}_B_20241030_20261029.fills.csv) · [B 來回交易]({symbol}_B_20241030_20261029.trades.csv)', '',
            f'[A 時間核對]({symbol}_A_timing_audit.csv) · [B 時間核對]({symbol}_B_timing_audit.csv) · '
            f'[A 持倉]({symbol}_A_holding.csv) · [B 持倉]({symbol}_B_holding.csv)']
    lines += ['', '## 前視偏差與原始檔案驗證', '',
        '- 每筆 B BUY 均獨立確認：原上穿在 i 棒成立，i+1 棒已完成且 Close > 當根 MA60、日線 BULL；'
        '成交棒索引必須為 i+2，成交價只能是它的 Open。成交棒的 Close／High／Low 不參與該次買進。',
        '- 每筆 SELL／EXIT 均核對原始觸發原因與緊接的次棒 Open；EXIT 保持日線失效優先，沒有額外延遲。',
        '- 用另一個獨立狀態機，依已完成日 K 的時間二分查找、原交叉訊號及確認條件，重建 A/B 所有應成交時點與動作；'
        '不呼叫 B runner 或原決策函式，須與完整成交序列一致，避免只檢查已成交而漏查應成交事件。',
        '- 獨立使用 Decimal 核對整股數、手續費、交易稅、現金及逐筆淨損益；由實際成交重建每一筆 Open／Close 權益，'
        '再獨立重算回撤。兩組的原始交叉訊號、資金成本設定與買進持有基準完全相同。',
        '- B 真實截斷前綴重跑 2024-10-30、2025-10-29、2026-10-28；成交、完成交易、所有決策事件、原始訊號與權益曲線'
        '均須等於完整兩年結果在該日以前的部分。A 精確重現既有已通過前綴驗證的連續回測。',
        '- 實驗測試包含修改未來成交棒的 Close，買進 Open 與部位不得改變；也涵蓋收盤等於 MA60、跨休市日、'
        '日線取消、SELL／EXIT 優先及樣本末尾不得提前成交。', '',
        f"既有 {len(baseline)} 檔 SHA-256 全部一致，原策略、Provider、原回測、先前快照與原助手均未修改。", '',
        '| 測試組 | 結果 |','|---|---|']
    for r in tests:lines.append(f"| {r['name']} | {r['summary']} |")
    lines += ['', '[機器可讀驗證](validation.json) · [完整統計與差異](comparison.json) · '
        '[原測試紀錄](existing_tests.log) · [實驗測試紀錄](experiment_tests.log)', '',
        '## 如何解讀與後續驗證', '',
        '本次樣本中，B 改善台積電報酬，但降低南亞科報酬；南亞科最大回撤仍超過 53%。'
        '較少交易與較高勝率，沒有在兩檔都換成較高報酬，新增一根確認的成效未呈現一致方向。', '',
        'B 的目的，是觀察多一根確認能否降低 MA60 附近的短期反覆進出；代價是承擔較晚的價格，並可能取消後來轉為大行情的原始進場。'
        '本報告同時保留被過濾的虧損、被錯過的獲利、資金與費稅差異，未將過濾交易的事後損益當成可直接相加的因果改善。', '',
        '**本次僅兩檔股票、同一連續兩年期間，結果仍需使用其他股票及其他期間驗證。**'
        '這不是獨立樣本外證明，不能據此認定 B 普遍較優；未依本次結果再選參數或修改原策略，也未接入實盤。', '',
        '重現（在台股目錄）：`python -B -X utf8 -m 666_strategy.experiments.run_buy_confirmation_ab`。'
        '分階段可使用 `--stage run|audit|report`；測試命令為 '
        '`python -B -X utf8 -m unittest discover -s 666_strategy/tests -v` 與 '
        '`python -B -X utf8 -m unittest discover -s 666_strategy/experiments/tests -v`。', '']
    (ROOT/'AB_ANALYSIS_REPORT.md').write_text('\n'.join(lines),encoding='utf-8')
    print('A/B analysis report and validation saved; all protected files unchanged.',flush=True)
