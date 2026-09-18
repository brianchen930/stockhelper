# Zone Lifecycle：價格區角色一致性

## 接入方式

沿用 `SupportResistanceEngine.detect()` 的區域，不重算指標、聚類或模型。
`stock.get_stock_analysis()` 在法人與 Bayesian 附加完成、歷史區域取得後，呼叫
`attach_zone_lifecycle()`；隨後重新格式化區域報告、產生原有短中期分析及 Decision Context。

原始 `support_zones`、`resistance_zones`、`active_zones` 保持不變，供既有通知規則與研究使用。
新增 `support_resistance.zone_lifecycle` 包含 canonical active view、必要區域紀錄及預測事件。
共用 `displayed_zones()`、價格情境與 Decision Context 讀取此 view。
因此原始 detector 的 `type` 是當輪候選分類，不能再當作已確認的生命周期角色。
本次未修改 scheduler、通知觸發／去重、Trading Decision Engine 評分與 Action State，
亦未實作尚待確認的 Position Action Engine。

## 角色與確認

狀態包含 ACTIVE_SUPPORT、ACTIVE_RESISTANCE、BROKEN_SUPPORT、BROKEN_RESISTANCE、
SUPPORT_TO_RESISTANCE、RESISTANCE_TO_SUPPORT、INVALIDATED、ROLE_TRANSITION，
以及 MINOR_BREAK、RECLAIMED_SUPPORT。INVALIDATED 保留為明確作廢介面，
不會只因本輪未偵測到區域就作廢或刪除。

支撐確認失守：收盤距離原邊界至少 0.75 ATR；或至少 0.35 ATR 且前一有效觀察已在下方、量比至少 1.5。
結果僅為 BROKEN_SUPPORT，退出 active support，也不直接加入 active resistance。

支撐轉壓力：必須在 break_confirmed_at 之後的新已收盤日線，前次價格在區域下方，
當日 High 回測原下界，收盤又低於下界至少 0.1 ATR。此為價格回測遭拒證據，
不會把跌破當根 K 棒的 High 當作後續回測。成立後才是 SUPPORT_TO_RESISTANCE。
反向壓力突破／回測承接也採對稱流程。

小幅跌破仍為 MINOR_BREAK，後續站回可為 RECLAIMED_SUPPORT。
ATR 不足不強行確認失守；未確認穿越在 view 中標為角色待確認。
無歷史身分且同時出現相反角色的高度重疊候選，標為 ROLE_TRANSITION，不宣稱已完成 flip。

## 身分配對與不連鎖合併

`ZoneLifecycleConfig` 集中設定：

| 設定 | 預設 |
| --- | --- |
| overlap_min | 0.8 |
| center_distance_atr | 0.15 ATR |
| width_difference_atr | 0.2 ATR |
| center_distance_width | 較窄區域寬度的 0.15 |
| width_difference_ratio | 較窄區域寬度的 0.25 |
| confirmed_break_atr / persistent_break_atr | 0.75 / 0.35 |
| volume_confirmation_ratio | 1.5 |
| rejection_atr | 0.1 |

配對必須同時通過重疊、ATR 距離及相對寬度限制；高 ATR 不足以讓相鄰區域合併。
若上游提供不同 origin_id，也禁止配對。
多對多候選依重疊、距離、既有 ID 及幾何鍵排序，做 deterministic greedy one-to-one matching。
同一旧區域最多匹配一個新區域，輸入排序不影響選擇。

同輪相反角色的重複候選採 complete-link 分組，每個新增成員需匹配組內所有成員，
防止 A 接近 B、B 接近 C 就把 A/C 合併。相同角色的不同區域保留獨立候選；精確重複先去重。
每個 lifecycle ID 保留固定 anchor_low/high 作跨輪匹配與事件基準，避免慢慢漂移造成跨輪 chain merge。
zone_low/high 可更新小幅偵測邊界；角色變化不變更 stable_zone_id。
超出固定 anchor 容許範圍會成為新 ID，而非無限吸收漂移。

## 最小化 schema / migration

沿用 `data/stocks.db`；`database.create_tables()` 與 lifecycle 首次使用均呼叫可重複執行的
`CREATE TABLE IF NOT EXISTS`。既有 watchlist、decision_state、法人資料表不變，無欄位刪除或資料清空。

### zone_lifecycle

- stable_zone_id：主鍵。
- symbol、timeframe（本版 1d），另有複合查詢索引。
- zone_low、zone_high：最新匹配邊界。
- anchor_low、anchor_high：固定身分／回測邊界。
- previous_role、current_role、status。
- break_confirmed_at、flip_confirmed_at、last_seen_at。
- last_observation_at、last_price：避免重跑確認及判斷後續回測所需的最小記憶。
- methods、origin_id、strength_score、strength_label：來源稽核與既有顯示所需欄位。

### zone_bayesian_events

- prediction_id：主鍵。
- stable_zone_id：區域關聯。
- prediction_time：預測實際產生／可用時間。
- observation_time：預測對應的日線收盤時間。
- support_probability：原始 posterior，不再次加權法人。
- predicted_level：原始模型評等。
- model_reference：模型檔名、mtime_ns、size。
- actual_outcome、outcome_time：可追蹤的 BREAK 結果。
- UNIQUE(stable_zone_id, observation_time, model_reference)：同輪重跑不重複預測事件。

這兩張表沒有完整 Context、技術指標序列、法人歷史複本或行情歷史庫。
讀取、狀態更新與事件解決在 SQLite 交易內完成。

## Bayesian 時序與顯示

不更改 posterior、likelihood、rating 或法人調整數值；只在 integration 附 prediction_time 與 model_reference。
只有連結到 canonical active support 的 ready 預測才建立新的待完成事件。
後續確認支撐失守時，僅把 prediction_time 與 observation_time 都早於失守時間的紀錄標為 BREAK。
同一天跌破後才出現的預測不倒填成事前預測，不回填無可靠時間證據的舊紀錄。

候選仍保留原 result/display，另附 zone_lifecycle 關聯。失效、角色轉換、未連結及被 active view 排除的候選，
正常模式不再輸出「模型評估支撐」；debug 顯示 HISTORICAL BAYESIAN 與 ZONE DEBUG。
這些 BREAK 是此版本 lifecycle 定義下的結果，不能不經事件定義核對就混入既有 Bayesian 回測標籤。

正常輸出：

- 剛確認失守：原支撐已失守，尚未確認轉為壓力。
- 後續回測遇阻：最近壓力，來源「原支撐轉壓力｜來源：Swing Low」。
- 新下方支撐：獨立顯示最近支撐，不沿用舊模型區域。

Decision Context 接受已解析角色，不再用單根價格自行判定 flip。
舊支撐只保留於歷史風險來源，與目前 active_support / active_resistance 分開。

## 時間與限制

本版採已收盤日線（台北 13:30）；重跑同日或較舊觀察不推進原有區域狀態。
盤中角色只供暫時 view，不保存失守／flip；較舊歷史查詢不套用未來 live state，也不改写 live state。
初次使用可由已取得的前期區域建立初始身分；沒有更早保存紀錄時，不宣稱已掌握完整歷史生命周期。
未提供 origin_id 的上游只能靠嚴格幾何匹配，不能保證辨識所有完全同價的不同事件來源。
配對、跌破、回測門檻均為需回測校準的初始設定。

## 測試

`tests/test_zone_lifecycle.py` 覆蓋 A～D、MINOR_BREAK／快速站回、邊界漂移、相鄰高 ATR 區域、
one-to-one、同輪與跨輪無 chain merge、盤中／重跑／倒序、原始輸入不變、
SQLite 遷移可重複、重啟 ID 延續、預測去重與時間先後、Bayesian presentation、Decision Context active view。
測試的 LifecycleStore 一律使用隔離 SQLite，不寫入使用者的正式行情資料庫。

本次指定範圍測試（lifecycle、Bayesian presentation、Decision Context、支撐壓力與通知事件回歸）
結果為 **95 passed**。
完整測試使用 `.venv/Scripts/python.exe -B -m pytest tests -q -p no:cacheprovider --basetemp=.pytest_tmp/lifecycle_full2`，
結果 **427 passed**（13.77 秒），一則既有 Starlette/httpx 棄用提醒。
`git diff --check` 無格式錯誤；指標、ATR、短中期計算、原區域偵測器、Bayesian model、法人及 notifier 無原始碼差異。
正式資料庫會於程式啟動或首次 lifecycle 呼叫建立新增表；本輪 migration 驗證均在隔離資料庫進行。
