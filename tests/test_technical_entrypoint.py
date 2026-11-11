import base64
import importlib.util
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import unittest
from unittest.mock import patch
from urllib.request import Request, urlopen

from backup_http import DOWNLOAD_PATH
from tests.technical_support import ROOT, TechnicalTestCase


class EntrypointTests(TechnicalTestCase):
    def run_script(self, script, environment=None):
        result = subprocess.run([sys.executable, '-c', script], cwd=ROOT,
                                env=environment or self.environment(), capture_output=True, timeout=30)
        return result

    def test_snapshot_and_marker_exist_before_real_legacy_import(self):
        # Freeze the application's Python calendar in the child process: both
        # fixture dates and import-time generation use the same day, even if
        # local/UTC dates differ or midnight occurs between setup and import.
        script = r'''
import importlib, sqlite3, sys
from contextlib import closing
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch
from backup_snapshot import Settings, verify_file

class FixedDate(date):
    @classmethod
    def today(cls):
        return cls(2026, 11, 12)

s=Settings.from_env()
today=FixedDate.today()
start=(today-timedelta(days=1)).isoformat()
with closing(sqlite3.connect(s.source)) as c, c:
    c.execute("INSERT INTO recurrences(title,frequency,start_date,next_date) "
              "VALUES ('test','daily',?,?)", (start,start))
original=importlib.import_module
def checked(name, *args, **kwargs):
    if name == 'app':
        assert (s.directory/'ready.json').is_file()
        assert (s.directory/'import-allowed.json').is_file()
        verify_file(s.directory/'snapshot.sqlite3')
        with sqlite3.connect(s.directory/'snapshot.sqlite3') as c:
            assert c.execute('SELECT COUNT(*) FROM tasks').fetchone()[0] == 24
    return original(name, *args, **kwargs)
importlib.import_module=checked
with patch('datetime.date', FixedDate):
    import backup_entrypoint
assert 'app' in sys.modules
with closing(sqlite3.connect(s.source)) as c:
    generated=c.execute('SELECT recurrence_date FROM tasks WHERE recurrence_id IS NOT NULL '
                        'ORDER BY recurrence_date').fetchall()
    assert generated == [(start,), (today.isoformat(),)]
'''
        result = self.run_script(script)
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors='replace'))
        self.assertEqual(self.count(self.source), 26)
        self.assertEqual(self.count(self.settings.directory / 'snapshot.sqlite3'), 24)

    def test_failure_prevents_any_app_import(self):
        self.source.write_bytes(b'corrupt')
        script = r'''
import importlib, sys
from backup_snapshot import BackupError
def forbidden(name, *a, **kw):
    if name == 'app': raise AssertionError('app imported before verified snapshot')
    raise AssertionError('Unexpected dynamic import')
importlib.import_module=forbidden
try:
    import backup_entrypoint
except BackupError:
    assert 'app' not in sys.modules
else:
    raise AssertionError('Expected startup refusal')
'''
        result = self.run_script(script)
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors='replace'))

    def test_local_wsgi_server_boots_and_serves_health_and_protected_snapshot(self):
        port_file = Path(self.tmp.name) / 'port.txt'
        script = r'''
import sys
from pathlib import Path
from werkzeug.serving import make_server
from backup_entrypoint import application
server=make_server('127.0.0.1',0,application,threaded=True)
Path(sys.argv[1]).write_text(str(server.server_port))
server.serve_forever()
'''
        process = subprocess.Popen([sys.executable, '-c', script, str(port_file)], cwd=ROOT,
                                   env=self.environment(), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            deadline = time.monotonic() + 15
            while not port_file.exists() and process.poll() is None and time.monotonic() < deadline:
                time.sleep(0.05)
            self.assertTrue(port_file.exists(), 'WSGI server did not become ready')
            url = 'http://127.0.0.1:' + port_file.read_text()
            with urlopen(url + '/healthz', timeout=5) as response:
                self.assertEqual(response.status, 200)
            headers = {'Authorization': 'Basic ' + base64.b64encode(
                ('admin:' + self.settings.password).encode()).decode(),
                       'X-Backup-Key': self.settings.download_key, 'X-Forwarded-Proto': 'https'}
            with urlopen(Request(url + DOWNLOAD_PATH, headers=headers), timeout=5) as response:
                self.assertEqual(response.status, 200)
                self.assertTrue(response.read().startswith(b'SQLite format 3\x00'))
        finally:
            process.terminate()
            process.communicate(timeout=10)

    @unittest.skipIf(os.name == 'nt', 'Gunicorn requires POSIX; Windows WSGI smoke test runs separately')
    def test_gunicorn_one_worker_four_threads(self):
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 0))
            port = probe.getsockname()[1]
        command = [sys.executable, '-m', 'gunicorn', '--workers', '1', '--threads', '4',
                   '--timeout', '90', '--bind', '127.0.0.1:' + str(port), 'backup_entrypoint:application']
        process = subprocess.Popen(command, cwd=ROOT, env=self.environment(),
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        ready = False
        try:
            deadline = time.monotonic() + 15
            while process.poll() is None and time.monotonic() < deadline:
                try:
                    with urlopen('http://127.0.0.1:%s/healthz' % port, timeout=1) as response:
                        ready = response.status == 200
                    if ready: break
                except OSError:
                    time.sleep(0.05)
            self.assertTrue(ready, 'Gunicorn did not become ready')
        finally:
            process.terminate()
            _, errors = process.communicate(timeout=15)
        self.assertIn(b'gthread', errors)
