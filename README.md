# YakuPatch

[中文](#中文) | [English](#english)

---

## 中文

YakuPatch 把日文遊戲翻譯成中文。它直接修改遊戲的文字檔，不注入程式；寫入前先備份，隨時可以還原。

### 支援的遊戲引擎

| 引擎 | 支援範圍 | 寫回方式 |
|---|---|---|
| RPG Maker MV／MZ | `data/*.json`（圖片、音訊加密不影響） | 直接改寫 JSON |
| TyranoScript | 散檔或 `app.asar` | 散檔直接改寫；asar 只改檔頭，劇本放在 `app.asar.unpacked/` |
| Ren'Py | 編譯後的 `.rpyc`（散檔或 `.rpa` 內） | 不改原腳本，新增語言 `game/tl/yakupatch/` |
| KiriKiri | 未加密 `.xp3` | 不改原封包，新增 `patch.xp3` |
| Unity | Mono 版：UI、Utage、Naninovel、自訂類別、`Assembly-CSharp.dll` 字串 | 改寫資產檔，字型換成 Windows 內建中文字型 |
| Godot | `.pck` 內的文字場景與 Godot 3 腳本 | 改寫 `.pck`，字型換成中文字型 |
| Artemis | `.pfs` 內的劇本與表格 | 不改原封包，新增 `.pfs.NNN` |
| Wolf RPG | 未加密散檔（地圖、共通事件、資料庫） | 直接改寫，附中文替換字型 |

加密的遊戲不支援，YakuPatch 不會嘗試解密。

### 環境需求

| 項目 | 需求 |
|---|---|
| 作業系統 | Windows 10／11，64 位元 |
| Python | **免安裝壓縮包與安裝腳本已內附 Python 3.12，不需要另外安裝。** 只有從原始碼執行時，才需要自行安裝 Python 3.11 以上 |
| Visual C++ 可轉散發套件（x64） | 大多數電腦已有。缺少時，啟動器會顯示[下載網址](https://aka.ms/vs/17/release/vc_redist.x64.exe) |
| 翻譯後端（擇一） | **Sakura**：自行啟動的 llama-server（本機或區網）<br>**Codex**（實驗性）：有 Codex 額度的 ChatGPT 帳號 |
| 網路 | 安裝腳本、補裝套件、Codex 需要；使用本機 Sakura 翻譯時不需要 |

### 安裝

選一種即可，兩種結果相同。程式資料夾自帶 Python 與套件，不寫登錄檔，也不動系統上的 Python。

- **免安裝壓縮包（建議）**：到 [Releases](https://github.com/Honguan/YakuPatch/releases) 下載 `YakuPatch_win.zip`，解壓後雙擊 `launch_win.bat`。
- **安裝腳本**：在要安裝的資料夾開啟 PowerShell，執行：

  ```powershell
  irm https://raw.githubusercontent.com/Honguan/YakuPatch/main/scripts/install.ps1 | iex
  ```

第一次啟動可能要等幾十秒，命令視窗會顯示進度點，主視窗出現後自動關閉。

### 使用方式

1. 啟動 Sakura 的 llama-server，或在設定裡登入 Codex。
2. 雙擊 `launch_win.bat`，把遊戲資料夾加入左側的遊戲庫。
3. 依上方步驟條操作：偵測 → 語言預檢 → 提取 → 翻譯 → 寫回。
4. 要還原遊戲時，按「還原遊戲」。

命令列版本：

```bat
yakupatch.bat extract   "D:\Games\MyGame"
yakupatch.bat translate "D:\Games\MyGame" --backend sakura
yakupatch.bat apply     "D:\Games\MyGame" --yes
yakupatch.bat restore   "D:\Games\MyGame"
```

所有指令與參數請看 `yakupatch.bat --help`。

### 資料位置

- 專案、設定、字典：`%USERPROFILE%\.yakupatch`。更新或刪除程式資料夾不會影響這些資料。
- 遊戲備份：遊戲資料夾內的 `_yakupatch_backup\`。
- 錯誤記錄：`%USERPROFILE%\.yakupatch\logs\gui.log`。

### 更新

下載新版壓縮包覆蓋程式資料夾，或重新執行安裝腳本。有新版時，視窗下方的狀態列會顯示下載連結。

> [!WARNING]
> Codex 使用非公開端點，可能隨時失效；翻譯時遊戲文本會送到 OpenAI。

### 從原始碼執行

需要 Python 3.11 以上：

```bash
pip install -e .
yakupatch-gui
```

執行測試：`python -X utf8 -m unittest discover -s tests -t tests`。設計與進度見 [PROJECT_PLAN.md](PROJECT_PLAN.md)。

### 授權

[GPL-3.0-or-later](LICENSE)。Sakura 提示詞與 Codex 請求格式參考 [BallonsTranslator](https://github.com/dmMaze/BallonsTranslator)（GPL-3.0）。

僅供翻譯你合法持有的遊戲、個人使用。分享時請只分享交換檔，不要分享遊戲檔案。

---

## English

YakuPatch translates Japanese games into Chinese. It edits the game's text files directly, without injecting code. It backs up every file before writing, so you can restore the game at any time.

### Supported engines

| Engine | Coverage | How it writes |
|---|---|---|
| RPG Maker MV/MZ | `data/*.json` (encrypted images and audio are fine) | Rewrites the JSON |
| TyranoScript | Loose files or `app.asar` | Loose files in place; for asar, patches the header and puts scripts in `app.asar.unpacked/` |
| Ren'Py | Compiled `.rpyc` (loose or inside `.rpa`) | Leaves the scripts alone and adds a language in `game/tl/yakupatch/` |
| KiriKiri | Unencrypted `.xp3` | Leaves the archive alone and adds `patch.xp3` |
| Unity | Mono builds: UI, Utage, Naninovel, custom classes, `Assembly-CSharp.dll` strings | Rewrites the asset files and switches fonts to a built-in Windows Chinese font |
| Godot | Text scenes and Godot 3 scripts inside `.pck` | Rewrites the `.pck` and switches fonts to a Chinese font |
| Artemis | Scripts and tables inside `.pfs` | Leaves the archive alone and adds `.pfs.NNN` |
| Wolf RPG | Unencrypted loose files (maps, common events, databases) | Rewrites in place and ships a Chinese substitute font |

Encrypted games are not supported. YakuPatch never tries to decrypt them.

### Requirements

| Item | Requirement |
|---|---|
| OS | Windows 10/11, 64-bit |
| Python | **The portable zip and the install script bundle Python 3.12; you do not need to install Python.** Only running from source needs Python 3.11 or later |
| Visual C++ Redistributable (x64) | Most PCs already have it. If it is missing, the launcher shows the [download link](https://aka.ms/vs/17/release/vc_redist.x64.exe) |
| Translation backend (one of) | **Sakura**: a llama-server you start yourself (local or LAN)<br>**Codex** (experimental): a ChatGPT account with Codex quota |
| Network | Needed for the install script, package repair, and Codex. Not needed to translate with a local Sakura server |

### Install

Pick one; both give the same result. The program folder carries its own Python and packages. It writes nothing to the registry and leaves any system Python alone.

- **Portable zip (recommended)**: download `YakuPatch_win.zip` from [Releases](https://github.com/Honguan/YakuPatch/releases), unzip it, and double-click `launch_win.bat`.
- **Install script**: open PowerShell in the folder to install into and run:

  ```powershell
  irm https://raw.githubusercontent.com/Honguan/YakuPatch/main/scripts/install.ps1 | iex
  ```

The first start can take a few dozen seconds. The console window shows progress dots and closes when the main window opens.

### Usage

1. Start your Sakura llama-server, or sign in to Codex in Settings.
2. Double-click `launch_win.bat` and add a game folder to the library on the left.
3. Follow the step bar at the top: Detect → Language check → Extract → Translate → Apply.
4. To restore the game, click 還原遊戲 (Restore game).

Command line:

```bat
yakupatch.bat extract   "D:\Games\MyGame"
yakupatch.bat translate "D:\Games\MyGame" --backend sakura
yakupatch.bat apply     "D:\Games\MyGame" --yes
yakupatch.bat restore   "D:\Games\MyGame"
```

Run `yakupatch.bat --help` for every command and option.

### Where data lives

- Projects, settings, dictionaries: `%USERPROFILE%\.yakupatch`. Updating or deleting the program folder leaves them intact.
- Game backups: `_yakupatch_backup\` inside the game folder.
- Error log: `%USERPROFILE%\.yakupatch\logs\gui.log`.

### Update

Unzip the new release over the program folder, or run the install script again. When a new version is out, the status bar shows a download link.

> [!WARNING]
> Codex uses a non-public endpoint and may stop working at any time. Game text is sent to OpenAI when you translate with it.

### Run from source

Needs Python 3.11 or later:

```bash
pip install -e .
yakupatch-gui
```

Run the tests with `python -X utf8 -m unittest discover -s tests -t tests`. Design and progress: [PROJECT_PLAN.md](PROJECT_PLAN.md) (Chinese).

### License

[GPL-3.0-or-later](LICENSE). The Sakura prompts and the Codex request format follow [BallonsTranslator](https://github.com/dmMaze/BallonsTranslator) (GPL-3.0).

For translating games you legally own, for personal use only. Share only the exchange files, never the game files.
