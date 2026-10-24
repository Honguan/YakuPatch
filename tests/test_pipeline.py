import io
import json
from pathlib import Path
import shutil
import sqlite3
import unittest
from contextlib import redirect_stdout
from dataclasses import replace
from http.client import IncompleteRead
from types import SimpleNamespace
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from yakupatch import cli, engines, pipeline, translate, writeback
from yakupatch.backends import AuthRequired, BackendCaps, ConfigError, Refused, TooLong, Transient, UsageLimit
from yakupatch.backends.sakura import Sakura, SakuraConfig
from yakupatch.backup import Backup
from yakupatch.exchange import Store
from yakupatch.extract import extract, project_dir
from helpers import copy_game, fake_translate, snapshot


class Fake:
    caps = BackendCaps(max_input_tokens=100_000, max_parallel=1)
    config = SimpleNamespace(retries=1, temperature=0.1)
    by = 'fake:model/p1'
    cache_identity = by

    def __init__(self, action=None):
        self.calls = []
        self.action = action

    def messages(self, works, terms, context=True):
        return [{'content': json.dumps({'text': [w.text for w in works],
                                       'context': works[0].context if context else [],
                                       'terms': terms}, ensure_ascii=False)}]

    def complete(self, messages, temperature=None):
        obj = json.loads(messages[0]['content'])
        self.calls.append((obj, temperature))
        if self.action:
            result = self.action(obj, temperature, len(self.calls))
            if result is not None:
                return result
        return '\n'.join(fake_translate(s) for s in obj['context'] + obj['text'])


class Pipeline(unittest.TestCase):
    def setUp(self):
        self.game, home = copy_game('mz_min')
        self.plugin = engines.PLUGINS[0]
        self.store = Store(project_dir(self.game, self.plugin, home))
        extract(self.game, self.plugin, self.store)
        self.codes = self.plugin.control_codes

    def small(self, *sources):
        records = {}
        for i, src in enumerate(sources):
            records[str(i)] = {'id': str(i), 'src': src, 'dst': '', 'status': 'pending', 'kind': 'dialogue',
                               'speaker': None, 'refs': [{'file': 'data/Map001.json', 'locs': [f'/{i}']}], 'flags': []}
        self.store.replace(records, self.store.manifest)

    def run_fake(self, backend=None, **kwargs):
        return pipeline.run(self.store, self.codes, backend or Fake(), sleep=lambda _: None, **kwargs)

    def test_complete_flow_and_resume(self):
        original = snapshot(self.game)
        backend = Fake()
        result = self.run_fake(backend)
        self.assertGreater(result['translated'], 20)
        self.assertEqual(result['failed'], 0)
        self.assertTrue(any(call[0]['terms'] for call in backend.calls))
        self.assertTrue(any(call[0]['context'] for call in backend.calls))
        self.assertTrue(all(r['status'] == 'translated' for r in self.store.records.values()
                            if r['status'] not in ('skip',) and 'uncertain' not in r['flags']))
        writeback.commit(self.game, writeback.plan(self.game, self.store, self.plugin))
        Backup(self.game).restore()
        self.assertEqual(snapshot(self.game), original)
        self.store = Store(self.store.dir)
        before = len(backend.calls)
        self.assertEqual(self.run_fake(backend)['translated'], 0)
        self.assertEqual(len(backend.calls), before)

    def test_transient_retry_split_and_hot_retry(self):
        self.small('こんにちは', 'おはようございます')
        waits = []
        def action(obj, temperature, n):
            if n == 1:
                raise Transient('timeout')
            if len(obj['text']) > 1:
                raise TooLong('context')
            if temperature is None:
                return '翻译：你好'
        backend = Fake(action)
        result = pipeline.run(self.store, self.codes, backend, sleep=waits.append)
        self.assertEqual((result['translated'], result['failed']), (2, 0))
        self.assertEqual(waits, [1])
        events = [json.loads(line) for line in Path(result['log']).read_text('utf-8').splitlines()]
        self.assertIn({'event': 'transient', 'attempt': 1, 'error': 'timeout'},
                      [{k: e[k] for k in ('event', 'attempt', 'error') if k in e} for e in events])
        self.assertTrue(any(temp is not None for _, temp in backend.calls))

    def test_interrupt_preserves_completed_batches(self):
        self.small('こんにちは', 'おはようございます', 'こんばんは')
        backend = Fake(lambda obj, temp, n: (_ for _ in ()).throw(KeyboardInterrupt()) if n == 2 else None)
        backend.caps = replace(backend.caps, max_items=1)
        with self.assertRaises(KeyboardInterrupt):
            self.run_fake(backend)
        self.store = Store(self.store.dir)
        self.assertEqual(sum(r['status'] == 'translated' for r in self.store.records.values()), 1)
        next_backend = Fake()
        self.assertEqual(self.run_fake(next_backend)['translated'], 2)
        self.assertFalse(any('こんにちは' in c[0]['text'] for c in next_backend.calls))

    def test_merged_lines_are_translated_joined_and_split_again(self):
        self.small('「いや、ダメとは言わんが。お前いっつも、\nパンくずぽろぽろこぼすから」')
        one = '「不，我不是说不行。只是你总是，会掉面包屑」'
        backend = Fake(lambda obj, temp, n: one)                 # always one line, as Sakura does here
        result = self.run_fake(backend)
        self.assertEqual((result['translated'], result['failed']), (1, 0))
        self.assertEqual(backend.calls[-1][0]['text'], ['「いや、ダメとは言わんが。お前いっつも、パンくずぽろぽろこぼすから」'])
        self.assertEqual(self.store.records['0']['dst'], '「不，我不是说不行。\n只是你总是，会掉面包屑」')

    def test_resplit(self):
        self.assertEqual(translate.join_lines(['I am', 'here', '友人', 'です']), 'I am here友人です')
        self.assertEqual(translate.resplit('甲乙丙丁戊己庚辛', ['あいうえ', 'かきくけ']), '甲乙丙丁\n戊己庚辛')
        self.assertEqual(translate.resplit('甲乙，丙丁戊己庚辛', ['あいうえ', 'かきくけ']), '甲乙，\n丙丁戊己庚辛')  # nearby comma
        self.assertEqual(translate.resplit('甲<0>乙', ['あ<0>', 'い', 'う']), '甲\n<0>\n乙')         # placeholder intact
        self.assertIsNone(translate.resplit('甲', ['あ', 'い']))
        self.assertEqual(translate.postprocess('说I　LOVE　you。　好', {}, ('', '')), '说I LOVE you。　好')

    def test_validation_failure_and_line_mismatch(self):
        self.small('\\C[2]こんにちは\\C[0]', 'こんばんは')
        backend = Fake(lambda obj, temp, n: '説明です' if len(obj['text']) > 1 else '你好')
        result = self.run_fake(backend)
        self.assertEqual((result['translated'], result['failed']), (1, 1))
        failed = self.store.records['0']
        self.assertEqual(failed['dst'], '')
        self.assertIn('placeholders', failed['errors'])
        self.assertEqual(failed['status'], 'failed')
        self.assertIn('failed', failed['flags'])
        log = [json.loads(line) for line in Path(result['log']).read_text('utf-8').splitlines()]
        self.assertEqual(Path(result['log']).parent, self.store.dir.parent.parent / 'logs')     # tool data, not the game
        self.assertEqual([e['event'] for e in log], ['start', 'failed', 'end'])
        self.assertEqual((log[1]['id'], log[1]['response'], log[1]['src']), ('0', '你好', '\\C[2]こんにちは\\C[0]'))
        self.assertEqual(log[2]['failed'], 1)

    def test_fatal_errors_leave_pending_and_refusal_is_not_resent(self):
        self.small('こんにちは')
        for error in [AuthRequired, UsageLimit, ConfigError]:
            backend = Fake(lambda *args: (_ for _ in ()).throw(error('stop')))
            with self.assertRaises(error):
                self.run_fake(backend)
            self.assertEqual(self.store.records['0']['status'], 'pending')
        backend = Fake(lambda *args: (_ for _ in ()).throw(Refused('no')))
        self.assertEqual(self.run_fake(backend)['failed'], 1)
        self.assertEqual(self.run_fake(backend)['failed'], 0)
        self.assertEqual(len(backend.calls), 1)

    def test_refusal_isolated_to_one_sentence(self):
        sources = [f'こんにちは、旅人{i}さん' for i in range(8)]
        self.small(*sources)
        def action(obj, temperature, n):
            self.assertLessEqual(n, 15)  # A binary split of eight sentences has at most 15 requests.
            if sources[3] in obj['text']:
                raise Refused('no')
        backend = Fake(action)
        result = self.run_fake(backend)
        self.assertEqual((result['translated'], result['failed']), (7, 1))
        self.assertEqual(self.store.records['3']['errors'], ['refused'])
        self.assertEqual(pipeline.select(self.store), [])
        calls = len(backend.calls)
        self.assertLessEqual(calls, 15)
        self.assertEqual(self.run_fake(backend)['translated'], 0)
        self.assertEqual(len(backend.calls), calls)

    def test_cache_revalidated_and_key_scoped(self):
        self.small('こんにちは')
        backend = Fake()
        self.run_fake(backend)
        self.store.update_many('reset', [('0', {'status': 'pending', 'dst': ''})])
        self.assertEqual(self.run_fake(backend)['cached'], 1)
        self.assertEqual(len(backend.calls), 1)
        self.store.update_many('reset', [('0', {'status': 'pending'})])
        backend.cache_identity = 'fake:model/p2'
        self.assertEqual(self.run_fake(backend)['cached'], 0)
        self.assertEqual(len(backend.calls), 2)
        with sqlite3.connect(self.store.dir / 'cache.sqlite3') as conn:
            conn.execute("UPDATE translations SET dst = '翻译：你好'")
        conn.close()
        self.store.update_many('reset', [('0', {'status': 'pending'})])
        self.assertEqual(self.run_fake(backend)['cached'], 0)
        self.assertEqual(len(backend.calls), 3)

    def test_dry_run_limit_does_not_write_or_connect(self):
        before = snapshot(self.store.dir)
        with patch.object(Sakura, '_request', side_effect=AssertionError('network')), redirect_stdout(io.StringIO()) as out:
            code = cli.main(['--home', str(self.store.dir.parent), 'translate', str(self.game),
                             '--backend', 'sakura', '--limit', '2', '--dry-run'])
        self.assertEqual(code, 0)
        self.assertIn('待翻 2 筆', out.getvalue())
        self.assertEqual(snapshot(self.store.dir), before)

    def test_translate_reads_local_config_and_cli_overrides(self):
        home = self.store.dir.parent.parent / 'cfg'
        home.mkdir()
        (home / 'config.json').write_text(json.dumps({'sakura': {'api_base': 'http://10.0.0.1:8080/v1', 'parallel': 2}}), 'utf-8')
        seen = []
        real = cli.Sakura
        args = ['--home', str(self.store.dir.parent), 'translate', str(self.game), '--dry-run']
        with patch.dict('os.environ', {'YAKUPATCH_HOME': str(home)}), \
                patch.object(cli, 'Sakura', side_effect=lambda c: seen.append(c) or real(c)), redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main(args), 0)
            self.assertEqual(cli.main(args + ['--api-base', 'http://127.0.0.1:9/v1']), 0)
        self.assertEqual((seen[0].api_base, seen[0].parallel), ('http://10.0.0.1:8080/v1', 2))
        self.assertEqual((seen[1].api_base, seen[1].parallel), ('http://127.0.0.1:9/v1', 2))
        status = ['--home', str(self.store.dir.parent), 'status', str(self.game)]
        for bad, msg in [('{"sakura": {"api-bse": "x"}}', '未知的 sakura 設定：api_bse'),
                         ('{"sakura": []}', 'sakura 必須是物件'), ('{broken', '無法讀取設定檔')]:
            (home / 'config.json').write_text(bad, 'utf-8')
            with patch.dict('os.environ', {'YAKUPATCH_HOME': str(home)}), redirect_stdout(io.StringIO()) as out:
                self.assertEqual(cli.main(args), 1)
                self.assertEqual(cli.main(status), 0)               # a broken config never blocks other commands
            self.assertIn(msg, out.getvalue())
        self.assertEqual(pipeline.estimate(self.store, self.codes, Fake(), 0)['items'], 0)
        with self.assertRaises(ValueError):
            pipeline.select(self.store, -1)

    def test_parallel_batches(self):
        self.small('こんにちは', 'こんばんは', 'おはようございます')
        backend = Fake()
        backend.caps = replace(backend.caps, max_parallel=2, max_items=1)
        self.assertEqual(self.run_fake(backend)['translated'], 3)
        self.assertEqual(len(Store(self.store.dir).records), 3)

    def test_fresh_context_and_mask_collision(self):
        self.small('こんにちは', 'こんばんは')
        backend = Fake()
        backend.caps = replace(backend.caps, max_items=1)
        self.run_fake(backend)
        self.assertEqual(backend.calls[1][0]['context'], [fake_translate('こんにちは')])
        text, masks = translate.mask('\\C[1]<0>⟦0⟧こんにちは', self.codes)
        self.assertEqual(text, '⟦1⟧<0>⟦0⟧こんにちは')
        self.assertEqual(translate.postprocess(text, masks, ('', '')), '\\C[1]<0>⟦0⟧こんにちは')
        self.assertEqual(translate.normalize('\rこんにちは\r')[1], ('\n', '\n'))
        self.assertIn('repetition', translate.validate('はい', '是' * 20, {}, 20))
        self.assertNotIn('repetition', translate.validate('はい', '是' * 4, {}, 20))

    def test_retry_exhaustion_and_input_budget(self):
        self.small('こんにちは')
        backend = Fake(lambda *args: (_ for _ in ()).throw(Transient('down')))
        self.assertEqual(self.run_fake(backend)['errors'], {'transient': 1})
        self.assertEqual(len(backend.calls), 2)
        backend = Fake()
        backend.caps = replace(backend.caps, max_input_tokens=1)
        self.assertEqual(self.run_fake(backend)['errors'], {'too_long': 1})
        self.assertEqual(backend.calls, [])


class SakuraClient(unittest.TestCase):
    def test_incomplete_read_is_transient(self):
        response = unittest.mock.MagicMock()
        response.__enter__.return_value.read.side_effect = IncompleteRead(b'private server text', 10)
        with self.subTest('response body'), patch('yakupatch.backends.sakura.urlopen', return_value=response):
            with self.assertRaises(Transient) as caught:
                Sakura().complete([])
            self.assertNotIn('private server text', str(caught.exception))
        error = HTTPError('http://x', 503, '', {}, response.__enter__.return_value)
        with self.subTest('error body'), patch('yakupatch.backends.sakura.urlopen', side_effect=error):
            with self.assertRaises(Transient) as caught:
                Sakura().complete([])
            self.assertNotIn('private server text', str(caught.exception))

    def test_settings_props_prompt_and_truncation(self):
        backend = Sakura(SakuraConfig(api_base='http://localhost:8080', parallel=9))
        with patch.object(backend, '_request', side_effect=[{}, {'total_slots': 8,
             'default_generation_settings': {'n_ctx': 2048}}, {'data': [{'id': 'sakura-model'}]}]):
            backend.prepare()
        self.assertEqual((backend.model, backend.caps.max_parallel, backend.caps.max_input_tokens), ('sakura-model', 8, 600))
        work = pipeline.Work('x', '彼女', '彼女', {}, ('', ''), [], None, {'彼女': ('她', '女性')})
        self.assertIn('彼女->她 #女性', backend.messages([work], {})[1]['content'])
        with patch.object(backend, '_request', return_value={'choices': [{'finish_reason': 'length'}]}):
            with self.assertRaises(TooLong):
                backend.complete([])
        for kwargs in [{'parallel': 0}, {'retries': 6}, {'timeout': -1}, {'temperature': float('nan')}, {'api_base': 'file:///x'}]:
            with self.assertRaises(ValueError):
                SakuraConfig(**kwargs)

    def test_http_errors_and_payload(self):
        backend = Sakura()
        for status, body, kind in [(401, b'no', AuthRequired), (429, b'no', Transient),
                                   (503, b'no', Transient), (400, b'context size', TooLong),
                                   (400, b'bad model', ConfigError)]:
            with patch('yakupatch.backends.sakura.urlopen', side_effect=HTTPError('http://x', status, '', {}, io.BytesIO(body))):
                with self.assertRaises(kind):
                    backend.complete([])
        with patch('yakupatch.backends.sakura.urlopen', side_effect=URLError('down')):
            with self.assertRaises(Transient):
                backend.prepare()
        response = unittest.mock.MagicMock()
        response.__enter__.return_value = io.BytesIO(b'{"choices":[{"message":{"content":"hello"}}]}')
        with patch('yakupatch.backends.sakura.urlopen', return_value=response) as send:
            self.assertEqual(backend.complete([]), 'hello')
        req = send.call_args.args[0]
        self.assertTrue(req.full_url.endswith('/v1/chat/completions'))
        payload = json.loads(req.data)
        self.assertEqual((payload['temperature'], payload['top_p'], payload['frequency_penalty']), (0.1, 0.3, 0.05))


class MV(unittest.TestCase):
    def test_www_and_encryption_refusal(self):
        game, home = copy_game('mv_min')
        www = game / 'www'
        www.mkdir()
        for directory in ('data', 'js'):
            shutil.move(str(game / directory), www / directory)
        roots = engines.find_game_roots(game)
        self.assertEqual(len(roots), 1)
        root, plugin = roots[0]
        self.assertEqual((root, plugin.name), (www, 'rpgmaker-mv'))
        (www / 'secret.rpgmvp').write_bytes(b'RPGMV')
        self.assertIn('data/System.json', plugin.locate(www))
        store = Store(project_dir(root, plugin, home))
        extract(root, plugin, store)
        original = snapshot(root)
        result = pipeline.run(store, plugin.control_codes, Fake(), sleep=lambda _: None)
        self.assertGreater(result['translated'], 20)
        writeback.commit(root, writeback.plan(root, store, plugin))
        Backup(root).restore()
        self.assertEqual(snapshot(root), original)
        (www / 'data/Actors.json').write_text('[null]', encoding='utf-16')
        self.assertIn('data/Actors.json', plugin.locate(www))
        (www / 'data/Actors.json').write_bytes(b'RPGMV\x00\x00')
        with self.assertRaisesRegex(ValueError, '加密'):
            plugin.locate(www)
