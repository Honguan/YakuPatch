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
from .exchange import Store, new_run, selectable, read_glossary, write_glossary
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
    summary: str = ""                  # F14 scene summary of the file so far (caps.summary backends only)


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
    # Dictionary suggestions (source dict:*) are unconfirmed until the user ticks them (§6.10), so they are not enforced.
    return {r[0]: (r[1], r[2]) for r in read_glossary(store.dir) if r[1] and not r[3].startswith("dict:")}


def style(store: Store) -> str:
    """F18: per-game style.md; only backends with caps.style receive it."""
    path = store.dir / "style.md"
    return path.read_text("utf-8").strip() if path.exists() else ""


def use_style(store: Store, backend) -> None:
    if backend.caps.style:
        backend.style = style(store)


SUMMARY_PROMPT = ("你是遊戲劇情摘要助手。輸入的 previous 是這個場景先前的摘要，lines 是接下來已翻譯的台詞（依順序）。"
                  "請用{}寫一段新的場景摘要，不超過 200 字：出場人物、彼此稱呼與人稱、說話語氣、目前情節。"
                  '只輸出一個 JSON 物件：{{"translations":[{{"id":"0","translation":"摘要"}}]}}')


def scenes(store: Store) -> dict:
    """F14: per-file scene summaries, kept between runs so a resumed job continues the story."""
    path = store.dir / "summary.json"
    return json.loads(path.read_text("utf-8")) if path.exists() else {}


def summarize(backend, previous: str, lines: list[str]) -> str:
    """Uses the backend's own translation format (id 0 carries the summary), so no extra schema is needed."""
    script = "繁體中文" if backend.caps.output_script == "zh-Hant" else "簡體中文"
    body = json.dumps({"previous": previous, "lines": lines}, ensure_ascii=False)
    response = backend.complete([{"role": "system", "content": SUMMARY_PROMPT.format(script)},
                                 {"role": "user", "content": body}])
    try:
        text = json.loads(response)["translations"][0]["translation"]
    except (ValueError, KeyError, IndexError, TypeError):
        raise ValueError("bad_json") from None
    if not isinstance(text, str) or not text.strip():
        raise ValueError("bad_json")
    return text.strip()[:400]


def update_scenes(store: Store, backend, story: dict, wave: list[list[Work]], log) -> None:
    """After each wave, fold the newly translated lines of every touched file into its summary (one call per file).
    A failed summary keeps the previous one: it only helps consistency and never blocks translation."""
    lines = {}
    for group in wave:
        for w in group:
            rec = store.records[w.id]
            if rec["status"] == "translated" and rec["kind"] == "dialogue":
                lines.setdefault(rec["refs"][0]["file"], []).append(
                    (f"{rec['speaker']}：" if rec.get("speaker") else "") + rec["dst"])
    for file, new in lines.items():
        try:
            story[file] = summarize(backend, story.get(file, ""), new)
        except (Transient, TooLong, Refused, ValueError) as ex:
            log("summary_failed", file=file, error=str(ex))
    if lines:
        atomic_write(store.dir / "summary.json", json.dumps(story, ensure_ascii=False, indent=1).encode("utf-8"))


def update_glossary(store: Store) -> None:
    # shortcut: glossary/cache are outside Store.undo, extend run-aware undo when those artifacts must roll back together.
    translations = {r["src"]: r["dst"] for r in store.records.values()
                    if r["kind"] == "name" and r["status"] in ("translated", "reviewed")}
    rows, changed = read_glossary(store.dir), False
    for row in rows:
        dst = translations.get(row[0], "")
        if not row[1] and row[3] == "seed" and dst and not any(c in dst for c in "\t\r\n"):
            row[1], changed = dst, True
    if changed:
        write_glossary(store.dir, rows)


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
    data = [backend.cache_identity, work.text, work.masks, work.context, work.speaker, terms] + \
        ([work.summary] if work.summary else [])          # no summary: keys stay as before F14
    return hashlib.sha256(json.dumps(data, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def cache_get_many(store: Store, keys) -> dict[str, str]:
    """One connection for the whole lookup: opening one per line took ~25 s for 30k lines."""
    # shortcut: project cache only, add a shared cache when cross-project reuse has explicit isolation rules.
    path, keys, found = store.dir / "cache.sqlite3", list(dict.fromkeys(keys)), {}
    if not path.exists() or not keys:
        return found
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as conn:
        for i in range(0, len(keys), 500):                  # stay under SQLite's bound-parameter limit
            chunk = keys[i:i + 500]
            found.update(conn.execute(f"SELECT key, dst FROM translations WHERE key IN ({','.join('?' * len(chunk))})",
                                      chunk).fetchall())
    return found


def estimate(store: Store, codes: list[str], backend, limit=None, only=None, fresh=False) -> dict:
    rows = select(store, limit, only)
    terms = glossary(store)
    use_style(store, backend)
    works = work_list(store, rows, codes, terms)
    keys = {w.id: cache_key(w, backend, terms) for w in works}
    cached = {} if fresh else cache_get_many(store, keys.values())
    pending = [w for w in works if keys[w.id] not in cached]
    hits = len(works) - len(pending)
    groups = batches(pending, backend, terms, store)
    extra_in = extra_out = 0
    if backend.caps.summary and backend.caps.io_format == "json_ids":   # F14: about one summary call per wave
        waves = math.ceil(len(groups) / backend.caps.max_parallel)
        extra_in = math.ceil(sum(len(w.text) for w in pending if store.records[w.id]["kind"] == "dialogue") * 1.5) \
            + waves * 400
        extra_out = waves * 300
    return {"items": len(rows), "characters": sum(len(r["src"]) for r in rows), "cached": hits,
            "batches": len(groups), "input_tokens": sum(tokens(backend.messages(g, terms)) for g in groups) + extra_in,
            "output_tokens": math.ceil(sum(len(w.text) for w in pending) * 1.5) + extra_out,
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

    def halves(w):
        """Too long for one request (long credits, a whole scene in one scrolling text): translate the first and the
        second half of the lines apart, then put them back together."""
        lines = w.text.split("\n")
        parts = [dataclasses.replace(w, text="\n".join(p), context=[], result=None, errors=[], attempts=0)
                 for p in (lines[:len(lines) // 2], lines[len(lines) // 2:])]
        for p in parts:
            if p.text.strip():
                solve([p], context=False)
            else:
                p.result = p.text                       # blank lines only, nothing to translate
        w.attempts += sum(p.attempts for p in parts)
        w.response = parts[-1].response
        failed = next((p for p in parts if p.result is None), None)
        if failed:
            w.errors, w.result = failed.errors, None
        else:
            dst = "\n".join(p.result for p in parts)
            w.errors = translate.validate(w.text, dst, w.masks, getattr(backend.config, "repeat_threshold", 4))
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
        if "too_long" in reason and "\n" in w.text:
            return [halves(w)]
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
            use_summary = not names and backend.caps.summary and backend.caps.io_format == "json_ids"
            story = scenes(store) if use_summary else {}
            pool = ThreadPoolExecutor(max_workers=backend.caps.max_parallel)
            cancelled = threading.Event()
            try:
                # Bounded waves avoid queuing the entire game on cancellation.
                for start in range(0, len(groups), backend.caps.max_parallel):
                    if stop is not None and stop.is_set():
                        stats["stopped"] = True
                        break
                    futures, keys = [], {}
                    wave = groups[start:start + backend.caps.max_parallel]
                    refreshed = [work_list(store, [store.records[w.id] for w in group], codes, terms) for group in wave]
                    for w in (w for group in refreshed for w in group):
                        w.summary = story.get(store.records[w.id]["refs"][0]["file"], "")
                        keys[w.id] = cache_key(w, backend, terms)
                    stored = {} if fresh else cache_get_many(store, keys.values())
                    for group in refreshed:
                        pending = []
                        for w in group:
                            cached = stored.get(keys[w.id])
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
                    if use_summary:
                        update_scenes(store, backend, story, wave, log)
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
