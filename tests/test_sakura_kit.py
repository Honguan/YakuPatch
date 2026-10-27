import hashlib
import io
import tempfile
import threading
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from yakupatch import sakura_kit as kit
from yakupatch.backends.sakura import Sakura, SakuraConfig

GIB = kit.GIB


class FakeResponse(io.BytesIO):
    def __init__(self, data: bytes, status=200):
        super().__init__(data)
        self.status, self.headers = status, {"Content-Length": str(len(data))}


def model(file, gib, note=""):
    return kit.Model("repo", file, int(gib * GIB), "", note)


class SakuraKit(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="yakupatch-kit-"))

    def test_recommend_picks_best_fitting_family_first(self):
        models = [model("galtransl-14b.gguf", 7.6), model("galtransl-7b-q6.gguf", 5.8), model("galtransl-7b-iq4.gguf", 4.0),
                  model("sakura-1.5b.gguf", 3.3)]
        labels = kit.recommend(models, 8 * GIB, 32 * GIB)
        self.assertEqual(labels["galtransl-7b-q6.gguf"], "推薦")
        self.assertEqual(labels["galtransl-14b.gguf"], "較慢（部分用 CPU）")
        self.assertEqual(labels["sakura-1.5b.gguf"], "可用")
        self.assertEqual(kit.recommend(models, 0, 0)["galtransl-14b.gguf"], "不建議（記憶體不足）")
        self.assertNotIn("推薦", kit.recommend(models, 2 * GIB, 0).values())

    def test_prompt_version_per_model(self):
        self.assertEqual(kit.version_of("Sakura-Galtransl-14B-v3.8-Q5_K_S.gguf"), "galtransl-v1")
        self.assertEqual(kit.version_of("sakura-14b-qwen3-v1.5-q6k.gguf"), "1.5")
        self.assertEqual(kit.version_of("sakura-7b-qwen2.5-v1.0-q6k.gguf"), "1.0")
        system = Sakura(SakuraConfig(version="1.5")).messages([], {}, context=False)[0]["content"]
        self.assertIn("二次元", system)

    def test_delete_only_an_installed_model(self):
        models = self.tmp / "models"
        models.mkdir()
        selected, other = models / "m.gguf", models / "other.gguf"
        selected.write_bytes(b"selected")
        other.write_bytes(b"other")
        outside = self.tmp / "outside.gguf"
        outside.write_bytes(b"outside")
        with patch.object(kit, "home", return_value=self.tmp):
            for name in ("../outside.gguf", str(outside), "missing.gguf"):
                with self.subTest(name=name), self.assertRaises(ValueError):
                    kit.delete_model(name)
            kit.delete_model(selected.name)
            self.assertEqual(kit.installed(), [other])
        self.assertEqual(other.read_bytes(), b"other")
        self.assertEqual(outside.read_bytes(), b"outside")

    def test_delete_model_running_and_file_errors(self):
        from types import SimpleNamespace
        models = self.tmp / "models"
        models.mkdir()
        selected = models / "m.gguf"
        selected.write_bytes(b"model")
        with patch.object(kit, "home", return_value=self.tmp):
            with patch.object(kit.SERVER, "process", SimpleNamespace(poll=lambda: None)), self.assertRaises(ValueError):
                kit.delete_model(selected.name)
            self.assertTrue(selected.exists())
            with patch.object(Path, "unlink", side_effect=PermissionError("in use")), self.assertRaises(OSError):
                kit.delete_model(selected.name)
        self.assertEqual(selected.read_bytes(), b"model")

    def test_download_resumes_and_verifies(self):
        data = b"x" * 3000
        dest = self.tmp / "m.gguf"
        dest.with_name("m.gguf.part").write_bytes(data[:1000])
        seen = []

        def opener(request, timeout):
            seen.append(request.headers.get("Range"))
            return FakeResponse(data[1000:], 206)
        with patch.object(kit, "urlopen", opener):
            kit.download("http://x/m", dest, hashlib.sha256(data).hexdigest())
        self.assertEqual((dest.read_bytes(), seen), (data, ["bytes=1000-"]))
        with patch.object(kit, "urlopen", lambda r, timeout: FakeResponse(b"bad")), self.assertRaises(OSError):
            kit.download("http://x/n", self.tmp / "n.gguf", hashlib.sha256(data).hexdigest())
        self.assertFalse((self.tmp / "n.gguf").exists() or (self.tmp / "n.gguf.part").exists())

    def test_download_can_pause(self):
        stop = threading.Event()
        stop.set()
        with patch.object(kit, "urlopen", lambda r, timeout: FakeResponse(b"x" * 10)), self.assertRaises(InterruptedError):
            kit.download("http://x/m", self.tmp / "m.gguf", stop=stop)

    def test_llama_release_skips_builds_without_windows_binaries(self):
        releases = [{"tag_name": "v0.7.0", "assets": []},
                    {"tag_name": "b2", "assets": [{"name": n, "size": 1} for n in (
                        "llama-b2-bin-win-cuda-12.4-x64.zip", "cudart-llama-bin-win-cuda-12.4-x64.zip",
                        "llama-b2-bin-win-vulkan-x64.zip")]}]
        with patch.object(kit, "_get_json", return_value=releases):
            self.assertEqual([a["name"] for a in kit.llama_release("cuda")[1]],
                             ["llama-b2-bin-win-cuda-12.4-x64.zip", "cudart-llama-bin-win-cuda-12.4-x64.zip"])
            self.assertEqual(kit.llama_release("vulkan")[0], "b2")
            with self.assertRaises(OSError):
                kit.llama_release("cpu")

    def test_install_llama_rejects_unsafe_zip_paths(self):
        bad = io.BytesIO()
        with zipfile.ZipFile(bad, "w") as z:
            z.writestr("../evil.exe", "x")
        asset = {"name": "llama-b2-bin-win-cpu-x64.zip", "size": 1, "browser_download_url": "http://x"}

        def fake_download(url, dest, sha256="", progress=None, stop=None):
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(bad.getvalue())
            return dest
        with patch.object(kit, "llama_release", return_value=("b2", [asset])), \
                patch.object(kit, "download", fake_download), patch.object(kit, "home", return_value=self.tmp), \
                self.assertRaises(OSError):
            kit.install_llama("cpu", self.tmp / "llama")
        self.assertFalse((self.tmp / "evil.exe").exists())

    def test_catalogue_is_fetched_once_until_refreshed(self):
        tree = [{"path": "m.gguf", "size": 5, "lfs": {"oid": "ab"}}, {"path": "README.md", "size": 1}]
        kit.catalogue.cache_clear()
        self.addCleanup(kit.catalogue.cache_clear)
        with patch.object(kit, "_get_json", return_value=tree) as get:
            self.assertEqual(kit.catalogue(), kit.catalogue())
            self.assertEqual(get.call_count, len(kit.CATALOGUE))
            kit.catalogue.cache_clear()
            kit.catalogue()
            self.assertEqual(get.call_count, 2 * len(kit.CATALOGUE))

    def test_server_listens_on_localhost_unless_lan(self):
        args = kit.server_args(Path("llama-server.exe"), Path("m.gguf"), kit.LocalConfig(slots=2, ctx_per_slot=4096))
        self.assertEqual(args[args.index("--host") + 1], "127.0.0.1")
        self.assertEqual(args[args.index("-c") + 1], "8192")
        lan = kit.server_args(Path("s.exe"), Path("m.gguf"), kit.LocalConfig(lan=True))
        self.assertEqual(lan[lan.index("--host") + 1], "0.0.0.0")
        with self.assertRaises(ValueError):
            kit.LocalConfig(port=0)

    def test_server_defaults_and_saved_overrides(self):
        from yakupatch import core
        with patch.object(core, "config_path", return_value=self.tmp / "config.json"):
            cfg = core.local_config()
            args = kit.server_args(Path("server.exe"), Path("m.gguf"), cfg)
            for flag, value in {"-np": "8", "-c": "16384", "-ctk": "q8_0", "-ctv": "q8_0",
                                "-b": "2048", "-ub": "512", "-fa": "on", "-ngl": "999"}.items():
                self.assertEqual(args[args.index(flag) + 1], value, flag)
            core.save_config("sakura_local", {"slots": 2, "ctx_per_slot": 4096, "gpu_layers": 0, "port": 8081})
            cfg = core.local_config()
            args = kit.server_args(Path("server.exe"), Path("m.gguf"), cfg)
            for flag, value in {"-np": "2", "-c": "8192", "-ngl": "0", "--port": "8081"}.items():
                self.assertEqual(args[args.index(flag) + 1], value, flag)

    def test_export_kit_uses_server_defaults(self):
        dest = self.tmp / "portable kit"
        llama = dest / "llama"
        llama.mkdir(parents=True)
        (llama / "llama-server.exe").touch()
        local = self.tmp / "models" / "model with spaces.gguf"
        local.parent.mkdir()
        local.write_bytes(b"model")
        with patch.object(kit, "home", return_value=self.tmp), \
                patch.object(kit, "install_llama", return_value=llama):
            kit.export_kit(dest, kit.Model("repo", local.name, 5, ""), "cuda", port=8081)
        bat = (dest / "start_sakura.bat").read_bytes()
        self.assertEqual(bat.count(b"\n"), bat.count(b"\r\n"))
        command = bat.decode("utf-8")
        for option in ("-np 8", "-c 16384", "-ctk q8_0", "-ctv q8_0", "-b 2048", "-ub 512", "-fa on",
                       "--host 0.0.0.0", "--port 8081", '"models\\model with spaces.gguf"'):
            self.assertIn(option, command)
        self.assertEqual((dest / "models" / local.name).read_bytes(), b"model")


if __name__ == "__main__":
    unittest.main()
