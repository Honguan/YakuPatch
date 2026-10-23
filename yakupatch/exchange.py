"""Exchange files (PROJECT_PLAN §4): manifest.json, strings.jsonl snapshot + journal.jsonl, batch export/import."""
import json
import os
import secrets
import threading
import time
from pathlib import Path

from . import translate
from .fsutil import atomic_write

COMPACT_EVERY = 10_000
BATCH_SIZE = 200
INSTRUCTIONS = """# 翻譯分批檔填寫說明 / Instructions

每個 part-*.jsonl 一行一句（JSON）。請只新增 `dst` 欄位填入譯文，其他欄位保持不變，存到 batches/in/。
Each line is one JSON object. Add a `dst` field with the translation; keep every other field unchanged.

規則 / Rules:
1. `<0>`、`<1>`（或 `⟦0⟧`）是遊戲控制碼的佔位符：原樣保留，數量與編號不可改變。
   Keep placeholders such as <0> exactly, same count and numbers.
2. `src` 有幾個 `\\n`，`dst` 就要有幾個 `\\n`。Keep the same number of line breaks.
3. 不要加入說明文字、引號或「翻譯：」。Output only the translation.
4. `speaker` 是說話者，`ctx` 是前文，只供參考，不必翻譯。speaker/ctx are context only.
"""


class Store:
    """Single place that writes the exchange files; a lock serialises writers (GUI + translator, §4.6)."""

    def __init__(self, project_dir: Path):
        self.dir = Path(project_dir)
        self._lock = threading.RLock()
        self.manifest: dict = {}
        self.records: dict[str, dict] = {}
        self._journal_lines = 0
        self.listeners: list = []      # fn(ids | None) after each write; None = everything may have changed
        if (self.dir / "manifest.json").exists():
            self.manifest = json.loads((self.dir / "manifest.json").read_text("utf-8"))
        if (self.dir / "strings.jsonl").exists():
            for line in (self.dir / "strings.jsonl").read_text("utf-8").splitlines():
                rec = json.loads(line)
                self.records[rec["id"]] = rec
        for entry in _read_journal(self.dir / "journal.jsonl"):
            self._apply(entry["id"], entry["set"])
            self._journal_lines += 1

    def update_many(self, run: str, items: list[tuple[str, dict]]) -> None:
        """Append to the journal (fsync) before changing memory, so a crash loses at most the unfinished batch."""
        if not items:
            return
        with self._lock:
            lines = []
            for rid, fields in items:
                rec = self.records[rid]
                lines.append(json.dumps({"run": run, "id": rid, "set": fields,
                                         "old": {k: rec.get(k) for k in fields}, "ts": time.time()},
                                        ensure_ascii=False))
            self.dir.mkdir(parents=True, exist_ok=True)
            with open(self.dir / "journal.jsonl", "a", encoding="utf-8", newline="\n") as f:
                f.write("\n".join(lines) + "\n")
                f.flush()
                os.fsync(f.fileno())
            for rid, fields in items:
                self._apply(rid, fields)
            self._journal_lines += len(items)
            if self._journal_lines >= COMPACT_EVERY:
                self.compact()
        self._notify([rid for rid, _ in items])

    def replace(self, records: dict[str, dict], manifest: dict) -> None:
        """Used by extraction (E11): write a fresh snapshot and manifest."""
        with self._lock:
            self.records, self.manifest = records, manifest
            atomic_write(self.dir / "manifest.json",
                         json.dumps(manifest, ensure_ascii=False, indent=1).encode("utf-8"))
            self.compact()
        self._notify(None)

    def _notify(self, ids) -> None:
        for fn in list(self.listeners):
            fn(ids)

    def compact(self) -> None:
        """New snapshot; the journal is archived (not deleted) so runs stay undoable (F21)."""
        with self._lock:
            body = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in self.records.values())
            atomic_write(self.dir / "strings.jsonl", body.encode("utf-8"))
            journal = self.dir / "journal.jsonl"
            if journal.exists() and journal.stat().st_size:
                history = self.dir / "history"
                history.mkdir(exist_ok=True)
                os.replace(journal, history / f"journal-{time.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(2)}.jsonl")
            self._journal_lines = 0

    def runs(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for entry in self._all_entries():
            counts[entry["run"]] = counts.get(entry["run"], 0) + 1
        return counts

    def undo(self, run: str) -> int:
        entries = [e for e in self._all_entries() if e["run"] == run]
        self.update_many(f"undo-{run}", [(e["id"], e["old"]) for e in reversed(entries) if e["id"] in self.records])
        return len(entries)

    def _all_entries(self) -> list[dict]:
        files = sorted((self.dir / "history").glob("journal-*.jsonl")) if (self.dir / "history").exists() else []
        return [e for f in files + [self.dir / "journal.jsonl"] for e in _read_journal(f)]

    def _apply(self, rid: str, fields: dict) -> None:
        rec = self.records.get(rid)
        if rec is None:
            return
        for k, v in fields.items():
            if v is None:
                rec.pop(k, None)
            else:
                rec[k] = v


def _read_journal(path: Path) -> list[dict]:
    if not path.exists():
        return []
    lines = path.read_text("utf-8").split("\n")
    out = []
    for i, line in enumerate(lines):
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            if i >= len(lines) - 2:     # power loss mid-write: drop only the unfinished last line
                break
            raise
    return out


def new_run() -> str:
    return f"{time.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(2)}"


def selectable(rec: dict) -> bool:
    """T1 Selector: what still needs translating."""
    return rec["status"] in ("pending", "failed") and "uncertain" not in rec.get("flags", [])


def export_batches(store: Store, control_codes: list[str], size: int = BATCH_SIZE) -> list[Path]:
    out = store.dir / "batches" / "out"
    out.mkdir(parents=True, exist_ok=True)
    for old in out.glob("part-*.jsonl"):
        old.unlink()
    recs = list(store.records.values())
    order = {r["id"]: i for i, r in enumerate(recs)}
    todo = sorted((r for r in recs if selectable(r)),     # names/system terms first (T1 two-stage)
                  key=lambda r: (r["kind"] not in ("name", "system"), order[r["id"]]))
    dialogue = [r for r in recs if r["kind"] == "dialogue"]
    dpos = {r["id"]: i for i, r in enumerate(dialogue)}
    lines = []
    for r in todo:
        core, _ = translate.normalize(r["src"])
        masked, _ = translate.mask(core, control_codes)
        i = dpos.get(r["id"])
        ctx = [d["src"] for d in dialogue[max(0, i - 3):i]] if i is not None else []
        lines.append(json.dumps({"id": r["id"], "kind": r["kind"], "speaker": r.get("speaker"),
                                 "ctx": ctx, "src": masked}, ensure_ascii=False))
    paths = []
    for n in range(0, len(lines), size):
        p = out / f"part-{n // size + 1:04d}.jsonl"
        atomic_write(p, ("\n".join(lines[n:n + size]) + "\n").encode("utf-8"))
        paths.append(p)
    atomic_write(out / "INSTRUCTIONS.md", INSTRUCTIONS.encode("utf-8"))
    return paths


def import_batches(store: Store, control_codes: list[str], files: list[Path], run: str) -> tuple[int, dict[str, int]]:
    """Every line passes the same checks as built-in translation (T11) before it is written."""
    accepted, rejected, items = 0, {}, []
    for path in files:
        bad_lines = []
        for n, line in enumerate(path.read_text("utf-8-sig").splitlines(), 1):
            if not line.strip():
                continue
            reason, fields = _check_line(store, control_codes, line)
            if reason:
                rejected[reason] = rejected.get(reason, 0) + 1
                bad_lines.append(json.dumps({"line": n, "reason": reason, "raw": line}, ensure_ascii=False))
                continue
            fields["by"] = f"import:{path.name}"
            items.append((fields.pop("id"), fields))
            accepted += 1
        if bad_lines:
            atomic_write(path.with_suffix(".rejected.jsonl"), ("\n".join(bad_lines) + "\n").encode("utf-8"))
    store.update_many(run, items)
    return accepted, rejected


def _check_line(store: Store, control_codes: list[str], line: str) -> tuple[str | None, dict]:
    try:
        obj = json.loads(line)
    except json.JSONDecodeError:
        return "bad_json", {}
    rec = store.records.get(obj.get("id"))
    if rec is None:
        return "unknown_id", {}
    if rec["status"] == "reviewed":
        return "reviewed", {}
    core, pad = translate.normalize(rec["src"])
    masked, masks = translate.mask(core, control_codes)
    if obj.get("src") != masked:
        return "src_changed", {}
    dst = obj.get("dst")
    if not isinstance(dst, str):
        return "no_dst", {}
    errors = translate.validate(masked, dst, masks)
    if errors:
        return errors[0], {}
    flags = [f for f in rec.get("flags", []) if f != "failed"]
    return None, {"id": rec["id"], "dst": translate.postprocess(dst, masks, pad),
                  "status": "translated", "flags": flags}
