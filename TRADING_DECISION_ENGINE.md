# Trading Decision Engine

## 支撐一致性與 Transition Triggers 修正

本節更新並取代下方第一版中「最多四個原因」的正常建議格式，以及以歷史支撐替換目前支撐的作法。

### 根因

1. 第一版 `decision_context` 以 `previous_zones.nearest_support` 覆蓋當前支撐，
   但主報告仍顯示當日區域。故前支撐有效失守會錯標成目前顯示支撐失守。
2. 歷史區域由 `stock.py` 重建前一／前兩根日線，並非 SQLite 保存了 support_status；
   SQLite 僅持有 Action State 與確認次數。防抖可能暫時保留風險狀態，但不能因此生成目前支撐失守的原因。
3. Bayesian presentation 原本會將相符的模型支撐與顯示區域取聯集，
   price_context 卻使用原始偵測邊界，造成如 469.01～470.99 與 469.05～470.95 的差異。

### 單一價格區參考

`decision_zones.displayed_zones()` 在不修改原始偵測資料的前提下選出實際顯示區域，
保留既有模型區域聯集合併規則。主報告、價格情境及 Decision Context 共用此選擇。
若目前測試區可與歷史支撐匹配，該測試區可作目前防守區；否則採報告的最近支撐。
不再用舊支撐直接取代目前支撐。Decision Context 的 current_price 與價格區使用同一日線價格基準。

新增 `active_support_zone`、`active_resistance_zone`、`previous_support_zone`、
`previous_resistance_zone`、`current_active_support_status`、`previous_support_status`、
`previous_resistance_status`、`previous_break_distance_atr`。
既有 support_status 對應目前支撐；previous 狀態另算，歷史失守使用 `PREVIOUS_SUPPORT_BREAK`，
不冒用 `SUPPORT_BREAK`。舊區域失守仍可構成市場風險，但操作文字明確稱為「前支撐」。

偵測器原先沒有穩定 ID；本次加入 symbol／role／精確邊界／methods 的確定性 zone_id，
相同區域快照重跑不变，並附 source、as_of。邊界改變會產生新 ID，
這是可稽核快照 ID，不宣稱已建立跨日漂移區域的永久生命週期 ID；跨快照仍以區域重疊比匹配。

### 狀態轉換與文字

TradingDecision 新增四組結構化條件：

- entry_upgrade_triggers
- entry_downgrade_triggers
- holder_improve_triggers
- holder_worsen_triggers

各條件包含 code、對應 zone（ID／邊界／時間／來源）及適用的 ATR、量比、
狀態、價格上下限或連續確認次數。涵蓋 SUPPORT_HOLD、RECLAIM_KEY_LEVEL、
BEARISH_MOMENTUM_WEAKENING、CONFIRMED_SUPPORT_BREAK、FAILED_RECLAIM、
MEDIUM_TERM_TREND_BREAK 等。防抖調整最終狀態後重新產生條件，避免狀態已降為 WATCH
但文字仍宣稱 ENTRY 成立。條件是升降級的必要證據，單一價位觸發不保證越過其他 Risk Gate。

正常輸出採「空手者／持有者｜中文 Action State」、一個主要態度／防守條件與一個轉換條件。
不再逐條複製法人、ATR、MACD、RSI、KD 等上游原因。
無價格區時降為結構條件，不虛構價位；Reason Codes、Score、Gate、完整 Triggers 與價格區參考均保留於 debug。
僅擴充決策結果與 formatter，未擴充資料庫歷史表，也未更改 Bayesian posterior 或技術指標計算。

### 本輪驗證

新增 `tests/test_decision_transitions.py`，共 10 項測試覆蓋使用者八項驗收要求及來源／ID 一致性。
473 元回歸案例：目前支撐 469.01～470.99 不判失守，前支撐 479～481 的有效跌破獨立記錄。
`python -B -m pytest tests -q -p no:cacheprovider --basetemp=.pytest_tmp/transition_full1`
結果 **415 passed**，13.60 秒；一則既有 Starlette/httpx 棄用提醒。

## 流程與責任

`stock.get_stock_analysis()` 完成原有指標、ATR、價格區、Bayesian、法人及短中期分析後，
由 `decision_context` 整理資料，再經 `DecisionEngine.evaluate()`、`stabilize()`、
`decision_formatter` 生成 `timeframe_analysis.operation_reference`。

`timeframe_summary.build_operation_reference()` 僅保留相容介面，委派引擎處理。
只有短中期結果、沒有完整價格與風險資訊的獨立呼叫，輸出資料不足／觀望。
短中期分數、趨勢、乖離警告、量價因素與綜合解釋保留；`price_context` 保留价格情境分析，
停止覆寫最終操作建議。指標與模型計算公式、Discord 傳送和排程時段未改。

## 輸出及除錯

- `decision_context`：全部決策輸入，可供序列化及離線回測。
- `trading_decision`：`entry_action`、`holder_action`、兩組 reasons、entry_score、risk_score、risk_gate、previous_action_state、confirmation_count。
- `operation_reference`：每位角色最多兩句、最多四個原因；只有狀態變化才增加 `state_change`。
- `format_timeframe_discord(timeframe_analysis, debug=True)` 顯示狀態、分數、Gate、完整原因與 Context。一般文字不含內部代碼。
- 所有輸出由固定規則產生，不依股票代號分支、不隨機換句。持有者狀態描述市場風險，不提供比例、個人停損或強制指令。

## Context 欄位及資料來源

| 分組 | 欄位 |
| --- | --- |
| 身分／時間 | symbol, observation_time, observation_complete, data_valid |
| 趨勢 | short_term_direction, short_term_score, medium_term_direction, medium_term_score |
| 支撐機率 | support_probability（原始五級評等）, base_support_probability, adjusted_support_probability, adjusted_support_level |
| 價格區 | support_strength, resistance_strength, distance_to_support, distance_to_resistance, support_status, resistance_status, break_distance_atr |
| 法人 | institutional_level, institutional_score, institutional_confidence, institutional_selling_weakened, institutional_as_of |
| 波動 | atr, atr_percent, volatility_level |
| 動能 | macd_state, macd_momentum, macd_histogram_change_atr, rsi_state, kd_state |
| 相對強弱／均線 | relative_market_strength, price_above_ma5, price_above_ma20, price_above_ma60, overextended |
| 量能 | volume_state, volume_ratio |

MACD 動能直接重用 `macd_analysis`；RSI/KD/均線從已有數值整理狀態，不重算指標。
量比以已載入日線最新 Volume 除以前 19 根有效 Volume 均值，與現有短期量能判斷窗口一致。
距離均為到區域邊界的非負 ATR 倍數；break_distance_atr 保留正負號。
判斷採日線 Close，盤中顯示價不混入日線突破確認；法人沿用既有可用時間與過期資料檢查。

## 法人不重複計分

計分選用 **原始支撐評等＋法人類別**。原始評等低／極低 -2、高／極高 +2。
法人 STRONG_PRESSURE -2、BEARISH -1、BULLISH／STRONG_SUPPORT +2；NEUTRAL／MIXED／UNKNOWN 不加分。
原始 institutional_score、confidence 與 adjusted probability 均保存，但不再次加入 entry_score。
法人賣壓減弱可以作為確認條件／解釋，不能另加分，因為既有法人評級已納入該特徵。
MACD 空方減弱則是獨立技術證據。

機率只接受 ready 模型對應的價格區，交集／聯集比須達設定值；不相符、warmup 或缺值標為 UNKNOWN。
原始機率與調整後機率均保留，即使不適用於選定區域，也不偷偷套用其評等。
五級評等是模型相對預測位置，並非已校準的絕對勝率。

## Action States

空手：AVOID、WAIT、WATCH_FOR_CONFIRMATION、ALLOW_PROBE_ENTRY、ENTRY_CONDITION_MET、DO_NOT_CHASE。
持有：HOLD、HOLD_WITH_CAUTION、TIGHTEN_RISK、REDUCE_EXPOSURE、EXIT_CONDITION_APPROACHING。

先處理有效跌破、低支撐加強法人壓力與短中期同步偏空，再處理正乖離追價風險、
壓力受阻、突破或支撐承接條件，最後套用 Gate 和確認次數。
進場成立需突破、量能、法人、短中期、MACD、MA5/20/60 及分數共同通過；試單也需要支撐承接／站回等硬條件。
持有者風險獨立計算。有效跌破加空方動能或法人偏空可達 REDUCE_EXPOSURE；
再有中期偏空與法人強壓才達 EXIT_CONDITION_APPROACHING。

## Risk Gate

以下禁止直接進入 ENTRY_CONDITION_MET 或 ALLOW_PROBE_ENTRY：

- 支撐評等極低。
- 法人 STRONG_PRESSURE。
- 極高波動。
- MACD 空方增強且柱狀體變化／ATR 達負向門檻（缺少標準化變化時保守沿用空方增強分類）。
- CONFIRMED_BREAK／FLIPPED_TO_RESISTANCE。
- 短中期同步偏空。
- 積極候選缺少有效 ATR／波動資訊；未收盤資料也不允許積極升級。

高波動使 ENTRY 降為 PROBE、PROBE 降為 WATCH；極高波動最多 WATCH。
低支撐＋法人強壓直接 AVOID；此風險組合即使中期偏多，持有者仍為 TIGHTEN_RISK。

## ATR 價格區判定

`break_distance_atr = (support_low - close) / ATR`。
跌破距離達 0.75 ATR，或距離達 0.35 ATR 且前一根仍在下方、量比至少 1.5，判為 CONFIRMED_BREAK。
其餘落在支撐下方的情況是 MINOR_BREAK；ATR 缺值／零值為 UNKNOWN，沒有固定 0.5% 後備判準。

七種支撐情境：APPROACHING、TESTING、HOLDING、MINOR_BREAK、CONFIRMED_BREAK、RECLAIMED、FLIPPED_TO_RESISTANCE。
跌破後日線站回是 RECLAIMED；前收在下方、當日高點回測邊界卻仍有效跌破，是 FLIPPED_TO_RESISTANCE。
壓力突破須至少 0.5 ATR 並有量比確認；另外區分 APPROACHING、TESTING、REJECTED、UNKNOWN。

利用同一份已下載資料額外偵測前一／前兩根日線價格區，不下載新行情、不更改偵測器。
前一根剛突破／跌破時保留前兩根的對應邊界，以辨識快速站回及第二次確認。
第一版不建立完整區域生命週期歷史：超過此窗口的舊區域可能不再被識別，後續可擴充最小區域追蹤資料。

## 防抖與資料庫

獨立 `decision_state` 表保存 symbol、entry_state、holder_state、candidate_entry_state、
candidate_holder_state、confirmation_count、holder_confirmation_count、last_observation_time。
額外持有者計數用來獨立確認風險改善；另保存下節說明的精簡轉換快照，不保存完整行情歷史。

監測讀取狀態、決策及更新在 SQLite 交易中完成。進場／試單連續兩次有效新日線確認後升級，
同一日線重跑不增加計數，計數飽和於確認門檻。更舊的資料不倒退保存狀態。
清楚風險惡化可即時降級；持有者風險改善須連續兩次新觀察。
API 查詢只產生未累積確認的結果，不改寫或推進監測資料庫。

目前資料源是日線，所以「兩次有效新觀察」指兩根已收盤日線，並非兩次排程呼叫。
若未來加入具有可靠時間戳的盤中分析資料，可另設觀察粒度；本版不以重複收盤資料模擬小時確認。
狀態變化隨原本輸出呈現，未新增 Discord 通知觸發条件，也未修改傳送重試或去重規則。

## 集中設定與校準

所有新增核心門檻在 `DecisionConfig`，目前為未回測校準的初始值：

| 設定 | 預設 |
| --- | --- |
| confirmation_required | 2 |
| confirmed_break_atr / persistent_break_atr | 0.75 / 0.35 |
| breakout_atr / near_zone_atr | 0.5 / 1.0 |
| volume_confirmation_ratio | 1.5 |
| overextended_ma5_pct / overextended_ma20_pct | 6 / 12 |
| entry_score_min / probe_score_min | 6 / 3 |
| min_support_strength | 4（亦用於有效壓力） |
| probability_zone_overlap | 0.7 |
| relative_strength_threshold | 2 個百分點 |
| tighten_risk_score | 4 |
| bearish_acceleration_atr | 0.02 |

分數是可解釋的規則積分，非機率。權重集中在 `DecisionEngine.evaluate()`，
短期分數 / 2 限幅 ±2，中期分數 / 3 限幅 ±2；MACD、波動、價格區及相對大盤另列原因。
均線、RSI/KD、量比主要作確認條件，避免把短中期已有因素再無限制累加。
上述門檻與權重、日線確認延遲都需要後續 walk-forward 回測校準。

## 回退與測試

回退接入時可還原本次 stock/timeframe_summary/price_context/scheduler/database 修改，
新狀態表可留存而不再使用；不需改動指標、模型或法人資料表。
測試使用離線假行情、隔離法人服務與暫存 SQLite，涵蓋七個驗收案例、資料區域匹配、
七種支撐狀態、防抖、重新啟動、debug、原指標及通知行為回歸。

本次完整驗證：`python -B -m pytest tests -q -p no:cacheprovider --basetemp=.pytest_tmp/decision_full2`
使用專案 `.venv`，結果 **405 passed**（13.31 秒）。有一則既有 Starlette/httpx 棄用提醒。
另檢查原指標、波動計算、短中期評分、價格區偵測、Bayesian、法人及 notifier 原始碼均無 diff。

## Action State 變化說明（2026-09-16）

`decision_transition_analyzer.py` 在 Engine 與 `stabilize()` 之後比較前後快照。
Analyzer 不呼叫 Engine、不重算分數／Gate，也不改寫最終 Action State。
既有 `decision_transitions.py` 仍描述未來升降級條件；本模組描述本次已發生的變化。

### Schema migration 與快照

`decision_state.create_table()` 使用 `PRAGMA table_info` 檢查後增量加入：

- `previous_context_summary TEXT`：JSON 格式的上一輪比較基準。
- `context_fingerprint TEXT`：精簡輸入內容的 SHA-256。

既有狀態、候選狀態及確認次數原值保留；重複執行 migration 安全。
監測更新改用 UPSERT，避免 REPLACE 清空新增欄位。
既有資料庫啟動或首次監測時自動 migration，不需刪表或重建資料庫。

快照結構：

```text
{
  version: 1,
  fingerprint: "...",
  context: { 短中期方向與分數、成功率、法人、波動、MACD、相對強弱、
             observation_time、current_price、均線與量能確認條件、
             active/previous support/resistance、各區狀態 ... },
  decision: { entry_state, holder_state, entry_score, risk_score, risk_gate },
  confirmation: { candidate_entry_state, candidate_holder_state,
                  confirmation_count, holder_confirmation_count },
  thresholds: { 現有 DecisionConfig 的值 }
}
```

Context 保留純量欄位；每個 zone 僅保留 stable ID／相容 zone ID、上下界、角色及狀態。
不保存 DataFrame、行情列、zone 原始偵測證據或每輪 Context 歷史。
資料庫內保存的本輪快照，於下一輪作為 previous 使用。

新增 `decision_transition_events` 表，欄位為：
`id, symbol, role, previous_state, current_state, data_timestamp,
observation_fingerprint, timestamp, event_json`。
`event_json` 保存 TransitionEvent，包括精簡前後快照、轉換類型、完整選取代碼、
`primary_transition_reason` 與 `supporting_transition_reasons`。
只有實際轉換才插入事件；未變狀態只更新最新快照。

### Delta 與主要／輔助依據

Delta 保存各欄位 previous/current、未變欄位、Gate added/removed。
Reason 從差異衍生，現況 Reason Codes 不直接複製為轉換原因。

- 同一 stable zone 才描述守穩／站回；原 active 移至 previous 時仍以同一 ID 追蹤失守。
- 替換成新支撐使用 reposition/new support，不宣稱原支撐風險解除。
- Gate 全部消失才稱全部解除；剩餘 Gate 存在則明確稱部分解除。
- 法人等級不變，只有新出現的明確賣壓減弱訊號才描述邊際改善。
- 確認達標須相同候選、該角色自己的計數跨過既有門檻。
- 分數門檻與相對強弱門檻直接使用既有 DecisionConfig；不新增決策門檻。

Entry／Holder 分別按改善、風險惡化、中性重分類選取相符方向的依據。
優先度通常為確認達標、支撐失守／站回／突破、Gate 變動、重要門檻、趨勢或動能變化。
Gate 屬於 Entry 限制，因此在 Holder 說明中降低優先度。
`DO_NOT_CHASE` 不以 enum 順序硬套成較好或較差。

最高優先依據為 primary，其餘相符差異為 supporting。這是證據排序，非反事實因果驗證。
一般模式每角色顯示一條「主要狀態變化依據」及最多兩條輔助依據；同一支撐跌破
與其唯一新增 Gate 不重複成兩句。完整代碼仍保留於事件與 debug。
若快照存在但無足夠可辨識依據，明確標記 `INSUFFICIENT_TRANSITION_EVIDENCE`。
狀態未變一般模式不顯示；debug 保留 Delta、全部候選、選取原因、忽略的未變條件。

### 去重、同日新資訊及重啟

唯一鍵：

```text
symbol + role + previous_state + current_state + data_timestamp + observation_fingerprint
```

Fingerprint 包含版本與精簡 Context 輸入，不含 Engine 輸出分數、確認次數或執行時間。
等值整數／浮點序列化使用同一 fingerprint，不因字典順序不同產生新身分。
純重跑保留原狀態／計數且不輸出事件；同日有不同有效 Context，可產生不同事件。
新 Context 不等於新日線確認：同日確認次數仍遵守原防抖規則，不增加確認次數。
相同轉換與 fingerprint 即使中間經過其他狀態，也由資料庫唯一鍵持續去重。

事件插入與狀態／快照更新在同一 transaction，只有本輪新插入的事件進入輸出。
這是事件生成去重，未新增 Discord 通知觸發或更改既有傳送重試規則。
重啟從原 state 與快照恢復；首次使用或舊 schema 無快照時只建立基準，
不生成 UNKNOWN → WAIT。若有已知前態但缺少快照，真實狀態改變可顯示，
並明確註記無法可靠歸因，不杜撰歷史事件。
舊日線不得覆蓋較新快照，即使較新日線尚未收盤。

### 測試

新增 `test_decision_transition_analyzer.py` 與 `test_decision_transition_storage.py`，
涵蓋指定八案例、主要／輔助排序、分角色確認、Gate 部分解除／替換、穩定 zone ID、
新舊價格區、同日更新／純重跑、migration、重啟、舊資料防倒退及交易回滾。
測試使用暫存 SQLite；不對正式資料庫執行 migration。

驗證命令：`.venv\Scripts\python.exe -B -m pytest tests -q -p no:cacheprovider --basetemp=.pytest_tmp/transition_final`。
結果：**454 passed**，其中本輪新增 **27** 項；一則既有 Starlette/httpx 棄用提醒。

## Current Action Evidence 與歷史證據失效

`decision_evidence.py` 與 Transition Analyzer 分開：前者解釋本輪風險狀態／保留依據，
後者解釋前後狀態差異。Engine 命中 Holder 分支時直接記錄規則及實際使用的條件，
不由 formatter 猜測原因。REDUCE 分支不列入未参与判斷的 ATR／RSI／KD；
TIGHTEN 的累積風險分支則保存實際風險分數貢獻。

### 資料結構與最低要求

`TradingDecision` 增加：`current_trigger_evidence`、`retained_state_evidence`、
`state_basis`、`consistency_warnings`。

Current evidence 包含 `version, holder_state, matched_rule_id, primary_trigger,
supporting_evidence, triggered_conditions, trigger_zone, current_defense_zone,
observation_time, confirmation_context, minimum_requirement_met, trade_thesis_assessment`，
並附本輪的 `worsen_conditions`、`improve_conditions`。
每個條件含 code、priority、established 及必要的價格區／分數證據。

最低要求集中在 `MINIMUM_TRIGGER_REQUIREMENTS`，對應既有 Holder 分支：

- TIGHTEN：確認支撐跌破，或低支撐成功率與強力法人壓力，或實際累積風險達門檻。
- REDUCE：確認支撐跌破，加上法人偏空／強力壓力或 MACD 空方加速。
- EXIT_APPROACHING：確認支撐跌破、中期方向偏空、MACD 空方加速及強力法人壓力。

不更改既有分數與 Gate，不將任何兩個一般風險重新組合成 REDUCE。
中期方向偏空不冒充已確認的結構破壞；沒有個人持有假設與最後反彈失敗確認時，
`trade_thesis_invalidated` 為 null，明確表示未知。未新增 Position Action／出清動作。

### state_basis 與文字

| 值 | 定義 |
| --- | --- |
| DIRECT_RULE_MATCH | 本輪直接命中規則；列一個主要觸發及最多兩個輔助條件 |
| RETAINED_PENDING_CONFIRMATION | Raw Holder 已改善，但有效歷史依據尚在且改善確認不足 |
| INSUFFICIENT_EVIDENCE | 證據不滿足最低要求；整合層回退為 HOLD_WITH_CAUTION 並記錄 warning |
| STALE_DATA | 舊資料不覆寫狀態，不宣稱旧資料為本輪觸發證據 |

防抖保留文字固定明示「原風險條件已部分緩解，但改善確認尚未完成」，
不把 retained evidence 寫成本輪已發生事件。一般模式接續一個惡化條件與一個改善條件。
Debug 輸出 `[CURRENT ACTION EVIDENCE]`，包含本輪／歷史證據、價格區、未來條件與 warnings。
未經驗證的舊 formatter payload 顯示「證據不足」，不顯示無依據的降低曝險標籤；
formatter 不自行決定或修改 Action State。

### 保存、失效與 supersede

在既有 `decision_state` 增量加入 `holder_evidence_summary TEXT`，只保存一份目前證據、
必要的 retained evidence 及 state_basis；與狀態／Transition Event 同一交易保存。
不新增歷史表。Future conditions 每輪重建，不寫入歷史證據；價格區只存 ID、邊界、角色及狀態。
原 trigger zone 與 current defense zone 分開：原支撐失守不會被套用到新支撐價位。

優先使用市場條件失效，其次才是時間上限：

1. 同一 stable ID 原區域確認 RECLAIMED／RECLAIMED_SUPPORT／RESISTANCE_TO_SUPPORT。
2. Lifecycle 同一區域重新確認 ACTIVE_SUPPORT、角色為 SUPPORT。
3. Raw Holder 連續改善達既有 `confirmation_required`（預設兩根有效新收盤日線）。
4. 新成立的反向確認：已收盤、壓力確認突破、放量、中期偏多及 MACD 多方增強；
   必須相對原證據新增確認，早已全部成立的訊號不能重複當作 supersede。
5. 最後才使用 `DecisionConfig.retained_evidence_max_days=10` 個日曆日作後備上限；
   以原觸發 observation_time 計算，不因保留或純重跑重新起算。此初始上限仍待回測校準。

Context 額外攜帶精簡 `zone_lifecycle_statuses`（stable ID → status/current_role），
讓原 trigger 不再是最近支撐時仍能確認修復；不修改 lifecycle 判定。
新支撐守穩不解除另一 ID 的舊風險；舊支撐轉為壓力也不代表風險已解除。
原始時間缺失或歷史證據缺失時不能無限保留，回到有本輪證據支持的 raw state。

一致性 warnings 包含 `<STATE>_WITHOUT_TRIGGER_EVIDENCE`、`RETAINED_EVIDENCE_UNAVAILABLE`。
失效／替代原因亦記錄於 debug：`TRIGGER_ZONE_RECLAIMED`、`LIFECYCLE_SUPPORT_RECONFIRMED`、
`IMPROVEMENT_CONFIRMATION_REACHED`、`SUPERSEDED_BY_CONFIRMED_BULLISH_STRUCTURE`、
`EVIDENCE_MAX_AGE_REACHED`、`EVIDENCE_TIME_UNAVAILABLE` 等，正常解除並不代表程式錯誤。

新增 `tests/test_decision_evidence.py`，涵蓋直接命中、缺證據回退、歷史／目前區域隔離、
防抖保留、重啟／重跑、各類失效、新舊相反證據、舊 schema migration、
目前原因與 Transition 獨立，以及 EXIT_APPROACHING 不誤稱已達出清條件。

完整驗證：`.venv\Scripts\python.exe -B -m pytest tests -q -p no:cacheprovider --basetemp=.pytest_tmp/evidence_verified`。
結果 **478 passed**，本輪新增 **24** 項；一則既有 Starlette/httpx 棄用提醒。
Migration 僅在隔離測試資料庫驗證，正式資料庫下次啟動／首次監測時自動增量更新。
