# -*- coding: utf-8 -*-
"""Резервное копирование и восстановление базы Task Tracker.

Копирование — через SQLite Backup API (con.backup): безопасно даже при
работающем приложении, база копируется согласованно.

Восстановление — только при остановленном приложении: перед заменой
проверяется целостность копии, делается автоматическая страховочная копия
текущей базы (backups/pre_restore/) и запрашивается подтверждение.

CLI:
    python backup.py backup  [--db PATH] [--out DIR]
    python backup.py restore ФАЙЛ [--db PATH] [--yes]

Примеры:
    python backup.py backup
    python backup.py restore backups/tasks_backup_2026-11-11_10-00-00.db
"""
import argparse
import os
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent


def get_db_path():
    """Путь к базе (той же логикой, что и db.py)."""
    return Path(os.environ.get("TASK_TRACKER_DB", BASE_DIR / "tasks.db"))


def _check_integrity(path):
    """PRAGMA integrity_check; True, если база цела."""
    con = sqlite3.connect(path)
    try:
        return con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        con.close()


def create_backup(db_path=None, backup_dir=None):
    """Создать резервную копию базы. Возвращает путь к копии.

    Используется SQLite Backup API: копия согласована даже при активных
    записях другого процесса. После копии проверяется целостность файла.
    """
    db_path = Path(db_path or get_db_path())
    if not db_path.exists():
        raise FileNotFoundError("База не найдена: %s" % db_path)
    backup_dir = Path(backup_dir or db_path.parent / "backups")
    backup_dir.mkdir(parents=True, exist_ok=True)

    dest = backup_dir / ("tasks_backup_%s.db"
                         % datetime.now().strftime("%Y-%m-%d_%H-%M-%S"))
    src = sqlite3.connect(db_path)
    dst = sqlite3.connect(dest)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()

    if not _check_integrity(dest):
        dest.unlink(missing_ok=True)
        raise sqlite3.DatabaseError(
            "Копия %s не прошла проверку целостности и удалена" % dest)
    return dest


def _db_is_idle(db_path):
    """True, если базу никто не держит на запись (приложение остановлено).

    Получить EXCLUSIVE-блокировку можно только когда нет активных
    подключений с незавершёнными транзакциями.
    """
    con = sqlite3.connect(db_path, timeout=0.2)
    try:
        con.execute("BEGIN EXCLUSIVE")
        con.execute("COMMIT")
        return True
    except sqlite3.OperationalError:
        return False
    finally:
        con.close()


def restore_backup(backup_path, db_path=None, assume_stopped=False):
    """Восстановить базу из копии. Возвращает путь к страховочной копии.

    Порядок (безопасность данных прежде всего):
      1. копия должна существовать и проходить проверку целостности;
      2. база должна быть свободна (приложение остановлено), если только
         не передан assume_stopped=True (используется в тестах);
      3. текущая база автоматически копируется в backups/pre_restore/;
      4. замена выполняется атомарно (os.replace), старые -wal/-shm
         файлы удаляются.
    """
    backup_path = Path(backup_path)
    db_path = Path(db_path or get_db_path())

    if not backup_path.exists():
        raise FileNotFoundError("Файл копии не найден: %s" % backup_path)
    if not _check_integrity(backup_path):
        raise sqlite3.DatabaseError(
            "Копия %s повреждена, восстановление отменено" % backup_path)

    safety_copy = None
    if db_path.exists():
        if not assume_stopped and not _db_is_idle(db_path):
            raise RuntimeError(
                "База %s занята другим процессом. Остановите приложение "
                "и повторите восстановление." % db_path)
        safety_copy = create_backup(
            db_path, backup_dir=db_path.parent / "backups" / "pre_restore")

    tmp = db_path.with_name(db_path.name + ".restore.tmp")
    src = sqlite3.connect(backup_path)
    dst = sqlite3.connect(tmp)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    if not _check_integrity(tmp):
        tmp.unlink(missing_ok=True)
        raise sqlite3.DatabaseError("Восстановление прервано: копия "
                                    "повреждена")
    # Windows не заменяет файл, пока любой процесс держит его открытым —
    # делаем несколько коротких попыток, затем объясняем причину
    for attempt in range(5):
        try:
            os.replace(tmp, db_path)
            break
        except PermissionError:
            if attempt == 4:
                tmp.unlink(missing_ok=True)
                raise RuntimeError(
                    "Не удалось заменить файл базы: он открыт другим "
                    "процессом. Остановите приложение и повторите.")
            time.sleep(0.2)
    # старые journal/wal файлы от прежней базы недействительны
    for suffix in ("-journal", "-wal", "-shm"):
        Path(str(db_path) + suffix).unlink(missing_ok=True)
    return safety_copy


def _main(argv=None):
    parser = argparse.ArgumentParser(
        description="Резервное копирование базы Task Tracker")
    sub = parser.add_subparsers(dest="command", required=True)

    p_backup = sub.add_parser("backup", help="создать резервную копию")
    p_backup.add_argument("--db", default=None, help="путь к базе")
    p_backup.add_argument("--out", default=None, help="каталог для копий")

    p_restore = sub.add_parser("restore", help="восстановить базу из копии")
    p_restore.add_argument("backup", help="файл копии")
    p_restore.add_argument("--db", default=None, help="путь к базе")
    p_restore.add_argument("--yes", action="store_true",
                           help="не спрашивать подтверждение")

    args = parser.parse_args(argv)

    if args.command == "backup":
        path = create_backup(args.db, args.out)
        print("OK: %s" % path)
        print("Integrity check passed.")
        return 0

    # restore: подтверждение перед заменой
    print("Restore will REPLACE the current database with:")
    print("  %s" % args.backup)
    print("Make sure the application is stopped.")
    if not args.yes:
        answer = input("Type YES to continue: ").strip()
        if answer != "YES":
            print("Cancelled.")
            return 1
    try:
        safety = restore_backup(args.backup, args.db)
    except (FileNotFoundError, RuntimeError, sqlite3.DatabaseError) as exc:
        print("ERROR: %s" % exc)
        return 2
    if safety:
        print("Safety copy of the previous database: %s" % safety)
    print("OK: database restored from %s" % args.backup)
    return 0


if __name__ == "__main__":
    sys.exit(_main())
