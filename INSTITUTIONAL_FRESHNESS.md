# 2026-09-21 法人資料更新修正

## 診斷與證據

原本只有 FinMind v4 `https://api.finmindtrade.com/api/v4/data`，使用
`TaiwanStockInstitutionalInvestorsBuySell` 與 `TaiwanStockPrice`。Yahoo resolver
只負責既有股票市場判斷；法人資料不是由 Yahoo／yfinance 取得。

原 service 明確把 end_date 設成昨天，normalize_records 將資料標成隔日可用，
storage.history 與 build_context 又以 `date < as_of.date()` 排除當日。
此外，第一次嘗試（包含失敗）會記錄整天已查詢，直到隔日才允許刷新。
features 原本只在超過 10 個曆日後排除，所以 9/18 的訊號仍被視為有效。

唯讀檢查本機 stocks.db：2408、2454、3443 最新資料均為 9/18，來源 FinMind；
更新時間依序為 9/21 19:20:31、19:20:34、19:20:39，舊 fetch state 均是 9/21。
這是程式日期與快取政策造成，不能據此宣稱 FinMind 當時沒有 9/21 資料。

## 現在如何取得

上市沿用 `.TW` → TWSE，上櫃 `.TWO` → TPEx，裸代號沿用原 resolver。

| 市場 | 法人報表 | 成交量 |
|---|---|---|
| TWSE | `/rwd/zh/fund/T86?date=YYYYMMDD&selectType=ALL&response=json` | `/rwd/zh/afterTrading/STOCK_DAY?date=YYYYMMDD&stockNo=代號&response=json` |
| TPEx | `/www/zh-tw/insti/dailyTrade?date=YYYY/MM/DD&type=Daily&response=json` | `/www/zh-tw/afterTrading/dailyQuotes?date=YYYY/MM/DD&response=json` |

網域分別為 `https://www.twse.com.tw` 與 `https://www.tpex.org.tw`。
解析回應自身的西元／民國日期，且法人及成交量必須是同一天。
不是以請求日期替不存在的回應補日期。必要數字缺漏、缺股票、總計不符或日期不符均拒收。
TPEx 24 欄報表使用外資合計、投信、自營商合計，避免子項與合計重複相加。

目標官方報表最多嘗試兩次；FinMind 補前 45 曆日歷史，官方資料優先於同日第三方資料。
官方目標失敗可用 FinMind；若亦無資料，再嘗試上一交易日官方報表／既有快取。
所有 fallback 保留 actual_data_date，並記錄來源與警告，不把缺資料補成零。

Windows 若 requests 的 certifi 無法驗證本機管理的信任根，改用標準函式庫讀取
Windows 信任根；仍要求有效憑證及主機名稱驗證，不使用 verify=False。
若使用者明確設定 REQUESTS_CA_BUNDLE／CURL_CA_BUNDLE，尊重該設定，不改用其他根。
未增加 dependency。

## Freshness、快取與決策

- 台北交易日 13:30 前：target 與 expected 都是上一交易日。
- 13:30 至 16:00：target 是今天，expected 仍是上一交易日，允許發布緩衝。
- 16:00 後：target 與 expected 都是今天。16:00 是可設定政策，不宣稱官方保證發布時間。
- 週末／設定休市日：使用最近交易日。預期日期與 actual_data_date 比較，產生 FRESH／STALE／UNAVAILABLE。
- 沒有完整交易日曆；預設週一至週五，可在 FlowConfig.closed_dates 設定已知休市日。
  未設定的國定假日、颱風休市可能保守判為 STALE；TPEx 缺完整日曆時連買也可能被保守中斷。
  不能由 API 失敗反推今天休市，否則會掩蓋本次同型問題。
- SQLite 原始列保留 symbol/date 與 market、版本時間；讀取按市場過濾。
  新刷新 key 是 `(symbol, market, target_date)`；已取得 target 才直接重用。
  失敗／只取得舊日，15 分鐘後可再嘗試（需下一次服務呼叫，不另外啟動排程）。
  舊整日 attempted marker 不再阻擋。無新增 JSON、LRU 或記憶體資料快取。
- 同日資料可在實際觀測後使用；strict replay 仍同時驗證 available_at 與 observed_at。
  明確 as_of／replay 呼叫唯讀，不下載、不假造過往已知資料。
- STALE 保留日期、來源與原始淨額，但 level／signal=UNKNOWN、score=None、confidence=0，features 清空。
  adjustment 不調整基礎機率；decision adapter 與 DecisionContext 再次清除法人證據。
  因而正負分、連買／賣壓轉弱、交易建議均不沿用舊訊號。
- FRESH 的 streak 以最新實際資料為終點重新計算；方向轉變重算，缺交易日中斷，不補零。

## 顯示與日誌

FRESH 顯示原法人等級與截至日期。盤中／發布緩衝期間使用前一交易日時，追加
「今日法人資料尚未公布，目前使用最近交易日資料」。STALE 顯示：

```text
法人籌碼：資料更新異常（最新僅至 2026-09-18）
・2026-09-21 法人資料尚未成功取得
・本次交易建議暫不採用法人籌碼訊號
```

UNAVAILABLE 顯示「暫無可用資料」。真正刷新時記錄股票、來源、target、actual 與 freshness，
失敗／STALE 用 warning；正常命中快取不重複打刷新日誌。

## 修改清單

- `app/institutional_flow/{provider,service,storage,features,config,presentation,adjustment}.py`
- 新增 `app/institutional_flow/freshness.py`
- `app/decision_context.py`、`app/decision_engine.py`（僅法人 freshness 輸入防護）
- `tests/test_institutional_flow.py`（測試改用交易日與固定時間）
- 新增 `tests/test_institutional_freshness.py`、`tests/fixtures/institutional/*.json`
- 新增 `runtime_verification/verify_institutional_freshness.py`、`runtime_verification/institutional_freshness/live_report.json`
- 更新 `INSTITUTIONAL_FLOW.md`，新增本文件。

未修改 RSI、MACD、支撐壓力偵測或其他策略條件；未修改現有 stocks.db，也未發送通知。

## 驗證

完整測試命令：

```powershell
.\.venv\Scripts\python.exe -m pytest tests -q -p no:cacheprovider --basetemp=.pytest_tmp/flow_freshness_full_1
```

結果：702 passed，1 個既有 Starlette/httpx 棄用提醒。涵蓋使用者六種情境、兩市場真實回應、
錯日期／缺數字／缺股票／不同日成交量、快取失敗重試與重啟、同日 point-in-time、
fresh 連買與缺日、STALE 正／負法人對交易建議及評分完全等同 UNKNOWN、API 全失敗仍完成分析。
後續追加 Windows TLS 信任根／明確 CA 設定測試，執行：

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_institutional_freshness.py -q -p no:cacheprovider --basetemp=.pytest_tmp/flow_freshness_tls
```

結果：33 passed（含上述回歸已有的 32 個 freshness 案例，不與 702 相加）。
官方 fixtures 由 9/21 真實 JSON 回應擷取 2408／3211 單列，保留欄位與回應日期；
TWSE 月成交量保留該月截至 9/21 的所有日期，以驗證實際資料日與前一交易日。

實際連線命令（隔離 SQLite，無通知）：

```powershell
.\.venv\Scripts\python.exe -X utf8 runtime_verification/verify_institutional_freshness.py
```

已成功經新 provider／service 取得：

| 股票 | 實際來源 | data_date | freshness |
|---|---|---|---|
| 2408.TW | TWSE | 2026-09-21 | FRESH |
| 3211.TWO | TPEx | 2026-09-21 | FRESH |

完整結果見 `runtime_verification/institutional_freshness/live_report.json`。
本次驗證有真實官方下載，並非僅 mock 測試。原本執行中的長駐程序仍需重新啟動以載入新程式。
