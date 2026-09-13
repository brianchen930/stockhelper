# ATR／波動度標準化

本次新增標準化資料與摘要，未更動 RSI、MACD、KD、均線、區間偵測、評分或交易通知規則。

## 架構與變更

- 新增 `app/volatility.py`：無網路、資料庫或其他分析模組相依的共用工具。
- 修改 `app/stock.py`：indicators、analysis 與資料不足分析結果附加波動度；重用現有已正規化 OHLC。
- 修改 `app/analysis_engine.py`：原摘要末尾加入 ATR14，原有指標文字保持不變。
- 修改 `app/support_resistance_analysis/engine.py`：在 as_of 截點之後、區間 lookback 裁切之前計算標準化資料，再附加區間寬度和最近邊界距離。
- 新增 `tests/test_volatility.py`：數學、異常值、因果性、整合及 API lifespan 測試。
- 新增本文件。

## 函式

| 函式 | 責任 |
| --- | --- |
| `_number` | 將有限數值轉為 float；無效值轉為 None |
| `calculate_atr(data, period=14)` | 回傳與輸入索引對齊的完整 Wilder ATR 序列 |
| `calculate_atr_percent(atr, close)` | ATR / Close × 100 |
| `classify_volatility(atr_percent)` | 波動度分類 |
| `calculate_atr_distance(price_a, price_b, atr)` | abs(price_a - price_b) / ATR |
| `calculate_atr_signed_distance(price, reference_price, atr)` | (price - reference_price) / ATR |
| `calculate_zone_width_atr(zone_low, zone_high, atr)` | (zone_high - zone_low) / ATR；反向區間無效 |
| `summarize_volatility(data, period=14)` | 最新 atr、atr_percent、volatility_level 字典 |

## 計算與資料約定

TR = max(High - Low, abs(High - Previous Close), abs(Low - Previous Close))。
第一根 K 棒只提供前收盤價；前 14 個連續有效 TR 的算術平均為種子，
之後 ATR[t] = ATR[t-1] × 13/14 + TR[t]/14。因此 ATR14 首值在第 15 根 K 棒。
這是明確初始化的 Wilder 平滑，不依赖 pandas-ta 預設初始化。

傳入依時間遞增、索引唯一、單層 High/Low/Close 欄位的 DataFrame；
直接工具不排序、不補資料，錯誤索引、重複欄位或缺欄回傳全 NaN 序列。
正常系統入口沿用 `normalize_history`（包含既有排序、重複日期與無效 Close 處理）。
每一步僅使用當根及之前資料；回測可使用完整序列，或先截斷資料再計算。

無效 HLC（None、NaN、Infinity、非數值、非正價格或 Close 不在 Low～High）
會重置暖機，需重新累積前收盤價與 14 個 TR；不回填或沿用過期 ATR。
資料不足時摘要為 atr=None、atr_percent=None、volatility_level="資料不足"。
序列內使用 pandas NaN，對外摘要使用 JSON 可接受的 None。

ATR=0 是合法的零波動：ATR%=0，分類低波動；所有以 ATR 為除數的工具回傳 None。
無效價格、非正分母或溢位結果也回傳 None。內部不 round，摘要顯示兩位小數。

門檻集中在 `app/volatility.py`，單位為百分點：
LOW_VOLATILITY_MAX=1.5、MEDIUM_VOLATILITY_MAX=3.0、HIGH_VOLATILITY_MAX=5.0。
依序為 <1.5 低波動、[1.5,3) 中等波動、[3,5) 高波動、>=5 極高波動。

## 支撐／壓力欄位與相容性

結果增加 atr、atr_percent、volatility_level、nearest_support_distance_atr、
nearest_resistance_distance_atr；每個輸出區間字典增加 zone_width_atr。
空結果仍有上述頂層欄位，無可用區間距離為 None。

最近距離從「已輸出」的支撐上緣或壓力下緣取最小值。
例如股價 500、支撐 480～490、ATR=10，距離 1 ATR、寬度 1 ATR。
原 nearest_support / nearest_resistance 仍按既有中心距離選擇，
因此區間寬度差異很大時可能與新增邊界距離對應不同區間；不改變原事件消費端。

新 ATR 一律用 14 期完整可用日線，ATR% 分母為同根日線 Close，與盤中報價分開。
原 swing 偵測內的 TR rolling mean、atr_period 和篩選門檻保持原樣，
避免改動區間結果；新 Wilder ATR 不參與評分、突破或 Touch/Retest 規則。
未增加下載行情、資料庫欄位、貝氏模型或自動交易。

## 修改前後與驗證

| 項目 | 修改前 | 修改後 |
| --- | --- | --- |
| 技術摘要 | RSI/MACD/KD/均線 | 原文字加 ATR14、ATR%、分類 |
| API 波動度 | 無共用欄位 | indicators/analysis 有完整精度欄位 |
| 區間資訊 | 價格、百分比距離、原強度 | 另附 ATR 邊界距離與寬度 |
| 標準化工具 | 無 | 可重用且保留正負方向 |
| 既有策略／區間演算法 | 原規則 | 保持原規則 |

在台股目錄使用 `.venv/Scripts/python.exe -m pytest tests -q -p no:cacheprovider --basetemp=.pytest_tmp/<新的測試目錄>`。
基線 161 項通過；修改後 196 項全部通過（唯一警告為 Starlette/httpx 棄用提醒）。
新增測試涵蓋手算種子與遞迴、七項指定案例、分類邊界、
NaN/Infinity/異常型別、零波動、索引一致性、逐截點因果性、缺值後恢復、
區間附加資訊、下載次數和舊欄位一致性。
API 測試使用獨立暫存資料庫，實際執行 FastAPI lifespan 與排程器啟停，GET / 回傳啟動成功。
不呼叫通知端點或外部行情；未驗證外部服務連線。
