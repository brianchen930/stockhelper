# 歷史決策回放 v1

在 `台股` 目錄執行（使用既有 Python 環境與 requirements.txt）：

```powershell
python backtest.py --symbol 2408 --start 2025-01-01 --end 2026-09-30
python backtest.py --symbol 2408 --start 2025-01-01 --end 2026-09-30 --position-status HOLDING --output backtest_results/2408_holding.json
```

Windows 既有虛擬環境也可直接使用 `.venv/Scripts/python.exe backtest.py ...`。
預設輸出 `backtest_results/2408_2025-01-01_2026-09-30.csv`。CSV 使用 UTF-8 BOM，方便 Excel 閱讀；巢狀欄位以 JSON 儲存。

## 判斷語意

- 每個股票交易日收盤 13:30（Asia/Taipei）回放一次，開始、結束日期均包含。休市或停牌無日線資料時不補值、不增加確認次數。
- 固定 `WATCHING`（預設，觀察中）或 `HOLDING`（假設已持有）情境；決策不視為成交，不自動變更持倉。要檢查減碼／出清請使用 `--position-status HOLDING`。
- 現行正式 DecisionEngine 的 WATCHING 動作是 OBSERVE；進場資格由獨立的 `entry_action` 與 `decision_snapshot.entry_paths` 記錄。目前核心不產生 ENTER／ADD，回測也不另造建倉／加碼訊號。保留正式的 `CONSIDER_REDUCE`（考慮減碼），不將它冒充為已減碼。EXIT 在 CSV 顯示「出清」。
- 不處理資金、持倉比例、成交、加減碼幅度或績效。
- 每次執行從空決策狀態開始。首日之前的六個月資料只用來暖機指標；不預先累計 confirmation。每日分析視窗同正式 `get_stock_analysis` 預設六個月，起始日會影響之後的狀態序列。
- 每日一次的確認次數是「收盤觀察次數」，不是重現正式盤中多次輪詢的通知紀錄。

## 共用核心與隔離

```text
get_stock_analysis（即時下載） ─┐
                              ├─ analyze_stock_history
BacktestRunner（歷史切片） ────┘       ├─ DecisionEngine / decision_state.update_monitor_decision
                                     └─ evaluate_notification / RuleEngine
```

`app/stock.py` 的指標、趨勢、支撐壓力、法人整合、DecisionContext 建構共用同一實作。
回測直接呼叫正式 `update_monitor_decision`，保留 previous_action_state、entry/holder confirmation、entry path memory、holder structure memory、trade memory、signal persistence 與 hysteresis。

決策狀態使用每次執行專用的 SQLite `:memory:`；支撐壓力生命週期使用專用暫存 SQLite，結束自動清除。RuleEngine 的支撐壓力事件狀態也在回放記憶體中延續，不發送通知。每檔獨立、每次重新執行重置，正式 `data/stocks.db` 不遷移、不更新。

## 時間邊界與資料限制

- 下載非股息調整日線（`auto_adjust=False`），逐日先切片再計算指標、swing、支撐壓力與決策；不在完整期間預先計算結果再取當日。
- 大盤只使用當日及之前資料。缺少當日大盤資料時，相對大盤標示「資料不足」，不沿用別日漲跌幅。缺少股票收盤價時明確失敗，不拿前一日冒充。
- 法人沿用既有 `InstitutionalFlowService` 與 `FlowStore.history(strict=True)`，正式資料庫以 `mode=ro` 讀取。資料日期、`available_at` 及版本 `observed_at` 必須均不晚於回放時刻；不下載今日資料回填歷史，不使用後來修訂版本。無歷史快照時標示 UNKNOWN。當日收盤後才公布的籌碼不會進入當日決策。
- 目前 Bayesian 模型檔沒有可驗證的歷史可用版本，v1 回放將其標示 `unavailable_in_replay`，不使用今日模型或重新訓練。其餘規則照正式資料不足路徑執行。
- Yahoo 回傳的是目前保存的歷史價格，可能含事後修訂或拆股調整；v1 保證計算與狀態不讀未來日期，但不宣稱供應商資料是當時原始版本。若需要原始版本稽核，可從 API 注入自存日線。

## 輸出

主要欄位：`date, close, signal, action, short_trend, mid_trend, reason`。
另包含 RSI、MACD／KD 狀態、支撐／壓力與結構支撐狀態、相對大盤、法人 context、RuleEngine 命中訊息、DecisionEngine 規則代碼、進場／持有狀態、確認次數與防抖資訊。

`action_changed` 與 `action_change_reason` 比較相鄰回放交易日；原因來自正式引擎的貢獻分數、限制、恢復確認及原因文字，不另做買賣判斷。首日標示建立基準；動作未變時明確記錄。`decision_snapshot` 保留正式引擎完整輸出及轉換證據，`state_before`／`state_after` 可查每次狀態記憶。

程式使用範例（DataFrame 為日期 index，欄位 Open/High/Low/Close/Volume）：

```python
from app.backtest.runner import BacktestRunner, export_rows

runner = BacktestRunner()
rows = runner.run('2408', '2025-01-01', '2026-09-30',
                  history=stock_history, benchmark_history=twii_history,
                  position_status='HOLDING')
export_rows(rows, 'backtest_results/2408.json')
```

注入上櫃資料時請使用例如 `3211.TWO`；若不提供 DataFrame，下載器會在指定歷史期間依序嘗試 `.TW`、`.TWO`，不查今日行情來辨識。

驗證：

```powershell
python -m pytest tests/test_decision_backtest.py -q
```
