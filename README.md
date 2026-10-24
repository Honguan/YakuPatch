# YakuPatch

YakuPatch 把日文遊戲翻譯成中文。它直接修改遊戲的文字檔，不注入程式，寫入前會先備份，可以隨時還原。

目前支援 RPG Maker MV／MZ、TyranoScript（含 `app.asar`）、Ren'Py（含 `.rpa`）、KiriKiri（未加密 `.xp3`）、Unity（Mono；UI、Utage、Naninovel、自訂類別、`Assembly-CSharp.dll` 字串）、Godot（`.pck` 內的文字場景與 Godot 3 腳本）與 Artemis（`.pfs`），翻譯後端有 Sakura（本地模型）與 Codex（實驗性）。

> [!NOTE]
> 專案仍在開發中。I1（MV/MZ、Sakura、命令列）與 I2（圖形介面、Codex）已完成；I3（全文 QA、外部字典、自動換行、Tyrano、Ren'Py）已完成；I4（KiriKiri、Unity）已完成；I5 進行中（Godot、Artemis 已實作）。設計見 [PROJECT_PLAN.md](PROJECT_PLAN.md)。

## 功能

- 偵測引擎並提取文本。遊戲已是中文或附中文語系時會停下。
- 用 Sakura 或 Codex 翻譯，可中斷，重跑時從中斷處接續。
- 每批譯文都會檢查控制碼、換行數、長度與重複，不合格的不寫入，該句保留原文。
- 翻譯後做全文 QA：標出術語缺譯、同句異譯與夾在中間的漏譯；可寫 `style.md` 指定 Codex 的翻譯風格。
- 術語候選可查外部字典（JMnedict、日本人名、VNDB、Wikidata、自備詞庫），字典譯名須人工確認才會生效。
- Tyrano 的 `app.asar` 只改檔頭，改過的劇本放在 `app.asar.unpacked/`；Ren'Py 不改原腳本，譯文寫成新語言 `game/tl/yakupatch/`。
- KiriKiri 不改原封包，譯文打包成新的 `patch.xp3`；Unity 整檔重寫資產檔，並把文字統一改用 Windows 內建的中文字型，避免方塊字。
- 寫回前先預覽，備份後才修改；`restore` 還原，`undo` 撤銷某一次翻譯或匯入。
- 可以匯出分批檔，交給其他工具或人工翻譯後再匯入。

## 需求

- Python 3.11 以上
- 使用 Sakura 時：自行啟動、與 OpenAI 相容的 llama-server
- 使用 Codex 時：有 Codex 額度的 ChatGPT 帳號

## 安裝

```bash
pip install -e .
```

安裝後即可使用 `yakupatch` 指令與 `yakupatch-gui` 圖形介面。沒有安裝時，在專案根目錄用 `python -X utf8 -m yakupatch`（命令列）或 `python -m yakupatch.gui`（圖形介面）代替。

## 快速開始

```bash
yakupatch extract   <遊戲資料夾>
yakupatch translate <遊戲資料夾> --backend sakura --dry-run   # 只預估，不連網
yakupatch translate <遊戲資料夾> --backend sakura --limit 20  # 先試譯 20 筆
yakupatch translate <遊戲資料夾> --backend sakura
yakupatch apply     <遊戲資料夾>                              # 預覽
yakupatch apply     <遊戲資料夾> --yes                        # 寫入
```

要還原遊戲時執行 `yakupatch restore <遊戲資料夾>`。

## 圖形介面

執行 `yakupatch-gui`。左側是遊戲庫，上方五步驟條（偵測 → 語言預檢 → 提取 → 翻譯 → 寫回），中間分頁，下方工作佇列。

- **遊戲庫**：加入單一遊戲或掃描上層資料夾。已是中文的遊戲以灰色顯示「不需翻譯」並排在後面。右鍵可開啟資料夾、重新偵測或移除；移除只會從清單拿掉，不刪遊戲與翻譯資料。
- **總覽**：遊戲資訊與語言預檢；依目前步驟只顯示一個主要按鈕。遊戲已是中文時可按「仍要翻譯」。
- **文本**：可依狀態、種類、檔案、旗標（失敗、超長、不確定）篩選與搜尋。編輯區把控制碼顯示成不可拆的標籤，儲存時同樣經過譯文檢查，手改的句子標為已審閱。`Ctrl+Enter` 儲存並跳下一句，`F5` 重翻此句，`Ctrl+F` 搜尋。
- **術語**：編輯 `glossary.tsv`。翻譯進行中為唯讀。
- **翻譯**：選 Sakura 或 Codex，範圍可選全部、選取的句子、只翻名稱與術語、試譯前 N 句。開始前顯示用量預估；可暫停、繼續、取消。歷史可撤銷整次翻譯（不還原術語表與快取）。也可匯出分批檔、匯入譯文。
- **寫回與還原**：預覽會改的檔案與警告，確認後寫回；還原逐檔顯示結果；可直接啟動遊戲。
- **設定**：Sakura 參數與測試連線、Codex 登入／登出與模型、匯出診斷包。

同一個後端一次只跑一個翻譯工作，其他的排隊；翻譯時仍可瀏覽與編輯其他遊戲。登入失效、額度用完或設定錯誤時，工作會暫停並顯示處理按鈕，已完成的批次會保留。

## 指令

| 指令 | 說明 |
|---|---|
| `detect` | 偵測引擎 |
| `extract` | 提取文本；遊戲已是中文時停下，`--force` 強制提取 |
| `status` | 顯示各狀態的句數與專案資料夾 |
| `translate` | 翻譯，`--backend sakura\|codex`；`--dry-run` 只預估，`--limit N` 只翻前 N 筆（名稱與系統優先） |
| `export` | 產生 `batches/out/part-*.jsonl` 與 `INSTRUCTIONS.md` |
| `import` | 匯入 `batches/in/` 中填好 `dst` 的檔案；不合格的寫到 `*.rejected.jsonl` |
| `apply` | 預覽寫回；加 `--yes` 才寫入 |
| `restore` | 從 `_yakupatch_backup/` 還原 |
| `undo [run]` | 不帶 `run` 列出翻譯歷史；帶 `run` 撤銷該次翻譯或匯入 |
| `diagnose` | 匯出診斷包，不含遊戲文本、token 或使用者路徑 |
| `codex login\|logout\|status\|models` | 管理 Codex 帳號 |

每個指令的完整參數請看 `yakupatch <指令> --help`。

## 翻譯後端

### Sakura

先自行啟動 llama-server，建議只監聽 `127.0.0.1`。預設位址是 `http://127.0.0.1:8080/v1`，可用 `--api-base http://<區網IP>:<埠>` 更改，結尾的 `/v1` 會自動補上。

開始翻譯時，YakuPatch 會檢查 `/health`，從 `/props` 讀取每個槽的 context 與平行數，從 `/v1/models` 讀取模型名稱。它不會自動啟動或關閉模型。Sakura 輸出簡體中文，YakuPatch 不做繁簡轉換。

| 參數 | 說明與預設值 |
|---|---|
| `--version` | 提示詞版本，`0.9`、`1.0` 或 `galtransl-v1`；預設 `1.0` |
| `--model` | 預設讀取 `/models` |
| `--parallel` | 平行數上限，預設讀取 `/props`，只能調低 |
| `--timeout` | 999 秒 |
| `--retries` | 3（最多 5） |
| `--max-tokens` | 1024 |
| `--temperature` | 0.1 |
| `--top-p` | 0.3 |
| `--frequency-penalty` | 0.05 |
| `--repeat-threshold` | 20 |
| `--force-glossary` | 每批附上整份術語表 |
| `--enlarge-kana` | 放大小假名 |

### Codex（實驗性）

```bash
yakupatch codex login     # 開啟瀏覽器登入 ChatGPT
yakupatch codex models    # 列出可用模型與推理等級
yakupatch translate <遊戲資料夾> --backend codex
yakupatch codex logout
```

- 預設模型是 `codex models` 清單的第一個，推理等級 `--effort low`，一次送 4 批（`--parallel`）。
- `--script zh-Hant|zh-Hans` 指定輸出繁體或簡體，預設繁體。
- 翻譯前會顯示預估的輸入與輸出 token 並詢問是否開始，`--yes` 可略過。
- 額度用完、登入失效或設定錯誤時會停下。已完成的批次會保存，其餘維持待翻，重跑 `translate` 即可續翻。

> [!WARNING]
> Codex 使用非公開端點，可能隨時失效。翻譯時遊戲文本會送到 OpenAI（`store: false`）。憑證分段存在系統 keyring（Windows 認證管理員），不讀寫 `~/.codex/auth.json`；`logout` 只刪除 YakuPatch 自己的憑證。

## 設定

專案資料預設放在 `~/.yakupatch/projects`，可用 `--home` 或環境變數 `YAKUPATCH_HOME` 更改。備份放在遊戲資料夾內的 `_yakupatch_backup/`。

Sakura 的常用參數可以寫在 `~/.yakupatch/config.json`（有設 `YAKUPATCH_HOME` 時放在該目錄）。`sakura` 底下的鍵名和命令列參數相同，`-` 可寫成 `_`，命令列參數優先：

```json
{"sakura": {"api_base": "http://192.168.1.10:8080/v1"}}
```

只有 `translate` 會讀這個檔。檔案格式錯誤或有未知鍵名時，`translate` 會停下並列出可用鍵名，其他指令不受影響。

`codex` 段落存放 Codex 的模型、推理等級、平行數與輸出字體（`output_script`：`zh-Hant` 或 `zh-Hans`），`translate --backend codex` 也會讀取，命令列參數優先。圖形介面的設定頁寫入同一個檔，只改自己的段落，保留其他鍵；遊戲庫清單存在同目錄的 `library.json`。

## 運作方式

- **術語**：名稱譯完後，會填入 `glossary.tsv` 中尚未填寫的種子術語，已填的詞條保持原值。
- **紀錄與快取**：每批驗證後立即寫入日誌，記錄模型、提示詞版本、錯誤與嘗試次數。成功的譯文另存在專案的 `cache.sqlite3`。
- **續跑**：會跳過已翻、已審閱與不確定的項目。一般失敗的項目可以重試，模型明確拒絕翻譯的不會自動重送。
- **MV 支援**：可以直接指定遊戲目錄或 `www/`，與 MZ 共用 JSON 解析和寫回。圖片或音訊加密不影響明文 JSON 的翻譯。缺少 `System.json`，或 JSON 本身加密、格式不支援時，會拒絕處理，不嘗試解密。
- **預估**：時間與 token 都是粗估，不計重試、動態前文與新增術語。`--dry-run` 不連網也不寫檔，使用保守的槽數，不解析模型名稱，快取命中數可能比實際少。
- **中斷**：Ctrl+C 會保留已完成的批次；進行中的 HTTP 請求可能要到 `--timeout` 才結束。單句超出輸入預算時標記 `too_long` 並保留原文。

## 測試

```bash
python -X utf8 -m unittest discover -s tests -t tests
```

單元測試不連網。以下腳本需要另外執行：

```powershell
# 真實翻譯流程：複製 data/ 與 js/ 到暫存目錄，跑 extract → translate → apply → restore，比對雜湊
python -X utf8 scripts/test_sakura_real.py '<日文MZ>' '<日文MV>' --output "$env:TEMP/yakupatch-real.json"

# 遊戲庫盤點：只讀，檢查偵測、JSON 往返與語言
python -X utf8 scripts/audit_games.py 'G:/game' 'D:/Games/TEMP' --output "$env:TEMP/yakupatch-audit.json"

# 後端評測：tests/eval/cases.jsonl 的 100 句，輸出 T11 失敗率、佔位符保留率與術語遵循率
python -X utf8 scripts/eval_backend.py --backend sakura --output "$env:TEMP/eval-sakura.json"
python -X utf8 scripts/eval_backend.py --backend codex --output "$env:TEMP/eval-codex.json"
```

- 加密或格式不支援的 JSON 會列為失敗。
- 報告不能寫進受測的遊戲庫。測試副本保留在報告記載的暫存路徑，方便人工複查。
- 這些腳本不啟動遊戲，也不檢查畫面或字型。

實測數字與失敗清單見 [I1 報告](docs/I1_REPORT.md)、[I2 報告](docs/I2_REPORT.md)、[I3 報告](docs/I3_REPORT.md) 與 [I4 報告](docs/I4_REPORT.md)。

## 授權

本專案以 [GPL-3.0-or-later](https://www.gnu.org/licenses/gpl-3.0.html) 授權。

Sakura 提示詞與術語格式參考 [BallonsTranslator](https://github.com/dmMaze/BallonsTranslator) 的 `trans_sakura.py`；Codex 登入與請求格式參考同專案的 `modules/codex.py`（兩者皆為 GPL-3.0）。

YakuPatch 僅供翻譯你合法持有的遊戲、個人使用。分享時請只分享交換檔，不要分享遊戲原始檔案。
