import io
import json
import os
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from yakupatch import core, engines, pipeline
from yakupatch.backends import AuthRequired, ConfigError, Refused, TooLong, Transient, UsageLimit
from yakupatch.backends import openai_compat
from yakupatch.backends.openai_compat import OpenAICompat, OpenAIConfig, base_url, clean, is_local, raise_for
from yakupatch.exchange import Store
from yakupatch.extract import extract, project_dir
from helpers import copy_game, fake_translate
from test_codex import FakeKeyring


class FakeServer:
    """Stands in for urlopen: answers /models and /chat/completions like an OpenAI-compatible server."""

    def __init__(self, models=("m1", "m2"), json_mode=True, reply=None):
        self.models, self.json_mode, self.reply, self.requests = models, json_mode, reply, []

    def __call__(self, request, timeout=None):
        body = json.loads(request.data) if request.data else None
        self.requests.append((request.full_url, dict(request.header_items()), body))
        if request.full_url.endswith("/models"):
            if self.models is None:
                raise HTTPError(request.full_url, 404, "Not Found", {}, io.BytesIO(b"not found"))
            return io.BytesIO(json.dumps({"data": [{"id": m} for m in self.models]}).encode())
        if "response_format" in body and not self.json_mode:
            raise HTTPError(request.full_url, 400, "Bad Request", {}, io.BytesIO(b'{"error":"response_format unsupported"}'))
        if self.reply:
            return io.BytesIO(json.dumps(self.reply).encode())
        items = json.loads(body["messages"][-1]["content"]).get("items", [{"id": "0", "text": "要約"}])  # F14 summary call
        content = json.dumps({"translations": [{"id": i["id"], "translation": fake_translate(i["text"])} for i in items]})
        return io.BytesIO(json.dumps({"choices": [{"finish_reason": "stop", "message": {
            "content": "<think>…</think>```json\n" + content + "\n```"}}]}).encode())


def backend(server, base="https://api.example.com", key="sk-test", **kw):
    return OpenAICompat(OpenAIConfig(api_base=base, **kw), key=key, opener=server)


class OpenAICompatTests(unittest.TestCase):
    def test_base_url_and_local(self):
        self.assertEqual(base_url("https://api.deepseek.com/"), "https://api.deepseek.com/v1")
        self.assertEqual(base_url("https://openrouter.ai/api/v1"), "https://openrouter.ai/api/v1")
        self.assertTrue(is_local("http://127.0.0.1:11434") and is_local("http://192.168.1.5:8080/v1"))
        self.assertFalse(is_local("https://api.openai.com/v1"))

    def test_config_validation(self):
        for bad in ({"api_base": ""}, {"api_base": "ftp://x"}, {"api_base": "https://u:p@x"},
                    {"api_base": "https://x", "temperature": 3}, {"api_base": "https://x", "retries": 9}):
            with self.assertRaises(ValueError):
                OpenAIConfig(**bad)

    def test_key_never_sent_over_plain_http_to_remote(self):
        with self.assertRaises(ValueError):
            backend(FakeServer(), base="http://api.example.com")
        backend(FakeServer(), base="http://127.0.0.1:8080")             # local server: fine

    def test_clean_strips_reasoning_and_fences(self):
        self.assertEqual(clean('<think>{"x":1}</think>\n```json\n{"a":1}\n```'), '{"a":1}')

    def test_prepare_picks_first_model_and_survives_missing_models(self):
        b = backend(FakeServer())
        b.prepare()
        self.assertEqual(b.model, "m1")
        b = backend(FakeServer(models=None), model="deepseek-chat")
        b.prepare()
        self.assertEqual(b.model, "deepseek-chat")
        with self.assertRaises(ConfigError):
            backend(FakeServer(models=None)).prepare()

    def test_request_carries_key_and_json_mode_falls_back(self):
        server = FakeServer(json_mode=False)
        b = backend(server, model="m")
        out = b.complete([{"role": "user", "content": '{"items":[]}'}])
        self.assertEqual(json.loads(out), {"translations": []})
        self.assertEqual(server.requests[0][1]["Authorization"], "Bearer sk-test")
        self.assertIn("response_format", server.requests[0][2])
        self.assertNotIn("response_format", server.requests[1][2])      # retried once without it, and remembered
        b.complete([{"role": "user", "content": '{"items":[]}'}])
        self.assertEqual(len(server.requests), 3)

    def test_finish_reasons(self):
        for reason, error in (("length", TooLong), ("content_filter", Refused)):
            reply = {"choices": [{"finish_reason": reason, "message": {"content": "x"}}]}
            with self.assertRaises(error):
                backend(FakeServer(reply=reply), model="m").complete([])
        with self.assertRaises(Transient):
            backend(FakeServer(reply={"choices": []}), model="m").complete([])

    def test_http_errors_map_to_categories(self):
        cases = [(401, "", AuthRequired), (402, "", UsageLimit), (429, "insufficient_quota", UsageLimit),
                 (400, "maximum context length", TooLong), (429, "", Transient), (503, "", Transient),
                 (404, "model not found", ConfigError)]
        for status, detail, error in cases:
            with self.subTest(status=status, detail=detail), self.assertRaises(error):
                raise_for(status, detail)

    def test_identity_and_metering(self):
        b = backend(FakeServer(), model="m")
        self.assertNotIn("sk-test", json.dumps(b.cache_identity))
        self.assertTrue(b.caps.metered)
        self.assertFalse(backend(FakeServer(), base="http://localhost:1234", model="m").caps.metered)

    def test_key_storage_per_base(self):
        ring = FakeKeyring()
        openai_compat.set_key("https://api.example.com", "sk-1", ring)
        with patch.dict(os.environ, {openai_compat.ENV_KEY: ""}):
            self.assertEqual(openai_compat.get_key("https://api.example.com/v1/", ring), "sk-1")
            self.assertEqual(openai_compat.get_key("https://other.example.com", ring), "")
            openai_compat.clear_key("https://api.example.com", ring)
            self.assertEqual(openai_compat.get_key("https://api.example.com", ring), "")
        with patch.dict(os.environ, {openai_compat.ENV_KEY: "sk-env"}):
            self.assertEqual(openai_compat.get_key("https://api.example.com", ring), "sk-env")

    def test_config_section_round_trip(self):
        with patch.object(core, "config_path", return_value=copy_game("mz_min")[0].parent / "config.json"):
            core.save_config("openai", {"api_base": "https://api.example.com", "model": "m"})
            self.assertEqual(core.openai_config().model, "m")
            with self.assertRaises(ValueError):
                core.save_config("openai", {"api_base": "https://x", "api_key": "sk"})   # keys never go to config.json

    def test_pipeline_translates_through_backend(self):
        game, home = copy_game("mz_min")
        plugin = engines.PLUGINS[0]
        store = Store(project_dir(game, plugin, home))
        extract(game, plugin, store)
        result = pipeline.run(store, plugin.control_codes, backend(FakeServer(), model="m"), sleep=lambda _: None)
        self.assertEqual(result["failed"], 0)
        self.assertGreater(result["translated"], 0)
        record = next(r for r in store.records.values() if r["status"] == "translated")
        self.assertEqual(record["by"], "openai:m-o1")


    def test_scene_summary_carries_into_later_waves(self):
        game, home = copy_game("mz_min")
        plugin = engines.PLUGINS[0]
        store = Store(project_dir(game, plugin, home))
        extract(game, plugin, store)
        server = FakeServer()
        result = pipeline.run(store, plugin.control_codes, backend(server, model="m", max_items=2, parallel=1),
                              sleep=lambda _: None)
        self.assertEqual(result["failed"], 0)
        bodies = [json.loads(b["messages"][-1]["content"]) for _, _, b in server.requests if b]
        asks = [b for b in bodies if "lines" in b]
        self.assertTrue(asks)                                        # summaries were requested after waves
        self.assertTrue(any(b.get("summary") for b in bodies if "items" in b))
        self.assertTrue(all("summary" not in b for b in bodies[:1]))  # nothing to summarize before the first wave
        self.assertIn(fake_translate("要約"), json.loads((store.dir / "summary.json").read_text("utf-8")).values())

    def test_failed_summary_never_blocks_translation(self):
        game, home = copy_game("mz_min")
        plugin = engines.PLUGINS[0]
        store = Store(project_dir(game, plugin, home))
        extract(game, plugin, store)
        server = FakeServer()
        original = server.__call__

        def broken(request, timeout=None):
            if request.data and "lines" in json.loads(json.loads(request.data)["messages"][-1]["content"]):
                return io.BytesIO(json.dumps({"choices": [{"finish_reason": "stop", "message": {"content": "no"}}]}).encode())
            return original(request, timeout)
        result = pipeline.run(store, plugin.control_codes, backend(broken, model="m", max_items=2, parallel=1),
                              sleep=lambda _: None)
        self.assertEqual(result["failed"], 0)
        self.assertGreater(result["translated"], 0)


if __name__ == "__main__":
    unittest.main()
