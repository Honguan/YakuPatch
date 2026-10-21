"""Opt-in full real-model cycle. Source game is read-only; apply/restore only on a fresh temp copy."""
import argparse
import json
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from yakupatch import engines, pipeline, writeback
from yakupatch.backends.sakura import Sakura, SakuraConfig
from yakupatch.backup import Backup
from yakupatch.exchange import Store
from yakupatch.extract import extract, project_dir
from yakupatch.fsutil import atomic_write, sha256
from audit_games import check_output


def hashes(root):
    return {str(p.relative_to(root)): sha256(p.read_bytes()) for p in root.rglob('*') if p.is_file()}


def cycle(source, backend, progress=None):
    roots = engines.find_game_roots(source.resolve())
    if len(roots) != 1:
        raise ValueError('請指定單一 MV/MZ 遊戲')
    source, plugin = roots[0]
    plugin.locate(source)  # Reject encrypted MV before omitting assets from the text-only copy.
    original = hashes(source / 'data')
    tmp = Path(tempfile.mkdtemp(prefix='yakupatch-real-')).resolve()
    protected = [Path('G:/game').resolve(), Path('D:/Games/TEMP').resolve(), source]
    if any(tmp == p or p in tmp.parents for p in protected):
        raise ValueError('暫存目錄落在唯讀遊戲庫，拒絕執行')
    game = tmp / 'game'
    for directory in ('data', 'js'):
        shutil.copytree(source / directory, game / directory)
    store = Store(project_dir(game, plugin, tmp / 'projects'))
    started = time.monotonic()
    before = hashes(game)
    probe = extract(game, plugin, store)
    assert probe['primary'] == 'ja', '驗收必須使用日文遊戲'
    estimate = pipeline.estimate(store, plugin.control_codes, backend)
    print(json.dumps({'source': str(source), 'temp': str(tmp), 'estimate': estimate}, ensure_ascii=False), flush=True)
    result = pipeline.run(store, plugin.control_codes, backend, progress=progress)
    plan = writeback.plan(game, store, plugin)
    assert not plan.warnings, plan.warnings
    writeback.commit(game, plan)
    assert not writeback.plan(game, store, plugin).files, '重複 apply 應無變更'
    restored = Backup(game).restore()
    identical = hashes(game) == before
    source_unchanged = hashes(source / 'data') == original
    assert identical and source_unchanged, '雜湊不符'
    return {'engine': plugin.name, 'source': str(source), 'temp': str(tmp), 'records': len(store.records),
            'selected': estimate['items'], 'result': result, 'applied_files': len(plan.files),
            'replaced': sum(f.replaced for f in plan.files), 'restored_files': len(restored),
            'restore_hash_identical': identical, 'source_hash_identical': source_unchanged,
            'seconds': round(time.monotonic() - started, 3)}


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description='真實 Sakura 完整驗收（只寫暫存副本）')
    ap.add_argument('games', nargs='+', type=Path)
    ap.add_argument('--api-base', default='http://127.0.0.1:8080/v1')
    ap.add_argument('--output', type=Path, required=True)
    args = ap.parse_args()
    check_output(args.output, [Path('G:/game'), Path('D:/Games/TEMP'), *args.games])
    backend = Sakura(SakuraConfig(api_base=args.api_base))
    backend.prepare()
    report = []
    for game in args.games:
        report.append(cycle(game, backend, progress=lambda s: print(
            f"已翻 {s['translated']} 失敗 {s['failed']}", flush=True)))
        atomic_write(args.output, json.dumps(report, ensure_ascii=False, indent=2).encode('utf-8'))
