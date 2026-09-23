# 持倉狀態

每檔 watchlist 股票都有 `position_status`：`WATCHING`（觀察中）或 `HOLDING`（已持有）。
這是使用者的持倉資料，目前不改變技術分數、決策、進出場條件或通知觸發規則。

## API 使用

沿用 `stock_code`，不是 `symbol`。以下 PowerShell 範例假設服務在本機 8000 port：

```powershell
$baseUrl = 'http://127.0.0.1:8000'

# 新增觀察股票；省略 position_status 也會使用 WATCHING
Invoke-RestMethod -Method Post -Uri "$baseUrl/watchlist" -ContentType 'application/json' -Body '{"stock_code":"2330","position_status":"WATCHING"}'

# 新增已持有股票
Invoke-RestMethod -Method Post -Uri "$baseUrl/watchlist" -ContentType 'application/json' -Body '{"stock_code":"2408","position_status":"HOLDING"}'

# 既有股票：觀察中改為已持有
Invoke-RestMethod -Method Patch -Uri "$baseUrl/watchlist/2454" -ContentType 'application/json' -Body '{"position_status":"HOLDING"}'

# 既有股票：已持有改回觀察中
Invoke-RestMethod -Method Patch -Uri "$baseUrl/watchlist/2454" -ContentType 'application/json' -Body '{"position_status":"WATCHING"}'

# 讀取狀態
Invoke-RestMethod -Uri "$baseUrl/watchlist"
```

新增已存在的股票仍回傳原有的重複股票錯誤，不會覆寫狀態，請改用 PATCH。
PATCH 可更新狀態或任一持倉欄位；不存在的股票回傳 HTTP 404，非法狀態（含 null、數字、小寫）回傳 HTTP 422。
Python 寫入函式也使用同一 Enum 驗證，非法值會拋出 ValueError。

`GET /monitor` 與 `GET /stock/{stock_code}/analysis` 都會帶入已儲存的狀態。
分析函式 `get_stock_analysis(..., position_status=...)` 接受顯式狀態，直接呼叫且未傳入時預設 WATCHING。
未追蹤股票的分析 API 也預設 WATCHING。

## SQLite 與相容性

沿用 `main.py` 原有初始化 `create_tables()`，檢查 `PRAGMA table_info(watchlist)` 後才執行：

```sql
ALTER TABLE watchlist
ADD COLUMN position_status TEXT NOT NULL DEFAULT 'WATCHING'
CHECK (position_status IN ('WATCHING', 'HOLDING'));
```

不刪除或重建 watchlist，不覆寫舊欄位。舊股票自動取得 WATCHING；重複啟動不會重設已修改的 HOLDING。
舊的記憶體字典缺少欄位或值為 None 時，讀取端視為 WATCHING。
重新啟動原服務即可自動升級，不必手動執行 SQL 或重新加入股票。

## 資料流程與顯示

`watchlist → get_all_stocks → scheduler / monitor → analyze_watchlist → get_stock_analysis → build_decision_context → DecisionEngine`

分析結果保留 `position_status`，同時放入 `decision_context` 與 `timeframe_analysis.decision_context`。
scheduler 使用共用的 `format_position_status()`，在終端與 Discord 股票價格標頭後顯示：

```text
2408 南亞科｜收盤價：525.0｜漲跌幅：+7.58%
持倉狀態：已持有
```

原先空手者／持有者操作參考維持原樣。持倉 metadata 不納入行情指紋；單純修改狀態不會強制發送 Discord。
下一次分析／終端輸出即使用新狀態，下一次符合原通知條件時 Discord 顯示新狀態。

## 驗證

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_position_status.py -q -p no:cacheprovider
.\.venv\Scripts\python.exe -m pytest tests -q -p no:cacheprovider
```

測試使用暫存 SQLite、模擬行情與攔截的通知發送器，不發送真實 Discord 通知。

## 平均成本、股數、買進日期

同一張 watchlist 新增三個可為 NULL 的欄位，沿用上述 PRAGMA 欄位檢查，缺少時才執行：

```sql
ALTER TABLE watchlist ADD COLUMN average_cost REAL;
ALTER TABLE watchlist ADD COLUMN shares INTEGER;
ALTER TABLE watchlist ADD COLUMN entry_date TEXT;
```

不刪除或重建資料表。舊 HOLDING 保留原狀態，新增欄位為 NULL，顯示「持倉資料：尚未完整設定」。
為相容上一版，新增或切換 HOLDING 時也允許暫缺資料，可之後逐欄補齊。

- HOLDING 成本必須是大於 0 的有限數值；WATCHING 可填 0。不接受數字字串或布林值。
- HOLDING 股數必須是大於 0 的整數；WATCHING 可填 0。支援 1、17 等零股，不接受字串、布林值或小數型別；上限為 SQLite 有號 64-bit 整數。
- 日期必須是嚴格的 YYYY-MM-DD 且為真實日期。
- PATCH 省略的欄位保留原值，明確傳入 null 清除該持倉欄位；空 PATCH 拒絕。
- WATCHING 的三個欄位一律清空。即使 request 帶入合法持倉資料也不儲存；非法資料仍拒絕。
- HOLDING → WATCHING 與清除欄位在同一筆 transaction 完成。

新增已持有股票（股票已存在時請使用 PATCH）：

```http
POST /watchlist
Content-Type: application/json

{"stock_code":"2408","position_status":"HOLDING","average_cost":480.5,"shares":100,"entry_date":"2026-09-10"}
```

手動更新平均成本、總股數，保留日期與狀態：

```http
PATCH /watchlist/2408
Content-Type: application/json

{"average_cost":492,"shares":150}
```

觀察股票改成已持有並一起設定完整資料：

```http
PATCH /watchlist/2454
Content-Type: application/json

{"position_status":"HOLDING","average_cost":1300,"shares":20,"entry_date":"2026-09-18"}
```

全部賣出後保留追蹤，並清空三個持倉欄位：

```http
PATCH /watchlist/2408
Content-Type: application/json

{"position_status":"WATCHING"}
```

三個欄位與 position_status 一起傳入分析結果、monitor 結果及 DecisionContext。
原有 `update_position_status()` 保留並委派給 `update_position()`，同樣遵守清空規則。

`app/position_status.py` 的 `calculate_unrealized_pnl()` 共用計算：

```text
unrealized_return_percent = (目前價格 - 平均成本) / 平均成本 × 100
unrealized_pnl = (目前價格 - 平均成本) × 股數
```

採用股票標頭的價格：有即時價則使用即時價，否則使用收盤價；數值四捨五入至小數點後兩位。
這是未扣手續費、稅費的基本損益。WATCHING 或缺少有效價格／成本時不計算，缺少股數時不計算金額。
API 透過上述兩個欄位回傳計算值，無法計算時為 null；不寫入資料庫。
formatter 以中文輸出、正負號及金額千分位顯示，金額小數尾零省略：

```text
2408 南亞科｜收盤價：525.0｜漲跌幅：+7.58%
持倉狀態：已持有
平均成本：480.5
持有股數：100 股
買進日期：2026-09-10
未實現報酬：+9.26%
未實現損益：+4,450 元
```

WATCHING 只顯示狀態；缺少持倉資料的 HOLDING 顯示未完整設定及已知欄位，不捏造數值。
成本、股數、日期不影響技術分析或決策，也不納入行情指紋，不會因修改成本就產生新行情事件。
