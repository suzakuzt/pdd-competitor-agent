"""Only synthetic temporary SQLite assets; no network, server, or real store."""
import base64
from contextlib import closing
import hashlib
import io
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch

from pdd_monitor import dashboard_images as images


PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jh3sAAAAASUVORK5CYII=')


class Handler:
    def __init__(self, headers=None):
        self.headers = headers or {}
        self.response_headers = {}
        self.wfile = io.BytesIO()
        self.status = None
        self.ended = False

    def send_response(self, status):
        self.status = status

    def send_header(self, key, value):
        self.response_headers[key] = value

    def end_headers(self):
        self.ended = True


class DashboardImageTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='pdd_image_reader_SYNTHETIC_')
        self.addCleanup(temporary.cleanup)
        self.data = Path(temporary.name)
        self.database = self.data / 'images.sqlite3'
        with closing(sqlite3.connect(self.database)) as database:
            database.execute('''CREATE TABLE assets (
                sha256 TEXT PRIMARY KEY, mime TEXT NOT NULL,
                byte_count INTEGER NOT NULL CHECK(byte_count > 0), data BLOB NOT NULL,
                created_at TEXT NOT NULL, CHECK(length(data) = byte_count))''')
            database.commit()
        self.sha = self.insert(PNG)
        self.reader = images.ImageReader(self.data)

    def insert(self, data, mime='image/png'):
        sha = hashlib.sha256(data).hexdigest()
        with closing(sqlite3.connect(self.database)) as database:
            database.execute('INSERT INTO assets VALUES(?,?,?,?,?)', (sha, mime, len(data), data, 'synthetic'))
            database.commit()
        return sha

    def corrupt(self, sql, values=()):
        before = self.database.stat().st_mtime_ns
        with closing(sqlite3.connect(self.database)) as database:
            database.execute('PRAGMA ignore_check_constraints=ON')
            database.execute(sql, values)
            database.commit()
        # Guarantee a distinguishable stat on platforms with coarse filesystem clocks.
        info = self.database.stat()
        os.utime(self.database, ns=(info.st_atime_ns, max(info.st_mtime_ns, before + 1_000_000)))

    def assert_error(self, status, sha=None, reader=None):
        with self.assertRaises(images.ImageReadError) as raised:
            (reader or self.reader).read(self.sha if sha is None else sha)
        self.assertEqual(raised.exception.status, status)
        self.assertNotIn(str(self.data), str(raised.exception))

    def test_verified_asset_uses_readonly_query_and_changes_no_files(self):
        before = self.database.read_bytes()
        connect = sqlite3.connect
        opened = []
        def readonly(filename, **kwargs):
            self.assertTrue(filename.endswith('?mode=ro'))
            self.assertTrue(kwargs['uri'])
            connection = connect(filename, **kwargs)
            opened.append(filename)
            return connection
        with patch.object(images.sqlite3, 'connect', side_effect=readonly):
            asset = self.reader.read(self.sha)
        self.assertEqual((asset.data, asset.mime, asset.sha256, asset.etag),
                         (PNG, 'image/png', self.sha, '"' + self.sha + '"'))
        self.assertEqual(len(opened), 1)
        self.assertEqual(before, self.database.read_bytes())
        self.assertEqual([path.name for path in self.data.iterdir()], ['images.sqlite3'])

    def test_invalid_sha_rejected_before_storage_access(self):
        for sha in ('', '../images.sqlite3', 'A' * 64, 'g' * 64, 'a' * 63, 'a' * 65, 'a' * 64 + '\n', 123):
            with self.subTest(sha=sha), patch.object(images.sqlite3, 'connect', side_effect=AssertionError('must not open')):
                self.assert_error(400, sha)

    def test_missing_image_is_404(self):
        self.assert_error(404, '0' * 64)

    def test_missing_database_is_503_and_never_created(self):
        missing = self.data / 'absent'
        self.assert_error(503, reader=images.ImageReader(missing))
        self.assertFalse(missing.exists())

    def test_invalid_database_and_missing_schema_are_safe_503(self):
        self.database.write_bytes(b'SYNTHETIC NOT SQLITE')
        self.assert_error(503)
        self.database.unlink()
        with closing(sqlite3.connect(self.database)) as database:
            database.execute('CREATE TABLE unrelated(value)')
        self.assert_error(503)

    def test_bad_blob_hash_length_mime_or_storage_type_is_rejected(self):
        original = self.database.read_bytes()
        cases = [
            ('UPDATE assets SET data=?', (bytes(len(PNG)),)),
            ('UPDATE assets SET byte_count=1', ()),
            ('UPDATE assets SET mime=?', ('image/jpeg',)),
            ('UPDATE assets SET data=?,byte_count=3', ('abc',)),
        ]
        for sql, values in cases:
            with self.subTest(sql=sql):
                self.database.write_bytes(original)
                self.corrupt(sql, values)
                self.assert_error(503)

    def test_unrecognized_image_signature_is_rejected_even_with_correct_hash(self):
        sha = self.insert(b'<html>NOT AN IMAGE</html>', mime='image/png')
        self.assert_error(503, sha)

    def test_supported_raster_signatures_use_the_original_mime_validator(self):
        for data, mime in ((b'\xff\xd8\xffsynthetic', 'image/jpeg'),
                           (b'GIF89asynthetic', 'image/gif'),
                           (b'RIFF0000WEBPsynthetic', 'image/webp')):
            with self.subTest(mime=mime):
                sha = self.insert(data, mime)
                asset = self.reader.read(sha)
                self.assertEqual((asset.data, asset.mime), (data, mime))

    def test_cache_reuses_verified_bytes_and_database_change_invalidates(self):
        first = self.reader.read(self.sha)
        with patch.object(images.sqlite3, 'connect', side_effect=AssertionError('cache hit should not query')):
            self.assertIs(first, self.reader.read(self.sha))
        self.corrupt('UPDATE assets SET data=?', (bytes(len(PNG)),))
        self.assert_error(503)
        self.assertEqual(self.reader._cache_bytes, 0)

    def test_deleted_asset_cannot_survive_database_cache_invalidation(self):
        self.reader.read(self.sha)
        self.corrupt('DELETE FROM assets')
        self.assert_error(404)

    def test_sidecar_change_invalidates_cache(self):
        before = self.reader._stamp()
        sidecar = Path(str(self.database) + '-wal')
        sidecar.write_bytes(b'SYNTHETIC STAMP ONLY')
        self.assertNotEqual(before, self.reader._stamp())
        sidecar.unlink()

    def test_change_during_read_is_503_and_not_cached(self):
        stamp = self.reader._stamp()
        with patch.object(self.reader, '_stamp', side_effect=[stamp, ('changed',)]):
            self.assert_error(503)
        self.assertEqual(self.reader._cache_bytes, 0)

    def test_cache_is_byte_bounded_and_least_recently_used(self):
        second = self.insert(PNG + b'2')
        third = self.insert(PNG + b'3')
        reader = images.ImageReader(self.data, max_cache_bytes=len(PNG) * 2 + 1)
        reader.read(self.sha)
        reader.read(second)
        reader.read(self.sha)
        reader.read(third)
        self.assertEqual(list(reader._cache), [self.sha, third])
        self.assertLessEqual(reader._cache_bytes, reader.max_cache_bytes)
        small = images.ImageReader(self.data, max_cache_bytes=1)
        self.assertEqual(small.read(self.sha).data, PNG)
        self.assertEqual(small._cache_bytes, 0)

    def test_concurrent_reads_share_one_verified_cache_entry(self):
        barrier = threading.Barrier(6)
        results, failures = [], []
        def read():
            try:
                barrier.wait(timeout=5)
                results.append(self.reader.read(self.sha))
            except BaseException as error:
                failures.append(error)
        with patch.object(images.sqlite3, 'connect', wraps=sqlite3.connect) as connect:
            threads = [threading.Thread(target=read) for _ in range(6)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(5)
            self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertEqual(failures, [])
        self.assertEqual(len(results), 6)
        self.assertTrue(all(asset is results[0] for asset in results))
        self.assertEqual(connect.call_count, 1)

    def test_get_and_head_have_matching_safe_cache_headers(self):
        for head_only in (False, True):
            with self.subTest(head_only=head_only):
                handler = Handler()
                self.assertEqual(images.serve_image(handler, self.reader, self.sha, head_only), 200)
                self.assertEqual(handler.response_headers['Content-Type'], 'image/png')
                self.assertEqual(handler.response_headers['Content-Length'], str(len(PNG)))
                self.assertEqual(handler.response_headers['ETag'], '"' + self.sha + '"')
                self.assertEqual(handler.response_headers['Cache-Control'], 'private, max-age=31536000, immutable')
                self.assertEqual(handler.response_headers['X-Content-Type-Options'], 'nosniff')
                self.assertEqual(handler.wfile.getvalue(), b'' if head_only else PNG)
                self.assertTrue(handler.ended)

    def test_conditional_get_and_head_validate_before_304(self):
        for header in ('"' + self.sha + '"', 'W/"' + self.sha + '"', '"other", "' + self.sha + '"', '*'):
            for head_only in (False, True):
                handler = Handler({'If-None-Match': header})
                self.assertEqual(images.serve_image(handler, self.reader, self.sha, head_only), 304)
                self.assertEqual(handler.wfile.getvalue(), b'')
                self.assertNotIn('Content-Length', handler.response_headers)
        self.corrupt('UPDATE assets SET data=?', (bytes(len(PNG)),))
        handler = Handler({'If-None-Match': '"' + self.sha + '"'})
        self.assertEqual(images.serve_image(handler, self.reader, self.sha), 503)
        self.assertNotIn('ETag', handler.response_headers)

    def test_nonmatching_etag_returns_verified_body(self):
        handler = Handler({'If-None-Match': '"different"'})
        self.assertEqual(images.serve_image(handler, self.reader, self.sha), 200)
        self.assertEqual(handler.wfile.getvalue(), PNG)

    def test_http_error_codes_are_safe_and_head_has_no_body(self):
        for sha, status in (('../private/path', 400), ('0' * 64, 404)):
            for head_only in (False, True):
                handler = Handler({'If-None-Match': '*'})
                self.assertEqual(images.serve_image(handler, self.reader, sha, head_only), status)
                self.assertEqual(handler.response_headers['Cache-Control'], 'no-store')
                self.assertNotIn('ETag', handler.response_headers)
                if head_only:
                    self.assertEqual(handler.wfile.getvalue(), b'')
                else:
                    body = handler.wfile.getvalue().decode('utf-8')
                    self.assertEqual(set(json.loads(body)), {'error'})
                    self.assertNotIn(str(self.data), body)
                    self.assertNotIn(sha, body)


if __name__ == '__main__':
    unittest.main()
