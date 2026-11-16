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
