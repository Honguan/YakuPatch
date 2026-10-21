# YakuPatch

不注入、直接修改遊戲檔案的遊戲翻譯器。設計見 [PROJECT_PLAN.md](PROJECT_PLAN.md)。

目前進度：I1（RPG Maker MZ／MV、Sakura 本地翻譯、CLI）。

## 安裝

```bash
pip install -e .
```

## 使用

```bash
yakupatch detect  <遊戲資料夾>          # 偵測引擎
yakupatch extract <遊戲資料夾>          # 提取文本；遊戲已是中文或附中文語系時會停下，--force 強制
yakupatch translate <遊戲資料夾> --backend sakura --dry-run  # 離線預估筆數、字數、批次與時間
yakupatch translate <遊戲資料夾> --backend sakura --limit 20 # 先試譯 20 筆（名稱／系統優先）
yakupatch translate <遊戲資料夾> --backend sakura            # 翻譯剩餘項目，可中斷續跑
yakupatch export  <遊戲資料夾>          # 產生 batches/out/part-*.jsonl 與 INSTRUCTIONS.md
#   填好 dst 後放到 batches/in/
yakupatch import  <遊戲資料夾>          # 匯入譯文（檢查佔位符、換行等，不合格的寫到 *.rejected.jsonl）
yakupatch apply   <遊戲資料夾>          # 預覽；加 --yes 才寫入
yakupatch restore <遊戲資料夾>          # 從 _yakupatch_backup/ 還原
yakupatch undo    <遊戲資料夾> [run]    # 撤銷一次匯入
yakupatch status  <遊戲資料夾>
```

專案資料預設放在 `~/.yakupatch/projects`（`--home` 或環境變數 `YAKUPATCH_HOME` 可改）；備份放在遊戲資料夾內 `_yakupatch_backup/`。

Sakura 需先自行啟動 OpenAI 相容的 llama-server（建議監聽 `127.0.0.1`）。預設位址 `http://127.0.0.1:8080/v1`；可用 `--api-base http://區網IP:埠` 改位址，會自動補 `/v1`。啟動翻譯會檢查 `/health`，從 `/props` 讀取每槽 context 與平行數，從 `/v1/models` 讀模型名稱；不會自動啟動或關閉模型。

設定使用 `translate --help` 查看：`--version`（0.9／1.0／galtransl-v1）、`--model`、`--parallel`（只能降低伺服器槽數）、`--timeout`（999 秒）、`--retries`（3，最多 5）、`--max-tokens`（1024）、`--temperature`（0.1）、`--top-p`（0.3）、`--frequency-penalty`（0.05）、`--repeat-threshold`（20）、`--force-glossary`、`--enlarge-kana`。

名稱譯完會填入 `glossary.tsv` 尚未填寫的種子術語，已填的詞條保持原值。翻譯每批驗證後立即寫入日誌，記錄模型、提示詞版本、錯誤與嘗試次數；成功譯文另存專案 `cache.sqlite3`。續跑跳過已翻／已審閱／不確定的項目；一般 `failed` 可重試，模型明確拒絕的項目不自動重送。驗證失敗不寫入可疑譯文，`apply` 保留原文。Sakura 輸出維持簡體，不做繁簡轉換。

MV 可直接指定遊戲目錄或 `www/`；共用 MZ 的 JSON 解析與寫回。發現 `.rpgmvp` 或加密／不支援的 JSON 即拒絕，不嘗試解密。即使只有圖片加密也會拒絕整款 MV。

時間及 token 是粗估，未計重試、動態前文和新增術語的影響；`--dry-run` 不連網、不寫檔，使用保守的槽數與未解析模型名稱，快取命中數可能低於實跑。Ctrl+C 保留已提交批次；既有 HTTP 請求可能到 `--timeout` 後才結束。對話過長且單句仍超出預算時標記 `too_long`，保留原文。

## 測試

```bash
python -X utf8 -m unittest discover -s tests -t tests
```

真實 Sakura 與遊戲庫盤點為獨立腳本，不會在 unittest 中連網：

```powershell
python -X utf8 scripts/test_sakura_real.py '<日文MZ>' '<日文MV>' --output "$env:TEMP/yakupatch-real.json"
python -X utf8 scripts/audit_games.py 'G:/game' 'D:/Games/TEMP' --output "$env:TEMP/yakupatch-audit.json"
```

真實測試只複製 `data/` 與 `js/` 到新暫存目錄，執行 extract→translate→apply→restore，檢查原始 data 與還原後副本雜湊。盤點只讀遊戲，檢查偵測、可讀 JSON 往返、語言；加密 MV 也只檢查可直接讀取的 JSON。報告不可寫入受測遊戲庫；這些腳本不啟動遊戲，也不驗證畫面或字型。測試副本保留在輸出記載的暫存路徑，供人工複核。

I1 的實際數字與失敗清單見 [交付報告](docs/I1_REPORT.md)。

## 授權

GPL-3.0-or-later

Sakura 提示詞與術語格式參考 [BallonsTranslator](https://github.com/dmMaze/BallonsTranslator) 的 `trans_sakura.py`（GPL-3.0）。僅供使用者對合法持有的遊戲做個人翻譯；分享時只分享交換檔，不分享遊戲原始檔案。
