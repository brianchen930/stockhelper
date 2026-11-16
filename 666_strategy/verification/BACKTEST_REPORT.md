# 第三階段獨立歷史回測驗證（2026-10-30）

已完成 666_strategy 的獨立歷史回測。沿用既有 60 分 K BUY／SELL、股票日線多頭過濾與 PositionState，
以確認訊號後**下一根交易 K 棒的 Open**成交。原台股助手及策略核心未修改。
完整參數、資料契約與計算定義見 [BACKTEST.md](../BACKTEST.md)。

## 操作

在 `台股` 目錄輸入股票、含首尾的開始日／結束日：

```powershell
python -B -X utf8 -m 666_strategy backtest --symbol 2330 --start 2026-09-17 --end 2026-10-29 --output 666_strategy/backtest_results/2330.json
```

也可用 `python -m 666_strategy.backtest`。接受裸代號、`.TW`、`.TWO`，
以及 `--initial-cash`、`--commission-rate`、`--minimum-commission`、`--sell-tax-rate`、`--share-unit`、
`--ca-bundle`、`--source-snapshot`、`--calendar`。
每次輸出完整 JSON、逐筆成交 fills CSV、已平倉 trades CSV。

可重現本報告實際行情案例：

```powershell
python -B -X utf8 -m 666_strategy backtest --symbol 2330 --start 2026-09-17 --end 2026-10-29 --source-snapshot 666_strategy/verification/daily_live_20261029.json --output 666_strategy/verification/backtest_2330_20260917_20261029.json
python -B -X utf8 -m 666_strategy backtest --symbol 2408 --start 2026-09-17 --end 2026-10-29 --source-snapshot 666_strategy/verification/daily_live_20261029.json --output 666_strategy/verification/backtest_2408_20260917_20261029.json
```

## 資金、成本與原系統參考

已閱讀原 `backtest.py`、`app/backtest/runner.py`、`app/backtest/config.py`。
原系統只回放決策，明確不模擬成交、股數、資金及報酬，沒有既有交易成本設定可沿用。
因此保留其輸入操作方式，另列可調模擬預設：每檔獨立本金 1,000,000 元；
買賣手續費率各 0.001425、每筆最低 20 元；賣出稅率 0.003；一股為最小單位。
以上是模擬假設，不宣稱特定券商的實收、折扣或稅務處理。

每次買入包含手續費後可負擔的最大整數股數，平倉全賣、不借款、不加碼。
用 Decimal 計算並將金額四捨五入到 0.01 元；可改用 `--share-unit 1000` 以整張模擬。
總報酬率以期末現金加持倉市值對初始本金計算；勝率僅計已平倉且淨損益大於零的交易。

## 實際行情回測結果

來源：[既有完整 Yahoo 快照](daily_live_20261029.json)，含原始 60 分 K、股票日線與大盤日期參考，
as_of 為 2026-10-29 13:30（Asia/Taipei）。使用修正後的既有日曆與休市零量列處理。
此處使用保存資料重現，沒有補造、回填或改用其他價格來源。

期間 2026-09-17～2026-10-29，共 26 個交易日；各自取得 60 根 60 分 K 與 120 個日K作期間前暖身，
暖身期間不交易。兩檔最後均已平倉。

| 股票 | 已平倉交易 | 獲利交易 | 勝率 | 淨損益 | 總報酬率 | 期末權益 |
|---|---:|---:|---:|---:|---:|---:|
| 2330.TW | 1 | 0 | 0% | -9,753.96 | -0.975396% | 990,246.04 |
| 2408.TW | 5 | 2 | 40% | -143,963.78 | -14.396378% | 856,036.22 |

2330 手續費合計 2,835.32 元、交易稅 2,978.64 元；
2408 手續費合計 13,246.50 元、交易稅 13,768.28 元，均已扣入上表損益。

逐筆交易（時間皆為 2026 年 Asia/Taipei；此表報酬率僅顯示六位小數）：

| 股票 | 買進成交 | 賣出成交 | 買價→賣價 | 股數 | 買／賣手續費 | 交易稅 | 淨損益 | 單筆報酬率 |
|---|---|---|---:|---:|---:|---:|---:|---:|
| 2330 | 10/14 11:00 | 10/14 13:00 | 2530→2520 | 394 | 1420.47／1414.85 | 2978.64 | -9753.96 | -0.977115% |
| 2408 | 09/18 10:00 | 09/22 12:00 | 522→501 | 1912 | 1422.24／1365.02 | 2873.74 | -45813.00 | -4.583655% |
| 2408 | 09/23 10:00 | 09/24 10:00 | 524→497.5 | 1818 | 1357.50／1288.85 | 2713.37 | -53536.72 | -5.611877% |
| 2408 | 09/29 09:00 | 09/29 10:00 | 491→502 | 1831 | 1281.10／1309.81 | 2757.49 | 14792.60 | 1.643071% |
| 2408 | 09/29 12:00 | 10/08 09:00 | 503→522 | 1817 | 1302.38／1351.58 | 2845.42 | 29023.62 | 3.171102% |
| 2408 | 10/08 10:00 | 10/13 10:00 | 532→485 | 1772 | 1343.35／1224.67 | 2578.26 | -88430.28 | -9.367145% |

完整檔案：

- 2330：[完整 JSON](backtest_2330_20260917_20261029.json)、[每筆成交](backtest_2330_20260917_20261029.fills.csv)、[已平倉交易](backtest_2330_20260917_20261029.trades.csv)。
- 2408：[完整 JSON](backtest_2408_20260917_20261029.json)、[每筆成交](backtest_2408_20260917_20261029.fills.csv)、[已平倉交易](backtest_2408_20260917_20261029.trades.csv)。

例如 2408 在 **9/24 13:30** 確認 BUY，9/25、9/28 等休市後，於 **9/29 09:00** 的下一根交易棒以 Open 491 成交，
沒有拿訊號 Close 502 當成交價。10/7 13:30 的 SELL 則於 10/8 09:00 以 Open 522 平倉。
盤中收盤訊號與下一棒 Open 可在相同牆鐘時間，但使用不同棒索引，驗證逐筆檢查此點。
本實際區間全部平倉均為 SELL；沒有為了展示而額外捏造 EXIT，強制 EXIT 另以明確合成案例驗證。

## 期末未平倉實例

2408 同樣從 9/17 開始、只回測到 9/21：

```powershell
python -B -X utf8 -m 666_strategy backtest --symbol 2408 --start 2026-09-17 --end 2026-09-21 --source-snapshot 666_strategy/verification/daily_live_20261029.json --output 666_strategy/verification/backtest_open_2408_20260917_20260921.json
```

結果：9/18 10:00 以 522 買入 1,912 股，買進費 1,422.24 元；期末現金 513.76 元。
9/21 最後 60 分 K Close 為 516，持倉市值 986,592 元，未實現淨損益 -12,894.24 元，
期末權益 987,105.76 元、總報酬率 -1.289424%。買進費已計入，沒有扣假想賣出費或交易稅。
已平倉交易數為 0、勝率 null，未假造賣出。
見 [完整期末持倉 JSON](backtest_open_2408_20260917_20260921.json)。

## 線上查詢驗證及明確失敗

另一次線上明確指定 **2408、2026-09-17～2026-10-22** 的查詢成功，
驗證 175 根 60 分 K（含 60 根暖身）及 143 個日K（含 120 日暖身），
結果同樣為 5 筆已平倉、勝率 40%、淨損益 -143,963.78 元。
見 [線上回測 JSON](backtest_live_2408_20260917_20261022.json) 及其 CSV。

本次另一個到 **10/29** 的線上查詢，Yahoo 未提供當天有效 `^TWII` 日線 Close，
回傳 `YAHOO_SOURCE_ERROR` 並停止，沒有輸出不完整回測或自動改成 10/22。
這是仍存在的外部資料問題；先前保存的 10/29 快照含所需資料，故可離線重現上面的完整區間。
大盤只用於原有交易日期核對，其方向不參與買進過濾。

目前 yfinance 的 `60m/1h` 實作以 730 日作查詢界線參考，來源：
[官方 history.py](https://github.com/ranaroussi/yfinance/blob/main/yfinance/scrapers/history.py)。
回測把暖身一併做上界預檢，並以實際回傳逐棒檢查為最終依據，不保證 Yahoo 一定提供完整 730 日。
來源空白、截短、缺棒、無效值、日曆或暖身不足、未完成的結束日均明確報錯。
靜態來源日線 UNKNOWN 時停止整個回測；既有決策層 UNKNOWN 禁 BUY、不直接 EXIT 的邏輯維持原樣。

## 測試及獨立核對

- **172 項測試全部通過**，原有 136 項保留，新增 36 項；結果見 [測試 JSON](backtest_tests_20261030.json)。
- 新測試涵蓋下一棒 Open 與訊號 Close 不同、同時刻不同棒、跨日／休市成交、
  SELL／EXIT 去重、無 SELL 的日線失效出場、BUY 成交重查、UNKNOWN 取消且不復活、
  日線不足報錯、每個暖身／期間缺棒、OHLCV／重複／時區問題、期末持倉與 pending、
  成本／稅／股數／現金守恆、730 日與暖身界線、來源截短、CSV／JSON、CLI 與自訂日曆。
- 三個實際快照案例共 **13 筆成交**逐筆比對原始下一棒 Open，獨立重算費用、稅、現金及損益。
- **55 個交易日前綴**重新回測，成交與已平倉交易均與完整區間的相同前綴一致；未來資料沒有改寫過去成交。
- 原始 BUY／SELL 與保存來源中的原第二階段訊號完全一致。
- **101 個受保護檔案 SHA256 前後一致**，包含原 app、原 backtest.py、requirements，
  以及 666_strategy 的 signals.py、decisions.py、history.py、provider.py、daily.py、calendar.py、2026 日曆。
  工作區既有的 app/scheduler.py 變更也未觸碰。
- 完整證據見 [回測獨立核對 JSON](backtest_audit_20261030.json)，保留來源及結果雜湊。

重現測試與核對：

```powershell
python -B -X utf8 -m unittest discover -s 666_strategy/tests -v
python -B -X utf8 666_strategy/verification/verify_backtest_snapshot.py 666_strategy/verification/daily_live_20261029.json 666_strategy/verification/backtest_2330_20260917_20261029.json 666_strategy/verification/backtest_2408_20260917_20261029.json 666_strategy/verification/backtest_open_2408_20260917_20260921.json --output 666_strategy/verification/backtest_audit_replayed.json
```

首次執行另帶 `--protected-baseline 666_strategy/.cache/backtest_before.json` 核對前後檔案；
基準是本機暫存，檔案清單與雜湊已保存於正式核對 JSON。

## 修改／新增檔案

修改：`../__main__.py`（分派 backtest 子命令，原模式保留）、`../README.md`（第三階段入口）。

新增實作：

- `../backtest/__init__.py`、`../backtest/__main__.py`：獨立程式介面與 CLI。
- `../backtest/config.py`：模擬資金、股數單位、費用與 Decimal 金額。
- `../backtest/data.py`：完整區間、雙週期暖身、Yahoo 可用性、日曆、快照及明確錯誤。
- `../backtest/runner.py`：既有 PositionState 驅動的逐棒事件、下一棒 Open 成交、持倉與績效。
- `../backtest/export.py`：完整 JSON、成交與已平倉 CSV。

新增測試與文件：`../tests/test_backtest.py`、`../BACKTEST.md`、`verify_backtest_snapshot.py`、本報告。
新增產物：上述兩檔完整區間、2408 期末持倉、2408 線上區間各一組 JSON／fills CSV／trades CSV，
以及 `backtest_tests_20261030.json`、`backtest_audit_20261030.json`。
沒有新增套件依賴，也沒有修改既有策略、日曆修正或原助手檔案。

## 未解決問題與實作界線

- 本次線上到 10/29 的 Yahoo 大盤日期參考缺漏仍未解決；明確報錯保護保留。
- Yahoo 歷史分鐘資料有保存期限、限流與修訂可能；較早年份的日曆必須以 `--calendar` 提供已核對完整清單，
  目前內建只有 2026。無法取得足夠真實資料的區間不會產生績效。
- 未模擬滑價、價差、成交量限制、排隊、整張與零股撮合差異；每股以同一 60 分 K Open 理想化成交。
- 未調整價格未補入股利、分割或除權息現金流。費率為可調假設，ETF 等商品需自行指定適用稅率。
- 單一股票獨立資金回測；沒有跨股票資金分配、券商下單、正式持倉庫或策略參數最佳化。
