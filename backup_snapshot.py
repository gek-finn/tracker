"""Standalone, crash-safe pre-import SQLite snapshot. Never imports app/db."""
from contextlib import contextmanager, closing
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import tempfile
import time

BASE_REVISION = "9229b82d838a5a93301cc6c5b4361807abea2a14"


class BackupError(RuntimeError):
    """Safe diagnostic: never includes credentials or database contents."""


@dataclass(frozen=True)
class Settings:
    source: Path
    job_id: str
    expires_at: int
    password: str = field(repr=False)
    download_key: str = field(repr=False)
    timeout: float = 30.0
    enabled: bool = True

    @property
    def directory(self):
        return self.source.parent / ".technical-backups" / self.job_id

    @classmethod
    def from_env(cls):
        source = os.environ.get("TASK_TRACKER_DB", "")
        job = os.environ.get("BACKUP_JOB_ID", "")
        password = os.environ.get("TRACKER_ADMIN_PASSWORD", "").strip()
        key = os.environ.get("BACKUP_DOWNLOAD_KEY", "")
        try:
            expires = int(os.environ.get("BACKUP_EXPIRES_AT", ""))
            timeout = float(os.environ.get("BACKUP_TIMEOUT_SECONDS", "30"))
        except ValueError:
            raise BackupError("Invalid backup deadline or timeout.") from None
        if not source or not Path(source).is_absolute():
            raise BackupError("TASK_TRACKER_DB must name an existing absolute SQLite path.")
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", job):
            raise BackupError("BACKUP_JOB_ID must be a fixed safe identifier.")
        if not password or not os.environ.get("TASK_TRACKER_SECRET", "").strip():
            raise BackupError("Administrative password and Flask secret are required.")
        if not re.fullmatch(r"[A-Za-z0-9_-]{43,128}", key):
            raise BackupError("BACKUP_DOWNLOAD_KEY must be a random URL-safe key of at least 43 characters.")
        if not 1 <= timeout <= 30 or expires <= 0:
            raise BackupError("Backup timeout must be 1..30 seconds and expiry must be a UTC epoch.")
        path = Path(source)
        if path.is_symlink():
            raise BackupError("Source symlinks are not supported.")
        return cls(path.resolve(), job, expires, password, key, timeout,
                   os.environ.get("BACKUP_DOWNLOAD_ENABLED", "0") == "1")


@dataclass(frozen=True)
class Snapshot:
    path: Path
    sha256: str
    size: int
    job_id: str
    expires_at: int


def _sync_directory(path):
    if os.name != "nt":
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def _plain_file(path):
    if path.is_symlink() or not path.is_file():
        raise BackupError("Snapshot state is missing, invalid or a symlink; no automatic replacement.")


def _prepare_directory(settings):
    if not settings.source.is_file() or settings.source.is_symlink():
        raise BackupError("Source database is missing; no database will be created.")
    for directory in (settings.directory.parent, settings.directory):
        if directory.is_symlink():
            raise BackupError("Backup directory symlinks are not supported.")
        directory.mkdir(mode=0o700, exist_ok=True)
        if not directory.is_dir():
            raise BackupError("Invalid backup directory.")


@contextmanager
def job_lock(settings):
    """OS-released interprocess lock, bounded waiting; never delete lock files."""
    lock_path = settings.directory / "job.lock"
    if lock_path.is_symlink():
        raise BackupError("Invalid lock file.")
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(fd, "r+b", buffering=0) as handle:
        if os.fstat(handle.fileno()).st_size == 0:
            handle.write(b"0")
        deadline = time.monotonic() + settings.timeout
        while True:
            try:
                if os.name == "nt":
                    import msvcrt
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise BackupError("Timed out waiting for the backup job lock.") from None
                time.sleep(0.05)
        try:
            yield
        finally:
            if os.name == "nt":
                import msvcrt
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _exclusive_json(path, data):
    # Never replace an existing manifest/marker, including incomplete state.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(data, handle, sort_keys=True)
        handle.flush()
        os.fsync(handle.fileno())
    _sync_directory(path.parent)


def _read_json(path):
    _plain_file(path)
    if path.stat().st_size > 16384:
        raise BackupError("Invalid snapshot metadata.")
    with path.open(encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise BackupError("Invalid snapshot metadata.")
    return data


def verify_file(path, timeout=30.0):
    """Only the independent DELETE-mode copy; no recovery or source access."""
    _plain_file(path)
    with path.open("rb") as handle:
        header = handle.read(100)
    if header[:16] != b"SQLite format 3\x00" or header[18:20] != b"\x01\x01":
        raise BackupError("Snapshot is not a standalone rollback-mode SQLite file.")
    if any(Path(str(path) + suffix).exists() for suffix in ("-wal", "-shm", "-journal")):
        raise BackupError("Snapshot has unexpected journal files.")
    deadline = time.monotonic() + timeout
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=1)) as con:
        con.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
        if con.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
            raise BackupError("Snapshot integrity check failed.")
        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {"tasks", "recurrences"} <= tables:
            raise BackupError("Expected Task Tracker tables are missing.")
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            if time.monotonic() > deadline:
                raise BackupError("Snapshot verification timed out.")
            digest.update(block)
            size += len(block)
    return digest.hexdigest(), size


def _remaining(deadline):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise BackupError("Snapshot preparation/verification timed out.")
    return remaining


def _load_snapshot(settings, deadline=None):
    manifest = _read_json(settings.directory / "ready.json")
    if (manifest.get("version") != 1 or manifest.get("job_id") != settings.job_id
            or Path(manifest.get("source", "")) != settings.source
            or manifest.get("base_revision") != BASE_REVISION
            or manifest.get("expires_at") != settings.expires_at):
        raise BackupError("Snapshot metadata does not match this job; no automatic replacement.")
    path = settings.directory / "snapshot.sqlite3"
    sha, size = verify_file(path, _remaining(deadline) if deadline is not None else settings.timeout)
    if sha != manifest.get("sha256") or size != manifest.get("size"):
        raise BackupError("Snapshot checksum mismatch; no automatic replacement.")
    marker = settings.directory / "import-allowed.json"
    if marker.exists():
        if _read_json(marker) != {"job_id": settings.job_id, "sha256": sha}:
            raise BackupError("Invalid import marker; no automatic replacement.")
    return Snapshot(path, sha, size, settings.job_id, settings.expires_at)


def _copy_source(settings, destination, deadline):

    def progress(_status, _remaining, _total):
        if time.monotonic() > deadline:
            raise BackupError("SQLite backup timed out; application was not imported.")

    # No immutable: ordinary SQLite locking and WAL handling remain in effect.
    with closing(sqlite3.connect(settings.source.as_uri() + "?mode=ro", uri=True, timeout=1)) as src:
        with closing(sqlite3.connect(destination, timeout=1)) as dst:
            src.backup(dst, pages=64, progress=progress, sleep=0.05)
            # Change only the COPY, never the source. Delivery needs no WAL files.
            if dst.execute("PRAGMA journal_mode=DELETE").fetchone()[0] != "delete":
                raise BackupError("Could not finalize the standalone backup file.")


def ensure_snapshot(settings):
    """Return a verified durable snapshot, or fail before importing app."""
    pending = None
    deadline = time.monotonic() + settings.timeout
    try:
        _prepare_directory(settings)
        with job_lock(settings):
            manifest = settings.directory / "ready.json"
            final = settings.directory / "snapshot.sqlite3"
            marker = settings.directory / "import-allowed.json"
            if manifest.exists():
                return _load_snapshot(settings, deadline)
            if final.exists() or marker.exists() or any(p.is_symlink() for p in (manifest, final, marker)):
                raise BackupError("Incomplete snapshot state; manual review required, no replacement.")
            if time.time() >= settings.expires_at:
                raise BackupError("Backup job expired before its first snapshot.")
            fd, filename = tempfile.mkstemp(prefix="pending-", suffix=".sqlite3", dir=settings.directory)
            os.close(fd)
            pending = Path(filename)
            _remaining(deadline)
            _copy_source(settings, pending, deadline)
            sha, size = verify_file(pending, _remaining(deadline))
            with pending.open("r+b") as handle:
                os.fsync(handle.fileno())
            # Hard-link publication is atomic and refuses to overwrite a ready file.
            os.link(pending, final)
            pending.unlink()
            pending = None
            _sync_directory(settings.directory)
            _exclusive_json(manifest, {
                "version": 1, "job_id": settings.job_id, "source": str(settings.source),
                "base_revision": BASE_REVISION, "expires_at": settings.expires_at,
                "created_at": datetime.now(timezone.utc).isoformat(), "sha256": sha, "size": size,
            })
            return Snapshot(final, sha, size, settings.job_id, settings.expires_at)
    except BackupError:
        raise
    except (OSError, sqlite3.Error, ValueError, TypeError, KeyError):
        raise BackupError("Backup failed or snapshot state is invalid; application must not start.") from None
    finally:
        # Only this invocation's scratch files; never source/final/unknown files.
        if pending is not None:
            for suffix in ("", "-journal", "-wal", "-shm"):
                try:
                    Path(str(pending) + suffix).unlink(missing_ok=True)
                except OSError:
                    pass


def allow_app_import(settings, snapshot):
    """Durable marker BEFORE import, including imports that later crash."""
    try:
        deadline = time.monotonic() + settings.timeout
        with job_lock(settings):
            current = _load_snapshot(settings, deadline)
            if current != snapshot:
                raise BackupError("Snapshot changed before application import.")
            marker = settings.directory / "import-allowed.json"
            if not marker.exists():
                _exclusive_json(marker, {"job_id": snapshot.job_id, "sha256": snapshot.sha256})
    except BackupError:
        raise
    except (OSError, sqlite3.Error, ValueError, TypeError):
        raise BackupError("Could not verify/persist the pre-import state; application must not start.") from None
