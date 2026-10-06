# 交易建議輸出檢查（2026-09-30）

## 2026-10-02 修正：盤中完整決策與進場風險降級

- 追查 `e73a382` 與目前未提交差異：收盤總 Gate 原已存在於 `evaluate_trade()`；近期改成明確的 `checks` 後仍將 `observation_complete` 放進 `all(checks.values())`。不是 `evaluate()` 在盤中提前 return，而是最後把計算結果覆蓋為 OBSERVE，且主因只保留未收盤訊息。
- `checks.observation_valid` 現在獨立檢查日期；`observation_complete` 留作事件確認 metadata，不再封鎖持倉決策。缺日期、錯誤日期、未來日期與舊資料仍不能當作有效當期行情。
- 近期 Entry Score 修改已讓高分強突破一根確認，但 `EXTREME_VOLATILITY` 與 `OVEREXTENDED` 仍在硬 Gate，`entry_conditions()` 又補上固定趨勢與連續日線文案，形成全 AND 的觀感。現改為軟風險降級、按路徑延長確認，輸出只列 1～3 個當前缺項。
- 盤中不抹除已收盤進場紀錄；當日未收盤條件波動不視為日線確認失敗。已收盤條件失敗仍重設，收盤去重、SQLite 重啟、持倉恢復防抖與三態訊號 Persistence 保留。
- ATR 若改變進場上限／確認根數，可出現在進場限制及後續觀察；持倉主要原因仍只在 ATR 改變持倉動作時列入。

現行規則見 [ENTRY_PATHS.md](ENTRY_PATHS.md)；以下保留 2026-09-30 的原始檢查紀錄。

## 修改前的資料有效性與時間設定

`DecisionEngine.evaluate_trade()` 原本只有一個 `checks.valid`：以下任一條件不成立就回到 `OBSERVE`。

| 條件 | 實際來源與規則 |
| --- | --- |
| `data_valid` | adapter 要求短、中期分析物件存在、label 不為「資料不足」、日線價格有效且 >0。原本未檢查趨勢 score 缺失。價格先取 support_resistance.current_price，再取 close。 |
| `observation_complete` | 日期取 result.history_date，缺少才取 result.date；stock.py 兩者均來自 data.index[-1]。早於本日算完成；同日需台北時間 >=13:30；缺日期、日期無法解析或未來日期不算完成。 |
| ATR | 必須存在且 >0；原本正無限值可能通過，已補有限數值檢查。 |
| 波動度分類 | 不得為「資料不足」或 UNKNOWN；已補 None／空字串檢查。 |
| `state_basis != STALE_DATA` | 本輪 observation_time 比已儲存 last_observation_time 更早；監測另與 previous_context_summary.context.observation_time 比較。這是日期倒退防護，沒有「距現在 N 分鐘／天」的門檻。原比較是 ISO 日期字串比較，上游提供 YYYY-MM-DD。 |

另有三種時間規則，不能混稱行情過期：

1. **歷史 holder 風險證據**：目前 observation_time 日期減去證據的 observation_time 日期，達 `retained_evidence_max_days=10` 個日曆日即失效；時間無法解析也失效。它只限制歷史證據保留，不直接設定交易 checks.valid=False。
2. **法人資料**：只採 date <= 查詢日且 available_at <= 查詢時間的資料。依最新資料 date 與 expected_trade_date 比較；預設 16:00 後要求當日交易日，之前接受前一交易日。13:30 後開始以當日為抓取 target。週末與 FlowConfig.closed_dates 跳過，預設 closed_dates 為空，尚非完整交易所日曆。非 FRESH 會把法人訊號改為 UNKNOWN 並排除計分；若原本可 HOLD，缺法人資料會使動作降為 OBSERVE；已成立的多訊號減碼不會因此消失。FlowConfig.max_age_days=10 目前沒有在 freshness/features 路徑使用，並非實際生效的法人 stale threshold。
3. **三態訊號持續確認**：使用監測輪次 signal_observation_time（預設目前 UTC 時間），無提供時才退回日線 observation_time。早於上次輪次為 STALE，相同為 REPLAY。它與日線是否完成、歷史 holder 證據年齡不同。

### 合理性判斷

- 收盤確認與倒序防護可保留，但未收盤應明示「尚未完成收盤確認」，不能聲稱缺行情或成本。13:30 是現有日線策略的固定收盤設定，本次不更改。
- 缺 ATR／波動分類會阻擋整體動作，是目前必要風險資料政策；本次保留並揭露真正原因，不擅自放寬進出場條件。
- 日期倒退判斷無法偵測「資料停在同一天、多日未更新」。若要增加即時行情過期判斷，須區分即時查詢與歷史重播，並採交易日曆；不能把 10 天歷史證據門檻當成行情門檻。本次未新增任意時效門檻。
- 法人的發布寬限有獨立用途，但空的休市日設定會在交易所假日誤認缺資料；本次未更動法人日曆政策。

## 兩句誤導文字的根因

- 「本輪缺少有效決策資料」：formatter 原本以 checks.valid 的預設 False 取代所有決策原因。未收盤也被混為缺資料；舊 payload 沒有 checks 時亦誤判。現在使用 engine 的 blocking_reasons；舊 payload 保留已記錄 reasons，不自行認定缺資料。
- 「持倉成本或本輪行情資料不足」：formatter 要求整體 valid 才顯示成本，否則一律寫不足。因此即使成本與現價完整，只因未收盤也會誤報。成本實際不影響本引擎的權重或動作，現在只留在稽核 cost_context，不固定出現在建議。
- 本機 stocks.db 唯讀抽查：共 80 筆交易歷史，全為 valid=True。該表僅保存符合寫入條件的日線結果，不能據此還原使用者看到的那次無效／盤中查詢，也不能聲稱已確認其唯一觸發條件。

## 修改後輸出契約

- 最終訊號、目前動作、主要原因、限制因素（有具體證據才顯示）、後續觀察。
- 保留部位的主要證據整併為限制因素，說明為何尚未進一步減碼；退出不以次要保護指標延後動作。
- 目前成本不改動作，因此不顯示固定持倉狀態。
- 後續觀察採「條件 → 動作」，從現有 holder triggers、entry_paths 與 recovery 產生；只有一條路徑缺條件，不代表另一條已成立路徑也受阻。沒有可用條件就省略，不填入泛用觀察句。
- 取消固定風險提醒與教育文字。主因使用實際影響動作的證據或阻擋條件。
- ATR 計分仍保留。引擎比較「有／無 ATR 放大」的動作分級，含缺漏資料對 HOLD 的限制；只有持倉、資料有效、動作確實不同時記錄 affects_action。formatter 再確認最終動作等於該候選動作才呈現 ATR，避免歷史保留動作錯用候選原因。
- `checks` 保存分項結果、blocking_reasons 與 observation_time；`action_constraints` 記錄使 HOLD 降為 OBSERVE 的具體缺漏。缺失／非有限趨勢 score 不再被默認成有效的中性趨勢。
- 保留原權重、交易門檻與連續確認機制。成本與全部 contribution 仍可供稽核。

回歸案例位於 tests/test_trade_output_validity.py，包含盤中／收盤邊界、日期缺失／格式錯誤／未來日期／倒退、ATR 非有限值、成本齊全仍被其他條件阻擋、舊 payload、缺法人資料阻擋續抱，以及 ATR 有加分但不改動作的情況。

## 驗證結果

- 三段說明結構更新後，完整 tests：**942 passed**；新增 14 個案例，使用 .pytest_tmp/trade_explanation_20260930_full。formatter 純函式檢查確認不修改輸入、分數或最終動作；一則既有 Starlette/httpx 棄用提醒。
- 完整 tests：**928 passed**；一則既有 Starlette/httpx 棄用提醒。使用專案 .venv、停用 bytecode/cacheprovider，暫存目錄為 .pytest_tmp/decision_output_20260930_full；測試 fixture 隔離正式資料庫。
- 唯讀重播本機 65 筆包含 weighted_score 的歷史快照，與原紀錄比較 weighted_score、raw_weighted_score、market_action，差異 **0**。此項驗證候選動作與分數，未重新提交監測記憶。
- formatter 與 DecisionEngine 已無指定的固定教育文字、泛用缺資料文字或固定風險提醒區塊。
