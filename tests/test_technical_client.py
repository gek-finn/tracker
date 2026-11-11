import hashlib
import http.client
import io
from pathlib import Path
import ssl
from unittest.mock import patch

from backup_snapshot import BackupError, ensure_snapshot
from download_backup import download, main
from tests.technical_support import TechnicalTestCase


class FakeResponse:
    def __init__(self, body, status=200, **overrides):
        self.status = status
        self.body = io.BytesIO(body)
        self.headers = {'X-Backup-SHA256': hashlib.sha256(body).hexdigest(),
                        'Content-Length': str(len(body)), 'Content-Type': 'application/octet-stream'}
        self.headers.update(overrides)

    def getheader(self, name, default=''):
        return self.headers.get(name, default)

    def read(self, amount):
        return self.body.read(amount)


class LocalClientTests(TechnicalTestCase):
    def setUp(self):
        super().setUp()
        self.snapshot = ensure_snapshot(self.settings)
        self.output = Path(self.tmp.name) / 'download.sqlite3'

    def transfer(self, response, output=None):
        with patch('download_backup.http.client.HTTPSConnection') as factory:
            factory.return_value.getresponse.return_value = response
            checksum = download('https://example.test', output or self.output,
                                self.settings.password, self.settings.download_key)
            self.assertTrue(factory.call_args.kwargs['context'].check_hostname)
            self.assertEqual(factory.call_args.kwargs['context'].verify_mode, ssl.CERT_REQUIRED)
            factory.return_value.request.assert_called_once()
            self.assertNotIn('?', factory.return_value.request.call_args.args[1])
            return checksum

    def test_download_verifies_hash_integrity_and_publishes_new_file(self):
        checksum = self.transfer(FakeResponse(self.snapshot.path.read_bytes()))
        self.assertEqual(checksum, self.snapshot.sha256)
        self.assertEqual(self.count(self.output), 24)
        self.assertEqual(list(self.output.parent.glob('.backup-download-*')), [])

    def test_existing_file_is_never_overwritten(self):
        self.output.write_bytes(b'keep this file')
        with self.assertRaises(BackupError):
            self.transfer(FakeResponse(self.snapshot.path.read_bytes()))
        self.assertEqual(self.output.read_bytes(), b'keep this file')

    def test_redirects_and_auth_failures_are_not_followed(self):
        for status in (301, 302, 401, 403, 404, 503):
            with self.assertRaisesRegex(BackupError, 'HTTP %s' % status):
                self.transfer(FakeResponse(b'not a backup', status=status))
            self.assertFalse(self.output.exists())

    def test_wrong_hash_wrong_length_or_html_never_published(self):
        for headers in ({'X-Backup-SHA256': '0' * 64}, {'Content-Length': '1'},
                        {'Content-Type': 'text/html'}, {'X-Backup-SHA256': ''}):
            with self.assertRaises(BackupError):
                self.transfer(FakeResponse(self.snapshot.path.read_bytes(), **headers))
            self.assertFalse(self.output.exists())
            self.assertEqual(list(self.output.parent.glob('.backup-download-*')), [])

    def test_corrupt_sqlite_even_with_matching_hash_is_not_published(self):
        with self.assertRaises(BackupError):
            self.transfer(FakeResponse(b'not sqlite'))
        self.assertFalse(self.output.exists())

    def test_interrupted_download_removes_only_its_scratch_file(self):
        response = FakeResponse(self.snapshot.path.read_bytes())
        response.read = lambda _: (_ for _ in ()).throw(http.client.IncompleteRead(b'partial'))
        with self.assertRaises(BackupError):
            self.transfer(response)
        self.assertFalse(self.output.exists())
        self.assertEqual(list(self.output.parent.glob('.backup-download-*')), [])

    def test_no_credentials_or_key_in_url_and_https_required(self):
        for url in ('http://example.test', 'https://admin:password@example.test',
                    'https://example.test?key=wrong', 'https://example.test/#fragment',
                    'https://example.test/arbitrary-path'):
            with patch('download_backup.http.client.HTTPSConnection') as connect:
                with self.assertRaises(BackupError):
                    download(url, self.output, self.settings.password, self.settings.download_key)
                connect.assert_not_called()

    def test_missing_destination_directory_has_safe_error(self):
        with self.assertRaises(BackupError):
            self.transfer(FakeResponse(self.snapshot.path.read_bytes()),
                          Path(self.tmp.name) / 'absent' / 'download.sqlite3')

    def test_cli_requires_interactive_secret_input(self):
        with patch('download_backup.sys.stdin.isatty', return_value=False), \
                patch('download_backup.getpass.getpass') as prompt, \
                patch('download_backup.sys.stderr', new_callable=io.StringIO) as errors:
            self.assertEqual(main(['https://example.test', str(self.output)]), 1)
            prompt.assert_not_called()
            self.assertNotIn(self.settings.password, errors.getvalue())

    def test_destination_race_does_not_replace_other_file(self):
        real_link = __import__('os').link
        def race(source, destination):
            Path(destination).write_bytes(b'other creator')
            return real_link(source, destination)
        with patch('download_backup.os.link', side_effect=race):
            with self.assertRaises(BackupError):
                self.transfer(FakeResponse(self.snapshot.path.read_bytes()))
        self.assertEqual(self.output.read_bytes(), b'other creator')

    def test_tls_certificate_failure_is_not_bypassed(self):
        with patch('download_backup.http.client.HTTPSConnection') as connect:
            connect.return_value.request.side_effect = ssl.SSLCertVerificationError('untrusted')
            with self.assertRaises(BackupError):
                download('https://example.test', self.output, self.settings.password, self.settings.download_key)
        self.assertFalse(self.output.exists())
