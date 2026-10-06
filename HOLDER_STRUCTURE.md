# 持有者支撐分工與突破結構（2026-10-02）

## 問題來源

原 `DecisionEngine.evaluate()` 直接用目前／前一個最近支撐的跌破狀態，搭配法人或動能決定
`REDUCE_EXPOSURE`；`evaluate_trade()` 又把相同支撐當成結構破壞的主要權重。
`attach_triggers()`、交易建議與操作參考皆把 `active_support_zone` 套入減碼條件。
因此突破後出現更高、更近的短線支撐時，持有邏輯的防守線也跟著上移。

## 兩種支撐

| 用途 | 資料 | 跌破後行為 |
| --- | --- | --- |
| 短線支撐 | 最近有效支撐、附近已失守支撐及支撐 interaction | 短線轉弱、停止加碼、提高警戒、觀察站回；不授權減碼 |
| 結構防守 | 近期帶量平台突破的原始範圍；否則重要 swing low／強支撐 | 確認失守且中期偏空後，才開放原風險分數的考慮減碼／減碼／退出分級 |

所有原始風險與保護面向仍計算。沒有結構失守或中期仍偏多時，持倉動作最多為觀察；
動能、法人、相對大盤與 ATR 仍影響風險分數。原有均線訊號可以偏空，與是否符合持倉減碼條件分開。
重大風險的支撐分支也改用結構防守；獨立多面向風險的訊號 Persistence bypass 保留。

## 平台突破辨識

`app/holder_structure.py` 僅使用截至觀察日期的已收盤 OHLCV，排除形成中的當日日線與未來資料。
不使用股票代號、固定價格或個人成本。

- 平台為突破前 `breakout_platform_bars=20` 根日線；上緣取該段 High 最大值。
- 至少兩次接近上緣（0.25 ATR 內），兩次測試至少間隔三根日線；整理寬度最多 `breakout_platform_width_atr=4` ATR。
- 突破日須為陽 K，收盤高於平台上緣至少既有 `volume_breakout_atr=0.25` ATR；量比至少既有 1.5。
- 量比基準為前 19 根日線均量；平台尺度使用突破前一天的既有 Wilder ATR14 計算，沒有未來資訊。
- 記錄 `breakout_level`、`breakout_candle_open`、`breakout_candle_low`、`breakout_date`，另存收盤、量比、平台起訖及當時 ATR 供稽核。
- 近期範圍由 `structural_breakout_max_days=90` 個日曆日設定；新的有效突破可替代舊事件。

平台上緣是回測起點；結構防守區下緣取平台上緣與突破 K 低點的較低者。
突破 K 開盤保留為區內參考。價格回到這個區間屬回測，不把跌回上緣或開盤價直接視為波段失效。
只有跌破區間下緣且達既有 ATR 確認條件，才記錄結構失守。

沒有近期突破時，先尋找已確認的重要 swing low（左右各 `structural_swing_window=5` 根、
反應至少 `structural_swing_atr_factor=1` ATR，沿用既有 swing 偵測器），其次使用既有強支撐
（`structural_support_min_strength=6`）。既有 swing 區域需達 `min_support_strength=4`。
不將未轉為支撐的強壓力區誤用為防守，也不因沒有結構資料就把最近弱支撐升格。

## 確認與保存

`DecisionContext` 保存 `breakout_event`、`structural_support_zone`、`structural_support_status`、
`previous_close` 與本輪結構評估標記。`price_context` 和 `trade_evidence` 同步提供兩種支撐，供各輸出層使用。

`decision_state.holder_structure_memory` 以 JSON 保存原始事件、防守區、最後確認狀態與收盤。
欄位透過既有 SQLite migration 建立，與同一筆決策交易一起提交。新近支撐更換或重啟、
行情視窗縮短，不會將原突破防守線換成最近支撐。

盤中不確認新的結構失守，也不解除已確認的失守。首次盤中掃描若發現先前已收盤的突破，
可保存該歷史事件，但不保存形成中日線作為收盤證據。過舊資料、重播與唯讀查詢沿用既有保護。
確認失守後，日線重新站回結構區上緣才是修復候選；持倉恢復仍須兩根新收盤確認。

新記憶標記 `support_policy=STRUCTURAL_V1`。舊版缺少結構用途的減碼記憶會重新評估，
避免因原最近支撐失守而持續保留錯誤減碼狀態；新制的遲滯、防抖與 Persistence 繼續有效。

## 輸出

- `短線支撐失守 → 短線轉弱，停止加碼／觀察是否站回`
- `結構防守確認跌破且中期趨勢轉弱 → 評估減碼`
- 沒有有效結構防守時，明示缺少結構參考，不產生最近支撐減碼線。
- 短線已失守時，不在持有者後續觀察同時宣傳加碼。
- 狀態變化歸因使用 `STRUCTURAL_SUPPORT_BREAK`；短線支撐失守只作警戒證據。

`tests/test_holder_structure.py` 覆蓋不同價格尺度、平台與量能門檻、盤中／未來資料排除、
短線與結構分工、無結構資料、強支撐與 swing fallback、SQLite 重啟／重播／過舊資料、
突破事件期限、恢復防抖及狀態變化歸因。規則參數尚未做投資績效校準，測試驗證的是決策行為。

完整回歸：**1024 passed**，1 個既有 Starlette/httpx 棄用提醒；`git diff --check` 通過。
命令：`.venv/Scripts/python.exe -B -m pytest tests -q --tb=short -p no:cacheprovider --basetemp=.pytest_tmp/structural_holder_20261002_final`。
