# 666_strategy：60 分 K 訊號與個股日線多頭過濾

獨立資料、訊號與純持倉決策模組，不匯入 `app`，不啟動 API、排程、正式持倉服務或 Discord。
第二階段僅依 Close／MA60 穿越產生買賣訊號，不模擬成交、資金與績效。
沿用專案已安裝的 pandas、yfinance；沒有新增套件依賴或修改原有 requirements。

歷史資料來源預設 Yahoo；加上 `--source fugle` 可選用獨立 Fugle Provider，
使用環境變數優先、專案根目錄 `.env` 備援的 `FUGLE_API_KEY`。例如：

```powershell
python -B -X utf8 -m 666_strategy 2330.TW --source fugle --as-of 2026-10-29T13:30:00+08:00 --source-snapshot 666_strategy/verification/fugle_history_20251030_20261029.snapshot.json --signals
```

Fugle 支援四位數 TWSE 上市股票（明確使用 `.TW`）與 TPEx 上櫃股票（明確使用 `.TWO`），
例如 `3105.TWO`、`6147.TWO`、`3211.TWO`。既有裸代號介面仍按上市處理，不自動跨市場猜測或 fallback。
TPEx 請求驗證真正的 `exchange=TPEx`／`market=OTC`，未支援興櫃 ESB。
原生每天五根 60 分 K 直接保留；
13:00 棒含收盤撮合並於 13:30 完成，不修改資料配合 Yahoo。
完整一年回測、快照重建及逐筆輸出請見 [回測操作](BACKTEST.md) 與
[Fugle 整合驗證報告](verification/FUGLE_INTEGRATION_REPORT.md)。

TPEx 使用 `fugle_history_snapshot_v2`，保留逐序列及來源列的市場識別、使用者指定研究樣本資訊與收盤時間驗證；
既有 TWSE v1 快照及成本／策略規則維持相容。60 分 K 成交量由張換股、日 K 保持股數。
新三檔的完整資料、停牌、逐棒收盤撮合核對與舊候選刪除清單，見
[TPEx 資料接入驗證](verification/tpex_integration_3105_6147_3211_20241030_20261029/TPEx_DATA_VALIDATION_REPORT.md)。
此階段只做行情與快照驗證，沒有執行正式 A/B 回測。其他日期仍須核對延後開收盤與停牌等制度例外，
不能將一般 13:30 完成時間直接當作每個特殊交易日都已驗證。

第三階段已新增獨立歷史回測：輸入股票、開始日、結束日，以下一根 60 分 K Open 模擬成交，
含成本、持倉、逐筆交易與績效，操作見 [BACKTEST.md](BACKTEST.md)。
已補上 2025 修正版交易日曆及最大回撤、持倉天數、買進持有比較。
2025-10-30～2026-10-29 的真實資料查核發現 Yahoo 內部缺口，一年績效暫不產生，
缺口、重試證據與替代資料方案見 [一年回測資料報告](verification/ANNUAL_BACKTEST_REPORT.md)。

## 定義與品質保護

- 行情只來自 Yahoo Finance，透過 `Ticker.history(interval='60m')`，預設回看 60 個日曆日。
- 固定 `auto_adjust=False`、`back_adjust=False`、`prepost=False`、`repair=False`、`keepna=True`，不補值、不重採樣、不套用日線 MA。
- 全部時間轉為 `Asia/Taipei`；無時區來源直接拒絕，不推測其時區。
- 時間標籤是 **K 棒開始時間**。正常交易日的棒為 09:00、10:00、11:00、12:00、13:00。
- 前四根各 60 分鐘；13:00～13:30 是截短棒，依本次確認 **保留、計為一根、等權重**。輸出 `duration_minutes=30` 與 `is_short_session_bar=True`。
- 僅在 `bar_end <= as_of` 時納入。13:00 棒在 13:29:59 不可用，13:30 才視為時段收盤。
- `MA60 = 最近 60 根預期、有效、已收盤棒 Close 的簡單平均`。這是 60m 序列的 60 根，包含跨交易日及尾盤短棒，不是 60 個交易日，也不是固定 3,600 分鐘。
- 預期交易時段會先建立完整時間格。缺棒保留空列，OHLCV 缺值、無限值、價格非正數、負成交量、不合理 OHLC 均標記無效。
- **不會刪掉缺棒再向前湊滿 60 根。**任何含缺棒／無效棒的視窗都沒有有效 MA60；之後累積 60 根完整資料才可恢復。
- 成交量為零但 OHLC 完整合理時保留為來源觀測；不以零量自行推定停牌或休市。
- 重複時間戳、非整點的盤中標籤、未確認交易日上的有效行情會拒絕產生有效結果。
- `recent()` 只過濾顯示；MA60 已先在完整時間格計算，顯示過濾不影響視窗。

## 交易日

`calendars/2026.json` 是附來源的 TWSE 年度休市日曆及已確認臨時休市紀錄，目前含 2026/07/10、2026/10/23 颱風休市。
兩日均由使用者於 2026/10/29 明確確認；7/10 修正依據記錄為 `user_confirmed`，沒有虛構公告網址或由缺行情推論休市。
`calendars/2025.json` 另依交易所官方 2025 修正版日期表建立，並與所需區間的 361 個完成交易日大盤參考核對。
這是交易日中繼資料，不是另一個 OHLCV 行情來源。日期依據：

- [TWSE 年度市場開休市日期](https://www.twse.com.tw/holidaySchedule/holidaySchedule?queryYear=115&response=html)
- [臺銀證券 2026/10/23 現貨市場休市公告](https://www.twfhcsec.com.tw/News/NewsContent?ID=1502&List=%E6%9C%80%E6%96%B0%E6%B6%88%E6%81%AF)
- [期交所臨時休市公告](https://www.taifex.com.tw/cht/11/announcement)
- [TWSE 正常交易時段 09:00～13:30](https://www.twse.com.tw/zh/products/system/trading.html)

預設另下載同源 `^TWII` 日線核對已完成交易日：開市日缺少／無效的大盤 Close 會回報 `CALENDAR_ERROR`，不能把行情缺漏自動推論成休市。
只有日曆明確列為休市、且來源 OHLC 都空白及成交量零／空白的列，才當作休市 placeholder 排除。
股票日線及大盤日線參考對已確認休市日的**明確零成交量價格列**另作排除，保留異常紀錄；
有成交量、成交量缺失或無效的價格列仍回報衝突。此例外不適用於開市日，其他零量行情不會被刪除。
60 分 K 的原有行情驗證及 BUY／SELL 規則維持原樣。

日曆不是自動更新的緊急公告服務。未知年份不外推平日規則，回報錯誤；其他年度或更完整交易日可注入 `SessionCalendar`。
呼叫端提供的日曆代表其確認已涵蓋整個區間的正常及臨時休市資訊。第一階段不處理非一般盤的特殊交易時段。

## 獨立呼叫

在 `台股` 目錄執行。因名稱以數字開頭，Python 的一般 `import 666_strategy` 語法不可用，請使用 `importlib`：

```python
import importlib

strategy = importlib.import_module('666_strategy')
result = strategy.fetch_history('2330.TW')

if result.status == 'OK' and result.ma60 is not None:
    print(result.ma60)
else:
    print(result.status, result.issues)

print(result.recent(10)[['bar_end', 'duration_minutes', 'Close', 'MA60']])
```

兩檔手動驗證：

```powershell
python -B -X utf8 -m 666_strategy 2330.TW 2408.TW --output 666_strategy/verification/live.json
```

可選 `--as-of 2026-10-22T13:30:00+08:00`、`--lookback-days 60`、`--exclude-short-bar`。
CLI 回傳 0 表示所有指定股票最新資料為 OK 且數值核對通過；任何失敗回傳 1。
JSON 包含全部時間格、每根品質旗標、最近十根、來源原始回應及取得時間，便於回放與核對。

本機 HTTPS 憑證需要 Windows 信任憑證時，可傳入有效的 PEM：

```powershell
python -B -X utf8 -m 666_strategy 2330.TW 2408.TW --ca-bundle 666_strategy/.cache/windows_current_roots.pem --output 666_strategy/verification/live_20261026.json
```

本次 `.cache/windows_current_roots.pem` 由目前 Windows 信任存放區匯出，屬本機快取，不是可攜帶部署的依賴；使用者也可提供自己的有效 CA PEM。
相對 ASCII 路徑可避開部分 Windows libcurl 對含中文絕對 CA 路徑的限制。從未設定 `verify=False`。
程式庫可透過 `provider=YFinanceProvider(session=...)` 注入已設定信任憑證的 curl_cffi session。
只有 CLI 設定其自身程序的 Yahoo 快取位置；匯入模組及程式庫呼叫不修改既有 yfinance 全域快取設定。

## 回測／離線介面

```python
from datetime import date, datetime
from zoneinfo import ZoneInfo
import importlib

strategy = importlib.import_module('666_strategy')
tz = ZoneInfo('Asia/Taipei')

# verified_sessions 必須是呼叫端已核對的完整交易日序列。
calendar = strategy.SessionCalendar(
    sessions=tuple(verified_sessions),
    start_date=date(2026, 8, 27),
    through_date=date(2026, 10, 22),
    source='caller verified calendar',
)
result = strategy.analyze_history(
    raw_60m_dataframe,
    symbol='2330.TW', calendar=calendar,
    request_start=datetime(2026, 8, 27, tzinfo=tz),
    as_of=datetime(2026, 10, 22, 13, 30, tzinfo=tz),
)
```

`analyze_history()` 是無 IO 的純計算介面：不下載、不讀正式資料庫、不寫資料、不改輸入。
`fetch_history(..., provider=..., calendar=...)` 可注入測試來源及已驗證日曆；來源不會自動換成其他服務。
回測需逐一傳入當時的 `as_of`，避免使用未來資料；Yahoo 不保證能下載任意年代的分鐘資料。

## 結果契約

| status | 意義 |
|---|---|
| `OK` | 最新預期已收盤棒有完整 60 根有效視窗 |
| `INSUFFICIENT_DATA` | 尚無 60 根有效完整視窗 |
| `INCOMPLETE_DATA` | 最新視窗有缺棒或無效 OHLCV |
| `INVALID_DATA` | 時區、重複時間戳、時間對齊或交易日衝突等 |
| `CALENDAR_ERROR` | 日曆不足、未知年份或大盤交易日參考失敗 |
| `SOURCE_ERROR` | 股票 API 失敗或沒有分鐘資料 |

`result.ma60` 在任何非 OK 狀態均為 `None`。前期有效歷史 MA 仍可保留於 `bars`，但不能拿它冒充最新有效 MA。
較早缺口若已離開最近 60 根，可恢復 OK；缺口紀錄仍保留在 `issues`。

`bars` 包含：`Open/High/Low/Close/Volume`、`bar_end`、`duration_minutes`、
`is_short_session_bar`、`is_valid`、`MA60`、`ma60_valid`；索引是台灣時區 `bar_start`。

## 第二階段：基本進出場訊號

第一階段資料與 MA60 計算不變，新增純計算介面 `generate_signals(history_result)`。
它接收 `fetch_history()` 或 `analyze_history()` 的完整 `HistoryResult`，不重新下載、不重算指標、不改輸入。

```python
import importlib

strategy = importlib.import_module('666_strategy')
history = strategy.fetch_history('2330.TW')
result = strategy.generate_signals(history)
print(result.status, result.latest_signal, result.latest_signal_valid)
print(result.signals)  # 此取得範圍內全部歷史買賣訊號
print(result.recent(10)[['Close', 'MA60', 'signal', 'signal_valid']])
```

精確條件（每根 Close 都比較自己那一根的 MA60）：

```text
BUY  = Close[t-1] <= MA60[t-1] 且 Close[t] > MA60[t]
SELL = Close[t-1] >= MA60[t-1] 且 Close[t] < MA60[t]
```

- 只比較完整時間格中相鄰兩根有效、已收盤、MA60 有效的 K 棒，不先刪除缺棒或暖身列。
- 不使用四捨五入、容忍值、防抖、其他指標或持倉條件。持續在同側不重複觸發。
- 當根相等不觸發；前根相等、當根嚴格穿越則觸發。因此「上方→相等→上方」依規則可再次 BUY，不能用持倉狀態擅自去重。
- 第一個 MA60 點沒有前根有效 MA60，不能產生訊號；正常無缺漏時最早是第 61 根。
- 既有 13:00～13:30 短棒照常計一根，訊號到 13:30 才成立。跨夜／休市後相鄰的實際交易棒可以比較，不要求牆鐘時間相差一小時。
- 每筆事件記錄 `symbol`、`bar_start`、`bar_end`、`signal_time`、`close`、`ma60`、`signal_type`。
- `bar_start` 是 K 棒開始時間；`signal_time` 等於 `bar_end`，是資料收盤後可判斷的時間。
- `signal` 為 `BUY`、`SELL` 或 `None`。`signal_valid=True` 且 `signal=None` 表示資料足夠但沒有穿越；`signal_valid=False` 表示本根無法判斷，原因在 `signal_issue`。
- `latest_signal` 僅回傳最後一根自己的事件，不會把幾根以前的訊號當成最新訊號。
- 本輪最新資料不完整不會抹除之前有效的歷史訊號；它們不代表目前又觸發一次。
- 不配對買賣、不預設持倉、不計算成交或績效，歷史開頭也可能先遇到 SELL。

CLI 使用 `--signals` 啟用第二階段；不加此參數仍維持第一階段輸出：

```powershell
python -B -X utf8 -m 666_strategy 2330.TW 2408.TW --signals --ca-bundle 666_strategy/.cache/windows_current_roots.pem --output 666_strategy/verification/signals_live_20261026.json
```

終端會列出全部訊號及最近十根附訊號的 K 棒。JSON 保留第一階段欄位，
在 `bars`／`recent_10` 增加訊號與有效性，另以 `signal_analysis` 保存狀態、訊號清單與統計。
第二階段實測結果見 `verification/SIGNALS_REPORT.md`。

## 個股中長期多頭過濾與交易決策

日線與 60 分 K **各自下載、各自計算**。日線欄位為 `daily_ma60`、`daily_ma120`、
`daily_ma60_5_sessions_ago`；60 分 K 原有的 `MA60` 和 BUY／SELL 穿越邏輯完全保留。
多頭 `BULL` 必須同時滿足：

```text
日線 Close > 日線 MA60
日線 MA60 > 日線 MA120
日線 MA60 > 5 個交易日前的日線 MA60
```

數值足夠、任一條件不成立為 `NON_BULL`；暖身不足、缺值、日曆或來源衝突為 `UNKNOWN`。
所有必要數值都可計算後才判 BULL／NON_BULL；不把尚缺 MA120 的前期資料判為非多頭。
大盤方向與價格不參與這三項條件。第一階段既有 `^TWII` 日曆核對機制維持原樣。

`fetch_daily_trend()` 會以第一個決策日為基準，另取 **之前 120 個已確認交易日**，
再加上整段決策期間。這樣首日盤中使用的前一交易日日K也有完整 MA120 暖身。
不直接沿用 60 分 K 預設的 60 個日曆日。日線採 Yahoo 未調整 `Close`；不補值、不重採樣，
不先刪除缺漏日再湊滿 120 根。五日前使用完整交易日格的 `shift(5)`，休市日不計入。
日線只驗證實際用於均線的正數有限 Close；不借用盤中 OHLC。

`snapshot_at(at)` 只讀 **`daily_close_time <= at`** 的已完成日K。一般盤 13:30 才使用當日日K；
盤中、次日開盤使用之前最新的已完成日K。最新預期交易日缺資料時不回退到舊的有效多頭。
要求超出資料 `as_of` 或日曆覆蓋區間時回傳 UNKNOWN；較晚來源衝突不會污染較早時點的判斷。

| 情境 | 決策行為 |
|---|---|
| 空手、新的原始 BUY、日線 BULL | 產生待執行 BUY |
| 空手、新的原始 BUY、日線 NON_BULL／UNKNOWN | 保留原始 BUY，但不建立待買進決策，原因為 `BUY_BLOCKED_*` |
| 持倉、原始 SELL | 產生 SELL，不以多頭條件阻擋 |
| 持倉、完成日K首次觀測到 NON_BULL | 產生 EXIT，最早下一個已確認交易日 09:00 執行 |
| 持倉、UNKNOWN | 不因 UNKNOWN 直接建立 EXIT；已存在的 EXIT 繼續保留 |
| SELL 與 EXIT 同時成立 | 單一待出場決策，以 EXIT 優先；確認出場後清空持倉與待決策 |
| 多頭恢復、沒有新的 BUY | 保持空手；不重用之前被阻擋、取消或已消耗的 BUY |

`PositionState`、`TradeDecision`、`evaluate_decision()`、`confirm_execution()` 都在獨立的
`decisions.py`，沒有行情下載、資料庫、下單或績效計算。**產生 BUY 決策不等於已持倉**。
外部呼叫端負責保留每次回傳的 state（包含拒絕成交時的取消狀態），並依時間順序提供開盤、
日線收盤、60 分 K 收盤事件，即使沒有穿越訊號也必須檢查日線失效。
若中間略過事件，持倉層仍檢查 state 記錄之後每一個完成日K，保留最早失效的出場要求。
新注入的持倉沒有建立時間時，只依當下趨勢判斷，不臆測建倉前的失效。

買進確認前再次用 **實際確認時間** 的已完成日K檢查；待買進期間若曾有 NON_BULL／UNKNOWN，
取消該 BUY，即使延遲成交時已恢復 BULL，也必須等新的有效 BUY。
盤中收盤訊號可在同時刻下一棒開端確認，13:30 尾盤訊號最早下一交易日開盤確認；
這只是可執行時間約束，不假設能以訊號 Close 成交。
EXIT 一旦成立不因多頭恢復而撤銷。下一交易日尚未被日曆涵蓋時，保留
`execute_after=None` 的待出場決策；補齊日曆後才能確認，不外推平日當開市日。

```python
import importlib
import pandas as pd

strategy = importlib.import_module('666_strategy')
# verified_calendar 必須同時涵蓋日線暖身區間、決策區間與之後的出場交易日。
at = pd.Timestamp('2026-10-29T13:30:00+08:00')
history = strategy.fetch_history('2330.TW', as_of=at)
raw_signals = strategy.generate_signals(history)
daily = strategy.fetch_daily_trend('2330.TW',
    decision_start=history.metadata['request_start'], as_of=at,
    calendar=verified_calendar)
state = strategy.PositionState('2330.TW', holding=False)  # 或注入實際已持倉狀態
result = strategy.evaluate_strategy(raw_signals, daily, state, at=at, calendar=verified_calendar)
state = result.state
print(result.to_dict())

# 在實際可執行時間，以 as_of >= execution_time 的 daily 結果重新確認。
# transition = strategy.confirm_execution(state, decision_id=state.pending.decision_id,
#     at=execution_time, daily=updated_daily, calendar=verified_calendar)
# state = transition.state

# 呼叫端可自行持久化；本模組不讀寫正式持倉。
saved = state.to_dict()
restored = strategy.PositionState.from_dict(saved)
```

`evaluate_strategy()` 僅轉交與 `at` **相同時刻** 的最新原始事件，不拿昨天的
`latest_signal` 在今天開盤重新建立 BUY。開盤事件請帶之前保留的 state；
直接呼叫 `evaluate_decision(..., signal=None)` 也會處理待出場及趨勢狀態。
同一事件只消耗一次；對已更新 state 重複確認同一決策不會再次出場。
持久化與券商的併發原子性留給未來整合，純函式不提供跨程序的交易鎖。

CLI 使用 `--decisions` 自動包含原始訊號，另輸出 `daily_trend` 與 `decision_analysis`：

```powershell
python -B -X utf8 -m 666_strategy 2330.TW 2408.TW --decisions --as-of 2026-10-29T13:30:00+08:00 --ca-bundle 666_strategy/.cache/windows_current_roots.pem --output 666_strategy/verification/daily_live_20261029.json
```

CLI 預設以空手檢查當下；加 `--holding` 則把每檔視為外部已確認持倉。
CLI 不重播整段歷史持倉、不自動成交、不保存跨次執行持倉；持續狀態請使用上述程式庫介面。
UNKNOWN 時 CLI 回傳 1，原始行情與訊號、日線問題仍完整保存在 JSON。
2026/10/29 已依使用者確認修正 7/10 休市，並排除 10/23 零量異常日線；以同一份來源快照重算後，
2330.TW、2408.TW 各 158 個有效日K，最新均為 NON_BULL。
最近十個有效交易日的狀態、完整數值、原因及離線重現方式見
[日曆修正驗證報告](verification/CALENDAR_CORRECTION_REPORT.md)。
之前的 UNKNOWN 診斷保留於 [初次多頭過濾驗證報告](verification/DAILY_TREND_REPORT.md)，原始來源快照未覆寫。

## 驗證與限制

```powershell
python -B -X utf8 -m unittest discover -s 666_strategy/tests -v
```

實測結果見 `verification/REPORT.md` 與 `verification/live_20261026.json`。
CLI 對每個有效 MA60 另以 `math.fsum(60 根原始 Close) / 60` 核對，不是再次呼叫 rolling mean。

Yahoo 為非官方行情，可能延遲、修訂、限流或限制歷史分鐘區間；不保證即時性與永久可重現。
本模組預設只請求最近 60 個日曆日，並記錄實際回傳範圍，不宣稱可取得無限分鐘歷史。
來源沒有逐根最終結算旗標，因此「已收盤」按台北一般盤時段與 `as_of` 判定，不保證供應商之後不修訂。
未調整 OHLC 遇除權息／分割可能跳價，第一階段不自動修補。
原始訊號仍僅為上述基本穿越；日線多頭與持倉決策另行封裝，未接入原有台股助手。
第三階段的回測、成交價格／成本／績效另置於 `backtest/`，沒有修改原始策略或接入原台股助手。
尚未開發加減碼、券商下單、回測以外的排程或正式持倉儲存。

yfinance 行為參考：[官方 history 文件](https://ranaroussi.github.io/yfinance/reference/api/yfinance.Ticker.history.html)、
[官方原始碼](https://github.com/ranaroussi/yfinance/blob/main/yfinance/scrapers/history.py)。
