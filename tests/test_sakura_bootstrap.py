import hashlib
import io
import json
import os
import subprocess
import tempfile
import threading
import unittest
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


import helpers  # noqa: F401  (redirects temp folders and cleans them up)
from yakupatch import sakura_kit as kit


@unittest.skipUnless(os.name == "nt", "Windows PowerShell launcher")
class Bootstrap(unittest.TestCase):
    def test_first_download_resume_checksum_zip_guard_and_cached_restart(self):
        root = Path(tempfile.mkdtemp(prefix="yakupatch-bootstrap-"))
        payload = b"test model" * 100

        def zipped(name):
            data = io.BytesIO()
            with zipfile.ZipFile(data, "w") as z:
                z.writestr(name, b"fixture server")
            return data.getvalue()

        bodies = {"/model": payload, "/server.zip": zipped("llama-server.exe"), "/bad.zip": zipped("../outside.exe")}
        requests = []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                body = bodies[self.path]
                requested = self.headers.get("Range")
                requests.append((self.path, requested))
                start = int(requested.split("=")[1].split("-")[0]) if requested else 0
                self.send_response(206 if requested else 200)
                self.send_header("Content-Length", str(len(body) - start))
                if requested:
                    self.send_header("Content-Range", f"bytes {start}-{len(body) - 1}/{len(body)}")
                self.end_headers()
                self.wfile.write(body[start:])

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_port}"
        package = kit.export_kit(root / "kit.zip", kit.Model("repo", "m.gguf", len(payload), hashlib.sha256(payload).hexdigest()), "cpu")
        target = root / "kit [test]"
        with zipfile.ZipFile(package) as z:
            z.extractall(target)
        (target / "models").mkdir()
        (target / "models/m.gguf.part").write_bytes(payload[:17])
        release = {"tag_name": "b1", "assets": [{"name": "llama-b1-bin-win-cpu-x64.zip",
                   "browser_download_url": base + "/server.zip", "digest": "sha256:" + hashlib.sha256(bodies["/server.zip"]).hexdigest()}]}
        (target / "release.json").write_text(json.dumps(release), "utf-8")
        script = root / "check.ps1"
        script.write_text("""
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.UTF8Encoding]::new()
$root = $env:YAKUPATCH_TEST_KIT
. (Join-Path $root 'start_sakura.ps1')
$script:realDownload = ${function:Get-KitFile}
$script:release = Get-Content -LiteralPath (Join-Path $root 'release.json') -Raw | ConvertFrom-Json
function Invoke-RestMethod($Uri, $Headers) { return $script:release }
function Get-KitFile($Url, $Dest, $Sha256) {
    & $script:realDownload ($Url.Replace('https://huggingface.co/SakuraLLM/repo/resolve/main/m.gguf', "$($env:YAKUPATCH_TEST_URL)/model")) $Dest $Sha256
}
$first = Install-SakuraKit $root
if (-not (Test-Path -LiteralPath $first)) { throw 'Server missing' }
function Invoke-RestMethod { throw 'Cached restart must not request releases' }
$second = Install-SakuraKit $root
if ($first -ne $second) { throw 'Cached server changed' }
$rejected = $false
try { Get-KitFile "$($env:YAKUPATCH_TEST_URL)/model" (Join-Path $root 'wrong.gguf') ('0' * 64) } catch { $rejected = $true }
if (-not $rejected -or (Test-Path -LiteralPath (Join-Path $root 'wrong.gguf.part'))) { throw 'Checksum guard failed' }
function Invoke-RestMethod($Uri, $Headers) { return $script:release }
$script:release.assets[0].browser_download_url = "$($env:YAKUPATCH_TEST_URL)/bad.zip"
$script:release.assets[0].digest = $env:YAKUPATCH_TEST_BAD_HASH
$unsafe = Join-Path $root 'unsafe'
New-Item -ItemType Directory $unsafe | Out-Null
Copy-Item -LiteralPath (Join-Path $root 'kit.json') -Destination $unsafe
$rejected = $false
try { Install-SakuraKit $unsafe } catch { if ("$_" -match 'Unsafe archive path') { $rejected = $true } else { throw } }
if (-not $rejected -or (Test-Path -LiteralPath (Join-Path $unsafe 'outside.exe'))) { throw 'ZIP path guard failed' }
Write-Output 'Bootstrap checks passed'
""", "utf-8")
        try:
            result = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)],
                                    env={**{k: v for k, v in os.environ.items() if k.lower() != "psmodulepath"},
                                         "YAKUPATCH_TEST_KIT": str(target), "YAKUPATCH_TEST_URL": base,
                                         "YAKUPATCH_TEST_BAD_HASH": "sha256:" + hashlib.sha256(bodies["/bad.zip"]).hexdigest()},
                                    capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
                                    creationflags=kit._NO_WINDOW)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(5)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Bootstrap checks passed", result.stdout)
        self.assertEqual((target / "models/m.gguf").read_bytes(), payload)
        self.assertEqual(requests.count(("/model", "bytes=17-")), 1)
        self.assertEqual(requests.count(("/server.zip", None)), 1)
