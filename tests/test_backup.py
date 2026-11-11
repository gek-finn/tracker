# -*- coding: utf-8 -*-
"""Резервное копирование и восстановление (SQLite Backup API)."""
import sqlite3
from pathlib import Path

import backup as backup_module
from tests.base import TrackerTestCase


class BackupTests(TrackerTestCase):

    def make_source(self):
        task_id = self.insert_task(title="Резервная задача", assignee="Анна")
        self.insert_task(title="Вторая", project="Сайт")
        return task_id

    def test_create_backup(self):
        self.make_source()
        path = backup_module.create_backup(self.db_path, self.tmpdir)
        self.assertTrue(Path(path).exists())
        self.assertIn("tasks_backup_", Path(path).name)

    def test_backup_contains_same_data(self):
        self.make_source()
        path = backup_module.create_backup(self.db_path, self.tmpdir)
        con = sqlite3.connect(path)
        count = con.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
        titles = [r[0] for r in con.execute("SELECT title FROM tasks")]
        con.close()
        self.assertEqual(count, 2)
        self.assertIn("Резервная задача", titles)

    def test_backup_safe_while_connection_open(self):
        # Backup API не блокируется активным подключением к источнику
        self.make_source()
        hold = sqlite3.connect(self.db_path)
        try:
            path = backup_module.create_backup(self.db_path, self.tmpdir)
            self.assertTrue(Path(path).exists())
        finally:
            hold.close()

    def test_backup_missing_source(self):
        with self.assertRaises(FileNotFoundError):
            backup_module.create_backup(Path(self.tmpdir) / "nope.db",
                                        self.tmpdir)

    def _wipe_tasks(self):
        from tests.base import db_connect
        with db_connect(self.db_path) as con:
            con.execute("DELETE FROM tasks")

    def test_restore_replaces_database(self):
        self.make_source()
        path = backup_module.create_backup(self.db_path, self.tmpdir)
        # портим текущую базу
        self._wipe_tasks()
        self.assertEqual(self.count_tasks(), 0)
        # восстанавливаем
        safety = backup_module.restore_backup(path, self.db_path,
                                              assume_stopped=True)
        self.assertEqual(self.count_tasks(), 2)
        self.assertIsNotNone(self.get_task(1))

    def test_restore_creates_safety_copy(self):
        self.make_source()
        path = backup_module.create_backup(self.db_path, self.tmpdir)
        self._wipe_tasks()
        safety = backup_module.restore_backup(path, self.db_path,
                                              assume_stopped=True)
        self.assertIsNotNone(safety)
        self.assertTrue(Path(safety).exists())
        self.assertIn("pre_restore", str(safety))
        # в страховке — состояние ДО восстановления (пустая база)
        con = sqlite3.connect(safety)
        self.assertEqual(con.execute("SELECT COUNT(*) FROM tasks")
                         .fetchone()[0], 0)
        con.close()

    def test_restore_rejects_corrupt_backup(self):
        corrupt = Path(self.tmpdir) / "corrupt.db"
        corrupt.write_bytes(b"this is not a sqlite database")
        with self.assertRaises(sqlite3.DatabaseError):
            backup_module.restore_backup(corrupt, self.db_path,
                                         assume_stopped=True)

    def test_restore_rejects_missing_backup(self):
        with self.assertRaises(FileNotFoundError):
            backup_module.restore_backup(Path(self.tmpdir) / "nope.db",
                                         self.db_path, assume_stopped=True)

    def test_restore_refuses_when_db_busy(self):
        self.make_source()
        path = backup_module.create_backup(self.db_path, self.tmpdir)
        # имитация работающего приложения: другой процесс держит запись
        busy = sqlite3.connect(self.db_path, timeout=0.2)
        busy.execute("BEGIN EXCLUSIVE")
        try:
            with self.assertRaises(RuntimeError):
                backup_module.restore_backup(path, self.db_path)
        finally:
            busy.rollback()
            busy.close()

    def test_backup_cli_backup_command(self):
        self.make_source()
        code = backup_module._main(["backup", "--db", self.db_path,
                                    "--out", self.tmpdir])
        self.assertEqual(code, 0)
