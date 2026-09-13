# 支撐／壓力分析

本功能只新增 `get_stock_analysis()` 的 `support_resistance` 與
`support_resistance_text` 欄位，不參與 RSI、MACD、KD、MA、RuleEngine、
technical summary 或既有 signal score。無新增依賴。

## 檔案

新增 `app/support_resistance_analysis/`：

| 檔案 | 用途 |
| --- | --- |
| `__init__.py` | 公開介面 |
| `config.py` | 所有演算法參數、評分與分類門檻 |
| `models.py` | Candidate 與 SupportResistanceLevel dataclass |
| `swing.py` | 已確認的局部高低點 |
| `volume_profile.py` | OHLCV 近似 Volume Profile、POC、HVN |
| `vwap.py` | 日線 rolling / anchored VWAP |
| `clustering.py` | 可重現的一維 K-Means |
| `engine.py` | 資料品質、偵測、合併、分類、touch、評分與排序 |
| `formatting.py` | 繁體中文終端文字 |

另新增 `tests/test_support_resistance.py`、本文件及 `support_resistance_example.txt`。
修改 `app/stock.py` 接入分析；原有但尚未納入 Git 的
`app/support_resistance.py` 改為相容入口，沒有保留第二套偵測器。
原有 `SupportResistanceDetector().detect(df)` 與 zone_low / zone_high /
sources / strength 輸出別名仍可用。
舊私有函式不保留；舊 break_threshold_pct、break_confirmation_bars、
proximity_threshold_pct 參數僅接受而不啟用，改用 config 控制新分類方式。
舊 EMA zone 偵測已移除；既有股票 MA／EMA 指標程式未修改。

## 呼叫

從 `台股` 目錄執行：

```python
from app.stock import get_stock_analysis

result = get_stock_analysis(stock_code)  # 使用呼叫者提供的股票代號
print(result['support_resistance_text'])
```

獨立分析／回測：

```python
from app.support_resistance_analysis import (
    SupportResistanceEngine, SupportResistanceConfig,
    format_support_resistance_output,
)

config = SupportResistanceConfig(
    swing_window=3,
    merge_tolerance_pct=0.01,
    neutral_tolerance_pct=0.002,
    vp_bins=40,
    max_support_zones=3,
    max_resistance_zones=3,
)
engine = SupportResistanceEngine(config)
result = engine.detect(ohlcv, as_of=cutoff_timestamp)
print(format_support_resistance_output(result))
```

輸入沿用 `market_data.normalize_history()`：日期索引，OHLCV 大寫欄位，
支援現有單股票 MultiIndex 正規化。缺少 Open 可用 HLC 繼續分析；
缺少 H/L 不捏造資料，回傳空結果與提示。NaN／負 volume 視為零，
價格不一致、非正值、非有限值及極端比例異常棒會剔除，不修改輸入。
單筆新上市資料可提供有限的量能／VWAP 資訊，不宣稱已確認 Swing。

預設基準為最後有效日線 Close，並輸出 `price_basis` 與 `as_of`。
股票分析即使有即時報價，本模組仍使用日線收盤，避免混淆時間基準。
如自行傳入 `current_price`，呼叫者須確保是截止當時已知的報價。
原始行情使用 `auto_adjust=False`；拆股／減資等公司行動必須由資料端處理，
極端比例過濾並非公司行動調整器。缺失棒移除後的 window 按有效棒計算。

## 四種演算法

1. Swing：高／低必須嚴格超過左右各 `swing_window` 根，並通過
   百分比與 trailing ATR 的較高幅度門檻。相近點於 consensus 合併。
   Candidate 同時保留 pivot 發生位置與右側完成後的確認位置。
2. Volume Profile：每根棒的成交量按 High–Low 與 bin 的重疊比例分配，
   平價棒歸入一個 bin，保留總成交量。最高 bin 是 POC；超過相對門檻
   的局部峰值是 HVN。可指定 bin 數或 bin size，並設資源上限。
   這是均勻分布近似，並非逐筆成交 VPVR。
3. VWAP：以 HLC3 × volume / total volume 計算 rolling VWAP；
   從最近已確認 Swing High 和 Swing Low 的真實發生位置各計算 AVWAP。
   只在確認後使用 anchor，並非盤中 VWAP。
4. K-Means：僅使用 Swing 與 POC/HVN 價位，採 NumPy 一維 Lloyd 迭代。
   k 為唯一價位數平方根的整數、上限預設 5；分位數初始化確保可重現。
   少於 3 個唯一價位跳過，空 cluster 保留中心。無需 sklearn/scipy。
   K-Means 衍生自其他候選，屬於結構共識，並非統計獨立的驗證。

## Consensus、分類與評分

候選排序後，同群最大與最小中心差不得超過 `現價 × merge_tolerance_pct`，
防止鏈狀相鄰價位把很遠的兩端合併。區域邊界涵蓋 VP bin 或單點 padding；
center 是邊界中點。先合併再分類，不會把現價上下的近價位提前分開。
現價在區域內或 neutral margin 內是 `active`，另外輸出 `active_zones`。

strength_score 範圍為 0–10，預設為：

```text
10 × (0.55 × 方法共識 + 0.20 × 有效測試
    + 0.10 × 相對成交量 + 0.15 × 時間相關性)
```

- 共識以四個 family 去重；兩個 Swing、POC/HVN、多種 VWAP 不重複加票。
- 有效測試為連續入區的一次 visit，在進入後最多 5 根內，
  依入區前方向反彈／回落超過區域邊界 1%，才記一次。
  尚未反應或長期盤整不重複計數；最近有效測試日期是進入日。
- 成交量採接觸棒平均量／全期間平均量，只有高於平均才加分。
- Recency 使用 60 根半衰期與 25% 下限；由最新候選出現或反應确认計算。
- 各項飽和量、權重、衰減、強度文字門檻均在 config，可個別調整。

排序分數為 `abs(distance_pct)/100 − ranking_strength_weight × strength_score/10`，
數值小者優先。預設各保留最多 3 個支撐／壓力區與 3 個 active 區。
`nearest_support/resistance` 是所選區域內距離最近的一個；排名首位可能
因強度而稍遠。distance_pct 為中心到現價的帶正負百分比。

## 回測與降級

`as_of` 在正規化、四種演算法與評分之前切斷資料。所有輸出只描述
截止當時已知的區域；未確認的 Swing 或 touch reaction 不會提前輸出。
歷史 touch 是對「現在形成的區域」回看反應的描述，不代表歷史當時
已可交易該區域。回測每個時點必須重新呼叫 engine，不可把完整資料的
結果回填歷史。預設診斷 candidates 包含 `date` 與 `confirmed_date`。

各偵測器獨立捕捉失敗並回報 warnings，其餘方法继续運作。
輸出是 JSON 可序列化字典，zone dataclass 欄位包含 type、low、high、
center、strength_score、strength_label、methods、method_count、touch_count、
distance_pct、last_touch_date、last_seen_date。

## 驗證與範例

```powershell
.venv/Scripts/python.exe -m pytest tests -q -p no:cacheprovider --basetemp=.pytest_tmp/sr_validation_run
```

請使用新的暫存目錄名稱，避免現有 pytest 暫存目錄的權限問題。
`support_resistance_example.txt` 是固定合成 OHLCV 經引擎實際執行產生的輸出，
不是即時股價。測試包含前高／前低、合併／不合併、active 分類、
方法共識、成交量守恆、錨點、反應確認、時間截斷、零量與異常價格、
方法失敗降級，以及整合前後既有分析欄位一致性。
