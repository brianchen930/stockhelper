# 支撐／壓力作為短中期文字背景

本次不更動偵測器、區間、指標、方向、分數、通知狀態或距離正負號。

## 資料流

`stock.get_stock_analysis()` 先用原有 `_attach_support_resistance()` 計算一次，
再把 `analysis_result['support_resistance']` 傳給
`analyze_timeframes(data, support_resistance)`。
原有短期、中期分析、`summarize_timeframes()` 與 `build_operation_reference()`
照常產生結果，最後由 `enrich_price_context()` 只更新文字欄位。
無結果、error、無效價位、弱區、遠區或週期資料不足時沿用既有文字。
直接呼叫舊 `analyze_timeframes(data)` 也仍相容。

## 文字選擇

- 短期：優先 active；偏弱看近支撐，偏多且壓力夠近才提壓力。
- 中期：修正時補充下一個防守區；同步偏多可觀察近壓力。
- 綜合判斷：保留原有短中期方向結論，再整合相關價格區。
- 綜合注意：把「跌破中期支撐」具體化。
- 操作參考：優先回指已解釋的區域，不產生買賣價位指令。

`PriceContextConfig` 集中管理文字引用門檻：strength_score 至少 4、支撐中心
距離最多 8%、近壓力中心距離最多 3%。只有 strong/very_strong 才稱強支撐。
同一價區在短中期敘述內最多出現 3 次，超過時用「上述支撐區」等回指。
這些門檻只控制文字，並非修改支撐／壓力候選、排序或分數。

## 事件顯示

`format_support_resistance_events()` 不再憑 active_zones 合成
CURRENTLY_TESTING_ZONE。若輸入事件本來包含此項，且上下界與支撐／壓力
區塊顯示的 active 相同，僅從本地顯示清單移除。原 events 不變；其他真實
事件繼續按既有顯示優先級選擇。過濾後沒有事件就不輸出標題。
原本的 CURRENTLY_TESTING_ZONE 只是 formatter 項目，沒有單獨觸發通知。

## 檔案與驗證

新增：`app/analysis/price_context.py`、`tests/test_price_context.py`、本文件。
修改：`app/stock.py`、`app/analysis/timeframe_summary.py`、
`app/rules/support_resistance_rule.py`（僅 formatter）、
`tests/test_support_resistance.py`、`tests/test_compact_presentation.py`。

測試涵蓋 active+支撐、active+壓力、無 active 的近支撐／近壓力、遠區、
無效資料／錯誤 fallback、CURRENTLY_TESTING_ZONE 單獨／搭配突破跌破，
以及原始結果不變、分數及因素不變、單次 detect 呼叫與文字重複上限。

正式 runtime 使用 watchlist 第一檔 2408 的真實行情，僅替換資料庫路徑與
notifier transport 為副本／測試接收器。短期 -3、中期 +4 不變，文字引用
488.51～494.90 與 479.01～485.81，未重複顯示目前測試事件。
完整輸出：`runtime_verification/terminal.txt`。
