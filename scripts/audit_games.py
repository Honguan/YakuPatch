"""Read-only MV/MZ library audit; output metadata only, never game text."""
import argparse
import json
import os
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from yakupatch import engines
from yakupatch.engines.rpgmaker_mz import RpgMakerMZ
from yakupatch.encoding import decode, encode
from yakupatch.extract import language_gate
from yakupatch.fsutil import atomic_write, sha256
from yakupatch.safety import read_bytes


def discover(roots):
    found = {}
    for root in roots:
        for directory, dirs, files in os.walk(root, followlinks=False):
            dirs[:] = [d for d in dirs if not d.startswith('_yakupatch') and not (Path(directory) / d).is_symlink()]
            if Path(directory).name.lower() == 'js':
                for marker, engine in [('rmmz_core.js', 'rpgmaker-mz'), ('rpg_core.js', 'rpgmaker-mv')]:
                    if marker in files:
                        found[str(Path(directory).parent.resolve())] = engine
    return sorted(found.items())


def audit(roots):
    results = []
    for path, name in discover(roots):
        game = Path(path)
        plugin = next(p for p in engines.PLUGINS if p.name == name)
        item = {'game': path, 'engine': name, 'files': 0, 'segments': 0, 'errors': []}
        started = time.monotonic()
        segments = []
        try:
            item['detected'] = engines.detect(game)[0] is plugin
            if not item['detected']:
                item['errors'].append({'stage': 'detect', 'reason': 'missing System.json/data'})
            try:
                files = plugin.locate(game)
            except ValueError as ex:
                item['errors'].append({'stage': 'unsupported', 'reason': str(ex)})
                # Readable JSON may coexist with encrypted assets; audit bytes without attempting decryption.
                files = RpgMakerMZ.locate(plugin, game)
            for rel in files:
                try:
                    raw = read_bytes(game, rel)
                    text, info = decode(raw, plugin.default_encoding)
                    segs = plugin.parse(rel, text)
                    assert encode(plugin.render(rel, text, {}), info) == raw, 'roundtrip bytes differ'
                    assert sha256(read_bytes(game, rel)) == sha256(raw), 'source changed during audit'
                    item['files'] += 1
                    segments.extend(segs)
                except (ValueError, OSError, AssertionError, TypeError, KeyError) as ex:
                    item['errors'].append({'stage': 'roundtrip', 'file': rel, 'reason': type(ex).__name__})
            item['segments'] = len(segments)
            item['language'] = language_gate(segments)
            item['packs'] = [p.path for p in plugin.language_packs(game)]
        except (ValueError, OSError) as ex:
            item['errors'].append({'stage': 'locate', 'reason': str(ex)})
        item['seconds'] = round(time.monotonic() - started, 3)
        results.append(item)
        print(f"{len(results)} {name} {item['files']} files {len(item['errors'])} errors", flush=True)
    return {'games': len(results), 'engines': dict(Counter(r['engine'] for r in results)),
            'passed': sum(not r['errors'] for r in results), 'results': results}


def check_output(path, roots):
    resolved = path.resolve()
    for root in roots:
        root = root.resolve()
        if resolved == root or root in resolved.parents:
            raise ValueError('報告不可寫入受測遊戲庫')


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description='唯讀盤點 MV/MZ 遊戲')
    ap.add_argument('roots', nargs='+', type=Path)
    ap.add_argument('--output', type=Path, required=True)
    args = ap.parse_args()
    check_output(args.output, args.roots)
    for root in args.roots:
        if not root.is_dir():
            raise ValueError(f'遊戲庫不存在：{root}')
    result = audit(args.roots)
    atomic_write(args.output, json.dumps(result, ensure_ascii=False, indent=2).encode('utf-8'))
    print(json.dumps({k: v for k, v in result.items() if k != 'results'}, ensure_ascii=False))
