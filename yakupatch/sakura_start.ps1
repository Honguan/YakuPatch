# Windows PowerShell 5.1; ASCII source so no BOM is required.
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
Add-Type -AssemblyName System.IO.Compression.FileSystem

function Get-KitFile($Url, $Dest, $Sha256) {
    if (Test-Path -LiteralPath $Dest) { return }
    New-Item -ItemType Directory -Force (Split-Path $Dest -Parent) | Out-Null
    $part = "$Dest.part"
    $offset = if (Test-Path -LiteralPath $part) { (Get-Item -LiteralPath $part).Length } else { 0 }
    Write-Host "Downloading $Url"
    $request = [Net.HttpWebRequest]::Create($Url)
    $request.UserAgent = 'YakuPatch'
    $request.Timeout = 60000
    $request.ReadWriteTimeout = 60000
    if ($offset -gt 0) { $request.AddRange([long]$offset) }
    $response = $null
    try {
        try { $response = $request.GetResponse() } catch [Net.WebException] {
            if (-not $_.Exception.Response -or [int]$_.Exception.Response.StatusCode -ne 416) { throw }
            $_.Exception.Response.Close()
        }
        if ($response) {
            $mode = if ([int]$response.StatusCode -eq 206 -and $offset -gt 0) { [IO.FileMode]::Append } else { [IO.FileMode]::Create }
            $output = [IO.File]::Open($part, $mode, [IO.FileAccess]::Write)
            try { $response.GetResponseStream().CopyTo($output) } finally { $output.Dispose() }
        }
    } finally {
        if ($response) { $response.Close() }
    }
    if ($Sha256 -and (Get-FileHash -LiteralPath $part -Algorithm SHA256).Hash -ne $Sha256) {
        Remove-Item -LiteralPath $part -Force
        throw "Checksum mismatch: $Dest. Run the launcher again to retry."
    }
    Move-Item -LiteralPath $part -Destination $Dest -Force
}

function Install-SakuraKit($Root) {
    $kit = Get-Content -LiteralPath (Join-Path $Root 'kit.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($kit.variant -notin @('cuda', 'vulkan', 'cpu') -or
        $kit.model.repo -notmatch '^[\w.-]+$' -or
        [IO.Path]::GetFileName($kit.model.file) -ne $kit.model.file -or
        -not $kit.model.file.EndsWith('.gguf')) { throw 'Invalid kit.json model or variant.' }
    $llama = Join-Path $Root 'llama'
    $marker = Join-Path $llama '.installed'
    $server = Get-ChildItem -LiteralPath $llama -Filter 'llama-server.exe' -Recurse -ErrorAction SilentlyContinue | Select-Object -First 1
    $ready = (Test-Path -LiteralPath $marker) -and (Get-Content -LiteralPath $marker -Raw).Trim() -eq $kit.variant
    if (-not $server -or -not $ready) {
        $releases = Invoke-RestMethod 'https://api.github.com/repos/ggml-org/llama.cpp/releases?per_page=10' -Headers @{'User-Agent'='YakuPatch'; Accept='application/vnd.github+json'}
        $suffix = if ($kit.variant -eq 'cuda') { 'cuda-12.4' } else { $kit.variant }
        $assets = @()
        foreach ($release in $releases) {
            $binary = @($release.assets | Where-Object { $_.name -eq "llama-$($release.tag_name)-bin-win-$suffix-x64.zip" })
            if ($binary.Count) {
                $assets = $binary
                if ($kit.variant -eq 'cuda') { $assets += @($release.assets | Where-Object { $_.name -eq 'cudart-llama-bin-win-cuda-12.4-x64.zip' }) }
                break
            }
        }
        if (-not $assets.Count) { throw 'No compatible Windows llama.cpp release was found.' }
        New-Item -ItemType Directory -Force $llama | Out-Null
        foreach ($asset in $assets) {
            if ([IO.Path]::GetFileName($asset.name) -ne $asset.name) { throw 'Unsafe download filename.' }
            $zip = Join-Path (Join-Path $Root 'downloads') $asset.name
            $sha = if ($asset.digest) { $asset.digest -replace '^sha256:', '' } else { '' }
            Get-KitFile $asset.browser_download_url $zip $sha
            $archive = [IO.Compression.ZipFile]::OpenRead($zip)
            try {
                foreach ($entry in $archive.Entries) {
                    $target = [IO.Path]::GetFullPath((Join-Path $llama $entry.FullName))
                    if (-not $target.StartsWith([IO.Path]::GetFullPath($llama) + '\', [StringComparison]::OrdinalIgnoreCase)) { throw 'Unsafe archive path.' }
                }
                foreach ($entry in $archive.Entries) {
                    $target = Join-Path $llama $entry.FullName
                    if ($entry.FullName.EndsWith('/') -or $entry.FullName.EndsWith('\')) {
                        [IO.Directory]::CreateDirectory($target) | Out-Null
                    } else {
                        [IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($target)) | Out-Null
                        [IO.Compression.ZipFileExtensions]::ExtractToFile($entry, $target, $true)
                    }
                }
            } finally { $archive.Dispose() }
        }
        $server = Get-ChildItem -LiteralPath $llama -Filter 'llama-server.exe' -Recurse | Select-Object -First 1
        if (-not $server) { throw 'llama-server.exe is missing after extraction.' }
        Set-Content -LiteralPath $marker -Value $kit.variant -Encoding ASCII
    }
    $model = Join-Path (Join-Path $Root 'models') $kit.model.file
    $repo = [Uri]::EscapeDataString($kit.model.repo)
    $file = [Uri]::EscapeDataString($kit.model.file)
    Get-KitFile "https://huggingface.co/SakuraLLM/$repo/resolve/main/$file" $model $kit.model.sha256
    return $server.FullName
}

if ($MyInvocation.InvocationName -ne '.') {
    try {
        $server = Install-SakuraKit $PSScriptRoot
        $kit = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'kit.json') -Raw -Encoding UTF8 | ConvertFrom-Json
        Set-Location -LiteralPath $PSScriptRoot
        Write-Host "Sakura server: http://$($env:COMPUTERNAME):$($kit.port)/v1"
        Write-Host 'Allow only private networks in Windows Firewall. Close this window to stop.'
        $serverArgs = @($kit.args)
        & $server @serverArgs
        exit $LASTEXITCODE
    } catch {
        Write-Host "Setup failed: $_" -ForegroundColor Red
        exit 1
    }
}
