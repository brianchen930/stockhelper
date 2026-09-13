# 支撐／壓力輸出與通知整合

## 一般通知精簡顯示（目前版本）

以下舊版詳細輸出範例保留作歷史說明；目前一般通知使用 `【支撐 / 壓力】`，
最多列出一個 active 區域、一個最近支撐、一個最近壓力。以絕對 distance_pct
選最近區域，不直接假設底層強度綜合排序的第一筆就是最近。完整 S1/S2/S3、
R1/R2/R3、touch、日期、center、score、warnings 與事件列表均留在原分析結果。

每區只顯示範圍、距離（active 可省略）、強度文字及方法。
事件 formatter 只顯示一個主要事件，優先級為突破／跌破 > 進入／目前測試 >
反彈／受阻 > 接近。`CURRENTLY_TESTING_ZONE` 僅是 formatter 的顯示選項，
沿用 active_zones，不新增通知事件、不修改判斷、cooldown、pending 或送達紀錄。
raw enum、收盤價重複資訊、ISO 日期與實作說明不出現在精簡事件段落。

均線文字矛盾並非 timeframe 差异或資料庫自行判斷：原 technical summary
只比較 MA5/20/60，`analyze_ma_strategy` 還比較 Close 與 MA5。現在
`build_technical_summary` 直接採用 `analysis['trend']`，和 scheduler 傳給
RuleEngine、generate_analysis 的 current_trend 同源；標示為「日線趨勢」。
策略、原始 signal、分數與資料庫狀態不變，不回寫或重置歷史 trend。
真正的短期／中期分析仍獨立顯示，不強制統一多週期判斷。

本次修改：區域 formatter、事件 formatter、scheduler 的 formatter 呼叫、
analysis_engine 的摘要文字來源，以及相關測試。128 項測試通過，新增
`tests/test_compact_presentation.py` 驗證顯示上限、完整資料不變、事件優先級
及同份日線資料的均線一致性。完整真實單股輸出見
`runtime_verification/terminal.txt`（2408，2026-09-11 行情）。本次沒有新通知，
runtime 未發送 Discord，通知格式經 scheduler/notifier 測試驗證。

以實際 runtime code 為準，未使用 `DETAILED_CHANGES.md` 的舊整合架構。
四種區域偵測演算法與模型不變，沒有新增依賴，也沒有修改 API endpoint。

## 實際資料流與輸出缺口

1. `app/stock.py` import `support_resistance_analysis`。
2. `get_stock_analysis()` 在技術指標完成後，以同一個日線 DataFrame 呼叫
   `_attach_support_resistance()`；其中只執行一次 `SupportResistanceEngine.detect(data)`。
   輸入仍為 normalize_history 整理的 Open/High/Low/Close/Volume，附帶既有指標欄位。
3. `sr` 寫入 `analysis_result['support_resistance']`，格式化文字寫入
   `analysis_result['support_resistance_text']`。資料不足的 result 也有相同入口。
4. 原本這兩個欄位已存在，但 `scheduler.run_monitor_job()` 沒有讀取它們，
   所以終端與 Discord 都未顯示。
5. 現在 scheduler 在技術摘要之後、通知狀態之前印出文字，並加入 Discord
   的 `stock_lines`。一般情況直接使用已產生的文字；缺少文字時只格式化結果，
   不呼叫偵測器。active 與空結果也顯示。
6. `evaluate_notification()` 將 sr 與資料庫上次狀態傳入 RuleEngine；
   RuleEngine 在既有計分完成後，獨立呼叫 `SupportResistanceRule.evaluate()`。
7. scheduler 組裝訊息；既有 `notifier.send_stock_notifications()`、
   `send_discord_message()` 繼續處理送出與分段。

## 現有回傳格式

```python
{
    'current_price': 493.0,
    'price_basis': 'daily_close',
    'as_of': '2026-09-11T00:00:00+08:00',
    'support_zones': [{
        'type': 'support', 'low': 479.014, 'high': 485.8126210414323,
        'center': 482.41331052071615,
        'strength_score': 6.25, 'strength_label': 'strong',
        'methods': ['kmeans', 'swing_low'], 'method_count': 2,
        'touch_count': 6, 'distance_pct': -2.1474,
        'last_touch_date': '2026-09-03T00:00:00+08:00',
        'last_seen_date': '2026-09-11T00:00:00+08:00',
        # 另有既有相容別名 zone_low / zone_high / sources / strength
    }],
    'resistance_zones': [...], 'active_zones': [...],
    # 另有 nearest_*、summary_key_levels、warnings、candidates、volume_profile 等
}
```

zone 模型仍是原有 `SupportResistanceLevel` dataclass 的 dict 輸出。
事件另含 `category`、`zone_id`、`role`、原 `zone`、`price`、`as_of`、
`distance_pct`、`level`、`notify`，所有事件 `score=0`。
`RuleEngine` 額外回傳 `support_resistance_events`（顯示）、
`support_resistance_notifications`（待送）、`support_resistance_state`、
`support_resistance_notify`。`technical_notify` 保留原規則通知判斷。
`score`、`technical_score`、technical_matched_rules 與原股票方向計分不變。

## 事件條件與等級

所有參數集中於 `SupportResistanceNotificationConfig`。預設所有事件皆啟用，
通知要求原區域 `strength_score >= 6.0`，不要求特定股票或方法。

| 事件 | 明確條件 | 現有通知等級 |
| --- | --- | --- |
| NEAR_SUPPORT | 收盤在支撐 high 上方，距 high ≤ 1.5% | 一般通知 |
| NEAR_RESISTANCE | 收盤在壓力 low 下方，距 low ≤ 1.5% | 一般通知 |
| ENTER_SUPPORT_ZONE | 上次已知收盤在 high 上方，本次收盤進入原區域 | 注意通知 |
| ENTER_RESISTANCE_ZONE | 上次已知收盤在 low 下方，本次收盤進入原區域 | 注意通知 |
| SUPPORT_BREAKDOWN | 上次尚未有效跌破，本次 close < low × 0.995 | 重要通知 |
| RESISTANCE_BREAKOUT | 上次尚未有效突破，本次 close > high × 1.005 | 重要通知 |
| SUPPORT_BOUNCE | 已觀測到進入支撐，後續收盤 > high × 1.01 | 重要通知 |
| RESISTANCE_REJECTION | 已觀測到進入壓力，後續收盤 < low × 0.99 | 重要通知 |

反彈／受阻為簡化確認：要求先觀察到收盤進區，再跨出 1% 邊界；
不宣稱已完成反轉 K 線、momentum 或放量確認。只看日 high/low 不會觸發。
首次觀察區域不推測過去曾經突破或進入，首次 active zone 只顯示。
目前所有事件都採已完成的日線收盤，不以即時價觸發接近或進入事件。
本日 13:30 前的日 K 不做事件判斷、不覆蓋先前確認收盤；前一日完整日 K 可使用。
資料日期晚於現在或早於保存的觀測日期不產生新事件。

弱區事件、cooldown 中的相同接近事件、首次 active 區域與空結果只顯示，
不因支撐／壓力觸發 Discord。未啟用的事件也是只顯示；既有技術規則仍可獨立通知。
資料失敗時保留原 SR 狀態；其他技術分析、計分與通知繼續走既有規則。

## 狀態、區域配對及去重

沿用現有 `watchlist` 表，僅加 `support_resistance_state TEXT`，保存 JSON：
`zones`、上次 `price/as_of`、待送 `pending`。`create_tables()` 可重複執行 migration；
既有 `main.py` 啟動時已會呼叫，重啟服務即可建立欄位。

新舊區域上下界最大差異／新 center ≤ 0.5% 視為同一個 ID，方法列表小變動
不會產生新 ID。每個區域一對一配對。測試期間保留原邊界、角色與強度，
所以原支撐即使當天被偵測器改列壓力仍能判斷跌破。已確認突破後，
下一根已完成日 K 可以採用偵測器的新角色。未再看到的區域保留最多 7 天。

同 zone／event 成功送出後，持續接近不重送；預設 cooldown 72 小時。
事件改變、離開後重新接近或確實換區域可重新通知。突破與反彈是跨日轉移，
同一根日 K 重複輪詢不再創造轉移；也不立即追加低優先接近通知。

scheduler 在送出前保存觀測及 pending；notifier 回報成功才 acknowledge、
清除 pending 並記錄 last_sent。失敗保留相同事件 ID，程序重啟仍可重試。
pending 保留最多 7 天並附原資料日期。既有 technical signature 仍用於原通知；
SR 待送可獨立通過，單純 SR 送出不改寫原 technical signature。
這是既有 sender 成功回報基礎上的去重，遠端已收到但本機未收到成功回應等
不確定送達情況，與原 notifier 一樣可能重試。

## 修改檔案

- `app/stock.py`：共用附加結果入口，隔離整體 SR 例外，仍只算一次。
- `app/support_resistance_analysis/formatting.py`：完整區域、active、空結果文字。
- `app/rules/support_resistance_rule.py`（新增）：事件、設定、狀態轉移、訊息格式。
- `app/rules/__init__.py`、`app/rules/engine.py`：傳遞狀態、獨立通知結果與等級。
- `app/database.py`：既有表的 JSON 狀態 migration 與讀寫。
- `app/scheduler.py`：實際終端／Discord 內容、收盤確認、去重與送達確認。
- `tests/test_support_resistance_events.py`（新增）：事件及 scheduler 整合驗證。
- `tests/test_support_resistance.py`：更新 active 文案斷言。
- `tests/verify_support_resistance_runtime.py`（新增）：真實 watchlist runtime 驗證。
- `.gitignore`：排除驗證用資料庫副本及本機信任憑證 bundle。
- 本文件與 `runtime_verification/terminal.txt`、`notifications.txt`、`rules.json`。

## 實際驗證

116 項測試通過：

```powershell
.venv/Scripts/python.exe -m pytest tests -q -p no:cacheprovider --basetemp=.pytest_tmp/sr_events_03
```

除了所有既有演算法測試，新增遠近、八種事件、弱區、換區、漂移、cooldown、
重進、形成中日 K、首次 active、亂序日期、失敗隔離、migration、通知重試等。
scheduler 整合測試實際經 notifier 的 sender 介面模擬 503 → 200 → 不重送。

2026-09-12 的人工 smoke test 從既有 watchlist 取第一檔 `2408 南亞科`，
走 `run_monitor_job → analyze_watchlist → get_stock_analysis → RuleEngine → notifier`，
取得 Yahoo Finance 真實 2026-09-11 收盤資料，未替換股價或偵測器。
確認 `NEAR_SUPPORT` 進入 RuleEngine，測試接收器成功接收一則組裝訊息。
資料庫使用 SQLite backup 副本，Discord transport 使用記錄接收器，未實際發送 webhook。

本環境 curl 憑證鏈原先無法通過；驗證腳本可選擇 Windows 信任憑證，TLS 驗證保持開啟：

```powershell
.venv/Scripts/python.exe tests/verify_support_resistance_runtime.py --windows-ca
```

正常憑證環境省略 `--windows-ca`。此選項只影響測試程序，不修改正式 yfinance 設定。
人工腳本不宣稱验证 Discord 遠端接收，只驗證真實 runtime、規則及 notifier 組裝。

實際終端摘錄：

```text
2408 南亞科｜收盤價：493.0｜漲跌幅：-4.27%
【支撐 / 壓力分析】
分析基準價：493.00
・目前價格位於重要價格區：488.51～494.90
狀態：正在測試此區域
・最近支撐區：479.01～485.81
中心：482.41｜距離分析基準價：-2.15%
強度：強（6.25/10）
確認方式：K-Means、Swing Low
歷史有效測試：6 次
```

實際 notifier 訊息中的事件摘錄：

```text
【支撐 / 壓力事件】
事件：接近強支撐（NEAR_SUPPORT）
收盤價：493.00｜資料日期：2026-09-11T00:00:00+08:00
原支撐區：479.01 ～ 485.81
相對區域邊界幅度：+1.48%
強度：強（6.25/10）
確認方法：K-Means、Swing Low
說明：接近強支撐；採已完成日線收盤確認。
通知等級：一般通知
```

分析距離採原模型的 zone center；事件距離採被測試的邊界，所以兩者數字不同。
