# -*- coding: utf-8 -*-
"""База тестов Task Tracker.

Важно: переменную окружения TASK_TRACKER_DB выставляем ДО первого импорта
app — импорт создаёт схему БД и запускает генерацию повторяющихся задач,
и без этого коснулся бы рабочей tasks.db. Каждый тест работает на своей
временной SQLite в отдельном каталоге; рабочая база не открывается.
"""
import itertools
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from contextlib import contextmanager

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


def _pick_tmp_base():
    """Каталог для временных баз.

    Системный TEMP (обычно C:) может быть заполнен — в этом случае используем
    .test_tmp рядом с проектом.
    """
    try:
        probe = os.path.join(tempfile.gettempdir(), "tracker_probe_%d" % os.getpid())
        with open(probe, "w") as f:
            f.write("ok")
        os.remove(probe)
        return tempfile.gettempdir()
    except OSError:
        base = os.path.join(PROJECT_ROOT, ".test_tmp")
        os.makedirs(base, exist_ok=True)
        return base


TMP_BASE = _pick_tmp_base()

# Загрузочный каталог: единственный импорт app выполняет init_db здесь,
# а не в рабочей базе.
_BOOTSTRAP_DIR = tempfile.mkdtemp(prefix="tracker_bootstrap_", dir=TMP_BASE)
os.environ["TASK_TRACKER_DB"] = os.path.join(_BOOTSTRAP_DIR, "bootstrap.db")

import app as app_module  # noqa: E402
import db as database     # noqa: E402

os.environ.pop("TASK_TRACKER_DB", None)

import atexit  # noqa: E402

atexit.register(lambda: shutil.rmtree(_BOOTSTRAP_DIR, ignore_errors=True))

_TOKENS = itertools.count(1)


@contextmanager
def db_connect(path):
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    try:
        yield con
        con.commit()
    finally:
        con.close()


class TrackerTestCase(unittest.TestCase):
    """Один тест — чистая временная база. Рабочая tasks.db не затрагивается."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="tracker_test_", dir=TMP_BASE)
        self.db_path = os.path.join(self.tmpdir, "tasks.db")
        os.environ["TASK_TRACKER_DB"] = self.db_path
        database.init_db()
        app_module.app.config["TESTING"] = True
        self.client = app_module.app.test_client()

    def tearDown(self):
        os.environ.pop("TASK_TRACKER_DB", None)
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    # --- Вспомогательные методы ---

    def next_token(self):
        """Уникальный form_token (защита от дублей в приложении)."""
        return "token-%d" % next(_TOKENS)

    def insert_task(self, title="Задача", description="", assignee="",
                    project="", priority="Средний", status="Новая",
                    due_date=None):
        with db_connect(self.db_path) as con:
            cur = con.execute(
                "INSERT INTO tasks (title, description, assignee, project, "
                "priority, status, due_date) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (title, description, assignee, project, priority, status,
                 due_date))
            return cur.lastrowid

    def insert_recurrence(self, title="Повтор", frequency="daily", weekday=None,
                          month_day=None, start_date=None, end_date=None,
                          paused=0, stopped=0, last_generated=None,
                          next_date=None):
        with db_connect(self.db_path) as con:
            cur = con.execute(
                "INSERT INTO recurrences (title, description, assignee, "
                "project, priority, frequency, weekday, month_day, start_date, "
                "end_date, paused, stopped, last_generated, next_date) "
                "VALUES (?, '', '', '', 'Средний', ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (title, frequency, weekday, month_day, start_date, end_date,
                 paused, stopped, last_generated, next_date))
            return cur.lastrowid

    def get_task(self, task_id):
        with db_connect(self.db_path) as con:
            row = con.execute("SELECT * FROM tasks WHERE id = ?",
                              (task_id,)).fetchone()
            return dict(row) if row else None

    def get_recurrence(self, rec_id):
        with db_connect(self.db_path) as con:
            row = con.execute("SELECT * FROM recurrences WHERE id = ?",
                              (rec_id,)).fetchone()
            return dict(row) if row else None

    def count_tasks(self, where="1=1", params=()):
        with db_connect(self.db_path) as con:
            return con.execute("SELECT COUNT(*) FROM tasks WHERE " + where,
                               params).fetchone()[0]

    def task_dates(self, rec_id):
        with db_connect(self.db_path) as con:
            return [r[0] for r in con.execute(
                "SELECT recurrence_date FROM tasks WHERE recurrence_id = ? "
                "ORDER BY recurrence_date", (rec_id,))]

    def post_new_task(self, **fields):
        data = {"form_token": self.next_token(),
                "priority": "Средний", "status": "Новая"}
        data.update(fields)
        return self.client.post("/task/new", data=data, follow_redirects=True)

    def post_edit_task(self, task_id, **fields):
        data = {"form_token": self.next_token(),
                "priority": "Средний", "status": "Новая"}
        data.update(fields)
        return self.client.post("/task/%d/edit" % task_id, data=data,
                                follow_redirects=True)
