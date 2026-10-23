"""T13 ConsistencyQA (PROJECT_PLAN §6.9): cross-sentence checks after translation. Only flags, never edits dst."""
import re
import unicodedata
from difflib import SequenceMatcher

from .exchange import Store, new_run

DONE = ("translated", "reviewed")
_SPACE = re.compile(r"\s+")


def _norm(text: str) -> str:
    """Source text without whitespace and punctuation: 「はい。」 and はい！ are the same sentence."""
    return "".join(c for c in _SPACE.sub("", text) if not unicodedata.category(c).startswith("P"))


def check(store: Store, terms: dict[str, str], run: str | None = None) -> int:
    """Set flag `inconsistent` + `qa` reasons; clear stale ones. `terms` is glossary src -> dst. Returns flagged count."""
    records = list(store.records.values())
    done = [r for r in records if r["status"] in DONE and r.get("dst")]
    terms = {s: d for s, d in terms.items() if len(s) >= 2 and d}
    for r in done:                                                       # translated names are terms too
        if r["kind"] == "name" and len(r["src"].strip()) >= 2:
            terms.setdefault(r["src"].strip(), r["dst"].strip())
    reasons: dict[str, list[str]] = {}

    # 術語遵循：原文含術語，譯文卻沒用指定譯名
    for r in done:
        for src, dst in terms.items():
            if src in r["src"] and src != r["src"].strip() and dst not in r["dst"]:
                reasons.setdefault(r["id"], []).append(f"term:{src}")

    # 同名異譯（同一原文在不同種類譯法不同）與同源異譯（只差標點或空白，譯文卻差很多）
    by_src: dict[str, list[dict]] = {}
    for r in done:
        key = _norm(r["src"])
        if key:
            by_src.setdefault(key, []).append(r)
    for group in by_src.values():
        base = group[0]
        for r in group[1:]:
            if SequenceMatcher(None, _norm(base["dst"]), _norm(r["dst"])).ratio() < 0.6:
                reasons.setdefault(r["id"], []).append("variant:" + base["id"])
                reasons.setdefault(base["id"], []).append("variant:" + r["id"])

    # 殘留未翻：同一檔案中前後句都已翻，中間卻是 failed 或未勾選的 uncertain
    order: dict[str, list[dict]] = {}
    for r in records:
        if r["status"] not in ("skip", "orphan") and r.get("refs"):
            order.setdefault(r["refs"][0]["file"], []).append(r)
    for rows in order.values():
        for prev, r, nxt in zip(rows, rows[1:], rows[2:]):
            if (prev["status"] in DONE and nxt["status"] in DONE and
                    (r["status"] == "failed" or r["status"] == "pending" and "uncertain" in r.get("flags", []))):
                reasons.setdefault(r["id"], []).append("gap")

    updates = []
    for r in records:
        new = sorted(set(reasons.get(r["id"], [])))
        flags = r.get("flags", [])
        has = "inconsistent" in flags
        if new == r.get("qa", []) and has == bool(new):
            continue
        fields = {"flags": [f for f in flags if f != "inconsistent"] + (["inconsistent"] if new else []),
                  "qa": new or None}
        updates.append((r["id"], fields))
    store.update_many(run or f"qa-{new_run()}", updates)
    return len(reasons)
