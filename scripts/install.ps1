# YakuPatch installer (PROJECT_PLAN 12.1 B). Run in the folder to install into:
#   irm https://raw.githubusercontent.com/Honguan/YakuPatch/main/scripts/install.ps1 | iex
# Creates .\YakuPatch with the Python embeddable runtime, the program, and the packages pinned in
# requirements.lock, then starts it. Nothing is written outside that folder (user data stays in %USERPROFILE%\.yakupatch).
# The release build runs this same script. Options are environment variables, because `iex` cannot pass parameters:
#   YAKUPATCH_DIR       install folder (default .\YakuPatch)
#   YAKUPATCH_SOURCE    local source checkout instead of downloading the main branch
#   YAKUPATCH_NOLAUNCH  set to 1 to skip starting the program
# Messages are ASCII: Windows PowerShell 5.1 misreads non-ASCII text in a script file saved without a BOM.

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12

$PyVersion = "3.12.10"
$PySha256 = "4ACBED6DD1C744B0376E3B1CF57CE906F9DC9E95E68824584C8099A63025A3C3"
$Repo = "Honguan/YakuPatch"
$Dir = if ($env:YAKUPATCH_DIR) { $env:YAKUPATCH_DIR } else { Join-Path (Get-Location) "YakuPatch" }
$Dir = [IO.Path]::GetFullPath($Dir)

function Get-Checked($Url, $OutFile, $Sha256) {
    Invoke-WebRequest $Url -OutFile $OutFile -UseBasicParsing
    if ((Get-FileHash $OutFile -Algorithm SHA256).Hash -ne $Sha256.ToUpper()) {
        throw "Checksum mismatch for $Url"
    }
}

if (-not [Environment]::Is64BitOperatingSystem) { throw "YakuPatch needs 64-bit Windows 10 or 11." }
Write-Host "Installing YakuPatch into $Dir"
New-Item -ItemType Directory -Force $Dir | Out-Null
$tmp = Join-Path ([IO.Path]::GetTempPath()) ("yakupatch-" + [guid]::NewGuid())
New-Item -ItemType Directory $tmp | Out-Null
try {
    $runtime = Join-Path $Dir "runtime"
    $python = Join-Path $runtime "python.exe"
    if (-not (Test-Path $python)) {
        Write-Host "Downloading Python $PyVersion (embeddable)"
        Get-Checked "https://www.python.org/ftp/python/$PyVersion/python-$PyVersion-embed-amd64.zip" "$tmp\python.zip" $PySha256
        Expand-Archive "$tmp\python.zip" $runtime -Force
        # Turn on site-packages and put the program folder (..) on the path; the embeddable runtime ignores PYTHONPATH.
        $pth = Get-ChildItem $runtime -Filter "python*._pth" | Select-Object -First 1
        $stdlib = (Get-Content $pth.FullName) | Where-Object { $_ -like "python*.zip" }
        Set-Content $pth.FullName @($stdlib, ".", "Lib\site-packages", "..", "import site") -Encoding Ascii
    }

    if ($env:YAKUPATCH_SOURCE) {
        $src = (Resolve-Path $env:YAKUPATCH_SOURCE).Path
    } else {
        Write-Host "Downloading YakuPatch source"
        Invoke-WebRequest "https://github.com/$Repo/archive/refs/heads/main.zip" -OutFile "$tmp\src.zip" -UseBasicParsing
        Expand-Archive "$tmp\src.zip" "$tmp\src"
        $src = (Get-ChildItem "$tmp\src" -Directory | Select-Object -First 1).FullName
    }
    if (Test-Path "$Dir\yakupatch") { Remove-Item "$Dir\yakupatch" -Recurse -Force }
    Copy-Item "$src\yakupatch" "$Dir\yakupatch" -Recurse
    Get-ChildItem "$Dir\yakupatch" -Directory -Recurse -Filter "__pycache__" | Remove-Item -Recurse -Force
    Copy-Item "$src\requirements.lock", "$src\README.md", "$src\scripts\launch_win.bat", "$src\scripts\yakupatch.bat" $Dir
    if (Test-Path "$src\LICENSE") { Copy-Item "$src\LICENSE" $Dir }

    $lock = Join-Path $Dir "requirements.lock"
    if (-not (Test-Path "$runtime\Lib\site-packages\pip")) {
        # pip from its own wheel (version and hash from the lock), so no unpinned get-pip.py is needed;
        # pip refuses to install itself from a wheel path, so the wheel is unpacked in place.
        $line = (Get-Content $lock) | Where-Object { $_ -match "^pip==" } | Select-Object -First 1
        if ($line -notmatch "^pip==(\S+) --hash=sha256:([0-9a-f]+)") { throw "requirements.lock has no pinned pip" }
        $pipVersion, $pipSha = $Matches[1], $Matches[2]
        $info = Invoke-RestMethod "https://pypi.org/pypi/pip/$pipVersion/json" -UseBasicParsing
        $wheel = $info.urls | Where-Object { $_.packagetype -eq "bdist_wheel" } | Select-Object -First 1
        $whl = Join-Path $tmp "pip.zip"
        Get-Checked $wheel.url $whl $pipSha
        Expand-Archive $whl "$runtime\Lib\site-packages" -Force         # a wheel is a zip of the installed files
    }
    Write-Host "Installing packages from requirements.lock"
    & $python -m pip install --no-warn-script-location --require-hashes -r $lock
    if ($LASTEXITCODE -ne 0) { throw "Installing packages failed; check the network connection and run the installer again." }
} finally {
    Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue
}

Write-Host "Done. Start YakuPatch with $Dir\launch_win.bat"
if ($env:YAKUPATCH_NOLAUNCH -ne "1") {
    Start-Process (Join-Path $Dir "launch_win.bat") -WorkingDirectory $Dir
}
