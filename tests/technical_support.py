"""Isolated fixtures. Does not import the legacy application."""
from contextlib import closing
from dataclasses import replace
import os
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest

from backup_snapshot import Settings

ROOT = Path(__file__).resolve().parents[1]


class TechnicalTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="tracker_technical_backup_")
        self.addCleanup(self.tmp.cleanup)
        self.source = Path(self.tmp.name) / "source.sqlite3"
        with closing(sqlite3.connect(self.source)) as con:
            con.executescript((ROOT / "schema.sql").read_text(encoding="utf-8"))
            con.execute("CREATE UNIQUE INDEX idx_tasks_recurrence ON tasks(recurrence_id,recurrence_date)")
            con.executemany("INSERT INTO tasks(title) VALUES (?)", [("Test %s" % n,) for n in range(24)])
            con.commit()
        self.settings = Settings(self.source, "before-update-test", int(time.time()) + 3600,
                                 "Test-Пароль🔑", "K" * 43, timeout=5)

    def environment(self):
        return dict(os.environ, TASK_TRACKER_DB=str(self.source),
                    BACKUP_JOB_ID=self.settings.job_id, BACKUP_EXPIRES_AT=str(self.settings.expires_at),
                    TRACKER_ADMIN_PASSWORD=self.settings.password, BACKUP_DOWNLOAD_KEY=self.settings.download_key,
                    TASK_TRACKER_SECRET="test-technical-secret", TRACKER_ENV="production",
                    BACKUP_DOWNLOAD_ENABLED="1", BACKUP_TIMEOUT_SECONDS="5", PYTHONIOENCODING="utf-8")

    def execute(self, sql):
        with closing(sqlite3.connect(self.source)) as con, con:
            con.execute(sql)

    def count(self, path):
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as con:
            return con.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
