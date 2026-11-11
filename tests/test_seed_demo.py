"""Демонстрационное заполнение: только временные базы."""
from contextlib import closing
from datetime import date, timedelta
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import seed_demo


class SeedDemoTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="tracker_seed_")
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "demo.sqlite3"
        self.today = date(2032, 2, 28)

    def rows(self, table):
        with closing(sqlite3.connect(self.path)) as con, con:
            con.row_factory = sqlite3.Row
            return [dict(row) for row in con.execute("SELECT * FROM " + table)]

    def seed(self):
        seed_demo.seed_demo(self.path, today=self.today)

    def test_content(self):
        self.seed()
        tasks = self.rows("tasks")
        self.assertEqual(len(tasks), 24)
        self.assertEqual(len({t['title'] for t in tasks}), 24)
        self.assertEqual({t['assignee'] for t in tasks}, set(seed_demo.ASSIGNEES))
        self.assertEqual({t['project'] for t in tasks}, {seed_demo.PROJECT})
        self.assertEqual({t['status'] for t in tasks}, {'Новая', 'В работе', 'Завершена'})
        self.assertEqual({t['priority'] for t in tasks}, {'Низкий', 'Средний', 'Высокий'})
        self.assertTrue(all(t['description'] for t in tasks))

    def test_relative_dates(self):
        self.seed()
        tasks = self.rows('tasks')
        for row, spec in zip(tasks, seed_demo.TASKS):
            expected = ((self.today + timedelta(days=spec[-1])).isoformat()
                        if spec[-1] is not None else None)
            self.assertEqual(row['due_date'], expected)
        self.assertTrue(any(t['due_date'] == self.today.isoformat() for t in tasks))

    def test_schedules(self):
        self.seed()
        schedules = self.rows('recurrences')
        self.assertEqual({r['frequency'] for r in schedules}, {'daily', 'weekly', 'monthly'})
        self.assertEqual(len(schedules), 3)
        for rec in schedules:
            start = date.fromisoformat(rec['start_date'])
            self.assertGreater(start, self.today)
            self.assertEqual(rec['next_date'], rec['start_date'])
            self.assertIsNone(rec['last_generated'])
            self.assertEqual((rec['paused'], rec['stopped']), (0, 0))
            if rec['frequency'] == 'weekly':
                self.assertEqual(rec['weekday'], start.weekday())
            if rec['frequency'] == 'monthly':
                self.assertEqual(rec['month_day'], start.day)

    def test_repeat_refuses_without_any_changes(self):
        self.seed()
        before = self.path.read_bytes()
        with self.assertRaisesRegex(ValueError, 'непустая'):
            self.seed()
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(len(self.rows('tasks')), 24)

    def test_nonempty_unknown_table_is_protected(self):
        with closing(sqlite3.connect(self.path)) as con, con:
            con.execute('CREATE TABLE private_data (value TEXT)')
            con.execute("INSERT INTO private_data VALUES ('keep')")
        before = self.path.read_bytes()
        with self.assertRaises(ValueError):
            self.seed()
        self.assertEqual(self.path.read_bytes(), before)

    def test_schedule_only_database_is_protected(self):
        self.seed()
        with closing(sqlite3.connect(self.path)) as con, con:
            con.execute('DELETE FROM tasks')
        before = self.path.read_bytes()
        with self.assertRaises(ValueError):
            self.seed()
        self.assertEqual(self.path.read_bytes(), before)

    def test_empty_existing_schema(self):
        with closing(sqlite3.connect(self.path)) as con, con:
            con.executescript((seed_demo.ROOT / 'schema.sql').read_text(encoding='utf-8'))
        self.seed()
        self.assertEqual(len(self.rows('tasks')), 24)

    def test_zero_byte_file(self):
        self.path.touch()
        self.seed()
        self.assertEqual(len(self.rows('tasks')), 24)

    def test_working_path_rejected_before_connect(self):
        with patch.object(seed_demo.sqlite3, 'connect') as connect:
            with self.assertRaisesRegex(ValueError, 'защищена'):
                seed_demo.seed_demo(seed_demo.ROOT / 'tasks.db')
            connect.assert_not_called()

    def test_rollback_on_insert_failure(self):
        with patch.object(seed_demo, 'TASKS', seed_demo.TASKS + ((None, '', 0, 'Средний', 'Новая', 0),)):
            with self.assertRaises(sqlite3.IntegrityError):
                self.seed()
        with closing(sqlite3.connect(self.path)) as con, con:
            self.assertEqual(con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall(), [])

    def test_cli_requires_explicit_path(self):
        result = subprocess.run([sys.executable, str(seed_demo.ROOT / 'seed_demo.py')],
                                capture_output=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn(b'--db', result.stderr)

    def test_cli_success_and_repeat(self):
        command = [sys.executable, str(seed_demo.ROOT / 'seed_demo.py'), '--db', str(self.path)]
        self.assertEqual(subprocess.run(command, capture_output=True).returncode, 0)
        before = self.path.read_bytes()
        self.assertEqual(subprocess.run(command, capture_output=True).returncode, 1)
        self.assertEqual(self.path.read_bytes(), before)

    def test_application_can_read_and_generate_without_duplicates(self):
        from tests.base import app_module
        import os
        self.seed()
        with patch.dict(os.environ, {'TASK_TRACKER_DB': str(self.path)}):
            with app_module.app.app_context():
                # Создаём экземпляры на текущую дату по штатным правилам.
                with closing(sqlite3.connect(self.path)) as con, con:
                    con.execute('UPDATE recurrences SET start_date=?, next_date=?',
                                (date.today().isoformat(), date.today().isoformat()))
                    con.execute('UPDATE recurrences SET weekday=?, month_day=?',
                                (date.today().weekday(), date.today().day))
                app_module.process_recurrences()
                app_module.process_recurrences()
            self.assertEqual(len(self.rows('tasks')), 27)
            with app_module.app.test_client() as client:
                self.assertEqual(client.get('/').status_code, 200)
                self.assertEqual(client.get('/recurrences').status_code, 200)


if __name__ == '__main__':
    unittest.main()
