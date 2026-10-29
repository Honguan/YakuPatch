import json,os,subprocess,sys,unittest


class Console(unittest.TestCase):
    def setUp(self):
        import tempfile
        from unittest.mock import patch
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        env = patch.dict(os.environ, {"YAKUPATCH_HOME": folder.name})
        env.start()
        self.addCleanup(env.stop)

    def test_history_search_scans_beyond_display_limit(self):
        import tempfile
        from pathlib import Path
        from yakupatch.gui.console import read_history
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "gui-old.log"
            path.write_text("ERROR early crash\n" + "normal\n" * 3000, encoding="utf8")
            text, count = read_history(path, "EARLY", True)
            self.assertEqual(count, 1)
            self.assertIn("early crash", text)
            text, count = read_history(path)
            self.assertEqual(count, 3001)
            self.assertLessEqual(len(text.splitlines()), 2000)

    def test_missing_stdout_descriptor(self):
        script = """
import os,sys
from yakupatch.gui.console import ConsoleCapture
os.close(1)
sys.stdout=None
c=ConsoleCapture()
print('without console',flush=True)
c.close()
assert 'without console' in c.drain()
"""
        result = subprocess.run([sys.executable, '-X', 'utf8', '-c', script], capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_python_native_and_windows_output_and_restore(self):
        script = r'''
import ctypes,json,os,sys,time
from yakupatch.gui.console import ConsoleCapture
c=ConsoleCapture()
print('python 中文',flush=True)
print('error 中文',file=sys.stderr,flush=True)
os.write(1,b'native stdout\n')
os.write(2,b'native stderr\n')
if os.name=='nt':
 k=ctypes.WinDLL('kernel32');k.GetStdHandle.restype=ctypes.c_void_p
 k.WriteFile.argtypes=[ctypes.c_void_p,ctypes.c_void_p,ctypes.c_ulong,ctypes.c_void_p,ctypes.c_void_p]
 data='Windows 原生\n'.encode();n=ctypes.c_ulong()
 assert k.WriteFile(k.GetStdHandle(-11),data,len(data),ctypes.byref(n),None)
c.close()
if os.name=='nt':
 data=b'windows restored\n'
 assert k.WriteFile(k.GetStdHandle(-11),data,len(data),ctypes.byref(n),None)
parts=[]
while True:
 text=c.drain()
 if not text:break
 parts.append(text)
print('CAPTURE='+json.dumps(''.join(parts),ensure_ascii=True))
print('restored',file=sys.stderr)
'''
        result=subprocess.run([sys.executable,'-X','utf8','-c',script],capture_output=True,timeout=15)
        self.assertEqual(result.returncode,0,result.stderr.decode('utf8',errors='replace'))
        stdout=result.stdout.decode('utf8')
        captured=json.loads(next(s[8:] for s in stdout.splitlines() if s.startswith('CAPTURE=')))
        for text in ('python 中文','error 中文','native stdout','native stderr'):
            self.assertIn(text,captured)
        if os.name=='nt':
            self.assertIn('Windows 原生',captured)
            self.assertIn('windows restored',stdout)
            self.assertNotIn('windows restored',captured)
        self.assertIn('python 中文',stdout)
        self.assertIn(b'restored',result.stderr)

    def test_buffer_is_bounded_and_reports_overflow(self):
        from collections import deque
        import threading
        from yakupatch.gui.console import ConsoleCapture
        c=ConsoleCapture.__new__(ConsoleCapture)
        import io
        c.log = io.BytesIO()
        c.pending=deque(maxlen=256);c.lock=threading.Lock();c.dropped=0
        for i in range(300):c._queue(str(i)+'\n')
        self.assertEqual(len(c.pending),256)
        self.assertIn('44',c.drain())
        self.assertIn(b'0\n', c.log.getvalue())

    def test_log_survives_abrupt_exit_and_export_contains_all_output(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as folder:
            script = r'''
import faulthandler,os,time
from pathlib import Path
from yakupatch.gui.console import ConsoleCapture
c=ConsoleCapture()
print('normal output',flush=True)
os.write(2,b'ERROR native failure\n')
for _ in range(200):
 if b'ERROR native failure' in c.log_path.read_bytes():break
 time.sleep(.01)
else:raise AssertionError('log not persisted')
faulthandler.dump_traceback(file=c.log)
c.export(Path(os.environ['YAKUPATCH_HOME'])/'export.log')
os._exit(17)
'''
            result = subprocess.run([sys.executable, '-X', 'utf8', '-c', script],
                                    env={**os.environ, 'YAKUPATCH_HOME': folder}, capture_output=True, timeout=15)
            self.assertEqual(result.returncode, 17, result.stderr)
            log = next((Path(folder)/'logs').glob('gui-*.log')).read_bytes()
            self.assertIn(b'ERROR native failure', log)
            self.assertIn(b'Current thread', log)
            self.assertEqual((Path(folder)/'export.log').read_bytes(), log)

    def test_disk_failure_does_not_stop_capture(self):
        from collections import deque
        import threading
        from unittest.mock import Mock
        from yakupatch.gui.console import ConsoleCapture
        c = ConsoleCapture.__new__(ConsoleCapture)
        c.pending = deque(maxlen=256)
        c.lock = threading.Lock()
        c.dropped = 0
        c.log = Mock()
        c.log.write.side_effect = OSError("disk full")
        c._queue("first\n")
        c._queue("second\n")
        text = c.drain()
        self.assertIn("紀錄寫入失敗", text)
        self.assertIn("second", text)
        self.assertEqual(c.log.write.call_count, 1)
