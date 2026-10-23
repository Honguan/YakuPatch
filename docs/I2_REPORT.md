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
- **A2 Codex**：瀏覽器登入（PKCE，回呼 `localhost:1455`，失敗時改 1457；用 `hmac.compare_digest` 檢查 state）。憑證只存 keyring，每 1000 字元一段；先寫完新的一組，再切換指標 `current`，寫一半當機時舊憑證仍可用。剩不到 60 秒過期或收到 401 就更新 token，更新後先存再用（refresh token 會輪替）。`store: false`、JSON schema 輸出、`prompt_cache_key` 固定為後端識別。錯誤分類：`usage_limit_reached` → UsageLimit；401 → AuthRequired；model_not_found／403 → ConfigError；context_length → TooLong；429 與 5xx → Transient；refusal → Refused。
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
| MZ | （路徑略） | 962 | 365 | 365 | 0 | 17／150 | 90.4 | 15／508 | 一致／一致 |
| MV | （路徑略） | 325 | 310 | 310 | 0 | 14／120 | 62.6 | 12／717 | 一致／一致 |
| MV | （路徑略） | 553 | 538 | 537 | 1（`repetition`） | 21／180 | 313.0 | 12／1851 | 一致／一致 |

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

- 階段 B GUI：尚未開始。

### Commit

見本報告所在 commit（`git log -1 -- docs/I2_REPORT.md`）。
