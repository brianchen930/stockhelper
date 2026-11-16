# 第三階段：獨立歷史回測

在 `台股` 目錄執行；沿用原助手的 `--symbol`、`--start`、`--end`，日期含首尾日。
本模組不匯入 `app`、不啟動 API／排程、不讀寫正式持倉或資料庫。

```powershell
python -B -X utf8 -m 666_strategy backtest --symbol 2330 --start 2026-09-17 --end 2026-10-29 --output 666_strategy/backtest_results/2330.json
```

也可使用 `python -B -X utf8 -m 666_strategy.backtest`，參數相同。
`--source yahoo` 為預設；`--source fugle` 選用獨立 Fugle Provider。兩種來源均使用原有策略、
多頭過濾與下一根 K 棒 Open 成交規則。Fugle 目前明確支援四位數 TWSE 上市股票（裸代號或 `.TW`），
未驗證的 `.TWO`／其他標的不會自動轉查 Yahoo。
代號接受 `2330`、`2330.TW`、`6488.TWO`；裸代號依原回放方式先查 `.TW`，完全沒有資料才查 `.TWO`。
部分缺資料不會被誤當成另一市場，也不會改變使用者要求的日期。

本機 HTTPS 需要先前匯出的信任憑證時，加上：

```powershell
--ca-bundle 666_strategy/.cache/windows_current_roots.pem
```

TLS 驗證維持開啟。CLI 才設定自己的 yfinance 快取位置，程式庫匯入不改全域設定。

## 可重現的實際行情範例

以下使用先前保存、含 Yahoo 原始 60 分 K 與股票日線的快照，沒有重新下載或補造價格：

```powershell
python -B -X utf8 -m 666_strategy backtest --symbol 2330 --start 2026-09-17 --end 2026-10-29 --source-snapshot 666_strategy/verification/daily_live_20261029.json --output 666_strategy/backtest_results/2330.json
python -B -X utf8 -m 666_strategy backtest --symbol 2408 --start 2026-09-17 --end 2026-10-29 --source-snapshot 666_strategy/verification/daily_live_20261029.json --output 666_strategy/backtest_results/2408.json
```

快照模式一樣檢查暖身、所有預期棒、日線、日曆及快照 as_of；不能用快照回測它尚未涵蓋的區間。
這份來源只保存較近期的 60 分 K，不代表任意開始日都能離線回測。

### Fugle 完整一年快照

已保存的實際 Fugle 完整驗證資料，可用以下離線流程重新驗證、建立可重現快照、
重跑兩檔一年與短區間回測，並核對逐筆帳務、訊號、績效、截斷重跑及收盤集合競價證據：

```powershell
python -B -X utf8 -m 666_strategy.verification.verify_fugle_integration
```

單獨重現指定一年：

```powershell
python -B -X utf8 -m 666_strategy backtest --source fugle --symbol 2330 --start 2025-10-30 --end 2026-10-29 --source-snapshot 666_strategy/verification/fugle_history_20251030_20261029.snapshot.json --output 666_strategy/backtest_results/fugle_2330.json
python -B -X utf8 -m 666_strategy backtest --source fugle --symbol 2408 --start 2025-10-30 --end 2026-10-29 --source-snapshot 666_strategy/verification/fugle_history_20251030_20261029.snapshot.json --output 666_strategy/backtest_results/fugle_2408.json
```

省略 `--source-snapshot` 則直接向 Fugle 查詢；沿用環境變數優先、`台股/.env` 備援的
`FUGLE_API_KEY` 管理，僅建立 HTTP 行情連線。每次請求區間最多 180 個日曆日，
TLS 與金鑰遮蔽保留；任何必要資料缺漏立即停止，不自動換來源或縮短日期。
離線快照不讀取金鑰、不發網路請求，驗證內容 SHA-256 並保留原始實測檔 SHA-256。
Yahoo 的 730 日預檢只適用 Yahoo；Fugle 依其 API 範圍及實際資料完整性判定。

Fugle 分 K 成交量由張轉股一次，日 K 已是股；保存原始成交量與單位。
60 分 K 直接使用 09:00、10:00、11:00、12:00、13:00 五根，最後一根於 13:30 完成。
保存的八組實際 1 分 K 樣本已核對，13:00 原生 60 分 K 包含 13:30 收盤撮合；
不再附加 13:30 棒、不二次合併集合競價、不修改 Close 配合 Yahoo。
日線總量涵蓋其他交易類別，不以分 K 總量強制配平。

完整結果與逐筆交易見 [Fugle 整合驗證報告](verification/FUGLE_INTEGRATION_REPORT.md)。

線上另有成功驗證的明確區間：

```powershell
python -B -X utf8 -m 666_strategy backtest --symbol 2408 --start 2026-09-17 --end 2026-10-22 --ca-bundle 666_strategy/.cache/windows_current_roots.pem --output 666_strategy/backtest_results/2408_live.json
```

線上資料可能修訂或缺漏。本次另一個指定結束日 10/29 的線上請求因缺少當日 `^TWII` 日期參考而失敗，
程式未產生部分回測，也未自動換成 10/22。上面 10/22 是另外明確指定的驗證案例。

## 沿用策略及成交順序

原 `signals.generate_signals()`、`daily` 多頭三條件與 `decisions.PositionState`／
`evaluate_decision()`／`confirm_execution()` 直接沿用，核心檔案沒有修改。

- 初始空手；只有指定期間內新的有效 BUY 且日線 BULL 才可建倉。
- 持倉時 SELL 平倉；完成日K變成 NON_BULL 依原規則產生 EXIT。
- BUY／SELL 於當根完成時確認，**只在下一根實際交易時段 K 棒的 Open 成交**。
  09:00 棒於 10:00 確認後可在 10:00 棒的 Open 成交；雖時間相同，棒索引不同，絕不使用訊號棒 Open。
- 13:00～13:30 截短棒維持原權重；13:30 訊號最早下一確認交易日 09:00 成交，跨週末與休市。
- 每根棒先處理 Open（只使用 Open 與當時完成資料），再到 bar_end 處理 Close 及訊號。
  日線在 13:30 才可用，盤中與次日開盤使用之前完成的日K。
- 買進成交前再呼叫既有決策層重查；失效／UNKNOWN 會取消待 BUY，多頭恢復也不能復活舊訊號。
- SELL／EXIT 共用既有單一 pending 決策，不重複執行。沒有穿越的日線失效也會被處理。
- 暖身期間不建倉；開始前已在均線上方不會憑空買進。
- 期末不假賣出；持倉、未成交 BUY／SELL／EXIT 分別保留在結果中，區間外不成交。

## 資料完整性與 Yahoo 範圍

第一個交易日前取得 120 個完成日K及 60 根完成 60 分 K。60 根包括 12 個正常交易日的五根棒，
讓第一根回測棒能比較自己與前一棒各自有效的 MA60。日線／60 分 K 各算自己的均線。

回測採嚴格資料政策：任何必要的暖身或指定區間棒缺漏、OHLCV 無效、時區／重複標籤問題、
日曆不足、日線 UNKNOWN，均明確報錯並停止，不縮短、不補值、不跳過。
策略函式本身的 UNKNOWN 契約不變：禁止 BUY、不因 UNKNOWN 直接強制 EXIT。
回測無法因缺資料可靠計算績效時，選擇報錯，而不是把未能判斷的期間當成沒有交易。

線上請求先用目前 yfinance 對 `60m/1h` 的 730 日實作界線作預檢，**暖身也計入**。
這不是 Yahoo 可用資料保證；最終仍逐一檢查實際回傳的完整時間格，更短的服務端限制一樣報錯。
參考：[yfinance history 原始碼](https://github.com/ranaroussi/yfinance/blob/main/yfinance/scrapers/history.py)。
來源快照不受今天的線上保留時間限制，但不能超出保存資料與 as_of。

預設內建 2025、2026 年日曆，含已確認的 2026/7/10、10/23 休市與既有排除零量異常日線規則。
未知年份不推算平日當交易日。需要其他年份時可提供已核對的日曆：

```powershell
--calendar verified_sessions.json
```

檔案格式是 `SessionCalendar` 欄位：

```json
{
  "sessions": ["完整且已核對、排序不重複的 YYYY-MM-DD 交易日清單"],
  "start_date": "YYYY-MM-DD",
  "through_date": "YYYY-MM-DD",
  "source": "核對依據"
}
```

以上是欄位示意，日期須換成真實值；清單與 coverage 必須涵蓋暖身及完整回測期間。
提供此檔案代表呼叫端已確認正常及臨時休市，不由行情日期自動建立日曆。
預設日曆模式另保留原 `^TWII` 日期核對；大盤方向不參與策略過濾。

## 資金與成本

原台股助手 `backtest.py`／`app/backtest/runner.py` 只做決策回放，不模擬成交、資金及績效，
沒有可直接沿用的費率或本金。第三階段採下列**可調模擬假設**，不宣稱是特定券商實收：

| 參數 | 預設 |
|---|---:|
| `--initial-cash` | 1000000 元 |
| `--commission-rate` | 0.001425，買賣各收 |
| `--minimum-commission` | 20 元／筆 |
| `--sell-tax-rate` | 0.003，僅賣出 |
| `--share-unit` | 1 股；1000 表示整張單位 |

單一股票每次買入可負擔的最大整數股單位，包含買進手續費；全數平倉、不加碼、不借款。
金額以 Decimal 計算，總價、手續費、稅各自四捨五入到 0.01 元。
資金不足買一個單位時記錄 `INSUFFICIENT_CASH`，不建立持倉，也不重用該 BUY。
不同股票各自獨立使用本金，沒有合併投資組合。

例如整張與不同本金：

```powershell
python -B -X utf8 -m 666_strategy backtest --symbol 2330 --start 2026-09-17 --end 2026-10-29 --initial-cash 5000000 --share-unit 1000 --source-snapshot 666_strategy/verification/daily_live_20261029.json --output 666_strategy/backtest_results/2330_lots.json
```

未模擬滑價、買賣價差、零股與整張撮合差異、成交量限制或無法成交情形。
每股使用同一原始 60 分 K Open 作理想化成交；未調整價格未自動補入股利、分割或除權息現金流。
ETF 等商品如需不同稅率，請明確覆寫，不自動辨識。

## 輸出及績效定義

`--output result.json` 會輸出：

- `result.json`：完整設定、資料品質、原始訊號、決策事件、成交、已平倉交易、期末持倉、pending 與統計。
- `result.fills.csv`：每筆 BUY／SELL／EXIT 的訊號時間、成交棒／時間、價格、股數、總額、費用、稅與現金。
- `result.trades.csv`：每次已平倉交易的買賣時間／價格、股數、雙邊費用、稅、淨損益及報酬率。

`--output result.csv` 以該 CSV 保存已平倉交易，並保留完整 JSON 與 fills CSV。
沒有交易也會輸出欄位標頭，不以空白檔冒充成功。錯誤時回傳非零，驗證成功前不輸出回測結果。

淨損益 = 賣出總額 − 賣出手續費 − 交易稅 − 買進總額 − 買進手續費。
單筆報酬率以含買進手續費的建倉成本為分母。勝率只計已平倉交易，淨利 > 0 為勝；無已平倉交易為 null。
`realized_net_profit` 為已平倉淨損益；`unrealized_net_profit` 為期末 60 分 K Close 市值減建倉成本，
已含買進費但不扣假想賣出費稅。總損益／總報酬率以期末「現金＋持倉市值」對初始本金計算。

另提供 `max_drawdown_percent`、`average_holding_days`、`buy_and_hold_return_percent`：

- 最大回撤為正數百分比，以初始本金及每根 60 分 K Open 成交後、已完成 Close 的權益樣本，
  計算歷史高點至後續低點跌幅。`equity_curve` 保留全部樣本；不推測棒內 High／Low 的路徑。
- 平均持倉天數只計已平倉交易，以買賣實際時間差／24 小時計算，含週末、休市及小數日；無平倉為 null。
  未平倉天數另列於 `open_position.holding_days`。
- 買進持有基準於第一個區間內 60 分 K Open 使用同本金、股數單位及買進費買入，
  最後一根 Close 估值；無假想期末賣出費稅。另列 `buy_and_hold_price_return_percent` 作未扣費價格報酬參考。
  完整基準在 `benchmark`，此統計不改變策略的 BUY／SELL 或 EXIT。

## 一年區間與資料查核

目標完整期間是 `2025-10-30`～`2026-10-29`，需前置日線自 `2025-05-08` 起、60 分 K 自 `2025-10-13 09:00` 起。
共 241 個交易日、1,205 根期間內棒及 60 根暖身棒。
實際 Yahoo 回應在兩檔各缺少 2026/2/2、4/20 的十根 60 分 K，另缺 2025/8/1 暖身日線；
單日重試仍無資料。因此目前程式會拒絕執行這個不完整區間，不會輸出一年交易或績效。

可重現資料拒絕（不是成功回測指令）：

```powershell
python -B -X utf8 -m 666_strategy backtest --symbol 2330 --start 2025-10-30 --end 2026-10-29 --source-snapshot 666_strategy/verification/annual_source_20251030_20261029.json --output 666_strategy/backtest_results/annual_2330.json
```

將股票改成 2408 同樣會明確報 `INCOMPLETE_60M`。補齊可信且通過驗證的實際資料後，沿用相同日期、機制及成本設定。
不因補上統計而繞過完整性檢查；資料缺口與替代來源操作方案見
[一年回測資料報告](verification/ANNUAL_BACKTEST_REPORT.md)。

## 純程式介面與驗證

```python
import importlib

bt = importlib.import_module('666_strategy.backtest')
provider = bt.SnapshotProvider('666_strategy/verification/daily_live_20261029.json')
data = bt.fetch_backtest_data('2408', '2026-09-17', '2026-10-29', provider=provider)
result = bt.run_backtest(data, config=bt.BacktestConfig())
print(result.summary)
bt.export_backtest(result, '666_strategy/backtest_results/2408.json')
```

也可用 `prepare_data(raw_60m, raw_daily, symbol=..., start=..., end=..., calendar=...)` 驗證外部資料後純計算。
`run_backtest()` 不下載、不寫檔、不改輸入；輸出由 `export_backtest()` 明確執行。

```powershell
python -B -X utf8 -m unittest discover -s 666_strategy/tests -v
```

實際結果、獨立成交／帳務核對、逐交易日前綴檢查及修改檔案見
[第三階段驗證報告](verification/BACKTEST_REPORT.md)。
