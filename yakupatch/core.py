"""Core API shared by the CLI and the GUI (PROJECT_PLAN §3.4, §11.12): the GUI never reads or writes project/game files itself."""
import json
import platform
import threading
import zipfile
from collections import Counter
from datetime import datetime
from pathlib import Path

from . import __version__, engines, pipeline
from .exchange import Store
from .extract import project_dir


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


def translate(store: Store, plugin, backend, limit=None, progress=None, stop=None) -> dict:
    if not store.manifest:
        raise ValueError("尚未提取文本，請先執行 extract")
    pipeline.select(store, limit)
    backend.prepare()
    return pipeline.run(store, plugin.control_codes, backend, limit, progress=progress, stop=stop)


def history(store: Store) -> list[dict]:
    """One row per translation/import run (F21), oldest first. Undo runs are listed as `undo-<run>`."""
    rows: dict[str, dict] = {}
    for e in store._all_entries():
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

