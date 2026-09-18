# 支撐／壓力位置狀態

本次只增加價格與既有區間的互動資料；Swing、VWAP、Volume Profile、K-Means、合併、強度與排序公式均未修改。

## 原有入口與整合點

- `support_resistance_analysis/engine.py`、`models.py`：偵測結果與區間模型。
- `support_resistance_analysis/lifecycle.py`、`lifecycle_integration.py`、`lifecycle_storage.py`：穩定區間 ID、角色、確認及 SQLite 保存。
- `support_resistance_analysis/formatting.py`：支壓顯示。
- `decision_zones.py`：報表與 decision 共用區間選擇。
- `decision_context.py`、`decision_engine.py`、`decision_transitions.py`、`decision_formatter.py`：decision 資料與操作參考。
- `analysis/price_context.py`：綜合判斷中的價格描述。
- `stock.py` 既有順序仍為偵測 → lifecycle → 報表／decision，通知事件規則不變。

## 結構

區間增加 `interaction`，核心分類集中在 `support_resistance_analysis/interaction.py`。欄位為 `zone_low`、`zone_high`、`role`、`state`、`previous_state`、`price`、`location`、`position_ratio`、`position_in_zone`、`distance_pct`。

`LevelInteractionState` 包含：

| 支撐 | 壓力 |
| --- | --- |
| ABOVE_SUPPORT | BELOW_RESISTANCE |
| TESTING_SUPPORT | TESTING_RESISTANCE |
| SUPPORT_BREAKDOWN_PENDING | RESISTANCE_BREAKOUT_PENDING |
| SUPPORT_BREAKDOWN_CONFIRMED | RESISTANCE_BREAKOUT_CONFIRMED |
| SUPPORT_BREAKDOWN_FAILED | RESISTANCE_BREAKOUT_FAILED |

兩端點皆屬區間內。單點區間以中段顯示，避免除以零。`FAILED` 只描述前一個 pending 後回到區間或另一側的觀察，不會把先前已確認的突破稱為未確認突破失敗。

## 保存及確認

沿用同一 `stable_zone_id` 的 lifecycle record。原資料表以冪等 migration 增加 nullable `interaction TEXT` JSON 欄位，不另建資料庫。每根新收盤日線將前次 `state` 存為 `previous_state`。同根重跑、舊日期不前進；盤中只投影、不寫入；歷史回放不讀取未來狀態。不同區間 ID 不繼承狀態，正式角色翻轉後也不繼承原角色的 pending。

確認直接讀既有 `BROKEN_SUPPORT`／`BROKEN_RESISTANCE`。原條件仍是 0.75 ATR，或 0.35 ATR 且前次也越界、量比至少 1.5；角色翻轉仍需後續回測／反壓確認。缺少 ATR 時只顯示 pending，不補造確認。decision 的連續確認、risk gate、holder／entry 規則均未改動。

沒有 lifecycle 的相容入口沿用 decision adapter 原本確認結果，並保留已提供的互動快照；不新增另一套歷史保存。首次發現且無歷史角色的 `active` 區間，仍為角色待確認，不從 Swing 方法名稱推測角色。舊資料庫的空欄位不補造未知的前一狀態。

## 顯示與 decision

中文轉譯集中於 `interaction_formatting.py`。formatter 不以價格自行判斷交易狀態。區間內不顯示負百分比；尚未觸及時用最近邊界距離，越界時用越界邊界距離。原有中心距離保留供既有排序、計算及事件使用。

`DecisionContext.level_interactions` 和 `TradingDecision.price_context.level_interactions` 保存同一份互動資料，觸發條件引用的 zone 也帶有 `interaction`。已離開 active 列表的 pending／confirmed 區間仍可在操作參考描述。決策分數與動作門檻不變。

## 3443 固定輸入案例

輸入為使用者提供的收盤 6500、支撐 6077.11～6127.56、壓力 6477～6503，並非即時行情。

修改前的壓力文字可能為「6477～6503（-0.15%）」及「重新站上壓力區」。修改後為：

```text
【支撐 / 壓力】
・最近支撐：6077.11～6127.56
目前位置：位於支撐區上方，尚未測試
距支撐區約 -5.73%
強度：中｜Anchored VWAP（Swing High）、Anchored VWAP（Swing Low）、K-Means

・最近壓力：6477.00～6503.00
目前位置：正在測試壓力區，價格接近區間上緣
強度：弱｜K-Means、Swing High
```

操作參考會包含：「目前正在測試 6477.00～6503.00 壓力區，後續需觀察能否站穩區間上緣並取得有效突破確認。」

## 驗證

`tests/test_level_interaction.py` 涵蓋雙側邊界、pending／confirmed／failed、零寬度、距離、無效輸入、邊界震盪、既有 ATR／量能確認、SQLite migration／重載、盤中與回放隔離、未知角色、不同區間與角色隔離、3443、結構序列化及操作參考整合。既有 compact 測試改驗證最近邊界距離。

執行：`.venv\Scripts\python.exe -m pytest tests -q -p no:cacheprovider --basetemp .pytest_tmp/interaction_final`

結果：修改前 478 項通過；修改後 517 項通過（新增 39 個案例）。另執行既有 `test_sr_comprehensive.py` 合成行情腳本成功。全套測試僅有既存 Starlette/httpx 棄用警告；未執行需要即時行情及使用正式資料庫的根目錄示範腳本。
