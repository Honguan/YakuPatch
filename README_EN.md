<div align="center">
  <img src="yakupatch/gui/icon.png" width="128" alt="YakuPatch">
  <h1>YakuPatch</h1>
  <p><strong>Translate Japanese games into Chinese in one click</strong><br>Edits the game's text files directly, without injecting code. Every file is backed up before writing, so you can restore the game at any time.</p>
  <p>
    <a href="https://github.com/Honguan/YakuPatch/releases/latest"><img src="https://img.shields.io/github/v/release/Honguan/YakuPatch" alt="Release"></a>
    <img src="https://img.shields.io/badge/platform-Windows%2010%20%7C%2011-0078d4" alt="Windows 10 | 11">
    <a href="LICENSE"><img src="https://img.shields.io/badge/license-GPL--3.0-blue" alt="GPL-3.0"></a>
  </p>
  <p><a href="README.md">繁體中文</a> | English</p>
</div>

---

## Overview 🧾

* **Works on the game itself, no separate extraction tool**
  * 🎮 **8 engines**: detects the engine, then extracts, translates and writes back in one flow.
  * 🛡️ **Safe and reversible**: backs up before writing and restores in one click; each translation run can be undone on its own.
  * 📚 **Batch processing**: select several games in the library and right-click 批次翻譯 (Batch translate) to extract, translate and write back each one in turn.
  * 🤝 **Share translations**: export a share file that holds only the translations (no game text); others apply it to the same game with 匯入譯文 (Import translations).
  * 🈶 **Stops on Chinese games**: skips games that are already in Chinese or ship a Chinese language pack.

* **Translation quality**
  * 🔍 **Checks every line**: a translation with broken control codes, a wrong line count, an odd length or repetition is not written; that line keeps the original.
  * 📖 **Consistent terms**: collects names and terms, can look them up in external dictionaries, and runs a full-text QA pass that flags inconsistent names and missed lines.
  * 🔤 **No missing glyphs**: Unity, Godot and Wolf RPG games get a Chinese font automatically.

* **Local or cloud**
  * 💻 **Sakura**: a local model on your own PC; no network, no cost.
  * ☁️ **Codex** (experimental): translates with the Codex quota of a ChatGPT account.
  * 🔌 **OpenAI-compatible API**: DeepSeek, OpenAI, OpenRouter, Ollama, LM Studio and others; enter the address and key.

---

## Supported engines 🎮

| Engine | Coverage | How it writes |
|:---:|---|---|
| RPG Maker MV/MZ | `data/*.json` (encrypted images and audio are fine) | Rewrites the JSON |
| TyranoScript | Loose files or `app.asar` | Loose files in place; for asar, patches the header and puts scripts in `app.asar.unpacked/` |
| Ren'Py | Compiled `.rpyc` (loose or inside `.rpa`) | Leaves the scripts alone and adds a language in `game/tl/yakupatch/` |
| KiriKiri | Unencrypted `.xp3` | Leaves the archive alone and adds `patch.xp3` |
| Unity | Mono builds: UI, Utage, Naninovel, custom classes, `Assembly-CSharp.dll` strings | Rewrites the asset files and switches fonts to a built-in Windows Chinese font |
| Godot | Text scenes and Godot 3 scripts inside `.pck` | Rewrites the `.pck` and switches fonts to a Chinese font |
| Artemis | Scripts and tables inside `.pfs` | Leaves the archive alone and adds `.pfs.NNN` |
| Wolf RPG | Unencrypted loose files (maps, common events, databases) | Rewrites in place and ships a Chinese substitute font |

> [!NOTE]
> Encrypted games are not supported. YakuPatch never tries to decrypt them.

---

## Requirements 🧰

| Item | Requirement |
|:---:|---|
| OS | Windows 10/11, 64-bit |
| Python | **The portable zip and the install script bundle Python 3.12; you do not need to install Python.** Only running from source needs Python 3.11 or later |
| Visual C++ Redistributable (x64) | Most PCs already have it. If it is missing, the launcher shows the [download link](https://aka.ms/vs/17/release/vc_redist.x64.exe) |
| Translation backend (one of) | **Sakura**: a llama-server you start yourself (local or LAN)<br>**Codex** (experimental): a ChatGPT account with Codex quota<br>**OpenAI-compatible API**: an API address and key (a local server needs no key); pick a general chat model |
| Network | Needed for the install script, package repair, Codex and cloud APIs. Not needed to translate with a local Sakura server |

---

## Three steps 📢

* **Step 1: Install**
  * **Portable zip (recommended)**: download `YakuPatch_win.zip` from [Releases](https://github.com/Honguan/YakuPatch/releases/latest), unzip it, and double-click `launch_win.bat`.
  * **Install script**: open PowerShell in the folder to install into and run:

    ```powershell
    irm https://raw.githubusercontent.com/Honguan/YakuPatch/main/scripts/install.ps1 | iex
    ```

  * The first start can take a few dozen seconds. The console window shows progress dots and closes when the main window opens.

* **Step 2: Set up a backend**
  * Start your Sakura llama-server, enter its address in Settings and click 測試連線 (Test connection); or sign in to Codex in Settings; or enter an API address, key and model under Settings → OpenAI 相容 (OpenAI-compatible).

* **Step 3: Add a game and translate**
  * Add the game folder to the library on the left and follow the step bar at the top: Detect → Language check → Extract → Translate → Apply.
  * To restore the game, click 還原遊戲 (Restore game).

> The interface is in Traditional Chinese.

---

<details>
<summary><h2>Command line ⌨️</h2></summary>

```bat
yakupatch.bat extract   "D:\Games\MyGame"
yakupatch.bat translate "D:\Games\MyGame" --backend sakura
yakupatch.bat apply     "D:\Games\MyGame" --yes
yakupatch.bat share     "D:\Games\MyGame"
yakupatch.bat batch     "D:\Games\GameA" "D:\Games\GameB" --backend sakura --apply
yakupatch.bat import    "D:\Games\MyGame" MyGame.yakupatch.jsonl
yakupatch.bat restore   "D:\Games\MyGame"
```

Run `yakupatch.bat --help` for every command and option.

</details>

<details>
<summary><h2>Data and updates 🗂️</h2></summary>

| Item | Location |
|:---:|---|
| Projects, settings, dictionaries | `%USERPROFILE%\.yakupatch` (updating or deleting the program folder leaves them intact) |
| Game backups | `_yakupatch_backup\` inside the game folder |
| Error log | `%USERPROFILE%\.yakupatch\logs\gui.log` |

**Update**: unzip the new release over the program folder, or run the install script again. When a new version is out, the status bar shows a download link.

</details>

<details>
<summary><h2>Run from source 🛠️</h2></summary>

Needs Python 3.11 or later:

```bash
pip install -e .
yakupatch-gui
```

Run the tests with `python -X utf8 -m unittest discover -s tests -t tests`. Design and progress: [PROJECT_PLAN.md](PROJECT_PLAN.md) (Chinese).

</details>

> [!WARNING]
> Codex uses a non-public endpoint and may stop working at any time. Game text is sent to OpenAI when you translate with it. Cloud OpenAI-compatible APIs also receive the game text and bill by usage.

---

## License 📜

[GPL-3.0-or-later](LICENSE). The Sakura prompts and the Codex request format follow [BallonsTranslator](https://github.com/dmMaze/BallonsTranslator) (GPL-3.0).

For translating games you legally own, for personal use only. Share only the share files, never the game files.
