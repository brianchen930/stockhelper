# 三態訊號遲滯

## 現有判斷與接入位置

「偏多／觀望／偏空」原本由 `strategies.analyze_ma_strategy()` 的均線排列產生，
不是 DecisionEngine 的 entry_score、risk_score 或 trade_evidence.weighted_score：

- 偏多：Close > MA5 > MA20 > MA60。
- 偏空：Close < MA5 < MA20 < MA60。
- 其餘：觀望；資料不足仍為無法判斷。

原進入條件、各指標計算、進場／持有評分及既有確認機制均保留。
DecisionContext 傳入原始訊號與既有完整精度的 Close、MA5、MA20、MA60，
DecisionEngine.apply_signal_hysteresis() 只處理三態訊號的退出條件。
其輸出再交由 `apply_signal_persistence()` 連續確認；僅高信心重大風險可 bypass，沒有 cooldown。

## 退出門檻

相鄰差距依序為 `(Close - MA5) / MA5 * 100`、
`(MA5 - MA20) / MA20 * 100`、`(MA20 - MA60) / MA60 * 100`。
這是原均線條件的距離，不是新的加權評分。

| 上一輪 | 保持條件 | 退出條件 |
| --- | --- | --- |
| 偏多 | 三個差距都 >= -0.2% | 任一差距 < -0.2% |
| 偏空 | 三個差距都 <= +0.2% | 任一差距 > +0.2% |
| 觀望／沒有前次狀態 | 沿用原策略分類 | 沿用原策略分類 |

退出後以本輪原始狀態作為候選，通常為觀望；若已完全反向排列且超出退出門檻，
候選可為反向狀態。一般新候選須經 Persistence 確認後才正式切換；重大風險例外見下節。
等於退出門檻仍保留，浮點計算採近似相等比較。

設定集中於 `DecisionConfig.signal_bullish_exit_pct` 與
`DecisionConfig.signal_bearish_exit_pct`，單位為百分點（0.2 代表 0.2%），
須為有限正數。0.2% 是可調整的初始容忍幅度，尚未經績效回測校準。
建立 `DecisionEngine(config)` 或傳入 `update_monitor_decision(..., config=config)`
即可套用同一設定。

## 狀態、紀錄與監測

`TradingDecision` 記錄 `raw_action_state`、`previous_action_state['signal_state']`、
`final_action_state`、`hysteresis_held`。`signal_hysteresis` 另記錄有效性、
三個差距與實際門檻。只有原始狀態不同、且因遲滯保留前次狀態時，held 才為 true。

`decision_state.signal_state` 保存每檔股票的最終狀態，重啟後持續使用；
舊資料庫自動新增欄位，第一次可由 watchlist.last_signal 初始化。
後續監測以 DecisionEngine 的持久狀態為準。
舊日期資料沿用已保存狀態且不寫入新歷史，也不將此標示為 hysteresis 維持。
無效均線不執行遲滯、不覆寫已保存的有效訊號記憶。

監測結果的 `analysis.signal`、通知規則與 watchlist 保存均使用最終訊號；
`analysis.raw_signal` 留下本輪原始分類。原始 trend、均線理由與技術指標仍描述本輪資料。
已收盤的決策歷史包含完整遲滯紀錄，重複監測相同結果不新增歷史。
獨立唯讀分析讀取既有 `decision_state`，沿用已確認訊號，不累加確認次數或寫入監測記憶；
尚無已確認狀態的股票才以本輪分類作為初始分析。

## 最終輸出與通知閘門

原本監控雖會將遲滯與 Persistence 後的結果寫回 `analysis.signal`，但
`SignalChangeRule` 仍可因原始 `trend` 變化觸發通知，MACD／KD 等規則也能獨立通知。
通知簽章包含原始趨勢、分數與摘要，無法阻擋這些波動；唯讀分析則沒有載入已確認狀態。

現在資料流為：原始均線分類 → DecisionEngine → Hysteresis → Persistence（含既有重大風險例外）
→ `decision_state.signal_state`／`final_action_state` → `publish_decision()`
→ formatter／RuleEngine → scheduler 去重 → notifier。

- `publish_decision()` 同步一般分析與監控的 `analysis.signal`、`trading_decision`、
  `timeframe_analysis.trading_decision` 及操作參考；摘要與交易建議明列最終訊號。
- scheduler 明確傳入 `final_action_state`。此模式下三態訊號通知僅在初次建立或
  已確認狀態與上次通知基準不同時觸發；原始趨勢與指標仍作為觀測及解釋，不能獨立觸發。
- 訊號去重簽章只使用最終狀態，不使用原始趨勢、分數、理由或摘要。
  缺少有效 DecisionEngine 結果時，不退回以原始 action 發送訊號通知。
- 發送失敗不推進 watchlist 的通知基準，下一輪可重試；決策狀態仍保存在原 `decision_state`。
- 獨立的支撐／壓力事件通知仍沿用既有確認、防抖、pending 及發送回執機制；
  即使它們觸發發送，訊息中的三態最終訊號仍取自已確認狀態。
- 持倉操作建議（HOLD／REDUCE 等）的既有風險與收盤確認機制保留，未新增狀態表或確認計數器。

驗證：MA5=100、MA20=99、MA60=98，上次已確認偏多。本輪 Close=99.9 時，原始分類
雖為觀望，但 -0.1% 未超出 -0.2% 退出門檻，最終仍偏多；即使原始趨勢轉為均線糾結、
同時出現 MACD 交叉，也不發送新的三態通知。Close=99.7 連續兩輪才確認觀望並發送一次。
端到端測試見 `tests/test_final_signal_output.py`（使用隔離 SQLite 與模擬發送端，不發送真實 Discord）。

測試：`tests/test_signal_hysteresis.py`。

## Persistence／連續確認

流程：原始均線分類 → Hysteresis 候選 → Persistence → 最終正式狀態。
兩個階段都以已保存的正式狀態為基準，pending candidate 不會成為下一輪的遲滯基準。

`DecisionConfig.signal_confirmation_required` 預設為 2，接受正整數；設為 1 時
候選在第一輪即生效，可單獨驗證既有遲滯行為。此設定與既有進場路徑的
`confirmation_required` 分開，不改變其收盤確認機制。

| 本輪 Hysteresis 候選 | 處理方式 |
| --- | --- |
| 等於正式狀態 | 清空 pending candidate，confirmation count 歸零 |
| 不同於正式狀態，且等於前次 pending | 次數加 1；未達 N 時保持正式狀態 |
| 不同於正式狀態及前次 pending | 改存新候選，從 1 開始 |
| 次數達 N | 提交新正式狀態，清空 pending 與計數 |

第一次有效觀察若沒有任何既有正式狀態，沿用原有初始化行為，直接建立基準；
有 watchlist.last_signal 的舊安裝會先使用該正式狀態，後續轉換一律經確認。
無效資料不計數；過舊的日線或過舊監測輪次不推進確認。

### SQLite 保存與輪次識別

仍使用 `decision_state`，以可重複執行的 migration 增加專用欄位：

| 欄位 | 用途 |
| --- | --- |
| signal_state | 目前正式狀態（原有欄位） |
| signal_previous_action_state | 上次有效監測處理前的正式狀態 |
| signal_pending_candidate_state | 尚待確認的候選，無候選時為 NULL |
| signal_confirmation_count | 候選累計次數，無候選時為 0 |
| signal_last_observation_time | 最後處理的監測輪次 UTC 時間 |

既有 `confirmation_count` 與 `last_observation_time` 屬於進場路徑及日線確認，
繼續保留原用途。三態訊號的記憶與決策歷史在同一個 transaction 保存，重啟不歸零。

「連續 N 次」指不同監測輪次，並非 N 根日線。排程傳入該輪 `now.isoformat()`；
直接呼叫 `update_monitor_decision()` 則預設產生當下 UTC 時間。
相同行情、同一天或未收盤，只要是不同有效監測輪次都可計數。
重試同一輪時可傳入相同的 `signal_observation_time`，不會再加 1。
單次更新內部多次套用狀態處理，都使用同一份前次快照，不會把一輪算成兩次。
直接呼叫 `stabilize()` 時也可傳入此參數；未提供時使用 context.observation_time。

### 決策紀錄

`TradingDecision` 新增：

- `hysteresis_candidate_state`：Hysteresis 後、Persistence 前的候選。
- `pending_candidate_state`、`signal_confirmation_count`、`required_confirmations`。
- `persistence_held`：候選確認尚未完成而維持正式狀態。
- `signal_persistence`：上述資訊、前次／最終狀態、輪次時間與處理 status。

`final_action_state` 永遠為最終正式狀態；原 `signal_hysteresis.final_action_state`
則維持其原義，記錄遲滯階段的輸出。`hysteresis_held` 與 `persistence_held` 分開記錄。
完成確認時 pending／count 清空，但 `signal_persistence.confirmed_count` 保存達標次數。
決策歷史會區分 pending=1、pending=2 及最終提交；僅監測時間改變、其餘結果相同時不重複建檔。
通知與 watchlist 仍只使用最終正式狀態，不會提前使用候選。

測試：`tests/test_signal_persistence.py`；原遲滯測試以 N=1 隔離驗證退出門檻。

## 重大風險 bypass

規則集中於 `app/critical_events.py::evaluate_critical_event()`，由 DecisionEngine
在 Persistence 提交狀態前使用；formatter 只呈現已記錄的 bypass 結果。
原進場／持有操作狀態的惡化原本就立即處理，本次不改變那些規則或評分。

所有重大事件都要求有效、已收盤的觀察及有效 ATR，且不得是 STALE_DATA。
只有原三態訊號輸入有效、有效且非重播的監測輪次，才可更新正式三態狀態。

| 規則 | 沿用的證據與額外信心要求 |
| --- | --- |
| IMPORTANT_SUPPORT_CONFIRMED_BREAK | 已有 SUPPORT_BREAK／PREVIOUS_SUPPORT_BREAK gate，對應有效支撐區、現價仍在下緣之下，強度 >= 既有 min_support_strength |
| MEDIUM_BEARISH_WITH_MACD_ACCELERATION | 中期偏空 + 既有 BEARISH_MACD_ACCELERATION gate，且實際 histogram change / ATR <= -bearish_acceleration_atr；缺少幅度不能 bypass |
| HOLDER_REDUCE_EXPOSURE／HOLDER_EXIT_CONDITION_APPROACHING | 已持有、當前 holder rule 有效、確認失守區域仍在價格上方，並有法人偏空或可量測的 MACD 加速確認 |
| MULTIPLE_INDEPENDENT_HIGH_RISKS | 既有加權 market_action 已達 REDUCE／EXIT，且至少 3 個獨立強風險面向的既有淨分數 > 0 |

獨立強風險面向限定：重要支撐確認失守、中期偏空、可量測的 MACD 加速、
法人 STRONG_PRESSURE、達既有門檻的相對大盤弱勢。
`DecisionConfig.critical_min_risk_dimensions` 預設 3，可調高，須為 >= 3 的整數。
不另算一套總分；RSI、KD、短期趨勢、ATR 與輕微支撐跌破不提供額外投票。
單獨 TIGHTEN_RISK、單一弱勢、MACD 輕微變動或盤中暫時跌破都不能 bypass。
前支撐必須使用自己的強度，不能借用目前支撐的高強度；已收復區域也不能觸發。

### 狀態提交與解除

重大事件的三態風險目標固定為「偏空」。目前正式狀態尚非偏空時，
直接提交偏空，必要時突破 Hysteresis 的維持結果，跳過 Persistence，
清空 `signal_pending_candidate_state` 與 `signal_confirmation_count`。
原始均線分類與遲滯階段候選均保留於稽核紀錄，不改寫指標或假裝均線已轉空。

正式狀態已為偏空且事件持續時，維持偏空、不累積偏多候選；此時不是新的 bypass。
當前事件解除後，一律回到正常 Hysteresis → Persistence。歷史保留的持有風險標籤
不能重新觸發重大事件，因此不會造成永久鎖定，也不會瞬間恢復偏多。
這不是 cooldown：沒有固定等待時間，只使用原連續確認次數。

### 紀錄

`TradingDecision` 新增 `critical_event`、`bypass_persistence`、`bypass_hysteresis`、
`critical_event_rules`、`pre_bypass_candidate_state`、`persistence_cleared`、
`critical_event_details`。詳細紀錄包括當前 holder 規則、支撐來源、獨立風險面向、
目標與最終狀態。`persistence_cleared` 表示實際清除了原有 pending 或非零計數；
原本即無 pending 的 bypass 則為 false。

Persistence status 為 `CRITICAL_BYPASS` 或 `CRITICAL_RISK_RETAINED`。
`hysteresis_held` 保留遲滯階段本身的判斷；最終是否突破該結果看 `bypass_hysteresis`。
同輪重播與過舊資料不能 bypass。最終狀態與清空後的記憶仍在原 SQLite transaction 保存，
完整重大事件紀錄沿用 `trading_decision_history.decision_json`，不需要新增資料表。

測試：`tests/test_critical_events.py`。
