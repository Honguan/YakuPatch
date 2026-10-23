# I2 交付報告

## 階段 A：核心 API、工作佇列與 Codex 後端

分支：`i2-core-codex`（從 `main` d592194 開）。日期：2026-10-23。

### 修改檔案

| 檔案 | 內容 |
|---|---|
| `yakupatch/pipeline.py` | A1：`parse_ids` 解析 `{"translations":[{"id","translation"}]}`；`run` 接受 `json_ids`、新增 `stop`（波次之間停止，含名稱→其他兩階段的外層迴圈） |
| `yakupatch/backends/codex.py` | A2：PKCE 登入、keyring 分段憑證、token 更新、`/models`、`/responses` SSE、錯誤分類 |
| `yakupatch/core.py` | A4–A6：`open_game`、`counts`、`translate`、`history`、`diagnostics`、`Job`、`JobQueue` |
| `yakupatch/exchange.py` | Store 變更事件 `listeners`（GUI 訂閱用） |
| `yakupatch/cli.py` | `--backend codex`、`--effort`、`--script`、`--yes`；`codex login/logout/status/models`；`undo` 列歷史；`diagnose` |
| `tests/test_codex.py` | 11 個離線測試（假 keyring、假 opener、假 json_ids 後端） |
| `tests/eval/cases.jsonl`、`scripts/eval_backend.py` | A7：評測集與評測腳本（術語只算原文中出現者） |
| `pyproject.toml`、`README.md`、`PROJECT_PLAN.md` | 加 `keyring` 依賴；使用說明；§15 I2 狀態 |

### 邏輯摘要

- **A1 json_ids**：id 為批次內位置字串。重複 id、未知 id、非 JSON → 整批 `bad_json`，走既有拆批重送；缺漏的 id → 該句 `missing_id`，其他句照常保存。
- **A2 Codex**：瀏覽器登入（PKCE，回呼 `localhost:1455`，失敗時改 1457；用 `hmac.compare_digest` 檢查 state）。憑證只存 keyring，每 1000 字元一段；先寫完新的一組，再切換指標 `current`，寫一半當機時舊憑證仍可用。剩不到 60 秒過期或收到 401 就更新 token，更新後先存再用（refresh token 會輪替）。`store: false`、JSON schema 輸出、每個翻譯工作（Codex 物件）用一把隨機的 `prompt_cache_key`。錯誤分類：`usage_limit_reached` → UsageLimit；401 → AuthRequired；model_not_found／403 → ConfigError；context_length → TooLong；429 與 5xx → Transient；refusal → Refused。
- **A3 預估與暫停**：metered 後端翻譯前顯示預估輸入／輸出 token 並詢問（`--yes` 略過）。UsageLimit、AuthRequired、ConfigError 會中止工作，已完成的批次已保存，其餘維持 pending，重跑即續翻。
- **A4 佇列**：同一個後端 key 同時只跑一個工作，其餘排隊；key=None（提取、寫回）立即執行。暫停＝在波次之間停止；繼續＝`resume` 重新送出，靠 pending 狀態接續。
- **A5 歷史**：`core.history` 按 run 彙總日誌（含已封存的 `history/`），列出時間、後端、筆數、成功／失敗、是否已撤銷；撤銷沿用 `Store.undo`。
- **A6 診斷包**：zip 內只有 `diagnostics.json`，內容為版本、平台、引擎、偵測分數、筆數、錯誤統計、run 列表、檔案路徑／sha256／編碼；遊戲路徑中的家目錄改寫為 `<HOME>`。

### 測試結果

```
python -X utf8 -m unittest discover -s tests -t tests
Ran 46 tests ... OK
```

原有 35 個測試全數通過，另新增 11 個，涵蓋：

- json_ids 依 id 對應、缺漏 id、重複 id
- UsageLimit 中止後保留 pending 並可續翻
- `stop` 在波次之間停止
- 憑證分段、輪替與清除
- 過期 token 更新、401 後重新更新並重試
- SSE 解析與 7 種 HTTP 錯誤分類
- 請求內容（store/stream/reasoning/schema/cache key）與 `prepare` 的設定驗證
- PKCE 回呼流程（錯誤的 state 會被拒絕）
- 佇列排隊、取消、暫停、繼續與失敗
- 歷史與撤銷、Store 事件、診斷包不含原文、譯文與家目錄
- CLI 未確認時不送出請求，`--yes` 才送出

### A7 評測（G5）

評測集為自編 100 句，控制碼、人名、換行、擬聲詞、選項各 20 句。未達企劃的每類 50 句，這次先做 20 句；不含真實遊戲文本。術語比對會先把術語與譯文都轉成簡體（OpenCC t2s），因為 Sakura 輸出簡體、Codex 輸出繁體。

| 後端 | T11 失敗率 | 佔位符保留率 | 術語遵循率 |
|---|---|---|---|
| Sakura `sakura-7b-qwen2.5-v1.0-iq4xs`（遠端，`~/.yakupatch/config.json`） | 0.02（2/100） | 0.90（18/20） | 0.75（15/20） |
| Codex `gpt-6.1-sol`／effort `low`（預設：清單第一個模型） | 0.00（0/100） | 1.00（20/20） | 1.00（20/20） |

- Sakura 的 2 句失敗都是佔位符錯誤（`control-00`、`control-08`），已被 T11 攔下，沒有寫入。
- 術語遵循率只算原文中實際出現的術語；說話者名稱不要求出現在譯文。舊算法會把說話者也算進去，Sakura 原本 0.55、Codex 0.75，兩者都是被錯判；修正後從已存的譯文重新計分，沒有重送。
- Sakura 的 5 個術語錯誤都是「艾莉西亞」寫成「艾莉西娅」：「娅」轉成簡體後仍然和「亞」不同。
- 指令：`python -X utf8 scripts/eval_backend.py --backend sakura|codex --output <json>`。

### 真實測試

使用者自行執行 `yakupatch codex login` 登入後，從盤點結果中「日文、無錯誤、片段數少於 3000」的 6 款裡，用 seed 20261023 隨機抽 3 款。來源唯讀，只複製 `data`、`js` 到暫存副本，依序跑 extract → 預估 → translate → apply → 重複 apply（應無變更）→ restore。後端為 Codex 預設（`gpt-6.1-sol`、effort `low`、一次 4 批）。

| 引擎 | 遊戲 | 記錄 | 待翻 | 成功 | 失敗 | 預估批次／秒 | 全流程秒數 | 寫回檔／處 | 還原雜湊／來源 data 雜湊 |
|---|---|---|---|---|---|---|---|---|---|
| MZ | `D:\Games\TEMP\2025-10-14\01272755\日本語版(Japanese) ver2_11\Noa_Exit8` | 962 | 365 | 365 | 0 | 17／150 | 90.4 | 15／508 | 一致／一致 |
| MV | `G:\game\2023-03-16\ＪＫ痴漢電車 種付けおじさん v4\Game\www` | 325 | 310 | 310 | 0 | 14／120 | 62.6 | 12／717 | 一致／一致 |
| MV | `D:\Games\TEMP\2025-06-05\しすたーず!-義姉妹との性活-\しすたーず!-義姉妹との性活-\www` | 553 | 538 | 537 | 1（`repetition`） | 21／180 | 313.0 | 12／1851 | 一致／一致 |

- 3 款共 1,213 筆待翻，成功 1,212 筆，失敗的 1 筆保留原文。
- 速度約每秒 2–5 句（含提取／寫回／還原）。I1 的 Sakura 實測只算翻譯時間，約每秒 1.6 句。
- 機器報告在 session scratchpad 的 `real_codex.json`（未提交）。
- **額度用完暫停續跑**：以 mock 證明（`test_usage_limit_pauses_and_resume_continues`），沒有刻意耗盡真實額度。

### 新增 shortcut 與已知限制

- `codex.py`：`CLIENT_VERSION` 固定為 0.159.0。BallonsTranslator 會定期查 GitHub release 取得最新版號；若端點拒絕舊版號，需要手動更新。
- 進行中的 urllib 請求無法中斷（沿用既有 shortcut）：暫停或取消最長要等到 timeout（Codex 預設 300 秒）。
- 術語表放在 user 訊息而不是 instructions，讓固定的 instructions 能命中 prompt cache。這和提示詞草稿略有不同。
- 撤銷不還原術語表與快取（沿用既有 shortcut）；GUI 需要寫明。
- Codex 是非公開端點，可能隨時失效；README 已標註實驗性與隱私說明。

### 未完成

- 階段 B GUI：見下節。

### Commit

見本報告所在 commit（`git log -1 -- docs/I2_REPORT.md`）。

## 階段 B：GUI

分支：`i2-gui`（從已合併階段 A 的 `main` 1bf6d2e 開）。日期：2026-10-23。

### 修改檔案

| 檔案 | 內容 |
|---|---|
| `yakupatch/core.py` | B0：提取、預估、寫回預覽／寫回、還原、撤銷、匯出入；手改（T11 驗證後標 `reviewed`）、標為已審閱、套用到相同原文；術語讀寫；`config.json` 讀寫（`sakura`、`codex` 段）；`Library`（遊戲庫、`library.json`）；啟動遊戲、開啟資料夾 |
| `yakupatch/pipeline.py` | `select`／`estimate`／`run` 加 `only`（指定句子，可含已翻譯，不含已審閱）與 `fresh`（略過快取，重翻用）；統計加 `total` |
| `yakupatch/extract.py` | 把讀檔解析拆成 `read_game`、語言預檢拆成 `probe`，遊戲庫不寫檔即可判定語言；`extract` 行為不變 |
| `yakupatch/exchange.py` | `Store.busy`：翻譯進行中時術語表唯讀 |
| `yakupatch/engines/__init__.py` | 掃描遇到無法讀取的資料夾時略過，不中斷 |
| `yakupatch/cli.py` | `translate --backend codex` 讀 `config.json` 的 `codex` 段（命令列優先）；其他輸出不變 |
| `yakupatch/gui/` | `window.py` 主視窗與五個分頁、`settings.py` 設定、`models.py` 表格 model、篩選、佔位符標籤編輯器 |
| `tests/test_core.py`、`tests/test_gui.py` | B0 的 7 個測試；GUI offscreen 的 5 個測試 |
| `pyproject.toml`、`README.md`、`PROJECT_PLAN.md` | 加 `PySide6` 依賴與 `yakupatch-gui`；使用說明；§15 狀態 |

### 邏輯摘要

- **單一寫入者**：GUI 每款遊戲只開一個 `Store`，所有寫入經 Core API。Store 與工作的回呼在背景執行緒觸發，透過 Qt signal 轉回主執行緒。
- **背景工作**：提取、預估、寫回、還原、登入、測試連線都送進 `JobQueue`（key=None，不排隊）；翻譯以後端名稱為 key，同一後端一次一個，其他的排隊。
- **暫停與錯誤**：暫停＝在波次之間停止；繼續＝`JobQueue.resume`，靠 pending 接續。`AuthRequired`／`UsageLimit`／`ConfigError` 讓工作停下，翻譯分頁顯示說明與處理按鈕（重新登入、稍後繼續、開啟設定）。
- **手改**：編輯器顯示遮罩後的譯文，`<0>` 這類佔位符以標籤樣式呈現；游標不會停在標籤中間，Backspace／Delete 一次刪整個標籤。儲存時用與翻譯相同的 T11 檢查，未通過不寫入並顯示原因；通過後還原控制碼與前後空白，標為 `reviewed`、`by=manual`，產生可撤銷的 run。
- **重翻**：指定句子並略過快取，否則相同上下文會直接拿到快取的舊譯文。
- **術語**：選擇「翻譯中唯讀」而非合併：`core.translate` 期間 `Store.busy` 為真，`save_glossary` 會拒絕；另檢查原文不可空白、重複，欄位不可含 Tab 或換行。
- **設定**：Sakura、Codex 各用自己的 config class 驗證後才寫入，只換掉自己的段落；失敗時檔案不變。Codex 首次翻譯前詢問隱私並記錄 `privacy_ack`。
- **在地化**：介面字串經 `QCoreApplication.translate`。原文就是繁體中文，所以沒有另外產生語言檔。高 DPI 用 Qt 6 預設行為。

### 測試結果

- `python -X utf8 -m unittest discover -s tests -t tests`：58 個測試，全數通過（3.0 秒）。另以 `-W error::DeprecationWarning` 跑一次，同樣通過。
- 新增：手改未通過 T11 被拒、手改可撤銷、標為已審閱與套用到相同原文、指定句子重翻略過快取且不碰已審閱、術語在翻譯中唯讀、`config.json` 保留其他鍵且驗證失敗不寫入、遊戲庫掃描與持久化、移除不動遊戲檔、語言預檢「仍要翻譯」；GUI：標籤切分與整塊刪除、表格 model 與篩選、Store 事件更新表格、GUI 走完提取 → 翻譯（第二個同後端工作排隊）→ 手改 → 寫回 → 撤銷 → 還原、`UsageLimit` 暫停後顯示處理按鈕並可繼續。

### 真實測試

從階段 A 盤點中「日文、無錯誤、片段數少於 3000」的 6 款，用 seed 20261024 隨機抽 3 款。來源唯讀，前兩款只複製 `data`、`js`、`index.html`、`package.json`，第三款複製整個遊戲（含 `Game.exe`）以測試啟動。以 Python 驅動 `MainWindow`（真實 Qt 平台，確認對話框以 mock 回答「是」），`YAKUPATCH_HOME` 指向暫存目錄，不動使用者的設定與遊戲庫。每款依序：加入遊戲庫 → 提取 → 試譯前 20 句 → 翻譯其餘 → 文本分頁手改 1 句 → 預覽 → 寫回 → 再預覽（應無變更）→（第三款）啟動遊戲 → 撤銷試譯 → 還原 → 比對雜湊。

| 引擎 | 遊戲 | 後端 | 記錄 | 待翻 | 試譯 | 全部翻譯（成功／失敗） | 翻譯秒數 | 寫回檔／處 | 再寫回 | 啟動 | 還原雜湊／來源雜湊 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| MZ | `D:\Games\TEMP\2025-12-12\01511868\01511868\Game` | Codex | 759 | 742 | 20／0 | 722／0 | 173.7 | 10／928 | 0 檔 | — | 一致／一致 |
| MV | `D:\Games\TEMP\2025-06-05\しすたーず!-義姉妹との性活-\しすたーず!-義姉妹との性活-\www` | Codex | 553 | 538 | 20／0 | 518／0 | 328.0 | 12／1853 | 0 檔 | — | 一致／一致 |
| MV | `D:\Games\TEMP\2025-06-05\01348401\www` | Sakura | 493 | 479 | 20／0 | 417／42 | 725.9 | 14／1541 | 0 檔 | 15 秒後仍在執行 | 一致／一致 |

- 3 款手改的句子都變成 `reviewed`；撤銷試譯的紀錄都標為已撤銷；過程中沒有出現錯誤對話框。
- Sakura 的 42 句失敗：殘留日文 41、超出長度 1，都被 T11 攔下並保留原文，和 I1 的 Sakura 品質一致。
- 第 2 款與階段 A 抽到的遊戲相同（隨機結果）。
- 機器報告在 session scratchpad 的 `real_gui.json`（未提交）。

### 新增 shortcut 與已知限制

- `core.open_folder`：只支援 Windows（`os.startfile`）。
- `MainWindow.refresh_library`：每次重新整理都從磁碟讀各專案來算進度；遊戲庫很大時可能變慢，屆時把統計快取進 `library.json`。
- 啟動遊戲只找 `Game.exe`（遊戲資料夾或上一層），找不到就提示手動啟動。
- 移除遊戲只從遊戲庫拿掉，專案資料保留，重新加入即可接續。
- 真實測試只確認遊戲程序能啟動，**沒有人工確認畫面上的譯文**。§15 要求「一般使用者不碰命令列」：由於對話框以程式回答，仍需要使用者親手操作一次 GUI 驗收。

### 使用者手動驗收（2026-10-23）

- 使用者親手操作 GUI，以 MV 遊戲 01050555、Sakura 後端全部翻譯 8,156 句：成功 8,061、失敗 95（保留原文），使用者回報「都正常」。
- 過程中發現並修正 1 個問題：按「開始翻譯」出現 `'bool' object is not iterable`。原因是 Qt 按鈕的 `clicked(bool)` 被當成 `only` 傳入；工具列「設定」有同樣問題（`tab=False` 剛好等於 0 才沒出錯）。兩處改用 lambda，`test_gui` 改成真的點按鈕（修正前該測試失敗）。

### 未完成

- 未逐項記錄手動驗收清單的每一步結果；以使用者「都正常」的回報為準。
- 依提示詞不做：首次啟動精靈、OneClickLLAMA 代為啟動、其他 OpenAI 相容後端、外部字典查詢、深色主題、多語系語言檔、PyInstaller 打包。
- 文本分頁的「無法編碼」「不一致」篩選、術語「全部確認」需再按一次確認、字典查詢面板：未做（字典為 I3）。

### Commit

見本報告所在 commit（`git log -1 -- docs/I2_REPORT.md`）。
