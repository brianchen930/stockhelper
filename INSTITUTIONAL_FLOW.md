# 法人籌碼修正層

## 架構與範圍

`stock.get_stock_analysis()` 在既有支撐偵測與 Bayesian inference 後，呼叫
`institutional_flow.integration.attach_institutional_flow()`。
Swing、Volume Profile、VWAP、Cluster、ATR、Touch、歷史反應與 Bayesian 模型均沿用。
**支撐強度與支撐守住機率不同**；本模組不修改區域位置、強度或交易規則。

現有 Bayesian 模型只替符合日線測試條件的支撐事件提供基礎機率；模型缺少或
盤中日線尚未完成時，不會由法人分數虛構基礎機率。法人背景仍可單獨顯示。
目前 scheduler 實際為每 30 分鐘呼叫 `analyze_watchlist()`，此次未修改排程。

## 檔案

新增 `app/institutional_flow/`：

| 檔案 | 責任 |
|---|---|
| `config.py` | 集中評分門檻、權重、有效天數及修正係數 |
| `provider.py` | FinMind 請求、市場辨識與股數正規化 |
| `storage.py` | SQLite 日資料 upsert、版本、下載狀態與事件快照 |
| `features.py` | 時點限制、累積量／比例、連續天數、動能與動態原因 |
| `adjustment.py` | 保留原始 posterior，額外計算修正後機率 |
| `service.py` | 日頻快取、歷史唯讀查詢、資料失敗 fallback |
| `integration.py` | 主流程接點與預測快照 |
| `presentation.py` | 最多三條原因與 debug 明細 |
| `backtest.py` | 在現有 OOS 預測上附加 A/B 比較欄位 |
| `__init__.py` | 套件入口 |

修改 `app/database.py`、`app/stock.py`、
`app/support_resistance_analysis/formatting.py`、`app/bayesian_support/presentation.py`。
測試新增 `tests/test_institutional_flow.py`；`tests/conftest.py` 隔離預設法人服務，
避免其他股票單元測試發網路請求或寫入實際資料庫；法人整合測試注入暫存服務。

## 資料來源與每日取得

使用 [FinMind 官方籌碼 API 文件](https://finmind.github.io/tutor/TaiwanMarket/Chip/)
所列 `TaiwanStockInstitutionalInvestorsBuySell`，分母由同來源
`TaiwanStockPrice.Trading_Volume` 取得。使用 requests，不需要安裝 FinMind SDK。
可用環境變數 `FINMIND_TOKEN` 傳入 API token；金鑰不寫進資料庫或日誌。
來源權限、配額或連線錯誤均走 fallback。

`.TW` 與 `.TWO` 分別保留為 TWSE、TPEx；裸代號沿用 Yahoo resolver。
FinMind 此 endpoint 同時提供兩市場資料，查詢參數使用裸 `stock_id`，不是把上櫃導到上市專用 API。
新增 resolver 對既有 `.TW/.TWO` 後綴的直接支援。

所有量統一為「股」。外資合計包含 `Foreign_Investor + Foreign_Dealer_Self`；
投信獨立；自營商合計包含 `Dealer + Dealer_self + Dealer_Hedging`（依來源的新舊制）。
缺必要類別或完整日成交量的日期不納入；真正的零買賣保留。
原始正規化函式也支援明示 `unit='lots'`，乘 1000 後才計算比例。
多日比例為 **期間淨股數合計 / 同期間成交股數合計**，不是每日比例相加。
資料缺日會中斷連續天數及窗口，不將缺漏日補零。

每股票每台北曆日最多嘗試一次，自動取得前 45 個曆日至昨天；
成功與失敗嘗試狀態均持久化，重啟或每半小時監控不重抓。
來源失敗但有未過期快取時可沿用；沒有可用資料／超過 10 曆日則 UNKNOWN。
日期窗口及過期政策是保守預設，長假可能退回 UNKNOWN。

## 時間與 look-ahead bias

所有時點轉為 Asia/Taipei；無時區時間解讀為台北時間。
規則同時檢查 `date < as_of.date()` 與 `available_at <= as_of`，當日資料一律隔日使用。
2026-09-15 11:00 不得使用 2026-09-15 日資料，即使資料已被匯入資料庫。

來源不保證歷史首次公開版本，因此額外保留每次內容變化的 `observed_at` 與完整 payload：

- **strict=True（預設）**：只能使用該時點前實際取得的版本。今日補抓的歷史資料不能倒灌昨日預測。
- **strict=False（研究選項）**：允許隔日可用假設的修訂歷史，標記
  `next_day_assumption_revised_history`。它不能宣稱是嚴格 point-in-time 回測。
- 歷史服務請求不下载；`replay=True` 不刷新來源、不寫事件。
- 每次版本內容變化另存新版本，`institutional_flow` 本身仍以 symbol/date upsert，不重複新增。

## 分數與機率

外資、投信各自評分，每項預設一分：

1. 同向連續至少 3 日：依方向 +1 / -1。
2. 5 日（不足時用完整 3 日）累积淨額／量至少 3%：依方向 +1 / -1。
3. 單日淨額／量至少 5%：依方向 +1 / -1。
4. 近 3 日賣壓逐步減弱 +1，逐步加速 -1。

外資、投信權重獨立，持續同向加共識分；最後限制於 [-6,+6]。
相反方向標記 MIXED，修正信心乘 0.35，不把兩者抵銷解讀成中性。
一般門檻為 >=4 強力支撐、>=2 偏多、<=-4 強力壓力、<=-2 偏空，其餘中性。
自營商特徵保存，初期不額外加入分數，以免與其他法人重複計權。

修正公式：

```text
adjusted = logistic(logit(base) + 0.12 × score × confidence)
```

此為 **provisional_log_odds_v1 / uncalibrated**，並非已訓練的新 Bayesian 特徵，
也不是把分數直接加到百分比。原始 `result`、`display` 與 posterior 不覆蓋。
輸出另保留 base_support_probability、institutional_score、institutional_level、
adjusted_support_probability、adjusted_support_level。
五級沿用該預測原本的 rating thresholds；等級尚不代表已校準的絕對成功率。

同樣買超訊號對壓力代表較可能突破。`adjust_probability(..., side='resistance')`
對「壓力守住機率」採反向 log-odds；目前沒有壓力基礎機率模型，因此僅附加方向解讀。
UNKNOWN 原樣回傳 base，與 NEUTRAL（已知資料沒有方向）分開。

## 儲存與回測

啟動時建立四表，不需破壞既有 watchlist：

- `institutional_flow`：完整法人買／賣／淨額、日量、比例、市場、來源、可用時間、建立／更新時間；UNIQUE(symbol,date)。
- `institutional_flow_versions`：實際觀測時間與版本 payload。
- `institutional_fetch_state`：每日嘗試日期。
- `institutional_support_events`：symbol、預測 timestamp、支撐上下界、base／adjusted、score／level、完整特徵、模型結果、設定快照；未結案 outcome 為 NULL。

用 `FlowStore.resolve_event(id, 'HOLD' 或 'BREAK', available_at)` 在結果成熟後記錄。
不自動把尚未結案的事件當失敗，也不將結果欄位回灌特徵。
快照保留原模型資料日期；若現在針對舊日線事件重新解讀，預測時間是現在，不是假造的歷史時間。

```python
from app.institutional_flow.storage import FlowStore
from app.institutional_flow.backtest import attach_institutional_backtest

# predictions 為既有 backtest_bayesian_support 的 walk-forward 輸出。
comparison = attach_institutional_backtest(predictions, FlowStore(), strict=True)
comparison.to_json('institutional_comparison.json', orient='records', force_ascii=False)
```

保留 actual_label、label_available_date 與原始預測，後續可比較 Accuracy、Precision、
Recall、F1、Brier Score、Calibration。這次不聲稱加入法人已改善預測能力。
需回測校準：各窗口門檻、外資／投信權重、共識分、MIXED 信心、log-odds 係數、
五級分界、過期天數，以及法人與原始特徵的相依性。

## 驗證

在 `台股` 目錄執行（basetemp 請指定新的專案內目錄）：

```powershell
.\.venv\Scripts\python.exe -m pytest tests -q -p no:cacheprovider --basetemp=.pytest_tmp/flow_validation_new
```

覆蓋指定七種案例，以及單位、類別缺漏、缺日、版本修訂、日頻快取、過期、
輸出精簡／debug、原始結果不變、事件保存、時區、API 請求契約與回測資料可用性。
本次最終驗證：完整測試 376 passed（法人專項 18 項），另有既有 Starlette/httpx 棄用提醒。
實際外連驗證遇到本機 Python CA 信任鏈錯誤；應配置可信 CA（例如 REQUESTS_CA_BUNDLE），
保留 HTTPS 憑證驗證。離線 API 契約測試不等同於真實資料下載成功。
