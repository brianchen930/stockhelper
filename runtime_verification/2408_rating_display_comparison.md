# 2408 輸出比較

使用既有真實日 K，截至 2026-09-11。修改前文字由同一份未變動的核心結果與舊 formatter 重建；不是重新 fit。

## 修改前

```text
【支撐 / 壓力】
・目前測試區：488.51～494.90
強度：強｜Swing High、Volume Profile HVN

・最近支撐：479.01～485.81（-2.15%）
強度：強｜K-Means、Swing Low

・最近壓力：504.01～505.99（+2.43%）
強度：中｜Swing High
・Bayesian 測試支撐（前日已知）：482.55～490.53
Bayesian 支撐可信度：46%｜uncertain
定義：已 resolved 的支撐測試中，達成既定反彈條件的估計機率。
研究模型，尚未校準；此數值不是股票上漲機率。
Prior：36%
主要正向證據：
・touch_volume_level=low（LR 1.49）
樣本：295 筆 resolved events；使用 5 項、跳過 3 項證據。
```

## 修改後 Normal

```text
【支撐 / 壓力】
・目前測試區：488.51～494.90
強度：強｜Swing High、Volume Profile HVN

・最近支撐：479.01～485.81（-2.15%）
強度：強｜K-Means、Swing Low

・最近壓力：504.01～505.99（+2.43%）
強度：中｜Swing High

・模型評估支撐：482.55～490.53
支撐成功機率：高
歷史條件偏有利，支撐成功可能性較佳。
```

## 修改後 Research

```text
【支撐 / 壓力】
・目前測試區：488.51～494.90
強度：強｜Swing High、Volume Profile HVN

・最近支撐：479.01～485.81（-2.15%）
強度：強｜K-Means、Swing Low

・最近壓力：504.01～505.99（+2.43%）
強度：中｜Swing High

・模型評估支撐：482.55～490.53
支撐成功機率：高
Research / Debug（等級為歷史模型預測的相對位置）
Posterior：45.8%
Prior：36.0%
Rating Strategy：distribution
Rating Thresholds：[0.2219221652175104, 0.3503774046741678, 0.4447505492574229, 0.5729612801567702]
Calibration / Model Status：uncalibrated / ready
完整研究資料（含 Positive / Negative Evidence LR、Used / Skipped Evidence、Training / Bucket Samples）：
{
  "prior_success_probability": 0.3602693602693603,
  "posterior_success_probability": 0.45785328431631167,
  "posterior_failure_probability": 0.5421467156836883,
  "confidence_percent": 45.785328431631164,
  "support_confidence_level": "uncertain",
  "model_status": "ready",
  "used_evidence_count": 5,
  "skipped_evidence_count": 3,
  "evidence_details": [
    {
      "feature": "touch_volume_level",
      "raw_value": "low",
      "bucket": "low",
      "success_count": 39,
      "failure_count": 46,
      "bucket_count": 85,
      "observed_success_count": 106,
      "observed_failure_count": 189,
      "p_given_success": 0.36363636363636365,
      "p_given_failure": 0.24352331606217617,
      "likelihood_ratio": 1.493230174081238,
      "log_likelihood_ratio": 0.4009416755163471,
      "low_sample_warning": false,
      "direction": "positive"
    },
    {
      "feature": "volatility_level",
      "raw_value": "極高波動",
      "bucket": "極高波動",
      "success_count": 22,
      "failure_count": 36,
      "bucket_count": 58,
      "observed_success_count": 106,
      "observed_failure_count": 189,
      "p_given_success": 0.20909090909090908,
      "p_given_failure": 0.19170984455958548,
      "likelihood_ratio": 1.0906633906633907,
      "log_likelihood_ratio": 0.08678612639739458,
      "low_sample_warning": false,
      "direction": "neutral"
    },
    {
      "feature": "support_source_count_bucket",
      "raw_value": 2.0,
      "bucket": "2",
      "success_count": 34,
      "failure_count": 59,
      "bucket_count": 93,
      "observed_success_count": 106,
      "observed_failure_count": 189,
      "p_given_success": 0.3181818181818182,
      "p_given_failure": 0.31088082901554404,
      "likelihood_ratio": 1.0234848484848484,
      "log_likelihood_ratio": 0.023213322379782353,
      "low_sample_warning": false,
      "direction": "neutral"
    },
    {
      "feature": "distance_to_support_atr_bucket",
      "raw_value": 0.08149044146334458,
      "bucket": "<=0.25",
      "success_count": 56,
      "failure_count": 105,
      "bucket_count": 161,
      "observed_success_count": 106,
      "observed_failure_count": 189,
      "p_given_success": 0.5181818181818182,
      "p_given_failure": 0.5492227979274611,
      "likelihood_ratio": 0.9434819897084049,
      "log_likelihood_ratio": -0.05817800316504762,
      "low_sample_warning": false,
      "direction": "neutral"
    },
    {
      "feature": "zone_width_atr_bucket",
      "raw_value": 0.2632768108815725,
      "bucket": "<0.5",
      "success_count": 99,
      "failure_count": 183,
      "bucket_count": 282,
      "observed_success_count": 106,
      "observed_failure_count": 189,
      "p_given_success": 0.9090909090909091,
      "p_given_failure": 0.9533678756476683,
      "likelihood_ratio": 0.9535573122529645,
      "log_likelihood_ratio": -0.0475557485084249,
      "low_sample_warning": false,
      "direction": "neutral"
    }
  ],
  "skipped_evidence": [
    {
      "feature": "current_touch_number_bucket",
      "raw_value": null,
      "reason": "missing_or_unknown_evidence"
    },
    {
      "feature": "touch_rebound_trend",
      "raw_value": null,
      "reason": "missing_or_unknown_evidence"
    },
    {
      "feature": "bars_since_last_touch_bucket",
      "raw_value": null,
      "reason": "missing_or_unknown_evidence"
    }
  ],
  "training_sample_count": 295,
  "training_success_count": 106,
  "training_failure_count": 189,
  "model_version": "bayes_support_v1"
}
```

