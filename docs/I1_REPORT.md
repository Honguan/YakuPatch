# I1 交付報告

日期：2026-10-21（Asia/Taipei）。分支：`i1-sakura-mv`，基底 `main` 的 `9110b2b`。本報告的提交雜湊由交付訊息列出；未 push、未開 PR、未合併。

## 修改檔案與邏輯

| 檔案 | 變更 |
|---|---|
| `yakupatch/pipeline.py` | Work、T1–T12 串接、兩階段名稱優先、專案 SQLite 快取、前文／術語、分批、用量估算、平行派送、重試拆批、主執行緒 Store 寫回 |
| `yakupatch/backends/__init__.py` | BackendCaps 與 Transient／TooLong／AuthRequired／UsageLimit／ConfigError／Refused |
| `yakupatch/backends/sakura.py` | urllib OpenAI 相容端點、health／props／models、固定版本 prompt、術語格式、取樣設定與錯誤分類 |
| `yakupatch/engines/rpgmaker_mv.py` | 繼承 MZ parse/render，補 MV 偵測、加密圖片／JSON 拒絕；支援 UTF-8／UTF-16 JSON |
| `yakupatch/engines/__init__.py` | 註冊 MV，沿用根目錄搜尋發現 www |
| `yakupatch/translate.py` | 沿用 normalize／mask／validate／postprocess；補 CR 正規化、雙樣式字面佔位符避碰、可設定重複門檻 |
| `yakupatch/cli.py` | translate、--backend、--limit、--dry-run 及 Sakura 設定，繁體中文估算／進度／錯誤，中斷回傳 130 |
| `tests/test_pipeline.py` | 假後端完整往返、重試、拆批、提高溫度、驗證失敗、拒絕、致命錯誤、快取、中斷續跑、平行、CLI dry-run、HTTP mock、MV www／加密／UTF-16 |
| `tests/test_conformance.py` | CASES 加入 MV，互相檢查引擎不誤判 |
| `tests/fixtures/mv_min/data/` | 更新 System.json；新增 Actors、Armors、Classes、CommonEvents、Enemies、Items、Map001、MapInfos、Skills、States、Troops、Weapons.json，全為自行製作樣本 |
| `scripts/audit_games.py` | 遞迴唯讀盤點引擎標記，逐檔 decode→parse→render 空替換→encode 比較位元組，語言預檢與錯誤清單；加密資料不解密 |
| `scripts/test_sakura_real.py` | 明確啟用的真實模型完整往返，先拒絕加密 MV，再複製 data／js 到暫存目錄；檢查重複 apply、還原與來源 data 雜湊 |
| `README.md`、`PROJECT_PLAN.md`、本報告 | 用法、實際結構、差異、進度與實測 |

沒有新增依賴，沒有修改 Sakura 參考實作、模型設定或原始遊戲檔。

## 實作與企劃的差異

- 保留 I0 扁平模組、物件 refs、日誌與 Store 鎖；不拆分原架構。管線只接 lines 後端，尚未實作 Codex/json_ids、T13、場景摘要或 GUI。
- 名稱／系統先翻，只回填既有空白 seed 名稱術語；手動詞條不覆蓋。前文為去重順序前 3 筆中同檔記錄，使用已有譯文並在派送前更新；並非完整事件／場景結構。
- Sakura 的前文作為原文前綴送出，解析後按行數捨棄其譯文；多行對話仍以一筆 Work 保留內部換行。驗證／行數失敗拆半，單句去前文再升溫；暫時錯誤預設重試 3 次，可設至 5 次。拒絕只拆小一輪，最後標 failed 且不自動續送。
- 快取限專案，鍵比企劃更嚴格，另含控制碼對照、前文、說話者與全部後端設定。只保存驗證成功內容；讀取快取仍驗證。模型與 prompt 來源記錄於 by，嘗試次數與原因另記 attempts／errors。
- token 用每字 1.5 粗估，初始每波 30 秒估時間；實跑動態術語、前文、重試會改變實際批次數。離線 dry-run 不讀伺服器，模型名稱／槽數與快取命中為保守估算。
- Sakura 重複門檻依 §6.7 預設 20，外部匯入沿用 I0 的 4；後處理維持 I0，只還原遮罩與空白，不新增標點轉換或繁簡轉換。
- CLI 連接現有服務，不實作可選的模型自動啟停。驗收時另依本機 bat 設定啟動服務：8 槽、每槽 2048、總 context 16384、ngl=999、KV=f16；位址改為 127.0.0.1，埠 8080 由該 llama-server --help 的預設值確認。
- 對 `.rpgmvp` 採整款拒絕，即使 JSON 仍可讀。唯讀盤點另檢查其明文 JSON，不代表允許翻譯該款遊戲。

## 驗證

指令：`python -X utf8 -m unittest discover -s tests -t tests`。31 項全數通過，含 MZ／MV 的 6 項共用合規方法及既有 I0 測試。HTTP 測試均 mock，不連網；真實模型只由獨立腳本執行。

模型為 `sakura-7b-qwen2.5-v1.0-iq4xs.gguf`，llama-server 為本機 8 槽、每槽 2048 context。兩款皆不加 --limit，對全部 selectable 項目跑 extract→translate→apply→restore；失敗原文保留。

| 引擎 | 提取記錄 | 待翻 | 成功 | 失敗 | 翻譯秒數 | 全流程秒數 | 寫回檔／處 | 還原雜湊／來源 data 雜湊 |
|---|---:|---:|---:|---:|---:|---:|---|---|
| MZ | 759 | 740 | 730 | 10 | 147.554 | 151.858 | 8／914 | 一致／一致 |
| MV | 2,633 | 2,605 | 2,581 | 24 | 1,901.301 | 1,908.307 | 5／5,173 | 一致／一致 |

MZ 失敗原因：`length_ratio` 9、`line_count` 1。來源為 （路徑略）；副本為 `C:\Users\666dd\AppData\Local\Temp\yakupatch-real-2o_z0rzw`。預估 52 批／210 秒，實際翻譯 147.554 秒。

MV 失敗原因：`line_count` 20、`repetition` 2、`placeholders` 1、`length_ratio` 1。來源為 （路徑略）；副本為 `C:\Users\666dd\AppData\Local\Temp\yakupatch-real-g7qerro2`。預估 581 批／2,190 秒，實際翻譯 1,901.301 秒。

兩款共 3,345 筆待翻，成功 3,311、失敗 34，翻譯共 2,048.855 秒；所有失敗均保留原文。兩款各自的重複 apply 均無變更，MZ 還原 8 檔、MV 還原 5 檔，副本全部檔案（含 js）均與寫回前雜湊一致，來源 data 雜湊也一致。原始機器報告為 `%TEMP%/yakupatch-i1-real-final.json`；逐款盤點為 `%TEMP%/yakupatch-i1-audit-full.json`。早期調整估算公式前的探索輪未列入這組驗收數字。

## 遊戲庫唯讀盤點

以 `js/rmmz_core.js` 或 `js/rpg_core.js` 發現根目錄，不限制掃描深度，不追蹤目錄符號連結。兩個遊戲庫均未執行任何遊戲程式，未建立專案或備份。

| 遊戲庫 | MZ | MV | 無錯誤 | 可讀且往返一致檔案 |
|---|---:|---:|---:|---:|
| D:\Games\TEMP | 31 | 38 | 40 | 7,955 |
| G:\game | 15 | 21 | 22 | 3,049 |
| 合計 | 46 | 59 | 62 | 11,004 |

105 個標記根目錄中，104 個通過引擎偵測；缺 data 的 1 個仍列入失敗。共解析 2,506,222 個片段；逐款處理耗時合計 82.422 秒（未含目錄探索）。沒有已解析檔案的往返位元組差異。43 款 MV 有 `.rpgmvp`，均明確標為不支援；其中一款另有 71 個 JSON 無法解析，另一款缺 data。

語言：日文 53、簡中 28、繁中 11、混合 7、未知 5、缺 data 無法判定 1。未知包含無可解析文本的遊戲，不能當作日文。語言判定是啟發式結果，未人工逐款確認。

### 失敗／不支援清單（43 款）

下列全部均有 `.rpgmvp`，原因為「加密 MV，不支援且不嘗試解密」。另外兩個問題在項目末尾註明。路徑以各自遊戲庫為基準。

`D:\Games\TEMP`（29 款）：

1. （路徑略）
2. （路徑略）
3. （路徑略）
4. （路徑略）
5. （路徑略）
6. （路徑略）
7. （路徑略）
8. （路徑略）
9. （路徑略）
10. （路徑略）
11. （路徑略）：另有 71 個 JSONDecodeError，未解密，無法完成 JSON 往返。
12. （路徑略）
13. （路徑略）
14. （路徑略）
15. （路徑略）
16. （路徑略）
17. （路徑略）
18. （路徑略）
19. （路徑略）
20. （路徑略）
21. （路徑略）
22. （路徑略）
23. （路徑略）
24. （路徑略）
25. （路徑略）
26. （路徑略）
27. （路徑略）
28. （路徑略）
29. （路徑略）

`G:\game`（14 款）：

1. （路徑略）：另缺 `data/`，引擎偵測未通過，無法往返或判定語言。
2. （路徑略）
3. （路徑略）
4. （路徑略）
5. （路徑略）
6. （路徑略）
7. （路徑略）
8. （路徑略）
9. （路徑略）
10. （路徑略）
11. （路徑略）
12. （路徑略）
13. （路徑略）
14. （路徑略）

## shortcut 與審查重點

新增的 4 個 shortcut：

1. `pipeline.tokens`：每字 1.5 token 僅為估算；需要精確預算時改用模型 tokenizer。
2. `pipeline.cache_get`：只有專案快取；跨專案重用時須先定義隔離規則。
3. `pipeline.run`：urllib 無法取消正在等待的 socket；Ctrl+C 後行程結束最多等到 timeout。
4. `pipeline.update_glossary`：術語／快取不在 Store.undo 交易內；需要整次回滾這些產物時，須擴充 run-aware undo。

原有 4 個 shortcut 保持：容器解包、charset-normalizer 交叉偵測、W3 自動換行、備份新建檔案清單。未加入替代依賴或臨時解密方案。

審查請特別確認：

- 多行對話的行數對齊、佔位符完整性及失敗降級；失敗內容不進快取、不寫回遊戲。
- Store 日誌為續跑依據，只有主執行緒寫入；繼承 I0 的單行程限制，同一專案不可同時開兩個 CLI。術語與快取並非 Store undo 的交易內容，撤銷 Store 譯文後可能仍保留種子譯名／快取。
- Sakura 固定模板與繁簡不轉換；版本 0.9 不送術語，galtransl-v1 的無術語情況依參考實作退回基本模板。
- MV 圖片加密亦整款拒絕，這會排除大量明文 JSON 遊戲，是本次要求的明確取捨。
- 驗收只覆蓋資料往返，未啟動遊戲、未檢查實際字型／畫面／存檔；原企劃隨機 10 款啟動驗收仍待後續。成功通過結構驗證不代表語意品質已人工確認。
- 原始 JSON 稽核報告、模型進度與暫存副本只留在 TEMP，沒有將遊戲文字、執行日誌、快取、模型或個人設定提交到 Git。
