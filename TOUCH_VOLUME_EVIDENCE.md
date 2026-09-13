# Touch 與成交量反應證據（Support Event schema v2）

本次擴充獨立研究／回測模組，沒有 Bayesian、買賣訊號、Resistance Backtest 或評分變更。
正式 stock analysis、RSI／MACD／KD、ATR、支撐偵測與 scheduler 程式均保持不變。

## 檔案

修改：

- `app/backtest/config.py`：加入 `TouchVolumeConfig`，由 `SupportEventConfig.evidence` 管理。
- `app/backtest/support_event.py`：對稱 touch buffer、共用 exit／zone matching helper、事件欄位與 predictor allowlist。
- `app/backtest/support_backtest.py`：一次預計算成交量、逐根追蹤 Touch、附加證據、沿用 CSV／報告與 debug。
- `app/backtest/support_stats.py`：Touch 次數、量級、量比分箱、二維交叉統計。
- `tests/test_support_backtest.py`：兩項舊斷言改為本次指定的新定義，其餘既有測試保留。
- `SUPPORT_EVENT_BACKTEST.md`：註明 v1／v2 差異。

新增：

- `app/backtest/touch_analysis.py`：TouchEpisode、TouchTracker、反應品質與衰減。
- `app/backtest/volume_analysis.py`：通用成交量比率、因果 rolling、後續量能與既有 profile 密度。
- `tests/test_touch_volume_evidence.py`：新證據的計算、時間邊界、洩漏、分組與 CSV 測試。
- `runtime_verification/verify_touch_volume_artifacts.py`：獨立核對實際 CSV 與原始行情。
- `runtime_verification/touch_volume_source_baseline.json`：修改前原始碼 SHA-256 清單。
- 本文件及新回測結果資料夾。舊事件 CSV／原行情快照沒有覆寫。

## Touch 定義與共用邏輯

`is_support_touch(low, high, support_low, support_high, atr, threshold)` 保持原呼叫介面，改成：

```
Low <= support_high + threshold * ATR
且 High >= support_low - threshold * ATR
```

預設 threshold=0.25；邊界相等亦算 touch。有效正 ATR 與價格為前提。
現在位於支撐下方、但仍觸及下側 buffer 的 K 棒也會符合。
與 v1 的上側 proximity 定義不同，因此新資料集可能產生更多事件；
Support Event success／failure／neutral 的既有公式、順序與 10 根窗口不變。

`advance_exit_count` 是 Episode 與 Event 的共用連續確認 helper，可指定 above／below／either，
可供未來壓力區等場景使用。本次沒有新增壓力回測。

| 狀態 | 離開定義 | 所需根數 |
| --- | --- | --- |
| Touch Episode | Close > 當次上緣 + 0.75 ATR，或 Close < 當次下緣 - 0.75 ATR | 連續 2 根 |
| 既有 Support Event 冷卻 | Close > 當次上緣 + 1 ATR | 連續 2 根 |

兩者都使用各自開始時固定的區間與 ATR，不跟著後續波動改門檻。
相等不算離開；缺值中斷連續根數；確認離開的第二根不會同時建立新 episode。
因此連續四根停在支撐附近只算一個 touch。較短的 Touch 冷卻可能在同一 Event 冷卻內產生新 episode，
它增加歷史證據，不會強制產生另一個 Support Event。

## 逐根歷史追蹤與計數

沿用 walk-forward：i 當根只取得 `data.iloc[:i]` 的支撐結果，支撐截至 i-1 已知。
`TouchTracker.observe(i, zones)` 每根處理一次，沒有每個 event 再從第一根掃描整份歷史。
區間第一次觀察時建立 track；以固定初始區間重疊／中心距離（既有 zone_match_atr=0.5）匹配，
不因每日來源名稱或小幅位移而清空歷史。持續追蹤已知區域，即使它暫時不在偵測輸出中。

**不回填發現前的 Touch**：例如第 60 根才首次看到一個區域，前 59 根的價格交會不會被偽裝成
當時已知支撐的測試。計數代表從 walk-forward 開始後、區域首次被觀察以來的紀錄，
不是股票上市以來的絕對 touch 次數。

- `historical_touch_count`：目前這次以外，事件日前已開始的歷史 episodes 數。
- `support_touch_count`：與 historical_touch_count 相同；不再使用原引擎反應計數。
- `detector_touch_count`：保留原引擎的 touch_count，方便追查語意差異。
- `current_touch_number` = historical_touch_count + 1。
- 正在持續的 episode 即使開始於前一日，仍屬於「這次」，不重算為歷史。
- `last_touch_date`、`bars_since_last_touch`：最後一個歷史 episode 的開始日及距今根數。
- `first_touch_date`：第一個歷史 episode；沒有歷史時留空。
- `support_first_seen_date`、`support_age_bars`：追蹤區域首次被觀察的日期／年齡，
  不是以第一個 touch 假裝區域形成日期。
- `current_touch_start_date`：目前 episode 的開始日，便於辨識跨日持續測試。

零個已觀察 touch 是有效的 0；缺少可計算反應時平均值等使用 None。
每次 episode 的 touch_close／touch_low／touch_high、區間、ATR、當時量能與已成熟反應
保存在 `historical_touch_episodes` JSON 欄位，不另製造重複事件 CSV。
事件頂層的 touch_close／touch_low／touch_high、touch_volume 等指事件當根，與 entry 欄位一致；
Episode 首根的數值則在上述 JSON 的各筆紀錄中保留。

## Touch 反應與成熟條件

`evaluate_touch_reaction` 使用 episode 首根 Close 作 reference，觀察之後 5 根（不含首根）：

- touch_max_rebound_atr = (最高 High - reference Close) / 首根 ATR。
- touch_max_breakdown_atr = (最低 Low - reference Close) / 首根 ATR。
- touch success：首次 High 達 +1 ATR 早於首次 Low 達 <=-0.5 ATR。
- touch failure：Low 先達 <=-0.5 ATR。
- 皆未達成為 neutral；同根同時達標沿用可調的 same_bar_policy，預設 failure。

Touch failure 使用 Low 相對 reference；Support Event failure 仍使用 Close 相對 support_low。
這兩個 label 不互相覆寫。所有距離保留正負號；若未來最低價仍高於 reference，breakdown 距離可為正。

**歷史 predictor 的整段 5 根反應窗口必須在 event_date 之前結束**。
即使提前第 1 根已反彈，只要 5 根未完整結束，就不拿其反應作 predictor。
窗口結束在 event_date 當天也排除；缺失 K 棒不能 dropna 後延長補足。

統計成熟反應的平均／中位／最大反彈、平均下探、成功／失敗次數。
`historical_touch_reaction_count` 明確標示有多少 episodes 具備可用反應；
它可能小於 historical_touch_count。沒有成熟反應時平均和成功／失敗次數為 None。

## 是否越測越弱

使用最近兩次歷史 episodes；兩者都需有完整且已成熟的反應，否則 insufficient_data。
不會跳過最新的未成熟 episode，拿更早兩次冒充「最近兩次」。

- recent < previous × 0.7：weakening。
- recent > previous / 0.7：strengthening。
- 其餘 stable。
- 前次反彈 <=0 無法合理計算比例，也回傳 insufficient_data。

另存 touch_rebound_weakening：weakening 時 True、其餘可判斷狀態 False、不足時 None。
這只描述觀察值，不改動任何訊號。

## 成交量基準與例外

`precompute_volume` 一次計算，採 **當根之前 20 根**，使用 trailing rolling 再 shift(1)，
沒有 centered=True，也沒有 backfill。需完整 20 根，因此第 21 根才有第一個 MA20。

- volume_ratio = current_volume / previous_20bar_mean。
- volume_zscore = (current_volume - previous_20bar_mean) / previous_20bar_std，ddof=0。
- touch_volume_ma20、touch_volume_ratio、touch_volume_zscore 為事件當根的上述值。
- touch_volume_level：<0.8 low；[0.8,1.2) normal；[1.2,1.8) elevated；>=1.8 spike。
- Volume=None／NaN／Infinity／負數視為缺失，不補 0。
- 真實 Volume=0 保留 0；若均量>0，ratio=0 是有效低量；均量=0 則 ratio=None。
- std=0 則 zscore=None；分組量級無法判斷時為 None／資料不足。

可透過 config 調整期間；為相容需求欄位仍叫 ma20／volume_ma_20，實際參數保存在報告中。

## 未來量能與跌破量

以下欄位屬於 **Outcome / Research，不能作事件當下 predictor**：

- future_3bar_avg_volume／post_touch_volume_avg：事件後第 1～3 根完整平均。
- future_volume_ratio：上項 / 事件前 20 根均量。
- pre_touch_volume_avg：事件前 5 根均量（這一項本身是 predictor）。
- post_pre_volume_ratio：事件後 3 根均量 / 事件前 5 根均量。
- rebound_volume_confirmation：未來 5 根最大反彈 >=1 ATR，且未來量比 >=1.2 為 strong；
  同樣有反彈但量比<1 為 weak；無明顯反彈或中間量比 [1,1.2) 為 none；需要的資料缺失則 None。
- breakdown_volume、breakdown_volume_ma20、breakdown_volume_ratio、breakdown_volume_level：
  僅最終 label=failure 時，取既有 failure_bar 對應那根的成交量及它之前 20 根基準。
  success 後即使較晚跌破，這些欄位仍留空。

後續平均要求完整根數，缺失時為 None，不擅自拿剩餘根數平均。
缺量不會刪掉原本價格有效的事件或改變 label。

## 支撐區成交量密度

重用 i-1 已知的 Volume Profile，不重算下載、不使用未來成交量：

- support_volume_share：依區間與 profile bins 重疊比例分配的成交量占比。
- support_volume_density_ratio：上述占比 / 覆蓋價格範圍占比，相對均勻分布的密度倍數。

僅為現有日 OHLCV profile 的近似證據；無有效 profile 或分母為零時留空，不評分。

## Schema 與禁止洩漏的介面

`support_event.py` 集中提供 PREDICTOR_COLUMNS、OUTCOME_COLUMNS、AUDIT_COLUMNS。
`select_predictor_features(events_df)` 只採 allowlist，排除所有未來結果與巢狀研究記錄。
report.json 同時保存 feature_groups 與 schema_version=2。

Predictor 包含歷史 Touch 次數／時間、已成熟反應統計、衰減、當下量比／量級／z-score、
事件前均量、支撐 profile 證據及原本當下區間／ATR 等欄位。
Outcome 包含原本 label／未来 extrema／達標時間，以及全部未來成交量、反彈確認與跌破量欄位。
AUDIT 的 historical_touch_episodes 只含截點前已知資料，但仍排除於預設純量 predictor allowlist 外。

## 統計與 CSV

沿用每次回測獨立資料夾及 UTF-8-SIG、拒絕覆寫的方式，新增欄位直接加入 support_events.csv。
新增四份小型統計表：

| 函式 | 分組 | 檔案 |
| --- | --- | --- |
| summarize_touch_statistics | #1、#2、#3、#4+ | support_touch_stats.csv |
| summarize_volume_statistics | low、normal、elevated、spike | support_volume_stats.csv |
| summarize_volume_ratio_bins | <0.8、[0.8,1)、[1,1.2)、[1.2,1.5)、[1.5,2)、>=2 | support_volume_ratio_bins.csv |
| summarize_touch_volume_cross | Touch Number × Volume Level | support_touch_volume_cross.csv |

各表含 event_count、三種 label count、三種 rate、resolved_success_rate 與 excluded_count。
比例為 0..1；resolved 分母為 success+failure，分母為零留空。未知分組獨立列「資料不足」，
無效 label 不算進分母；沒有先假設放量或多次 touch 的好壞。

## 使用

```python
from app.backtest.config import SupportEventConfig, TouchVolumeConfig
from app.backtest.support_backtest import backtest_support_events
from app.backtest.support_event import select_predictor_features

events = backtest_support_events(df, '2408', config=SupportEventConfig(
    evidence=TouchVolumeConfig(touch_exit_atr=0.75, volume_ma_period=20)), debug=False)
predictors = select_predictor_features(events)
```

沿用 CLI，不需要新排程：

```powershell
.\.venv\Scripts\python.exe -m app.backtest.support_backtest 2408 --input data/backtests/support_events_2408_20260913T082716Z_da6fd64c/history.csv
```

debug=True 顯示歷史次數、當次編號、過去反彈、趨勢、量比與量級；API 預設不印逐筆訊息。

## 回測框架調整與限制

1. v1 的 support_touch_count 是引擎已確認反應數；本次分離為 detector_touch_count 與真實觀察的 episode 計數。
2. 新 touch 使用雙側 buffer，事件樣本集合可能改變；原有標籤規則不變。
3. 支撐沒有原生持久 ID。匹配固定初始區間仍有合併／分裂歧義；相近區域可能共用 track。
4. Touch 的歷史始於實際被觀察，故 #1 是本研究首次觀察到的測試，不能宣稱股票史上第一次。
5. 同日不同支撐、跨事件的觀察窗口可重疊，統計樣本不是獨立試驗；沒有將此當作 Bayes 機率。
6. 20 根均量缺失與除權息／停牌的價格量能異常仍需原始資料品質判讀，本次不新增公司行動調整模型。
7. 後續量比用整個 3 根窗口，反彈判斷用 5 根，屬第一版研究定義，並非逐根「上漲日才計量」。

## 驗證

既有 233 項基線通過；新測試涵蓋指定 12 案例及 lower buffer、兩側退出、
reaction end==event_date 排除、前 20 根而非包含當根、Volume=0 與缺失區別、
predictor allowlist、完整資料前綴不變、改動未來價量 predictor 不變、JSON／CSV BOM、
統計分箱边界與交叉加總。完整 suite 亦包含 FastAPI 暫存資料庫啟動／scheduler 啟停，
及正式 stock／scheduler import 不載入 backtest 的測試。

每次報告額外記錄 evidence_seconds，量測新證據部分實際耗時。
成交量 rolling 一次預計算；Touch 逐根更新，反應窗口到期只算一次，無逐事件全歷史重掃。
既有支撐引擎仍逐前綴執行，保留原本效能與因果行為。

## 2408 實測結果

使用上一版保存的真實 2408.TW 日線快照，期間 **2016-09-12～2026-09-11**、2432 根 K，
本次未重複下載。回測參數、欄位分組、時間與跳過原因均在 report.json。

輸出目錄：`data/backtests/support_events_2408_20260913T085146Z_c1304bd4/`。

- 352 筆完整事件：success 106、failure 189、neutral 57。
- success_rate 30.11%、failure_rate 53.69%、neutral_rate 16.19%。
- resolved_success_rate 35.93%。
- 尾端不足完整窗口跳過 2 筆。
- 新舊共有 297 筆相同日期與區間事件，label 全部相同。
  v1 獨有 50 筆、v2 獨有 55 筆：buffer 擴張可提早啟動事件與冷卻，因此不是簡單增加 5 筆。
  比較結果在 comparison_v1.json。

Touch 分組：

| Touch | 事件 | Success | Failure | Neutral | Resolved success rate |
| --- | ---: | ---: | ---: | ---: | ---: |
| #1 | 56 | 19 | 27 | 10 | 41.30% |
| #2 | 27 | 11 | 13 | 3 | 45.83% |
| #3 | 16 | 3 | 11 | 2 | 21.43% |
| #4+ | 253 | 73 | 138 | 42 | 34.60% |

成交量分組：

| 量級 | 事件 | Success rate | Failure rate | Resolved success rate |
| --- | ---: | ---: | ---: | ---: |
| low | 99 | 39.39% | 46.46% | 45.88% |
| normal | 117 | 28.21% | 52.99% | 34.74% |
| elevated | 80 | 27.50% | 57.50% | 32.35% |
| spike | 56 | 21.43% | 62.50% | 25.53% |

Touch × Volume 交叉表：每格為「resolved success rate（全部事件數）」；全部事件數包含 neutral，
比例分母只包含 success+failure，完整三類計數見 support_touch_volume_cross.csv。

| Touch | low | normal | elevated | spike |
| --- | --- | --- | --- | --- |
| #1 | 66.67%（16） | 20.00%（20） | 36.36%（13） | 40.00%（7） |
| #2 | 70.00%（11） | 20.00%（6） | 66.67%（4） | 16.67%（6） |
| #3 | 25.00%（4） | 20.00%（6） | 50.00%（2） | 0.00%（4） |
| #4+ | 37.50%（68） | 40.00%（85） | 28.85%（61） | 27.27%（39） |

這些交叉格有小樣本，數值只呈現資料，不能直接視為未來支撐可信度。

Volume Ratio 分箱的 resolved_success_rate：
<0.8：45.88%（99 筆）；[0.8,1)：36.36%（65 筆）；[1,1.2)：32.50%（52 筆）；
[1.2,1.5)：26.19%（52 筆）；[1.5,2)：39.53%（50 筆）；>=2：20.00%（34 筆）。

最近五筆事件（皆為 2026 年）：

| 日期 | 支撐區 | Touch # | 歷史次數 | 量比 | 量級 | Trend | Label |
| --- | --- | ---: | ---: | ---: | --- | --- | --- |
| 07-27 | 379.70～381.30 | 3 | 2 | 0.9820 | normal | strengthening | failure |
| 07-30 | 339.62～345.75 | 2 | 1 | 1.5413 | elevated | insufficient_data | success |
| 07-30 | 309.00～315.12 | 1 | 0 | 1.5413 | elevated | insufficient_data | success |
| 08-11 | 488.50～495.01 | 1 | 0 | 1.3041 | elevated | insufficient_data | neutral |
| 08-24 | 509.29～518.35 | 1 | 0 | 0.8215 | normal | insufficient_data | failure |

**273 項測試通過**，僅有既有 Starlette/httpx 棄用提醒。
獨立逐筆核對全部 352 筆：歷史反應截止、事件／跌破量比、前後量比、episode 次數、
標籤、達標位置、extrema、分組加總及 UTF-8-SIG 均一致。
核對結果分別在 touch_volume_verification.json 與 verification.json。
正式 app/backtest 以外既有應用程式 .py 的 SHA-256 全部未變。

本機全回測 162.804 秒，其中新證據程式區段合計 **3.573 秒，約 2.19%**。
这是單次 wall-clock 分段量測，不把不同次執行的時間差當作嚴格效能改善證據。

## CSV 新增欄位清單

共新增 39 欄；support_touch_count 為既有欄位語意調整，另保留 detector_touch_count。

- 次數與時間：detector_touch_count、historical_touch_count、current_touch_number、last_touch_date、
  bars_since_last_touch、first_touch_date、support_age_bars、support_first_seen_date、current_touch_start_date。
- 品質與稽核：historical_touch_reaction_count、historical_touch_avg_rebound_atr、
  historical_touch_median_rebound_atr、historical_touch_max_rebound_atr、historical_touch_avg_breakdown_atr、
  historical_touch_success_count、historical_touch_failure_count、touch_rebound_weakening、
  touch_rebound_trend、historical_touch_episodes。
- 當下價量：touch_close、touch_low、touch_high、touch_volume、touch_volume_ma20、touch_volume_ratio、
  touch_volume_level、touch_volume_zscore、pre_touch_volume_avg、support_volume_share、support_volume_density_ratio。
- Outcome：future_3bar_avg_volume、future_volume_ratio、post_touch_volume_avg、post_pre_volume_ratio、
  rebound_volume_confirmation、breakdown_volume、breakdown_volume_ma20、breakdown_volume_ratio、breakdown_volume_level。
