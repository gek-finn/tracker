"""Local HTTPS downloader: credentials are prompted, never passed in URL/argv."""
import argparse
from contextlib import closing
import getpass
import hashlib
import http.client
import os
from pathlib import Path
import re
import sqlite3
import ssl
import sys
import tempfile
import base64
import unicodedata
from urllib.parse import urlsplit

from backup_http import DOWNLOAD_PATH
from backup_snapshot import BackupError, verify_file


def download(url, destination, password, key):
    parsed = urlsplit(url)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username is not None
            or parsed.password is not None or parsed.query or parsed.fragment
            or parsed.path not in ("", "/", DOWNLOAD_PATH)):
        raise BackupError("Use an HTTPS service URL without credentials, query or fragment.")
    if not password or not re.fullmatch(r"[A-Za-z0-9_-]{43,128}", key):
        raise BackupError("Password and a valid backup key are required.")
    destination = Path(destination).absolute()
    if destination.exists() or destination.is_symlink():
        raise BackupError("Destination exists; refusing to overwrite it.")
    fd = None
    pending = None
    connection = None
    try:
        fd, filename = tempfile.mkstemp(prefix=".backup-download-", suffix=".sqlite3", dir=destination.parent)
        pending = Path(filename)
        credentials = base64.b64encode(("admin:" + unicodedata.normalize("NFC", password)).encode("utf-8"))
        connection = http.client.HTTPSConnection(parsed.hostname, parsed.port or 443,
                                                timeout=60, context=ssl.create_default_context())
        connection.request("GET", DOWNLOAD_PATH, headers={
            "Authorization": "Basic " + credentials.decode("ascii"), "X-Backup-Key": key,
            "Accept": "application/octet-stream", "Accept-Encoding": "identity",
        })
        response = connection.getresponse()
        if response.status != 200:
            # Never follow redirects or echo server bodies, URLs or credentials.
            raise BackupError("Download refused (HTTP %s); no local backup published." % response.status)
        expected = response.getheader("X-Backup-SHA256", "")
        length = response.getheader("Content-Length", "")
        if not re.fullmatch(r"[a-f0-9]{64}", expected) or not length.isdecimal():
            raise BackupError("Missing or invalid integrity metadata.")
        if response.getheader("Content-Type", "").split(";")[0] != "application/octet-stream":
            raise BackupError("Unexpected download type.")
        total = 0
        digest = hashlib.sha256()
        with os.fdopen(fd, "wb") as handle:
            fd = None
            while True:
                block = response.read(1024 * 1024)
                if not block:
                    break
                total += len(block)
                if total > 64 * 1024 * 1024:
                    raise BackupError("Backup exceeds this demo client's 64 MiB safety limit.")
                digest.update(block)
                handle.write(block)
            handle.flush()
            os.fsync(handle.fileno())
        if total != int(length) or digest.hexdigest() != expected:
            raise BackupError("Incomplete download or checksum mismatch.")
        if verify_file(pending)[0] != expected:
            raise BackupError("Local integrity check failed.")
        with closing(sqlite3.connect(pending.as_uri() + "?mode=ro", uri=True)) as con:
            tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if not {"tasks", "recurrences"} <= tables:
                raise BackupError("Expected Task Tracker tables are missing.")
        os.link(pending, destination)  # race-safe no-clobber publication
        return expected
    except BackupError:
        raise
    except (OSError, sqlite3.Error, http.client.HTTPException, ValueError):
        raise BackupError("Download or local verification failed; destination was not replaced.") from None
    finally:
        if fd is not None:
            os.close(fd)
        if connection is not None:
            connection.close()
        if pending is not None:
            pending.unlink(missing_ok=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url", help="HTTPS service URL, no credentials")
    parser.add_argument("output", help="New local SQLite filename")
    args = parser.parse_args(argv)
    try:
        if not sys.stdin.isatty():
            raise BackupError("Use an interactive terminal for hidden credential entry.")
        password = getpass.getpass("Administrator password: ")
        key = getpass.getpass("Temporary backup key: ")
        checksum = download(args.url, args.output, password, key)
        print("Backup downloaded and integrity verified. SHA-256: " + checksum)
        return 0
    except (BackupError, EOFError, KeyboardInterrupt) as exc:
        print(str(exc) if isinstance(exc, BackupError) else "Download cancelled.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
