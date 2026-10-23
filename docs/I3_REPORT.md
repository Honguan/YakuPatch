# I3 交付報告

分支：`feat/i3`（從 `main` 7ecaf4f 開）。日期：2026-10-23。

## 修改檔案

| 階段 | 檔案 | 內容 |
|---|---|---|
| A | `yakupatch/qa.py` | T13 全文 QA：術語缺譯、同原文異譯、譯文斷層 |
| A | `yakupatch/pipeline.py`、`backends/codex.py` | `style.md` 傳給 Codex；翻譯結束自動跑 QA |
| A | `yakupatch/writeback.py`、`engines/base.py`、`engines/rpgmaker_mz.py` | W3 自動換行（`wrap_rule`、`fit`） |
| A | `yakupatch/core.py`、`gui/window.py`、`gui/models.py` | 全文 QA 按鈕、「不一致」篩選、風格編輯 |
| B | `yakupatch/dicts.py` | 外部字典：JMnedict、日本人名、VNDB、Wikidata、自備詞庫 |
| B | `yakupatch/extract.py`、`core.py`、`gui/settings.py`、`gui/window.py` | 術語種子帶字典建議；字典設定頁；查字典按鈕 |
| C | `yakupatch/vfs.py` | 封包成員路徑 `<封包>!<內部路徑>`；asar 讀取與只改標頭的寫回 |
| C | `yakupatch/backup.py` | 新增 `head`（只備份檔頭）與 `created`（還原時刪除）兩種備份 |
| C | `yakupatch/engines/tyrano.py` | TyranoScript：散檔、`resources/app/`、`app.asar` |
| D | `yakupatch/engines/renpy.py` | Ren'Py：讀 `.rpyc`（散檔或 `.rpa`），寫成新語言 `tl/yakupatch` |
| D | `yakupatch/vfs.py`、`engines/base.py`、`writeback.py`、`extract.py` | RPA 讀取；外掛介面新增 `decode`、`outputs` |
| 測試 | `tests/test_consistency.py`、`test_dicts.py`、`test_tyrano.py`、`test_renpy.py`、`test_gui.py` | 新增 14 個測試 |

## 邏輯摘要

- **T13 全文 QA**：每次翻譯結束後執行，也可以在 GUI 按「全文 QA」。檢查三種問題：
  - 術語表或已翻譯的角色名出現在原文，譯文卻沒有對應譯名。
  - 原文相同（忽略空白與標點），譯文相似度低於 0.6。
  - 同一檔案內，前後都已翻譯，夾在中間的句子失敗或不確定。
  
  有問題的句子標上 `inconsistent` 旗標，GUI 可以篩選出來。QA 寫在獨立的 run，不會出現在翻譯歷史。
- **style.md**：使用者在 GUI 寫翻譯風格，Codex 附在系統提示後面。與既有規則衝突時，以規則為準。
- **自動換行（W3）**：只用在引擎不會自己換行的文字（RPG Maker 對話）。寬度上限取原文每行寬度的第 99 百分位，至少要 20 行才計算。全形字算 1，半形字算 0.5，控制碼算 0。結尾標點可以超出行寬，不另起一行。
- **外部字典（§6.10）**：
  - 只查術語候選，不比對全文。
  - JMnedict 與日本人名可下載到本機。VNDB 與 Wikidata 只在使用者確認後才連網。
  - 字典給的譯名標成 `dict:` 建議，人工確認前不會用來約束翻譯。
- **Tyrano**：
  - 提取範圍：
    - 對話：同一則訊息遇到 `[p]`、`[cm]`、`[er]` 才結束。
    - `#名字` 行。
    - 白名單內的標籤屬性：`glink`／`button` 的 `text`、`ptext` 等。
  - 跳過的部分：
    - `[iscript]`、`[html]` 區塊與註解。
    - 以 `&`、`%` 開頭的屬性值，它們是運算式，不是文字。
  - 不改的部分：
    - 沒有翻譯的行原樣保留，包括空白、引號與 CRLF。
    - 譯文的換行改成 `[r]`。
    - 行首若是 `; * # @`，改成全形，避免被當成指令。
  - asar：不搬動資料區。
    - 改過的成員標為 `unpacked`，內容寫到 `app.asar.unpacked/`。
    - 標頭以等長的 JSON 重寫，不足的長度補空白。
    - 原本有 integrity 的成員會重算雜湊；標頭空間不夠時改為移除該欄位。
    - 撤銷某些譯文、讓成員回到原文時，對應的 unpacked 檔會一併刪除。
- **Ren'Py**：
  - 讀取 `.rpyc` 的 slot 2。這是 Ren'Py 自己的翻譯處理之後的腳本，對話已經包在 Translate 節點裡，並附有 Ren'Py 算好的 identifier。
  - 解 pickle 只允許基本型別與 `renpy.*` 的替身類別，不會執行遊戲程式碼。測試確認惡意 pickle 會被拒絕。
  - 寫回時不修改原腳本，只新增以下檔案：
    - `game/tl/yakupatch/*.rpy`：每一句的 translate 區塊。
    - `strings.rpy`：選項與角色名，全遊戲只出現一次。
    - `game/yakupatch_language.rpy`：設定 `config.language`。
  - 還原時刪除上述檔案，以及 Ren'Py 執行後自動編譯出的 `.rpyc`。
  - 遊戲已有中文 `tl/` 語言包時，語言關卡會停下。

## 測試結果

```
python -X utf8 -m unittest discover -s tests -t tests
Ran 72 tests ... OK
```

I2 結束時有 58 個測試，I3 新增 14 個。涵蓋以下項目：

- QA 植入同名異譯。
- 換行與行寬。
- style。
- 字典下載、匯入、VNDB、Wikidata；網路請求一律用假的 opener，不連真網路。
- Tyrano：
  - 解析、原樣寫回、引號切換、CRLF。
  - 合成 asar 只改標頭、寫入 unpacked、撤銷成員、還原後雜湊一致。
- Ren'Py：
  - 合成 rpyc 與 rpa、translate 區塊格式、拒絕惡意 pickle。
  - 寫回、再寫回時刪除不再需要的檔案（含 `.rpyc`）、還原後雜湊一致。

## 唯讀一致性檢查（真實遊戲，記憶體內）

- **Tyrano**：遊戲庫 19 款。每個 `.ks` 都做兩項檢查：用原文寫回要與原檔逐位元組相同；寫入標記後重新解析，標記要完整取回。兩項都是 0 失敗。
- **Ren'Py**：遊戲庫 10 款，Ren'Py 6.99 到 8.4 都有。所有 `.rpyc` 都能以受限 unpickler 載入；`.rpa` 內的腳本也能讀出。
- **Electron fuse**：asar 遊戲的 exe 都檢查過，有 asar integrity 驗證設定的 7 款都是關閉的（`EnableEmbeddedAsarIntegrityValidation` = 0）。

## 實機驗收

**選遊戲的條件**：原文是日文、遊戲沒有附中文語言包，語言探測的結果也必須是 `ja`。

- Tyrano 有 12 款符合，選 3 款，涵蓋散檔、asar 無 integrity、asar 有 integrity 三種形式。
- **Ren'Py 只用 1 款**：遊戲庫的 10 款 Ren'Py 裡，只有いもうとびより符合條件。其餘 9 款不是中文版，就是英文原作或附中文 `tl/`。驗收標準要求 3 款，這次以 1 款驗收。

**流程**：

1. 原始遊戲唯讀，完整複製到 `C:\YakuPatchTest\NN_<引擎>\`。
2. extract。
3. 用假譯文翻譯：假名換成「中文」，控制碼保留。
4. 植入 QA 測試案例：把一個角色名譯成「艾莉絲」，再在一句含該名字的對話前面加上「愛麗絲」。
5. 預覽、apply。
6. 從修改後的遊戲檔讀回，確認譯文生效。

腳本放在 session scratchpad 的 `e2e.py`，沒有提交。

| 資料夾 | 引擎 | 來源 | 記錄／翻譯 | 寫回檔／處 | 讀回 | QA 植入 |
|---|---|---|---|---|---|---|
| `11_tyrano` | Tyrano 散檔 `resources/app/` | `D:\Games\TEMP\2025-12-12\くノ一橘の身体を14日間の個人修練で敏感に開発しました。` | 681／665 | 28／958 | 958 處皆為譯文，殘留假名 0 | 抓到 |
| `12_tyrano` | Tyrano asar，無 integrity | `D:\Games\TEMP\2026-02-08\彼氏持ちJKの乳首を壊れるまでしつこ～く弄り倒すだけの夏期バイト。` | 90／84 | 8／87 | 87 處皆為譯文，殘留假名 0 | 無法植入（遊戲只有 1 個角色名，沒有對話提到它） |
| `13_tyrano` | Tyrano asar，有 integrity | `D:\Games\TEMP\2026-04-14\01571952` | 1,298／1,289 | 11／2,116 | 2,116 處皆為譯文，殘留假名 0 | 抓到 |
| `14_renpy` | Ren'Py 8.4.1，`.rpa` | `D:\Games\TEMP\2026-02-08\いもうとびより` | 154／147 | 新增 6 檔／160 處 | 見下 | 抓到 |

- **14_renpy 的 Ren'Py 檢查**：在複本上執行遊戲自帶的 Ren'Py。
  - `compile`：沒有錯誤，產生的 5 個 tl 檔都編譯成功。
  - `translate yakupatch --count`：「0 missing dialogue translations」，表示每句對話都對應到 Ren'Py 的 identifier。另有 316 條 string 未翻譯，是畫面 UI 的 `_()` 字串，這次沒有提取（見已知限制）。
- **啟動測試**：
  - 12_tyrano 換遊戲前，曾用舊的 asar 複本（原本就附中文的那款）啟動到標題畫面。
  - 現在 4 款的遊戲畫面由使用者親自檢查，**結果尚待使用者回報**。
- **還原**：備份在各遊戲資料夾內。使用者檢查完後，可用 `e2e.py restore` 還原，並和寫回前的雜湊比對。還原的正確性由單元測試涵蓋：Tyrano 與 Ren'Py 都驗證還原後雜湊與原檔一致。
- **曾經跑過、後來刪除的複本**：
  - 英文的 NTR'd by Clumsiness（Ren'Py 7.6.3）。
  - 中文版 STEINS.GATE DLC（Ren'Py 6.99）。
  
  這兩款每一行都以加前綴的假譯文寫回，`compile` 都沒有錯誤；NTR'd 的 `--count` 也是 0 句對話缺譯。它們不符合翻譯測試條件，只能證明 7.x 與 6.99 版的寫回格式可用，複本已刪除。

## 新增 shortcut 與已知限制

- **Ren'Py 說話者**：說話者是變數時（例如 `e`），不會換成角色名，speaker 欄位留空。名字仍會以 `name` 記錄提取並翻譯。
- **Ren'Py UI 字串**：畫面上的 `_()` 字串與 screen 文字不提取。要等有原始 `.rpy` 掃描，或解析 screen AST 時再補。
- **Ren'Py 帶參數的對話**：`e "..." (arguments)` 這類句子不翻譯，維持原文。
- **Ren'Py 6.99 以前**：沒有 slot 2 時退回 slot 1。這時沒有 identifier，對話不會被提取。
- **字型**：日文遊戲的字型可能缺部分繁體字。這次沒有自動檢查字型。
- **Tyrano 標籤屬性**：只翻白名單內的屬性。巨集自訂的文字參數不會提取。
- **asar 標頭空間**：重寫後的標頭比原本長，而且移除 integrity 也騰不出空間時，會拒絕寫回（不支援重建整個封包）。這次所有實測都沒有發生。

## Commit

| 階段 | Commit |
|---|---|
| A：全文 QA、style.md、自動換行 | c4e8d36 |
| B：外部字典 | 12cca99 |
| C：Tyrano 與 asar | ace5538 |
| D：Ren'Py | 899cd6e |
| 文件 | 見本報告所在 commit |
