"""External dictionaries (PROJECT_PLAN §6.10). Nothing ships with the program: data is fetched only when the user
asks, converted to dicts/<name>.sqlite, and used to suggest glossary candidates. Suggestions are never final."""
import csv
import gzip
import io
import json
import os
import sqlite3
import time
import xml.etree.ElementTree as ET
from contextlib import closing
from pathlib import Path
from urllib.parse import quote_plus
from urllib.request import Request, urlopen

from . import __version__

UA = f"YakuPatch/{__version__} (https://github.com/Honguan/YakuPatch)"
_NAMES = "https://raw.githubusercontent.com/shuheilocale/japanese-personal-name-dataset/main/japanese_personal_name_dataset/dataset/"
SOURCES = {
    "user": {"title": "自備詞庫", "license": "由使用者負責", "zh": True,
             "credit": "使用者匯入的 CSV／TSV／JSON 術語表。"},
    "wikidata": {"title": "Wikidata", "license": "CC0 1.0", "zh": True, "url": "https://query.wikidata.org/sparql",
                 "credit": "Wikidata（https://www.wikidata.org），CC0。只送出術語候選字串。"},
    "vndb": {"title": "VNDB", "license": "ODbL 1.0", "zh": False, "url": "https://api.vndb.org/kana",
             "credit": "VNDB（https://vndb.org），資料以 ODbL 1.0 授權。只送出遊戲標題。"},
    "jmnedict": {"title": "JMnedict（EDRDG）", "license": "EDRDG License", "zh": False,
                 "url": "http://ftp.edrdg.org/pub/Nihongo/JMnedict.xml.gz",
                 "credit": "This publication has included material from the JMnedict dictionary files in accordance "
                           "with the licence provisions of the Electronic Dictionaries Research Group "
                           "(https://www.edrdg.org/edrdg/licence.html)."},
    "names": {"title": "Japanese Personal Name Dataset", "license": "MIT", "zh": False, "url": _NAMES,
              "credit": "japanese-personal-name-dataset（https://github.com/shuheilocale/japanese-personal-name-dataset），"
                        "MIT License。"},
}
DOWNLOADABLE = ("jmnedict", "names")
_NAME_FILES = {"first_name_man_org.csv": "男性名", "first_name_woman_org.csv": "女性名", "last_name_org.csv": "姓"}
_ZH = ("zh-tw", "zh-hant", "zh-hk", "zh", "zh-hans", "zh-cn")        # Traditional first: Codex default output


def home() -> Path:
    from .extract import projects_home      # extract imports this module for E10
    return projects_home().parent / "dicts"


def _path(name: str) -> Path:
    if name not in SOURCES:
        raise ValueError(f"未知的字典：{name}")
    return home() / f"{name}.sqlite"


def _connect(path: Path):
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE IF NOT EXISTS entries (src TEXT NOT NULL, dst TEXT, lang TEXT, reading TEXT, type TEXT)")
    conn.execute("CREATE INDEX IF NOT EXISTS by_src ON entries (src)")
    conn.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
    return conn


def _write(name: str, rows, meta: dict, replace: bool) -> int:
    """`replace`: a fresh download swaps the whole file atomically; lookups (VNDB, Wikidata, user) accumulate."""
    home().mkdir(parents=True, exist_ok=True)
    target = _path(name)
    path = target.with_name(target.name + ".tmp") if replace else target
    if replace:
        path.unlink(missing_ok=True)
    with closing(_connect(path)) as conn, conn:
        if not replace:                                 # a new lookup replaces older answers for the same terms
            conn.executemany("DELETE FROM entries WHERE src = ?", [(s,) for s in {r[0] for r in rows}])
        conn.executemany("INSERT INTO entries VALUES (?, ?, ?, ?, ?)", rows)
        count = conn.execute("SELECT COUNT(*) FROM entries").fetchone()[0]
        meta = {**meta, "date": time.strftime("%Y-%m-%d"), "entries": str(count)}
        conn.executemany("INSERT OR REPLACE INTO meta VALUES (?, ?)", meta.items())
    if replace:
        os.replace(path, target)
    return len(rows)


def info() -> list[dict]:
    out = []
    for name, src in SOURCES.items():
        row = {"name": name, **src, "installed": _path(name).exists(), "date": "", "entries": 0}
        if row["installed"]:
            with closing(sqlite3.connect(_path(name))) as conn:
                meta = dict(conn.execute("SELECT key, value FROM meta"))
            row.update(date=meta.get("date", ""), entries=int(meta.get("entries", 0)), version=meta.get("version", ""))
        out.append(row)
    return out


def remove(name: str) -> None:
    _path(name).unlink(missing_ok=True)


def _get(url: str, opener=None, data: bytes | None = None, headers: dict | None = None, timeout=120) -> bytes:
    req = Request(url, data=data, headers={"User-Agent": UA, **(headers or {})})
    with (opener or urlopen)(req, timeout=timeout) as resp:
        return resp.read()


# ---------- downloads (whole dictionaries) ----------
def download(name: str, opener=None) -> int:
    if name == "jmnedict":
        raw = _get(SOURCES[name]["url"], opener, timeout=600)
        return _write(name, jmnedict_rows(gzip.GzipFile(fileobj=io.BytesIO(raw))), {"version": _jmnedict_date(raw)},
                      replace=True)
    if name == "names":
        rows = []
        for file, kind in _NAME_FILES.items():
            rows += name_rows(_get(_NAMES + file, opener).decode("utf-8"), kind)
        return _write(name, rows, {"version": "main"}, replace=True)
    raise ValueError(f"{name} 不是可下載的字典")


def jmnedict_rows(stream) -> list[tuple]:
    """One row per (written form, reading); type is JMnedict's name_type (expanded entity text)."""
    rows = []
    for _, el in ET.iterparse(stream, events=("end",)):
        if el.tag != "entry":
            continue
        kebs = [k.text for k in el.iter("keb") if k.text]
        rebs = [r.text for r in el.iter("reb") if r.text]
        kind = "；".join(dict.fromkeys(t.text for t in el.iter("name_type") if t.text))
        for src in kebs or rebs:
            rows.append((src, "", "", rebs[0] if rebs else "", kind))
        el.clear()
    return rows


def _jmnedict_date(raw: bytes) -> str:
    head = gzip.GzipFile(fileobj=io.BytesIO(raw)).read(1 << 16).decode("utf-8", "replace")
    mark = head.find("JMnedict created:")
    return head[mark + 17:mark + 28].strip() if mark >= 0 else ""


def name_rows(text: str, kind: str) -> list[tuple]:
    """CSV rows: reading, romaji, kanji spellings…"""
    rows = []
    for cols in csv.reader(io.StringIO(text)):
        if len(cols) >= 2 and cols[0]:
            for src in (cols[2:] or [cols[0]]):
                if src.strip():
                    rows.append((src.strip(), "", "", cols[0].strip(), kind))
    return rows


# ---------- lookups (only what the user asked for) ----------
def import_user(path: Path) -> int:
    """CSV/TSV (src, dst[, note]) or JSON ({src: dst} or [{"src","dst","note"}]); header rows are skipped."""
    text = Path(path).read_text("utf-8-sig")
    if Path(path).suffix.lower() == ".json":
        data = json.loads(text)
        items = data.items() if isinstance(data, dict) else [(d.get("src"), d.get("dst"), d.get("note", ""))
                                                             for d in data if isinstance(d, dict)]
        rows = [(str(i[0]), str(i[1]), str(i[2]) if len(i) > 2 else "") for i in items]
    else:
        delimiter = "\t" if "\t" in text.split("\n", 1)[0] else ","
        rows = [(c[0], c[1], c[2] if len(c) > 2 else "") for c in csv.reader(io.StringIO(text), delimiter=delimiter)
                if len(c) >= 2]
    rows = [(s.strip(), d.strip(), "", "", n.strip()) for s, d, n in rows
            if s and d and s.strip().lower() not in ("src", "原文", "source")]
    if not rows:
        raise ValueError("檔案中沒有可匯入的「原文、譯文」欄位")
    return _write("user", rows, {"version": Path(path).name}, replace=False)


def fetch_vndb(title: str, opener=None) -> list[tuple]:
    """Character names of the best VNDB match for `title` (sends only the title). Returns the new rows."""
    def post(path, body):
        return json.loads(_get(SOURCES["vndb"]["url"] + path, opener, json.dumps(body).encode("utf-8"),
                               {"Content-Type": "application/json"}, timeout=30))

    hits = post("/vn", {"filters": ["search", "=", title], "fields": "id,title,alttitle", "results": 1})["results"]
    if not hits:
        return []
    rows, page = [], 1
    while True:
        res = post("/character", {"filters": ["vn", "=", ["id", "=", hits[0]["id"]]], "fields": "name,original,aliases",
                                  "results": 100, "page": page})
        for c in res["results"]:
            names = [c.get("original")] + [a for a in c.get("aliases") or [] if a]
            for src in dict.fromkeys(s.replace(" ", "").replace("　", "") for s in names if s):   # 椎名 まゆり
                rows.append((src, "", "", c.get("name") or "", f"角色（{hits[0].get('alttitle') or hits[0]['title']}）"))
        if not res.get("more") or page >= 10:
            break
        page += 1
    if rows:
        _write("vndb", rows, {"version": hits[0]["id"]}, replace=False)
    return rows


def fetch_wikidata(terms: list[str], opener=None) -> int:
    """Items whose Japanese label equals a term, with their Chinese labels (sends only the terms)."""
    rows = []
    for start in range(0, len(terms), 40):
        values = " ".join('"' + t.replace("\\", "\\\\").replace('"', '\\"') + '"@ja' for t in terms[start:start + 40])
        query = ("SELECT ?label ?zh ?desc WHERE { VALUES ?label { " + values + " } ?item rdfs:label ?label . "
                 "?item rdfs:label ?zh . FILTER(LANG(?zh) IN (" + ",".join(f'"{z}"' for z in _ZH) + ")) "
                 'OPTIONAL { ?item schema:description ?desc . FILTER(LANG(?desc) = "ja") } } LIMIT 2000')
        res = json.loads(_get(SOURCES["wikidata"]["url"], opener, f"query={quote_plus(query)}".encode("ascii"),
                              {"Accept": "application/sparql-results+json",
                               "Content-Type": "application/x-www-form-urlencoded"}, timeout=60))
        for b in res["results"]["bindings"]:
            zh = b["zh"]
            rows.append((b["label"]["value"], zh["value"], zh.get("xml:lang", ""), "", b.get("desc", {}).get("value", "")))
    if rows:
        _write("wikidata", rows, {"version": "query"}, replace=False)
    return len(rows)


def lookup(term: str) -> list[dict]:
    """All local matches, best first: user > Wikidata (zh, Traditional first) > reading-only sources."""
    out = []
    for name in SOURCES:
        path = _path(name)
        if not path.exists():
            continue
        with closing(sqlite3.connect(path)) as conn:
            for dst, lang, reading, kind in conn.execute("SELECT dst, lang, reading, type FROM entries WHERE src = ?",
                                                         (term,)):
                out.append({"source": name, "src": term, "dst": dst or "", "lang": lang or "", "reading": reading or "",
                            "type": kind or ""})
    order = list(SOURCES)
    out.sort(key=lambda d: (order.index(d["source"]), not d["dst"], _ZH.index(d["lang"]) if d["lang"] in _ZH else 0))
    return out


def suggest(term: str) -> tuple[str, str, str] | None:
    """(dst, note, source) for a glossary candidate (E10); reading-only hits stay `seed` so T1 still translates them."""
    hits = lookup(term)
    if not hits:
        return None
    best = hits[0]
    readings = [h for h in hits if h["source"] in ("vndb", "jmnedict", "names") and h["reading"]]
    note = "；".join(dict.fromkeys(f"{h['reading']}（{h['type']}）" if h["type"] else h["reading"] for h in readings[:3]))
    if best["dst"]:
        return best["dst"], note or best["type"], f"dict:{best['source']}"
    return "", note, "seed"
