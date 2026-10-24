"""T1–T12 orchestration. Only the main thread writes Store and cache (§6)."""
import dataclasses
import hashlib
import json
import math
import sqlite3
import threading
import time
from collections import Counter
from contextlib import closing
from concurrent.futures import CancelledError, ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field

from . import qa, translate
from .backends import ConfigError, Refused, TooLong, Transient
from .exchange import Store, new_run, selectable
from .fsutil import atomic_write


@dataclass
class Work:
    id: str
    src: str
    text: str
    masks: dict
    pad: tuple[str, str]
    context: list[str]
    speaker: str | None
    terms: dict
    result: str | None = None
    errors: list[str] = field(default_factory=list)
    attempts: int = 0
    response: str | None = None        # the backend's last raw answer, logged when the line fails


class RunLog:
    """One JSONL file per translation run in the tool's data folder (never the game): settings, transient errors,
    every failed line with the backend's raw answer, and the totals. Workers write too, hence the lock."""

    def __init__(self, store: Store, run_id: str):
        self.path = store.dir.parent.parent / "logs" / f"{store.dir.name}-{run_id}.jsonl"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.file = open(self.path, "a", encoding="utf-8")
        self.lock = threading.Lock()

    def __call__(self, event: str, **data) -> None:
        line = json.dumps({"time": time.strftime("%Y-%m-%d %H:%M:%S"), "event": event, **data}, ensure_ascii=False)
        with self.lock:
            if not self.file.closed:          # a cancelled worker may still finish after the run ended
                self.file.write(line + "\n")
                self.file.flush()

    def close(self) -> None:
        with self.lock:
            self.file.close()


def select(store: Store, limit: int | None = None, only=None) -> list[dict]:
    """`only` (ids) is an explicit user choice: it may include translated or uncertain rows, never reviewed ones."""
    if limit is not None and limit < 0:
        raise ValueError("--limit 不可小於零")
    if only is not None:
        rows = [store.records[i] for i in only if i in store.records and
                store.records[i]["status"] in ("pending", "failed", "translated")]
    else:
        rows = [r for r in store.records.values() if selectable(r) and "refused" not in r.get("errors", [])]
    rows.sort(key=lambda r: r["kind"] not in ("name", "system"))
    return rows if limit is None else rows[:limit]


def glossary(store: Store) -> dict:
    path = store.dir / "glossary.tsv"
    rows = [line.split("\t") for line in path.read_text("utf-8").splitlines()[1:]] if path.exists() else []
    # Dictionary suggestions (source dict:*) are unconfirmed until the user ticks them (§6.10), so they are not enforced.
    return {r[0]: (r[1], r[2] if len(r) > 2 else "") for r in rows
            if len(r) > 1 and r[1] and not (len(r) > 3 and r[3].startswith("dict:"))}


def style(store: Store) -> str:
    """F18: per-game style.md; only backends with caps.style receive it."""
    path = store.dir / "style.md"
    return path.read_text("utf-8").strip() if path.exists() else ""


def use_style(store: Store, backend) -> None:
    if backend.caps.style:
        backend.style = style(store)


def update_glossary(store: Store) -> None:
    # shortcut: glossary/cache are outside Store.undo, extend run-aware undo when those artifacts must roll back together.
    path = store.dir / "glossary.tsv"
    if not path.exists():
        return
    translations = {r["src"]: r["dst"] for r in store.records.values()
                    if r["kind"] == "name" and r["status"] in ("translated", "reviewed")}
    lines = path.read_text("utf-8").splitlines()
    for i, line in enumerate(lines[1:], 1):
        cols = line.split("\t")
        dst = translations.get(cols[0], "")
        if len(cols) >= 4 and not cols[1] and cols[3] == "seed" and dst and not any(c in dst for c in "\t\r\n"):
            cols[1] = dst
            lines[i] = "\t".join(cols)
    body = "\n".join(lines) + "\n"
    if body != path.read_text("utf-8"):
        atomic_write(path, body.encode("utf-8"))


def work_list(store: Store, rows: list[dict], codes: list[str], terms: dict) -> list[Work]:
    all_rows = list(store.records.values())
    positions = {r["id"]: i for i, r in enumerate(all_rows)}
    works = []
    for row in rows:
        core, pad = translate.normalize(row["src"])
        text, masks = translate.mask(core, codes)
        pos = positions[row["id"]]
        file = row["refs"][0]["file"]
        previous = [r for r in all_rows[max(0, pos - 3):pos] if r["refs"][0]["file"] == file]
        context = [translate.mask(translate.normalize(r.get("dst") or r["src"])[0], codes)[0] for r in previous]
        matched = {k: v for k, v in sorted(terms.items(), key=lambda kv: -len(kv[0]))
                   if k in core or k == row.get("speaker")}
        works.append(Work(row["id"], row["src"], text, masks, pad, context, row.get("speaker"), matched))
    return works


def tokens(messages: list[dict]) -> int:
    # shortcut: 1.5 tokens/character is an estimate, use model tokenizer when calibrated budgets are needed.
    return math.ceil(sum(len(m["content"]) for m in messages) * 1.5) + 32


def batches(works: list[Work], backend, terms: dict, store: Store) -> list[list[Work]]:
    out, batch = [], []
    for work in works:
        candidate = batch + [work]
        same_file = not batch or store.records[batch[0].id]["refs"][0]["file"] == store.records[work.id]["refs"][0]["file"]
        if batch and (not same_file or len(candidate) > backend.caps.max_items or
                      tokens(backend.messages(candidate, terms)) > backend.caps.max_input_tokens):
            out.append(batch)
            batch = []
        batch.append(work)
    if batch:
        out.append(batch)
    return out


def cache_key(work: Work, backend, terms: dict) -> str:
    data = [backend.cache_identity, work.text, work.masks, work.context, work.speaker, terms]
    return hashlib.sha256(json.dumps(data, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def cache_get(store: Store, key: str) -> str | None:
    # shortcut: project cache only, add a shared cache when cross-project reuse has explicit isolation rules.
    path = store.dir / "cache.sqlite3"
    if not path.exists():
        return None
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as conn:
        row = conn.execute("SELECT dst FROM translations WHERE key = ?", (key,)).fetchone()
    return row[0] if row else None


def estimate(store: Store, codes: list[str], backend, limit=None, only=None, fresh=False) -> dict:
    rows = select(store, limit, only)
    terms = glossary(store)
    use_style(store, backend)
    works = work_list(store, rows, codes, terms)
    hits = 0 if fresh else sum(cache_get(store, cache_key(w, backend, terms)) is not None for w in works)
    pending = works if fresh else [w for w in works if cache_get(store, cache_key(w, backend, terms)) is None]
    groups = batches(pending, backend, terms, store)
    return {"items": len(rows), "characters": sum(len(r["src"]) for r in rows), "cached": hits,
            "batches": len(groups), "input_tokens": sum(tokens(backend.messages(g, terms)) for g in groups),
            "output_tokens": math.ceil(sum(len(w.text) for w in pending) * 1.5),
            "seconds": math.ceil(len(groups) / backend.caps.max_parallel) * 30}


def parse(response: str, works: list[Work], prefix: list[str]) -> list[str]:
    lines = response.replace("\r\n", "\n").strip("\n").split("\n")
    start = sum(s.count("\n") + 1 for s in prefix)
    sizes = [w.text.count("\n") + 1 for w in works]
    if len(lines) != start + sum(sizes):
        raise ValueError("line_count")
    result = []
    for size in sizes:
        result.append("\n".join(lines[start:start + size]))
        start += size
    return result


def parse_ids(response: str, works: list[Work]) -> list[str | None]:
    """json_ids backends answer {"translations":[{"id","translation"}]}; ids are batch positions. Missing ids -> None."""
    try:
        items = json.loads(response)["translations"]
        found = {}
        for item in items:
            key, dst = item["id"], item["translation"]
            if not isinstance(dst, str) or str(key) in found or str(key) not in {str(i) for i in range(len(works))}:
                raise ValueError
            found[str(key)] = dst
    except (ValueError, KeyError, TypeError):
        raise ValueError("bad_json") from None
    return [found.get(str(i)) for i in range(len(works))]


def dispatch(works: list[Work], backend, terms: dict, sleep=time.sleep, cancelled=None, log=None) -> list[Work]:
    """Workers only return results. A cancelled/incomplete batch never mutates Store."""
    def request(group, context=True, hot=False):
        messages = backend.messages(group, terms, context=context)
        for attempt in range(backend.config.retries + 1):
            if cancelled and cancelled.is_set():
                raise CancelledError
            try:
                for w in group:
                    w.attempts += 1
                return backend.complete(messages, temperature=min(2, max(0.3, getattr(backend.config, "temperature", 0) + 0.2))
                                        if hot else None)
            except Transient as ex:
                if log:
                    log("transient", ids=[w.id for w in group], attempt=attempt + 1, error=str(ex))
                if attempt == backend.config.retries:
                    raise
                if cancelled and sleep is time.sleep:
                    cancelled.wait(2 ** attempt)
                else:
                    sleep(2 ** attempt)

    def rejoin(w):
        """A message broken mid-sentence often comes back as one line: translate it joined, then break it again."""
        lines = w.text.split("\n")
        one = dataclasses.replace(w, text=translate.join_lines(lines), result=None, errors=[])
        solve([one], context=False, joined=True)
        w.attempts, w.response = one.attempts, one.response
        dst = translate.resplit(one.result, lines) if one.result is not None else None
        w.errors = (one.errors or ["line_count"]) if dst is None else \
            translate.validate(w.text, dst, w.masks, getattr(backend.config, "repeat_threshold", 4))
        w.result = dst if not w.errors else None
        return w

    def solve(group, context=True, hot=False, joined=False):
        if cancelled and cancelled.is_set():
            raise CancelledError
        reason = []
        try:
            if tokens(backend.messages(group, terms, context=context)) > backend.caps.max_input_tokens:
                raise TooLong("輸入預算不足")
            response = request(group, context, hot)
            for w in group:
                w.response = response
            values = (parse(response, group, group[0].context if context else []) if backend.caps.io_format == "lines"
                      else parse_ids(response, group))
            for w, dst in zip(group, values):
                w.errors = (["missing_id"] if dst is None
                            else translate.validate(w.text, dst, w.masks, getattr(backend.config, "repeat_threshold", 4)))
                w.result = dst if not w.errors else None
            if not any(w.errors for w in group):
                return group
        except (TooLong, Refused, Transient, ValueError) as ex:
            reason = ["too_long" if isinstance(ex, TooLong) else "refused" if isinstance(ex, Refused)
                      else "transient" if isinstance(ex, Transient) else "bad_json" if str(ex) == "bad_json"
                      else "line_count"]
            for w in group:
                w.errors, w.result = reason, None
            if isinstance(ex, Transient) or (isinstance(ex, Refused) and len(group) == 1):
                return group
        if len(group) > 1:
            mid = len(group) // 2
            return solve(group[:mid]) + solve(group[mid:])
        if context:
            return solve(group, context=False)
        if not hot and "too_long" not in reason:
            return solve(group, context=False, hot=True, joined=joined)
        w = group[0]
        if not joined and "\n" in w.text and {"line_count", "newlines"} & set(w.errors):
            return [rejoin(w)]
        return group

    return solve(works)


def run(store: Store, codes: list[str], backend, limit=None, progress=None, sleep=time.sleep, stop=None,
        only=None, fresh=False) -> dict:
    """`stop` (threading.Event) ends the job between waves; finished batches stay saved, the rest stay pending.
    `fresh` skips the cache (re-translate this sentence)."""
    if not store.manifest:
        raise ValueError("尚未提取文本，請先執行 extract")
    if backend.caps.io_format not in ("lines", "json_ids"):
        raise ConfigError(f"不支援的後端輸出格式：{backend.caps.io_format}")
    rows = select(store, limit, only)
    use_style(store, backend)
    run_id = new_run()
    log = RunLog(store, run_id)
    log("start", project=store.dir.name, backend=backend.by, total=len(rows), limit=limit, fresh=fresh,
        config=dataclasses.asdict(backend.config) if dataclasses.is_dataclass(backend.config) else None)
    stats = {"run": run_id, "log": str(log.path), "total": len(rows), "translated": 0, "failed": 0, "cached": 0, "errors": Counter(),
             "stopped": False}
    started = time.monotonic()

    def save(results, keys, cached=False):
        updates, cache_rows = [], []
        for w in results:
            ok = w.result is not None and not w.errors
            rec = store.records[w.id]
            fields = {"status": "translated" if ok else "failed", "by": backend.by,
                      "errors": w.errors, "attempts": w.attempts,
                      "flags": [f for f in rec.get("flags", []) if f != "failed"] + ([] if ok else ["failed"])}
            if ok:
                fields["dst"] = translate.postprocess(w.result, w.masks, w.pad)
                cache_rows.append((keys[w.id], w.result))
            else:
                log("failed", id=w.id, kind=rec["kind"], file=rec["refs"][0]["file"], locs=rec["refs"][0]["locs"],
                    src=w.src, sent=w.text, errors=w.errors, attempts=w.attempts, response=w.response)
            stats["translated" if ok else "failed"] += 1
            stats["cached"] += int(cached and ok)
            stats["errors"].update(w.errors)
            updates.append((w.id, fields))
        store.update_many(run_id, updates)
        if cache_rows and not cached:
            with closing(sqlite3.connect(store.dir / "cache.sqlite3")) as conn, conn:
                conn.execute("CREATE TABLE IF NOT EXISTS translations (key TEXT PRIMARY KEY, dst TEXT NOT NULL)")
                conn.executemany("INSERT OR REPLACE INTO translations VALUES (?, ?)", cache_rows)
        if progress:
            progress(stats)

    try:
        for names in (True, False):
            update_glossary(store)  # Also recovers seeds if interrupted just after the preceding phase.
            terms = glossary(store)
            phase = [r for r in rows if (r["kind"] in ("name", "system")) == names]
            works = work_list(store, phase, codes, terms)
            groups = batches(works, backend, terms, store)
            pool = ThreadPoolExecutor(max_workers=backend.caps.max_parallel)
            cancelled = threading.Event()
            try:
                # Bounded waves avoid queuing the entire game on cancellation.
                for start in range(0, len(groups), backend.caps.max_parallel):
                    if stop is not None and stop.is_set():
                        stats["stopped"] = True
                        break
                    futures, keys = [], {}
                    for group in groups[start:start + backend.caps.max_parallel]:
                        refreshed = work_list(store, [store.records[w.id] for w in group], codes, terms)
                        pending = []
                        for w in refreshed:
                            keys[w.id] = cache_key(w, backend, terms)
                            cached = None if fresh else cache_get(store, keys[w.id])
                            if cached is not None and not translate.validate(w.text, cached, w.masks,
                                                                             getattr(backend.config, "repeat_threshold", 4)):
                                w.result = cached
                                save([w], keys, cached=True)
                            else:
                                pending.append(w)
                        if pending:
                            futures.append(pool.submit(dispatch, pending, backend, terms, sleep, cancelled, log))
                    error = None
                    for future in as_completed(futures):
                        try:
                            results = future.result()
                        except Exception as ex:
                            cancelled.set()
                            error = error or ex
                            continue
                        save(results, keys)
                    if error:
                        log("error", error=f"{type(error).__name__}: {error}")
                        raise error
            finally:
                cancelled.set()
                # shortcut: urllib cannot cancel an in-flight socket, process exit can wait up to --timeout after Ctrl+C.
                pool.shutdown(wait=False, cancel_futures=True)
            if stats["stopped"]:
                break
        stats["inconsistent"] = qa.check(store, {s: d for s, (d, _) in glossary(store).items()}, f"qa-{run_id}")   # T13
        stats["seconds"] = round(time.monotonic() - started, 3)
        stats["errors"] = dict(stats["errors"])
        log("end", **{k: v for k, v in stats.items() if k not in ("log", "run")})
    finally:
        log.close()
    return stats
