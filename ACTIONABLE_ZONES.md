# 可操作支壓與歷史追蹤分離

1. **原本的問題**：`support_resistance_analysis/formatting.py` 逐筆走訪 `historical_zones`，輸出 BROKEN_SUPPORT、BROKEN_RESISTANCE、ROLE_TRANSITION 及其 interaction；沒有距離、時效或數量限制。此外，未具明確角色的 detector `active_zones` 也出現在主畫面。
2. **新增篩選**：`support_resistance_analysis/selection.py` 提供唯讀 `actionable()`、`select_zone_display()`。回傳支撐、壓力、次要觀察三個欄位，每筆有 `zone / display_role / is_actionable / display_priority`。主列表各選一個最近有效角色，使用距區間邊界的距離，不修改原始列表。
3. **正式顯示的 state**：支撐接受 ACTIVE_SUPPORT、RECLAIMED_SUPPORT、RESISTANCE_TO_SUPPORT；壓力接受 ACTIVE_RESISTANCE、SUPPORT_TO_RESISTANCE。仍須與 current_role、interaction、目前價格位置一致。無 lifecycle 的舊資料沿用既有角色列表，但排除已跨越或明確失去角色的項目。
4. **預設隱藏**：BROKEN_SUPPORT、BROKEN_RESISTANCE、ROLE_TRANSITION、MINOR_BREAK、INVALIDATED 不當成正式支壓；未有明確角色的 detector active zone 也不直接顯示。其他歷史區間保留在分析及 debug／research 輸出。
5. **次要觀察區**：只取近期、接近現價且具有突破／跌破 interaction 的候選區，最多一個。尚未完成突破確認時明確寫「尚待有效突破確認」，不宣稱已突破；確認突破但未完成角色轉換時，說明仍須回踩確認。
6. **近期相關性**：距離使用既有 `calculate_atr_distance()`，以最近邊界計算 `distance / ATR`，沿用 `DecisionConfig.near_zone_atr`（預設 1 ATR）。另有具名 `ZoneDisplayConfig.recent_transition_days=3`，為日曆日；confirmed 使用原 `break_confirmed_at`，pending 使用觀察時間。缺 ATR、日期、未來日期或過期 confirmed 不展示為次要觀察區，不使用固定百分比替代。pending 的觀察時間代表目前仍在測試，並不宣稱已知最初跨越日期。
7. **不冒充正式角色**：次要觀察固定 `is_actionable=False`、priority 2，與 priority 1 的正式支壓分開。`decision_zones.displayed_zones()` 共用角色檢查，決策不從 secondary 取防守支撐。只有既有 lifecycle 正式確認角色轉換後，新的支撐／壓力才能進入主列表。
8. **Entry 的歷史引用問題確實存在**：原 `entry_paths.py` 只要 previous resistance 有 confirmed／pending／failed 就覆蓋 active resistance，造成較低的歷史突破區一直占用主要路徑。本輪改為 active resistance 優先，並移除已不屬於當前突破路徑的 Entry 突破加分與原因。
9. **Entry / Holder 的當前參考**：Breakout 使用最近有效壓力；其下方為 WATCHING、測試為 WAITING_CONFIRMATION。Pullback 及 Holder 防守參考使用最近有效支撐。Holder 的未來站回條件不再引用失去角色的舊支撐。歷史失守仍可保留為風險觸發證據，並不被當成目前防守區。若完全沒有 active resistance，僅容許近期且 1 ATR 內的真正突破紀錄作 `breakout_reference_zone`，讓新突破仍可完成原有確認；這不等於將該區當成支撐，超時或遠離即停止使用。
10. **歷史全部保留**：偵測、ATR 計算、breakout／breakdown 確認、role reversal 狀態推進、資料庫儲存與歷史事件皆未重寫或刪除。selector 回傳副本；支壓事件仍可獨立顯示歷史價位。已確認 RESISTANCE_TO_SUPPORT 可使用既有 Pullback 路徑，未新增獨立交易策略。
11. **測試**：新增 `tests/test_actionable_zones.py` 共 17 項，涵蓋 active 角色顯示、歷史隱藏、最多一個 secondary、ATR 門檻、過期／未來／缺日期、pending 不冒充、失守支撐、錯置列表防護、目前 B 壓力優先於歷史 A、Holder 參考、近期突破保留、實際 role reversal 推進、事件／序列化／歷史不變。舊測試中「所有 active／historical 必須顯示」及「已完成角色轉換仍不能使用回檔路徑」的預期依本輪需求更新。
12. **2408 南亞科前後**：用需求指定價位建立測試案例，並非即時行情。原本主畫面列出 469、479、483、488 等歷史區間，Entry 也可能引用 483.73～485.59。現在主要輸出為：

    ```text
    【支撐 / 壓力】
    ・最近支撐：503.95～506.53
    目前位置：位於支撐區上方，尚未測試
    距支撐區約 -3.52%

    ・最近壓力：531.95～541.05
    目前位置：位於壓力區下方，尚未測試
    距壓力區約 +1.32%
    ```

    原有強度及來源欄位仍顯示。若 513.42～519.60 符合近期與 ATR 條件，最多補一筆次要觀察區，不能取代 503.95～506.53。

    ```text
    空手者｜不宜追價
    主要進場路徑：突破型
    目前上方 531.95～541.05 為最近有效壓力，尚未完成突破確認。

    另一條獨立路徑：回檔型
    若後續拉回，觀察 503.95～506.53 支撐；
    須守穩確認、短期動能改善且風險允許。
    ```

    Breakout Path = WATCHING，沒有因舊區間突破而 READY。之後 513～519 若經現有 lifecycle 完成角色轉換，可成為新的最近支撐。

驗證命令：

```powershell
.\.venv\Scripts\python.exe -m pytest tests -q -p no:cacheprovider --basetemp=.pytest_tmp/actionable_final
```

結果：580 passed，含新增 17 項；僅有一個既有 Starlette／httpx 棄用警告。沒有執行正式行情抓取、實際通知發送或歷史資料刪除。
