# 空手者 Entry Path 修改說明

## 目前規則（2026-10-02）

本節取代下方舊版的進場條件。沿用 `DecisionEngine` → `evaluate_entry_paths()` →
`stabilize_paths()` → SQLite `entry_path_memory`，沒有新增平行決策引擎。

### Hard Gate

- 分析無效、價格或 ATR 缺失／非有限值／非正數、波動分類缺失。
- 中期明確偏空；短中期皆偏空的既有風險仍保留。
- 當前支撐確認破位或前支撐失守風險仍存在；支撐 interaction 確認失守也阻擋。
- 法人 `STRONG_PRESSURE`、支撐成功率 `VERY_LOW／極低`。
- 每條路徑另需有效價位參考與中期偏多；核心條件盤中照常評估，實際進場確認才要求收盤。回檔不能使用待確認跌破的支撐。
- 舊日期不更新已保存狀態，也不把先前允許進場的結果重新當成本次進場建議。

極高波動（含 ATR% >= 8%）與正乖離過大改存 `risk_modifiers`，維持原扣分，
進場上限降為試單並延長確認；不要求先解除這些風險才開始累計。一般高波動只限制試單。
`confirmation_reasons` 記錄延長確認原因，與 `risk_blockers` 的硬性否決分開。

### 分數與三級結果

沿用 engine 的市場分數：短期趨勢最多 ±2、中期分數最多 ±2、動能改善 +1／
空方加速 -1、法人偏多或強力買超 +2／偏空 -1／強力賣壓 -2、支撐高評等 +2／
低評等 -2、相對大盤強弱 ±1，以及既有破位、拒絕與波動扣分。
法人 UNKNOWN／MIXED／NEUTRAL 都不加分、不阻擋；過期法人資料仍由既有 freshness 規則轉為 UNKNOWN。

各路徑另加：突破或回檔核心成立 +2、站上 MA5 +0.5、量比 >= 1.5 +1、
回檔支撐強度 >= 4 再 +1。支撐與突破的核心分數按路徑各自計算，不互相借用。
MA5、MACD、短期方向、支撐強度都是輔助證據，不必全部成立。

通過 Gate 與核心條件後：

| 分數與確認結果 | `entry_action` |
| --- | --- |
| 分數 < `probe_score_min`（預設 3），或確認未完成 | 不允許進場，保留 WAIT／WATCH 等既有觀察狀態 |
| 3 <= 分數 < `entry_score_min`（預設 6），且確認完成 | `ALLOW_PROBE_ENTRY` |
| 分數 >= 6，且確認完成 | `ENTRY_CONDITION_MET` |
| 高／極高波動或正乖離過大，且確認完成 | 最多 `ALLOW_PROBE_ENTRY` |

`entry_paths` 保存每一路徑的分數、分項、門檻、目標動作、Gate 與確認根數。
分數是規則權重，不是獲利機率；本次測試驗證邏輯，未校準投資績效。

### 突破流程

1. 沿用現有壓力區優先序與近期突破參考；失敗／過期突破不能冒充新突破。
2. 中期偏多，並滿足以下任一條件：
   - 強突破：收盤超過壓力上緣至少 `breakout_atr`（0.5 ATR），不強制大量。
   - 一般突破：至少 `volume_breakout_atr`（0.25 ATR），且量比 >= 1.5。
3. 通過 Gate，累計該路徑分數，再按確認根數輸出試單或正常進場。

Entry 的幅度／量能判斷由共用 `breakout_strength()` 提供；可讀取 lifecycle
`RESISTANCE_BREAKOUT_PENDING`，不必先等角色翻轉確認，再額外等待兩天。
支壓 lifecycle 本身的角色翻轉門檻維持獨立。

### 回檔流程

中期偏多 → 目前價格接近有效支撐（上緣外最多 1 ATR）且未跌破下緣 →
支撐 HOLDING／RECLAIMED／跌破失敗收回，或 TESTING_SUPPORT → Gate → 分數 → 收盤確認。
不要求 `price > MA5`，不要求 MACD 與支撐強度同時達標；高波動可試單，極高波動延長確認後也可試單。

### 一根／兩根收盤與 Persistence

- **1 根**：強突破且分數 >= 6；或回檔已守穩／重新站回且分數 >= 6；兩者均無極高波動與正乖離過大。
- **2 根**：一般放量突破、單純支撐測試、僅達試單分數的較弱訊號，或極高波動／正乖離過大。
  沿用 `confirmation_required`（預設 2，可配置更長）。
- 進場只由路徑層確認一次；移除 entry 的額外固定兩根確認，持有者改善與三態均線訊號各自沿用原 Persistence。
- 依台北日期去重，同日不同監測時間不多算一根；已收盤資料的路徑或區域改變、條件不成立會重設。盤中評估不增加也不抹除先前的收盤確認紀錄。
- 重啟恢復同一份 JSON 記憶。記憶帶規則版本與設定，版本／設定變更後以當前有效收盤重新評估，避免同資料重播保留舊決策。
- 兩條路徑以 OR 合併；任一路徑確認可進場，有正常進場路徑時優先於試單。
- 路徑記憶版本為 3，避免沿用舊版硬性封鎖產生的確認紀錄。

### 盤中與輸出

`evaluate_trade()` 將日期有效性與是否收盤分開；盤中仍計算趨勢、支壓、動能、法人、
相對大盤與持倉動作。無效日期／未來日期／倒退資料仍保留原防護。進場、風險解除及
重大事件各自使用收盤確認；三態訊號依原監測輪次 Persistence 處理，不把日線未收盤當總開關。

後續進場觀察只列所選路徑目前未滿足的 1～3 項條件；尚未突破時先列幅度／量能的 OR，
核心已具備才列尚欠的收盤確認。已達成的趨勢條件不重複列出，也不把軟風險寫成必須解除。

### 驗證

2026-10-02 完整測試 **1001 passed**，1 個既有 Starlette/httpx 棄用提醒。
命令：`.venv/Scripts/python.exe -B -m pytest tests -q -p no:cacheprovider --basetemp=.pytest_tmp/intraday_entry_20261002_c`。
新增 `tests/test_intraday_entry_policy.py` 覆蓋盤中各面向與持倉動作、軟風險確認後可試單、
盤中不重設收盤紀錄、實際缺項輸出、風險恢復防抖與 SQLite 重啟後的訊號 Persistence。

`tests/test_entry_scoring.py` 涵蓋三級門檻、Hard Gate、法人中性／未知、MA5 與
動能可互補、高波動降級、實際 lifecycle 尚未翻轉時的一根／兩根進場、同日去重、
不完整收盤、條件中斷、過期資料、SQLite 重播與規則版本更新。

2026-10-01 歷史驗證：`.venv/Scripts/python.exe -B -m pytest tests -q -p no:cacheprovider
--basetemp=.pytest_tmp/entry_score_20261001_final`，**989 passed**；1 個既有
Starlette/httpx 棄用提醒。修改檔案的 `git diff --check` 通過。

## 舊版設計紀錄（以下 AND 條件與固定兩根確認已由上方規則取代）

1. **原始位置**：`app/decision_engine.py` 負責進場與持有者決策；`decision_context.py` 轉接趨勢、風險及支壓資料；`decision_transitions.py` 組裝升級條件；`decision_formatter.py` 產生操作參考；`decision_state.py` 執行連續收盤確認與 SQLite 儲存。
2. **實際 AND 問題**：原 engine 已有突破及支撐試單兩個分支，並不是單一 `support AND breakout` 判斷。錯誤主要在 `entry_upgrade_triggers` 同時放入 SUPPORT_HOLD、RECLAIM_KEY_LEVEL、VOLUME_CONFIRMED_BREAKOUT，再由 formatter 的 conditions() 用「，並」串接，將不同路徑表達為共同必要條件。
3. **新增路徑**：`app/entry_paths.py` 提供 BREAKOUT_ENTRY、PULLBACK_ENTRY；沒有路徑 ready 時 active_path 為 NO_ENTRY。兩條路徑始終保留各自結果，不強制互斥。
4. **突破型**：中期偏多、短期非偏空，且既有壓力 interaction／legacy status 已確認突破，資料及風險允許。直接沿用上游 ATR、量能與 lifecycle 確認結果，不另計突破門檻。不要求測試下方支撐。高波動仍沿用「僅可小幅試單」限制。
5. **回檔型**：中期偏多；同一有效支撐正在測試或已 HOLDING／RECLAIMED，符合既有支撐強度門檻；既有動能分類為 bullish_strengthening 或 bearish_weakening，且站回 MA5；風險允許。TESTING 本身不等於守穩，須連續有效收盤確認。遠離支撐不使用舊 HOLDING；失守待確認不進場；確認失守或中期轉空失效。未加入突破回踩策略。
6. **OR 合併**：`entry_ready = breakout.ready or pullback.ready`。READY breakout 映射既有 ENTRY_CONDITION_MET（高波動降為 ALLOW_PROBE_ENTRY）；READY pullback 映射既有 ALLOW_PROBE_ENTRY，保留回檔試單的積極程度。原 entry_score 仍供稽核，不再作為混合兩條路徑的進場門檻。
7. **狀態與確認**：INACTIVE、WATCHING、WAITING_CONFIRMATION、READY、BLOCKED_BY_RISK、INVALIDATED。純 evaluator 的 READY 是待 debounce 的候選；對外分析及監控仍經 stabilize。新增 `entry_path_memory` JSON 欄位，按路徑及 zone identity 各自使用原 confirmation_required（預設 2）計數；相同日期不累加，換路徑／價位不借用計數，資料過期不形成候選。
8. **風險**：沿用既有 risk_gate，另保留過熱乖離、必要資料／法人資料不確定、高波動回檔限制。核心價位條件未成立時仍是等待狀態，blockers 留在各路徑；條件已成立但風險未解除才是 BLOCKED_BY_RISK。持有者風險分數與規則未改。
9. **支壓狀態**：優先使用已建立的 interaction，再回退相容 legacy status。測試壓力及突破 pending 等待；確認突破才具備突破條件；突破 failed 失效。支撐 pending 等待、confirmed break 失效。被 lifecycle 移出 active 清單的交互狀態仍可讀取；已跌回原壓力內的歷史 confirmed 紀錄不可當成有效突破。
10. **Formatter**：讀取 entry_paths 中 status、zone、missing、risk_blockers、確認計數；僅選主要與另一條獨立路徑，不重新計算技術條件。升級 trigger 以 `ENTRY_PATH`／`operator: OR` 分組。debug 與 JSON 序列化保留完整結果。持有者原 conditions() 僅用於持有者改善條件；舊的未帶 entry_paths payload 仍保留相容顯示。
11. **持有者**：續抱、減碼、退出、holder debounce 與 evidence 規則均未重新設計。SQLite 僅增加 Entry 記憶欄位；測試使用隔離資料庫，未對正式資料庫執行 migration。
12. **測試**：新增 `tests/test_entry_paths.py`，涵蓋兩種單獨 READY 的 OR、兩條同時 READY、突破測試／失敗／風險阻擋、支撐測試／失守／動能不足、確認次數不跨路徑與價位、同日重播、未收盤、SQLite 重啟、過期資料、序列化、3443 輸出、回檔確認通知歸因及排除突破回踩。完整 `tests` 回歸包含支壓、risk、holder、trend、notification、formatter。
13. **3443 範例**：使用需求提供的測試資料，非即時行情。收盤 6500、支撐 6077～6128、壓力 6477～6503、短期 +5、中期 +7、高波動／乖離。修改前是「支撐守穩，並壓力突破，才可進場」。修改後 breakout 為 WAITING_CONFIRMATION、pullback 為 INACTIVE、overall false；保留原過熱時「不宜追價」標籤，未因偏多而升級。

   ```text
   主要進場路徑：突破型
   目前正在測試 6477.00～6503.00 壓力區；尚未完成有效突破確認；
   進場確認須連續 2 根新收盤日線符合此路徑條件且風險限制解除。

   另一條獨立路徑：回檔型
   若後續拉回，觀察 6077.00～6128.00 支撐；
   須守穩確認、短期動能改善且風險允許，才具備回檔型進場確認條件。
   ```

14. **範圍與限制**：沒有需要大幅重構才能拆分的部分。但既有前支撐失守風險閘門仍可能阻擋新的支撐進場，本輪未變更該風險解除政策。無監控歷史的單次查詢不會累積兩次確認；需監控的不同有效收盤觀察。保留舊 ActionState 以維持通知相容性，具體路徑使用 entry_paths 查閱。沒有加入抄底、反轉、反向交易、加碼或部位配置。

驗證命令：

```powershell
.\.venv\Scripts\python.exe -m pytest tests -q -p no:cacheprovider --basetemp=.pytest_tmp/entry_paths_final
```

結果：541 passed；1 個既有 Starlette／httpx 棄用警告。
