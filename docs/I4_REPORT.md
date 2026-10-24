# I4 交付報告

分支：`feat/i4`（從 `main` 54069be 開）。日期：2026-10-24。

## 修改檔案

| 階段 | 檔案 | 內容 |
|---|---|---|
| A | `yakupatch/vfs.py` | XP3 讀取（索引鏈、zlib 索引、分段）與寫出（未壓縮內容 + zlib 索引）；`封包!內部路徑` 支援 `.xp3` |
| A | `yakupatch/engines/kirikiri.py` | KiriKiri：讀 `.ks`／`.tjs`，譯文寫成新的 `patch.xp3`（或下一個空著的 `patchN.xp3`） |
| A | `yakupatch/engines/tyrano.py` | 標籤正則改為認得引號內的 `]`；屬性分類改由 `attr_kind()` 決定，供 KiriKiri 覆寫 |
| B | `yakupatch/engines/unity.py` | Unity：UI 元件、Utage、Naninovel、本地化表格、自訂類別的句子、DLL 字串；整檔重寫；catalog CRC；字型統一（TMP 與舊版 UI Text） |
| B | `yakupatch/clr.py` | .NET 組件的字串區（#US）讀取與重建，修正每個 `ldstr` 參照 |
| B | `yakupatch/engines/base.py`、`extract.py`、`writeback.py`、`engines/renpy.py` | `decode()`、`outputs()` 多傳遊戲資料夾，Unity 要靠它讀同一款遊戲的其他檔案 |
| B | `yakupatch/engines/__init__.py`、`pyproject.toml` | 註冊 Unity；新增依賴 `UnityPy>=1.25`、`TypeTreeGeneratorAPI>=0.0.10` |
| 文件 | `PROJECT_PLAN.md` | 發佈改為 BallonsTranslator 式安裝包一鍵啟動（§12.1、§15 I6） |
| 測試 | `tests/test_kirikiri.py`、`tests/test_unity.py`、`tests/fixtures/clr/Lines.cs`（與編譯好的 `Lines.dll`） | 新增 13 個測試 |

## 邏輯摘要

### KiriKiri

- **讀取**：只支援未加密的 XP3。腳本開頭是 `\xfe\xfe`（吉里吉里簡易加密）或無法解碼時，直接報錯，不猜。KiriKiri Z 的雜湊檔名不支援。
- **提取範圍**：
  - `.ks`：沿用 Tyrano 的 KAG 解析。另外：
    - `【名字】` 行提取成 `name`，並帶給同一則訊息作說話者。
    - 標籤的 `text` 屬性：標籤名含 select／link／button／choice 時算選項，其餘算 UI；`[ruby text=]` 是讀音，不提取。
    - `//` 開頭的行是開發註解，跳過。
  - `.tjs`：只取白名單內玩家看得到的字串：`KAGMenuItem` 選單、`caption=`、`askYesNo()`、`System.title`。其他字串可能是字型名稱、禁則表或除錯文字，不碰。
- **寫回**：不改原封包。改過的腳本存成 UTF-16LE 加 BOM（KiriKiri 原生支援，任何中文字都能存），打包成新的 `patch.xp3`。遊戲已有 `patch.xp3` 時用下一個空著的編號。
- **檔名衝突**：KiriKiri 的 patch 封包依檔名覆蓋（不分目錄）。原封包裡有同名檔案時，那幾個檔案不翻，避免一個 patch 檔蓋掉兩個原檔。
- **還原**：刪除新增的 patch 封包即可，原封包從頭到尾沒動。

### Unity

- **讀取**：
  - 以 UnityPy 讀 `*_Data` 下的 `.assets`、`level*`、`globalgamemanagers`，以及 Naninovel 的劇本 bundle（`StreamingAssets/aa/*/naninovel_assets_naninovel/scripts/*.bundle`）。
  - 檔案沒有存 TypeTree 時，用 TypeTreeGeneratorAPI 從遊戲 DLL 產生。這個套件會把清單欄位標成元素型別（例如 `string`），讀取前改回 `vector`。
  - 同一款遊戲共用一份環境，跨檔參照（MonoScript）才找得到。全部從記憶體的位元組載入，不開著檔案，寫回時 Windows 才換得掉檔案。
- **提取規則**（只取日文）：
  - UI 元件：`TextMeshProUGUI`／`TextMeshPro` 的 `m_text`、`Text` 的 `m_Text`。
  - Utage：`AdvImportBook` 劇本表只取 `Text` 欄。`Selection*` 指令是選項，其他是對話，`Arg1` 是說話者。`AdvChapterData` 設定表取 `NameText`、`Japanese`、`Title` 欄。
  - Utage 本地化表（TextAsset，表頭含 `Japanese`）。
  - Naninovel：`Script` 物件裡 SerializeReference 的 `PrintText.Text`（對話）、`PrintText.AuthorId`（說話者名）、`AddChoice.ChoiceSummary`（選項）。英文的 AuthorId 是設定過的角色 ID，不提取；翻了會找不到角色。
  - 其他自訂類別（例如 18 的 `MessageData`）：某個欄位（清單元素算同一欄位）只要有一句「像句子」的日文（含標點，或至少 6 個字），整個欄位的日文都提取。物件名稱，以及 Live2D（`Cubism*`）、`TMP_*` 的內部名稱跳過。只有短詞的欄位不提取，可能是程式用的鍵值。
  - Mono 組件 `Managed/Assembly-CSharp*.dll` 寫死的字串：只取被 `ldstr` 使用、像句子、不像路徑或檔名的日文。
- **DLL 寫回**（`clr.py`）：
  - 依序重建 #US 字串區，換上譯文，記下每個字串的新位置。
  - 逐一解析每個方法的 IL，把 `ldstr` 的參數改指到新位置；遇到不認得的指令就整個 DLL 放棄，不猜。
  - PE 標頭通常沒有空間新增區段，所以把新的中繼資料（只有 #US 變了）接在最後一個區段後面，延長該區段，並更新 CLI 標頭的中繼資料位置。程式本體都不搬動。
  - 帶數位簽章的 DLL 不處理（改了簽章會失效）。
- **寫回**：整檔重寫，原檔留在備份。
  - bundle 以 LZ4 重新壓縮。
  - Addressables catalog 對每個 bundle 記了 CRC，改過的 bundle 會被拒絕載入。所以把 catalog 裡的 `m_Crc` 改成 0，用空白補足原本長度，每筆資料長度不變。
- **字型（F20）**：遊戲的日文 TMP 字型多半是靜態字表，只有約 6,000 個漢字，缺「說、們、啊、試」這類字，會變成方塊。
  - 找出 TMP_Settings 的預設字型，再找它的**動態**備援字型（執行時才從字型檔產生字形）。
  - 把這個備援字型的字型資料換成 Windows 內建的中文字型：
    - 譯文以簡體為主就用簡體字型，以繁體為主就用繁體字型。
    - 遊戲中文字型多數是明體 → 細明體／宋體；否則 → 正黑體／雅黑，原字型是粗體時用粗體版。
  - 備援字型改用多張 2048 貼圖，並加到全域備援清單。
  - 清空遊戲所有靜態中文字型的字表（含 bundle 內的字型），讓每個字都落到同一個中文字型，整句字型一致。第一次實測只補缺字，結果細黑體和粗明體混在同一句，使用者選擇改成統一字型。
  - 統一時只把漢字、假名、英數和全形字交給中文字型；其他符號留在遊戲字型。18 用 `┻` 代表愛心，遊戲字型把它畫成愛心，所以要保留。
  - 遊戲沒有動態備援字型時，退回舊做法：把同檔案內其他中文字型接成備援。
  - 舊版 UI Text 用的內嵌 Unity Font：字型資料換成同風格的 Windows 字型（名稱帶 Mincho 用宋體／細明體，帶 Bold 用粗體黑體）；TMP 自己在用的來源字型不動。

## 測試結果

```
python -X utf8 -m unittest discover -s tests
Ran 85 tests ... OK
```

I3 結束時有 72 個測試，I4 新增 13 個：

- XP3 往返讀寫。
- KiriKiri 解析：說話者、選項、ruby、`//` 註解、TJS 白名單。
- KiriKiri 寫回：已有 `patch.xp3` 時建立 `patch2.xp3`、同名檔衝突跳過、還原後與原檔一致。
- Unity：Utage 劇本與設定欄、UI 元件、自訂類別的欄位規則、Cubism 跳過、本地化表格、`_set`、Naninovel 規則、catalog CRC 歸零且長度不變。
- DLL：讀出所有 `ldstr` 字串、只取句子、改寫後每個 `ldstr` 都指到正確的新字串、改過的 DLL 可再讀回。
- 開發時另以 Windows 的 .NET Framework 實際載入改過的 `Lines.dll` 並呼叫方法：switch、迴圈、字串串接都正確，譯文比原文長也正常。

## 遊戲庫調查（唯讀）

- **Unity**：93 款。其中 35 款 IL2CPP、55 款以日文為主。框架：TMP 88、XUnity 譯者外掛殘留 51、Naninovel 14、I2 Localization 11、Utage 9、Fungus 6。
- 結論：TMP 幾乎每款都有，字型替換是 Unity 的必要條件；Naninovel 與 Utage 是最常見的劇本框架。

## 實機驗收

**選遊戲的條件**：原文是日文、遊戲沒有附中文，語言探測結果為 `ja`。原始遊戲唯讀，完整複製到 `C:\YakuPatchTest\NN_<引擎>\` 後測試；專案資料放 `C:\YakuPatchTest\_projects\`。

| 資料夾 | 引擎 | 來源 | 句數 | 翻譯 | 已翻／失敗 | 寫回 |
|---|---|---|---|---|---|---|
| `15_kirikiri` | KiriKiri 2，未加密 XP3 | （路徑略） | 7,371 | Codex，失敗句由 Sakura 補翻 | 7,340／11（另 20 句不翻） | 新增 `patch.xp3` |
| `16_unity` | Unity Mono，Utage | （路徑略） | 10,619 | Sakura | 10,605／14 | level0、resources.assets、sharedassets0.assets |
| `17_unity` | Unity Mono，Naninovel（Addressables） | （路徑略） | 6,513 | Sakura | 6,510／3 | 158 個劇本 bundle、關卡與字型檔、catalog |
| `18_unity` | Unity Mono，自訂類別 + 舊版 UI Text | （路徑略） | 612 | Sakura | 611／1 | 關卡、sharedassets（含字型）、`Assembly-CSharp.dll` |

- **15_kirikiri**：
  - Codex 第一輪 7,281 句失敗 481 句，重跑兩輪仍剩 243 句，失敗集中在成人內容。之後改用 Sakura 補翻，剩 11 句失敗，保留日文。
  - 使用者回報介面還有日文和英文，補上 TJS 白名單（選單、對話框、標題）後重新寫回。
  - 遊戲在繁中 Windows（cp950）下，原檔 `KLayers.ks` 就會出現轉換錯誤，無法啟動；用 Locale Emulator 以日文語系啟動即正常。這是遊戲本身的限制，與寫回無關。
  - **使用者實機檢查：通過。**
- **16_unity**：先用假譯文驗證寫回與讀回，撤銷後以 Sakura 翻譯。失敗 14 句都是佔位符不符。**使用者實機檢查：通過。**
- **17_unity**：
  - 先用假譯文驗證 bundle 寫回與 catalog CRC。第一次只補缺字，使用者截圖顯示字型混用；改成統一字型。
  - 寫回時遊戲正開著，bundle 被鎖住，寫回失敗並自動回滾，關閉遊戲後重跑成功。
  - **使用者實機檢查：通過，譯文正常、字型一致、沒有方塊字。**
- **18_unity**：從遊戲庫符合條件的 Mono 款中以固定種子隨機抽選。
  - 第一次抽到的遊戲（Naninovel 新版，文字在 `textMap`），原文是英文，不符條件，副本刪除後重抽。
  - 第一輪只提取到 45 句介面文字：對話在自訂類別 `MessageData`。加入自訂類別規則後為 498 句。
  - 使用者回報對話還有日文：一是欄位裡沒標點的短句被略過，改成以欄位判斷；二是約 100 多句台詞寫死在 `Assembly-CSharp.dll`，新增 DLL 字串支援後補上 107 句。
  - 失敗 1 句是 502 字的人名清單，超出長度限制，保留原文。
  - **使用者實機檢查：通過。**
- **還原**：備份在各遊戲資料夾的 `_yakupatch_backup`。KiriKiri 的還原（刪除 patch 封包、與原檔雜湊一致）由單元測試涵蓋；Unity 的還原走既有的整檔備份流程。這次沒有在實機複本上執行還原。

**I4 驗收狀態**：

- KiriKiri 未加密款可用：達成（1 款）。
- Unity 隨機 3 款至少 2 款可用且無方塊字：**3 款全部通過**（16、17、18）。

## 新增 shortcut 與已知限制

- **字型用 Windows 系統字型**：改好的遊戲只能自己玩，不能散佈。之後應改成使用者可指定、授權允許散佈的字型（例如思源黑體）。
- **字型筆畫較細**：Windows 內建的明體、宋體比遊戲原本的字型細，粗細不一定接近原作。
- **字型偵測只看名稱**：明體／黑體、粗體的判斷是看字型名稱的關鍵字，名稱看不出來時會猜錯。
- **TTC 字型**：正黑體、雅黑等是 TTC 字型集合檔，Unity 讀第 0 個字面。這次實測可用，但不保證每個 Unity 版本都行。
- **Unity 未支援**：IL2CPP 的 `global-metadata.dat` 字串、I2 Localization、Fungus、Naninovel 新版的 `textMap`，以及 Naninovel 以外的 Addressables bundle。
- **自訂類別與 DLL 是猜的**：靠「像句子」判斷，可能把當資料用的字串也翻了（例如 18 的人名清單，這次剛好翻譯失敗沒寫回）；只有短詞的欄位或 DLL 字串（例如 18 的換裝選項「スカート」，DLL 裡剩 66 個）不翻。
- **16、17 沒有重跑**：自訂類別規則是在 16、17 驗收之後才加的，兩款沒有用新規則重新寫回；17 的首頁台詞 `HomeQuote`、地名 `StageObject` 重跑後會被提取，但未驗證。
- **Naninovel 說話者**：只翻日文的 AuthorId；角色設定裡的 `DisplayName` 沒有處理。
- **KiriKiri**：不支援加密封包、KiriKiri Z 雜湊檔名、`.scn`（KiriKiri Z 的編譯劇本）。
- **寫回回滾**：寫入中途失敗時，回滾遇到第一個錯誤就停止。這是既有流程的弱點，這次沒有修。
- **Codex 對成人內容失敗率高**：15_kirikiri 第一輪約 6.6%。實機測試預設改用 Sakura。

## Commit

| 階段 | Commit |
|---|---|
| A：KiriKiri 引擎與 patch.xp3 | b594488 |
| A：KAG3 系統介面字串（.tjs） | 0486b81 |
| 企劃書：一鍵啟動安裝包 | 5b811b1 |
| B：Unity 引擎、Utage、Naninovel、字型統一 | 8970d9a |
| B：自訂類別、DLL 字串、舊版字型統一 | daaf61c |
| 文件 | 見本報告所在 commit |
