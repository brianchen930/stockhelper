# 支撐事件與歷史回測

目前已升級為 schema v2，新增 Touch／Volume 證據，詳見 [TOUCH_VOLUME_EVIDENCE.md](TOUCH_VOLUME_EVIDENCE.md)。
下方實測數據與 Touch／touch_count 定義保留為 v1 的歷史紀錄；v2 使用對稱 buffer，
support_touch_count 改為已觀察 Episode 次數，原引擎次數另存 detector_touch_count。

這是獨立呼叫的研究模組。既有 stock analysis、scheduler、API 摘要、指標、
支撐／壓力演算法與訊號評分完全未修改。沒有 Bayesian、ML 或交易決策。

## 檔案與 API

| 新增檔案 | 用途 |
| --- | --- |
| `app/backtest/__init__.py` | 獨立研究套件，不自動載入回測 |
| `app/backtest/config.py` | `SupportEventConfig` 與集中門檻、參數驗證 |
| `app/backtest/support_event.py` | `SupportEvent` 資料結構、touch、當下特徵與未來標籤 |
| `app/backtest/support_backtest.py` | walk-forward、冷卻、CSV／報告、獨立 CLI |
| `app/backtest/support_stats.py` | 整體、波動度、ATR 距離分組統計 |
| `app/backtest/benchmark.py` | 使用真實引擎及離線行情測量 500／1000／2000 根 K |
| `tests/test_support_backtest.py` | 標籤、因果性、去重、匯出與隔離測試 |
| `runtime_verification/verify_backtest_artifacts.py` | 不呼叫標籤函式，獨立核對實際 CSV 的所有結果 |
| 本文件 | 定義、操作方式、實測與限制 |

本次未修改任何既有應用程式 `.py`；以修改前 SHA-256 清單比對確認。
`runtime_verification/backtest_source_baseline.json` 保存比對基準。

```python
from app.backtest.config import SupportEventConfig
from app.backtest.support_backtest import backtest_support_events, export_support_events
from app.backtest.support_stats import (
    summarize_support_events, summarize_by_volatility, summarize_by_atr_distance,
)

events = backtest_support_events(df, '2408', config=SupportEventConfig(), debug=False)
summary = summarize_support_events(events)
by_volatility = summarize_by_volatility(events)
by_distance = summarize_by_atr_distance(events)
export_support_events(events, 'data/backtests/support_events_2408.csv')
```

`df` 必須是單層 OHLCV 欄位、時間遞增且唯一的 DatetimeIndex。
HLC 用於事件及結果；Open／Volume 缺少時仍可使用價格方法。
`prepare_history` 保留缺值 K 棒的位置，不刪除缺值後湊足 N 根；
重複／倒序日期、NaT 或重複欄位明確拋出 ValueError，避免錯誤對齊。
傳入 None／空表時回傳帶完整欄位的空事件表。

## 獨立執行

在 `台股` 目錄：

```powershell
.\.venv\Scripts\python.exe -m app.backtest.support_backtest 2408 --period 10y
.\.venv\Scripts\python.exe -m app.backtest.support_backtest 2408 --input data/backtests/<run>/history.csv
```

可指定 `--lookahead 10`、`--output data/backtests`、`--debug`。
更多定義由 Python API 的 `SupportEventConfig` 修改。
下載只在 CLI 的迴圈外進行，優先 TW、無資料時嘗試 TWO；也可直接指定 `2408.TW`。
保留 `auto_adjust=False`，與正式系統一致；下載時保守排除當日尚可能未完成的日 K。
離線輸入視為呼叫者提供的已完成日 K，不替呼叫者刪除最後日期。

本機 Python 信任庫曾無法驗證 Yahoo 憑證，實測使用 Windows 已信任憑證產生的
`runtime_verification/windows_trusted_ca.pem`，透過 `--ca-bundle` 傳給單次下載 session。
沒有關閉 TLS 驗證，也未更改正式程式或系統憑證設定；一般環境不需要此參數。

## 時間線與無未來資料洩漏

對 event_index=i：

1. `detector.detect(data.iloc[:i].copy())`：只提供到 i-1 收盤的資料。
2. 取得該時點的 `support_zones`，用 i 當根 High/Low 判斷 touch。
3. 以 i 的 Close 當 `entry_close`，固定此刻區間及 Wilder ATR14。
4. 僅將 `data.iloc[i+1:i+1+N]` 提供給獨立的 `evaluate_support_event`。

此選擇比「支撐可用到當根」更保守：支撐確實在測試前已知，
也避免目前引擎將當根已進入的區間改列 active，造成 touch 遺漏。
`support_as_of` 明確記錄 i-1；`event_date` 記錄 i。
首筆可能事件為第 61 根 K（預設先有 60 根歷史）。

ATR 使用既有 `calculate_atr` 一次計算完整因果序列，再取 i 的值。
該函式只使用當根及之前資訊，已以各歷史前綴的一致性測試驗證。
未來 ATR 不會改變本事件的 touch、exit、success、failure 或標準化結果。

回測不以未來 outcome 控制下一筆事件能否建立；即使事件的未來窗口無效，
touch 當下的冷卻仍生效。不同支撐可在同日各有事件，事件窗口也可能重疊。

## Touch 與去重

預設 `touch_atr_threshold=0.25`：

- K 棒實際與 `[support_low, support_high]` 相交，一定符合 touch。
- 或 Low 在支撐上緣之上，且 `Low <= support_high + 0.25 * entry_ATR`。
- 整根跳空到支撐下方（High < support_low）不算實際測試該區。

事件建立後，冷卻區間／ATR 固定；與冷卻區間重疊，或中心距離不超過
`zone_match_atr * frozen_ATR`（預設 0.5）的候選視為同次測試，不重複建立。
來源名稱、每日小幅位移或暫時不再被偵測，均不直接解除冷卻。
匹配以固定的原始區間為基準，不逐日累積位移。

連續兩根有效 K 的 `Close > support_high + 1 * frozen_ATR` 後解除，
下一根才允許再次 touch。相等、只有一天離開或無效行情均不算連續兩天。
跌到區間下方不解除；這避免在持續跌破期間反覆建立同一事件。
這裡「事件結束」指可重新進入；標籤觀察仍固定為 N 根，不隨離開而縮短。

## 標籤

事件當根不包含在未來窗口。只接受完整且每根 HLC 有效的 t+1..t+10：

| 標籤 | 定義 |
| --- | --- |
| success | High >= entry_close + 2 ATR，且早於首次有效收盤跌破 |
| failure | Close < support_low - 0.5 ATR，且早於首次反彈目標 |
| neutral | 全部 N 根均沒有達到任一條件 |

若兩者首次出现在同一根，預設 `same_bar_policy='failure'`，並保存
`same_bar_ambiguous=True`；可改成 success 做敏感度比較。
此為保守標籤約定，不宣稱由日 OHLC 能還原所有盤中先後與成交情況。

兩種首次達標時間都保存，即使最後 label 已由較早者決定：
`success_bar`／`failure_bar` 是輸入資料中零起算的絕對位置；
`bars_to_success`／`bars_to_failure` 是 1..N 的相對根數；未發生為 nullable Int64 的缺值。
第 N 根包含、第 N+1 根排除，且不足 N 根一律跳過，即使前三根已達標也不例外。

## 欄位單位與來源

完整 schema 由 `SupportEvent` 定義，含需求中的所有欄位，另含 support_as_of 與 same_bar_ambiguous。

- `atr`、`atr_percent`、`volatility_level`：entry 當根值，沿用既有 ATR 與分類門檻。
- `zone_width_atr` = (support_high - support_low) / entry_ATR。
- `distance_to_support_atr`：entry Close 到區間最近邊界的非負距離／ATR，Close 在區間內為 0。
  Touch 使用 Low，距離分組使用 Close，因此即使 touch 門檻只有 0.25，收盤距離仍可能 >1 ATR。
- `future_max_price`／`future_min_price`：完整未來 N 根的最高 High／最低 Low。
- `future_*_return_pct` = (future_price / entry_close - 1) × 100，**單位為百分點**；7.2 代表 7.2%。
- `max_rebound_atr` = (future_max_price - entry_close) / ATR。
- `max_breakdown_atr` = (future_min_price - support_low) / ATR，為**帶正負號的距離**；
  正值代表未來最低價仍高於支撐下緣，並非已發生跌破。Failure 仍只由 Close 判斷。
- 未來 extrema 保留全窗口，即使 failure 後大漲，也會有較大的 max_rebound_atr；不能拿它覆寫 label。
- `support_touch_count` 沿用 t-1 已知的 `touch_count`；它是原引擎「已確認反應的造訪次數」，
  不是新模組計算出的 touch 次數，亦不是包含 event_date 以後的結果。缺少時留空。
- `support_sources` 保留原始精確方法名稱（例如 swing_low、rolling_vwap、kmeans、volume_profile_poc）。
  `support_source_count` 是不同方法名称數；不是独立證據家族數，rolling／anchored VWAP 可能同属一族。
  CSV 使用 JSON array 保存來源，不丟失方法名稱或中文字。

## 異常處理與統計

ATR None／NaN／Infinity／0／負值或當根 HLC 異常時跳過，不生成 invalid label。
未來窗口任何 HLC 無效時整筆不列入資料集；不 dropna 後偷偷延長窗口。
正規化前保留輸入所有 K 棒，所以事件 index 與窗口不會因缺值而錯位。
引擎錯誤、落後截點或部分方法 unavailable 時跳過該快照，後續日期仍可繼續。
`events.attrs['skipped']` 與 report.json 保存原因計數，detector_warnings 保存引擎警告次數。

`summarize_support_events`：total_events、success/failure/neutral_count、三種 rate、
resolved_success_rate、excluded_count。所有 rate 為 0..1 的比例，CLI 才乘 100 顯示。
resolved_success_rate = success / (success + failure)；分母為 0 則 None，不偽裝成 0% 成功率。
外部輸入的 incomplete／invalid／未知 label 不列入分母。

`summarize_by_volatility` 使用 volatility_level 分組；
`summarize_by_atr_distance` 使用 [0,0.25]、(0.25,0.5]、(0.5,1]、>1 分组。
缺少／非法分組數值單獨列「資料不足」，有觀測的組別才輸出。

## 匯出與重現

`export_support_events` 建立父目錄，以 UTF-8-SIG、mode='x' 匯出；已有檔案會拒絕覆寫。
CLI 每次建立含 UTC 時間與隨機碼的新資料夾：

- support_events.csv：事件資料。
- by_volatility.csv、by_atr_distance.csv：分組統計。
- report.json：整體統計、參數、資料範圍、略過原因、定義版本與時間。
- history.csv：該次輸入行情快照，供離線重現。

`debug=False` 的 API 不輸出逐筆資訊；`debug=True` 顯示日期、支撐區間、Close、ATR、達標根數和 label。
CLI 即使未開 debug 也會在完成時列印整體摘要與最近五筆。

## 2408 實際驗證

自選股資料庫以唯讀方式確認包含 2408（南亞科），以 Yahoo Finance 2408.TW 下載一次歷史。
期間 **2016-09-12～2026-09-11**，**2432 根日 K**，完整預設設定。

資料夾：`data/backtests/support_events_2408_20260913T082716Z_da6fd64c/`。

| 結果 | 數量 | 比例 |
| --- | ---: | ---: |
| 全部完整事件 | 347 | 100% |
| success | 104 | 29.97% |
| failure | 185 | 53.31% |
| neutral | 58 | 16.71% |
| 排除 neutral 的成功率 | 104 / 289 | 35.99% |

另有 2 筆尾端窗口不足，已跳過；無引擎警告。完整回測計算耗時 169.271 秒，不含下載。

最近五筆（同日不同區間分開保存）：

| 日期 | 支撐區 | ATR | label | max_rebound_atr | max_breakdown_atr |
| --- | --- | ---: | --- | ---: | ---: |
| 2026-07-27 | 379.70～381.30 | 35.5709 | failure | +1.86 | -1.62 |
| 2026-07-30 | 339.62～345.75 | 37.5316 | success | +5.38 | +0.56 |
| 2026-07-30 | 309.00～315.12 | 37.5316 | success | +5.38 | +1.37 |
| 2026-08-11 | 488.50～495.01 | 35.1643 | neutral | +1.82 | -0.24 |
| 2026-08-24 | 509.29～518.35 | 35.2760 | failure | +1.93 | -1.11 |

波動度分組：中等波動 119 筆／成功率 41.18%；高波動 158 筆／21.52%；
極高波動 70 筆／30.00%；低波動沒有事件。
這些是本資料與定義下的樣本統計，不是貝氏估計、策略報酬率或未來可信度。

## 歷史研究的既有限制

1. 現有 swing 必須等右側 K 棒確認；回測只使用當時已確認的 pivot，不能把確認日回推為可交易日。
2. Volume Profile、VWAP、K-Means 與合併區間會隨截點更新；不能先跑完整歷史再回套。
3. 原引擎 touch_count 是今日區間在已知歷史中的確認反應統計，不是獨立事件紀錄。
4. 原引擎沒有持久 zone ID；研究端以固定區間重疊／中心距離匹配。
   區間大幅分裂或合併時仍有身份歧義，鄰近區間可能被合併冷卻；zone_match_atr 是可調的研究假設。
5. 只測當時引擎輸出的支撐（預設最多 3 區），不是所有可能候選；不同區間事件／重疊窗口也不互相獨立。
6. 日線 Volume Profile 是 OHLCV 近似；原 swing 使用 TR rolling mean，與新增事件的 Wilder ATR 定義不同，
   本次刻意保留既有演算法，沒有改其篩選門檻。
7. `auto_adjust=False` 未處理現金除息造成的機械跳空；供應商歷史可能被修訂，
   保存快照能重現本次結果，但不能證明供應商資料是當時發布版本。
8. 沒有交易執行、費用或滑價假設；entry 是事件日收盤，這是標籤資料集而非可成交績效。

## 測試與效能

基線 196 項測試通過；加入此模組後 233 項通過。
涵蓋需求十個案例、同根歧義、相等邊界、t+N off-by-one、凍結特徵、
真實引擎前綴一致性、區間漂移／消失／來源變更、非法索引、空分母、
CSV BOM／JSON 來源往返、禁止覆寫、離線 CLI、正式程式不載入 backtest。
既有 FastAPI lifespan 測試使用暫存資料庫完成實際啟動與排程器啟停。
僅有既有 Starlette/httpx 棄用提醒。

測試命令（basetemp 使用新的目錄）：

```powershell
.\.venv\Scripts\python.exe -m pytest tests -q -p no:cacheprovider --basetemp=.pytest_tmp/<new_run>
.\.venv\Scripts\python.exe -m app.backtest.benchmark data/backtests/<run>/history.csv --output data/backtests/<run>/benchmark.json
```

效能程式使用同一份已保存真實行情的最後 500／1000／2000 根，真實引擎、單次量測、不含下載。
既有引擎每個前綴重新正規化及計算 ATR，總計約 O(n²)，固定 lookback 的偵測成本另計；
沒有直接新增 O(n³) 流程。事件 ATR 已一次預先計算；未來可透過 detector 注入等價快取實作，
但本次不為效能改動原有支撐演算法。

本機實測（Python 3.13.14，Windows，單次 wall-clock，非隔離效能實驗）：

| 日 K 數 | 秒數 | 完整事件數 |
| ---: | ---: | ---: |
| 500 | 21.923 | 67 |
| 1000 | 56.231 | 148 |
| 2000 | 128.456 | 297 |

原始效能紀錄保存於同一資料夾的 `benchmark.json`。
另以獨立向量比較邏輯核對實際 347 筆 CSV 的標籤、達標位置、窗口及 extrema，全部一致；
結果保存於 `verification.json`。原始應用程式的 SHA-256 比對亦全部一致。
