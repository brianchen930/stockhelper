# Momentum / Trend / Combined Direction

本輪只拆分解釋資料與摘要，不將新的綜合方向回灌交易策略、Entry evaluator 或通知分數。

1. **原本混合的位置**：`analysis_engine.py` 的 `_analyze_rule_bias()` 以文字關鍵字計數，再由 `_determine_market_bias()` 混入均線排列及原始 signal。`scheduler.py` 將混合結果稱為「基礎訊號摘要」。RSI／KD 同一規則內另有通知權重、極端風險及方向證據，先前缺少可供摘要使用的明確分類。
2. **MOMENTUM**：MACD 黃金／死亡交叉、柱狀體動能；KD 黃金／死亡交叉。交叉沿用原權重 2，柱狀體沿用 1，另以正負號表示方向。負柱縮小仍是弱空方證據，並不直接當成多方翻轉。
3. **TREND**：沿用既有 `medium_term.score` 與 `score_to_view()`；保留短期分數及日線 MA 狀態供追蹤。沒有有效中期分數時，使用既有 `analyze_ma_strategy()` 給出的均線狀態。沒有新建 MA rule，也沒有重算另一套均線或高低點模型。
4. **RISK**：RSI 超買／超賣、KD 高低檔及 J 值極端／相對高檔，以及短中期分析已提供的風險提醒。KD J < 0 改明確標為極弱位置風險，其原通知分數保留。風險證據不進入動能多空分數；既有風險狀態與風險閘門不受這份摘要影響。
5. **EVENT**：`SignalChangeRule` 同時處理 signal change 與 trend change，專案沒有獨立的 `TrendChangeRule`。首次監控及支壓事件也屬 EVENT；繼續出現在命中規則／事件區，不作動能證據。
6. **資料結構**：新增 `RuleCategory`（MOMENTUM、TREND、RISK、EVENT、CONFIRMATION）；使用 `rule_category`，避免覆寫事件原有的 `category: signal_change` 等欄位。同一 rule 可有多種 evidence；KD 的交叉與過熱分開保存。evidence 有 `category/code/reason/directional_score`。RuleEngine 新增 `rule_results/evidence`，規則結果另以 `notification_score` 明確標示舊 score 的用途。分析結果新增 `signal_summary.momentum/trend/combined/risk`。
7. **Momentum 計算**：僅加總 MOMENTUM 的帶符號分數，分別保存 bullish/bearish score、規則清單與數量。差值正負決定方向，沿用摘要原有 3 分強方向界線；1～2 分為中性偏多／空。RSI 正常區是 CONFIRMATION、方向分數 0，原通知 +1 保留，但顯示不再寫成「訊號確認 +1」。
8. **Trend 計算**：主要方向沿用既有中期分數的五級 mapping。short_term_score、medium_term_score、ma_trend、source 都保留。現有短中期分數本來包含部分 MACD／量價；本輪不為理論上的完全獨立而改寫它們，也不宣稱 Trend 是新的純結構模型。
9. **Combined 計算**：趨勢為主，動能作同向／分歧修正，不平均分數。同向多→偏多，同向空→偏空；多頭趨勢遇空方動能→中性偏多，反向則中性偏空；雙中性→中性。趨勢中性但有動能方向時，最多為中性偏多／空。這是顯示用總結，不取代原策略 signal。
10. **Alignment**：ALIGNED_BULLISH、ALIGNED_BEARISH、DIVERGENT、NEUTRAL；缺少有效趨勢／行情時為 INSUFFICIENT_DATA。分歧說明直接引用兩層結果，不另產生第三套指標理由。
11. **Strength**：Momentum 沿用原摘要的方向證據數量分級（1 弱、2 中、3 以上強），內部多空混合降為弱。Trend 沿用現有分數分級邊界（絕對值 4／2），為強／中／弱；只有 MA 狀態時最多中。Combined 同向取兩者較弱的強度，分歧／未確認為弱；強度不代表勝率。
12. **Formatter**：共用 `format_signal_summary()`，終端機及 Discord 改用「【訊號摘要】」，分列動能、趨勢、綜合方向與狀態；風險另列。formatter 不讀原始指標、不計算方向。移除關鍵字推算多空的舊摘要方法。通知顯示的「股票訊號分數／規則分數」改為「通知權重」。
13. **短中期分數**：計算、門檻、短期／中期看法區塊均保留。只把既有結果傳入新的解釋層，沒有改寫 `short_term.py`、`medium_term.py`。
14. **Entry / Holder / Risk / Notification**：本輪未修改 DecisionEngine、Entry paths、持有者或支壓狀態模型；新摘要不回灌它們。保留通知觸發、分數、等級、去重及發送政策。摘要與少數規則顯示文字更新後，既有包含文字的通知簽章會有所不同，但不會單獨創造通知觸發事件。未在本輪發送任何實際通知。
15. **測試**：新增 `tests/test_signal_layers.py` 共 22 項，覆蓋 3443 分歧、五類整合案例、動能獨立於 MA／signal／通知分數、事件隔離、中性 RSI、KD 同時動能與風險、極端值、沿用中期分數、不足資料、formatter 純呈現、序列化、API 與 RuleEngine 一致，以及既有交易決策與通知權重維持不變。
16. **3443 前後**：以下為需求提供的案例測試，非即時行情。原本「偏空 1、偏多 0，基礎方向中性偏多」混合了不同層次；現在分別呈現：

    ```text
    【訊號摘要】
    動能：中性偏空｜弱
    ・MACD 柱狀體仍在零軸下，但正在縮小，空方動能減弱，但尚未完成多方翻轉。
    ・RSI 64.76，中性，未對目前訊號形成明顯干擾
    趨勢：偏多｜強
    ・日線均線呈多頭排列
    ・月線方向向上
    綜合方向：中性偏多｜弱
    狀態：趨勢與動能分歧
    整體趨勢仍偏多，但短期動能尚未完全跟上。
    風險提醒：KD J 81.00，位於相對高檔
    ```

相容性：`generate_analysis()` 保留舊參數，但純文字 matched_rules 不再推測方向；新呼叫者應提供 `rule_evidence`。未提供時明確標示缺少可分類動能證據。`market_bias/strength/summary` 保留作為新結構的相容欄位。

驗證：

```powershell
.\.venv\Scripts\python.exe -m pytest tests -q -p no:cacheprovider --basetemp=.pytest_tmp/signal_layers_full
```

結果：563 passed，包含新增 22 項；一個既有 Starlette／httpx 棄用警告。相關來源檔 `git diff --check` 通過。
