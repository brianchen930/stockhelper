# 獨立 BUY 確認 A/B 實驗

在 `台股` 目錄執行：

```powershell
python -B -X utf8 -m 666_strategy.experiments.run_buy_confirmation_ab
```

A 直接使用原始 `backtest.runner.run_backtest`，並須精確重現既有連續兩年結果。
B 僅將原先符合日線多頭的 BUY 延至下一根完成 K 棒再確認；該棒 Close 嚴格高於自己的 MA60、日線仍多頭，才在再下一根 Open 買進。失敗即取消，必須等待新的上穿。SELL／EXIT 呼叫原決策函式，資金、整股、費稅及評價口徑與 A 一致。

固定使用既有 Fugle 合併快照，股票為 2330、2408，期間為 2024-10-30～2026-10-29，每檔每組初始 100 萬元、連續兩年不重設。無網路、憑證或券商下單；不匯入原台股助手。輸出於 `verification/fugle_ab_confirmation_20241030_20261029`。

可分階段使用 `--stage run`、`--stage audit`、`--stage report`。報告階段須有兩檔完整稽核及通過的測試紀錄；本次保存的測試執行命令與結果在輸出目錄的 `tests_result.json`。`protected_baseline.json` 保存實驗開始前的既有檔案 SHA-256，產出報告時必須全數相同。

```powershell
python -B -X utf8 -m unittest discover -s 666_strategy/tests -v
python -B -X utf8 -m unittest discover -s 666_strategy/experiments/tests -v
```

逐筆差異以原始上穿訊號時間配對；取消一次進場與完全錯過整段行情分別呈現。淨損益差包含前期資金累積效應，另提供每股毛價差診斷。結果僅是兩檔、固定期間的研究，仍須以其他股票及期間驗證，不會自動替換原策略。

## 2026-11-16 追加的隔離實驗（六檔，2024-10-30～2026-10-29）

以下實驗都沿用 `run_tpex_ab` 的六檔離線 Fugle 快照（2330、2408、2454、3105、6147、3211），不修改 `signals.py`、`decisions.py`、`backtest/runner.py`，只在執行期間暫時替換 `generate_signals` 或包裝 `evaluate_decision`。每支程式都會先驗證基準組精確重現既有結果，失敗即中止。

| 實驗 | 程式 | 內容 | 基準重現 | 輸出目錄（`verification/`） |
|---|---|---|---|---|
| ATR 緩衝帶 | `atr_buffer.py`、`run_atr_buffer.py` | MA60 ± k·ATR14 遲滯帶，k = 0.25／0.5（主）／1.0 | A 對 `six_stock_summary.csv` | `atr_buffer_20241030_20261029` |
| 高檔爆量出場 | `volume_top_exit.py`、`run_volume_top_exit.py` | 日 K 高檔＋爆量＋長上影／長黑全部出清；A／A2／C／D 四組 | A 對 `six_stock_summary.csv` | `volume_top_exit_20241030_20261029` |
| 日線多頭改為 MA60>MA120 | `run_ma_bull.py` | A／N／N2／N3（N3 加爆量出場） | A 對 `six_stock_summary.csv` | `ma_bull_20241030_20261029` |
| 慢出場與補進場 | `exit_reentry.py`、`run_exit_reentry.py` | 以 N2 為基準：連續兩根跌破、MA60−0.5·ATR14 出場、日線轉多補進場 | N2 對 `ma_bull_.../summary.json` | `exit_reentry_20241030_20261029` |

```powershell
python -B -X utf8 -m 666_strategy.experiments.run_atr_buffer
python -B -X utf8 -m 666_strategy.experiments.run_volume_top_exit
python -B -X utf8 -m 666_strategy.experiments.run_ma_bull
python -B -X utf8 -m 666_strategy.experiments.run_exit_reentry
```

`run_exit_reentry` 依賴 `run_ma_bull` 的輸出，須在其後執行。各輸出目錄的 `REPORT.md` 有結果與解讀；這些都是單一多頭期間的研究，不會自動替換原策略。
