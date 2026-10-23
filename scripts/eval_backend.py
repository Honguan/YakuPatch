"""Opt-in backend evaluation (G5) on tests/eval/cases.jsonl: T11 failure, placeholder retention, glossary adherence as JSON."""
import argparse
import json
import sys
from collections import Counter
from pathlib import Path

import opencc

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from yakupatch import pipeline, translate
from yakupatch.backends.codex import Codex, CodexConfig
from yakupatch.backends.sakura import Sakura, SakuraConfig
from yakupatch.cli import _sakura_defaults
from yakupatch.engines import RpgMakerMZ
from yakupatch.extract import projects_home

_T2S = opencc.OpenCC("t2s")      # Sakura writes Simplified, Codex Traditional: compare terms script-neutrally
CASES = Path(__file__).resolve().parents[1] / "tests" / "eval" / "cases.jsonl"


def works(cases, codes):
    out = []
    for case in cases:
        core, pad = translate.normalize(case["src"])
        text, masks = translate.mask(core, codes)
        terms = {k: (v, "") for k, v in case.get("terms", {}).items()}
        out.append(pipeline.Work(case["id"], case["src"], text, masks, pad, [], case["speaker"], terms))
    return out


def evaluate(backend, cases, size=10):
    codes = RpgMakerMZ.control_codes
    items = works(cases, codes)
    done = []
    for start in range(0, len(items), size):
        group = items[start:start + size]
        terms = {k: v for w in group for k, v in w.terms.items()}
        done += pipeline.dispatch(group, backend, terms)
    by_cat, rows = {}, []
    for case, w in zip(cases, done):
        ok = w.result is not None and not w.errors
        kept = ok and all(k in w.result for k in w.masks) if w.masks else None
        terms = {k: v for k, v in case.get("terms", {}).items() if k in case["src"]}  # the speaker term need not appear
        followed = (ok and all(_T2S.convert(v) in _T2S.convert(w.result) for v in terms.values())) if terms else None
        stat = by_cat.setdefault(case["category"], Counter())
        stat["total"] += 1
        stat["failed"] += not ok
        if w.errors:
            stat["errors:" + ",".join(w.errors)] += 1
        if w.masks:
            stat["with_placeholders"] += 1
            stat["placeholders_kept"] += bool(kept)
        if terms:
            stat["with_terms"] += 1
            stat["terms_followed"] += bool(followed)
        rows.append({"id": case["id"], "src": case["src"],
                     "dst": translate.postprocess(w.result, w.masks, w.pad) if ok else None, "errors": w.errors})
    total = sum((c for c in by_cat.values()), Counter())
    rate = lambda a, b: round(total[a] / total[b], 4) if total[b] else None
    return {"backend": backend.by, "cases": len(cases), "t11_failure_rate": rate("failed", "total"),
            "placeholder_retention": rate("placeholders_kept", "with_placeholders"),
            "glossary_adherence": rate("terms_followed", "with_terms"),
            "by_category": {k: dict(v) for k, v in by_cat.items()}, "rows": rows}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--backend", choices=["sakura", "codex"], default="sakura")
    ap.add_argument("--model", default="")
    ap.add_argument("--effort", default="low")
    ap.add_argument("--output", type=Path)
    args = ap.parse_args(argv)
    if args.backend == "codex":
        backend = Codex(CodexConfig(model=args.model, effort=args.effort))
    else:
        backend = Sakura(SakuraConfig(**_sakura_defaults(projects_home().parent / "config.json")))
    backend.prepare()
    cases = [json.loads(line) for line in CASES.read_text("utf-8").splitlines() if line]
    report = evaluate(backend, cases)
    body = json.dumps(report, ensure_ascii=False, indent=1)
    if args.output:
        args.output.write_text(body, "utf-8")
    summary = {k: report[k] for k in ("backend", "cases", "t11_failure_rate", "placeholder_retention", "glossary_adherence")}
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
