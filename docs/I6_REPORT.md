# I6 交付報告

分支：`feat/i6`（從 `main` 552d5b4 開）。日期：2026-10-25。版本：1.0.0。

## 修改檔案

| 檔案 | 內容 |
|---|---|
| `requirements.lock` | 依 Python 3.12／win_amd64 解析的 24 個套件，固定版本與 SHA-256；`install.ps1`、`launch_win.bat` 補裝與 CI 都用這一份（`--require-hashes`） |
| `scripts/lock.py` | 由 `pyproject.toml` 重新產生 `requirements.lock` |
| `pyproject.toml` | 版本 1.0.0；`PySide6` 改為 `PySide6-Essentials`（只用到 QtCore／QtGui／QtWidgets，壓縮包少數百 MB）；補上 Wolf 字型用到的 `fonttools` |
| `scripts/install.ps1` | 安裝腳本（§12.1 B），也是建置壓縮包的唯一步驟 |
| `scripts/build_win.ps1` | 以本機原始碼跑 `install.ps1`，壓成 `dist/YakuPatch_win.zip` |
| `scripts/launch_win.bat`、`scripts/yakupatch.bat` | 啟動腳本（依賴檢查、一鍵補裝、啟動 GUI）與命令列包裝 |
| `yakupatch/depcheck.py` | 啟動前檢查：鎖定清單的每個套件版本相符、Qt 能載入 |
| `yakupatch/core.py`、`yakupatch/gui/` | `newer_release`：啟動時背景查 GitHub Releases，有新版只在狀態列顯示下載連結 |
| `.github/workflows/release.yml` | 推送 `v*` tag 時建置壓縮包、用內附 Python 跑依賴檢查與全部測試，附加到 GitHub Release；手動執行只上傳成品 |
| `LICENSE` | GPL-3.0 全文（gnu.org 官方文字），壓縮包一併附上 |
| `tests/test_release.py` | 新增 3 個測試：鎖定清單涵蓋所有依賴且帶雜湊、依賴檢查回報缺少與版本不符、新版比對（含離線） |

## 邏輯摘要

- **安裝流程（`install.ps1`）**：
  - 下載 Python 3.12.10 嵌入版，比對固定的 SHA-256（與 python.org 公布的 MD5 核對過）。
  - 改寫 `python312._pth`：開啟 `site`、加入 `Lib\site-packages` 與程式資料夾（`..`）。嵌入版不讀 `PYTHONPATH`，系統 Python 與環境變數都不影響它。
  - 複製程式碼：本機原始碼（`YAKUPATCH_SOURCE`）或下載 `main` 分支。
  - pip：取鎖定清單裡的版本與雜湊，從 PyPI 下載 wheel、驗證後直接解壓到 site-packages。不用未固定版本的 `get-pip.py`，而 pip 不允許從 wheel 路徑安裝自己。
  - 依 `requirements.lock` 以 `--require-hashes` 安裝套件，完成後啟動。
  - `irm | iex` 無法傳參數，選項改用環境變數（`YAKUPATCH_DIR`、`YAKUPATCH_SOURCE`、`YAKUPATCH_NOLAUNCH`）。訊息只用 ASCII：Windows PowerShell 5.1 讀沒有 BOM 的腳本檔時，非 ASCII 會亂碼。
- **壓縮包**：`build_win.ps1` 跑同一個 `install.ps1`，再壓縮，所以兩種方式的內容相同：`runtime/`、`yakupatch/`、`requirements.lock`、`launch_win.bat`、`yakupatch.bat`、`README.md`。大小 119 MB。
- **啟動（`launch_win.bat`）**：
  - 先跑 `depcheck`：結束碼 1 表示套件缺少或版本不符，詢問後以 pip 補裝；結束碼 2 表示 Qt 載入失敗，顯示 Visual C++ 可轉散發套件的下載網址。
  - 檢查通過後用 `pythonw.exe -m yakupatch.gui` 啟動，不留主控台視窗。
- **更新提示**：只在實際啟動 GUI 時查詢（測試建立視窗時不連網），逾時 5 秒；離線、沒有 Release 或已是最新時不顯示。

## 測試結果

- `python -X utf8 -m unittest discover -s tests -t tests`：102 個測試全部通過；改用壓縮包內附的 Python 3.12.10（套件為鎖定版本，例如 UnityPy 1.25.2）跑同一套測試也全部通過。
- **壓縮包**（解壓到 `C:\YakuPatchTest\_release\zip`，PATH 只留 `C:\Windows`，清掉 `PYTHONHOME`／`PYTHONPATH`，系統找不到 `python`）：
  - 雙擊 `launch_win.bat`：依賴檢查通過，GUI 視窗開啟（標題 YakuPatch）。
  - 刪掉 `opencc` 套件後再啟動：依賴檢查列出缺少的套件，回答 Y 後補裝成功，接著開啟 GUI。
- **安裝腳本**：以 `Get-Content -Raw install.ps1 | iex` 模擬 `irm | iex`，安裝到新資料夾後依賴檢查通過。`main` 還沒有這次的檔案，所以用 `YAKUPATCH_SOURCE` 指向本機原始碼。
- **完整流程**：以壓縮包的 `yakupatch.bat` 和全新專案資料夾處理 19_godot：
  - 提取 94 句，Sakura 翻譯成功 94、失敗 0（10 秒），寫回。
  - 遊戲啟動 10 秒正常。
  - 還原後兩個 pck 的 SHA-256 與原檔相同；之後重新寫回。

## 未完成與已知限制

- **乾淨電腦驗收未做**：驗收標準要求「未安裝 Python 的乾淨 Windows 10／11」。本機有系統 Python，只用清空 PATH 與環境變數模擬；Visual C++ 可轉散發套件本機已安裝，缺少時的錯誤訊息沒有實測。需要在乾淨電腦或 Windows 沙箱上實機檢查。
- **GitHub Actions 未執行**：工作流程要推送 tag 才會跑，尚未驗證。測試裡 Godot／Wolf 的字型替換需要 Windows 中文字型；GitHub 的 Windows 映像若沒有雅黑／正黑，這些測試會失敗並擋下發佈。
- **安裝腳本的網址**：`irm https://raw.githubusercontent.com/.../main/scripts/install.ps1` 要等合併到 `main` 才有效；下載的程式碼是 `main` 分支，不是指定版本。
- **選用套件**：§12.1 的「選用功能第一次用到時才提示安裝」沒有實作，目前沒有選用套件，全部依賴都在鎖定清單內。
- **鎖定清單只適用 Windows**：其他平台從原始碼執行時請用 `pip install -e .`。
