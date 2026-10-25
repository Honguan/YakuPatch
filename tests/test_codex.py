import base64
import io
import json
import threading
import time
import unittest
import zipfile
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit
from urllib.request import urlopen

from yakupatch import cli, core, engines, pipeline
from yakupatch.backends import AuthRequired, BackendCaps, ConfigError, Refused, TooLong, Transient, UsageLimit
from yakupatch.backends import codex
from yakupatch.backends.codex import Codex, CodexConfig, Credentials
from yakupatch.exchange import Store
from yakupatch.extract import extract, project_dir
from helpers import copy_game, fake_translate


class FakeKeyring:
    def __init__(self):
        self.data = {}

    def get_password(self, service, key):
        return self.data.get((service, key))

    def set_password(self, service, key, value):
        self.data[(service, key)] = value

    def delete_password(self, service, key):
        self.data.pop((service, key))


def jwt(claims):
    part = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    return f"h.{part}.s"


def tokens(exp=None, n=1):
    return {"access_token": jwt({"exp": exp or time.time() + 3600, "pad": "x" * 1500, "n": n}),
            "refresh_token": f"refresh-{n}",
            "id_token": jwt({"email": "a@example.com", "https://api.openai.com/auth": {"chatgpt_account_id": "acct"}})}


class Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


def sse(*events):
    return Response(b"".join(b"data: " + json.dumps(e).encode() + b"\n\n" for e in events))


def completed(text):
    return {"type": "response.completed", "response": {"status": "completed", "output": [
        {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": text}]}]}}


class IdsFake:
    """json_ids backend: answers every id unless `action` overrides."""
    caps = BackendCaps(io_format="json_ids", max_input_tokens=100_000, max_parallel=1, metered=True)
    config = SimpleNamespace(retries=1)
    by = "ids:fake/p1"
    cache_identity = by

    def __init__(self, action=None):
        self.calls, self.action = [], action

    def prepare(self):
        pass

    def messages(self, works, terms, context=True):
        return [{"content": json.dumps([w.text for w in works], ensure_ascii=False)}]

    def complete(self, messages, temperature=None):
        texts = json.loads(messages[0]["content"])
        self.calls.append(texts)
        if self.action:
            result = self.action(texts, len(self.calls))
            if result is not None:
                return result
        return json.dumps({"translations": [{"id": str(i), "translation": fake_translate(t)} for i, t in enumerate(texts)]})


class CodexTests(unittest.TestCase):
    def setUp(self):
        self.game, home = copy_game("mz_min")
        self.plugin = engines.PLUGINS[0]
        self.store = Store(project_dir(self.game, self.plugin, home))
        extract(self.game, self.plugin, self.store)
        self.codes = self.plugin.control_codes
        self.keyring = FakeKeyring()
        self.creds = Credentials(self.keyring)

    def small(self, *sources):
        records = {str(i): {"id": str(i), "src": s, "dst": "", "status": "pending", "kind": "dialogue", "speaker": None,
                            "refs": [{"file": "data/Map001.json", "locs": [f"/{i}"]}], "flags": []}
                   for i, s in enumerate(sources)}
        self.store.replace(records, self.store.manifest)

    def run_ids(self, backend, **kw):
        return pipeline.run(self.store, self.codes, backend, sleep=lambda _: None, **kw)

    def test_json_ids_maps_by_id_and_isolates_missing(self):
        self.small("こんにちは", "こんばんは", "おはよう")
        drop = lambda texts, n: json.dumps({"translations": [{"id": "1", "translation": fake_translate(texts[1])},
                                                             {"id": "0", "translation": fake_translate(texts[0])}]}) if n == 1 else None
        result = self.run_ids(IdsFake(drop))
        self.assertEqual((result["translated"], result["failed"]), (3, 0))
        self.assertEqual(self.store.records["2"]["dst"], fake_translate("おはよう"))
        bad = IdsFake(lambda texts, n: '{"translations":[{"id":"0","translation":"a"},{"id":"0","translation":"b"}]}')
        self.small("さようなら", "ありがとう")                               # new sources: avoid the cache
        self.assertEqual(self.run_ids(bad)["errors"], {"bad_json": 2})       # duplicate id -> whole reply rejected

    def test_usage_limit_pauses_and_resume_continues(self):
        self.small(*[f"テスト{i}です" for i in range(4)])
        backend = IdsFake(lambda texts, n: (_ for _ in ()).throw(UsageLimit("quota")) if n == 2 else None)
        backend.caps = BackendCaps(io_format="json_ids", max_input_tokens=100_000, max_items=2, max_parallel=1)
        with self.assertRaises(UsageLimit):
            self.run_ids(backend)
        status = [r["status"] for r in self.store.records.values()]
        self.assertEqual(status.count("translated"), 2)
        self.assertEqual(status.count("pending"), 2)                        # not failed: resumable
        self.assertEqual(self.run_ids(IdsFake())["translated"], 2)

    def test_stop_between_waves(self):
        self.small(*[f"テスト{i}です" for i in range(4)])
        backend = IdsFake()
        backend.caps = BackendCaps(io_format="json_ids", max_input_tokens=100_000, max_items=1, max_parallel=1)
        stop = threading.Event()
        result = pipeline.run(self.store, self.codes, backend, sleep=lambda _: None, stop=stop,
                              progress=lambda s: stop.set())
        self.assertTrue(result["stopped"])
        self.assertEqual(result["translated"], 1)
        self.assertEqual(sum(r["status"] == "pending" for r in self.store.records.values()), 3)

    def test_credentials_chunked_and_rotated(self):
        data = codex.token_set(tokens())
        self.creds.save(data)
        self.assertGreater(len(self.keyring.data), 2)                       # split across entries
        self.assertEqual(self.creds.load(), data)
        self.assertEqual((data["account_id"], data["email"]), ("acct", "a@example.com"))
        self.creds.save(codex.token_set(tokens(n=2), data))
        self.assertEqual(self.creds.load()["refresh_token"], "refresh-2")
        self.assertEqual(len(self.keyring.data), 1 + int(self.keyring.data[(codex.KEYRING_SERVICE, "current")].split(":")[1]))
        self.creds.clear()
        self.assertEqual(self.keyring.data, {})
        self.assertIsNone(self.creds.load())

    def test_refresh_and_401_retry(self):
        self.creds.save(codex.token_set(tokens(exp=time.time() - 10)))
        posts, sent = [], []

        def post(url, form, timeout=30):
            posts.append(form["grant_type"])
            return tokens(n=len(posts) + 1)

        def opener(request, timeout):
            sent.append(request.get_header("Authorization"))
            if len(sent) == 1:
                raise HTTPError(request.full_url, 401, "x", {}, io.BytesIO(b"{}"))
            return Response(json.dumps({"models": [{"slug": "m1", "supported_reasoning_levels": [{"effort": "low"}]}]}).encode())
        backend = Codex(CodexConfig(), self.creds, opener)
        with patch.object(codex, "_post_form", post):
            self.assertEqual(backend.list_models(), {"m1": ["low"]})
        self.assertEqual(posts, ["refresh_token", "refresh_token"])         # expired, then 401 -> refresh again
        self.assertNotEqual(sent[0], sent[1])
        self.assertEqual(self.creds.load()["refresh_token"], "refresh-3")   # rotated token stored

    def test_stream_parsing_and_error_mapping(self):
        self.assertEqual(codex.final_text(codex.read_events(sse({"type": "response.created"}, completed('{"a":1}')))), '{"a":1}')
        refusal = {"type": "response.completed", "response": {"status": "completed", "output": [
            {"type": "message", "role": "assistant", "content": [{"type": "refusal", "refusal": "no"}]}]}}
        with self.assertRaises(Refused):
            codex.final_text(codex.read_events(sse(refusal)))
        with self.assertRaises(TooLong):
            codex.read_events(sse({"type": "response.incomplete", "response": {"status": "incomplete"}}))
        with self.assertRaises(UsageLimit):
            codex.read_events(sse({"type": "error", "error": {"code": "usage_limit_reached"}}))
        with self.assertRaises(Transient):
            codex.read_events(Response(b"data: {\"type\":\"response.created\"}\n\n"))
        for status, detail, kind in [(401, "", AuthRequired), (429, "", Transient), (429, "usage_limit_reached", UsageLimit),
                                     (400, "context_length_exceeded", TooLong), (503, "", Transient),
                                     (400, "model_not_found", ConfigError), (403, "", ConfigError)]:
            with self.assertRaises(kind, msg=(status, detail)):
                codex.raise_for(status, detail)

    def test_codex_request_payload_and_prepare(self):
        self.creds.save(codex.token_set(tokens()))
        bodies = []

        def opener(request, timeout):
            if request.full_url.startswith(codex.API_URL + "/models"):
                return Response(json.dumps({"models": [{"slug": "m1", "supported_reasoning_levels": [{"effort": "low"}]},
                                                       {"slug": "hidden", "visibility": "hide"}]}).encode())
            bodies.append(json.loads(request.data))
            items = json.loads(bodies[-1]["input"][0]["content"][0]["text"]).get("items", [{"id": "0", "text": "要約"}])
            return sse(completed(json.dumps({"translations": [{"id": i["id"], "translation": fake_translate(i["text"])}
                                                              for i in items]})))
        backend = Codex(CodexConfig(), self.creds, opener)
        backend.prepare()
        self.assertEqual(backend.model, "m1")
        self.small("こんにちは", "こんばんは")
        self.assertEqual(self.run_ids(backend)["translated"], 2)
        body = bodies[0]
        self.assertEqual((body["store"], body["stream"], body["tools"], body["reasoning"]), (False, True, [], {"effort": "low"}))
        self.assertIn("繁體中文", body["instructions"])
        self.assertEqual(body["text"]["format"]["type"], "json_schema")
        self.assertEqual({b["prompt_cache_key"] for b in bodies}, {backend.cache_key})
        with self.assertRaises(ConfigError):
            Codex(CodexConfig(model="nope"), self.creds, opener).prepare()
        with self.assertRaises(ConfigError):
            Codex(CodexConfig(effort="xhigh"), self.creds, opener).prepare()
        with self.assertRaises(AuthRequired):
            Codex(CodexConfig(), Credentials(FakeKeyring()), opener).prepare()

    def test_login_pkce_callback(self):
        seen = {}

        def browser(url):
            query = parse_qs(urlsplit(url).query)
            seen.update(query)
            redirect = query["redirect_uri"][0]

            def hit():
                with self.assertRaises(HTTPError):                          # wrong state is rejected
                    urlopen(redirect.replace("localhost", "127.0.0.1") + "?code=bad&state=wrong", timeout=5)
                urlopen(redirect.replace("localhost", "127.0.0.1") + f"?code=good&state={query['state'][0]}", timeout=5).read()
            threading.Thread(target=hit, daemon=True).start()

        def post(url, form, timeout=30):
            seen["form"] = form
            return tokens()
        with patch.object(codex, "_post_form", post):
            data = codex.login(self.creds, open_url=browser, timeout=10)
        self.assertEqual(seen["code_challenge_method"], ["S256"])
        self.assertEqual((seen["form"]["code"], seen["form"]["grant_type"]), ("good", "authorization_code"))
        self.assertEqual(self.creds.load(), data)

    def test_job_queue_serialises_per_backend(self):
        queue, gate, order = core.JobQueue(), threading.Event(), []

        def first(job):
            order.append("first")
            gate.wait(5)

        a = queue.submit("codex", first)
        b = queue.submit("codex", lambda job: order.append("second"))
        c = queue.submit(None, lambda job: order.append("extract"))          # not limited by the queue
        c.wait(5)
        self.assertEqual(b.state, "queued")
        d = queue.submit("codex", lambda job: order.append("never"))
        d.cancel()
        gate.set()
        for job in (a, b, d):
            self.assertTrue(job.wait(5))
        self.assertEqual(order, ["first", "extract", "second"])
        self.assertEqual((a.state, b.state, d.state), ("done", "done", "cancelled"))
        p = queue.submit("sakura", lambda job: job.stop.wait(5))
        p.pause()
        p.wait(5)
        self.assertEqual(p.state, "paused")
        r = queue.resume(p)
        r.cancel()
        r.wait(5)
        self.assertEqual(r.state, "cancelled")
        fail = queue.submit("x", lambda job: 1 / 0)
        fail.wait(5)
        self.assertEqual((fail.state, type(fail.error)), ("failed", ZeroDivisionError))

    def test_history_undo_listeners_and_diagnostics(self):
        self.small("こんにちは", "こんばんは")
        changed = []
        self.store.listeners.append(changed.append)
        run = self.run_ids(IdsFake())["run"]
        self.assertTrue(changed and set(changed[-1]) <= {"0", "1"})
        rows = core.history(self.store)
        self.assertEqual([(r["run"], r["by"], r["translated"], r["undone"]) for r in rows], [(run, "ids:fake/p1", 2, False)])
        self.store.undo(run)
        self.assertTrue(core.history(self.store)[0]["undone"])
        self.assertEqual(self.store.records["0"]["status"], "pending")
        with redirect_stdout(io.StringIO()) as out:
            self.assertEqual(cli.main(["--home", str(self.store.dir.parent), "undo", str(self.game)]), 0)
        self.assertIn("已撤銷", out.getvalue())
        zpath = core.diagnostics(self.game, self.plugin, self.store, self.store.dir / "d.zip")
        with zipfile.ZipFile(zpath) as z:
            blob = z.read("diagnostics.json").decode("utf-8")
        info = json.loads(blob)
        self.assertEqual(info["engine"], "rpgmaker-mz")
        self.assertTrue(info["files"])
        for secret in ("こんにちは", fake_translate("こんにちは"), str(__import__("pathlib").Path.home())):
            self.assertNotIn(secret, blob)

    def test_cli_codex_confirm_and_status(self):
        self.creds.save(codex.token_set(tokens()))
        backend = IdsFake()
        args = ["--home", str(self.store.dir.parent), "translate", str(self.game), "--backend", "codex", "--limit", "1"]
        with patch.object(cli, "Codex", lambda config: backend), patch("builtins.input", return_value="n"), \
                redirect_stdout(io.StringIO()) as out:
            self.assertEqual(cli.main(args), 0)
        self.assertIn("預估輸入約", out.getvalue())
        self.assertIn("已取消", out.getvalue())
        self.assertEqual(backend.calls, [])                                 # nothing sent before confirmation
        with patch.object(cli, "Codex", lambda config: backend), redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main(args + ["--yes"]), 0)
        self.assertEqual(len(backend.calls), 1)
        with patch.object(codex, "Credentials", lambda: Credentials(FakeKeyring())), redirect_stdout(io.StringIO()) as out:
            self.assertEqual(cli.main(["codex", "status"]), 1)
        self.assertIn("yakupatch codex login", out.getvalue())


if __name__ == "__main__":
    unittest.main()
