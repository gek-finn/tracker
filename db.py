"""Работа с базой данных SQLite."""
import os
import sqlite3
from pathlib import Path

from flask import g

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_DB_PATH = BASE_DIR / "tasks.db"
SCHEMA_PATH = BASE_DIR / "schema.sql"


def get_db_path():
    """Путь к файлу БД. Можно переопределить переменной окружения TASK_TRACKER_DB."""
    return Path(os.environ.get("TASK_TRACKER_DB", DEFAULT_DB_PATH))


def get_db():
    """Возвращает соединение с БД — одно на каждый запрос."""
    if "db" not in g:
        g.db = sqlite3.connect(get_db_path(), timeout=10)
        g.db.row_factory = sqlite3.Row
    return g.db


def close_db(_exc=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def _add_missing_columns(con, table, columns):
    """Добавляет недостающие столбцы в существующую таблицу (миграция без потерь)."""
    present = {row[1] for row in con.execute("PRAGMA table_info(" + table + ")")}
    for name, ddl in columns:
        if name not in present:
            con.execute("ALTER TABLE " + table + " ADD COLUMN " + name + " " + ddl)


def init_db():
    """Создаёт таблицы и индексы, если их ещё нет. Существующие данные не трогает.

    Миграция этапа 5: в таблицу tasks, созданную раньше, добавляются столбцы
    recurrence_id и recurrence_date; таблица recurrences создаётся с нуля.
    """
    with open(SCHEMA_PATH, encoding="utf-8") as f:
        schema = f.read()
    con = sqlite3.connect(get_db_path(), timeout=10)
    try:
        con.executescript(schema)
        # Для баз, созданных до этапа 5: добавляем недостающие столбцы
        _add_missing_columns(con, "tasks", [
            ("recurrence_id", "INTEGER REFERENCES recurrences(id)"),
            ("recurrence_date", "TEXT"),
        ])
        # Уникальный индекс против дублей экземпляров — строго после столбцов
        con.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_tasks_recurrence "
                    "ON tasks(recurrence_id, recurrence_date)")
        con.commit()
    finally:
        con.close()
