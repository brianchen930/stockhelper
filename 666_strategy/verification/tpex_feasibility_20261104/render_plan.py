"""Offline planning report only; never executes strategy A/B runners."""
import csv
import json
from pathlib import Path
import re
import zipfile

import probe

ROOT, PROJECT = probe.ROOT, probe.PROJECT


def table(lines, headers, rows):
    lines += ['', '| '+' | '.join(headers)+' |', '| '+' | '.join(['---']*len(headers))+' |']
    lines += ['| '+' | '.join(str(x) for x in r)+' |' for r in rows]
    lines.append('')


def render():
    policy = probe.read(ROOT/'selection_policy.json')
    selected = probe.read(ROOT/'selection_result.json')
    coverage = probe.read(ROOT/'coverage_result.json')
    halt = probe.read(ROOT/'halt_corroboration.json')
    assert selected['policy_sha256']==probe.sha(ROOT/'selection_policy.json')
    assert coverage['selected_digest']==probe.sha(ROOT/'selection_result.json')
    assert [s['symbol'] for s in selected['selected']]==['4105','3260','6188']
    assert halt['official_notice_saved'] and all(r['status']=='EMPTY_DATA' for r in halt['exact_day_retries'])
    cap_doc = probe.read(ROOT/'public/cap_old_20240930.json')
    assert cap_doc['date']=='20240930'
    cap_rows = {r[1]:r for t in cap_doc['tables'] for r in t['data']}
    for row in selected['features']:
        official = float(cap_rows[row['symbol']][5].replace(',',''))*1e6
        assert abs(official-row['market_cap_TWD'])<1e6
    expected_halt_minute = [f'2025-10-01T{h:02d}:00:00+08:00' for h in (9,10,11,12,13)]
    unexplained = {}
    for s in coverage['symbols']:
        expected_m = expected_halt_minute if s['symbol']=='3260' else []
        expected_d = ['2025-10-01T00:00:00+08:00'] if s['symbol']=='3260' else []
        assert s['coverage']['60']['missing']==expected_m
        assert s['coverage']['D']['missing']==expected_d
        assert all(not x['unexpected'] and not x['duplicates'] for x in s['coverage'].values())
        assert all(a['passed'] for a in s['auction_samples']) and not s['close_comparison_mismatches']
        unexplained[s['symbol']] = 0
    baseline = probe.read(ROOT/'protected_baseline.json')
    changed = [p for p,h in baseline.items() if not (PROJECT/p).exists() or probe.sha(PROJECT/p)!=h]
    assert not changed
    lines = ['# 666_strategy 下一階段：上櫃中小型股 A/B 資料可行性與規劃', '',
        '研究日期：2026-11-04（Asia/Taipei）。本階段只做候選篩選、歷史資料探測與模型限制評估；'
        '**沒有執行 A/B 策略回測、計算策略績效或產生模擬成交**。', '',
        '**建議候選：4105 東洋、3260 威剛、6188 廣明。Fugle API 實際支援 TPEx，但目前 Provider／Client／快照管線不支援。'
        '東洋與廣明符合既有預期資料格網；威剛 2025-10-01 有官方確認停牌。'
        '因此尚不能直接沿用目前程式對三檔產生完整兩年 A/B 結果。**', '',
        '## 1. API 與現有 Provider 的支援範圍', '',
        'Fugle 官方 [Historical Candles](https://developer.fugle.tw/docs/data/http-api/historical/candles/) '
        '列有上市櫃日 K 與分 K，日 K 歷史自 2010 年、分 K 自 2023-05-23 起；單次區間須小於一年。'
        '本次以既有帳號直接探測三檔，分段均收到 HTTP 200、原始 `exchange=TPEx`、`market=OTC`；'
        '服務公開範圍與本帳號實測結果分開保存，不將文件支援直接當作個股完整性通過。', '',
        '本次獨立探測器以相同既有金鑰來源、TLS 驗證與認證遮蔽機制呼叫 API，保留實際 TPEx metadata。'
        '它只做白名單 OHLCV／turnover、時間、單位及格網驗證，沒有建立新 Provider 或修改原有驗證器。']
    table(lines,['層級','實查結果','正式回測前含義'],[
        ['Fugle 歷史 API','三檔 native 60／D 可讀，真正 TPEx／OTC metadata','資料服務可行，仍須逐檔核對停牌／缺漏'],
        ['fugle_provider.twse_symbol','只接受四位數 .TW；.TWO 回 FUGLE_UNSUPPORTED_SYMBOL','現有 Provider 無法直接讀上櫃'],
        ['fugle_client.normalize','exchange 被限定 TWSE；真實 TPEx payload 回 METADATA_MISMATCH','只放寬副檔名仍不足'],
        ['checked_rows／create_fugle_snapshot','硬編 TWSE metadata 與 .TW，現有 schema 未保留真正 TPEx 市場識別','未來需獨立市場驗證與版本化快照，不能把 TPEx 冒充 TWSE'],
        ['既有完整資料格網','每個市場開市日要求五根有效 60 分 K、一筆有效日 K','個股停牌不能以市場休市或補價處理']])
    lines += ['支援結論：**API 可以；現有完整管線不可以**。只通過 API 下載，不代表可以直接執行原回測。'
        '原始程式位置：[Provider](../../fugle_provider.py)、[Client](../../fugle_client.py)、'
        '[完整性檢查](../../backtest/data.py)。', '',
        '## 2. 先固定選股條件，再讀完整期間行情', '',
        '在讀取候選的回測期間價格前，先寫入並封存 selection_policy.json；'
        '讀完回測前特徵後封存 selection_result.json，再開始完整期間探測。名單沒有因停牌或取得資料後的結果而替換。', '',
        '- 資訊截止 2024-10-29。市值取 2024-09-30 的櫃買官方當時收盤價 × 當時發行股數，'
        '另與官方同日市值排行（佰萬元）核對；沒有用 2026 年市值回推。',
        '- 量值與波動特徵視窗固定為 2024-08-01～2024-10-29 共 60 個交易日；另取 2024-07-31 Close，'
        '使 60 個日對數報酬均有前收。日波動為這 60 個 ln(Close[t]/Close[t−1]) 的樣本標準差，'
        '未用累積報酬、趨勢漲幅或任何策略輸贏排序。採未還原價，若視窗含公司行動，波動可能受價格跳動影響。',
        '- 本研究將「中小型」操作性定義為市值 **50～500 億元**；這不是交易所官方大小型分類。'
        '三檔最終落在約 183～291 億元，屬此範圍的中型部分，不宣稱涵蓋微型股或全市場市值尾端。',
        '- 共同流動性下限：60 日成交金額中位數 ≥1,000 萬元、成交股數中位數 ≥10 萬股（100 張），'
        '且 60 個特徵交易日資料完整。這只是來源篩選門檻，並非 100 萬元能零滑價成交的保證。',
        '- 預先固定七檔目的性候選池：生技組 1565／4105 選日波動最低者；電子流動性組 3260／6147 選成交金額中位數最高者；'
        '設備／周邊組 3552／6125／6188 選日波動最高者；同分選代號較小者。這些是組內選取，非全市場排序。', '',
        '市值來源：[櫃買 2024-09-30 收盤行情](https://www.tpex.org.tw/www/zh-tw/afterTrading/otc?date=2024%2F09%2F30&type=EW&response=json)'
        '、[同日歷史市值排行](https://www.tpex.org.tw/web/stock/aftertrading/daily_mktval/mkt_result.php?l=zh-tw&d=113%2F09%2F30&o=json)。'
        '公司身分、產業碼與掛牌日期另核對 [櫃買公司基本資料](https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_O)；'
        '此基本資料為目前版本，只用來核對身分，不作歷史股數或市值計算。']
    table(lines,['代號／名稱','產業','2024-09-30 市值（億元）','60 日成交金額中位數（萬元）','60 日成交量中位數（張）','日波動 σ','用途'],[
        [s['symbol']+' '+s['name'],s['industry'],f'{s["market_cap_TWD"]/1e8:.2f}',f'{s["median_turnover_TWD"]/1e4:,.2f}',
         f'{s["median_volume_shares"]/1000:,.2f}',f'{s["daily_log_return_sigma_percent"]:.4f}%',
         {'LOW_VOL':'低波動、較低流動性生技樣本','LIQUID':'電子組較高流動性、中波動樣本','HIGH_VOL':'高波動周邊設備樣本'}[s['group']]]
        for s in selected['selected']])
    lines += ['東洋提供低波動但較薄流動性的對照；威剛提供記憶體產業與較高成交量值；廣明提供較高波動、不同終端產業的對照。'
        '三檔在規模上的差異有限，主要區別為波動與流動性，不應宣稱已完整分離市值效果。'
        '廣明與同致的日波動為 3.3764%／3.3679%，差距很小，機械排序選中廣明，不代表統計上明顯更高風險。', '',
        '完整七檔篩選特徵（包括未入選者）如下；全部通過事前共同門檻。']
    table(lines,['代號／名稱','組別','市值（億元）','成交金額中位數（萬元）','日波動 σ','入選'],[
        [s['symbol']+' '+s['name'],s['group'],f'{s["market_cap_TWD"]/1e8:.2f}',f'{s["median_turnover_TWD"]/1e4:,.2f}',
         f'{s["daily_log_return_sigma_percent"]:.4f}%','是' if s['symbol'] in {'4105','3260','6188'} else '否']
        for s in selected['features']])
    lines += ['目的性候選池可能存在研究者選擇與存活者偏差；現在才對歷史期間規劃，也不是完全盲測。'
        '本次能確認的是沒有依策略績效挑股，不能據此宣稱消除了所有選股偏誤。', '',
        '## 3. 固定兩年與暖身資料的實際可取得性', '',
        '正式研究期間保持 **2024-10-30～2026-10-29**。依既有日曆，市場開市 484 日，回測本體應有 2,420 根 60 分 K；'
        '60 分 K 暖身從 2024-10-14 09:00 起共 60 根，合計 2,480 根；'
        '日 K 暖身從 2024-05-06 起共先前 120 個交易日，合計 604 筆。'
        '每次 API 請求不超過 180 個日曆日，跨段檢查重複、缺漏、時間、正數有限價格與合理 OHLCV。']
    table(lines,['候選','實得／預期 60 分 K','實得／預期日 K','未對帳缺漏','目前格網結果'],[
        [s['symbol']+' '+s['name'],f'{s["coverage"]["60"]["observed"]}／2480',f'{s["coverage"]["D"]["observed"]}／604',
         unexplained[s['symbol']],'通過既有格網' if s['api_full_grid_passed'] else '停牌 1 日，嚴格格網不通過']
        for s in coverage['symbols']])
    lines += ['三檔皆無重複、非預期時間戳或無效 OHLCV；每檔有盤中尾盤資料的 496／495 個交易日，'
        '其原生 13:00 棒 Close 與同日原生日 K Close 均一致。'
        '每檔另抽查 2024-10-30、2026-10-29 的 1 分 K，13 時後含 13:30 的加總 OHLCV 與原生 13:00 棒完全一致；'
        '只用來核對，沒有以 1 分 K 拼造新的 60 分 K。', '',
        '**威剛停牌已由獨立官方資訊對帳。** [櫃買 2025-09-30 公告](https://www.tpex.org.tw/storage/eb_data/11409/11402023641.html) '
        '明訂 3260 自 2025-10-01 暫停交易；[官方當日行情](https://www.tpex.org.tw/www/zh-tw/afterTrading/otc?date=2025%2F10%2F01&type=EW&response=json) '
        '該股 OHLC 為「----」、成交股數／金額為 0。Fugle 分段少的正是當日 09～13 時五根與一筆日 K；'
        '單日 60／D 重試實際均為 HTTP 200、空 data。本次帳號實測與文件可能描述的 404 不能混為一談，原始回應已保存。', '',
        '所以威剛是「可交易日行情已對帳、已知停牌日沒有價格」，不是完整的無缺價密集序列；'
        '不能為停牌日補造 OHLC、借用別家價格、刪掉整個市場交易日，或把停牌擅自當成資料錯誤修復。'
        '目前回測器要求所有市場開市日有效，因此即使加上 TPEx 下載功能，威剛仍無法通過原嚴格入口。', '',
        '本次格網以既有 2024～2026 日曆作比較，包含先前以使用者確認的 2026-07-10、2026-10-23 休市。'
        '尚未完成整段獨立 TPEx 年度與臨時休市公告逐日認證；原始來源與日曆檔案雜湊均保留。'
        '因此東洋／廣明的「完整」表示在該既有格網下完整，不等於已完成正式 TPEx 回測的所有准入驗證。', '',
        '## 4. 單位、時間與零滑價模型限制', '',
        'Fugle [官方欄位與單位說明](https://developer.fugle.tw/docs/data/http-api/historical/candles/) '
        '對上市櫃一般整股分 K 使用張、日 K 使用股；本次將分 K 乘 1,000 正規化為股，並保存原數值。'
        '日量包含零股、盤後定價、鉅額等，範圍較盤中分 K 大，不能要求日量等於盤中量加總。'
        '選股用的是實際 daily turnover，沒有以 Close×Volume 冒充成交金額；但此全日金額也可能高估一般盤內可成交容量。', '',
        '櫃買 [交易系統規則](https://www.tpex.org.tw/zh-tw/mainboard/trading/rules/system.html) '
        '列一般撮合 09:00～13:30、一般交易單位 1,000 股，並有暫緩開盤至 09:02、暫緩收盤至 13:33 及盤中延緩撮合。'
        '現有五根棒、13:30 完成的假設適用一般時段；完整格網並不能證明每一天實際最後成交都已在 13:30 完成。'
        '若來源 13:00 棒吸收了 13:33 成交，卻仍在 13:30 判斷訊號，會有使用尚未完成價格的風險；正式研究前需核對事件與完成時間。', '',
        '特別是流動性較薄股票，「時間戳標 10:00」只代表棒開始時間，不保證該棒 Open 就是在 10:00:00 發生成交。'
        '小時棒也不包含買賣價差、開端深度、成交先後及委託隊列；有一根完整棒不等於可在其 Open 足額成交。', '',
        '以下只是固定假設 100 萬元部位的容量診斷，沒有訊號、策略現金路徑或模擬下單。'
        '比率定義為 100 萬 ÷（該棒 Open × 該棒成交股數），分母只是整根棒成交值的近似值，並非開盤深度；'
        '使用完整兩年資料的這項後續診斷在候選封存後才計算，未回饋到選股。']
    table(lines,['候選','100萬／事前日成交值中位數','100萬／棒值近似：中位數','第95百分位','棒值占比>5%的棒占比'],[
        [s['symbol']+' '+s['name'],f'{1e6/s["median_turnover_TWD"]*100:.2f}%',
         f'{s["capacity_diagnostic"]["participation_percent_quantiles"]["0.5"]:.2f}%',
         f'{s["capacity_diagnostic"]["participation_percent_quantiles"]["0.95"]:.2f}%',
         f'{s["capacity_diagnostic"]["percent_bars_over_5pct"]:.2f}%'] for s in coverage['symbols']])
    lines += ['東洋即使符合事前日量門檻，100 萬部位相對不少盤中棒仍很大；廣明的盤中容量也顯示限制。'
        '威剛整體較流動，仍有極薄時段，且部分期間處置／暫停等制度狀態需另核對。'
        '這些數字既不是滑價估計，也不是可成交比例，更不表示可用整根小時量承接棒初委託。', '',
        '目前零滑價、次棒 Open 全額成交模型還有下列具體限制：', '',
        '- 買賣價差、整股與零股市場不同。原模型 share_unit=1，以一般盤 Open 為任意整數股數定價，'
        '不等於零股委託真的能以同價成交；不可因移到上櫃而悄悄改成整張，以免破壞 A/B 基線。',
        '- 漲跌停鎖單、處置分盤、暫停交易與瞬間價格穩定，可能造成沒有可用開價、延遲或部分成交；'
        '不能只靠 OHLCV 推定買賣方向均可成交。',
        '- 同一整點完成確認後立刻用次棒 Open 成交，忽略計算與傳輸延遲。'
        'B 延後一根可能跨夜，價差不是固定的一根滑價，三檔的缺量與隔夜跳動可能不同。',
        '- 仍應保留既有手續費 0.1425%、最低 20 元、賣出稅 0.3%、無股利、無滑價的模型條件作可比研究基線。'
        '若日後研究真實成交與成本敏感度，應另列實驗，不把改過的成本混進原 A/B。', '',
        '## 5. 建議的下一階段順序與准入條件', '',
        '1. **先完成資料接入研究。** 另立獨立 TPEx Provider／快照版本設計，保留 .TWO、exchange=TPEx、market=OTC、'
        '原始單位、取得時間與 digest。測試應證明上市與上櫃市場不會混淆，並在凍結快照下通過既有資料驗證。'
        '本階段沒有實作這些變更。',
        '2. **處理已知制度例外。** 先以官方 TPEx 日曆、停復牌、處置、延後開收盤資訊核對資料。'
        '威剛停牌日如何保留無價格網、MA 暖身失效、pending 指令與恢復後行為，必須在正式研究前形成明確、A/B 一致的設計。'
        '若不能在原規則與入口條件下表示，應明列該固定設計不可執行，不以改日曆或刪資料假裝成功。',
        '3. **候選保持封存。** 4105 與 6188 目前僅達「資料格網可用」；3260 為「有已確認停牌、原嚴格入口不可用」。'
        '不因後來看到報酬、取消率或資料處理較麻煩而更換候選。七檔中的其他股票只是本次選取比較池，並非可任意替補的名單。',
        '4. **接入與制度核對完成後，才另行啟動正式 A/B。** 沿用 2024-10-30～2026-10-29、每檔每組 100 萬元、'
        '連續不重設，以及原 A／既有一根確認 B、原費稅與評價口徑。預定比較報酬、回撤、交易、勝率、費稅、持倉、'
        '買進持有、原始訊號取消與大贏單配對；本報告沒有產生這些策略結果。',
        '5. **先限制結論，再擴展樣本。** 三檔只是不同交易特徵的目的性案例，不能代表所有上櫃股。'
        '零滑價結果將來只能回答固定理想化模型下的 A/B 差異；對東洋尤其不能直接推論 100 萬實盤收益。'
        '後續若要外推，另預登錄更多產業與市值層、獨立期間，並將真實成交可行性與策略規則比較分開研究。', '',
        '## 6. 保存證據與驗證範圍', '',
        f'已核對 {len(baseline)} 個既有 Python／日曆檔案 SHA-256，變更數 0。未修改原策略、A/B、Provider 或原台股助手；'
        '新增內容都在本研究目錄。未產生任何策略績效或交易輸出。', '',
        '- [事前選股規則](selection_policy.json)、[七檔特徵與封存名單](selection_result.json)、[候選摘要 CSV](candidates.csv)。',
        '- [完整資料探測結果](coverage_result.json)、[停牌對帳與單日重試](halt_corroboration.json)、[最終驗證](validation.json)。',
        '- [4105 研究快照](4105_feasibility.snapshot.json)、[3260 研究快照](3260_feasibility.snapshot.json)、'
        '[6188 研究快照](6188_feasibility.snapshot.json)。這是 feasibility_raw_v1，並非既有 Provider 可直接載入的回測快照。',
        '- captures/ 保存成功請求的白名單實際回應；failures/ 保存停牌日 HTTP 200 空資料，兩者不混用。'
        'public/ 保存官方歷史市值、股數、停牌公告等來源；含少數用來辨認介面的探索性報表，未拿目前市值或總市場月報作個股事前特徵。',
        '- [唯讀探測器](probe.py)、[離線報告產製器](render_plan.py)、protected_baseline.json 與 evidence_sha256.json。', '',
        '可在台股目錄執行以下命令重建；screen／coverage 的成功分段先驗證並重用，不更動既有市場 Provider：', '',
        '```powershell',
        'python -B -X utf8 666_strategy/verification/tpex_feasibility_20261104/probe.py screen',
        'python -B -X utf8 666_strategy/verification/tpex_feasibility_20261104/probe.py coverage',
        'python -B -X utf8 666_strategy/verification/tpex_feasibility_20261104/probe.py corroborate',
        'python -B -X utf8 666_strategy/verification/tpex_feasibility_20261104/render_plan.py',
        '```','']
    path = ROOT/'FEASIBILITY_PLAN.md'
    path.write_text('\n'.join(lines),encoding='utf-8')
    with (ROOT/'candidates.csv').open('w',encoding='utf-8-sig',newline='') as stream:
        fields = ['symbol','name','industry','group','market_cap_TWD','market_cap_date','median_turnover_TWD','median_volume_shares',
                  'daily_log_return_sigma_percent','preperiod_start','preperiod_end','api_full_grid_passed','existing_provider_status']
        writer = csv.DictWriter(stream,fieldnames=fields,extrasaction='ignore')
        writer.writeheader()
        writer.writerows(coverage['symbols'])
    write = probe.write
    write(ROOT/'validation.json',dict(status='COMPLETED_WITH_BLOCKERS',no_strategy_backtest=True,no_strategy_provider_app_changes=True,
        protected_existing_files=len(baseline),changed_existing_files=changed,selection_policy_sha256=probe.sha(ROOT/'selection_policy.json'),
        selection_result_sha256=probe.sha(ROOT/'selection_result.json'),coverage_result_sha256=probe.sha(ROOT/'coverage_result.json'),
        no_unexplained_missing_given_inherited_calendar=unexplained,strict_grid_passed={s['symbol']:s['api_full_grid_passed'] for s in coverage['symbols']},
        blockers=['Existing TWSE-only Provider/client/snapshot pipeline','3260 official 2025-10-01 halt fails unchanged dense grid',
            'Full independent TPEx calendar and exception-time certification pending','Zero-slippage model not execution validation'],
        all_six_closing_auction_samples_passed=True,all_observed_daily_intraday_closes_match=True,
        report_sha256=probe.sha(path),probe_code_sha256=probe.sha(ROOT/'probe.py'),render_code_sha256=probe.sha(__file__)))
    links = re.findall(r'\]\(([^)]+)\)',path.read_text(encoding='utf-8'))
    assert all(s.startswith('https://') or (ROOT/s).exists() for s in links)
    manifest = {p.relative_to(ROOT).as_posix():probe.sha(p) for p in sorted(ROOT.rglob('*')) if p.is_file()
                and p.name not in ('evidence_sha256.json','tpex_feasibility_evidence.zip','bundle_sha256.txt') and '__pycache__' not in p.parts}
    write(ROOT/'evidence_sha256.json',manifest)
    bundle = ROOT/'tpex_feasibility_evidence.zip'
    with zipfile.ZipFile(bundle,'w',compression=zipfile.ZIP_DEFLATED) as archive:
        for name in list(manifest)+['evidence_sha256.json']:
            archive.write(ROOT/name,name)
    (ROOT/'bundle_sha256.txt').write_text(probe.sha(bundle)+'  '+bundle.name+'\n',encoding='ascii')
    print('Planning report and evidence saved. No strategy backtests executed.')


if __name__=='__main__':
    raise SystemExit('Retired candidate report generator retained for reference. Original candidate data/reports were deleted at user request; use the current TPEx integration report.')
