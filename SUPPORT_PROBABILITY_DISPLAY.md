# 支撐成功機率：評級與顯示層

本次只新增 Probability Rating Layer 與 Output Presentation Layer。Bayesian Prior、likelihood、smoothing、posterior、evidence、事件 label、ATR、Touch、Volume、指標及分數均未修改。

## 檔案

修改：

- `app/bayesian_support/integration.py`：載入獨立評級 JSON、附加 display object，預設輸出簡潔文字。原 Bayesian result 完整保留。
- `app/support_resistance_analysis/formatting.py`：增加 `research_mode` / `debug` 關鍵字參數；統一一般及研究格式，處理區域合併。
- `app/stock.py`：`get_stock_analysis(..., research_mode=True)` 可取得完整研究文字，預設 False。

新增：

- `app/bayesian_support/rating.py`：集中門檻、中文 mapping、OOS 分布分析、評級資料結構及離線 CLI。
- `app/bayesian_support/presentation.py`：純顯示格式與價格區交集／聯集比。
- `tests/test_support_probability_display.py`：新增 21 個測試案例（含參數化）。
- `data/models/bayesian_support/2408.rating.json`：独立顯示參考，不改動 `2408.json`。
- `runtime_verification/rating_baseline_hashes.json`、`verify_rating_display.py`：本次核心不變驗證。
- `runtime_verification/2408_rating_display_comparison.md`：真實 2408 修改前後及研究輸出。
- 本說明文件。

## 五級如何產生

預設使用 OOS 預測 P20/P40/P60/P80 作為分界，等級依序為極低、低、中、高、極高，等於分界值時進入較高級。這是模型預測值的相對位置，不是經校準的絕對成功率。

`analyze_posterior_distribution(predictions_df)` 僅接收具備 walk-forward 稽核欄位的資料：ready、有效 probability [0,1]，並要求 training_available_until < event_date。拒絕重複事件與無法驗證時間的 ready 預測。呼叫端仍須提供真正由 walk-forward 產生的原始資料，無法藉日期欄位識別偽造資料。

包含 neutral 的 OOS 預測：因為這裡描述的是曾發出的預測值，而非僅 resolved 的評估成功率；warmup 無預測，排除。沒有以全部資料重新 fit 後的 in-sample 值決定級距。

## 2408 實際分布

來源：`data/models/bayesian_support/2408_20260913T141108Z_174a42d9/predictions.csv`。230 個有效 OOS 預測，資料來源 SHA256 保存在評級 JSON。

| 統計 | 值 |
| --- | --- |
| count | 230 |
| mean | 0.399401 |
| median | 0.407636 |
| std（母體，ddof=0） | 0.183210 |
| min | 0.066880 |
| max | 0.907753 |
| p10 | 0.156610 |
| p20 | 0.221922 |
| p25 | 0.250865 |
| p40 | 0.350377 |
| p50 | 0.407636 |
| p60 | 0.444751 |
| p75 | 0.521941 |
| p80 | 0.572961 |
| p90 | 0.649625 |

| 等級 | 2408 distribution 區間 |
| --- | --- |
| 極低 | <22.1922% |
| 低 | [22.1922%,35.0377%) |
| 中 | [35.0377%,44.4751%) |
| 高 | [44.4751%,57.2961%) |
| 極高 | >=57.2961% |

最低分布樣本數為 `MIN_RATING_DISTRIBUTION_SAMPLES=100`。不足時 `fixed_fallback`：30%、40%、50%、60% 四條分界；也可離線指定 `--strategy fixed`。分位數重疊／退化無法形成五級時同樣 fallback，保存原因，不人为拆散相同預測。

門檻參考必須早於正在顯示的交易日；若參考来自未來、檔案缺失或損毀，使用固定 fallback，不拿未來分布重標過去事件。即時不掃描歷史 CSV、不計算分位數、不重新回測。研究數據及 posterior 原值不覆寫。

## Normal 與 Research

一般模式 Bayesian 新增段落最多三行，預設 `SHOW_BAYESIAN_PERCENT_IN_NORMAL_MODE=False`，不含技術詞與精確百分比。設 True 可顯示例如 `高（46%）`。顯示設定不代表模型已校準；`calibration_status=uncalibrated` 保留，未新增校準演算法。

五級說明集中在 `RATING_DESCRIPTIONS`。Bayesian model_status 非 ready，或 probability 無效／缺失，顯示 `支撐成功機率：資料不足`；分布少但 posterior 有效則使用 fixed fallback。

`SupportProbabilityDisplayResult` 包含 posterior_probability、rating、rating_description、rating_strategy、thresholds、model_status、calibration_status。display 放在各候選區內，沒有刪改其 `result`。

研究用法：

```python
analysis = get_stock_analysis('2408', research_mode=True)
# 或將已有分析重新格式化，完全不再推論：
text = format_support_resistance_output(analysis['support_resistance'], research_mode=True)
# debug=True 等價。
```

研究輸出保留評估區、等級、posterior%、prior%、策略、四條分界、calibration/model status，以及完整 JSON，包括每項正負中性 LR、bucket counts、小樣本標記、使用與跳過證據及原因、訓練樣本。原 CSV、JSON 模型與 backtest 結果均保持原樣。

## 區域合併

Overlap = 交集寬度 / 聯集寬度。門檻 `ZONE_OVERLAP_THRESHOLD=0.7`。只比對當前實際顯示的「目前測試區」及「最近支撐」，不與壓力區合併。若多個符合，選比例最高的一個。點區間只有完全相同時視為重疊 1。

達標時一般文字使用兩區聯集價格並附加等級，不再重複列模型段落。保留當前區原有強度與來源；移除舊區 distance% 以避免把原始距離誤套在聯集範圍。僅調整文字，原區 low/high、distance、methods 和模型評估區都不變。研究模式總是分列原始區與模型區，可檢查其差異。

## 真實 2408 修改前後

截至既有 2026-09-11 日 K，原 posterior=0.45785328431631167，格式修改後完全相同。使用前述真實門檻得到「高」，並非需求中假設門檻示例的「中」。

修改前：

```text
・Bayesian 測試支撐（前日已知）：482.55～490.53
Bayesian 支撐可信度：46%｜uncertain
定義：已 resolved 的支撐測試中，達成既定反彈條件的估計機率。
研究模型，尚未校準；此數值不是股票上漲機率。
Prior：36%
（後續 LR、樣本與證據統計詳見完整對照檔）
```

修改後：

```text
・模型評估支撐：482.55～490.53
支撐成功機率：高
歷史條件偏有利，支撐成功可能性較佳。
```

此例與目前測試區及最近支撐的 IoU 均未達 0.7，維持分列。完整支撐／壓力輸出、舊完整 Bayesian 文字及新研究文字見 `runtime_verification/2408_rating_display_comparison.md`。舊文字由同一份核心結果與保留的舊 explain function 重建，不虛構另一次模型預測。

## 重建與驗證

```powershell
.\.venv\Scripts\python.exe -m app.bayesian_support.rating data/models/bayesian_support/2408_20260913T141108Z_174a42d9/predictions.csv --output data/models/bayesian_support/new_rating.json
.\.venv\Scripts\python.exe runtime_verification/verify_rating_display.py
```

離線 CLI 不覆寫既有檔案。新增 display 參考是研究分布，不變更模型校準狀態。現有 OOS 使用最多 8 項證據，live 缺失 Touch 歷史時最多 5 項；這個參考尚非專門針對 live 證據子集獨立驗證的分布，不能由等級推論已確認的絕對成功率。

完整測試：358 passed，1 項既有 Starlette/httpx 棄用提醒。包含原有 FastAPI 啟動、Scheduler/live import、Support Event 及 Bayesian walk-forward、核心 odds、快取不 fit 測試；新增五級邊界、分布不足、退化分位數、時間稽核、重複事件、缺失值、百分比開關、研究保留、重疊／不重疊及不合併壓力區。

本次另以 SHA256 比對 9 個核心／回測／模型檔，全部不變；真實 2408 格式化前後結果物件一致。
