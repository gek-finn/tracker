"""Temporary fixed-file HTTPS download, independent of legacy Flask auth."""
import hashlib
import hmac
import time
import unicodedata

from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.wrappers import Request, Response
from werkzeug.wsgi import wrap_file

from backup_snapshot import BackupError

DOWNLOAD_PATH = "/__maintenance/backup"


def _equal(left, right):
    try:
        return hmac.compare_digest(unicodedata.normalize("NFC", left).encode("utf-8"),
                                   unicodedata.normalize("NFC", right).encode("utf-8"))
    except (UnicodeError, TypeError):
        return False


class BackupApplication:
    def __init__(self, legacy_app, settings, snapshot):
        self.legacy_app = legacy_app
        self.settings = settings
        self.snapshot = snapshot
        # Railway terminates TLS at one trusted reverse proxy, as legacy app does.
        # Service must not be exposed via a separate direct public container port.
        self.download_app = ProxyFix(self._download, x_for=0, x_proto=1, x_host=0,
                                     x_port=0, x_prefix=0)

    def __call__(self, environ, start_response):
        if environ.get("PATH_INFO") == DOWNLOAD_PATH:
            return self.download_app(environ, start_response)
        return self.legacy_app(environ, start_response)

    def _download(self, environ, start_response):
        request = Request(environ)
        headers = {"Cache-Control": "private, no-store", "Pragma": "no-cache",
                   "Vary": "Authorization, X-Backup-Key", "X-Content-Type-Options": "nosniff"}

        def reject(status, message, extra=None):
            return Response(message, status=status, headers=dict(headers, **(extra or {})),
                            content_type="text/plain; charset=utf-8")(environ, start_response)

        if not self.settings.enabled or time.time() >= self.snapshot.expires_at:
            return reject(404, "Not found.")
        if request.scheme != "https":
            return reject(403, "HTTPS required.")  # no redirect carrying credentials
        auth = request.authorization
        if (auth is None or auth.type.lower() != "basic" or auth.username != "admin"
                or not _equal(auth.password or "", self.settings.password)):
            return reject(401, "Authentication required.",
                          {"WWW-Authenticate": 'Basic realm="technical-backup", charset="UTF-8"'})
        if not _equal(request.headers.get("X-Backup-Key", ""), self.settings.download_key):
            return reject(403, "Access denied.")
        if request.method not in ("GET", "HEAD"):
            return reject(405, "Method not allowed.", {"Allow": "GET, HEAD"})
        if request.query_string:
            return reject(400, "Query parameters are not accepted.")
        handle = None
        try:
            if self.snapshot.path.is_symlink():
                raise BackupError("Invalid file.")
            # Hash and serve the SAME open descriptor, not two different path opens.
            handle = self.snapshot.path.open("rb")
            digest = hashlib.sha256()
            size = 0
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
                size += len(block)
            if digest.hexdigest() != self.snapshot.sha256 or size != self.snapshot.size:
                raise BackupError("Invalid file.")
            handle.seek(0)
            headers.update({"Content-Disposition": 'attachment; filename="task-tracker-backup.sqlite3"',
                            "X-Backup-SHA256": self.snapshot.sha256,
                            "X-Backup-Job-ID": self.snapshot.job_id,
                            "Content-Length": str(size)})
            response = Response(wrap_file(environ, handle), headers=headers,
                                content_type="application/octet-stream", direct_passthrough=True)
            response.call_on_close(handle.close)
            return response(environ, start_response)
        except (OSError, BackupError):
            if handle is not None:
                handle.close()
            return reject(503, "Verified backup unavailable. No replacement was created.")
