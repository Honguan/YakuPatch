<div align="center">
  <img src="yakupatch/gui/icon.png" width="128" alt="YakuPatch">
  <h1>YakuPatch</h1>
  <p><strong>把日文遊戲一鍵翻成中文的補丁工具</strong><br>直接修改遊戲文字檔，不注入程式；寫入前先備份，隨時可以還原。</p>
  <p>
    <a href="https://github.com/Honguan/YakuPatch/releases/latest"><img src="https://img.shields.io/github/v/release/Honguan/YakuPatch" alt="Release"></a>
    <img src="https://img.shields.io/badge/platform-Windows%2010%20%7C%2011-0078d4" alt="Windows 10 | 11">
    <a href="LICENSE"><img src="https://img.shields.io/badge/license-GPL--3.0-blue" alt="GPL-3.0"></a>
  </p>
  <p>繁體中文 | <a href="README_EN.md">English</a></p>
</div>

---

## 軟體介紹 🧾

* **直接處理遊戲，不必另找提取工具**
  * 🎮 **8 種引擎**：自動偵測引擎，提取文本、翻譯、寫回一次完成。
  * 🛡️ **安全可還原**：寫回前先備份，一鍵還原；每次翻譯都能單獨撤銷。
  * 🈶 **已是中文就停下**：遊戲已是中文或內建中文語言包時，不會重複翻譯。

* **翻譯品質**
  * 🔍 **逐句檢查**：控制碼、換行數、長度與重複不合格的譯文不寫入，該句保留原文。
  * 📖 **術語一致**：自動整理人名與術語，可查外部字典；翻譯後做全文 QA，標出同名異譯與漏譯。
  * 🔤 **不再出現方塊字**：Unity、Godot、Wolf RPG 會自動換成中文字型。

* **本地或雲端**
  * 💻 **Sakura**：自己的電腦跑本地模型，不用連網、不花錢。
  * ☁️ **Codex**（實驗性）：用 ChatGPT 帳號的 Codex 額度翻譯。
  * 🔌 **OpenAI 相容 API**：DeepSeek、OpenAI、OpenRouter、Ollama、LM Studio 等，填網址與金鑰即可。

---

## 支援的遊戲引擎 🎮

| 引擎 | 支援範圍 | 寫回方式 |
|:---:|---|---|
| RPG Maker MV／MZ | `data/*.json`（圖片、音訊加密不影響） | 直接改寫 JSON |
| TyranoScript | 散檔或 `app.asar` | 散檔直接改寫；asar 只改檔頭，劇本放在 `app.asar.unpacked/` |
| Ren'Py | 編譯後的 `.rpyc`（散檔或 `.rpa` 內） | 不改原腳本，新增語言 `game/tl/yakupatch/` |
| KiriKiri | 未加密 `.xp3` | 不改原封包，新增 `patch.xp3` |
| Unity | Mono 版：UI、Utage、Naninovel、自訂類別、`Assembly-CSharp.dll` 字串 | 改寫資產檔，字型換成 Windows 內建中文字型 |
| Godot | `.pck` 內的文字場景與 Godot 3 腳本 | 改寫 `.pck`，字型換成中文字型 |
| Artemis | `.pfs` 內的劇本與表格 | 不改原封包，新增 `.pfs.NNN` |
| Wolf RPG | 未加密散檔（地圖、共通事件、資料庫） | 直接改寫，附中文替換字型 |

> [!NOTE]
> 加密的遊戲不支援，YakuPatch 不會嘗試解密。

---

## 環境需求 🧰

| 項目 | 需求 |
|:---:|---|
| 作業系統 | Windows 10／11，64 位元 |
| Python | **免安裝壓縮包與安裝腳本已內附 Python 3.12，不需要另外安裝。** 只有從原始碼執行時，才需要自行安裝 Python 3.11 以上 |
| Visual C++ 可轉散發套件（x64） | 大多數電腦已有。缺少時，啟動器會顯示[下載網址](https://aka.ms/vs/17/release/vc_redist.x64.exe) |
| 翻譯後端（擇一） | **Sakura**：自行啟動的 llama-server（本機或區網）<br>**Codex**（實驗性）：有 Codex 額度的 ChatGPT 帳號<br>**OpenAI 相容 API**：API 位址與金鑰（本機伺服器可不填金鑰）；需選通用對話模型 |
| 網路 | 安裝腳本、補裝套件、Codex、雲端 API 需要；使用本機 Sakura 翻譯時不需要 |

---

## 三步上手 📢

* **第一步：安裝**
  * **免安裝壓縮包（建議）**：到 [Releases](https://github.com/Honguan/YakuPatch/releases/latest) 下載 `YakuPatch_win.zip`，解壓後雙擊 `launch_win.bat`。
  * **安裝腳本**：在要安裝的資料夾開啟 PowerShell，執行：

    ```powershell
    irm https://raw.githubusercontent.com/Honguan/YakuPatch/main/scripts/install.ps1 | iex
    ```

  * 第一次啟動可能要等幾十秒，命令視窗會顯示進度點，主視窗出現後自動關閉。

* **第二步：準備翻譯後端**
  * 啟動 Sakura 的 llama-server，在「設定」填入位址後按「測試連線」；或在「設定」登入 Codex；或在「設定 → OpenAI 相容」填入 API 位址、金鑰與模型。

* **第三步：加入遊戲並翻譯**
  * 把遊戲資料夾加入左側的遊戲庫，依上方步驟條操作：偵測 → 語言預檢 → 提取 → 翻譯 → 寫回。
  * 要還原遊戲時，按「還原遊戲」。

---

<details>
<summary><h2>命令列 ⌨️</h2></summary>

```bat
yakupatch.bat extract   "D:\Games\MyGame"
yakupatch.bat translate "D:\Games\MyGame" --backend sakura
yakupatch.bat apply     "D:\Games\MyGame" --yes
yakupatch.bat restore   "D:\Games\MyGame"
```

所有指令與參數請看 `yakupatch.bat --help`。

</details>

<details>
<summary><h2>資料位置與更新 🗂️</h2></summary>

| 項目 | 位置 |
|:---:|---|
| 專案、設定、字典 | `%USERPROFILE%\.yakupatch`（更新或刪除程式資料夾不影響） |
| 遊戲備份 | 遊戲資料夾內的 `_yakupatch_backup\` |
| 錯誤記錄 | `%USERPROFILE%\.yakupatch\logs\gui.log` |

**更新**：下載新版壓縮包覆蓋程式資料夾，或重新執行安裝腳本。有新版時，視窗下方的狀態列會顯示下載連結。

</details>

<details>
<summary><h2>從原始碼執行 🛠️</h2></summary>

需要 Python 3.11 以上：

```bash
pip install -e .
yakupatch-gui
```

執行測試：`python -X utf8 -m unittest discover -s tests -t tests`。設計與進度見 [PROJECT_PLAN.md](PROJECT_PLAN.md)。

</details>

> [!WARNING]
> Codex 使用非公開端點，可能隨時失效；翻譯時遊戲文本會送到 OpenAI。雲端 OpenAI 相容 API 也會收到遊戲文本並依用量計費。

---

## 授權 📜

[GPL-3.0-or-later](LICENSE)。Sakura 提示詞與 Codex 請求格式參考 [BallonsTranslator](https://github.com/dmMaze/BallonsTranslator)（GPL-3.0）。

僅供翻譯你合法持有的遊戲、個人使用。分享時請只分享交換檔，不要分享遊戲檔案。
