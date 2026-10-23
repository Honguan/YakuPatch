"""Command line for I0. The GUI (I2) will call the same functions."""
import argparse
import json
import sys
from collections import Counter
from pathlib import Path

from . import engines, exchange, pipeline, writeback
from .backends.sakura import Sakura, SakuraConfig
from .backup import Backup
from .extract import LanguageGateStop, extract, project_dir, projects_home
from .exchange import Store

_LANG = {"ja": "日文", "mixed": "混合（部分已是中文）", "zh-Hans": "簡體中文", "zh-Hant": "繁體中文",
         "zh": "中文", "unknown": "未知"}
_REASON = {"placeholders": "佔位符不符", "newlines": "換行數不符", "length_ratio": "長度比例異常",
           "kana_residue": "殘留日文", "repetition": "重複退化", "extra_text": "含多餘說明",
           "empty": "譯文空白", "unknown_id": "找不到 id", "src_changed": "原文已變動",
           "reviewed": "已人工審閱，不覆蓋", "no_dst": "沒有 dst", "bad_json": "JSON 格式錯誤",
           "line_count": "回應行數不符", "too_long": "輸入或輸出超出限制",
           "transient": "連線重試耗盡", "refused": "模型拒絕翻譯"}


def main(argv=None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    ap = argparse.ArgumentParser(prog="yakupatch", description="遊戲文本翻譯：提取、匯出入、寫回、還原")
    ap.add_argument("--home", type=Path, help="專案資料存放位置（預設 ~/.yakupatch/projects）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name, help_ in [("detect", "偵測引擎"), ("extract", "提取文本"), ("status", "顯示進度"),
                        ("export", "匯出分批檔"), ("import", "匯入譯文"), ("apply", "寫回遊戲"),
                        ("restore", "還原遊戲"), ("undo", "撤銷一次匯入"), ("translate", "Sakura 本地翻譯")]:
        p = sub.add_parser(name, help=help_)
        p.add_argument("game", type=Path)
        if name == "translate":
            p.add_argument("--backend", choices=["sakura"], default="sakura", help="翻譯後端")
            p.add_argument("--limit", type=int, help="只翻前 N 筆（名稱／系統優先）")
            p.add_argument("--dry-run", action="store_true", help="只預估，不連網、不修改專案")
            p.add_argument("--api-base", default="http://127.0.0.1:8080/v1", help="Sakura API 位址")
            p.add_argument("--model", default="", help="模型名稱（預設讀 /models）")
            p.add_argument("--version", choices=["0.9", "1.0", "galtransl-v1"], default="1.0", help="提示詞版本")
            p.add_argument("--timeout", type=float, default=999, help="請求逾時秒數")
            p.add_argument("--parallel", type=int, help="平行數上限（預設讀 /props）")
            p.add_argument("--retries", type=int, default=3, help="連線暫時失敗的重試次數（0–5）")
            p.add_argument("--max-tokens", type=int, default=1024, help="每次最大輸出 token")
            p.add_argument("--temperature", type=float, default=0.1, help="取樣溫度")
            p.add_argument("--top-p", type=float, default=0.3, help="取樣 top_p")
            p.add_argument("--frequency-penalty", type=float, default=0.05, help="頻率懲罰")
            p.add_argument("--force-glossary", action="store_true", help="每批附上整份術語表")
            p.add_argument("--enlarge-kana", action="store_true", help="放大小假名")
            p.add_argument("--repeat-threshold", type=int, default=20, help="重複退化偵測門檻")
            translate_parser = p
        if name == "extract":
            p.add_argument("--force", action="store_true", help="遊戲已是中文仍要提取")
        if name == "import":
            p.add_argument("files", nargs="*", type=Path, help="預設為 batches/in/*.jsonl")
        if name == "apply":
            p.add_argument("--yes", action="store_true", help="確認寫回（不加只顯示預覽）")
        if name == "undo":
            p.add_argument("run", nargs="?", help="不指定時列出可撤銷的紀錄")
    args = ap.parse_args(argv)
    if args.cmd == "translate":                          # only translate reads config.json, so a bad file never blocks restore
        try:
            translate_parser.set_defaults(**_sakura_defaults(projects_home().parent / "config.json"))
        except ValueError as ex:
            print(f"錯誤：{ex}")
            return 1
        args = ap.parse_args(argv)                       # command line still wins over config.json
    try:
        return _run(args)
    except KeyboardInterrupt:
        print("已中斷；已完成批次已保存，重跑 translate 即可續跑。")
        return 130
    except (LanguageGateStop, writeback.GameChanged, ValueError, OSError, RuntimeError) as ex:
        print(f"錯誤：{ex}")
        return 1


def _sakura_defaults(config: Path) -> dict:
    if not config.exists():
        return {}
    try:
        data = json.loads(config.read_text("utf-8"))
    except (ValueError, OSError) as ex:
        raise ValueError(f"無法讀取設定檔 {config}：{ex}") from ex
    sakura = data.get("sakura", {}) if isinstance(data, dict) else None
    if not isinstance(sakura, dict):
        raise ValueError(f"設定檔 {config} 格式錯誤：sakura 必須是物件")
    out = {k.replace("-", "_"): v for k, v in sakura.items()}
    unknown = sorted(set(out) - set(SakuraConfig.__dataclass_fields__))
    if unknown:
        raise ValueError(f"設定檔 {config} 有未知的 sakura 設定：{', '.join(unknown)}"
                         f"（可用：{', '.join(SakuraConfig.__dataclass_fields__)}）")
    return out


def _open(args) -> tuple[Path, engines.EnginePlugin, Store]:
    roots = engines.find_game_roots(args.game.resolve())
    if len(roots) != 1:
        raise ValueError("找不到支援的遊戲" if not roots else
                         "找到多個遊戲，請指定其中一個：\n" + "\n".join(f"  {r}" for r, _ in roots))
    game, plugin = roots[0]
    return game, plugin, Store(project_dir(game, plugin, args.home))


def _run(args) -> int:
    game, plugin, store = _open(args)
    if args.cmd == "detect":
        print(f"引擎：{plugin.name}\n遊戲資料夾：{game}\n專案資料夾：{store.dir}")
    elif args.cmd == "extract":
        try:
            probe = extract(game, plugin, store, force=args.force)
        except LanguageGateStop as stop:
            p = stop.probe
            print(f"語言預檢：{_LANG.get(p['primary'], p['primary'])}，不需翻譯。"
                  f"（假名比例 {p['kana_ratio']:.0%}，簡體專有字 {p['simp_only']}，繁體專有字 {p['trad_only']}）")
            for pack in stop.packs:
                print(f"內建語言包：{pack.hint}")
            print("仍要翻譯請加 --force。")
            return 2
        print(f"語言預檢：{_LANG.get(probe['primary'], probe['primary'])}"
              f"（假名比例 {probe['kana_ratio']:.0%}，中文句比例 {probe['zh_sentence_ratio']:.0%}）")
        _status(store)
    elif args.cmd == "status":
        _status(store)
    elif args.cmd == "translate":
        if not store.manifest:
            raise ValueError("尚未提取文本，請先執行 extract")
        pipeline.select(store, args.limit)
        backend = Sakura(SakuraConfig(**{key: getattr(args, key) for key in SakuraConfig.__dataclass_fields__}))
        if not args.dry_run:
            backend.prepare()
        est = pipeline.estimate(store, plugin.control_codes, backend, args.limit)
        print(f"待翻 {est['items']} 筆、{est['characters']:,} 字，快取 {est['cached']} 筆、"
              f"約 {est['batches']} 批，預估 {est['seconds']} 秒（未計重試；離線槽數未經伺服器確認）。")
        if args.dry_run:
            return 0
        result = pipeline.run(store, plugin.control_codes, backend, args.limit,
                              progress=lambda s: print(f"已翻 {s['translated']}、失敗 {s['failed']}", flush=True))
        print(f"翻譯紀錄 {result['run']}：成功 {result['translated']}、失敗 {result['failed']}，耗時 {result['seconds']} 秒")
        for reason, n in result["errors"].items():
            print(f"  {_REASON.get(reason, reason)}：{n}")
        return 1 if result["failed"] else 0
    elif args.cmd == "export":
        paths = exchange.export_batches(store, plugin.control_codes)
        print(f"已匯出 {len(paths)} 個分批檔到 {store.dir / 'batches' / 'out'}（填寫說明：INSTRUCTIONS.md）")
    elif args.cmd == "import":
        files = args.files or sorted((store.dir / "batches" / "in").glob("part-*.jsonl"))
        if not files:
            raise ValueError(f"沒有可匯入的檔案，請放到 {store.dir / 'batches' / 'in'}")
        run = exchange.new_run()
        ok, bad = exchange.import_batches(store, plugin.control_codes, files, run)
        print(f"匯入紀錄 {run}：通過 {ok} 句，拒絕 {sum(bad.values())} 句")
        for reason, n in sorted(bad.items(), key=lambda kv: -kv[1]):
            print(f"  {_REASON.get(reason, reason)}：{n}")
        if bad:
            print("被拒絕的句子已寫入同名的 .rejected.jsonl")
    elif args.cmd == "apply":
        plan = writeback.plan(game, store, plugin)
        for w in plan.warnings[:20]:
            print(f"警告：{w}")
        print(f"將修改 {len(plan.files)} 個檔案（替換 {sum(f.replaced for f in plan.files)} 處）：")
        for f in plan.files:
            print(f"  {f.rel}  {len(f.new):,} bytes")
        if any(f.rel.endswith(("Actors.json", "System.json")) for f in plan.files):
            print("提示：舊存檔中的角色名等資料會維持原文，建議開新遊戲。")
        if not plan.files:
            print("沒有需要寫回的內容。")
        elif not args.yes:
            print("這是預覽。確認寫回請加 --yes（原檔會先備份到 _yakupatch_backup/）。")
        else:
            writeback.commit(game, plan)
            print("寫回完成。")
    elif args.cmd == "restore":
        results = Backup(game).restore()
        if not results:
            print("沒有備份，遊戲未被修改。")
        counts = Counter(r for _, r in results)
        print(f"已還原 {counts['restored']} 個，原本就是原檔 {counts['original']} 個，"
              f"遊戲已更新而略過 {counts['stale']} 個")
        for rel, r in results:
            if r == "stale":
                print(f"  略過（遊戲已更新）：{rel}")
    elif args.cmd == "undo":
        if not args.run:
            for run, n in store.runs().items():
                print(f"{run}  {n} 筆")
        else:
            print(f"已撤銷 {store.undo(args.run)} 筆修改。")
    return 0


def _status(store: Store) -> None:
    recs = store.records.values()
    by = Counter(r["status"] for r in recs)
    unc = sum(1 for r in recs if r["status"] == "pending" and "uncertain" in r.get("flags", []))
    print(f"共 {len(store.records)} 句：待翻 {by['pending'] - unc}、不確定 {unc}（預設不翻）、"
          f"已翻 {by['translated']}、已審閱 {by['reviewed']}、失敗 {by['failed']}、"
          f"不翻 {by['skip']}、孤立 {by['orphan']}")
    print(f"專案資料夾：{store.dir}")


if __name__ == "__main__":
    sys.exit(main())
