"""Protect an append-only single-SKU publication without copying untouched stores."""
from contextlib import contextmanager, closing
import hashlib
import json
from pathlib import Path
import re
import sqlite3

from scripts.backup_store import _ordinary_directory_path, _paired_locks, _regular, _write_json

PAIRED_FILES = ('data/monitor.sqlite3', 'data/images.sqlite3', 'schema.sql')


def _record(path):
    _regular(path)
    with path.open('rb') as stream:
        return {'bytes': path.stat().st_size, 'sha256': hashlib.file_digest(stream, 'sha256').hexdigest()}


def _scope(job):
    if (not re.fullmatch(r'collect_[0-9a-f]{32}', str(job.get('id', '')))
            or not re.fullmatch(r'shop_[0-9a-f]{24}', str(job.get('shop_id', '')))
            or type(job.get('observation_id')) is not int or job['observation_id'] < 1):
        raise ValueError('Invalid single-SKU protection scope')
    return {key: job[key] for key in ('id', 'shop_id', 'observation_id')}


def _card(project, scope):
    with closing(sqlite3.connect((project / 'data/monitor.sqlite3').as_uri() + '?mode=ro', uri=True)) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute('PRAGMA query_only=ON')
        row = connection.execute('''SELECT o.*,r.shop_id,r.source_url AS shop_source_url,s.shop_name
            FROM observations o JOIN runs r ON r.run_id=o.run_id JOIN shops s ON s.shop_id=r.shop_id
            WHERE o.observation_id=? AND r.shop_id=?''', (scope['observation_id'], scope['shop_id'])).fetchone()
    from .sku_store import sku_eligible
    if row is None or not sku_eligible(dict(row)):
        raise ValueError('SKU protection needs its eligible original card')
    return hashlib.sha256(json.dumps(dict(row), ensure_ascii=False, sort_keys=True, allow_nan=False).encode()).hexdigest()


def _pointer(project, scope):
    path = project / 'sources/sku_captures/index' / scope['shop_id'] / f"{scope['observation_id']}.json"
    _ordinary_directory_path(path.parent)
    if not path.exists():
        return path, {'exists': False}, {}
    fingerprint = {'exists': True, **_record(path)}
    value = json.loads(path.read_text(encoding='utf-8'))
    capture_id = value.get('capture_id')
    if (value.get('shop_id') != scope['shop_id'] or value.get('observation_id') != scope['observation_id']
            or not isinstance(capture_id, str) or not re.fullmatch(r'collect_[0-9a-f]{32}(?:_o[1-9]\d*)?', capture_id)):
        raise ValueError('Existing SKU pointer has invalid scope')
    directory = project / 'sources/sku_captures' / capture_id
    _ordinary_directory_path(directory)
    previous = {str(path.relative_to(project)).replace('\\', '/'): _record(path)
                for path in (directory / 'capture.json', directory / 'images.sqlite3')}
    return path, fingerprint, previous


def prepare_append_protection(project, workspace, job):
    """Record untouched pair/card and independently preserve the only replaced file."""
    from .sku_store import SkuBatchBackup
    project, workspace = Path(project).resolve(), Path(workspace).absolute()
    _ordinary_directory_path(workspace.parent)
    if (workspace.resolve().is_relative_to(project / 'data')
            or workspace.resolve().is_relative_to(project / 'sources')):
        raise ValueError('SKU protection must be outside business data and sources')
    scope = _scope(job)
    workspace.mkdir(parents=True, exist_ok=False)
    backup = workspace / 'sku_append_protection'
    backup.mkdir()
    with _paired_locks(project):
        paired = {name: _record(project / name) for name in PAIRED_FILES}
        card = _card(project, scope)
        pointer, pointer_state, previous = _pointer(project, scope)
        if pointer_state['exists']:
            content = pointer.read_bytes()
            with (backup / 'previous_pointer.json').open('xb') as stream:
                stream.write(content)
                stream.flush()
                import os
                os.fsync(stream.fileno())
            if _record(backup / 'previous_pointer.json') != {k: v for k, v in pointer_state.items() if k != 'exists'}:
                raise ValueError('SKU pointer backup differs from its original')
        manifest = {'schema': 'sku-append-protection-v1', 'status': 'ready', 'scope': scope,
                    'paired_files': paired, 'card_sha256': card, 'previous_pointer': pointer_state,
                    'previous_capture_files': previous, 'main_database_writes': False}
        _write_json(backup / 'manifest.json', manifest)
    return SkuBatchBackup(project, workspace.resolve(), backup, paired, scope='sku_append_only')


@contextmanager
def append_publication_guard(project, protection):
    """Serialize publication and fail if the card, store or old pointer changed."""
    from .sku_store import SkuSourceChanged
    _ordinary_directory_path(protection.backup)
    _regular(protection.backup / 'manifest.json')
    manifest = json.loads((protection.backup / 'manifest.json').read_text(encoding='utf-8'))
    if (manifest.get('schema') != 'sku-append-protection-v1' or manifest.get('status') != 'ready'
            or manifest.get('main_database_writes') is not False
            or manifest.get('paired_files') != protection.source_inventory):
        raise SkuSourceChanged('Single-SKU protection evidence changed')
    scope = _scope(manifest['scope'])
    with _paired_locks(project):
        paired = {name: _record(project / name) for name in PAIRED_FILES}
        _, pointer_state, previous = _pointer(project, scope)
        if (paired != manifest['paired_files'] or _card(project, scope) != manifest['card_sha256']
                or pointer_state != manifest['previous_pointer'] or previous != manifest['previous_capture_files']):
            raise SkuSourceChanged('SKU original store, card or pointer changed before publication')
        if pointer_state['exists'] and _record(protection.backup / 'previous_pointer.json') != {
                k: v for k, v in pointer_state.items() if k != 'exists'}:
            raise SkuSourceChanged('SKU pointer recovery copy changed')
        yield scope
