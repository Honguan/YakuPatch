"""Core API shared by the CLI and the GUI (PROJECT_PLAN §3.4, §11.12): the GUI never reads or writes project/game files itself."""
import json
import os
import platform
import re
import subprocess
import threading
import urllib.request
import zipfile
from collections import Counter
from datetime import datetime
from pathlib import Path

from . import (__version__, dicts, engines, exchange, extract as extraction, langdetect, pipeline, translate as text, vfs,
               writeback)
from .backends import AuthRequired, ConfigError, UsageLimit
from .backends.codex import CodexConfig
from .backends.openai_compat import OpenAIConfig
from .backends.sakura import SakuraConfig
from .sakura_kit import LocalConfig
from .backup import Backup
from .exchange import Store, new_run
from .extract import project_dir, projects_home
from .fsutil import atomic_write

RELEASES = "https://api.github.com/repos/Honguan/YakuPatch/releases/latest"


def newer_release(current: str = __version__, timeout: float = 5, proxies: dict | None = None) -> tuple[str, str] | None:
    """GUI startup check (§12.1): (tag, page) of a GitHub release newer than `current`; None when up to date,
    offline or there is no release. Only tells; never downloads.
    `proxies` from urllib.request.getproxies() on the GUI thread: reading the Windows registry for them on a
    worker thread has crashed the process (segfault in getproxies_registry)."""
    try:
        req = urllib.request.Request(RELEASES, headers={"Accept": "application/vnd.github+json"})
        handler = urllib.request.ProxyHandler(urllib.request.getproxies() if proxies is None else proxies)
        with urllib.request.build_opener(handler).open(req, timeout=timeout) as r:
            data = json.load(r)
    except (OSError, ValueError):
        return None
    tag = data.get("tag_name") or ""
    return (tag, data.get("html_url") or "") if _version(tag) > _version(current) else None


def _version(s: str) -> tuple[int, ...]:
    return tuple(int(n) for n in re.findall(r"\d+", s)[:3])


LANG = {"ja": "日文", "mixed": "混合", "zh-Hans": "簡中", "zh-Hant": "繁中", "zh": "中文", "unknown": "未知",
        "packs": "中文包"}


def open_game(path: Path, home: Path | None = None) -> tuple[Path, engines.EnginePlugin, Store]:
    roots = engines.find_game_roots(Path(path).resolve())
    if len(roots) != 1:
        raise ValueError("找不到支援的遊戲" if not roots else
                         "找到多個遊戲，請指定其中一個：\n" + "\n".join(f"  {r}" for r, _ in roots))
    game, plugin = roots[0]
    return game, plugin, Store(project_dir(game, plugin, home))


def counts(store: Store) -> dict:
    recs = store.records.values()
    by = Counter(r["status"] for r in recs)
    unc = sum(1 for r in recs if r["status"] == "pending" and "uncertain" in r.get("flags", []))
    return {"total": len(store.records), "pending": by["pending"] - unc, "uncertain": unc,
            "translated": by["translated"], "reviewed": by["reviewed"], "failed": by["failed"],
            "skip": by["skip"], "orphan": by["orphan"]}


def extract(game: Path, plugin, store: Store, force=False) -> dict:
    """Raises extract.LanguageGateStop when the game is already Chinese; `force` is「仍要翻譯」."""
    if not force and Library().games.get(str(Path(game).resolve()), {}).get("builtin_chinese"):
        raise extraction.LanguageGateStop({**extraction.language_gate([]), "primary": "zh"}, [])
    return extraction.extract(game, plugin, store, force=force)


def _chinese_probe(entry: dict, manifest: dict) -> dict:
    probes = (entry.get("probe", {}), manifest.get("language_probe", {}),
              {"primary": entry.get("lang", ""),
               "packs": ["內建中文"] if entry.get("builtin_chinese") or entry.get("lang") == "packs" else []})
    return next((p for p in probes if langdetect.has_chinese(p)), {})


def translation_blocked(game: Path, plugin, store: Store) -> bool:
    entry = Library().games.get(str(Path(game).resolve()), {})
    return bool(_chinese_probe(entry, store.manifest) or plugin.language_packs(game))


def _check_translation(store: Store, plugin, limit, only) -> None:
    if not store.manifest:
        raise ValueError("尚未提取文本，請先執行 extract")
    if translation_blocked(Path(store.manifest["game_dir"]), plugin, store):
        raise ValueError("遊戲內已有中文，不需翻譯")
    pipeline.select(store, limit, only)


def estimate(store: Store, plugin, backend, limit=None, only=None, fresh=False, *, prepare=False) -> dict:
    _check_translation(store, plugin, limit, only)
    if prepare:
        backend.prepare()
    return pipeline.estimate(store, plugin.control_codes, backend, limit, only, fresh)


def translate(store: Store, plugin, backend, limit=None, progress=None, stop=None, only=None, fresh=False) -> dict:
    """While running, `store.busy` keeps the glossary read-only (pipeline.update_glossary writes it)."""
    _check_translation(store, plugin, limit, only)
    backend.prepare()
    store.busy = True
    try:
        return pipeline.run(store, plugin.control_codes, backend, limit, progress=progress, stop=stop,
                            only=only, fresh=fresh)
    finally:
        store.busy = False


def batch(paths: list, make_backend, home: Path | None = None, apply=False, limit=None, progress=None,
          stop=None, opener=None) -> list[dict]:
    """F16: extract (first time only) → translate → optionally apply, one game after another. A game that fails is
    reported and the next one runs; a problem the user must fix (login, quota, settings) ends the batch.
    `opener(path)` lets the GUI pass its open Stores, so one project never has two Store objects in memory."""
    results = []
    for i, path in enumerate(paths):
        if stop is not None and stop.is_set():
            break
        row = {"game": str(path), "status": "ok", "translated": 0, "failed": 0, "files": 0, "error": ""}
        results.append(row)

        def report(stage, **more):          # stage: 提取 / 翻譯 / 寫回, with the game's place in the batch
            if progress:
                progress({**more, "stage": stage, "game": row["game"], "index": i, "of": len(paths)})

        try:
            game, plugin, store = opener(path) if opener else open_game(path, home)
            row["game"] = str(game)
            if translation_blocked(game, plugin, store):
                row["status"] = "skip"
                continue
            if not store.manifest:
                report("提取")
                extract(game, plugin, store)
            report("翻譯")
            result = translate(store, plugin, make_backend(), limit, stop=stop,
                               progress=lambda s: report("翻譯", **s))
            row.update(translated=result["translated"], failed=result["failed"])
            if result.get("stopped"):
                row["status"] = "stopped"
            elif apply:
                report("寫回")
                plan = writeback.plan(game, store, plugin)
                if plan.files:
                    writeback.commit(game, plan)
                row["files"] = len(plan.files)
        except extraction.LanguageGateStop:
            row["status"] = "skip"
        except (AuthRequired, UsageLimit, ConfigError) as ex:
            row.update(status="paused", error=str(ex))
            break
        except Exception as ex:             # third-party game files: report this game and go on with the next
            row.update(status="error", error=str(ex) or type(ex).__name__)
    return results


def preview(game: Path, store: Store, plugin) -> writeback.ApplyPlan:
    # Unity's large native memory copies hold the GIL even on a worker thread.
    return (writeback.isolated_plan if plugin.name == "unity" else writeback.plan)(game, store, plugin)


def apply(game: Path, plan: writeback.ApplyPlan) -> None:
    writeback.commit(game, plan)


def restore(game: Path) -> list[tuple[str, str]]:
    return Backup(game).restore()


def undo(store: Store, run: str) -> int:
    return store.undo(run)


def export_batches(store: Store, plugin) -> list[Path]:
    return exchange.export_batches(store, plugin.control_codes)


def import_batches(store: Store, plugin, files: list[Path]) -> tuple[str, int, dict]:
    """Batch files and share files (F15) in one run, so one undo reverts the whole import."""
    run = new_run()
    shares = [f for f in files if exchange.is_share(f)]
    ok, bad = exchange.import_batches(store, plugin.control_codes, [f for f in files if f not in shares], run)
    for path in shares:
        n, rejected = exchange.import_share(store, plugin.name, plugin.control_codes, path, run)
        ok += n
        bad = dict(Counter(bad) + Counter(rejected))
    return run, ok, bad


def export_share(store: Store, plugin, path: Path) -> int:
    return exchange.export_share(store, plugin.name, plugin.control_codes, path)


def masked(rec: dict, plugin) -> tuple[str, str, dict]:
    """(source, translation, masks) with control codes as placeholders, for the editor's tags."""
    core, _ = text.normalize(rec["src"])
    src, masks = text.mask(core, plugin.control_codes)
    return src, exchange.mask_dst(rec.get("dst") or "", masks), masks


def edit(store: Store, plugin, rid: str, dst: str) -> list[str]:
    """Manual edit (masked text). Same T11 checks as the translator; on success the row becomes reviewed."""
    rec = store.records[rid]
    src, _, masks = masked(rec, plugin)
    errors = text.validate(src, dst, masks)
    if not errors:
        pad = text.normalize(rec["src"])[1]
        store.update_many(new_run(), [(rid, _reviewed(rec, text.postprocess(dst, masks, pad)))])
    return errors


def mark_reviewed(store: Store, ids) -> int:
    items = [(i, _reviewed(store.records[i], store.records[i]["dst"])) for i in ids
             if store.records[i].get("dst") and store.records[i]["status"] != "reviewed"]
    store.update_many(new_run(), items)
    return len(items)


def apply_same(store: Store, rid: str) -> int:
    """Copy one translation to the other rows with the same source text (rows differ only by kind)."""
    rec = store.records[rid]
    if not rec.get("dst"):
        return 0
    items = [(r["id"], _reviewed(r, rec["dst"])) for r in store.records.values()
             if r["id"] != rid and r["src"] == rec["src"] and r["status"] not in ("skip", "orphan")]
    store.update_many(new_run(), items)
    return len(items)


def _reviewed(rec: dict, dst: str) -> dict:
    return {"dst": dst, "status": "reviewed", "by": "manual", "errors": [],
            "flags": [f for f in rec.get("flags", []) if f != "failed"]}


def glossary(store: Store) -> list[list[str]]:
    return [r[:4] for r in exchange.read_glossary(store.dir)]


def save_glossary(store: Store, rows: list[list[str]]) -> None:
    if getattr(store, "busy", False):
        raise ValueError("翻譯進行中，術語表暫時唯讀")
    exchange.write_glossary(store.dir, rows)


def dict_fill(store: Store) -> int:
    """Re-run dictionary suggestions (§6.10) for glossary candidates that still have no translation."""
    rows, n = glossary(store), 0
    for r in rows:
        if r[3] == "seed" and not r[1]:
            hit = dicts.suggest(r[0])
            if hit and (hit[0] or hit[1] and not r[2]):
                r[1], r[2], r[3] = hit[0], r[2] or hit[1], hit[2]
                n += 1
    if n:
        save_glossary(store, rows)
    return n


def dict_vndb(game: Path, plugin, store: Store) -> int:
    """VNDB character names for this game (sends only the title); new names become glossary candidates."""
    title = plugin.title(game, lambda rel: vfs.read_original(game, rel)) or game.name
    rows = glossary(store)
    known = {r[0] for r in rows}
    added = [[src, "", "", "seed"] for src in dict.fromkeys(r[0] for r in dicts.fetch_vndb(title)) if src not in known
             and not any(c in src for c in "\t\r\n")]
    if added:
        save_glossary(store, rows + added)
    dict_fill(store)
    return len(added)


def dict_wikidata(store: Store) -> int:
    """Ask Wikidata about candidates without a translation (sends only those terms)."""
    terms = [r[0] for r in glossary(store) if r[3] == "seed" and not r[1]]
    if terms:
        dicts.fetch_wikidata(terms)
    return dict_fill(store)


def qa(store: Store) -> int:
    """T13 on demand (after manual edits or imports); returns the number of flagged sentences."""
    return pipeline.qa.check(store, {s: d for s, (d, _) in pipeline.glossary(store).items()})


def style(store: Store) -> str:
    return pipeline.style(store)


def save_style(store: Store, text: str) -> None:
    """F18 style.md: free-form rules sent to backends that follow them (Codex)."""
    atomic_write(store.dir / "style.md", (text.strip() + "\n" if text.strip() else "").encode("utf-8"))


def launch(game: Path) -> None:
    """MV keeps Game.exe next to www/, MZ inside the game folder."""
    exe = next((d / "Game.exe" for d in (game, game.parent) if (d / "Game.exe").is_file()), None)
    if exe is None:
        raise ValueError("找不到 Game.exe，請手動啟動遊戲")
    subprocess.Popen([str(exe)], cwd=exe.parent)


def open_folder(path: Path) -> None:
    os.startfile(path)  # shortcut: Windows only (the target platform); add xdg-open/open when other OSes are supported.


def config_path() -> Path:
    return projects_home().parent / "config.json"


CONFIG_KEYS = {"sakura": set(SakuraConfig.__dataclass_fields__),
               "codex": set(CodexConfig.__dataclass_fields__) | {"privacy_ack"},
               "openai": set(OpenAIConfig.__dataclass_fields__),
               "sakura_local": set(LocalConfig.__dataclass_fields__)}


def load_config() -> dict:
    path = config_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text("utf-8"))
    except (ValueError, OSError) as ex:
        raise ValueError(f"無法讀取設定檔 {path}：{ex}") from ex
    if not isinstance(data, dict):
        raise ValueError(f"設定檔 {path} 格式錯誤")
    return data


def save_config(section: str, values: dict) -> None:
    """Validated by the backend's own config class; other keys in config.json are kept."""
    unknown = set(values) - CONFIG_KEYS[section]
    if unknown:
        raise ValueError(f"未知的 {section} 設定：{', '.join(sorted(unknown))}")
    {"sakura": SakuraConfig, "codex": CodexConfig, "openai": OpenAIConfig, "sakura_local": LocalConfig}[section](
        **{k: v for k, v in values.items() if k != "privacy_ack"})
    data = load_config()
    data[section] = values
    atomic_write(config_path(), json.dumps(data, ensure_ascii=False, indent=1).encode("utf-8"))


def sakura_config() -> SakuraConfig:
    return SakuraConfig(**load_config().get("sakura", {}))


def codex_config() -> CodexConfig:
    return CodexConfig(**{k: v for k, v in load_config().get("codex", {}).items() if k != "privacy_ack"})


def local_config() -> LocalConfig:
    return LocalConfig(**load_config().get("sakura_local", {}))


def openai_config() -> OpenAIConfig:
    return OpenAIConfig(**load_config().get("openai", {}))


class Library:
    """Games the GUI knows about (library.json). Removing one forgets it; game files and project data stay."""

    def __init__(self, path: Path | None = None):
        self.path = path or projects_home().parent / "library.json"
        data = json.loads(self.path.read_text("utf-8")) if self.path.exists() else {}
        self.games: dict[str, dict] = data.get("games", {})
        self.folders: list[str] = data.get("folders", [])      # scanned parent folders, rescanned together

    def _save(self) -> None:
        atomic_write(self.path, json.dumps({"games": self.games, "folders": self.folders}, ensure_ascii=False,
                                           indent=1).encode("utf-8"))

    def add(self, path: Path) -> list[str]:
        game, plugin, _ = open_game(path)
        return self._add([(game, plugin)])

    def scan(self, parent: Path, depth: int = 5, progress=None, stop=None) -> list[str]:
        """Remembers `parent` for rescan(); progress gets {"stage": "掃描", "index", "of", "game"} per game found."""
        parent = Path(parent).resolve()
        if str(parent) not in self.folders:
            self.folders.append(str(parent))
        if progress:
            progress({"stage": "尋找遊戲", "game": str(parent)})
        return self._add(engines.find_game_roots(parent, depth), progress, stop)

    def rescan(self, progress=None, stop=None) -> list[str]:
        """Scan every remembered folder again; a folder that no longer exists is skipped."""
        found = []
        for folder in list(self.folders):
            if stop is not None and stop.is_set():
                break
            if Path(folder).is_dir():
                found += self.scan(Path(folder), progress=progress, stop=stop)
        return found

    def forget_folder(self, folder: str) -> None:
        self.folders = [f for f in self.folders if f != folder]
        self._save()

    def _add(self, roots, progress=None, stop=None) -> list[str]:
        added = []
        for i, (game, plugin) in enumerate(roots):
            if stop is not None and stop.is_set():
                break
            if progress:
                progress({"stage": "掃描", "index": i, "of": len(roots), "game": str(game)})
            entry = self.games.setdefault(str(game), {"engine": plugin.name})
            try:
                fingerprint = plugin.scan_fingerprint(game)
            except Exception:                    # cache validation must not prevent normal error reporting
                fingerprint = None
            if not fingerprint or entry.get("scan_fingerprint") != fingerprint or entry.get("unsupported") \
                    or entry.get("engine") != plugin.name:
                entry.update({"engine": plugin.name, **_detect(game, plugin), "scan_fingerprint": fingerprint})
                if entry.get("unsupported"):
                    print(f"[掃描失敗] {game}：{entry['unsupported']}", flush=True)
            added.append(str(game))
        self._save()
        return added

    def redetect(self, key: str) -> None:
        game, plugin, _ = open_game(Path(key))
        self.games[key].pop("scan_fingerprint", None)
        try:
            plugin.scan_fingerprint(game)        # also invalidates an in-memory Unity context after file changes
        except Exception:
            pass
        self.games[key].update({"engine": plugin.name, **_detect(game, plugin)})
        self._save()

    def set_builtin_chinese(self, key: str, enabled: bool) -> None:
        if enabled:
            self.games[key]["builtin_chinese"] = True
        else:
            self.games[key].pop("builtin_chinese", None)
        self._save()

    def remove(self, key: str) -> None:
        self.remove_many([key])

    def remove_many(self, keys) -> None:
        for key in keys:
            self.games.pop(key, None)
        self._save()

    def row(self, key: str, home: Path | None = None, store: Store | None = None) -> dict:
        """Name, engine, language label, status and progress of one library row (§11.3)."""
        e, game = self.games[key], Path(key)
        row = {"key": key, "name": game.parent.name if game.name.lower() == "www" else game.name,
               "engine": e["engine"], "lang": LANG.get(e.get("lang"), e.get("lang", "")), "progress": 0,
               "no_need": False, "reason": e.get("unsupported", "")}
        if e.get("builtin_chinese"):
            return {**row, "lang": "中文包", "status": "不需翻譯", "no_need": True,
                    "reason": "已由使用者確認遊戲內可切換中文"}
        if row["reason"]:
            return {**row, "status": "不支援"}
        plugin = next(p for p in engines.PLUGINS if p.name == e["engine"])
        project = store.dir if store is not None else project_dir(game, plugin, home)
        store = store if store is not None else Store(project)
        probe = _chinese_probe(e, store.manifest)
        if probe:
            row.update(no_need=True, reason=_probe_text(probe),
                       lang=LANG["packs"] if probe.get("packs") else LANG.get(probe["primary"], probe["primary"]))
        if store.manifest:
            c = counts(store)
            done = c["translated"] + c["reviewed"]
            todo = done + c["pending"] + c["failed"]
            row["progress"] = round(100 * done / todo) if todo else 100
        row["status"] = ("不需翻譯" if row["no_need"] else "已寫回" if Backup(game).files
                         else "已提取" if (project / "manifest.json").exists() else "未處理")
        return row


def _detect(game: Path, plugin) -> dict:
    try:
        lang, packs = extraction.probe(game, plugin)
    except Exception as ex:                          # third-party game files: one bad game must not stop a scan
        return {"lang": "", "probe": {}, "unsupported": str(ex) or type(ex).__name__}
    return {"lang": "packs" if packs else lang["primary"], "probe": lang, "unsupported": ""}


def _probe_text(p: dict) -> str:
    return (f"假名比例 {p.get('kana_ratio', 0):.0%}、簡體專有字 {p.get('simp_only', 0)}、"
            f"繁體專有字 {p.get('trad_only', 0)}、內建語言包 {len(p.get('packs', []))} 個")


def history(store: Store) -> list[dict]:
    """One row per translation/import run (F21), oldest first. Undo runs are listed as `undo-<run>`."""
    rows: dict[str, dict] = {}
    for e in store._all_entries():
        if e["run"].startswith(("qa-", "undo-qa-")):     # QA flag passes are not translation runs
            continue
        row = rows.setdefault(e["run"], {"run": e["run"], "time": e.get("ts", 0), "by": "", "items": 0,
                                         "translated": 0, "failed": 0})
        row["items"] += 1
        row["by"] = row["by"] or e["set"].get("by") or ""
        row["translated"] += e["set"].get("status") == "translated"
        row["failed"] += e["set"].get("status") == "failed"
    undone = {r[5:] for r in rows if r.startswith("undo-")}
    out = sorted(rows.values(), key=lambda r: r["time"])
    for r in out:
        r["undone"] = r["run"] in undone
        r["time"] = datetime.fromtimestamp(r["time"]).isoformat(timespec="seconds") if r["time"] else ""
    return out


def diagnostics(game: Path, plugin, store: Store, out: Path) -> Path:
    """G8: tool/engine info, file list + hashes, error counts. No game text, tokens or user-name paths."""
    home = str(Path.home())
    files = store.manifest.get("files", {})
    errors = Counter(e for r in store.records.values() if r["status"] == "failed" for e in r.get("errors", []))
    info = {"yakupatch": __version__, "python": platform.python_version(), "platform": platform.platform(),
            "engine": plugin.name, "engine_version": plugin.version, "detect": plugin.detect(game),
            "game_dir": str(game).replace(home, "<HOME>"), "counts": counts(store), "errors": dict(errors),
            "runs": [{k: r[k] for k in ("run", "time", "items", "translated", "failed", "undone")} for r in history(store)],
            "files": [{"path": rel, "sha256": i.get("sha256"), "encoding": i.get("encoding")} for rel, i in sorted(files.items())]}
    out = Path(out)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("diagnostics.json", json.dumps(info, ensure_ascii=False, indent=1))
    return out


class Job:
    """A background unit of work. `fn(job)` reads `job.stop` and calls `job.report(progress)`."""

    def __init__(self, key, fn, label=""):
        self.key, self.fn, self.label = key, fn, label
        self.stop = threading.Event()
        self.state = "queued"           # queued / running / done / failed / paused / cancelled
        self.progress: dict = {}
        self.result = self.error = None
        self._pausing = False
        self._done = threading.Event()
        self.listeners: list = []

    def report(self, progress: dict) -> None:
        self.progress = dict(progress)
        self._notify()

    def pause(self) -> None:
        self._pausing = True
        self.stop.set()

    def cancel(self) -> None:
        self.stop.set()

    def wait(self, timeout=None) -> bool:
        return self._done.wait(timeout)

    def _notify(self):
        for fn in list(self.listeners):
            fn(self)


class JobQueue:
    """G7: one translation job per backend key at a time; key=None (extract/apply) runs immediately."""

    def __init__(self):
        self._lock = threading.Lock()
        self._waiting: dict = {}
        self._busy: set = set()
        self.listeners: list = []

    def submit(self, key, fn, label="") -> Job:
        job = Job(key, fn, label)
        job.listeners.extend(self.listeners)
        with self._lock:
            if key is not None and key in self._busy:
                self._waiting.setdefault(key, []).append(job)
                job._notify()
                return job
            if key is not None:
                self._busy.add(key)
        self._start(job)
        return job

    def resume(self, job: Job) -> Job:
        return self.submit(job.key, job.fn, job.label)

    def _start(self, job: Job) -> None:
        threading.Thread(target=self._run, args=(job,), daemon=True).start()

    def _run(self, job: Job) -> None:
        if job.stop.is_set():
            job.state = "paused" if job._pausing else "cancelled"
        else:
            job.state = "running"
            job._notify()
            try:
                job.result = job.fn(job)
                job.state = ("paused" if job._pausing else "cancelled") if job.stop.is_set() else "done"
            except Exception as ex:      # surfaced to the caller through job.error; the queue keeps going
                job.error, job.state = ex, "failed"
        job._done.set()
        job._notify()
        with self._lock:
            nxt = self._waiting.get(job.key, []).pop(0) if self._waiting.get(job.key) else None
            if nxt is None:
                self._busy.discard(job.key)
        if nxt is not None:
            self._start(nxt)

