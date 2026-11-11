"""Gunicorn target: backup_entrypoint:application. Snapshot BEFORE app import."""
import importlib

from backup_http import BackupApplication
from backup_snapshot import Settings, allow_app_import, ensure_snapshot


def create_application(settings=None, importer=None):
    settings = settings or Settings.from_env()
    snapshot = ensure_snapshot(settings)
    allow_app_import(settings, snapshot)
    legacy = (importer or importlib.import_module)("app")
    return BackupApplication(legacy.app, settings, snapshot)


application = create_application()
