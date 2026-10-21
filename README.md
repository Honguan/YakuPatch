# YakuPatch

不注入、直接修改遊戲檔案的遊戲翻譯器。設計見 [PROJECT_PLAN.md](PROJECT_PLAN.md)。

目前進度：I0（RPG Maker MZ，CLI）。

## 安裝

```bash
pip install -e .
```

## 使用

```bash
yakupatch detect  <遊戲資料夾>          # 偵測引擎
yakupatch extract <遊戲資料夾>          # 提取文本；遊戲已是中文或附中文語系時會停下，--force 強制
yakupatch export  <遊戲資料夾>          # 產生 batches/out/part-*.jsonl 與 INSTRUCTIONS.md
#   填好 dst 後放到 batches/in/
yakupatch import  <遊戲資料夾>          # 匯入譯文（檢查佔位符、換行等，不合格的寫到 *.rejected.jsonl）
yakupatch apply   <遊戲資料夾>          # 預覽；加 --yes 才寫入
yakupatch restore <遊戲資料夾>          # 從 _yakupatch_backup/ 還原
yakupatch undo    <遊戲資料夾> [run]    # 撤銷一次匯入
yakupatch status  <遊戲資料夾>
```

專案資料預設放在 `~/.yakupatch/projects`（`--home` 或環境變數 `YAKUPATCH_HOME` 可改）；備份放在遊戲資料夾內 `_yakupatch_backup/`。

## 測試

```bash
python -X utf8 -m unittest discover -s tests -t tests
```

## 授權

GPL-3.0-or-later
