# 專案企劃書：YakuPatch

> 開源、不注入的遊戲文本翻譯工具：提取遊戲文本 → 本地模型或 Codex 訂閱翻譯 → 直接寫回遊戲檔案，全程備份且可一鍵還原。

- 版本：v0.5（企劃草案，第五次疊代）
- 日期：2026-10-24
- 授權：GPL-3.0（理由見 §13）

**v0.5 變更摘要**
- 發佈方式改為參考 BallonsTranslator 的「安裝包一鍵啟動」：免設定壓縮包內含 Python 嵌入版，解壓後雙擊 `launch_win.bat` 即可啟動；另提供一行 PowerShell 安裝腳本。取代原本的 PyInstaller 單一執行檔（§1.2、§12、§15 I6）。

**v0.4 變更摘要**
- 新增語言預檢（L1）：遊戲已是中文或內建中文語言包時不翻譯；可分辨繁體與簡體。已用使用者遊戲庫實測，抽得到文字的 102 款中有 36 款已是中文（§5.1）。
- **不做繁簡轉換**：Sakura 譯文維持簡體；移除簡轉繁步驟，OpenCC 只用於辨識（F7、T12）。
- Sakura 設定參考 BallonsTranslator：API 位址（可填區網 IP）、模型版本、術語表、重試、逾時、取樣參數等；自動啟動時預設只監聽 `127.0.0.1`（§6.7）。
- 使用者介面設計說明：版面、各分頁、設定、首次啟動精靈、提示與錯誤（§11）。

**v0.3 變更摘要**
- 交換檔改為「快照 + 日誌」寫入，並由單一寫入者負責；大型遊戲不必每批重寫整檔，GUI 可邊翻邊編輯，也能撤銷整次翻譯（§4.6）。
- 一致性：提取時自動產生術語種子（E10）；名稱類先翻（T1）；翻完做全文 QA（T13）。
- 每款遊戲可設定翻譯風格 `style.md`；Codex 開跑前先預估用量（§6.8）。
- 新增設計原則 P6「遊戲檔案視為不可信資料」與對應的解包安全規則（§3.6）。
- Unity 字型替換提前到 I4；Plugin 合規測試套件；成功指標；交換檔格式遷移規則。
- 外部字典：使用者選擇下載後轉成本地詞庫，只用於術語候選的辨識、讀音與既有譯名（§6.10）。
- G3、G4 已用實際資料驗證：MZ 提取規格以 46 款遊戲核對並補上遺漏項目（§17.1）；佔位符改用實測最佳的 `<n>`（§17.2）；JMnedict 授權已查證。
- 設計缺口清單 G1–G9（§17）：遊戲指紋、更新後不還原（修正 §9 的降版問題）、MZ 提取規格、佔位符實測、評測集、存檔相容、工作佇列、診斷包。

**v0.2 變更摘要**
- 全案改為「提取 → 翻譯 → 寫回」三段，以**交換檔**為分界（§3、§4）。
- 提取、翻譯、寫回各自拆成單元管線（§5、§6、§7）。
- 新增 Codex 訂閱翻譯後端，參考 BallonsTranslator 的設計（§6.6）。
- 後端差異改由能力描述 `BackendCaps` 表達，管線內不針對個別後端寫分支判斷（§6.5）。
- 里程碑改為垂直切片疊代，每一輪都能端到端使用（§15）。

---

## 1. 背景與目標

### 1.1 問題
| 工具 | 方式 | 限制 |
|---|---|---|
| MTool | 注入／Hook 執行中的遊戲 | 閉源；易被防毒或遊戲更新破壞；譯文不落地 |
| LunaTranslator | Hook／OCR 擷取，另開視窗顯示 | 譯文不進遊戲畫面；每次遊玩都要掛著 |

### 1.2 目標
1. **不注入**：只讀寫遊戲檔案，遊戲照原本方式啟動。
2. **提取與翻譯分離**：提取結果是純文字交換檔，任何翻譯方式（本地模型、Codex、人工、外部工具）都能讀寫。
3. **翻譯後端可選**：預設本地 Sakura（`D:\AI\OneClickLLAMA_NV_20240429`），離線免費；也可選 Codex 訂閱（ChatGPT 帳號登入），品質較高。
4. **直接替換**：譯文寫回遊戲原本的檔案格式。
5. **可還原**：修改前自動備份，一鍵還原並驗證雜湊。
6. **自動匹配**：自動辨識引擎、找出需修改的檔案、產生替換檔。
7. **處理編碼**：自動偵測與轉換，避免亂碼與遊戲崩潰。
8. **圖形介面**：一般玩家不需要碰命令列。
9. **下載即用**：最終交付為可下載的 Windows 安裝包，參考 BallonsTranslator：從 GitHub Releases 下載免設定壓縮包，解壓後雙擊 `launch_win.bat` 即可啟動，不需自行安裝 Python 或任何套件；也可用一行 PowerShell 指令自動安裝（§12、§15 I6）。

### 1.3 非目標（第一版不做）
- 圖片內文字（OCR／重繪）、語音。
- 執行期 Hook、記憶體修改。
- 破解加密／DRM 或繞過授權驗證。
- 散佈遊戲原始檔案。

### 1.4 設計原則
所有設計決策以這五條為準：

| # | 原則 | 具體規定 |
|---|---|---|
| P1 | **三段分離、以檔案為邊界** | 提取只讀遊戲；翻譯只讀寫交換檔；寫回只寫遊戲（先備份） |
| P2 | **單元單一職責** | 每個單元有固定的輸入／輸出；邏輯單元寫成純函式；I/O 只出現在邊界單元（讀取、寫入、連線、提交） |
| P3 | **差異用描述，不用分支** | 引擎差異放在 `EnginePlugin`，後端差異放在 `BackendCaps`；管線程式碼裡不出現 `if engine ==`、`if backend ==` |
| P4 | **失敗只降級，不破壞** | 任何驗證失敗都保留原文並標記，不寫入可疑內容 |
| P5 | **可重現、可追溯** | 每筆譯文記錄後端、模型、提示詞版本；遊戲檔案記錄雜湊 |
| P6 | **遊戲檔案視為不可信資料** | 只解析、不執行；解包時防止路徑跳脫；不使用不受限制的反序列化（§3.6） |

---

## 2. 現況調查

### 2.1 使用者遊戲庫引擎分布
以檔案特徵掃描 `D:\Games\TEMP` 與 `G:\game` 共 **365** 個遊戲資料夾（深度 4 層，屬於估計）：

| 引擎 | 數量 | 文本位置（典型） | 難度 |
|---|---:|---|---|
| Unity（Mono） | 88 | `*_Data/*.assets` 內 TextAsset／MonoBehaviour；`Assembly-CSharp.dll` 字串 | 高 |
| Unity（IL2CPP） | 52 | 同上；硬編碼字串在 `global-metadata.dat` | 高 |
| RPG Maker MV | 56 | `www/data/*.json` | 低 |
| RPG Maker MZ | 42 | `data/*.json` | 低 |
| KiriKiri | 28 | `*.xp3` 內 `.ks`／`.tjs`（UTF-16LE 或 Shift-JIS） | 中（部分加密） |
| TyranoBuilder | 21 | `data/scenario/*.ks`，或封裝在 `app.asar`／`package.nw` | 低～中 |
| Ren'Py | 9 | `game/*.rpy`、`*.rpyc`、`*.rpa` | 中 |
| Artemis | 5 | `*.pfs` 內 `.ast`／`.lua` | 中 |
| Godot | 5 | `*.pck` | 中 |
| Wolf RPG | 5 | `Data.wolf`／`Data/*.wolf`（多數加密） | 中～高 |
| NW.js／Electron（其他） | 5 | `package.nw`／`app.asar` 內 JS／JSON | 中 |
| CatSystem2 | 4 | `*.int` 內 `.cst` | 中 |
| Unreal | 2 | `*.pak` 內 `.locres` | 高 |
| SRPG Studio、NScripter | 各 1 | `data.dts`；`nscript.dat` | 中 |
| 未識別 | 39 | 含 YU-RIS（`.ypf`）、BGI（`.arc`+`._bp`）、GameMaker（`data.win`）等 | 高 |

**結論**：RPG Maker MV/MZ + Unity 佔 238／365（65%）。RPG Maker 數量多且格式最單純，用來驗證整體架構（I0、I1）；Unity 數量最多但每款遊戲格式不同，等架構穩定後再做（I4，§15）。

### 2.2 已觀察到的編碼問題
- 遊戲資料夾名稱是日文，系統地區碼為 CP950；用非 Unicode API 讀取會變亂碼（掃描時已實際觸發 `UnicodeEncodeError`）。→ 全程使用 Unicode 路徑 API，所有輸出一律 UTF-8。
- 舊引擎（KiriKiri 舊作、BGI、YU-RIS、NScripter、Wolf 舊版）多使用 CP932（Shift-JIS），無法直接寫入大量中文字。

### 2.3 參考工具
| 工具 | 授權 | 參考內容 |
|---|---|---|
| MTool（`G:\game\MTool`） | 閉源 | 只參考行為：依引擎分 loader、譯文表格編輯、匯出入流程；不反編譯 |
| LunaTranslator（`D:\AI\LunaTranslator`） | GPL-3.0 | `translator/sakura_base.py` 的 Sakura 提示詞與術語表格式 |
| BallonsTranslator（`D:\AI\Ballonstranslator_win_minium`） | GPL-3.0 | `modules/codex.py`：ChatGPT OAuth 登入、token 存系統憑證庫、Responses 串流、錯誤分類；`llm_translation_contract.py`：以 JSON Schema + id 對應譯文 |
| OneClickLLAMA | — | `01_2K_NP8.bat` 啟動 `llama-server`：8 個平行槽、每槽 2048 context、Sakura 7B、埠 8080 |

---

## 3. 整體設計

### 3.1 三段流程
```
            ┌──────────── A. 提取（§5）────────────┐
 遊戲資料夾 ─┤ 只讀遊戲；解包、解碼、解析、分類、去重 ├─→ 交換檔（§4）
 （唯讀）    └──────────────────────────────────────┘     manifest.json
                                                        strings.jsonl
            ┌──────────── B. 翻譯（§6）────────────┐        ▲ │
            │ 只讀寫交換檔；三種來源可混用：        │ ───────┘ │
            │  ① 內建管線 + Sakura 本地             │          │
            │  ② 內建管線 + Codex 訂閱              │          │
            │  ③ 匯出分批檔 → 外部工具／人工 → 匯入 │          │
            └──────────────────────────────────────┘          │
            ┌──────────── C. 寫回（§7）────────────┐          │
 遊戲資料夾 ◄┤ 換行、編碼檢查、渲染、驗證、備份、原子寫入├◄─────────┘
 （已翻譯）  └──────────────────────────────────────┘
            還原（§9）：依備份清單復原並驗證雜湊
```

三段之間只透過交換檔溝通，好處：
- 任一段可以單獨重跑（例如換模型重翻，不必重新提取）。
- 翻譯可以交給任何工具；只要符合交換檔格式就能匯入。
- 交換檔本身就是可分享的譯文補丁，不含遊戲原檔（F15）。

### 3.2 功能清單
| 編號 | 功能 | 所在段 | 優先 |
|---|---|---|---|
| F1 | 引擎自動偵測（特徵比對，給信心分數） | A | P0 |
| F2 | 文本提取（每種引擎一個 Plugin） | A | P0 |
| F3 | 交換檔與重新提取時的合併（保留既有譯文） | A | P0 |
| F4 | 控制碼保護（佔位符遮蔽，翻譯後還原並驗證） | B | P0 |
| F5 | 翻譯後端：Sakura 本地、Codex 訂閱、任意 OpenAI 相容端點 | B | P0 |
| F6 | 匯出分批檔／匯入外部譯文（走同一套驗證） | B | P0 |
| F7 | 語言預檢與繁簡辨識：遊戲已是中文（簡或繁）或內建中文語言包時不翻譯；**不做任何繁簡轉換**，譯文維持後端輸出的字體（§5.1） | A、B | P0 |
| F8 | 寫回、備份與還原（雜湊驗證、原子寫入） | C | P0 |
| F9 | 編碼自動偵測與寫回策略 | A、C | P0 |
| F10 | 圖形介面 | 全 | P0 |
| F11 | 翻譯快取與續跑 | B | P1 |
| F12 | 術語表（人名、地名） | B | P1 |
| F13 | 自動換行與長度控制 | C | P1 |
| F14 | 場景摘要記憶（長上下文後端用，提升人稱與語氣一致性） | B | P2 |
| F15 | 譯文補丁分享（只含交換檔） | — | P2 |
| F16 | 批次處理多個遊戲 | 全 | P2 |
| F17 | 一致性：術語種子（E10）、名稱類先翻（T1）、全文 QA（T13） | A、B | P1 |
| F18 | 翻譯風格設定 `style.md`（人稱、敬語、語氣、專有名詞規則） | B | P1 |
| F19 | 雲端後端用量預估（開跑前顯示句數、批次、預估 token 與時間） | B | P1 |
| F20 | 字型替換（遊戲字型缺中文字時；Unity TMP 必需） | C | P1 |
| F21 | 撤銷整次翻譯（依日誌回到該次之前） | B | P1 |
| F22 | 外部字典：使用者選擇下載後，匯入本地詞庫，用於辨識專有名詞、提供讀音與既有中文譯名（§6.10） | A、B | P1 |

### 3.3 非功能需求
- Windows 10／11 x64 優先；支援 Unicode 與長路徑。
- 寫入採「暫存檔 → 驗證 → 原子替換」，崩潰不會毀損遊戲或交換檔。
- 預設不連外網；選擇 Codex 等雲端後端時，介面明示「文本會送到 OpenAI」。
- 日誌不記錄遊戲文本、token、OAuth 代碼，只記錄雜湊與數量。
- 吞吐目標：Sakura 7B 每小時約 1 萬句（實測後修正）。

### 3.4 模組架構
```
┌──────────────────────── GUI（PySide6）────────────────────────┐
│ 遊戲庫 │ 文本表格 │ 翻譯進度 │ 套用／還原 │ 設定（後端/登入/編碼） │
└───────────────┬──────────────────────────────────────────────┘
                │ 只呼叫 Core API
┌───────────────▼───────────── Core ────────────────────────────┐
│ extract/   提取管線 E1–E11 ──→ exchange/（單一寫入者，§4.6）    │
│ translate/ 翻譯管線 T1–T13 ←→ exchange/                        │
│            backends/  sakura、codex、openai_compat（BackendCaps）│
│ writeback/ 寫回管線 W1–W7  ──→ backup/（備份、還原）            │
│ encoding/  偵測、轉換、無法編碼字元處理                          │
└───────────────▲──────────────────────────────────────────────┘
                │ 實作介面
        engines/  每種引擎一個 EnginePlugin（§3.5）
```

### 3.5 Engine Plugin 介面
Plugin 只負責「格式相關」的部分；解包流程、解碼、分類、去重、驗證、備份都由 Core 統一處理。

```python
class EnginePlugin:
    name: str
    version: str                              # 版本變更會觸發重新提取時的合併
    default_encoding: str                     # 偵測不出來時的預設編碼
    control_codes: list[str]                  # 控制碼正規式（遮蔽與驗證共用）

    def detect(self, game_dir) -> float: ...                  # 0~1 信心分數
    def language_packs(self, vfs) -> list[LangPack]: ...      # 遊戲內建的語言包（§5.1），沒有就回傳空清單
    def containers(self, game_dir) -> list[Container]: ...    # 需要解包的封包
    def locate(self, vfs) -> list[VPath]: ...                 # 哪些檔案含文本
    def parse(self, vpath, text) -> Iterable[Segment]: ...    # 解析出片段與定位
    def render(self, vpath, text, repl: dict[Loc, str]) -> str: ...  # 以譯文替換
    def wrap_rule(self, kind) -> WrapRule | None: ...         # 每行字數上限等

class Container:                              # xp3 / rpa / asar / assets / pck ...
    def list(self) -> list[str]: ...
    def read(self, path) -> bytes: ...
    def write(self, changes: dict[str, bytes]) -> list[Path]: ...  # 優先產生覆蓋包，不改原封包
```

`Segment` 欄位：`loc`（檔內定位：JSON 路徑、行號、資源 PathID）、`kind`（dialogue／choice／name／system／ui／other）、`text`、`speaker`、`group`（同一則訊息的多行共用）。

### 3.6 安全規則（P6）
遊戲來源不一定可信，Core 統一實施以下規則，Plugin 不能繞過：

| 風險 | 規則 |
|---|---|
| 封包內檔名路徑跳脫（`../`、絕對路徑、磁碟代號） | `Container` 讀出的路徑一律正規化並確認落在遊戲資料夾內，否則拒絕該檔案 |
| 解壓縮炸彈 | 單檔與總解包大小設上限，超過即停止並回報 |
| Ren'Py `.rpyc` 是 pickle 格式 | 只用受限的反序列化器（只允許 Ren'Py AST 類別，與 `unrpyc` 相同做法）；不使用 `pickle.load` |
| 執行遊戲程式碼 | 不執行任何遊戲的腳本或執行檔；`plugins.js`、`.tjs` 只做文字解析 |
| 寫入範圍 | 寫回只允許寫入遊戲資料夾與 `_yakupatch_backup/`；不會修改其他位置 |

---

## 4. 交換檔（三段之間的唯一契約）

### 4.1 位置與檔案
```
projects/<遊戲資料夾名>-<遊戲指紋8碼>/   遊戲指紋見 §17 G1，搬移資料夾不會失去對應
  manifest.json        遊戲與提取資訊
  strings.jsonl        全部文本的快照
  journal.jsonl        快照之後的變更日誌（§4.6）
  glossary.tsv         術語表：原文<TAB>譯文<TAB>備註<TAB>來源（seed／manual）
  style.md             翻譯風格（人稱、敬語、語氣、專有名詞規則；F18）
  batches/out/         匯出給外部工具的分批檔
  batches/in/          外部工具填好、待匯入的分批檔
```
共同規則：UTF-8 無 BOM、LF 換行、一行一筆 JSON（`ensure_ascii=False`）、依原檔出現順序排序（方便 diff 與 git）；每次儲存都用暫存檔加原子替換。

### 4.2 manifest.json
```json
{
  "format": "yakupatch-strings/1",
  "game_dir": "G:/game/Summer Memories",
  "engine": "rpgmaker-mz", "plugin_version": "1.0.0",
  "source_lang": "ja", "target_lang": "zh-Hans",
  "language_probe": {"primary": "ja", "kana_ratio": 0.31, "simp_only": 2, "trad_only": 1, "packs": []},
  "files": {
    "data/Map001.json": {"sha256": "…", "encoding": "utf-8", "bom": false, "newline": "\n"}
  }
}
```
`files` 記錄每個來源檔的雜湊、編碼、BOM、換行風格；寫回時用來確認遊戲沒有變動，並以相同格式輸出。

### 4.3 strings.jsonl（一行一筆）
```json
{"id":"3f9a1c0b7d2e","kind":"dialogue","speaker":"リリア","src":"\\C[2]こんにちは\\C[0]","dst":"\\C[2]你好\\C[0]","status":"translated","by":"codex:gpt-x/p2","refs":["data/Map001.json#/events/3/pages/0/list/12-14"],"flags":[],"note":""}
```

| 欄位 | 說明 |
|---|---|
| `id` | `sha1(kind + "\0" + src)` 前 12 碼；同一句原文只會有一筆，碰撞時加長 |
| `kind`、`speaker` | 提取時產生，給翻譯當上下文 |
| `src`／`dst` | 原文／譯文（譯文保留原始控制碼，不含佔位符） |
| `status` | `pending`、`skip`、`translated`、`failed`、`reviewed`、`orphan` |
| `by` | 譯文來源：`後端:模型/提示詞版本`、`import:檔名`、`manual` |
| `refs` | 所有出現位置，寫回時依此替換 |
| `flags` | `overlong`、`unencodable`、`uncertain`、`inconsistent` 等，供介面篩選 |
| `overrides` | 選填。同一句原文在特定位置需要不同譯文時使用，例如 `{"data/Map003.json#/…": "…"}`；寫回時優先於 `dst` |

### 4.4 分批交換檔（交給外部工具）
- 匯出 `batches/out/part-0001.jsonl`：每行 `{"id","src","speaker","ctx"}`，`src` 已遮蔽控制碼（`<0>`），每檔預設 200 句。
- 同時產生 `batches/out/INSTRUCTIONS.md`：說明填寫規則（只填 `dst`、保留 `<n>`、不增減行），可直接丟給 Codex CLI、ChatGPT 或人工。
- 匯入 `batches/in/*.jsonl`：比對 `id` 存在且 `src` 雜湊一致（防止舊檔覆蓋新提取），通過 §6.3 驗證才寫入，`by` 記為 `import:檔名`。

### 4.5 重新提取的合併規則
遊戲更新或 Plugin 升級後重新提取：
- 相同 `id`：保留 `dst`、`status`，更新 `refs`。
- 新的 `id`：`pending`。
- 消失的 `id`：改成 `orphan`，不刪除（遊戲改回舊版時可直接沿用）。
- `reviewed` 永遠不被自動流程覆蓋。

### 4.6 寫入模型：快照 + 日誌
十萬句的遊戲，`strings.jsonl` 可能有數十 MB，每批都重寫整檔太慢；GUI 編輯和翻譯同時進行時，也會互相覆蓋。因此：

| 規則 | 說明 |
|---|---|
| 單一寫入者 | 所有修改（翻譯結果、匯入、手動編輯）都送進同一個 `Store`，由一條寫入執行緒依序處理；其他元件不直接寫檔 |
| 只追加日誌 | 每筆修改追加一行到 `journal.jsonl`：`{"run","id","field","old","new","by","ts"}`，寫入後 `fsync` |
| 讀取 | 快照 + 依序重播日誌 = 目前狀態；日誌最後一行不完整（斷電）時捨棄該行 |
| 壓縮 | 翻譯結束、程式關閉、或日誌超過 1 萬行時，產生新快照（原子替換）並清空日誌 |
| 撤銷整次翻譯（F21） | 每次執行有一個 `run` 編號；依日誌把該次的修改還原成 `old`，產生反向記錄，不刪除歷史 |

### 4.7 格式版本
- `manifest.format` 帶主版本號（`yakupatch-strings/1`）。
- 只新增選填欄位時不升版；讀取時忽略不認得的欄位。
- 刪除或改變欄位意義時升主版本，並在 `exchange/migrations/` 提供 `1→2` 遷移；遷移前自動備份舊檔。

---

## 5. 提取管線（A 段）

只讀遊戲，輸出交換檔。每個單元的輸入與輸出如下：

| # | 單元 | 輸入 → 輸出 | 職責 | 不負責 |
|---|---|---|---|---|
| E1 | **Detector** | 遊戲資料夾 → Plugin + 信心分數 | 執行所有 Plugin 的 `detect`；低於門檻就回報不支援，並附上原因（例如封包加密） | 解析內容 |
| E2 | **Unpacker** | 封包 → 虛擬檔案系統（VFS） | 透過 `Container` 唯讀列出／讀取封包內檔案；記錄來源鏈（`game.xp3!scenario/a.ks`） | 寫入封包 |
| E3 | **Locator** | VFS → 文本檔清單 | 呼叫 `plugin.locate`；遊戲規則檔（§10）可以追加或排除 | 讀內容 |
| E4 | **Decoder** | bytes → 文字 + 編碼資訊 | 依 §8.1 判斷編碼，記錄 BOM 與換行風格到 manifest，確保寫回時可以逐位元組重現 | 修改內容 |
| E5 | **Parser** | 文字 → `Segment` 列表 | 呼叫 `plugin.parse`，取得片段與檔內定位 | 判斷要不要翻 |
| L1 | **LanguageGate** | `Segment` 樣本 + 語言包 → 遊戲語言判定 | 依 §5.1 判斷遊戲主要語言與繁簡；已是中文或有中文語言包時停止流程並回報，不產生待翻條目 | 逐句判斷（E6 做） |
| E6 | **Classifier** | `Segment` → 要翻／`skip`／`uncertain` | 規則判斷：依 `kind` 白名單；排除空字串、純數字／符號、檔名路徑、腳本識別字（`snake_case`、`$var`）、已是中文（依 §5.1 的逐句規則：**只有漢字沒有假名的句子，預設仍視為日文**）；不確定時標 `uncertain` 而不是丟掉。**`uncertain` 預設不翻**（例如 `plugins.js` 參數，翻錯可能讓遊戲崩潰），由使用者在表格中勾選後才進入翻譯 | 修改文字 |
| E7 | **Segmenter** | 多個片段 → 翻譯單位 | 合併同一 `group` 的多行（如 RPG Maker 連續 401 指令），保留原換行位置，讓寫回時能拆回 | 換行重排 |
| E8 | **Annotator** | 翻譯單位 → 加上 `speaker`、`kind`、順序 | 補上下文資訊 | 翻譯 |
| E9 | **Deduper** | 翻譯單位 → 記錄（依 `id`） | 相同原文合併成一筆，累積 `refs` | — |
| E10 | **GlossarySeeder** | 記錄 → 術語候選 | 收集 `kind=name` 的條目（角色、道具、技能、地名）與所有 `speaker`，寫入 `glossary.tsv`，來源標為 `seed`；有本地詞庫時查詢讀音與既有中文譯名一併填入（§6.10）；T1 會先翻仍無譯名者，人工確認後成為正式術語 | 翻譯術語 |
| E11 | **Merger + Exporter** | 記錄 + 既有交換檔 → 新交換檔 | 依 §4.5 合併，原子寫出 manifest 與 strings.jsonl；既有的手動術語不覆蓋 | — |

E3 到 E10 與 L1 都是純轉換；檔案 I/O 只發生在 E2（讀）與 E11（寫）。

### 5.1 語言預檢與繁簡辨識
**為什麼要先檢查**：實測使用者遊戲庫，抽得到文字的 102 款中，**36 款已經是中文版**（簡體 27、繁體 9）。這些遊戲若再翻譯，只會浪費時間並可能改壞譯文。

**遊戲層級判定（L1）**：從解析後的片段抽樣（至少 2,000 字，優先取 dialogue），計算：

| 指標 | 算法 |
|---|---|
| 假名比例 | 假名數 ÷（假名數 + 漢字數） |
| 簡體專有字數 | 樣本中不重複的漢字裡，經 OpenCC 簡→繁會改變的字數（只存在於簡體） |
| 繁體專有字數 | 經 OpenCC 繁→簡會改變的字數（只存在於繁體） |

| 判定 | 條件 | 處理 |
|---|---|---|
| 日文 | 假名比例 > 5% | 正常翻譯 |
| 簡體中文 | 假名比例 ≤ 5%，且簡體專有字 > 繁體專有字 × 2 | **不翻譯**，標示「已是簡體中文」 |
| 繁體中文 | 假名比例 ≤ 5%，且繁體專有字 > 簡體專有字 × 2 | **不翻譯**，標示「已是繁體中文」 |
| 中文（無法分辨繁簡） | 假名比例 ≤ 5%，兩者差距不足 2 倍 | **不翻譯**，標示「已是中文」 |
| 混合（部分漢化） | 日文判定，但有 10% 以上的句子被逐句判定為中文 | 只翻日文句子；介面顯示中文句比例 |
| 抽不到樣本 | 少於 50 字 | 不擋，進入 E6 逐句判斷 |

實測時，兩邊的差距都非常明顯：例如簡體專有字 862、繁體專有字 0；繁體的例子則是簡 7、繁 341。

**內建中文語言包（`plugin.language_packs`）**：遊戲本身有中文版、只是沒有切換時，提示使用者切換的方法，不翻譯。**必須由各引擎模組判斷，不能只靠檔名**。實測發現，用全域的檔名規則會大量誤判：幾乎每款 NW.js 遊戲都有瀏覽器核心自帶的 `locales/zh-CN.pak`，音效檔 `SC_Foot.ogg` 也會被誤判為簡中。各引擎的判斷依據：

| 引擎 | 依據 |
|---|---|
| Ren'Py | `game/tl/` 下的 `schinese`、`tchinese`、`chinese` 等資料夾 |
| Unity | I2 Localization 的語言清單、`StreamingAssets` 下的語言資料夾或檔案 |
| Godot | 含 `zh` 語系的 `.translation` 檔 |
| RPG Maker MV/MZ | 多語言插件設定（`plugins.js` 中的語言清單）、`data_zh` 這類替代資料夾；**排除** `locales/*.pak` |
| 其他 | 該引擎模組自行定義；沒有定義時回傳空清單 |

**逐句判定（E6）**：
- 含假名 → 日文。
- 不含假名，但含簡體或繁體專有字，或含中文特有虛詞（的、了、吗、呢、們、這）→ 中文，標 `skip`。
- **只有漢字、沒有上述證據**（例如人名「田中」、技能名「魔法陣」）→ 視為日文，照常翻譯。避免把日文的漢字名詞誤判成中文而漏翻。

**使用者可覆寫**：任何判定都可以在介面按「仍要翻譯」改為正常翻譯。

---

## 6. 翻譯管線（B 段）

只讀寫交換檔。三種來源（Sakura、Codex、外部匯入）共用同一套前處理、驗證與後處理。

### 6.1 單元總覽
```
strings.jsonl
  │
 T1 Selector → T2 Normalizer → T3 Masker → T4 CacheLookup ──命中─────────────┐
                                              │未命中                         │
                     ┌────────────────────────┴───────────────┐               │
                     │ 內建後端                                │ 外部工具       │
       T5 ContextBuilder → T6 GlossaryMatcher         匯出 batches/out         │
       T7 Batcher → T8 PromptBuilder                   （人工／Codex CLI）      │
       T9 Dispatcher ⇄ BackendClient                  匯入 batches/in          │
       T10 ResponseParser                                    │                 │
                     └────────────────────┬──────────────────┘                 │
                           T11 Validator ──失敗──→ 降級重試（§6.4）            │
                                  │通過                                        │
                           T12 PostProcessor ◄─────────────────────────────────┘
                                  │
                           Writer：送進 Store（§4.6）+ 寫入快取（每批提交）
                                  │ 全部批次完成後
                           T13 ConsistencyQA：跨句檢查，標記 inconsistent
```

| # | 單元 | 輸入 → 輸出 | 職責 | 不負責 |
|---|---|---|---|---|
| T1 | **Selector** | 交換檔 → 工作清單 | 選出 `pending`，以及可重試的 `failed`；跳過 `skip`、`reviewed`、`orphan`、未勾選的 `uncertain`。**依序分兩階段**：先翻術語種子與 `name`／`system`，寫入術語表後，再翻 `choice`／`dialogue`，讓對話中的人名與介面一致 | 判斷要不要翻（已在 E6 做完） |
| T2 | **Normalizer** | 文字 → 文字 + 前後空白 | 剝除前後空白另存；統一換行為 `\n` | 控制碼 |
| T3 | **Masker** | 文字 → 文字 + 佔位符對照 | 依 `plugin.control_codes` 換成 `caps.placeholder`（預設 `<n>`，§17 G4 實測選定）；相鄰控制碼合併成一個；原文本身就含 `<數字>` 字樣時，該句改用其他樣式，避免混淆 | 翻譯 |
| T4 | **CacheLookup** | 遮蔽後文字 → 命中或未命中 | 鍵 = 雜湊(遮蔽後原文 + 後端 + 模型 + 提示詞版本 + 術語表版本)；查專案快取 → 全域快取 | 寫入快取 |
| T5 | **ContextBuilder** | 記錄 → 上下文 | 取前 N 句（已翻譯的用譯文），加上說話者；`caps.summary` 為真時附上場景摘要（F14） | 截斷 token |
| T6 | **GlossaryMatcher** | 文字 → 命中的術語 | 只挑出本句出現的術語（長詞優先） | 建立術語表 |
| T7 | **Batcher** | 工作清單 → 批次 | 依 `caps` 的 token 預算與句數上限分批；同檔案的連續句放同一批 | 平行處理 |
| T8 | **PromptBuilder** | 批次 → 請求內容 | 依 `caps.io_format` 選模板：`lines`（Sakura，一句一行）或 `json_ids`（Codex，帶 id 的 JSON Schema）；`caps.style` 為真時把 `style.md` 放進固定指令；模板有版本號 | HTTP |
| T9 | **Dispatcher** | 請求 → 回應 | 平行數依 `caps.max_parallel`；依錯誤類別處理（§6.4）；支援暫停、取消、回報進度 | 解析內容 |
| T10 | **ResponseParser** | 回應 → 每句譯文 | `lines`：依換行拆回，行數不符則整批失敗；`json_ids`：依 id 對應，缺少或多出的 id 視為該句失敗 | 判斷好壞 |
| T11 | **Validator** | 譯文 → 通過／錯誤清單 | §6.3 的逐項檢查 | 修正譯文 |
| T12 | **PostProcessor** | 譯文 → 最終 `dst` | 依序：還原佔位符 → 標點正規化 → 補回空白。**不做繁簡轉換**，譯文維持後端輸出的字體 | 換行（屬於 W3） |
| — | **Writer** | 結果 → Store | 每批完成即送進 Store（§4.6），帶 `run` 編號與 `by`；通過驗證的才寫入快取 | — |
| T13 | **ConsistencyQA** | 全部記錄 → 旗標 | 全部批次完成後執行的跨句檢查（§6.9）；只標記 `inconsistent`，不改譯文 | 修正譯文 |
| — | **Orchestrator** | 設定 → 執行 | 開跑前做用量預估（§6.8）；串接單元、處理狀態轉移；本身不含翻譯邏輯 | — |

### 6.2 工作資料
管線中只傳遞這個結構，不使用其他隱性狀態：

```python
@dataclass
class Work:
    id: str                      # 對應 strings.jsonl 的 id
    src: str                     # 原文
    text: str                    # 管線中目前的文字
    masks: dict[str, str]        # "<0>" → "\\C[2]"
    pad: tuple[str, str]         # 剝除的前後空白
    context: list[str]
    speaker: str | None
    terms: dict[str, str]
    result: str | None
    errors: list[str]
    attempts: int
```

### 6.3 Validator 檢查項目
每項是獨立的函式，可以個別開關；內建後端與外部匯入共用。

| 檢查 | 失敗條件 |
|---|---|
| 佔位符完整 | `<n>` 的數量、編號與原文不同 |
| 非空 | 原文非空，譯文卻是空的 |
| 長度比 | 譯文長度 / 原文長度不在 0.3～3.0 之間（可調）；原文一半以上是片假名（外來語、道具名）時不檢查下限 |
| 殘留日文 | 假名比例超過 30%（擬聲詞白名單除外） |
| 重複退化 | 同一片段連續重複 4 次以上 |
| 多餘內容 | 出現「翻譯：」「以下是」等模型說明文字 |
| 換行數 | 原文有 `\n` 時，譯文的換行數必須相同 |

### 6.4 失敗處理
**錯誤分類**（由 BackendClient 轉成統一類別，Dispatcher 只看類別）：

| 類別 | 例子 | Dispatcher 的處理 |
|---|---|---|
| `Transient` | 逾時、5xx、429 | 指數退避後重試，最多 5 次 |
| `TooLong` | 超出 context、輸出被截斷 | 把該批拆成兩半重送 |
| `AuthRequired` | 401（自動更新 token 後仍失敗） | 暫停整個工作，請使用者重新登入；進度保留 |
| `UsageLimit` | 訂閱額度用完 | 暫停整個工作並顯示訊息，不重試，避免連續送出被拒的請求 |
| `ConfigError` | 模型不可用、參數被拒 | 停止，請使用者改設定 |
| `Refused` | 模型拒絕翻譯 | 拆小批送一次；仍拒絕的句子標 `failed`，不再重送 |

**驗證失敗的降級順序**：
```
整批失敗 → 拆半重送（直到單句）
單句失敗 → 去掉上下文重送 → 提高 temperature 重送一次 → 標 failed（保留原文，介面標紅）
```

**狀態轉移**：
```
pending ─E6─→ skip
pending ─T4 命中─→ translated
pending → 送出 ─通過─→ translated ─使用者修改─→ reviewed
                └─失敗─→ 降級重試 ─→ failed
```

### 6.5 後端能力描述
管線只讀這個結構，新增後端只需實作 Client 並填寫能力描述：

```python
@dataclass
class BackendCaps:
    io_format: Literal["lines", "json_ids"]
    max_input_tokens: int        # 每批輸入的 token 預算
    max_items: int               # 每批句數上限
    max_parallel: int
    output_script: Literal["zh-Hans", "zh-Hant"]   # 後端實際輸出的字體，只做紀錄，不轉換
    summary: bool                # 是否支援場景摘要記憶
    style: bool                  # 是否能遵循 style.md 這類自由格式指令
    metered: bool                # 是否計量（雲端額度），為真時開跑前做用量預估
    placeholder: str             # 佔位符樣式，Sakura 實測選定 "<{n}>"（§17 G4）
```

| 後端 | io_format | 每批預算 | 平行數 | 輸出 | 摘要 | 風格 | 計量 |
|---|---|---|---|---|---|---|---|
| Sakura（本地） | `lines` | 約 600 token／16 句（單槽 2048 context） | 讀 `/props` 取得的槽數（目前為 8） | zh-Hans（不轉換） | 否 | 否（Sakura 只吃固定模板） | 否 |
| Codex（訂閱） | `json_ids` | 約 4000 token／80 句（起始值） | 預設 4，可調 | 預設 zh-Hant（提示詞指定，可改 zh-Hans；不轉換） | 是 | 是 | 是 |
| 其他 OpenAI 相容 | 可設定 | 可設定 | 可設定 | 可設定 | 可設定 | 可設定 | 可設定 |

### 6.6 Codex 訂閱後端
參考 BallonsTranslator `modules/codex.py`（GPL-3.0，可依授權移植並標註出處）：

| 項目 | 設計 |
|---|---|
| 登入 | ChatGPT 帳號 OAuth（PKCE）；開啟瀏覽器，本機 `http://localhost:<埠>/auth/callback` 接收授權碼 |
| 憑證保存 | 存在 Windows 認證管理員（`keyring`）；與 Codex CLI 的 `~/.codex/auth.json` 完全分開，不讀取、不覆蓋，登出時也不影響它 |
| Token 更新 | 到期前自動更新；更新後舊的 refresh token 會失效，因此新 token 要先原子儲存再使用；遇到 401 先更新一次再重送，仍失敗才要求重新登入 |
| 模型清單 | `GET /models` 取得可用模型與推理等級；使用者選擇模型，翻譯預設低推理等級 |
| 請求 | `POST /responses` 串流；`store: false`、不帶工具；固定的指令與術語表放在 `instructions`，同一個工作共用同一個 `prompt_cache_key`，讓重複的前綴可以被快取 |
| 輸出格式 | JSON Schema：`{"translations":[{"id":…,"translation":"…"}]}`，依 id 對應，不依行數 |
| 錯誤 | 轉成 §6.4 的類別：額度用完 → `UsageLimit`；截斷／超長 → `TooLong`；拒絕 → `Refused` |
| 隱私 | 日誌只記雜湊；首次使用時提示文本會送到 OpenAI |
| 隔離 | 所有 Codex 細節集中在 `backends/codex.py`；端點變更時只改這個檔案 |

> 這個端點不是 OpenAI 公開的 API，可能變更或受服務條款限制（§16）。因此標示為「實驗性」；不想使用時，可走 §4.4 的分批交換檔，改用官方 Codex CLI 手動翻譯。

### 6.7 Sakura 本地後端
**輸出字體**：Sakura 模型輸出簡體中文，**譯文維持簡體，不做繁簡轉換**。

**設定項目**（參考 BallonsTranslator `modules/translators/trans_sakura.py`）：

| 設定 | 預設 | 說明 |
|---|---|---|
| API 位址 | `http://127.0.0.1:8080/v1` | 可填區網 IP（例如 `http://192.168.1.20:8080`），使用另一台電腦的 GPU；結尾沒有 `/v1` 時自動補上 |
| 模型版本 | `1.0` | `0.9`、`1.0`、`galtransl-v1`；決定使用哪一版提示詞模板，以及術語表格式 |
| 術語表 | 專案的 `glossary.tsv` | 送出前轉成該版本的格式（Sakura 為 `原文->譯文 #備註`）；只附上本句命中的術語（T6） |
| 強制附上整份術語表 | 關 | 不論是否命中都附上整份；術語表大時會佔用 context，不建議 |
| 小假名放大 | 關 | 把「ァ」換成「ア」等，改善部分模型對小假名的辨識 |
| 重試次數 | 3 | 每批最多重送次數（§6.4 的降級另計） |
| 逾時 | 999 秒 | 單一請求的上限 |
| 最大輸出 token | 1024 | 單一請求的輸出上限 |
| 重複偵測門檻 | 20 | 同一片段重複超過此次數視為模型退化（T11 的重複退化檢查使用此值） |
| 取樣參數 | temperature 0.1、top_p 0.3、frequency_penalty 0.05 | Sakura 建議值；進階設定中才顯示 |
| 平行數 | 自動 | 讀 `/props` 的槽數；可手動降低 |

**連線與啟動**：
- 開始前檢查 `/health`；讀 `/props` 取得槽數與 context，自動填入 `BackendCaps`。
- 設定頁提供「測試連線」：顯示伺服器狀態、模型名稱、槽數、每槽 context。
- 可選：指定 OneClickLLAMA 資料夾，由工具代為啟動與關閉伺服器。**預設監聽 `127.0.0.1`**。OneClickLLAMA 原本的 `.bat` 監聽 `0.0.0.0`，會對區網開放；只有使用者勾選「允許區網連線」時才這樣做，並顯示警告。
- 可設定 GPU 層數、平行槽數、每槽 context，對應 `llama-server` 的 `-ngl`、`-np`、`-c`。

### 6.8 開跑前的用量預估
`caps.metered` 為真時，Orchestrator 先跑 T1–T8（不送出），顯示：

| 項目 | 算法 |
|---|---|
| 待翻句數／快取命中數 | T1、T4 的結果 |
| 批次數 | T7 的結果 |
| 預估輸入／輸出 token | 依 T8 產生的請求計算；輸出以原文 token × 1.5 估 |
| 預估時間 | 批次數 ÷ 平行數 × 平均每批耗時（初值 30 秒，之後依實際紀錄修正） |

使用者確認後才開始；可以先選「只翻前 N 句試譯」確認品質。

### 6.9 全文 QA（T13）
逐句的 T11 抓不到跨句問題，T13 在全部批次完成後執行：

| 檢查 | 標記條件 |
|---|---|
| 術語遵循 | 原文含已確認術語，譯文卻沒用指定譯名 |
| 同名異譯 | 同一 `speaker` 或名稱在不同句中出現不同譯名 |
| 同源異譯 | 原文只差標點或空白，譯文卻差很多（提示可能翻錯） |
| 殘留未翻 | 同一場景中，前後句已翻、中間卻有 `failed` 或 `uncertain` |

結果只加 `inconsistent` 旗標；介面可一鍵篩選後重翻或手動修改。

### 6.10 外部字典
**用途**：
1. **辨識**：判斷術語候選是否為人名、地名、作品名，以及它的種類。
2. **讀音**：提供平假名或羅馬字，讓片假名人名的音譯前後一致。
3. **既有譯名**：直接採用已存在的中文譯名。

**來源**（已查證授權；只在使用者按下下載時，才從官方來源取得）：

| 來源 | 內容 | 有中文 | 授權 | 用途 |
|---|---|---|---|---|
| Wikidata | 角色、作品、地名的多語標籤（含 zh-tw／zh-hant） | 有 | CC0 | 既有譯名（主要來源） |
| JMnedict（EDRDG） | 約 74 萬筆日文專有名稱：讀音、種類 | 無 | EDRDG 授權：可自由使用，但必須「適當標示出處」並遵守 EDRDG 授權頁的條件（已查證；JMdict 另明定為 CC BY-SA 4.0） | 辨識、讀音 |
| Japanese Personal Name Dataset | 常見名字與姓氏：漢字、平假名、羅馬字 | 無 | MIT | 辨識人名、讀音 |
| VNDB（依遊戲標題查詢） | 該作品的角色名與讀音 | 無 | ODbL | 提取前先取得角色名單 |
| 使用者自備詞庫 | Sakura 術語表、LunaTranslator 或 MTool 匯出、社群術語表（CSV／TSV／JSON） | 有 | 由使用者負責 | 既有譯名 |

不採用的來源：
- **JMdict 一般詞彙**：一般詞彙模型本來就會翻，查字典只會增加雜訊。
- **Bangumi、PokeAPI**：資料授權沒有寫明，不內建下載；使用者可以自行當成自備詞庫匯入。
- **Jiyomi**：尚未查證，暫不採用。

**設計規則**：
- **不隨程式散佈字典資料**：只提供下載器與轉換器，使用者選擇下載後轉成本地 `dicts/*.sqlite`；`docs/` 與「關於」畫面列出每個來源的出處與授權（CC BY-SA 的標示要求）。
- **只查候選，不掃全文**：JMnedict 收錄大量同時也是普通詞彙的名稱，對全文比對會產生大量誤判。因此字典只在 E10 查詢術語候選，以及使用者在表格中手動查詢時使用；T6 只比對專案術語表。
- **優先順序**：手動術語 > 已確認的種子術語 > 字典中文譯名 > 只有讀音（由模型依讀音音譯）。
- **字典譯名不直接定案**：來源標為 `dict:<字典名>`，仍需人工確認才會成為正式術語，避免同名不同人（例如常見名字）套錯譯名。
- **更新**：記錄每個字典的下載日期與版本，介面提供手動更新（EDRDG 要求使用者能更新資料）。

---

## 7. 寫回管線（C 段）

只讀交換檔，寫入遊戲（先備份）。**全部檔案成功才算完成，任何一個失敗就整批回滾。**

| # | 單元 | 輸入 → 輸出 | 職責 |
|---|---|---|---|
| W1 | **Loader** | 交換檔 + 遊戲資料夾 → 寫回清單 | 比對 manifest 中的雜湊；遊戲已變動時要求先重新提取（§4.5） |
| W2 | **Selector** | 記錄 → 替換表 | 只取 `translated`、`reviewed`；依 `refs` 展開成 `檔案 → {定位: 譯文}`，有 `overrides` 的位置用覆寫譯文 |
| W3 | **LineWrapper** | 譯文 → 換行後譯文 | 依 `plugin.wrap_rule(kind)` 控制每行字數；超出行數上限時標 `overlong` |
| W4 | **EncodeGuard** | 譯文 → 可編碼譯文 | 依 §8.2 策略處理；仍無法編碼時標 `unencodable`，該句保留原文 |
| W5 | **Renderer** | 原檔文字 + 替換表 → 新內容 | 呼叫 `plugin.render`；以 manifest 記錄的編碼、BOM、換行風格輸出 |
| W6 | **Verifier** | 新內容 → 通過／失敗 | 用 `plugin.parse` 重新解析，確認片段數不變、每處都等於預期譯文、控制碼完整、可以正常解碼 |
| W7 | **Committer** | 新內容 → 遊戲檔案 | 依 §9 備份 → 寫暫存檔 → 原子替換；封包類優先用覆蓋包（如 `patch.xp3`、Ren'Py 散檔），只有無法覆蓋時才重建封包 |

---

## 8. 編碼策略

### 8.1 讀取（E4）
1. 有 BOM 就照 BOM（UTF-8／UTF-16LE／UTF-16BE）。
2. 嚴格 UTF-8 解碼成功，就判定為 UTF-8。
3. 否則用 Plugin 的 `default_encoding`（多為 CP932），再以 `charset-normalizer` 交叉確認；兩者不一致時標 `uncertain`，由使用者確認。
4. 結果記入 manifest，寫回時沿用。

### 8.2 寫回（W4）
| 策略 | 適用 | 說明 |
|---|---|---|
| A. 原編碼直接寫 | UTF-8／UTF-16 引擎（RPG Maker MV/MZ、Ren'Py、Tyrano、Unity、Godot） | 不需處理 |
| B. 改用 Unicode | 引擎能讀 Unicode（如 KiriKiri 可讀 UTF-16LE BOM 腳本） | 轉成 UTF-16LE／UTF-8 寫回 |
| C. 字形替代 | 只能讀 CP932 的舊引擎 | 多數繁體字可直接用 CP932 編碼（如「這」「們」「為」，已實測）；無法編碼的換成日文異體字（如「說→説」）；沒有對應的（如「你」「妳」）列出由使用者處理或交給 D。**注意**：Sakura 輸出簡體且不轉換，簡體字（如「这」「们」）多數不在 CP932 內，這類遊戲建議改用 Codex 並指定繁體，或使用策略 D |
| D. 字元對應 | C 仍不夠時（P2） | 把未使用的 CP932 碼位對應到中文字，搭配替換字型顯示 |

### 8.3 路徑
- 一律用 `pathlib` 與 Unicode API。
- 封包內的檔名依封包格式解碼（XP3 為 UTF-16；ZIP 依旗標判斷 UTF-8 或 CP932）。

---

## 9. 備份與還原

- **位置**：遊戲資料夾內的 `_yakupatch_backup/`，跟著遊戲移動（已定案，不提供集中備份目錄）。
- **只備份一次**：檔案第一次被修改前才備份，之後再次寫回不覆蓋，永遠保有最原始的版本。
- **清單** `manifest.json`：相對路徑、原始 SHA-256、大小、修改時間、寫回後 SHA-256、工具版本；新增的檔案（如 `patch.xp3`）也要記錄，還原時刪除。
- **還原**：
  1. 逐檔比對目前雜湊：
     - 等於「寫回後雜湊」→ 用備份還原。
     - 等於「原始雜湊」→ 已是原檔，略過。
     - 兩者都不是 → 遊戲已被官方更新或其他工具修改。**不還原**（否則會把新版遊戲蓋回舊檔），把該檔備份標為 `stale` 並保留，直到使用者手動刪除。
  2. 還原後驗證雜湊等於原始雜湊，全部通過才刪除備份。
- **遊戲更新後再次寫回**：`stale` 的檔案以目前版本為新的原始檔重新備份。
- **安全**：寫回前檢查磁碟空間；任何一步失敗就回滾已改的檔案。

---

## 10. 引擎支援路線

| 疊代 | 引擎 | 作法 | 主要依賴 |
|---|---|---|---|
| I0 | RPG Maker MZ | `data/*.json` 事件指令（401 文字、102 選項、名稱欄位） | 標準函式庫 `json` |
| I1 | RPG Maker MV | 同 MZ，路徑在 `www/data` | — |
| I3 | Ren'Py | 讀 `.rpyc`（散檔或 `.rpa` 內）的 Translate 節點；寫成新語言 `game/tl/yakupatch/` 並設定 `config.language`，不改原腳本 | 自寫 rpa 讀取、受限 unpickler |
| I3 | TyranoBuilder | `.ks` 腳本；`app.asar` 只改等長標頭，改過的檔放 `app.asar.unpacked/`（`package.nw` 未支援） | 自寫 asar 讀寫 |
| I4 | Unity Mono／IL2CPP | TextAsset、MonoBehaviour（TypeTree）；常見框架規則（I2 Localization、Naninovel、Utage、Fungus）；IL2CPP 改 `global-metadata.dat`；**字型替換**（TextMeshPro 字型資產沒有中文字時會顯示方塊，必須替換或加上備援字型） | `UnityPy`（MIT） |
| I4 | KiriKiri | 未加密 XP3 解包 → `.ks`；用 `patch.xp3` 覆蓋，不動原封包 | 自寫 XP3 讀寫 |
| I5 | Wolf RPG、Artemis、Godot、CatSystem2、SRPG Studio | 各自封包格式 | 視格式 |
| I6+ | YU-RIS、BGI、Siglus、NScripter、GameMaker、Unreal | 依社群需求 | 視格式 |

- **遊戲規則檔**：Unity 這類每款遊戲格式不同的引擎，可為單一遊戲寫 YAML 規則（要掃哪些資源、哪些欄位要翻），不必修改程式；社群可以分享。
- **「通用所有遊戲」的實際範圍**：加密封包、自製引擎、文字做在圖片裡的遊戲無法保證支援。偵測不到時，介面會明確顯示「不支援」及原因，不會修改任何檔案。

---

## 11. 使用者介面

### 11.1 介面原則
| 原則 | 具體做法 |
|---|---|
| 一條主流程 | 每款遊戲都照「偵測 → 語言預檢 → 提取 → 翻譯 → 寫回」五步，畫面上永遠顯示目前在第幾步、下一步按哪裡 |
| 狀態永遠看得到 | 遊戲的引擎、語言、進度、後端連線狀態都用固定位置的標籤顯示，不需要點進去才知道 |
| 改動遊戲前先讓人看到影響 | 寫回與還原前列出會改哪些檔案、有幾句超長或無法編碼，確認後才執行 |
| 不凍結 | 提取、翻譯、寫回都在背景執行；同時可以瀏覽與編輯其他遊戲 |
| 錯誤要能處理 | 每個錯誤訊息都附上處理按鈕（例如「重新登入」「測試連線」「拆小批重送」），不只顯示錯誤 |

### 11.2 主視窗版面
```
┌──────────────────────────────────────────────────────────────────────────────┐
│ YakuPatch                 [＋加入遊戲] [掃描資料夾]   後端：Sakura ● 已連線 8槽 [設定] │
├────────────────────┬─────────────────────────────────────────────────────────┤
│ 遊戲庫 [搜尋___]    │  Summer Memories                RPG Maker MZ  [日文]    │
│ 篩選：全部 ▾        │  ┌───────────────────────────────────────────────────┐  │
│                    │  │ ①偵測 ✓ → ②語言預檢 ✓ → ③提取 ✓ → ④翻譯 62% → ⑤寫回 │  │
│ ● Summer Memories  │  └───────────────────────────────────────────────────┘  │
│   MZ  [日文]  62%   │  [總覽] [文本] [術語] [翻譯] [寫回與還原]                 │
│ ○ 迷家的幼婆们      │ ┌─────────────────────────────────────────────────────┐ │
│   MZ  [簡中] 不需翻譯│ │                                                     │ │
│ ○ 銀白の追憶        │ │              （目前分頁的內容）                       │ │
│   Unity [繁中]不需翻譯│ │                                                     │ │
│ ○ NEKO-NIN exHeart │ │                                                     │ │
│   KiriKiri [日文]   │ └─────────────────────────────────────────────────────┘ │
├────────────────────┴─────────────────────────────────────────────────────────┤
│ 工作佇列：翻譯 Summer Memories 3,120/5,034 句 · 失敗 12 · 剩餘約 9 分  [暫停] [展開] │
└──────────────────────────────────────────────────────────────────────────────┘
```
- **左側遊戲庫**：所有遊戲的清單與狀態。
- **上方步驟條**：目前遊戲走到哪一步；每一步可以點擊跳到對應分頁。
- **中間分頁**：目前遊戲的詳細內容。
- **下方工作佇列**：所有背景工作；展開後可看每個工作的詳細進度與日誌。

### 11.3 遊戲庫
| 項目 | 設計 |
|---|---|
| 加入 | 「加入遊戲」選單一資料夾；「掃描資料夾」選上層資料夾（例如 `G:\game`），自動找出所有遊戲並批次偵測 |
| 每列顯示 | 名稱、引擎、語言標籤、狀態、進度 |
| 語言標籤 | `日文`、`簡中`、`繁中`、`中文`（無法分辨繁簡）、`混合`、`中文包`（內建中文語言包）、`未知` |
| 不需翻譯 | 判定為中文或有中文包的遊戲以灰色顯示「不需翻譯」，排在清單後面；滑鼠移上去顯示判定依據 |
| 不支援 | 偵測失敗的遊戲顯示原因（例如「封包加密」），不會進入提取 |
| 篩選 | 依語言、引擎、狀態（未處理／翻譯中／已寫回／不需翻譯／不支援） |
| 右鍵選單 | 開啟遊戲資料夾、重新偵測、移除（只移除專案，不刪遊戲） |

### 11.4 總覽分頁
- **遊戲資訊**：路徑、引擎與信心分數、文本檔數、句數（待翻／已翻／失敗／不翻）。
- **語言預檢卡片**（§5.1）：
  ```
  語言預檢：日文 ✓ 可以翻譯
  假名比例 31%　簡體專有字 2　繁體專有字 1　內建語言包：無
  ```
  已是中文時，卡片改為「已是簡體中文，不需翻譯」，並顯示依據與「仍要翻譯」按鈕；有中文語言包時，顯示切換方法。
- **主要按鈕**：依目前步驟只顯示一個主要按鈕（「開始提取」→「開始翻譯」→「寫回遊戲」→「啟動遊戲」）。

### 11.5 文本分頁
```
┌ 篩選：狀態 ▾ 旗標 ▾ 種類 ▾ 檔案 ▾  [搜尋原文或譯文______]       5,034 句 ─┐
│ #    說話者   原文                         譯文                  狀態     │
│ 1021 リリア   <0>こんにちは<1>             <0>你好<1>            已翻譯   │
│ 1022 リリア   今日はいい天気ですね。        今天天气真好呢。        已翻譯   │
│ 1023 —        \ct<0>聖女は<1>…             （空）               ⚠ 失敗   │
├─────────────────────────────────────────────────────────────────────────┤
│ 原文：\ct\C[2]聖女は\C[0]『…』             上下文：前 3 句（可展開）      │
│ 譯文：[ <0> ][ 圣女 ][ <1> ]…              錯誤：佔位符數量不符（缺 <2>） │
│ [重翻此句] [標為已審閱] [套用到相同原文]   出現位置：Map003.json 等 2 處  │
└─────────────────────────────────────────────────────────────────────────┘
```
- 表格使用虛擬捲動，十萬句也能流暢操作。
- 下方編輯區把佔位符顯示成不可拆開的標籤，避免手動編輯時打壞控制碼；存檔時一樣經過 T11 驗證。
- 手動修改的句子自動標為 `reviewed`。
- 多選後可以批次「重翻」「標為已審閱」「勾選翻譯不確定項目」。
- 篩選捷徑：失敗、超長、無法編碼、不一致、不確定。

### 11.6 術語分頁
- 欄位：原文、譯文、讀音、種類（人名／地名／道具／技能）、來源（`seed`／`dict:<字典名>`／`manual`）、是否已確認。
- 右側「字典查詢」面板：選取術語後，列出本地詞庫的讀音、種類與既有中文譯名（§6.10），可一鍵套用。
- 未確認的術語以虛線框顯示；「全部確認」需要再按一次確認。

### 11.7 翻譯分頁
| 區塊 | 內容 |
|---|---|
| 後端 | 下拉選單：Sakura／Codex／其他 OpenAI 相容；旁邊顯示狀態（Sakura：連線、模型、槽數；Codex：帳號、模型、推理等級） |
| 輸出字體 | 顯示本次譯文會是簡體或繁體：Sakura 固定簡體（不轉換）；Codex 依設定 |
| 範圍 | 全部待翻／只翻選取的句子／只翻術語與名稱／試譯前 N 句 |
| 用量預估 | Codex 開跑前跳出對話框：句數、快取命中、批次數、預估 token 與時間（§6.8） |
| 進度 | 完成數／總數、失敗數、每分鐘句數、預估剩餘時間；暫停、繼續、取消 |
| 歷史 | 每次翻譯一列：時間、後端、模型、句數、失敗數；可「撤銷此次翻譯」（F21） |
| 交換檔 | 「匯出分批檔」「匯入譯文」；匯入後顯示通過與被拒的句數及原因 |

### 11.8 寫回與還原分頁
- **寫回前預覽**：列出將修改或新增的檔案（路徑、大小、替換句數、編碼、使用的編碼策略），以及警告：超長 N 句、無法編碼 N 句、存檔相容提示（§17 G6）。
- **確認對話框**：「將修改 12 個檔案，原檔會先備份到 `_yakupatch_backup/`」→［寫回］［取消］。
- **還原**：逐檔顯示結果（已還原／已是原檔／遊戲已更新而略過 `stale`）。
- **啟動遊戲**：寫回後可直接啟動遊戲測試。

### 11.9 設定
| 分頁 | 內容 |
|---|---|
| 翻譯後端 | **Sakura**：§6.7 全部設定、「測試連線」、OneClickLLAMA 路徑與啟動參數、「允許區網連線」（含警告）；**Codex**：登入／登出、帳號、模型、推理等級、平行數、首次使用的隱私提示；**其他 OpenAI 相容**：網址、API 金鑰（存在 Windows 認證管理員）、模型 |
| 輸出 | Codex 目標字體（預設繁體，可改簡體）、標點正規化、每批句數上限 |
| 語言預檢 | 判定門檻（假名比例、繁簡倍數）；預設不翻中文遊戲（可關閉） |
| 編碼 | 預設策略、CP932 字形替代清單 |
| 字典 | 已下載的字典、版本與日期、下載、更新、刪除、授權與出處 |
| 介面 | 介面語言（繁中／簡中／English／日本語）、淺色／深色主題、字型大小 |
| 進階 | 日誌等級、匯出診斷包（§17 G8）、快取清除 |

### 11.10 首次啟動精靈
1. **選擇翻譯後端**：自動尋找 OneClickLLAMA（例如 `D:\AI\OneClickLLAMA_NV_20240429`），找到就提供「啟動並測試」；也可以改選 Codex 登入或略過。
2. **加入遊戲**：選單一遊戲或上層資料夾；掃描後直接顯示語言預檢結果，告訴使用者哪些遊戲需要翻譯。
3. **完成**：跳到第一款需要翻譯的遊戲的總覽分頁。

### 11.11 提示與錯誤
| 類型 | 呈現方式 |
|---|---|
| 完成、一般資訊 | 右下角短暫提示，不打斷操作 |
| 需要使用者處理（`AuthRequired`、`UsageLimit`、`ConfigError`） | 工作暫停，在工作佇列與翻譯分頁顯示說明與處理按鈕 |
| 會改動遊戲的操作 | 一律用確認對話框，列出影響範圍 |
| 失敗的句子 | 不跳出視窗；在表格標紅，可用篩選集中處理 |

### 11.12 鍵盤、在地化與實作
- **快捷鍵**：`Ctrl+F` 搜尋、`Ctrl+Enter` 儲存譯文並跳到下一句、`F5` 重翻目前句、`Ctrl+Z` 復原編輯。
- **在地化**：介面文字使用 Qt 翻譯檔；預設繁體中文。
- **顯示**：支援高 DPI。
- **實作**：PySide6 Model/View（`QAbstractTableModel` 虛擬捲動）。背景工作由 Core 的工作佇列執行（§17 G7）。GUI 只呼叫 Core API，並訂閱 Store（§4.6）的變更事件更新畫面，本身不讀寫交換檔或遊戲檔案。

---

## 12. 技術選型

| 項目 | 選擇 | 理由 |
|---|---|---|
| 語言 | Python 3.12 | 遊戲資源解析生態完整；LunaTranslator、BallonsTranslator 都是 Python，可直接參考 |
| GUI | PySide6 | 官方 Qt 綁定，LGPL |
| HTTP | `httpx` | 非同步平行請求、串流 |
| 憑證保存 | `keyring` | 使用 Windows 認證管理員，不把 token 寫成明文檔案 |
| 繁簡辨識 | `opencc`（只用來判斷某個字是否為簡體或繁體專有字，**不轉換譯文**） | §5.1 實測已使用 |
| 編碼偵測 | `charset-normalizer` | MIT |
| Unity | `UnityPy` | MIT |
| 快取 | SQLite（標準函式庫） | 只存全域翻譯快取；譯文以交換檔為準 |
| 打包 | Python 嵌入版（embeddable package）+ 啟動腳本，參考 BallonsTranslator | 見 §12.1；直接執行原始碼，不經 PyInstaller 凍結，原生模組（UnityPy、TypeTreeGeneratorAPI、PySide6）不會漏打包，防毒誤判較少，更新只需替換程式資料夾 |
| 測試 | `pytest` | — |

### 12.1 安裝包與一鍵啟動
參考 BallonsTranslator 的兩種安裝方式，兩者結果相同，都在使用者選的資料夾內自成一體，不寫登錄檔、不動系統 Python：

| 方式 | 使用者操作 | 內容 |
|---|---|---|
| **A. 免設定壓縮包**（主要） | 從 GitHub Releases 下載 `YakuPatch_win.zip`，解壓後雙擊 `launch_win.bat` | 內含 Python 3.12 嵌入版（`runtime/`）、已安裝的全部依賴、程式原始碼（`yakupatch/`）、`launch_win.bat` |
| **B. 一鍵安裝腳本** | PowerShell 執行 `irm https://raw.githubusercontent.com/Honguan/YakuPatch/main/scripts/install.ps1 \| iex`（或在 cmd 以 `powershell -NoProfile -ExecutionPolicy Bypass -Command "..."` 執行） | 在目前資料夾下載 Python 嵌入版與程式碼，依鎖定版本的 `requirements.lock` 安裝依賴，建立 `launch_win.bat`，完成後自動啟動 |

- **啟動腳本** `launch_win.bat`：用 `runtime\python.exe` 執行 `python -m yakupatch.gui`；啟動時檢查核心依賴，缺少或版本不符時提示並可一鍵補裝。選用功能需要的額外套件，等使用者第一次用到時才提示安裝。
- **使用者資料分離**：專案、設定、字典放在 `%USERPROFILE%\.yakupatch`（可改），更新或刪除程式資料夾不會影響。
- **更新**：下載新版壓縮包覆蓋程式資料夾，或用安裝腳本重新執行；GUI 啟動時可檢查 GitHub Releases 是否有新版，只提示，不自動下載。
- **命令列**：同一個 `runtime\python.exe -m yakupatch ...` 可直接使用 CLI，另附 `yakupatch.bat` 包裝。
- **支援範圍**：Windows 10／11 64 位元。不支援 Windows 7。缺少 Microsoft Visual C++ Redistributable 時，啟動腳本要給出可理解的錯誤和下載說明。
- **建置**：GitHub Actions 在 tag 時下載 Python 嵌入版、安裝 `requirements.lock`、複製原始碼，壓成 zip 上傳 Releases；`install.ps1` 與壓縮包使用同一份鎖定清單。

---

## 13. 開源與授權

- **GPL-3.0**：可依授權移植 LunaTranslator、BallonsTranslator（都是 GPL-3.0）的程式碼並標註出處；其他依賴（MIT／LGPL／BSD）都相容。
- 儲存庫不收錄任何遊戲檔案或模型檔；測試樣本自行製作。
- MTool 是閉源軟體，只參考公開行為，不反編譯、不複製。
- README 註明：僅供使用者對自己合法持有的遊戲做個人翻譯；分享時只分享交換檔，不分享遊戲原檔。
- **貢獻引擎 Plugin**：`CONTRIBUTING.md` 說明介面；新 Plugin 只要附上一個最小樣本遊戲並通過合規測試套件（§14.1），即可合併。

儲存庫結構：
```
yakupatch/
  engines/      base.py（EnginePlugin、Container、Segment）, rpgmaker_mvmz.py, renpy.py ...
  extract/      detector.py, unpacker.py, locator.py, decoder.py, parser.py,
                classifier.py, segmenter.py, annotator.py, deduper.py,
                glossary_seeder.py, merger.py
  exchange/     store.py（單一寫入者、快照 + 日誌）, batches.py（匯出入）, migrations/
  translate/    work.py, orchestrator.py
                units/  selector.py, normalizer.py, masker.py, cache.py, context.py,
                        glossary.py, batcher.py, prompt.py, dispatcher.py,
                        parser.py, validator.py, postprocess.py, writer.py,
                        consistency_qa.py, estimate.py
                backends/  caps.py, errors.py, sakura.py, codex.py, openai_compat.py
  writeback/    loader.py, selector.py, wrapper.py, encode_guard.py, renderer.py,
                verifier.py, committer.py
  backup/       backup.py, restore.py
  encoding/     detect.py, cp932_fallback.py
  safety/       safe_path.py（路徑跳脫檢查、解包大小上限）
  dicts/        downloaders/（wikidata、jmnedict、personal_names、vndb）, importers/（自備詞庫）, lookup.py
  gui/
tests/
  conformance/  Plugin 合規測試套件（§14.1），每個 Plugin 自動套用
  fixtures/     每種引擎的最小樣本遊戲
docs/           使用說明、引擎支援表、遊戲規則檔與交換檔格式說明
```

**I0 實際結構**（先用扁平模組，檔案變大再依上方結構拆分）：`fsutil.py`、`safety.py`、`encoding.py`、`jsonspan.py`（JSON 字串位置掃描與原地替換）、`langdetect.py`、`translate.py`（遮罩／驗證／後處理）、`extract.py`、`exchange.py`（Store、匯出入）、`writeback.py`、`backup.py`、`cli.py`、`engines/{base,rpgmaker_mz}.py`；`tests/` 用標準函式庫 `unittest`（`test_conformance.py`、`test_units.py`、`helpers.py`）。

**I0 與本企劃的差異**（以程式為準）：
- 提取順序：先切句（E6 Segmenter）再分類（E7 Classifier），因為分類要看整段文字。
- `refs` 為物件陣列 `[{"file","locs"}]`；日誌行格式 `{"run","id","set","old","ts"}`，`old` 供 `undo` 使用。
- Store 以行程內鎖（`threading.RLock`）串行化寫入，未另開寫入執行緒；不防多個行程同時開同一專案。
- 事件指令 355／655（腳本）、657（外掛參數顯示）一律不提取；357 提取參數物件的字串值，參數本身是 JSON（結構／陣列參數）時遞迴拆開，位置以 `~j` 標記「此字串再解碼為 JSON」。鍵名含 text／message／label／title／caption／desc／help 且不含 file／image／picture／icon／sound／font／symbol／switch／variable 等字樣的標 `system` 送翻，其餘標 `uncertain`，預設不送翻。
- I0 不提取 `js/plugins.js`。
- 尚未實作（程式內以 `shortcut:` 註記）：容器解包、W3 自動換行、charset-normalizer 編碼偵測、備份記錄新建檔案。

**I1 實際結構與差異**：
- 新增 `pipeline.py`（Work、選取、快取、前文、術語、分批、估算、派送與 Store 寫回）、`backends/{__init__,sakura}.py`（BackendCaps、錯誤分類、設定與 urllib Client）、`engines/rpgmaker_mv.py`；沿用 I0 的 `translate.py` 單元、Store、MZ 解析器及備份還原，不拆分原有模組。
- T1 先處理 `name`／`system`，只回填原有空白 `seed` 名稱術語，保持手動詞條；續跑從 Store 已翻名稱恢復種子。T5 使用去重記錄首次出現位置的前 3 筆同檔案記錄，送出前刷新已翻前文；不是事件場景樹。
- Sakura 使用固定 lines 模板；前文放在待譯內容前綴，解析時依行數丟棄前綴譯文。單句失敗仍依序去前文、提高溫度重試。整批行數或驗證失敗拆半；連線暫時失敗依 §6.7 預設重試 3 次（可設 0–5），模型明確拒絕後持續拆半至單句，只有單句仍拒絕才標 `errors=refused` 且不自動續送。授權、額度與設定錯誤停止新工作並保留進度。
- 每批由主執行緒透過 `Store.update_many` 寫入；額外記錄 `errors`、`attempts`，`by=sakura:模型/版本-p1`。只在驗證通過後存入專案 SQLite 快取；鍵包含後端設定、模型／prompt、遮罩原文與對照、術語及前文／說話者。尚無全域快取。
- T11 沿用 I0 驗證，Sakura 的重複門檻依 §6.7 預設 20；外部匯入保留原本 4。T12 沿用還原遮罩與空白，不額外改標點或轉換繁簡。T2 補上單獨 CR 正規化，T3 補上兩種佔位符同時存在的避碰。
- token 以每字 1.5 粗估，請求預算計入固定 prompt、術語與前文，每批最多 16 筆；時間以每波 30 秒估算，尚無 tokenizer／歷史耗時校準。`--dry-run` 離線且不寫檔；正式翻譯會讀 health／props／models 後顯示預估。Sakura 不計量，使用者執行 translate 即直接開始，不另要求確認。
- MV 共用 MZ parse/render，`www/` 由既有根目錄搜尋處理；圖片／音訊加密不影響明文 JSON，`.rpgmvp` 不阻擋翻譯。保留 System.json 存在與逐檔 JSON 檢查；加密或不支援的 JSON 明確拒絕，不解密。
- I1 沒有 GUI、模型自動啟停、場景摘要、自由風格、Codex/json_ids、T13 QA 或評測集；不修改 I0 的既有限制。已連線的 urllib socket 無法取消，Ctrl+C 後行程結束最多仍等到 timeout；工作執行緒不直接寫 Store。
- 測試與真實驗收數字、未通過清單、shortcuts 及審查重點見 `docs/I1_REPORT.md`。2026-10-23 完成實機驗收：10 款啟動、3 款還原雜湊比對通過。

---

## 14. 品質驗證

| 測試 | 內容 |
|---|---|
| Plugin 合規 | 見 §14.1，每個 Plugin 自動套用 |
| 單元測試 | E3–E10、T2–T8、T10–T13、W3–W6 都是純函式，用固定輸入比對輸出，不需要模型 |
| Store 測試 | 日誌重播、最後一行不完整時的復原、壓縮、撤銷整次翻譯、GUI 與翻譯同時寫入 |
| 安全測試 | 含 `../` 與絕對路徑的惡意封包、超大解包、惡意 `.rpyc`（受限反序列化必須拒絕） |
| 後端測試 | 用假的 Client 測 Dispatcher 的重試、拆批、暫停、各錯誤類別；Codex 的 OAuth 與 token 更新用模擬伺服器測 |
| 交換檔測試 | 合併規則（§4.5）、匯入時的雜湊比對、中斷後續跑 |
| 編碼測試 | CP932／UTF-8／UTF-16 讀寫；無法編碼字元的替代與回報 |
| 備份測試 | 寫回 → 還原後雜湊等於原始；中途失敗會回滾 |
| 實機測試 | 每個引擎從使用者遊戲庫挑 3 款，寫回後實際啟動並走過開頭劇情 |
| CI | GitHub Actions（Windows）跑 pytest；需要真實模型或帳號的測試只在本機手動執行 |

### 14.1 Plugin 合規測試套件
`tests/conformance/` 對每個 Plugin 和它附帶的樣本遊戲自動執行，全部通過才能合併：

| 測試 | 必須成立 |
|---|---|
| 往返 | 提取 → 不修改直接寫回，輸出與原檔逐位元組相同 |
| 解析與渲染互逆 | 每個片段替換成隨機標記字串後，`parse(render(...))` 取回的正好是這些標記 |
| 定位穩定 | 同一檔案提取兩次，`loc` 與 `id` 完全相同 |
| 控制碼 | 樣本中的每種控制碼都能被 `control_codes` 匹配 |
| 偵測 | 自己的樣本信心分數 ≥ 0.9；其他 Plugin 的樣本 ≤ 0.3 |
| 安全 | 只透過 `Container` 讀寫；不在遊戲資料夾外寫入 |

---

## 15. 疊代計畫

每一輪都是完整的垂直切片，結束時可以端到端使用，下一輪只往橫向擴充。

| 疊代 | 內容 | 驗收標準 |
|---|---|---|
| **I0 骨架** | RPG Maker MZ 單一引擎走完 A→B→C：提取、交換檔（Store 快照 + 日誌）、匯出／匯入分批檔、寫回、備份還原；安全規則；合規測試套件；CLI | MZ 通過合規測試；手填譯文匯入後遊戲可正常顯示並能還原 |
| **I1 本地翻譯** | 完成（2026-10-23）：MV/MZ 隨機 10 款翻譯寫回，10 款以 `Game.exe` 實機啟動並顯示譯文（使用者確認）；02_mv、06_mz、10_mz restore 後 data 雜湊與原版一致，再 apply 與原譯版一致。已知問題見 `C:\YakuPatchTest\_quality\REPORT.md` | MV/MZ 隨機 3 款可完整翻譯、啟動、還原 |
| **I2 介面 + Codex** | 完成（2026-10-23）：階段 A（Core API、工作佇列、Codex 登入與後端、用量預估、撤銷整次翻譯、診斷包、評測集）已實作並通過離線測試；Codex 評測與 3 款 MV/MZ 實機驗收通過；階段 B GUI 已實作，通過離線與 offscreen 測試，並以程式驅動 GUI 完成 3 款 MV/MZ 實機流程（Codex 2、Sakura 1）；使用者親手操作 GUI 驗收通過（2026-10-23，MV 1 款 Sakura 全部翻譯 8,156 句，回報正常）。詳見 `docs/I2_REPORT.md` | 一般使用者不碰命令列即可完成全流程；Codex 額度用完時能暫停並續跑 |
| **I3 一致性 + 文字腳本引擎** | 已實作（2026-10-23）：術語種子、外部字典（§6.10）、T13 全文 QA、`style.md`、自動換行；Ren'Py、Tyrano。實機驗收：Tyrano 3 款、Ren'Py 1 款（遊戲庫只有 1 款日文且無中文的 Ren'Py）寫回後讀回全為譯文，Ren'Py `compile` 無錯、0 句對話缺譯；QA 在 3 款抓到植入的同名異譯；4 款以 Codex 真實譯文重跑後，使用者實機檢查通過。詳見 `docs/I3_REPORT.md` | 各引擎隨機 3 款實機通過；QA 能抓出刻意植入的同名異譯 |
| **I4 封包引擎** | 完成（2026-10-24）：KiriKiri（未加密 XP3，譯文寫成新的 `patch.xp3`）實機通過 1 款；Unity Mono 的 UI 元件、Utage、Naninovel、自訂類別、`Assembly-CSharp.dll` 字串與字型統一（F20），隨機 3 款實機全部通過；I2 Localization、Fungus、IL2CPP 未做。詳見 `docs/I4_REPORT.md` | Unity 隨機 3 款至少 2 款可用且無方塊字；KiriKiri 未加密款可用 |
| **I5 擴充** | 進行中（2026-10-24）：Godot（`.pck` 內 `.tscn`／`.tres` 文字屬性、Godot 3 `.gdc` 字串、字型替換）已實作，1 款實機通過；Artemis（`.pfs` 內 `.ast`／`.tbl`，譯文寫成新的 `.pfs.NNN`）1 款實機通過。詳見 `docs/I5_REPORT.md`。待做：Wolf、CatSystem2、SRPG；CP932 策略 C | 各引擎至少 1 款實機通過 |
| **I6 發佈** | 安裝包一鍵啟動（§12.1）：免設定壓縮包 `YakuPatch_win.zip`（Python 嵌入版 + 依賴 + `launch_win.bat`）、一行 PowerShell 安裝腳本 `install.ps1`、依賴檢查與補裝、GitHub Actions 自動建置、使用說明、v1.0 | 在未安裝 Python 的乾淨 Windows 10／11 電腦上：壓縮包解壓後雙擊 `launch_win.bat`、以及執行安裝腳本，兩種方式都能啟動 GUI，並用隨機 1 款遊戲走完提取→翻譯→寫回→啟動→還原 |

### 15.1 成功指標
以使用者遊戲庫（365 款）為基準，每輪疊代結束時量測：

| 指標 | v1.0 目標 | 量測方式 |
|---|---|---|
| 支援率 | ≥ 60%（約 220 款可完成提取與寫回） | 對整個遊戲庫批次跑 E1–E11 與寫回的乾跑 |
| 合規測試通過率 | 100% | CI |
| 驗證失敗率 | Sakura ≤ 5%、Codex ≤ 1%（T11 標 `failed` 的比例） | 翻譯紀錄 |
| 實機可玩率 | 抽測樣本 100% 能啟動並走過開頭劇情 | 人工抽測 |
| 還原正確率 | 100%（還原後雜湊等於原始） | 自動測試 + 抽測 |
| 吞吐 | Sakura 7B ≥ 1 萬句／小時 | 翻譯紀錄 |

---

## 16. 風險與對策

| 風險 | 影響 | 對策 |
|---|---|---|
| Codex 端點非公開 API，可能變更或受服務條款限制 | Codex 後端失效或帳號風險 | 標為實驗性；集中在單一模組；可改走分批交換檔 + 官方 Codex CLI；Sakura 本地永遠可用 |
| Codex 訂閱額度用完 | 翻譯中斷 | `UsageLimit` 時暫停並保留進度，額度恢復後續跑 |
| 文本送到雲端的隱私疑慮 | 使用者疑慮 | 預設本地；首次使用雲端時明確提示 |
| 加密封包無法解開 | 該遊戲不支援 | 偵測時明確標示；不破解 DRM |
| Unity 文本格式各異 | 支援率低 | 通用規則 + 每款遊戲的 YAML 規則檔 |
| 譯文破壞控制碼導致遊戲崩潰 | 嚴重 | 遮蔽 + T11 驗證 + W6 重新解析；失敗保留原文 |
| 譯文過長超出對話框 | 顯示異常 | W3 換行；表格標示 `overlong` |
| CP932 引擎無法顯示中文 | 亂碼 | §8.2 策略 A～D；字型替換 |
| 本地 7B 模型品質不穩 | 體驗差 | 術語表、上下文、單句重翻、手動編輯、改用 Codex |
| 寫入中途失敗 | 遊戲損毀 | 先備份、原子替換、整批回滾 |
| 防毒誤判 | 使用者疑慮 | 不注入、不碰執行檔記憶體；開源可審查 |
| 惡意或損壞的遊戲封包 | 寫到遊戲資料夾外、執行惡意程式碼 | §3.6 安全規則；安全測試 |
| 同一句原文在不同情境需要不同譯文 | 去重後只有一個譯文 | `overrides` 依位置覆寫（§4.3） |
| 大型遊戲交換檔過大 | 速度慢、同時寫入互相覆蓋 | 快照 + 日誌、單一寫入者（§4.6） |
| 字型缺中文字 | 方塊字 | 字型替換（F20），Unity 於 I4 一併完成 |
| 字典授權不明或變更 | 開源專案違反授權 | 不隨程式散佈字典資料；只內建授權明確的下載器；列出出處與授權（§6.10） |
| 字典譯名套錯（同名不同人） | 譯名錯誤 | 字典譯名只作候選，需人工確認 |
| 只有漢字的日文句子被誤判成中文 | 人名、技能名漏翻 | 逐句判定需要簡繁專有字或中文虛詞作證據，否則視為日文（§5.1） |
| Sakura 簡體譯文寫入 CP932 引擎 | 多數簡體字無法編碼（實測 11 個常用字中有 10 個不在 CP932） | 這類遊戲建議用 Codex 指定繁體，或使用編碼策略 D（§8.2） |

---

## 17. 設計缺口與處理

v0.3 審查時找到的缺口。「已定案」表示設計已寫進本企劃；「已驗證」表示已用使用者的實際遊戲或本機模型實測。目前沒有待驗證的缺口。

| # | 缺口 | 處理 | 狀態 | 疊代 |
|---|---|---|---|---|
| G1 | 專案以路徑對應遊戲，搬移資料夾後會找不到 | 改用**遊戲指紋**：引擎 + 文本檔相對路徑與大小的雜湊。開啟時若路徑不同但指紋相同，提示重新連結 | 已定案（§4.1） | I0 |
| G2 | 遊戲更新後還原會把新版蓋回舊檔 | 雜湊三向比對，更新過的檔案不還原，改標 `stale`（§9） | 已定案 | I0 |
| G3 | I0 缺少 RPG Maker MZ 的提取範圍規格 | 見下方 §17.1，已用使用者遊戲庫 46 款 MZ 遊戲、3,196 個資料檔核對 | 已驗證 | I0 |
| G4 | Sakura 能否保留佔位符 | 見下方 §17.2 實測；採用 `<n>` | 已驗證 | I1 |
| G5 | 修改提示詞或換模型時，沒有方法判斷品質變好或變差 | 建立固定評測集 `tests/eval/`：控制碼、人名、換行、擬聲詞、選項等困難句各 50 句；比較 T11 失敗率、佔位符保留率、術語遵循率，並在 GUI 做盲測 A/B 比較。提示詞改版前必須跑過 | 已定案 | I1 |
| G6 | 存檔相容性：RPG Maker 存檔內含角色名等字串 | 無法由工具修正，改在寫回前提示：舊存檔中的角色名維持原文，建議開新遊戲；還原後，翻譯期間建立的存檔會保留中文名 | 已定案（提示） | I1 |
| G7 | 多個遊戲同時翻譯會搶同一個 Sakura 伺服器或 Codex 額度 | 每個後端一個**工作佇列**，同一時間只執行一個翻譯工作，其餘排隊；提取與寫回不受限 | 已定案 | I2 |
| G8 | 開源使用者回報問題時，沒有不含遊戲文本的診斷資料 | 「匯出診斷包」：工具版本、引擎、偵測分數、檔案清單與雜湊、錯誤日誌；不含遊戲文本、token、路徑中的使用者名稱 | 已定案 | I2 |
| G9 | 外部字典的匹配可能誤判 | 只查詢術語候選，不比對全文；字典譯名需人工確認（§6.10） | 已定案 | I3 |

### 17.1 RPG Maker MZ 提取範圍（I0 規格）
| 來源 | 欄位 | kind | 預設 |
|---|---|---|---|
| 事件指令 101 | `parameters[4]` 說話者名稱（MZ 新增） | name | 翻 |
| 事件指令 401 | 連續的 401 合併為一則訊息（E7） | dialogue | 翻 |
| 事件指令 405 | 捲動文字，連續合併 | dialogue | 翻 |
| 事件指令 102／402 | 選項清單／選項分支文字 | choice | 翻 |
| 事件指令 320／324／325 | 變更名稱／暱稱／簡介 | name／system | 翻 |
| 事件指令 108／408 | 註解 | — | 不提取 |
| 事件指令 355／655、657 | 腳本與插件參數顯示行（實測 657 出現 30 萬次，是第二多的指令） | — | 不提取 |
| 事件指令 357 | 插件指令參數（含 JSON 編碼的結構／陣列參數）；文字類鍵名 | system | 翻 |
| 事件指令 357 | 其他鍵名（id、檔名、目的地等） | other | `uncertain`（預設不翻） |
| 各資料庫的 `note` | 備註標籤（插件讀取的設定） | other | 不提取 |
| `Actors.json` | `name`、`nickname`、`profile` | name／system | 翻 |
| `Skills.json`、`Items.json`、`Weapons.json`、`Armors.json` | `name`、`description`；技能另含 `message1`、`message2` | name／system | 翻 |
| `Classes.json`、`Enemies.json` | `name` | name | 翻 |
| `States.json` | `name`、`message1`～`message4` | name／system | 翻 |
| `System.json` | `gameTitle`、`currencyUnit`、`terms`、`elements`、`skillTypes`、`weaponTypes`、`armorTypes`、`equipTypes` | system／ui | 翻 |
| `System.json` | `switches`、`variables`（只在編輯器顯示） | — | 不提取 |
| `System.json` | `locale` | — | **維持 `ja`**：MZ 用它決定命名輸入畫面與字寬判斷，改成 `zh` 會讓命名輸入改用拉丁字母表 |
| `MapXXX.json` | `displayName` 與事件；`CommonEvents.json`、`Troops.json` 的事件 | 同上 | 翻 |
| `MapInfos.json` | `name`（只在編輯器顯示） | — | 不提取 |
| `js/plugins.js` | 插件參數 | other | `uncertain`（預設不翻） |

核對結果（46 款 MZ 遊戲、3,196 個 JSON 資料檔）：
- 全部可直接用 UTF-8 解析，沒有加密的資料檔（圖片和音效的 `.png_`、`.ogg_` 有加密，但不影響文本）。
- 102 個檔案帶 UTF-8 BOM，寫回時必須保留（manifest 已記錄 `bom`）。
- 有 156,650 個 `101` 指令帶說話者名稱，是術語種子（E10）的主要來源。
- 上表的資料庫欄位都實際存在。
- 控制碼除了標準的 `\C[n]` 這類，還有插件自訂、不帶 `[]` 的寫法（例如 `\ct`）；MZ Plugin 的 `control_codes` 必須涵蓋 `\\[A-Za-z]+(\[[^\]]*\])?` 與 `\\[{}\\.|!><^$]`。

### 17.2 佔位符實測（G4）
- **條件**：從使用者遊戲庫的 MZ 遊戲中隨機抽 200 句含控制碼、且含假名的真實 `401` 對話；用本機 Sakura 7B（`sakura-7b-qwen2.5-v1.0-iq4xs`，8 槽）翻譯，每批 10 句，與實際的 Batcher 相同。
- **判定**：每句的佔位符種類與數量都與原文相同，才算保留成功。

| 樣式 | 批次行數錯亂 | 佔位符保留率 |
|---|---:|---:|
| 不遮蔽（直接送原始控制碼） | 1／20 | 77.4% |
| `⟦n⟧` | 5／20 | 54.7%（模型常改寫成 `﹝n﹞`） |
| `{n}` | 8／20 | 14.2% |
| **`<n>`** | **1／20** | **94.2%；補上不帶 `[]` 控制碼的正規式後，另一組樣本 98.9%** |
| `<n>` + 句首句尾控制碼不送模型 | 2／20 | 97.2%（沒有改善，不採用） |

**結論**：Sakura 採用 `<n>`。剩下的失敗（漏掉或重複佔位符、批次行數錯亂）由 T11 驗證攔截，再依 §6.4 拆批重送。Codex 改用 JSON 輸出，不受行數錯亂影響，但它的佔位符保留率仍要在 I2 用 §17 G5 的評測集測過；之後每次換模型或改提示詞也都要重測。

---

## 18. 待決事項
1. ~~GitHub 帳號或組織~~ 已定：<https://github.com/Honguan/YakuPatch>。

### 已決事項
| 日期 | 決定 |
|---|---|
| 2026-10-24 | 發佈改為 BallonsTranslator 式安裝包一鍵啟動：免設定壓縮包（Python 嵌入版，雙擊 `launch_win.bat`）＋一行 PowerShell 安裝腳本，取代 2026-10-23 的 PyInstaller `YakuPatch.exe`（§12.1、§15 I6） |
| 2026-10-23 | ~~最終交付為可下載、雙擊即可啟動的 `YakuPatch.exe`（PyInstaller onedir zip，發佈於 GitHub Releases），不要求使用者安裝 Python~~（已由 2026-10-24 取代） |
| 2026-10-22 | 各疊代實機驗收改為隨機 3 款（原 I1、I4 為 10 款）；每階段先跑單元／合規測試，再用 3 款走完提取→翻譯→寫回→啟動→還原 |
| 2026-10-21 | Codex 後端內建，於 I2 實作（§6.6）；分批交換檔仍保留作為替代路徑 |
| 2026-10-21 | 不做繁簡轉換；Sakura 譯文維持簡體（§6.7） |
| 2026-10-21 | Codex 預設輸出繁體中文，由提示詞指定，可在設定改為簡體（§6.5） |
| 2026-10-21 | 遊戲已是中文（繁或簡）或內建中文語言包時，預設不翻譯（§5.1） |
| 2026-10-21 | 專案名稱 **YakuPatch**（「訳」＝翻譯 + Patch）；GitHub 儲存庫同名；Python 套件名 `yakupatch`（GitHub 與 PyPI 查無同名專案） |
| 2026-10-21 | 備份放在遊戲資料夾內的 `_yakupatch_backup/`，不提供集中目錄（§9） |
