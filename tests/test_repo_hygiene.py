# -*- coding: utf-8 -*-
"""Гигиена репозитория: секреты и рабочие базы не должны публиковаться."""
import os
import re
from pathlib import Path

from tests.base import PROJECT_ROOT, TrackerTestCase

# Значения .env.example, допустимые в публичном репозитории (плейсхолдеры)
PLACEHOLDER_VALUES = {
    "change-me-to-a-long-random-string", "set-a-strong-password",
    "tasks.db", "0", "1", "5000", "127.0.0.1", "local",
}


class RepoHygieneTests(TrackerTestCase):
    """Проверки подготовленных к публикации файлов."""

    def test_gitignore_covers_sensitive_paths(self):
        gitignore = (Path(PROJECT_ROOT) / ".gitignore").read_text(
            encoding="utf-8")
        for pattern in ["tasks.db", ".env", "!.env.example", ".venv/",
                        "__pycache__/", "backups/", ".test_tmp/"]:
            self.assertIn(pattern, gitignore, pattern)

    def test_env_example_exists_and_has_no_real_secrets(self):
        path = Path(PROJECT_ROOT) / ".env.example"
        self.assertTrue(path.exists())
        for line in path.read_text(encoding="utf-8").splitlines():
            m = re.match(r"^([A-Z_]+)=(.*)$", line.strip())
            if not m:
                continue
            key, value = m.groups()
            self.assertIn(value, PLACEHOLDER_VALUES,
                          "Подозрительное значение в .env.example: %s" % key)

    def test_no_hardcoded_secret_in_source(self):
        # старый «зашитый» ключ и дефолтные пароли в коде не используются
        patterns = [r"task-tracker-dev", r"TRACKER_ADMIN_PASSWORD\s*=\s*[\"'][^\"']+[\"']"]
        for name in ["app.py", "backup.py", "db.py", "main.js", "kanban.js",
                     "task-view.js"]:
            path = Path(PROJECT_ROOT) / name
            if not path.exists():
                continue
            text = path.read_text(encoding="utf-8")
            for pattern in patterns:
                self.assertIsNone(
                    re.search(pattern, text),
                    "%s содержит %r" % (name, pattern))

    def test_procfile_for_railway(self):
        path = Path(PROJECT_ROOT) / "Procfile"
        self.assertTrue(path.exists())
        text = path.read_text(encoding="utf-8")
        self.assertIn("gunicorn", text)
        self.assertIn("$PORT", text)
        self.assertIn("app:app", text)

    def test_license_present(self):
        text = (Path(PROJECT_ROOT) / "LICENSE").read_text(encoding="utf-8")
        self.assertIn("MIT License", text)

    def test_requirements_pinned(self):
        text = (Path(PROJECT_ROOT) / "requirements.txt").read_text(
            encoding="utf-8")
        for line in text.splitlines():
            if line.strip() and not line.startswith("#"):
                self.assertIn("==", line, "Зависимость не зафиксана: %s" % line)

    def test_no_databases_in_code_folders(self):
        # в каталогах кода/документации не должно лежать файлов баз
        # (рабочая tasks.db в корне существует, но исключена .gitignore)
        folders = [os.path.join(PROJECT_ROOT, "tests"),
                   os.path.join(PROJECT_ROOT, "docs"),
                   os.path.join(PROJECT_ROOT, "static"),
                   os.path.join(PROJECT_ROOT, "templates")]
        for folder in folders:
            if not os.path.isdir(folder):
                continue
            for name in os.listdir(folder):
                self.assertFalse(
                    name.endswith((".db", ".sqlite", ".sqlite3")),
                    "Файл базы в публикуемом каталоге: %s/%s"
                    % (folder, name))
