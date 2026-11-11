import base64
from dataclasses import replace
import hashlib
from pathlib import Path
import time
import unicodedata
from unittest.mock import patch

from werkzeug.test import Client
from werkzeug.wrappers import Response

from backup_http import BackupApplication, DOWNLOAD_PATH
from backup_snapshot import ensure_snapshot
from tests.technical_support import TechnicalTestCase


class DownloadHTTPTests(TechnicalTestCase):
    def setUp(self):
        super().setUp()
        self.snapshot = ensure_snapshot(self.settings)
        self.legacy_calls = []
        def legacy(environ, start_response):
            self.legacy_calls.append(environ['PATH_INFO'])
            return Response('legacy unchanged')(environ, start_response)
        self.client = Client(BackupApplication(legacy, self.settings, self.snapshot), Response)
        self.auth = {'Authorization': 'Basic ' + base64.b64encode(
            ('admin:' + self.settings.password).encode('utf-8')).decode('ascii'),
                     'X-Backup-Key': self.settings.download_key}

    def get(self, headers=None, path=DOWNLOAD_PATH, method='GET'):
        return self.client.open(path, method=method, base_url='https://example.test', headers=headers or {})

    def test_download_requires_both_factors_and_does_not_touch_source(self):
        before = self.source.read_bytes()
        for headers, expected in (({}, 401), ({'X-Backup-Key': self.settings.download_key}, 401),
                                  ({'Authorization': self.auth['Authorization']}, 403)):
            with self.get(headers) as response:
                self.assertEqual(response.status_code, expected)
                self.assertNotIn(b'SQLite format', response.data)
        with self.get(self.auth) as response:
            self.assertEqual(response.status_code, 200)
            self.assertEqual(hashlib.sha256(response.data).hexdigest(), self.snapshot.sha256)
            self.assertEqual(response.headers['X-Backup-SHA256'], self.snapshot.sha256)
            self.assertEqual(response.headers['Cache-Control'], 'private, no-store')
            self.assertEqual(response.content_type, 'application/octet-stream')
            self.assertIn('attachment;', response.headers['Content-Disposition'])
        self.assertEqual(self.source.read_bytes(), before)
        self.assertEqual(self.legacy_calls, [])

    def test_wrong_unicode_password_key_and_non_basic_auth(self):
        wrong = 'Basic ' + base64.b64encode('admin:Неверный🔑'.encode()).decode()
        for headers in (dict(self.auth, Authorization=wrong),
                        dict(self.auth, Authorization='Digest username="admin", password="unused"'),
                        dict(self.auth, Authorization='Basic ???')):
            with self.get(headers) as response:
                self.assertEqual(response.status_code, 401)
                self.assertIn('UTF-8', response.headers['WWW-Authenticate'])
        with self.get(dict(self.auth, **{'X-Backup-Key': 'bad'})) as response:
            self.assertEqual(response.status_code, 403)

    def test_basic_unicode_nfc_and_ascii(self):
        for password in ('ASCII-test-password', 'Кириллица🔐', 'café'):
            settings = replace(self.settings, password=password)
            client = Client(BackupApplication(lambda e, s: [], settings, self.snapshot), Response)
            auth = dict(self.auth, Authorization='Basic ' + base64.b64encode(
                ('admin:' + unicodedata.normalize('NFD', password)).encode()).decode())
            with client.get(DOWNLOAD_PATH, base_url='https://example.test', headers=auth) as response:
                self.assertEqual(response.status_code, 200)

    def test_http_refused_and_single_trusted_proxy_supported(self):
        with self.client.get(DOWNLOAD_PATH, headers=self.auth) as response:
            self.assertEqual(response.status_code, 403)
        with self.client.get(DOWNLOAD_PATH, headers=dict(self.auth, **{'X-Forwarded-Proto': 'https'})) as response:
            self.assertEqual(response.status_code, 200)

    def test_expired_or_disabled_download_is_404(self):
        with patch('backup_http.time.time', return_value=self.snapshot.expires_at + 1):
            with self.get(self.auth) as response:
                self.assertEqual(response.status_code, 404)
        client = Client(BackupApplication(lambda e, s: [], replace(self.settings, enabled=False), self.snapshot), Response)
        self.assertEqual(client.get(DOWNLOAD_PATH, base_url='https://example.test', headers=self.auth).status_code, 404)

    def test_no_post_options_query_or_client_selected_file(self):
        for method in ('POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS'):
            self.assertEqual(self.get(self.auth, method=method).status_code, 405)
        self.assertEqual(self.get(self.auth, path=DOWNLOAD_PATH + '?path=/data/demo.sqlite3').status_code, 400)

    def test_head_and_repeated_download_return_same_snapshot(self):
        with self.get(self.auth, method='HEAD') as response:
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.data, b'')
        for _ in range(3):
            with self.get(self.auth) as response:
                self.assertEqual(hashlib.sha256(response.data).hexdigest(), self.snapshot.sha256)

    def test_corrupted_or_removed_snapshot_not_recreated_on_http_request(self):
        for payload in (b'damaged', None):
            if payload is None:
                self.snapshot.path.unlink()
            else:
                self.snapshot.path.write_bytes(payload)
            with self.get(self.auth) as response:
                self.assertEqual(response.status_code, 503)
                self.assertNotIn(b'SQLite format', response.data)

    def test_legacy_routes_are_forwarded_unchanged(self):
        self.assertEqual(self.get(path='/').data, b'legacy unchanged')
        self.assertEqual(self.get(path='/healthz').data, b'legacy unchanged')
        self.assertEqual(self.legacy_calls, ['/', '/healthz'])

    def test_secrets_absent_from_all_error_bodies(self):
        for headers in ({}, self.auth, dict(self.auth, **{'X-Backup-Key': 'wrong'})):
            with self.get(headers, path=DOWNLOAD_PATH + '?bad=1') as response:
                self.assertNotIn(self.settings.password.encode(), response.data)
                self.assertNotIn(self.settings.download_key.encode(), response.data)
