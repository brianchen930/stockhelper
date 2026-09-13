# Bayesian Support Confidence V1 實作與研究報告

2408 樣本外 Bayesian 結果未優於同期先驗基準。此版本保留原始評估，不依測試結果調參；顯示機率尚未校準，且只表示 **resolved 支撐事件條件下的 success 機率**。Neutral 排除不代表 neutral 是 failure，也不能將數值解讀為股票上漲機率。

## 檔案與架構

新增 `app/bayesian_support/`：`config.py` 集中常數與 predictor guard；`features.py` 分箱、事件驗證及交易日標籤成熟時間；`model.py` fit/predict/save/load/explain；`evaluation.py` 指標、校準、年度、來源及條件關聯統計；`backtest.py` 獨立研究 CLI；`integration.py` 即時快取載入；`__init__.py` 套件入口。

新增 `tests/test_bayesian_support.py`、本報告與 `runtime_verification/build_bayes_report.py`，研究輸出位於 `data/models/bayesian_support/2408_20260913T141108Z_174a42d9/`；`2408.json` 是即時載入模型。

修改 `app/backtest/support_event.py`、`app/backtest/support_backtest.py`：增加 outcome 欄位 `label_available_date`，新事件 schema 為 3。修改 `app/stock.py`：既有支撐文字產生後附加可選 Bayesian 顯示。此次沒有修改 RSI、MACD、KD、股票總分或 Scheduler 邏輯。

## 計算定義

- Prior = (success + 1) / (success + failure + 2)。不含 neutral，沒有樣本時為 None。
- 每個分類 likelihood = (該類別該 label 計數 + alpha) / (該 feature 有效觀察的該 label 總數 + alpha × 類別數)，alpha=1。缺失觀察排除分母，完整表因此仍歸一化。
- LR = P(E|Success) / P(E|Failure)；log LR = log P(E|Success) − log P(E|Failure)。log posterior odds = log prior odds + 各項 log LR，再用穩定 logistic 還原。不是直接連乘小數。
- None、NaN、insufficient_data、未知類別跳過，不當負向證據；回傳 used/skipped counts。整個 feature 無有效訓練觀察也跳過。
- LR >=1.1 為 positive，<=0.9 為 negative，其餘 neutral。逐項保存 raw value、bucket、兩類 likelihood、LR、log LR、原始計數、小樣本標記。
- 少於 100 筆 resolved 或缺少任一類：insufficient_training_data，正式 confidence 不顯示百分比。Walk-forward 則 warmup、prediction=None。Bucket <10 筆標記 low_sample_warning。
- 顯示分類：<40% low；[40,60)% uncertain；[60,75)% moderate；[75,90)% high；>=90% very_high。僅為顯示分類，使用整數百分比。

## Evidence 與分箱

預設 8 項：current_touch_number_bucket、touch_rebound_trend、touch_volume_level、volatility_level、support_source_count_bucket、distance_to_support_atr_bucket、zone_width_atr_bucket、bars_since_last_touch_bucket。

| 原始 feature | 分箱 |
| --- | --- |
| current_touch_number / support_source_count | 1、2、3、4+ |
| distance_to_support_atr | <=0.25、(0.25,0.5]、(0.5,1]、>1 |
| zone_width_atr | <0.5、[0.5,1)、[1,1.5)、>=1.5 |
| bars_since_last_touch | 0–5、6–20、21–60、>60 |
| support_age_bars（可選，預設不啟用） | 0–20、21–60、61–120、>120 |

排除 historical_touch_count，避免與 current_touch_number 重複；排除 touch_volume_ratio，避免與 volume level 重複。來源家族/組合只做描述統計，不作第二組 Bayesian evidence。所有 future/post/breakdown outcome、label、target、標籤成熟日期禁止進 predictor list，違反立即 raise。原始數值與分類重複啟用也不允許。

## 時間與資料洩漏控制

每個日期重建 expanding training set，條件同時為 event_date < 預測日期、label_available_date < 預測日期。label_available_date 是完整 N 根結果窗口最後一根的實際交易日期，並非 success 首次觸發日期，也不是日曆日期加 N。即使提前觸發 label 仍保守等待完整窗口成熟。同日事件共享訓練集。

舊 schema 2 CSV 透過原始 history.csv 與 event_index 對齊補成熟日期，來源檔不覆寫。新 schema 3 回測直接輸出成熟日期。API 對呼叫端提供的成熟日期做順序檢查；自訂資料呼叫端仍須依原始交易日歷正確建立該欄位。

事件依日期排序，完全相同事件去重；同一 symbol/timeframe/date/zone 的衝突資料報錯。全量 fit 模型禁止拿去預測其訓練結果尚未可知的過去日期。JSON 保存版本、訓練範圍、來源 SHA256、事件/Touch/ATR/label/detector 設定、bins 及原始 likelihood 計數；讀取驗證機率和 LR 一致性。

## 即時整合與使用

正常流程只快取讀取該股票 JSON，不呼叫 fit、完整回測或下載。只額外偵測前一天已知支撐，對今日有觸及的候選區更新 posterior。當日日 K 尚未收盤跳過。既有 live 流程沒有因果 Touch 歷史，故跳過 touch number、trend、bars_since_last_touch，最多使用其餘 5 項，絕不以舊版 touch_count 冒充。歷史評估使用最多 8 項，因此不能將其指標當作 live 5 項版本已獨立驗證的績效。

JSON 不存在時維持原本分析；模型損毀或時間/股票範圍不符時顯示資料不足。模型只適用記錄的股票及事件設定；資料或定義更新後需離線重新研究。沒有交易指令、部位管理或自動調權重。

```powershell
.\.venv\Scripts\python.exe -m app.bayesian_support.backtest data/backtests/support_events_2408_20260913T085146Z_c1304bd4/support_events.csv
```

`--activate` 可另存股票 JSON，但既有檔案不覆寫；所有研究 run 使用時間戳與隨機尾碼隔離。

```python
from app.bayesian_support.model import BayesianSupportModel, explain_prediction
model = BayesianSupportModel().fit(training_events, as_of=prediction_date)
result = model.predict(current_event)  # 必須含 event_date
print(explain_prediction(result))
model.save("new_model.json")
loaded = BayesianSupportModel.load("new_model.json")
```

## 2408 實測

沿用真實 2408.TW 日 K：2016-09-12～2026-09-11，共 2432 根。352 個事件，success 106、failure 189、neutral 57；resolved 295。全量最終 prior =107/297=36.03%。Walk-forward 共 122 個 warmup，194 個 resolved 樣本可供 OOS 評估。Baseline 每次只使用同一時間可知訓練集的 prior，不使用最終全量 prior 偷看未來。

| 模型 | Brier（低佳） | Log Loss（低佳） | Accuracy |
| --- | --- | --- | --- |
| Bayesian | 0.246932 | 0.697881 | 61.86% |
| 同期 prior baseline | 0.224329 | 0.641216 | 68.04% |

本次僅驗證已有的 2408 資料，未另行測試 2330、2454、3211，不將單一股票結果推論為全市場表現。

## Calibration

| probability_low | probability_high | prediction_count | average_predicted_probability | actual_success_rate |
| --- | --- | --- | --- | --- |
| 0.0000 | 0.1000 | 5 | 0.0772 | 0.2000 |
| 0.1000 | 0.2000 | 26 | 0.1517 | 0.3462 |
| 0.2000 | 0.3000 | 27 | 0.2480 | 0.2963 |
| 0.3000 | 0.4000 | 36 | 0.3512 | 0.2222 |
| 0.4000 | 0.5000 | 46 | 0.4516 | 0.3261 |
| 0.5000 | 0.6000 | 22 | 0.5504 | 0.3636 |
| 0.6000 | 0.7000 | 23 | 0.6522 | 0.5217 |
| 0.7000 | 0.8000 | 7 | 0.7330 | 0.1429 |
| 0.8000 | 0.9000 | 1 | 0.8084 | 0.0000 |
| 0.9000 | 1.0000 | 1 | 0.9078 | 0.0000 |

70–80% 區間只有 7 筆，實際成功率 14.29%；80–90%、90–100% 各只有 1 筆且失敗。高機率區樣本少且呈現過度自信，不能說預測 70% 已對應實際 70% 成功。

## 最近 10 筆（台北交易日）

| event_date | support_low | support_high | prior_success_probability | predicted_probability | top_positive_evidence | top_negative_evidence | actual_label |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 2026-06-18 | 429.6260 | 431.3740 | 0.3601 | 0.4157 | current_touch_number_bucket=1 LR 1.30 | touch_volume_level=elevated LR 0.80 | failure |
| 2026-06-26 | 466.7960 | 470.6477 | 0.3611 | 0.4819 | current_touch_number_bucket=1 LR 1.31 | — | failure |
| 2026-07-03 | 397.9000 | 405.5500 | 0.3611 | 0.4935 | touch_volume_level=low LR 1.45 | support_source_count_bucket=1 LR 0.89 | success |
| 2026-07-16 | 446.5872 | 448.5112 | 0.3586 | 0.3796 | current_touch_number_bucket=1 LR 1.22 | support_source_count_bucket=1 LR 0.90 | failure |
| 2026-07-17 | 397.9000 | 405.5500 | 0.3586 | 0.4357 | current_touch_number_bucket=2 LR 1.49 | support_source_count_bucket=1 LR 0.90 | failure |
| 2026-07-27 | 379.6980 | 381.3020 | 0.3608 | 0.3971 | distance_to_support_atr_bucket=>1 LR 1.75 | current_touch_number_bucket=3 LR 0.64 | failure |
| 2026-07-30 | 309.0000 | 315.1250 | 0.3608 | 0.3151 | current_touch_number_bucket=1 LR 1.28 | touch_volume_level=elevated LR 0.78 | success |
| 2026-07-30 | 339.6250 | 345.7500 | 0.3608 | 0.3376 | current_touch_number_bucket=2 LR 1.48 | touch_volume_level=elevated LR 0.78 | success |
| 2026-08-11 | 488.4960 | 495.0057 | 0.3571 | 0.3530 | current_touch_number_bucket=1 LR 1.25 | touch_volume_level=elevated LR 0.79 | neutral |
| 2026-08-24 | 509.2909 | 518.3500 | 0.3615 | 0.4894 | support_source_count_bucket=3 LR 1.36 | — | failure |

## 全量研究 Likelihood Table

下表使用最終 295 個 resolved 的訓練統計，用於解釋，並不是獨立驗證的因果效果。最近 10 筆的 LR 則來自各事件當時模型。

| feature | bucket | success_count | failure_count | p_given_success | p_given_failure | likelihood_ratio | low_sample_warning |
| --- | --- | --- | --- | --- | --- | --- | --- |
| current_touch_number_bucket | 1 | 19 | 27 | 0.1818 | 0.1451 | 1.2532 | False |
| current_touch_number_bucket | 2 | 11 | 13 | 0.1091 | 0.0725 | 1.5039 | False |
| current_touch_number_bucket | 3 | 3 | 11 | 0.0364 | 0.0622 | 0.5848 | False |
| current_touch_number_bucket | 4+ | 73 | 138 | 0.6727 | 0.7202 | 0.9341 | False |
| touch_rebound_trend | strengthening | 26 | 55 | 0.4030 | 0.3889 | 1.0362 | False |
| touch_rebound_trend | stable | 14 | 29 | 0.2239 | 0.2083 | 1.0746 | False |
| touch_rebound_trend | weakening | 24 | 57 | 0.3731 | 0.4028 | 0.9264 | False |
| touch_volume_level | low | 39 | 46 | 0.3636 | 0.2435 | 1.4932 | False |
| touch_volume_level | normal | 33 | 62 | 0.3091 | 0.3264 | 0.9469 | False |
| touch_volume_level | elevated | 22 | 46 | 0.2091 | 0.2435 | 0.8586 | False |
| touch_volume_level | spike | 12 | 35 | 0.1182 | 0.1865 | 0.6336 | False |
| volatility_level | 低波動 | 0 | 0 | 0.0091 | 0.0052 | 1.7545 | True |
| volatility_level | 中等波動 | 50 | 54 | 0.4636 | 0.2850 | 1.6269 | False |
| volatility_level | 高波動 | 34 | 99 | 0.3182 | 0.5181 | 0.6141 | False |
| volatility_level | 極高波動 | 22 | 36 | 0.2091 | 0.1917 | 1.0907 | False |
| support_source_count_bucket | 1 | 61 | 118 | 0.5636 | 0.6166 | 0.9141 | False |
| support_source_count_bucket | 2 | 34 | 59 | 0.3182 | 0.3109 | 1.0235 | False |
| support_source_count_bucket | 3 | 6 | 9 | 0.0636 | 0.0518 | 1.2282 | False |
| support_source_count_bucket | 4+ | 5 | 3 | 0.0545 | 0.0207 | 2.6318 | True |
| distance_to_support_atr_bucket | <=0.25 | 56 | 105 | 0.5182 | 0.5492 | 0.9435 | False |
| distance_to_support_atr_bucket | (0.25,0.5] | 17 | 32 | 0.1636 | 0.1710 | 0.9570 | False |
| distance_to_support_atr_bucket | (0.5,1] | 25 | 43 | 0.2364 | 0.2280 | 1.0368 | False |
| distance_to_support_atr_bucket | >1 | 8 | 9 | 0.0818 | 0.0518 | 1.5791 | False |
| zone_width_atr_bucket | <0.5 | 99 | 183 | 0.9091 | 0.9534 | 0.9536 | False |
| zone_width_atr_bucket | [0.5,1) | 7 | 6 | 0.0727 | 0.0363 | 2.0052 | False |
| zone_width_atr_bucket | [1,1.5) | 0 | 0 | 0.0091 | 0.0052 | 1.7545 | True |
| zone_width_atr_bucket | >=1.5 | 0 | 0 | 0.0091 | 0.0052 | 1.7545 | True |
| bars_since_last_touch_bucket | 0-5 | 9 | 9 | 0.1099 | 0.0602 | 1.8242 | False |
| bars_since_last_touch_bucket | 6-20 | 50 | 91 | 0.5604 | 0.5542 | 1.0112 | False |
| bars_since_last_touch_bucket | 21-60 | 21 | 39 | 0.2418 | 0.2410 | 1.0033 | False |
| bars_since_last_touch_bucket | >60 | 7 | 23 | 0.0879 | 0.1446 | 0.6081 | False |

### 最強正負向 LR 與稀疏 bucket

| feature | bucket | bucket_count | likelihood_ratio | low_sample_warning |
| --- | --- | --- | --- | --- |
| support_source_count_bucket | 4+ | 8 | 2.6318 | True |
| zone_width_atr_bucket | [0.5,1) | 13 | 2.0052 | False |
| bars_since_last_touch_bucket | 0-5 | 18 | 1.8242 | False |
| volatility_level | 低波動 | 0 | 1.7545 | True |
| current_touch_number_bucket | 3 | 14 | 0.5848 | False |
| bars_since_last_touch_bucket | >60 | 30 | 0.6081 | False |
| volatility_level | 高波動 | 133 | 0.6141 | False |
| touch_volume_level | spike | 47 | 0.6336 | False |

小樣本 bucket 共 4 個，完整原始計數如上；Laplace smoothing 不能替代樣本量。

## 年度穩定性

| year | event_count | actual_success_rate | average_predicted_probability | brier_score | log_loss | accuracy |
| --- | --- | --- | --- | --- | --- | --- |
| 2020 | 14 | 0.3571 | 0.5090 | 0.3917 | 1.0467 | 0.4286 |
| 2021 | 30 | 0.3333 | 0.3422 | 0.2065 | 0.5997 | 0.7333 |
| 2022 | 37 | 0.2432 | 0.3798 | 0.2096 | 0.6244 | 0.6757 |
| 2023 | 25 | 0.4400 | 0.4511 | 0.2654 | 0.7371 | 0.4400 |
| 2024 | 35 | 0.1714 | 0.3132 | 0.1906 | 0.5743 | 0.7714 |
| 2025 | 32 | 0.4688 | 0.4907 | 0.3226 | 0.8548 | 0.4062 |
| 2026 | 21 | 0.2857 | 0.4293 | 0.2306 | 0.6553 | 0.7619 |

年度成功率與誤差有明顯差異，但單年度樣本量有限，不能單凭這些統計判定特定 regime 原因。

## 支撐來源與組合（全量描述統計）

| kind | source | event_count | success_count | failure_count | neutral_count | resolved_count | success_given_source |
| --- | --- | --- | --- | --- | --- | --- | --- |
| family | swing | 207 | 62 | 116 | 29 | 178 | 0.3483 |
| family | volume_profile | 126 | 45 | 58 | 23 | 103 | 0.4369 |
| family | vwap | 77 | 25 | 40 | 12 | 65 | 0.3846 |
| family | kmeans | 88 | 23 | 46 | 19 | 69 | 0.3333 |
| combination | kmeans | 25 | 4 | 13 | 8 | 17 | 0.2353 |
| combination | kmeans+swing | 35 | 10 | 19 | 6 | 29 | 0.3448 |
| combination | kmeans+swing+volume_profile | 4 | 4 | 0 | 0 | 4 | 1.0000 |
| combination | kmeans+swing+volume_profile+vwap | 3 | 1 | 1 | 1 | 2 | 0.5000 |
| combination | kmeans+swing+vwap | 2 | 0 | 1 | 1 | 1 | 0.0000 |
| combination | kmeans+volume_profile | 13 | 3 | 8 | 2 | 11 | 0.2727 |
| combination | kmeans+volume_profile+vwap | 3 | 0 | 2 | 1 | 2 | 0.0000 |
| combination | kmeans+vwap | 3 | 1 | 2 | 0 | 3 | 0.3333 |
| combination | swing | 113 | 29 | 71 | 13 | 100 | 0.2900 |
| combination | swing+volume_profile | 30 | 9 | 16 | 5 | 25 | 0.3600 |
| combination | swing+volume_profile+vwap | 6 | 2 | 2 | 2 | 4 | 0.5000 |
| combination | swing+vwap | 14 | 7 | 6 | 1 | 13 | 0.5385 |
| combination | volume_profile | 55 | 22 | 22 | 11 | 44 | 0.5000 |
| combination | volume_profile+vwap | 12 | 4 | 7 | 1 | 11 | 0.3636 |
| combination | vwap | 34 | 10 | 19 | 5 | 29 | 0.3448 |

## 條件獨立性診斷

分 success/failure 計算類別 Cramér’s V；以下列出最大 10 組。這是樣本內描述，稀疏格會放大關聯估計，沒有當作顯著性檢定。

| label | feature_a | feature_b | sample_count | cramers_v |
| --- | --- | --- | --- | --- |
| success | support_source_count_bucket | zone_width_atr_bucket | 106 | 0.5697 |
| failure | volatility_level | zone_width_atr_bucket | 189 | 0.2863 |
| success | volatility_level | zone_width_atr_bucket | 106 | 0.2814 |
| failure | current_touch_number_bucket | volatility_level | 189 | 0.2730 |
| failure | support_source_count_bucket | zone_width_atr_bucket | 189 | 0.2697 |
| success | volatility_level | support_source_count_bucket | 106 | 0.2635 |
| success | distance_to_support_atr_bucket | zone_width_atr_bucket | 106 | 0.2513 |
| success | touch_rebound_trend | distance_to_support_atr_bucket | 64 | 0.2469 |
| success | support_source_count_bucket | bars_since_last_touch_bucket | 87 | 0.2310 |
| success | touch_rebound_trend | support_source_count_bucket | 64 | 0.2285 |

雖然已去除明顯重複欄位，Touch 次數、距上次 Touch 時間、反彈趨勢仍共享事件歷史，不能假定完全條件獨立。下一版建議先在獨立時間留出集做逐項移除比較，優先比較 Touch 次數與間隔保留單項的版本；對稀疏分類可事先訂定合併規則，再以新的未見資料驗證。若進行機率校準，也應用獨立時間區段，不在本次 OOS 結果上訓練並回報同一份結果。

## 驗證

測試涵蓋 prior、零計數 smoothing、LR=2 的 2/3 posterior、正負中性證據、缺失、0/1 指標邊界、極端 log odds、禁用 outcome、完整窗口成熟、同日事件、未來資料變動不影響過去預測、排序去重、JSON 回讀/損毀/禁止覆寫、calibration、live 快取且不得 fit、原有分數保留。完整測試包含原有 FastAPI 隔離資料庫啟動及 live 不匯入 backtest 的驗證。

實際完整測試結果：337 passed；1 項既有 Starlette/httpx 棄用提醒，沒有測試失敗。
