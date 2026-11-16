# Fugle HTTP 400 診斷修正（2026-10-30）

**工具的診斷與停機缺陷已修正；原 API 400 根因尚未確認，實測與一年覆蓋尚未完成。** 本次 Codex 程序的 `FUGLE_API_KEY` 不存在；沒有送出未授權或假金鑰的請求，也沒有用合成測試冒充實測。

## 證據與根因界線

- 原 `fugle_live_probes.json`：22 / 22 為 HTTP 400，產生時間 2026-10-30 14:54:21 +08:00。
- 原 `fugle_live_full.json`：另一次 22 / 22 為 HTTP 400，產生時間 14:55:59 +08:00；一年請求未執行。
- 舊工具在 `HTTPError` 時只留下 `HTTP_400`，沒有保留 API 錯誤訊息，因此無法從既存紀錄回復伺服器拒絕原因。
- 舊工具僅對 401 / 403 / 429 停機，400 仍繼續呼叫，是兩輪大量失敗的原因。這是已確認的工具問題，不能當成 API 回傳 400 的根因。
- 現行官方規格未顯示原查詢參數違規。金鑰內容、帳號權限、代理伺服器或 API 驗證差異都還沒有實測證據；不能逕自判定任何一項為根因。

## 官方文件對照

查閱日期 2026-10-30；[Historical Candles](https://developer.fugle.tw/docs/data/http-api/historical/candles/) 頁尾更新日期為 2026-10-27。

| 項目 | 核對結果 |
| --- | --- |
| HTTP / endpoint | `GET https://api.fugle.tw/marketdata/v1.0/stock/historical/candles/{symbol}`，原設定相符 |
| 認證 | `X-API-KEY` header，原設定相符；另加 `Accept: application/json` |
| 代碼 | `2330`、`2408`，不附 `.TW` |
| 日期 | `from` / `to`，`yyyy-MM-dd`；原單日查詢及 180 日分段均小於一年 |
| timeframe | `D`、`60`、`1` 皆合法；未提供則預設 `D` |
| 其他參數 | `fields=open,high,low,close,volume`、`sort=asc` 合法；`adjusted=false` 僅送日 K |
| 資料範圍 | 分 K 自 2023-05-23 起、上市櫃日 K 自 2010 年起；原查詢日期在範圍內 |

認證／前綴另對照[開始使用](https://developer.fugle.tw/docs/data/http-api/getting-started/)；HTTP 狀態判讀對照[官方錯誤代碼](https://developer.fugle.tw/docs/data/error_codes/)。不能以 400 推論金鑰無效或必須購買方案。

## 修正內容

只修改驗證工具、其測試及文件／驗證輸出。策略核心、日曆、原台股助手、正式 provider 與回測規則均未改動。

1. 加入前置診斷，使用既存 Yahoo 快照確認的正常交易日 **2026-04-21**：
   - 2330 日 K：只送 `from`、`to`，使用預設日 K。
   - 2330 60 分 K：相同日期，加 `timeframe=60`。
   - 依序測試原設定的日 K、60 分 K，確認額外參數相容。
2. 四次診斷全部成功後，依原順序跑原本 **22 組**；再由 `--full-range` 執行原 **12 組**一年加暖身查詢。總計最多 38 次，不自動重試。
3. 任一 HTTP、網路或資料驗證失敗即停止該階段後續呼叫，也不進入下一階段。400 不再重複 22 次。
4. 分開記錄 `http_status`、資料驗證 `status`、遮蔽後的 `api_error`，另存實際安全查詢參數。HTTP 200 後資料驗證失敗仍保留 200。
5. 錯誤本文最多讀取 16 KiB；只允許 JSON `message` / `error` 字串或字串陣列。含敏感欄位賦值的訊息整段捨棄；遮蔽金鑰原值與 URL 編碼形式、Bearer 及長 token 後才截斷為 1,000 字元。其他 header、物件、HTML、超大本文不記錄。
6. 增加金鑰格式與日期格式的本機檢查；格式不合直接失敗，不悄悄改寫金鑰。保留 TLS 驗證、禁止重新導向及呼叫間隔至少 1.05 秒。
7. 一年報告增加日線預期／回傳筆數與缺漏日期；仍使用既有 `prepare_data`、原始 60 分 K 網格與暖身規則。13:30 額外棒保留並判定不相容，不合併、不補價、不縮短期間。

## 本次可重現結果

- [fugle_diagnostic_20261030.json](fugle_diagnostic_20261030.json)：`NOT_RUN_MISSING_API_KEY`。四次前置診斷與原 22 組均標示未執行；沒有新的 HTTP 狀態碼可報告。
- [fugle_diagnostic_tests_20261030.json](fugle_diagnostic_tests_20261030.json)：完整單元測試 **223 項通過，0 失敗、0 錯誤、0 略過**；其中 Fugle 專項 37 項（新增 11 項）。全部為本機測試，不能證明實際 API 可用。
- [fugle_diagnostic_protected_20261030.json](fugle_diagnostic_protected_20261030.json)：對照原 SHA-256 基準，121 個受保護檔案全部未變更。
- 原兩份 live JSON 保留原樣，沒有以缺金鑰結果覆蓋歷史證據。
- 使用者後續產出的 `fugle_diagnostic_live.json`（2026-10-30 20:11:03 +08:00）仍為 `api_key_present=false`，四次診斷均 `NOT_RUN_MISSING_API_KEY`，並未送出 API 請求；這不是新的 HTTP 400 證據。

## 避免環境變數傳遞問題的啟動方式

`run_fugle_verification.ps1` 與 Python 驗證工具優先使用既有非空的 `FUGLE_API_KEY` 環境變數；未設定或僅有空白時，才讀取專案根目錄 `台股/.env` 的 `FUGLE_API_KEY`，不依執行時的工作目錄搜尋。支援一般 `.env` 的引號、註解、`export` 與 UTF-8 BOM，不展開 `${...}` 變數，也不將其他設定載入程序環境。環境變數若格式錯誤，仍回報原有格式錯誤，不改用檔案中的金鑰。

`台股/.gitignore` 已以 `.env` 排除該檔案；請勿提交金鑰。工具不輸出金鑰，報告仍僅記錄是否取得金鑰，API 錯誤沿用既有遮蔽。檔案不存在或沒有金鑰時，Python 仍回報 `NOT_RUN_MISSING_API_KEY`；檔案無法讀取或不是有效 UTF-8 時，以固定的 `ENV_FILE_READ_ERROR` 回報，不輸出檔案內容或原始例外。PowerShell 在環境變數與 `.env` 都沒有金鑰時，保留隱藏輸入；輸入值只暫存於程序環境，完成後還原。

在專案根目錄執行一次：

```powershell
powershell -NoProfile -File .\台股\666_strategy\verification\run_fugle_verification.ps1
```

環境變數或 `台股/.env` 已有金鑰時會直接執行；若出現提示，貼入金鑰並按 Enter，輸入不會顯示。預設依序跑前置診斷、原 22 組與一年加暖身；首個失敗即停止。結果寫入 `fugle_live_full_corrected.json`，不覆蓋原 HTTP 400 紀錄。只診斷可加 `-DiagnoseOnly`；可信任憑證可加 `-CaBundle <PEM路徑>`。本次讀取功能修改以本機合成測試驗證，沒有新增 API 實測結果。

## 在有金鑰的 PowerShell 執行

請在 `台股` 目錄執行；可使用既有環境變數或 `台股/.env` 的 `FUGLE_API_KEY`。不要把金鑰貼到聊天、命令列、報告或會提交的檔案。若不使用 `.env`，也可沿用隱藏輸入：

```powershell
$fugleSecureKey = Read-Host 'Fugle v1 行情 API Key' -AsSecureString
$env:FUGLE_API_KEY = [System.Net.NetworkCredential]::new('', $fugleSecureKey).Password
```

先取得最多四次呼叫的診斷結果；首次失敗後便停止：

```powershell
python -B -X utf8 -m 666_strategy.verification.verify_fugle_history --diagnose-only --output 666_strategy/verification/fugle_diagnostic_live.json
```

若出現 HTTP 400，檢查該 JSON 的 `diagnostics` 中 `query_params`、`http_status`、`api_error`，確認原因並修正後再跑。若回應是非 JSON，僅會留下 `NON_JSON_ERROR_BODY_OMITTED`，需進一步檢查網路／代理來源，不能把原始本文直接貼出。

診斷成功後，一次執行完整順序：

```powershell
python -B -X utf8 -m 666_strategy.verification.verify_fugle_history --full-range --output 666_strategy/verification/fugle_live_full_corrected.json
```

如需憑證，使用既存可信任 PEM 的 `--ca-bundle`，不要關閉 TLS 驗證。若最小 REST 請求仍無法解釋，才以[官方 Python SDK](https://github.com/fugle-dev/fugle-marketdata-python)用相同 `symbol` / `from` / `to` / `timeframe` 交叉驗證；本環境 SDK 未安裝，也沒有執行 SDK 請求。不得直接輸出 SDK 例外全文、`url`、`params` 或 `response_text`，以免旁路遮蔽機制。

## 尚待完成

取得同一執行環境的有效金鑰後，才可確認原 HTTP 400 根因、修正實際拒絕原因、取得日 K／60 分 K 的真實結果，並重驗 22 組與完整一年覆蓋。本次沒有宣稱這些項目成功，也沒有切換行情來源或執行一年回測。
