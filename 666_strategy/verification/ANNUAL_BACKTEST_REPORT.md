# 一年回測資料完整性與績效擴展（2026-10-30）

目標固定為 **2025-10-30～2026-10-29**，沒有縮短、日K替代、補造價格或策略最佳化。
已補 2025 日曆與三項描述性績效指標，但 **2330.TW、2408.TW 的一年回測均因真實來源缺口而停止**。
逐筆一年交易、勝率、損益、報酬率、回撤、平均持倉天數、買進持有報酬均保留 N/A（JSON null），不是 0。

## 實際 Yahoo 來源與必要暖身

本次實際下載原 Yahoo `Ticker.history()`，保留既有 `auto_adjust=False`、`repair=False`、`keepna=True`，TLS 驗證開啟。
沒有改換歷史價口徑或填補價。原始回應保存在 [annual_source_20251030_20261029.json](annual_source_20251030_20261029.json)。

| 請求 | 原始列數／檔 | 原始回傳首尾 |
|---|---:|---|
| 2330、2408 的 60m | 各 1,300 | 2025-10-01 09:00～2026-10-29 13:00 |
| 2330、2408 的 1d | 各 407 | 2025-03-03～2026-10-29 |
| 大盤日期參考 ^TWII 1d | 407 | 2025-03-03～2026-10-29 |

回測日曆確認 241 個交易日。暖身取回測前 120 個交易日日K，以及 12 個交易日共 60 根 60 分 K：

- 日線必須自 **2025-05-08** 起；含回測期間共 361 個日線交易日格。
- 60 分 K 必須自 **2025-10-13 09:00** 起；含期間 1,205 根與暖身 60 根，共 1,265 根。
- 區間首尾有資料只代表涵蓋到端點，不能當作中間完整；檢查逐根 canonical grid 與 OHLCV。

目前一年加暖身在程式既有 730 日預檢界線內。此次問題是區間內資料空洞，不是以保存期限為由直接拒絕一年。

## 確切缺口

| 股票 | 期間應有60m棒 | 有效期間棒 | 缺價格棒 | 暖身60m | 必要日線 |
|---|---:|---:|---:|---:|---:|
| 2330.TW | 1,205 | 1,195 | 10 | 60／60 有效 | 360／361 有效 |
| 2408.TW | 1,205 | 1,195 | 10 | 60／60 有效 | 360／361 有效 |

兩檔缺口相同：

1. **2026-02-02、2026-04-20**：每天 09:00、10:00、11:00、12:00、13:00 五根，全為 OHLC 空值、Volume=0。
   時間標籤存在，但沒有可用價格；這些日期在已確認日曆為開市日，股票日線與大盤參考也有有效行情，不能當休市排除。
2. **2025-08-01**：兩檔股票日線缺漏；該日也是日曆確認交易日，大盤日線參考存在。
   這個缺口在起點前必要 120 日暖身中，導致 2025-10-30 09:00 多頭為 UNKNOWN。
   本份來源直到 2026-01-26 13:30 才第一次恢復可判斷的完整日線視窗；不能跳過起點前段來換取績效。

最新 HistoryResult.status 仍可為 OK，因它只描述最近視窗；回測另外檢查整個必要區間，正確拒絕早期缺洞。
原多頭與訊號規則要求相鄰有效窗，缺棒會影響後續 MA60 與穿越；不能只刪兩天後繼續算。

詳細證據：[可用性 JSON](annual_availability_20261030.json)、[20 筆無效60m棒 CSV](annual_gaps_20261030.csv)。
JSON 同時列每日線缺口、首日 UNKNOWN、逐月棒數、原異常代碼及完整要求區間。

## 單日重試

對每檔股票、每個缺分鐘日期分別查詢 `60m` 與 Yahoo 等價 `1h`，共 **8 次**，全回報沒有價格資料。
另外逐檔重查 2025-08-01 日線，共 **2 次**，也沒有資料。
這 10 次來源回應保存在 [annual_gap_probes_20261030.json](annual_gap_probes_20261030.json)。
Yahoo 的 `YFPricesMissingError` 包含通用「possibly delisted」文字；本次沒有據此認定股票下市。
重試結果未拼回來源，也沒有日K價格拿來充當60m成交。

## 交易日曆補齊及交叉驗證

新增 `calendars/2025.json`，依交易所官方 2025 修正版表建立，保留農曆年前僅交割不交易的 1/23、1/24，
並納入修正後 9/29、10/24、12/25。2/8 補班不交易。
主要依據：[TPEx 2025 修正版股票市場日期表](https://www.tpex.org.tw/storage/eb_data/11407/114030173911-1.pdf)，
另以 [TAIFEX 2025 修正版日期表](https://www.taifex.com.tw/file/taifex/CHINESE/11/attach/%E5%8F%B0%E6%9C%9F%E4%BA%A4%E5%AD%97%E7%AC%AC1140001929%E8%99%9F%E5%87%BD.pdf) 交叉查核國定休市。

2026 日曆與先前使用者確認的 7/10、10/23 颱風休市原樣保留，沒有為了避開缺洞新增假休市。
整段日線暖身加回測的 **361 個完成交易日**以有效 ^TWII Close 核對通過；
10/23 的已知休市 placeholder 依既有規則排除，大盤方向不參與買進過濾。

查核發現 TWSE holidaySchedule 端點對 `queryYear=114` 或 `2025` 仍回傳 `queryYear=2026` 及 2026 表。
這些回應保存為拒用證據，未把錯年 API 回應拿來驗證 2025。日曆不是從股票缺價日期反推。

## 績效指標已完成的程式擴展

只增加描述性統計與權益樣本，不改 BUY、SELL、日線條件、持倉決策或下一棒 Open 成交。

- `max_drawdown_percent`：正數百分比；初始本金為第一個高水位，再看每根 Open 成交後及完成 Close 的權益。
  `equity_curve` 保存現金、持股、估值及時點。不推測棒內 High／Low 路徑，故不是逐筆行情最大回撤。
- `average_holding_days`：已平倉買賣實際時間差／24 小時之平均，含夜間、休市及小數日；無平倉為 null。
  未平倉天數另列，不偽造期末平倉。
- `buy_and_hold_return_percent`：同本金、股數單位與買進費，第一根區間內60m Open 買入、最後60m Close估值。
  不扣假想賣出費稅；另提供未扣費的價格報酬 `buy_and_hold_price_return_percent`。

這些欄位已對完整的既有短區間與測試資料驗證；**沒有用短區間數字冒充本次一年結果**。

## 替代真實資料來源及可行方案

以下只提出可執行方案，未購買、登入或自行切換來源，也不保證供應商這幾日一定完整；需實際取樣驗證。

| 方案 | 可用能力與限制 | 適用方式 |
|---|---|---|
| 永豐 Shioaji | 官方提供歷史 Ticks／Kbars，Kbars 單次最多30日；需要相應帳戶與行情權限，受流量限制 | 按不超過30日分段取得真實分K／逐筆，先驗證兩個缺日，再取得全區間及暖身 |
| FinMind TaiwanStockKBar | 官方列為 Sponsor 分鐘K資料，逐日查詢；整日批次另有權限層級，資料也有已公告缺漏 | 先取2330、2408兩缺日及重疊日，通過後取得全區間；日線可使用其日價或交易所正式成交資訊 |
| 等 Yahoo 補資料／供應商實際匯出60m | 既有 provider 及回測可保持原樣，但修復時間無法保證 | 重新查詢後逐根比對；沒有完整資料之前繼續拒絕績效 |

依據：[Shioaji 官方歷史行情](https://sinotrade.github.io/zh/tutor/market_data/historical/)、
[FinMind 官方技術面資料](https://finmind.github.io/tutor/TaiwanMarket/Technical/)、
[FinMind 官方資料集與已知缺漏說明](https://github.com/FinMind/FinMind-MCP/blob/master/knowledge/datasets.md)。

優先方案是**使用同一可信分鐘來源重建完整一年60m**，維持日線獨立計算；比無聲拼接兩家價格更容易稽核。
建議操作次序：

1. 先取得兩檔 2026-02-02、04-20 的真實分鐘／逐筆，以及兩檔 2025-08-01 的真實日線，確認來源確有資料。
2. 抽查至少前後相鄰交易日，核對未調整價格、商品代碼、時區、分鐘標籤是開始或結束、成交量單位，
   並確認13:30收盤撮合與盤後成交如何標記。
3. 用真實分鐘／逐筆彙整為既有 09、10、11、12、13 五根，13點尾棒維持13:30完成及等權重；
   排除非一般盤，正確納入收盤撮合，不直接用不明邊界的 resample，也不補造無成交價格。
4. 取得 2025-10-13～2026-10-29 全部分鐘資料及 2025-05-08 起日線，保留來源、原始檔與 SHA256。
   若日後選擇只補兩日，也須明確標示每根來源、重疊比對與差異，不由本程式自行混用。
5. 通過同一完整性檢查：1,265 根有效60m、361 個有效日K、交易日曆核對、相鄰訊號及時間前綴測試，
   再原日期執行2330、2408一年回測，輸出逐筆交易與全部指標。

這不需要策略最佳化或改用日K訊號；只需可信原始分鐘資料與對應資料轉換／供應商介面。
目前沒有可用的上述帳戶授權或實際補缺檔，因此未宣稱替代資料已解決缺洞。

## 測試與回歸

- **186 項測試全部通過**，既有172項保留，新增14項；見 [測試結果](annual_tests_20261030.json)。
- 新增回撤、費用造成的初始回撤、持倉日數、開放持倉不混入平均、同費用買進持有、Open估值不讀未來Close、
  2025修正版日曆、固定一年與暖身日期、實際來源內部空洞及明確拒絕的測試。
- 原三個真實短區間案例的所有既有結果欄位保持一致，另獨立核對新指標；
  原始訊號、13筆成交與55個交易日前綴回歸證據見 [回歸 JSON](annual_regression_20261030.json)。
- 101 個受保護檔案 SHA256 不變，包括原助手、signals.py、decisions.py、history.py、daily.py、provider.py、calendar.py 與2026日曆。

## 操作與檔案

重現現有資料的完整區間拒絕（預期非零退出，不產生一年績效）：

```powershell
python -B -X utf8 -m 666_strategy backtest --symbol 2330 --start 2025-10-30 --end 2026-10-29 --source-snapshot 666_strategy/verification/annual_source_20251030_20261029.json --output 666_strategy/backtest_results/annual_2330.json
```

2408 同樣參數、改代號即可。重現缺口與測試：

```powershell
python -B -X utf8 666_strategy/verification/audit_annual_history.py 666_strategy/verification/annual_source_20251030_20261029.json --output 666_strategy/verification/annual_availability_replayed.json --gaps-csv 666_strategy/verification/annual_gaps_replayed.csv
python -B -X utf8 -m unittest discover -s 666_strategy/tests -v
```

新增：`calendars/2025.json`、`backtest/metrics.py`、`tests/test_backtest_metrics.py`、
`verification/download_annual_history.py`、`verification/audit_annual_history.py`、本報告及六份annual來源／缺口／測試／回歸產物。
修改：`backtest/runner.py`（只附加指標、持倉日數與權益樣本）、`verification/verify_backtest_snapshot.py`
（驗證舊欄位不變及新統計）、`README.md`、`BACKTEST.md`。
原有測試、買賣／多頭規則、原台股助手及2026日曆均未修改。
