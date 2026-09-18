# 空手者 Entry Path 修改說明

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
