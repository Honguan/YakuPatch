import io
import json
import re
import tempfile
import tomllib
import unittest
from importlib import metadata
from pathlib import Path
from unittest import mock

from yakupatch import core, depcheck

ROOT = Path(__file__).resolve().parent.parent


def _name(s: str) -> str:
    return re.sub(r"[-_.]+", "-", s).lower()


class Release(unittest.TestCase):
    def test_lock_pins_every_dependency(self):
        deps = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["dependencies"]
        lock = (ROOT / "requirements.lock").read_text(encoding="utf-8")
        pinned = {_name(m.group(1)) for m in re.finditer(r"^([A-Za-z0-9_.\-]+)==\S+ --hash=sha256:[0-9a-f]{64}$", lock, re.M)}
        self.assertTrue({_name(re.match(r"[A-Za-z0-9_.\-]+", d).group(0)) for d in deps} | {"pip"} <= pinned)

    def test_depcheck_reports_missing_and_wrong_versions(self):
        lock = Path(tempfile.mkdtemp(prefix="yakupatch-test-")) / "requirements.lock"
        lock.write_text(f"# header\nkeyring=={metadata.version('keyring')} --hash=sha256:00\n"
                        "keyring-not-installed==1.0 --hash=sha256:00\nopencc-python-reimplemented==0.0.1\n", encoding="utf-8")
        found = depcheck.problems(lock)
        self.assertEqual(len(found), 2)
        self.assertTrue(found[0].startswith("keyring-not-installed") and found[1].startswith("opencc"))

    def test_newer_release(self):
        def answer(tag):
            body = io.BytesIO(json.dumps({"tag_name": tag, "html_url": "https://x/r"}).encode())
            return mock.patch("urllib.request.OpenerDirector.open", return_value=mock.MagicMock(__enter__=lambda s: body))
        with answer("v1.2.0"):
            self.assertEqual(core.newer_release("1.1.9"), ("v1.2.0", "https://x/r"))
        with answer("v1.0.0"), mock.patch("urllib.request.getproxies") as registry:
            self.assertIsNone(core.newer_release("1.0.0", proxies={}))
            registry.assert_not_called()                  # proxies read by the caller are used as given
        with mock.patch("urllib.request.OpenerDirector.open", side_effect=OSError("offline")):
            self.assertIsNone(core.newer_release("1.0.0"))


if __name__ == "__main__":
    unittest.main()
