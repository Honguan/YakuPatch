import json,os,subprocess,sys,unittest


class Console(unittest.TestCase):
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
        c.pending=deque(maxlen=256);c.lock=threading.Lock();c.dropped=0
        for i in range(300):c._queue(str(i)+'\n')
        self.assertEqual(len(c.pending),256)
        self.assertIn('44',c.drain())
