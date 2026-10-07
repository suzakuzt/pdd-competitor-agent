"""Read and serve verified, content-addressed dashboard images without network IO.

The HTTP router must enforce its existing local same-origin request policy
before calling serve_image. The only caller-controlled storage key is a SHA.
"""
from __future__ import annotations

from collections import OrderedDict
from contextlib import closing
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import stat
import threading

from .store import _image_mime


_SHA = re.compile(r'[0-9a-f]{64}')
_CACHE_CONTROL = 'private, max-age=31536000, immutable'


class ImageReadError(ValueError):
    """A safe public error; never contains a path or a SQLite exception."""
    def __init__(self, status, code):
        self.status = status
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class ImageAsset:
    data: bytes
    mime: str
    sha256: str

    @property
    def etag(self):
        return '"' + self.sha256 + '"'


class ImageReader:
    """Read-only SQLite reader with a byte-bounded, invalidated LRU cache."""
    def __init__(self, data_dir, *, max_cache_bytes=16 * 1024 * 1024):
        if type(max_cache_bytes) is not int or max_cache_bytes < 0:
            raise ValueError('max_cache_bytes must be a nonnegative integer')
        self.database = Path(data_dir).absolute() / 'images.sqlite3'
        self.max_cache_bytes = max_cache_bytes
        self._cache = OrderedDict()
        self._cache_bytes = 0
        self._database_stamp = None
        self._lock = threading.Lock()

    def _stamp(self):
        result = []
        # The paired store normally uses DELETE mode; observing sidecars also
        # prevents a WAL commit from leaving a main-file-only cache stale.
        for suffix in ('', '-wal', '-shm', '-journal'):
            path = Path(str(self.database) + suffix)
            try:
                info = path.lstat()
            except FileNotFoundError:
                if not suffix:
                    raise
                result.append(None)
                continue
            if not stat.S_ISREG(info.st_mode) or path.is_symlink() or (
                    getattr(info, 'st_file_attributes', 0) & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0)):
                raise ImageReadError(503, 'image_unavailable')
            result.append((info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns))
        return tuple(result)

    def _clear(self):
        self._cache.clear()
        self._cache_bytes = 0
        self._database_stamp = None

    def read(self, sha):
        """Return ImageAsset, or ImageReadError with status 400/404/503."""
        if not isinstance(sha, str) or _SHA.fullmatch(sha) is None:
            raise ImageReadError(400, 'invalid_image_id')
        with self._lock:
            try:
                before = self._stamp()
                if before != self._database_stamp:
                    self._clear()
                    self._database_stamp = before
                cached = self._cache.get(sha)
                if cached is not None:
                    self._cache.move_to_end(sha)
                    return cached
                with closing(sqlite3.connect(self.database.as_uri() + '?mode=ro', uri=True, timeout=2)) as database:
                    database.execute('PRAGMA query_only=ON')
                    row = database.execute('SELECT sha256,mime,byte_count,data FROM assets WHERE sha256=?', (sha,)).fetchone()
                if self._stamp() != before:
                    self._clear()
                    raise ImageReadError(503, 'image_unavailable')
                if row is None:
                    raise ImageReadError(404, 'image_not_found')
                saved_sha, mime, byte_count, data = row
                if (saved_sha != sha or type(data) is not bytes or type(byte_count) is not int or byte_count <= 0
                        or len(data) != byte_count or hashlib.sha256(data).hexdigest() != sha
                        or _image_mime(data) != mime):
                    raise ImageReadError(503, 'image_unavailable')
                asset = ImageAsset(data=data, mime=mime, sha256=sha)
                if byte_count <= self.max_cache_bytes:
                    while self._cache and self._cache_bytes + byte_count > self.max_cache_bytes:
                        _, removed = self._cache.popitem(last=False)
                        self._cache_bytes -= len(removed.data)
                    self._cache[sha] = asset
                    self._cache_bytes += byte_count
                return asset
            except ImageReadError:
                raise
            except (OSError, sqlite3.Error, ValueError, TypeError):
                self._clear()
                raise ImageReadError(503, 'image_unavailable') from None


def _etag_matches(header, etag):
    if not isinstance(header, str):
        return False
    for item in header.split(','):
        item = item.strip()
        if item == '*' or (item[2:] if item.startswith('W/') else item) == etag:
            return True
    return False


def serve_image(handler, reader, sha, head_only=False):
    """Send verified GET/HEAD bytes or a conditional 304 after successful read.

    Same-origin authorization belongs to the outer router. Errors use fixed
    codes and no-store; no raw exception or filesystem detail reaches the client.
    """
    try:
        asset = reader.read(sha)
    except ImageReadError as error:
        body = (json.dumps({'error': error.code}, separators=(',', ':')) + '\n').encode('utf-8')
        handler.send_response(error.status)
        handler.send_header('Content-Type', 'application/json; charset=utf-8')
        handler.send_header('Content-Length', str(len(body)))
        handler.send_header('Cache-Control', 'no-store')
        handler.send_header('X-Content-Type-Options', 'nosniff')
        handler.end_headers()
        if not head_only:
            handler.wfile.write(body)
        return error.status
    not_modified = _etag_matches(handler.headers.get('If-None-Match'), asset.etag)
    status = 304 if not_modified else 200
    handler.send_response(status)
    handler.send_header('ETag', asset.etag)
    handler.send_header('Cache-Control', _CACHE_CONTROL)
    handler.send_header('X-Content-Type-Options', 'nosniff')
    if not not_modified:
        handler.send_header('Content-Type', asset.mime)
        handler.send_header('Content-Length', str(len(asset.data)))
    handler.end_headers()
    if not head_only and not not_modified:
        handler.wfile.write(asset.data)
    return status
