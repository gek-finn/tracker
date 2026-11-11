from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import replace
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time
from unittest.mock import patch

from backup_snapshot import BackupError, Settings, allow_app_import, ensure_snapshot, verify_file
from tests.technical_support import ROOT, TechnicalTestCase


class SnapshotTests(TechnicalTestCase):
    def test_delete_snapshot_preserves_source_and_is_standalone(self):
        before = self.source.read_bytes()
        snapshot = ensure_snapshot(self.settings)
        self.assertEqual(self.count(snapshot.path), 24)
        self.assertEqual(verify_file(snapshot.path), (snapshot.sha256, snapshot.size))
        self.assertEqual(self.source.read_bytes(), before)
        self.assertFalse(any(Path(str(snapshot.path) + suffix).exists() for suffix in ('-wal', '-shm', '-journal')))

    def test_wal_committed_data_is_copied_without_changing_source_or_wal(self):
        with closing(sqlite3.connect(self.source)) as writer:
            writer.execute('PRAGMA journal_mode=WAL')
            writer.execute("INSERT INTO tasks(title) VALUES ('Committed in WAL')")
            writer.commit()
            wal = Path(str(self.source) + '-wal')
            before, wal_before = self.source.read_bytes(), wal.read_bytes()
            snapshot = ensure_snapshot(self.settings)
            self.assertEqual(self.count(snapshot.path), 25)
            self.assertEqual(self.source.read_bytes(), before)
            self.assertEqual(wal.read_bytes(), wal_before)
        with closing(sqlite3.connect(snapshot.path)) as copy:
            self.assertEqual(copy.execute('PRAGMA journal_mode').fetchone()[0], 'delete')

    def test_restart_uses_original_snapshot_after_live_data_changes(self):
        snapshot = ensure_snapshot(self.settings)
        allow_app_import(self.settings, snapshot)
        original = snapshot.path.read_bytes()
        self.execute("INSERT INTO tasks(title) VALUES ('After original snapshot')")
        with patch('backup_snapshot._copy_source', side_effect=AssertionError('must not copy')):
            self.assertEqual(ensure_snapshot(self.settings), snapshot)
            allow_app_import(self.settings, snapshot)
        self.assertEqual(snapshot.path.read_bytes(), original)
        self.assertEqual(self.count(snapshot.path), 24)
        self.assertEqual(self.count(self.source), 25)

    def test_corrupted_ready_copy_is_never_replaced(self):
        snapshot = ensure_snapshot(self.settings)
        allow_app_import(self.settings, snapshot)
        snapshot.path.write_bytes(b'damaged snapshot')
        with self.assertRaises(BackupError):
            ensure_snapshot(self.settings)
        self.assertEqual(snapshot.path.read_bytes(), b'damaged snapshot')

    def test_missing_copy_after_import_marker_refuses_new_capture(self):
        snapshot = ensure_snapshot(self.settings)
        allow_app_import(self.settings, snapshot)
        snapshot.path.unlink()
        (self.settings.directory / 'ready.json').unlink()
        with patch('backup_snapshot._copy_source') as copy:
            with self.assertRaises(BackupError):
                ensure_snapshot(self.settings)
            copy.assert_not_called()

    def test_ready_file_without_manifest_is_preserved(self):
        snapshot = ensure_snapshot(self.settings)
        before = snapshot.path.read_bytes()
        (self.settings.directory / 'ready.json').unlink()
        with self.assertRaises(BackupError):
            ensure_snapshot(self.settings)
        self.assertEqual(snapshot.path.read_bytes(), before)

    def test_invalid_manifest_refuses_repair(self):
        ensure_snapshot(self.settings)
        manifest = self.settings.directory / 'ready.json'
        manifest.write_text('{incomplete', encoding='utf-8')
        with self.assertRaises(BackupError):
            ensure_snapshot(self.settings)
        self.assertEqual(manifest.read_text(encoding='utf-8'), '{incomplete')

    def test_job_deadline_cannot_be_extended_on_restart(self):
        ensure_snapshot(self.settings)
        with self.assertRaises(BackupError):
            ensure_snapshot(replace(self.settings, expires_at=self.settings.expires_at + 3600))

    def test_expired_first_capture_refused_but_ready_copy_survives_expiry(self):
        expired = replace(self.settings, expires_at=int(time.time()) - 1)
        with self.assertRaises(BackupError):
            ensure_snapshot(expired)
        snapshot = ensure_snapshot(self.settings)
        with patch('backup_snapshot.time.time', return_value=self.settings.expires_at + 1):
            self.assertEqual(ensure_snapshot(self.settings), snapshot)

    def test_missing_source_is_not_created(self):
        self.source.unlink()
        with self.assertRaises(BackupError):
            ensure_snapshot(self.settings)
        self.assertFalse(self.source.exists())

    def test_damaged_source_refuses_snapshot(self):
        self.source.write_bytes(b'not sqlite')
        with self.assertRaises(BackupError):
            ensure_snapshot(self.settings)
        self.assertEqual(self.source.read_bytes(), b'not sqlite')
        self.assertFalse((self.settings.directory / 'ready.json').exists())

    def test_copy_error_leaves_no_ready_state_and_retry_is_safe(self):
        with patch('backup_snapshot._copy_source', side_effect=sqlite3.OperationalError('test error')):
            with self.assertRaises(BackupError):
                ensure_snapshot(self.settings)
        self.assertFalse((self.settings.directory / 'ready.json').exists())
        self.assertEqual(list(self.settings.directory.glob('pending-*')), [])
        self.assertEqual(self.count(ensure_snapshot(self.settings).path), 24)

    def test_readonly_source_connection_and_no_immutable(self):
        original = sqlite3.connect
        calls = []
        def connect(path, *args, **kwargs):
            calls.append(str(path))
            return original(path, *args, **kwargs)
        with patch('backup_snapshot.sqlite3.connect', side_effect=connect):
            ensure_snapshot(self.settings)
        self.assertIn(self.source.as_uri() + '?mode=ro', calls)
        self.assertFalse(any('immutable' in path for path in calls))

    def test_parallel_process_startup_publishes_only_one_copy(self):
        script = ('from backup_snapshot import Settings,ensure_snapshot,allow_app_import; '
                  's=Settings.from_env(); b=ensure_snapshot(s); allow_app_import(s,b); print(b.sha256)')
        processes = [subprocess.Popen([sys.executable, '-c', script], cwd=ROOT,
                                     env=self.environment(), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                     for _ in range(2)]
        results = [p.communicate(timeout=20) for p in processes]
        for process, (_, errors) in zip(processes, results):
            self.assertEqual(process.returncode, 0, errors.decode(errors='replace'))
        self.assertEqual(results[0][0], results[1][0])
        self.assertEqual(self.count(ensure_snapshot(self.settings).path), 24)

    def test_concurrent_wal_writer_yields_consistent_snapshot(self):
        with closing(sqlite3.connect(self.source)) as con:
            con.execute('PRAGMA journal_mode=WAL')
        import threading
        started = threading.Event()
        stop = threading.Event()
        def writer():
            with closing(sqlite3.connect(self.source, timeout=5)) as con:
                started.set()
                while not stop.is_set():
                    con.execute("UPDATE tasks SET description='same transaction'")
                    con.commit()
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(writer)
            self.assertTrue(started.wait(5))
            try:
                snapshot = ensure_snapshot(self.settings)
            finally:
                stop.set()
            future.result(timeout=10)
        self.assertEqual(self.count(snapshot.path), 24)
        with closing(sqlite3.connect(snapshot.path)) as con:
            self.assertEqual(con.execute('SELECT COUNT(DISTINCT description) FROM tasks').fetchone()[0], 1)

    def test_exclusive_source_lock_times_out_without_capture(self):
        with closing(sqlite3.connect(self.source)) as con:
            con.execute('BEGIN EXCLUSIVE')
            with self.assertRaises(BackupError):
                ensure_snapshot(replace(self.settings, timeout=0.15))
        self.assertFalse((self.settings.directory / 'ready.json').exists())

    def test_secret_values_are_not_in_manifest_or_settings_repr(self):
        ensure_snapshot(self.settings)
        text = (self.settings.directory / 'ready.json').read_text() + repr(self.settings)
        self.assertNotIn(self.settings.password, text)
        self.assertNotIn(self.settings.download_key, text)

    def test_settings_fail_closed_and_never_echo_secret(self):
        env = self.environment()
        for name in ('TASK_TRACKER_DB', 'BACKUP_JOB_ID', 'BACKUP_DOWNLOAD_KEY',
                     'BACKUP_EXPIRES_AT', 'TRACKER_ADMIN_PASSWORD', 'TASK_TRACKER_SECRET'):
            altered = dict(env)
            altered.pop(name)
            with patch.dict(os.environ, altered, clear=True):
                with self.assertRaises(BackupError) as result:
                    Settings.from_env()
            self.assertNotIn(self.settings.password, str(result.exception))
            self.assertNotIn(self.settings.download_key, str(result.exception))

    def test_concurrent_delete_writer_and_reader(self):
        import threading
        started = threading.Event()
        stop = threading.Event()
        def writer():
            with closing(sqlite3.connect(self.source, timeout=5)) as con:
                while not stop.is_set():
                    con.execute("UPDATE tasks SET description='committed together'")
                    con.commit()
                    started.set()
                    stop.wait(0.002)
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(writer)
            self.assertTrue(started.wait(5))
            try:
                snapshot = ensure_snapshot(self.settings)
            finally:
                stop.set()
            future.result(timeout=10)
        with closing(sqlite3.connect(snapshot.path)) as con:
            self.assertEqual(con.execute('PRAGMA integrity_check').fetchall(), [('ok',)])
            self.assertEqual(con.execute('SELECT COUNT(*) FROM tasks').fetchone()[0], 24)
            self.assertEqual(con.execute('SELECT COUNT(DISTINCT description) FROM tasks').fetchone()[0], 1)

    def test_hot_journal_source_is_not_recovered(self):
        script = (
            "import os,sqlite3; "
            "c=sqlite3.connect(os.environ['TASK_TRACKER_DB']); "
            "c.executemany('INSERT INTO tasks(title,description) VALUES (?,?)', [('temp','x'*2000)]*100); "
            "c.commit(); c.execute('PRAGMA cache_size=1'); "
            "c.execute(\"UPDATE tasks SET description='y'||description\"); os._exit(0)"
        )
        result = subprocess.run([sys.executable, '-c', script], env=self.environment(), cwd=ROOT,
                                capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        journal = Path(str(self.source) + '-journal')
        before, journal_before = self.source.read_bytes(), journal.read_bytes()
        with self.assertRaises(BackupError):
            ensure_snapshot(replace(self.settings, timeout=0.15))
        self.assertEqual(self.source.read_bytes(), before)
        self.assertEqual(journal.read_bytes(), journal_before)

    def test_failure_between_final_file_and_manifest_never_replaces_final(self):
        with patch('backup_snapshot._exclusive_json', side_effect=OSError('storage failure')):
            with self.assertRaises(BackupError):
                ensure_snapshot(self.settings)
        final = self.settings.directory / 'snapshot.sqlite3'
        before = final.read_bytes()
        with self.assertRaises(BackupError):
            ensure_snapshot(self.settings)
        self.assertEqual(final.read_bytes(), before)

    def test_local_restore_rehearsal_only_on_temporary_database(self):
        from backup import restore_backup
        snapshot = ensure_snapshot(self.settings)
        self.execute("INSERT INTO tasks(title) VALUES ('After snapshot')")
        self.assertEqual(self.count(self.source), 25)
        safety = restore_backup(snapshot.path, self.source)
        self.assertEqual(self.count(self.source), 24)
        self.assertEqual(self.count(safety), 25)
        self.assertEqual(self.count(snapshot.path), 24)
