"""Offline evidence reconciliation and report, with no strategy backtest calls."""
import ast
from collections import Counter
from datetime import date
import html
import json
from pathlib import Path
import re
import zipfile

import pandas as pd

import run_data_validation as r

ROOT, PROJECT, f = r.ROOT, r.PROJECT, r.f


def table(lines, headers, rows):
    lines += ['', '| '+' | '.join(headers)+' |', '| '+' | '.join(['---']*len(headers))+' |']
    lines += ['| '+' | '.join(str(x) for x in row)+' |' for row in rows]
    lines.append('')


def calendar_audit():
    annual = []
    for year in (2024,2025,2026):
        raw = r.read(ROOT/f'public/calendar_year_{year}.json')['data']['html']
        assert f'{year-1911}年' in raw
        rows = [[re.sub(r'\s+',' ',html.unescape(re.sub('<[^>]+>','',c))).strip()
            for c in re.findall(r'<t[dh][^>]*>(.*?)</t[dh]>',tr,re.S|re.I)]
            for tr in re.findall(r'<tr[^>]*>(.*?)</tr>',raw,re.S|re.I)]
        closed = set()
        for row in rows:
            if len(row)<2:
                continue
            label = row[0]
            if '最後交易日' in label:
                # Stock last-trading dates remain open; following settlement-only days close.
                text = row[-1] if '股票交易系統' in label else ''
            elif '開始交易' in label:
                continue
            elif re.fullmatch(r'\d+月\d+日',label):
                text = label  # HTML rowspans omit the holiday name on following rows.
            else:
                text = row[1]
            closed.update(date(year,int(m),int(d)) for m,d in re.findall(r'(\d+)月(\d+)日',text))
        configured = r.read(PROJECT/f'666_strategy/calendars/{year}.json')
        expected = {date.fromisoformat(s) for s in configured['scheduled_closed_dates']}
        only_official = sorted(d for d in closed-expected if d.weekday()<5)
        only_bundled = sorted(d for d in expected-closed if d.weekday()<5)
        assert not only_official and not only_bundled,(year,only_official,only_bundled)
        annual.append(dict(year=year,passed=True,official_scheduled_weekday_closures=len([d for d in closed if d.weekday()<5]),
            official_rows=rows,only_official=[],only_bundled=[],bundled_sha256=r.sha(PROJECT/f'666_strategy/calendars/{year}.json'),
            evidence_sha256=r.sha(ROOT/f'public/calendar_year_{year}.json')))
    result = dict(status='SCHEDULED_CALENDARS_MATCH',annual=annual,
        emergency_closures='Existing exceptional closures retained; 2026-07-10 and 2026-10-23 remain user-confirmed, not newly independently certified TPEx emergency notices',
        calendar_files_modified=False)
    r.write(ROOT/'calendar_validation.json',result)
    return result


def halt_audit():
    results = []
    for year in (2024,2025,2026):
        doc = r.read(ROOT/f'public/official_halts_{year}.json')
        assert str(doc['date'])==str(year)
        records = [row for t in doc['tables'] for row in t.get('data',[])]
        selected = [row for row in records if str(row[2]) in r.SYMBOLS]
        assert not selected
        results.append(dict(year=year,official_records=len(records),selected_records=selected,
            returned_year_verified=True,evidence_sha256=r.sha(ROOT/f'public/official_halts_{year}.json')))
    result = dict(status='NO_SELECTED_SECURITY_HALT_RESUME_RECORDS_FOUND',coverage_start='2024-05-06',coverage_end=r.END,
        annual=results,per_symbol={s:[] for s in r.SYMBOLS},
        scope='Official published suspension/resumption records; not a complete intraday price-stability/disposition event tape',
        rejected_probes='Default OpenAPI and incorrect year query returned 2026; excluded from historical completeness conclusion')
    r.write(ROOT/'halt_validation.json',result)
    return result


def legacy_audit():
    reports = []
    for relative in ['666_strategy/verification/fugle_history_20251030_20261029.snapshot.json',
        '666_strategy/verification/fugle_continuous_20241030_20261029/fugle_history_20241030_20261029.snapshot.json',
        '666_strategy/verification/fugle_ab_2454_20241030_20261029/2454_fugle_history.snapshot.json']:
        path = PROJECT/relative
        before = path.read_bytes()
        doc = r.read(path)
        assert doc['schema']==f.SCHEMA and doc['policy']==f.POLICY
        provider = f.FugleSnapshotProvider(path)
        rows = 0
        for series in doc['source_snapshots']:
            expected = f.native_frame(series['rows'],series['interval'])
            actual = provider.frames[series['symbol'],series['interval']]
            pd.testing.assert_frame_equal(actual,expected)
            rows += len(expected)
        assert path.read_bytes()==before
        reports.append(dict(path=relative,sha256=r.sha(path),series=len(doc['source_snapshots']),rows=rows,
            prices_units_timestamps_exact=True,legacy_policy_exact=True,file_unchanged=True,strategy_replay_executed=False))
    r.write(ROOT/'twse_compatibility.json',dict(status='PASSED',snapshots=reports,no_strategy_backtest=True))
    return reports


def tests():
    result = []
    for name,file,expected,command in [
        ('strategy_and_provider','existing_and_tpex_tests.log',263,'python -B -X utf8 -m unittest discover -s 666_strategy/tests -v'),
        ('AB_synthetic_regression','ab_synthetic_regression.log',12,'python -B -X utf8 -m unittest discover -s 666_strategy/experiments/tests -v')]:
        raw = (ROOT/file).read_bytes()
        text = raw.decode('utf-16') if raw.startswith((b'\xff\xfe',b'\xfe\xff')) else raw.decode('utf-8-sig')
        match = re.search(r'Ran (\d+) tests in ([\d.]+)s',text)
        assert match and int(match[1])==expected and text.rstrip().endswith('OK')
        result.append(dict(name=name,tests=expected,exit_code=0,command=command,elapsed_seconds=float(match[2]),
            log=file,sha256=r.sha(ROOT/file)))
    r.write(ROOT/'tests_result.json',result)
    return result


def render():
    study = r.read(ROOT/'data_validation.json')
    assert study['research_sample']==r.sample() and len(study['symbols'])==3
    deletion = r.read(ROOT/'deletion_manifest.json')
    assert deletion['deleted'] and len(deletion['items'])==59
    assert all(not Path(item['path']).exists() for item in deletion['items'])
    baseline = r.read(ROOT/'source_baseline.json')
    allowed = {'666_strategy/fugle_provider.py','666_strategy/fugle_client.py'}
    changed = [p for p,h in baseline.items() if not (PROJECT/p).exists() or r.sha(PROJECT/p)!=h]
    assert {Path(p).as_posix() for p in changed}==allowed,changed
    protected = [p for p in baseline if Path(p).as_posix() not in allowed]
    requests = r.read(ROOT/'request_manifest.json')
    for request in requests:
        assert r.sha(ROOT/'downloads'/request['file'])==request['sha256']
    calendar = calendar_audit()
    halts = halt_audit()
    legacy = legacy_audit()
    test_results = tests()
    total_clock = 0
    for s in study['symbols']:
        assert s['strict_error'] is None and s['minute_bars']==2480 and s['daily_bars']==604
        assert not any(s[k] for k in ('missing_minute','missing_daily','unexpected_minute','unexpected_daily'))
        assert s['clock']['completion_at_1330_verified'] and s['clock']['checked_native_bars']==2480
        total_clock += s['clock']['checked_native_bars']
        path = ROOT/s['snapshot']['path']
        assert r.sha(path)==s['snapshot']['sha256']
        document = r.read(path)
        assert document['schema']==f.SCHEMA_V2 and document['research_sample']==r.sample()
        before = path.read_bytes()
        f.create_fugle_snapshot(ROOT/(s['symbol'][:4]+'_source.json'),path)
        assert path.read_bytes()==before
        provider = f.FugleSnapshotProvider(path)
        for item in provider.snapshots:
            assert item['exchange']=='TPEx' and item['market']=='OTC'
            frame = provider.frames[item['symbol'],item['interval']]
            assert str(frame.index.tz)=='Asia/Taipei' and frame.index.is_unique
            assert all(row['source_exchange']=='TPEx' and row['source_market']=='OTC' for row in item['rows'])
            for row in item['rows']:
                unit = 1000 if item['interval']=='60m' else 1
                assert abs(row['Volume']-row['source_volume']*unit)<1e-6
            frame.to_csv(ROOT/(s['symbol'][:4]+'_'+item['interval']+'_validated.csv'),encoding='utf-8-sig')
    # Data-only execution audit: none of these study scripts calls strategy runners.
    forbidden = {'run_backtest','run_confirmed_backtest','evaluate_decision','evaluate_strategy','generate_signals'}
    for file in ('run_data_validation.py','render_validation_report.py'):
        calls = {getattr(n.func,'id',getattr(n.func,'attr','')) for n in ast.walk(ast.parse((ROOT/file).read_text(encoding='utf-8'))) if isinstance(n,ast.Call)}
        assert not forbidden.intersection(calls)
    removed_groups = Counter('專屬行情快照' if 'snapshot.json' in i['relative_path'] else
        '原候選分段下載／失敗回應' if i['relative_path'].startswith(('downloads/','captures/','failures/')) else
        '原候選停牌公告' if i['relative_path'].startswith('public/') else '舊報告／清單／彙總／封存包'
        for i in deletion['items'])
    lines = ['# TPEx 資料接入與完整性驗證：3105、6147、3211', '',
        '完成日期：2026-11-04（Asia/Taipei）。研究期間固定 **2024-10-30～2026-10-29**。'
        '本階段僅完成 Provider、原始行情、時間／單位、停牌與快照驗證；**沒有執行正式 A/B 回測或產生策略交易／績效**。', '',
        '**穩懋、頎邦、順達皆通過完整資料入口與離線快照重建。** '
        '原生 60 分 K／日 K 含暖身各 2,480／604 筆；官方逐年公布停復牌紀錄未查得三檔紀錄。'
        '一般交易時段之收盤撮合以完整同源 1 分 K 逐棒交叉核對，全部一致。', '',
        '## 使用者指定樣本', '',
        '研究樣本明確標示 `origin=user_designated`：3105 穩懋、6147 頎邦、3211 順達。'
        '本次名單直接來自使用者指示；未沿用舊候選的市值、波動或流動性篩選結論，未進行績效選股或參數最佳化。'
        '樣本身分與期間同時保存在 research_sample.json、來源報告及各 v2 快照。', '',
        '## 舊候選刪除與依賴檢查', '',
        f'已實際刪除 **59 個檔案，共 {sum(i["bytes"] for i in deletion["items"]):,} bytes**。'
        '刪除前逐一記錄路徑、SHA-256 與大小，確認每個絕對路徑都位於原候選研究目錄內；'
        '僅刪明確列出的檔案，未遞迴刪目錄或跨出研究範圍。']
    table(lines,['類別','檔案數'],sorted(removed_groups.items()))
    lines += ['包含 4105／3260／6188 的三份研究快照、全部專屬 60 分／日／1 分下載與失敗回應、'
        '3260 專屬停牌公告、舊選股規則／結果／候選摘要、完整性與停牌報告、Markdown 報告、舊證據 ZIP 及其雜湊清單。'
        '沒有透過備份或新封存包保留舊候選專屬資料副本。', '',
        '保留原研究工具 probe.py／render_plan.py 的可重用程式與歷史方法，停用其直接執行入口，以免重建已退役的選股結論。'
        '共用官方全市場資料、年度資料、其他股票 captures（含 6147 的既有回測前日資料）及其他功能依賴均保留；'
        '本次新快照仍由目前 Provider 重新取得所需完整區間，沒有把舊篩選資料當作新樣本的選取依據。', '',
        '[完整逐檔刪除清單](deletion_manifest.json) 記錄 59 個刪除項目與雜湊；本報告附錄亦逐檔列出。', '',
        '## Fugle Provider 的相容性擴充', '',
        '- 新增明確 `.TW → TWSE/TSE`、`.TWO → TPEx/OTC` 的市場解析。TPEx API 請求／回應雙方必須符合 exchange／market，'
        '拒絕跨市場錯配與興櫃 ESB；不根據數字代號猜測市場或自動切換來源。',
        '- TPEx 正規化列保留 source_exchange／source_market；分鐘量由張乘 1,000 轉股，日量保持股數，'
        '來源時間與原始量值一併保存。檢查無時區、重複時間戳、不合理 OHLCV、非原生格網及被重封雜湊的市場／單位錯誤。',
        '- 新增 fugle_history_snapshot_v2，逐序列保存市場資訊、研究樣本身分與收盤時間驗證。'
        '既有 TWSE v1 schema、政策常數、行情列格式及預設請求保留相容；裸代號介面仍按既有上市規則處理，上櫃須明確 `.TWO`。',
        '- 實際程式變更僅限資料接入檔 fugle_client.py、fugle_provider.py，另新增 12 項 TPEx 測試、資料驗證工具及文件。'
        'history／daily／calendar、原策略、決策、回測 runner、A/B 實作及原台股助手均未修改。', '',
        '同一份來源報告重建快照兩次完全相同；讀取快照後資料框與下載結果逐欄完全一致。'
        '既有資料入口 fetch_backtest_data 只用來檢查完整期間及暖身，不呼叫 run_backtest。', '',
        '## 完整期間、暖身及品質驗證']
    table(lines,['樣本','回測開市日','60 分 K（含暖身）','日 K（含暖身）','缺漏／非預期／重複','資料入口／快照'],[
        [s['symbol']+' '+s['name'],s['run_sessions'],s['minute_bars'],s['daily_bars'],'0／0／0','通過／精確重現']
        for s in study['symbols']])
    lines += ['60 分 K 暖身固定 **2024-10-14 09:00 起 60 根**，回測本體 484×5=2,420 根；'
        '日 K 暖身固定 **2024-05-06 起先前 120 個交易日**，加上本體 484 日。'
        '全部時間使用 Asia/Taipei；回測截止 2026-10-29 13:30。未補值、補造 OHLC、刪除缺日、縮短期間、'
        '重採樣價格或改用其他 Provider。來源實際 OHLCV 均經既有嚴格驗證器通過。', '',
        '## 收盤撮合時間與成交量單位', '',
        f'以同源完整 1 分 K 核對三檔自 2024-10-14 起的 **{total_clock:,} 根原生 60 分 K**，'
        '逐根比較 Open／High／Low／Close／Volume 全部一致，沒有修改原生價格。'
        '每檔 496 個交易日的 13:00 尾盤棒都納入此驗證，而非只抽查首尾兩日；'
        '來源沒有出現晚於 13:30 的最後分鐘、額外 13:30 小時棒或未能對帳的尾盤撮合。', '',
        'Fugle 的 [歷史行情文件](https://developer.fugle.tw/docs/data/http-api/historical/candles/) '
        '區分分鐘成交張數與日成交股數。新快照保留分鐘 source_volume_unit=lots、日線 shares，並逐列核對正規化股數。'
        '日線涵蓋零股、盤後及鉅額等範圍，未要求日量等於一般盤分鐘量；'
        '1 分 K 與 60 分 K 則皆屬同一盤中範圍，可用作原生棒核對。', '',
        '一般收盤時點核對是同一來源內部一致性驗證，不是另一家獨立逐筆成交時間認證。'
        'TPEx [交易制度](https://www.tpex.org.tw/zh-tw/mainboard/trading/rules/system.html) '
        '仍存在暫緩開收盤及盤中延緩撮合；本次三檔期間未發現來源晚收盤分鐘，不代表其他股票／其他日期都能直接套用 13:30。', '',
        '## 停復牌與日曆紀錄', '',
        '已查詢官方逐年公布停復牌資料，並核對回應年度，避免把伺服器預設的最新年度誤當歷史年度。'
        '有效舊介面的年度參數為 y=民國年；探索期間使用 d／date／year 等參數而回傳 2026 的資料未納入歷史查核結論。']
    table(lines,['官方年度','全市場停復牌列數','3105','6147','3211'],[
        [x['year'],x['official_records'],'未查得','未查得','未查得'] for x in halts['annual']])
    lines += ['逐年查詢涵蓋暖身開始 2024-05-06 至區間末 2026-10-29。'
        '官方公布停復牌資料中未查得三檔紀錄，且行情沒有整日或小時缺價，兩項證據相符。'
        '這不等於證明期間內從未有瞬間價格穩定、處置分盤、盤中短暫撮合延緩或流動性停滯。', '',
        '官方歷史網址：[2024](https://www.tpex.org.tw/web/stock/aftertrading/spendi/sprc_result.php?y=113&l=zh-tw&o=json)、'
        '[2025](https://www.tpex.org.tw/web/stock/aftertrading/spendi/sprc_result.php?y=114&l=zh-tw&o=json)、'
        '[2026](https://www.tpex.org.tw/web/stock/aftertrading/spendi/sprc_result.php?y=115&l=zh-tw&o=json)。', '',
        '另取得 TPEx 2024、2025、2026 年度股票開休市日期表，正確區分股票最後交易日與結算交割日、債券市場不同時程。'
        '三年排定的平日休市日與目前 bundled calendar 全數一致，未更改原日曆。'
        '2026-07-10、2026-10-23 的臨時休市仍沿用既有使用者確認記錄，未另外標示為本次獨立取得 TPEx 緊急公告。'
        '其他既有臨時休市來源保持原紀錄。詳見 calendar_validation.json。', '',
        '## TWSE 回歸與來源保護', '',
        f'策略／資料測試 **263 項**（包含新 TPEx 測試 12 項）、既有 A/B 合成資料回歸 **12 項**均通過。'
        f'比對 {len(protected)} 個既有 Python 程式檔雜湊，除明確允許的兩個 Fugle 接入檔外全部未變。'
        '既有 TWSE 實際快照另外只做資料重建，未重新執行真實股票策略回測：']
    table(lines,['舊 TWSE 快照','序列數','來源列數','驗證'],[
        [Path(x['path']).name,x['series'],x['rows'],'原價格／量值／時間／政策完全一致'] for x in legacy])
    lines += ['測試紀錄保存命令、項目數與 log 雜湊。TPEx 測試另涵蓋錯誤市場識別、ESB 拒絕、UTC 轉台北時區、'
        '分鐘／日線量單位、v1 相容、重封雜湊後的市場或量值竄改、缺棒仍應明確失敗，以及離線讀取不碰金鑰／網路。', '',
        '## 可重現交付物與操作', '',
        '- [3105 穩懋 v2 快照](3105_20241030_20261029.snapshot.json)、[6147 頎邦 v2 快照](6147_20241030_20261029.snapshot.json)、'
        '[3211 順達 v2 快照](3211_20241030_20261029.snapshot.json)。',
        '- 各檔 source.json、60m／1d_validated.csv、closing_time_validation.json、minute_to_native_comparisons.json.gz；'
        'downloads/ 保存帶來源時間、原始量值與市場識別的成功 API 回應，1 分 K 使用固定 gzip mtime 壓縮保存。',
        '- [使用者指定樣本](research_sample.json)、[資料完整性](data_validation.json)、[停復牌核對](halt_validation.json)、'
        '[日曆核對](calendar_validation.json)、[TWSE 相容性](twse_compatibility.json)、[測試結果](tests_result.json)、'
        '[最終驗證](validation.json)、[逐檔刪除清單](deletion_manifest.json)。', '',
        '在台股目錄重現，capture 會優先驗證並重用已保存資料；本階段沒有策略回測命令：', '',
        '```powershell',
        'python -B -X utf8 666_strategy/verification/tpex_integration_3105_6147_3211_20241030_20261029/run_data_validation.py public',
        'python -B -X utf8 666_strategy/verification/tpex_integration_3105_6147_3211_20241030_20261029/run_data_validation.py capture',
        'python -B -X utf8 666_strategy/verification/tpex_integration_3105_6147_3211_20241030_20261029/render_validation_report.py',
        '```', '',
        '## 剩餘問題與結論範圍', '',
        '1. 三檔行情完整、快照可重建、資料接入通過；本報告沒有 A/B 報酬、交易次數、買進持有或勝率，'
        '不能從資料接入成功推論策略有效。樣本為使用者指定，也不代表全體上櫃股票。',
        '2. 兩個 2026 年臨時休市保留既有使用者確認；若後續要求每個休市日均附獨立 TPEx 公告，仍需補齊公告證據。'
        '這是日曆證據來源的限制，本次沒有為了配合行情改寫日曆。',
        '3. 全期間分鐘／小時內部對帳不等於可在每根 Open 足額成交。零滑價、整數股以一般盤價格成交、'
        '買賣價差、漲跌停、處置、撮合延遲與交割限制仍是原模型的既有限制；本次沒有修改這些假設或加入新的策略規則。',
        '4. 未來若出現個股停牌或延後收盤，仍須保存真正缺價或完成時間，讓既有嚴格資料入口明確拒絕不符合條件的區間，'
        '不能刪日、補價或以新快照 schema 略過品質檢查。Fugle 歷史資料仍可能修訂，本次可重現性限定於已封存來源版本。', '',
        '## 附錄：逐檔刪除項目', '']
    table(lines,['相對舊研究目錄的檔案','Bytes'],[[x['relative_path'],x['bytes']] for x in deletion['items']])
    report = ROOT/'TPEx_DATA_VALIDATION_REPORT.md'
    report.write_text('\n'.join(lines)+'\n',encoding='utf-8')
    validation = dict(status='PASSED_DATA_INTEGRATION',period=[r.START,r.END],research_sample=r.sample(),no_strategy_backtest=True,
        no_parameter_optimization=True,no_fabrication=True,no_shortening=True,deleted_files=59,
        deletion_manifest_sha256=r.sha(ROOT/'deletion_manifest.json'),protected_source_files=len(protected),
        changed_allowed_sources={p:r.sha(PROJECT/p) for p in changed},unexpected_source_changes=[],
        source_snapshots={s['symbol']:s['snapshot'] for s in study['symbols']},tests=test_results,
        minute_to_native_bars_verified=total_clock,official_halt_years=[x['year'] for x in halts['annual']],
        scheduled_calendars_match=True,remaining_calendar_evidence='2026-07-10 and 2026-10-23 retain prior user-confirmed provenance',
        report_sha256=r.sha(report),data_validation_sha256=r.sha(ROOT/'data_validation.json'),
        source_files_sha256={str(p.relative_to(PROJECT)):r.sha(p) for p in [PROJECT/'666_strategy/fugle_provider.py',PROJECT/'666_strategy/fugle_client.py',
            PROJECT/'666_strategy/tests/test_fugle_tpex.py',ROOT/'run_data_validation.py',ROOT/'render_validation_report.py']})
    r.write(ROOT/'validation.json',validation)
    links = re.findall(r'\]\(([^)]+)\)',report.read_text(encoding='utf-8'))
    assert all(s.startswith('https://') or (ROOT/s).exists() for s in links)
    manifest = {str(p.relative_to(ROOT)):r.sha(p) for p in sorted(ROOT.rglob('*')) if p.is_file()
        and p.name not in ('evidence_sha256.json','TPEx_data_evidence.zip','bundle_sha256.txt') and '__pycache__' not in p.parts}
    r.write(ROOT/'evidence_sha256.json',manifest)
    bundle = ROOT/'TPEx_data_evidence.zip'
    with zipfile.ZipFile(bundle,'w',compression=zipfile.ZIP_DEFLATED) as archive:
        for name in list(manifest)+['evidence_sha256.json']:
            archive.write(ROOT/name,name)
        for path in [PROJECT/'666_strategy/fugle_provider.py',PROJECT/'666_strategy/fugle_client.py',PROJECT/'666_strategy/tests/test_fugle_tpex.py']:
            archive.write(path,'source/'+str(path.relative_to(PROJECT)))
    (ROOT/'bundle_sha256.txt').write_text(r.sha(bundle)+'  '+bundle.name+'\n',encoding='ascii')
    print('Data integration report, removal inventory, validation and evidence bundle saved; no AB backtest.')


if __name__=='__main__':render()
