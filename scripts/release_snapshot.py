"""Portable sealed capture -> paired backup -> rehearsal -> import -> job finish.

prepare writes only an explicit independent workspace and invokes the project's
original paired-backup program. apply defaults to read-only preflight; --execute
is required for publishing. It never reads an owner/private file or accepts a
token: capture_control.finish owns that boundary. Dashboard build is separate.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing, contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import sqlite3
import stat
import subprocess
import sys
import tempfile
import time
import uuid
import zipfile

sys.dont_write_bytecode = True
DATABASES = ('data/monitor.sqlite3', 'data/images.sqlite3')
STATE_DIRS = ('competitors', 'new_arrivals', 'profit_lab', 'artist_research', 'artist_heat')
TABLES = ('shops', 'runs', 'observations', 'candidate_groups', 'group_members', 'image_tasks',
          'validation_runs', 'images.assets', 'images.source_links')
PRIVATE_KEYS = {'ownertoken', 'accesstoken', 'refreshtoken', 'apikey', 'password', 'cookies', 'authorization', 'credentials'}


class ReleaseError(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise ReleaseError(message)


def now():
    return datetime.now(timezone.utc).isoformat()


def regular(path, directory=False):
    info = Path(path).lstat()
    require(not Path(path).is_symlink() and not getattr(info, 'st_file_attributes', 0) & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0), 'Linked/reparse source refused')
    require(stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode), 'Ordinary file/directory required')


def fingerprint(path):
    path = Path(path)
    regular(path)
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(block)
    return {'bytes': path.stat().st_size, 'sha256': digest.hexdigest()}


def read(path):
    regular(path)
    require(Path(path).stat().st_size <= 64 * 1024 * 1024, 'JSON receipt too large')
    return json.loads(Path(path).read_text(encoding='utf-8-sig'), parse_constant=lambda v: (_ for _ in ()).throw(ValueError(v)))


def public_path(relative):
    text = str(relative).replace('\\', '/').lower()
    return not any(part in text for part in ('private', 'token', 'credential', 'cookie', '__pycache__', '.pyc'))


def public_json(value):
    if isinstance(value, dict):
        require(not any(re.sub(r'[^a-z]', '', str(k).lower()) in PRIVATE_KEYS for k in value), 'Private field in public evidence')
        for item in value.values():
            public_json(item)
    elif isinstance(value, list):
        for item in value:
            public_json(item)


def scan_public(path):
    require(public_path(Path(path).name), 'Private input name refused')
    regular(path)
    if Path(path).suffix.lower() == '.json':
        public_json(read(path))
    elif Path(path).suffix.lower() == '.zip':
        with zipfile.ZipFile(path) as archive:
            members = archive.infolist()
            require(len(members) <= 10000 and sum(m.file_size for m in members) <= 256 * 1024 * 1024, 'Archive public scan bounds exceeded')
            for member in members:
                name = PurePosixPath(member.filename)
                require(not name.is_absolute() and '..' not in name.parts and ':' not in str(name) and public_path(name), 'Private/unsafe archive member refused')
                require(not stat.S_ISLNK(member.external_attr >> 16) and member.file_size <= 32 * 1024 * 1024, 'Unsafe archive entry refused')
                if name.suffix.lower() == '.json':
                    public_json(json.loads(archive.read(member).decode('utf-8-sig')))


def write_new(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    public_json(value)
    data = (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n').encode('utf-8')
    # Publish only a complete commit marker; no truncated success receipt survives.
    temporary = path.with_name('.' + path.name + '.' + uuid.uuid4().hex + '.tmp')
    with temporary.open('xb') as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    if path.exists():
        temporary.unlink()
        raise ReleaseError('Existing receipt is immutable: ' + path.name)
    temporary.rename(path)


def copy_exact(source, destination):
    source, destination = Path(source), Path(destination)
    expected = fingerprint(source)
    if destination.exists():
        require(fingerprint(destination) == expected, 'Existing destination conflicts; nothing overwritten')
        return expected
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name('.' + destination.name + '.' + uuid.uuid4().hex + '.copy.tmp')
    with source.open('rb') as reader, temporary.open('xb') as writer:
        shutil.copyfileobj(reader, writer, 1024 * 1024)
        writer.flush()
        os.fsync(writer.fileno())
    require(fingerprint(source) == expected == fingerprint(temporary), 'Source changed while copying')
    require(not destination.exists(), 'Destination appeared while copying')
    temporary.rename(destination)
    return expected


def walk_files(root):
    if not root.exists():
        return
    regular(root, directory=True)
    for folder, dirs, files in os.walk(root, followlinks=False):
        for name in list(dirs):
            child = Path(folder) / name
            regular(child, directory=True)
            if not public_path(child.relative_to(root)):
                dirs.remove(name)
        for name in sorted(files):
            child = Path(folder) / name
            if public_path(child.relative_to(root)):
                regular(child)
                yield child


def public_state(project):
    result = {}
    for folder in STATE_DIRS:
        for path in walk_files(project / 'state' / folder):
            require(path.suffix.lower() not in ('.db', '.sqlite', '.sqlite3'), 'Unexpected DB in public business state')
            scan_public(path)
            result[path.relative_to(project).as_posix()] = fingerprint(path)
    return result


def code_inventory(project):
    files = [project / 'schema.sql', project / 'scripts/backup_store.py']
    if (project / 'scripts/restic_backup.py').exists():
        files.append(project / 'scripts/restic_backup.py')
    files.extend(p for p in walk_files(project / 'pdd_monitor') if p.suffix == '.py')
    return {p.relative_to(project).as_posix(): fingerprint(p) for p in files}


def db_hashes(project):
    return {name: fingerprint(project / name) for name in DATABASES}


def canonical_hash(value):
    def binary(item):
        if isinstance(item, bytes):
            return {'bytes': len(item), 'sha256': hashlib.sha256(item).hexdigest()}
        raise TypeError(type(item).__name__)
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), default=binary).encode('utf-8')).hexdigest()


def row_baseline(project):
    with closing(sqlite3.connect((project / DATABASES[0]).as_uri() + '?mode=ro', uri=True)) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute('ATTACH DATABASE ? AS images', ((project / DATABASES[1]).as_uri() + '?mode=ro',))
        connection.execute('PRAGMA query_only=ON')
        connection.execute('BEGIN')
        result = {}
        for table in TABLES:
            schema, name = table.split('.') if '.' in table else ('main', table)
            keys = sorted((r['pk'], r['name']) for r in connection.execute(f'PRAGMA {schema}.table_info({name})') if r['pk'])
            rows = {}
            for row in connection.execute(f'SELECT * FROM {table} ORDER BY rowid'):
                key = json.dumps([row[k] for _, k in keys], ensure_ascii=False)
                require(key not in rows, 'Duplicate primary-key audit key')
                rows[key] = canonical_hash(dict(row))
            result[table] = {'count': len(rows), 'rows': rows}
        return result


def preserved(before, after):
    return {table: all(after[table]['rows'].get(key) == digest for key, digest in old['rows'].items()) for table, old in before.items()}


def worker(project, action, payload):
    command = [sys.executable, '-B', '-X', 'utf8', str(Path(__file__).resolve()), '_worker', '--project', str(project), '--action', action]
    result = subprocess.run(command, input=json.dumps(payload), text=True, encoding='utf-8', capture_output=True, timeout=900)
    require(result.returncode == 0, 'Project operation failed: ' + action + '; inspect the isolated stage and retry without overwriting')
    response = json.loads(result.stdout)
    require(response.get('ok') is True, 'Project operation did not return success: ' + action)
    return response['result']


def verify_backup(backup):
    manifest = read(backup / 'manifest.json')
    require(manifest.get('status') == 'complete' and all(manifest.get(k) is True for k in ('source_unchanged', 'backup_validation_ok', 'restore_validation_ok')), 'Paired backup has not passed validation and restore')
    require(manifest.get('files') == manifest.get('source_before') == manifest.get('source_after') and
            all(name in manifest['files'] for name in (*DATABASES, 'schema.sql')), 'Incomplete paired-backup inventory')
    version = manifest.get('format_version', 1)
    require(version in (1, 2), 'Unsupported paired-backup format')
    checked_files = manifest['files']
    if version == 2:
        expected_materialized = {name: manifest['files'][name] for name in (*DATABASES, 'schema.sql')}
        require(manifest.get('materialized_files') == expected_materialized and
                manifest.get('restore_scope') == 'paired_databases_and_schema', 'Invalid v2 materialized inventory')
        if __package__:
            from .restic_backup import verify_archive
        elif __name__ != '__main__':
            from scripts.restic_backup import verify_archive
        else:
            from restic_backup import verify_archive
        verify_archive(Path(manifest['source_project']), manifest.get('archive'), manifest['files'])
        checked_files = expected_materialized
    ordinary_directory_path(backup)
    checked_directories = {backup}
    for name, expected in checked_files.items():
        relative = PurePosixPath(name)
        require(not relative.is_absolute() and '..' not in relative.parts and ':' not in name and '\\' not in name,
                'Backup inventory path is unsafe')
        path = backup / name
        for parent in path.parents:
            if parent in checked_directories:
                break
            regular(parent, directory=True)
            checked_directories.add(parent)
        regular(path)
        require(path.stat().st_nlink == 1, 'Backup files must be independent copies, not hard links')
        require(path.resolve().is_relative_to(backup) and fingerprint(path) == expected, 'Backup file changed or escaped inventory')
    return manifest


def ordinary_directory_path(path):
    path = Path(os.path.abspath(Path(path).expanduser()))
    for parent in reversed((path, *path.parents)):
        if os.path.lexists(parent):
            regular(parent, directory=True)
    return path


def verify_retained_restore(recovery, backup, manifest):
    """Authorize a fresh restore only at its original path and complete bytes."""
    evidence = manifest.get('retained_restore') or {}
    restored_files = manifest['materialized_files'] if manifest.get('format_version') == 2 else manifest['files']
    require(evidence.get('path') == str(recovery) and evidence.get('files') == restored_files and
            evidence.get('validation_ok') is True and evidence.get('independent_copy') is True,
            'Backup did not retain this validated restore')
    ordinary_directory_path(recovery)
    regular(recovery, directory=True)
    identity = recovery.stat()
    require(evidence.get('directory_identity') == {'device': identity.st_dev, 'inode': identity.st_ino},
            'Retained restore directory was replaced')
    paths = []
    for folder, dirs, files in os.walk(recovery, followlinks=False):
        for name in dirs:
            regular(Path(folder) / name, directory=True)
        paths.extend(Path(folder) / name for name in files)
    require({path.relative_to(recovery).as_posix() for path in paths} == set(restored_files),
            'Retained restore inventory changed')
    def inspect(path):
        name = path.relative_to(recovery).as_posix()
        regular(path)
        require(path.stat().st_nlink == 1 and not os.path.samefile(path, backup / name),
                'Retained restore must contain independent copies, not hard links')
        require(fingerprint(path) == restored_files[name], 'Retained restore file changed')
    with ThreadPoolExecutor(max_workers=4, thread_name_prefix='pdd-restore-check') as pool:
        list(pool.map(inspect, paths))
    restore_check = read(backup / 'restore_validation.json')
    require(restore_check.get('restored_files_match_backup') is True and
            restore_check.get('temporary_restore_directory_removed') is False and
            restore_check.get('retained_restore_directory') == str(recovery) and
            (restore_check.get('validation') or {}).get('ok') is True,
            'Retained restore validation receipt does not match')
    return restore_check['validation']


def collect_inputs(snapshot, integrity, options):
    result = {'snapshot.json': snapshot}
    directory = snapshot.parent / 'batches' if integrity['checkpoint_layout'] == 'adjacent_batches' else snapshot.parent
    for item in integrity['source_hashes']:
        if item['role'] == 'batch':
            path = directory / item['file']
            require(fingerprint(path) == {'bytes': item['byte_count'], 'sha256': item['sha256']}, 'Batch changed after integrity verification')
            result['batches/' + item['file']] = path
    for key, path in options.items():
        if path is not None:
            result['import_bundle/' + key] = Path(path).resolve()
    for path in result.values():
        scan_public(path)
    return result


def validate_project(project, workspace):
    regular(project, directory=True)
    require((project / 'AGENTS.md').is_file() and all((project / name).is_file() for name in DATABASES), 'Existing project and paired databases required')
    require(not workspace.is_relative_to(project) and not project.is_relative_to(workspace), 'Workspace must be independent of the entire project')


def windows_path_preflight(project, workspace, input_names=(), *, windows=None):
    """Budget normal Win32 paths, including our temporary atomic-copy filenames.

    Read names only. No long-path machine setting or extended-path escape is used.
    The original backup's own system-temp restore drill is included separately.
    """
    if windows is None:
        windows = os.name == 'nt'
    if not windows:
        return {'status': 'not_applicable', 'platform': os.name}
    project, workspace = Path(project).resolve(), Path(workspace).resolve()
    attempt = workspace / 'attempts' / ('0' * 32)
    backup, recovery = attempt / 'paired_backup', attempt / 'recovery_project'
    restore = Path(tempfile.gettempdir()) / ('pdd_restore_drill_' + '0' * 8)
    planned = []
    def add(path, temporary=None):
        planned.append(path)
        if temporary == 'copy':
            planned.append(path.with_name('.' + path.name + '.' + '0' * 32 + '.copy.tmp'))
        elif temporary == 'receipt':
            planned.append(path.with_name('.' + path.name + '.' + '0' * 32 + '.tmp'))
    names = [Path(name) for name in (*DATABASES, 'schema.sql')]
    # The paired backup preserves all old sources; do not omit deep directories.
    for folder, dirs, files in os.walk(project / 'sources', followlinks=False):
        for name in (*dirs, *files):
            relative = (Path(folder) / name).relative_to(project)
            if name in dirs:
                # A file below each directory exercises the stricter parent budget.
                relative = relative / '.path_budget_probe'
            names.append(relative)
    for name in names:
        add(backup / name); add(restore / name); add(recovery / name, 'copy')
    for name in ('schema.sql', 'AGENTS.md', 'scripts/backup_store.py'):
        add(recovery / name, 'copy')
    for folder in [project / 'pdd_monitor', *(project / 'state' / name for name in STATE_DIRS)]:
        for path in walk_files(folder):
            add(recovery / path.relative_to(project), 'copy')
    source_relative = Path('sources') / ('release_' + '0' * 24)
    for name in input_names:
        add(recovery / source_relative / name, 'copy')
        add(project / source_relative / name, 'copy')
    for name in ('intent.json', 'prepared.json', 'commit_intent.json', 'committed.json', 'finished.json'):
        add(workspace / name, 'receipt')
    for name in ('started.json', 'baseline_rows.json', 'capture_integrity.json', 'rehearsal.json'):
        add(attempt / name, 'receipt')
    for name in ('INCOMPLETE.json', 'manifest.json', '.backup-commit.tmp', 'backup_validation.json', 'restore_validation.json'):
        add(backup / name)
    for name in DATABASES:
        add(recovery / (name + '-journal'))
    units = lambda path: len(str(path).encode('utf-16-le')) // 2
    max_file = max(units(path) for path in planned)
    max_parent = max(units(path.parent) for path in planned)
    require(max_file <= 259 and max_parent <= 247,
            'Windows 路径预算不足：预计最长文件路径 ' + str(max_file) + '/259、目录 ' + str(max_parent) +
            '/247 个字符。请改用较短的独立 --workspace（例如盘符根目录下的专用备份子目录），'
            '并确保 TEMP/TMP 和原 sources/state 路径足够短；未创建备份或恢复副本，无需修改机器设置。')
    return {'status': 'passed', 'max_file_utf16_units': max_file, 'max_directory_utf16_units': max_parent,
            'file_limit': 259, 'directory_limit': 247, 'includes_atomic_copy_names': True,
            'includes_system_temp_restore': True}


def prepare(project, snapshot, expected_sha256, shop_id, workspace, *, image_archive=None, image_queue=None, detail_note=None,
            incremental_backup=False):
    started = time.perf_counter()
    timings = {}
    project, snapshot, workspace = (Path(v).expanduser().resolve() for v in (project, snapshot, workspace))
    validate_project(project, workspace)
    require(re.fullmatch(r'[0-9a-f]{64}', expected_sha256 or '') and fingerprint(snapshot)['sha256'] == expected_sha256, 'Sealed snapshot SHA mismatch')
    require(re.fullmatch(r'shop_[0-9a-f]{24}', shop_id or ''), 'Explicit stable shop ID required')
    integrity = worker(project, 'capture_check', {'snapshot': str(snapshot), 'shop_id': shop_id})
    require(integrity.get('ready_for_rehearsal') is True and integrity.get('snapshot_sha256') == expected_sha256,
            'Capture integrity or coverage-drop review has not passed')
    options = {'images.zip': image_archive, 'image_queue.json': image_queue, 'detail_note.json': detail_note}
    inputs = collect_inputs(snapshot, integrity, options)
    path_budget = windows_path_preflight(project, workspace, inputs)
    intent = {'format_version': 1, 'project': str(project), 'snapshot_path': str(snapshot), 'shop_id': shop_id,
              'snapshot_sha256': expected_sha256, 'input_paths': {k: str(v) for k, v in inputs.items()},
              'input_hashes': {k: fingerprint(v) for k, v in inputs.items()}}
    require(intent['input_hashes']['snapshot.json']['sha256'] == expected_sha256, 'Snapshot changed after integrity check')
    if workspace.exists():
        regular(workspace, directory=True)
        require((workspace / 'intent.json').is_file() and read(workspace / 'intent.json') == intent, 'Workspace belongs to another input; use a new workspace')
        if (workspace / 'prepared.json').exists():
            return check(workspace)
    else:
        workspace.mkdir(parents=True)
        write_new(workspace / 'intent.json', intent)
    # A failed preparation is retained; retry creates a fresh independent attempt.
    attempts = workspace / 'attempts'
    attempts.mkdir(exist_ok=True)
    attempt = attempts / uuid.uuid4().hex
    attempt.mkdir()
    baseline_db, baseline_state, baseline_code = db_hashes(project), public_state(project), code_inventory(project)
    write_new(attempt / 'started.json', {'at': now(), 'business_databases': baseline_db, 'public_state': baseline_state, 'code': baseline_code,
                                      'windows_path_budget': path_budget})
    backup, recovery = attempt / 'paired_backup', attempt / 'recovery_project'
    command = [sys.executable, '-B', '-X', 'utf8', str(project / 'scripts/backup_store.py'), '--project', str(project),
               '--output', str(backup), '--retain-restore', str(recovery)]
    if incremental_backup:
        command.append('--incremental')
    stage_started = time.perf_counter()
    completed = subprocess.run(command, text=True, encoding='utf-8', capture_output=True, timeout=900)
    require(completed.returncode == 0, 'Original paired-backup program failed; partial backup retained')
    timings['backup_and_restore_seconds'] = round(time.perf_counter() - stage_started, 3)
    stage_started = time.perf_counter()
    manifest = verify_backup(backup)
    require(Path(manifest['source_project']).resolve() == project and all(manifest['files'][k] == baseline_db[k] for k in DATABASES), 'Paired backup belongs to another baseline')
    require(manifest.get('validator_source_sha256') == baseline_code['pdd_monitor/validation.py']['sha256'],
            'Restore was not validated by the rehearsed project validator')
    verify_retained_restore(recovery, backup, manifest)
    timings['backup_and_retained_restore_check_seconds'] = round(time.perf_counter() - stage_started, 3)
    stage_started = time.perf_counter()
    for name in {**baseline_code, **baseline_state}:
        copy_exact(project / name, recovery / name)
    copy_exact(project / 'AGENTS.md', recovery / 'AGENTS.md')
    before = row_baseline(recovery)
    write_new(attempt / 'baseline_rows.json', before)
    # The original backup already validated these exact restored bytes. Reuse
    # that proof only after checking its complete inventory and directory identity.
    local_inputs = recovery / 'sources' / ('release_' + expected_sha256[:24])
    for name, path in inputs.items():
        copy_exact(path, local_inputs / name)
    payload = import_payload(local_inputs, intent)
    result = worker(recovery, 'import_validate', payload)
    require(result['import']['status'] == 'imported', 'Input already belongs to baseline; no new release is necessary')
    after = row_baseline(recovery)
    preservation = preserved(before, after)
    require(all(preservation.values()), 'Rehearsal altered an old row or image')
    require(after['runs']['count'] == before['runs']['count'] + 1 and after['observations']['count'] == before['observations']['count'] + result['new_run']['cards'], 'Rehearsal produced unexpected extra records')
    before_duplicate = db_hashes(recovery)
    duplicate = worker(recovery, 'import_validate', payload)
    require(duplicate['import']['status'] == 'duplicate' and db_hashes(recovery) == before_duplicate, 'Rehearsal replay is not byte-idempotent')
    timings['rehearsal_seconds'] = round(time.perf_counter() - stage_started, 3)
    stage_started = time.perf_counter()
    require(public_state(recovery) == baseline_state, 'Rehearsal moved fixed baselines or settings')
    require(db_hashes(project) == baseline_db and public_state(project) == baseline_state and code_inventory(project) == baseline_code, 'Source project changed during rehearsal; prepare a new baseline')
    verify_backup(backup)
    require(all(fingerprint(path) == intent['input_hashes'][name] for name, path in inputs.items()), 'Input changed during rehearsal')
    timings['final_preflight_seconds'] = round(time.perf_counter() - stage_started, 3)
    timings['total_seconds'] = round(time.perf_counter() - started, 3)
    write_new(attempt / 'capture_integrity.json', integrity)
    write_new(attempt / 'rehearsal.json', {'ok': True, 'result': result, 'preserved_old_rows': preservation, 'duplicate_db_bytes_unchanged': True,
                                         'new_counts': {k: v['count'] for k, v in after.items()}, 'recovery_database_sha256': before_duplicate})
    ready = {**intent, 'status': 'prepared', 'attempt': attempt.relative_to(workspace).as_posix(),
             'backup_manifest': fingerprint(backup / 'manifest.json'), 'baseline_rows': fingerprint(attempt / 'baseline_rows.json'),
             'rehearsal': fingerprint(attempt / 'rehearsal.json'), 'baseline_database': baseline_db,
             'protected_public_state': baseline_state, 'protected_code': baseline_code,
             'run_id': result['import']['run_id'], 'source_relative': 'sources/release_' + expected_sha256[:24],
             'snapshot_status': result['new_run']['status'], 'source_files_public_only': True,
             'validated_restore_reused': True, 'timings': timings,
             'business_databases_written': False, 'tracking_changed': False, 'dashboard_built': False}
    write_new(workspace / 'prepared.json', ready)
    return ready


def import_payload(source, prepared):
    result = {'snapshot': str(source / 'snapshot.json'), 'shop_id': prepared['shop_id']}
    for name, argument in (('images.zip', 'image_archive'), ('image_queue.json', 'image_queue'), ('detail_note.json', 'detail_note')):
        result[argument] = str(source / 'import_bundle' / name) if 'import_bundle/' + name in prepared['input_hashes'] else None
    return result


def read_prepared(workspace, *, verify_backup_files=True):
    workspace = Path(workspace).resolve()
    prepared = read(workspace / 'prepared.json')
    project = Path(prepared['project']).resolve()
    validate_project(project, workspace)
    attempt = (workspace / prepared['attempt']).resolve()
    require(attempt.is_relative_to(workspace / 'attempts'), 'Prepared attempt escapes workspace')
    require(fingerprint(attempt / 'baseline_rows.json') == prepared['baseline_rows'] and
            fingerprint(attempt / 'rehearsal.json') == prepared['rehearsal'], 'Rehearsal/baseline receipt changed')
    require(fingerprint(attempt / 'paired_backup/manifest.json') == prepared['backup_manifest'], 'Backup manifest changed')
    # Full backup bytes are checked at the external and final pre-import
    # boundaries. Lock entry can reuse that proof for receipt/source checks.
    manifest = verify_backup(attempt / 'paired_backup') if verify_backup_files else read(attempt / 'paired_backup/manifest.json')
    for name, expected in manifest['source_before'].items():
        if name not in DATABASES:
            require(fingerprint(project / name) == expected, 'An original source/schema changed after backup')
    require(public_state(project) == prepared['protected_public_state'] and code_inventory(project) == prepared['protected_code'], 'Protected business state or importer changed; obtain a new backup/rehearsal')
    for name, value in prepared['input_paths'].items():
        require(fingerprint(Path(value)) == prepared['input_hashes'][name], 'Sealed source changed after rehearsal')
    return workspace, project, attempt, prepared


def check(workspace):
    workspace, project, attempt, prepared = read_prepared(workspace)
    require(db_hashes(project) == prepared['baseline_database'], 'Business databases changed after backup; explicit apply recovery or new preparation required')
    return {'status': 'preflight_passed', 'run_id': prepared['run_id'], 'shop_id': prepared['shop_id'],
            'snapshot_status': prepared['snapshot_status'], 'workspace': str(workspace), 'production_writes': False,
            'backup_and_rehearsal_passed': True, 'dashboard_built': False}


@contextmanager
def release_lock(project):
    path = project / 'reports' / '.sealed_release.lock'
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a+b') as handle:
        if path.stat().st_size == 0:
            handle.write(b'0')
            handle.flush()
        handle.seek(0)
        if os.name == 'nt':
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == 'nt':
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def job_matches(project, job_id, prepared):
    status = worker(project, 'job_status', {'job_id': job_id})
    snapshot = read(Path(prepared['snapshot_path']))
    session = status.get('session') or {}
    sealed = session.get('snapshot') or {}
    require(status['shop_id'] == prepared['shop_id'] and status['attempt_id'] == snapshot['collectionEvidence'].get('attemptId'), 'Job is not this sealed shop/attempt')
    require(sealed.get('sha256') == prepared['snapshot_sha256'] and session.get('importReady') is True and session.get('phase') in ('complete', 'partial'), 'Job session is not sealed to the rehearsed bytes')
    require(status['journal_status'] == 'running' or (status.get('accepted_into_history') is True and status.get('run_id') == prepared['run_id']), 'Job is not running or already finished with this exact run')
    return status


def verify_committed(project, attempt, prepared):
    rehearsal = read(attempt / 'rehearsal.json')
    before = read(attempt / 'baseline_rows.json')
    result = worker(project, 'validate_run', {'run_id': prepared['run_id'], 'snapshot': prepared['snapshot_path'], 'shop_id': prepared['shop_id']})
    require(result['new_run'] == rehearsal['result']['new_run'], 'Committed evidence differs from rehearsal')
    after = row_baseline(project)
    require(all(preserved(before, after).values()) and {k: v['count'] for k, v in after.items()} == rehearsal['new_counts'], 'Old rows changed or unexpected records were added; manual review required')
    require(public_state(project) == prepared['protected_public_state'], 'Business state changed during import')
    return result


def apply(workspace, job_id, *, execute=False):
    started = time.perf_counter()
    workspace, project, attempt, prepared = read_prepared(workspace)
    require(isinstance(job_id, str) and re.fullmatch(r'capture_[0-9a-f]{32}', job_id), 'Explicit capture-control job ID required')
    status = job_matches(project, job_id, prepared)
    source = (project / prepared['source_relative']).resolve()
    regular(project / 'sources', directory=True)
    require(prepared['source_relative'] == 'sources/release_' + prepared['snapshot_sha256'][:24] and source.is_relative_to(project / 'sources'), 'Publish source escapes its deterministic directory')
    if (workspace / 'finished.json').exists():
        finished = read(workspace / 'finished.json')
        require(finished['job_id'] == job_id and status.get('run_id') == prepared['run_id'] and status.get('accepted_into_history'), 'Finish receipt no longer matches the public journal')
        return {**finished, 'status': 'already_finished', 'production_writes': False}
    committed = (workspace / 'commit_intent.json').exists()
    exists = worker(project, 'find_run', {'run_id': prepared['run_id']})
    require(not exists or committed, 'Run appeared without this release intent; do not adopt unrelated writes')
    if exists:
        verify_committed(project, attempt, prepared)
    else:
        require(db_hashes(project) == prepared['baseline_database'], 'Business databases changed after backup; explicit apply recovery or new preparation required')
    if not execute:
        return {'status': 'preflight_passed', 'next_action': 'finish_existing_import' if exists else 'import_then_finish',
                'run_id': prepared['run_id'], 'shop_id': prepared['shop_id'], 'production_writes': False}
    with release_lock(project):
        # Recheck after acquiring the process lock; crash releases the OS lock.
        _, _, _, locked_prepared = read_prepared(workspace, verify_backup_files=False)
        require(locked_prepared == prepared, 'Prepared release changed while acquiring the lock')
        status = job_matches(project, job_id, prepared)
        exists = worker(project, 'find_run', {'run_id': prepared['run_id']})
        require(not exists or (workspace / 'commit_intent.json').exists(), 'Run appeared without this release intent; do not adopt unrelated writes')
        if not exists:
            require(db_hashes(project) == prepared['baseline_database'], 'Business databases changed after backup; explicit apply recovery or new preparation required')
        if not (workspace / 'commit_intent.json').exists():
            write_new(workspace / 'commit_intent.json', {'at': now(), 'job_id': job_id, 'run_id': prepared['run_id'], 'snapshot_sha256': prepared['snapshot_sha256']})
        else:
            require(read(workspace / 'commit_intent.json')['job_id'] == job_id, 'Release belongs to another job')
        for name, path in prepared['input_paths'].items():
            copy_exact(Path(path), source / name)
        require(all(fingerprint(source / name) == digest for name, digest in prepared['input_hashes'].items()), 'Published source differs from sealed input')
        exists = worker(project, 'find_run', {'run_id': prepared['run_id']})
        # Recheck every backup byte after input copying, at the last boundary
        # before import/finish; a changed backup can never authorize publication.
        _, _, _, checked = read_prepared(workspace)
        require(checked == prepared, 'Prepared release changed during publication')
        require(all(fingerprint(source / name) == digest for name, digest in prepared['input_hashes'].items()),
                'Published source changed during final preflight')
        if not exists:
            require(db_hashes(project) == prepared['baseline_database'], 'Business databases changed after backup; explicit apply recovery or new preparation required')
            imported = worker(project, 'import_validate', import_payload(source, prepared))
            require(imported['import']['run_id'] == prepared['run_id'], 'Unexpected import result')
        result = verify_committed(project, attempt, prepared)
        if not (workspace / 'committed.json').exists():
            write_new(workspace / 'committed.json', {'status': 'imported_pending_finish', 'at': now(), 'run_id': prepared['run_id'],
                'job_id': job_id, 'validation': result, 'database_hashes': db_hashes(project), 'dashboard_built': False})
        before_finish = db_hashes(project)
        status = job_matches(project, job_id, prepared)
        if not status.get('accepted_into_history'):
            status = worker(project, 'finish', {'job_id': job_id, 'run_id': prepared['run_id']})
        require(status.get('accepted_into_history') is True and status.get('run_id') == prepared['run_id'], 'Job finish did not confirm the exact imported run')
        require(db_hashes(project) == before_finish and public_state(project) == prepared['protected_public_state'], 'Finishing changed business databases or protected state')
        finished = {'status': 'finished', 'ok': True, 'at': now(), 'job_id': job_id, 'run_id': prepared['run_id'], 'shop_id': prepared['shop_id'],
            'snapshot_sha256': prepared['snapshot_sha256'], 'snapshot_status': prepared['snapshot_status'],
            'source_directory': str(source), 'new_run': result['new_run'], 'journal': status,
            'old_rows_and_images_preserved': True, 'fixed_baselines_and_settings_preserved': True,
            'timings': {'apply_seconds': round(time.perf_counter() - started, 3)},
            'dashboard_built': False, 'production_writes': True, 'private_credentials_read_by_this_script': False}
        write_new(workspace / 'finished.json', finished)
        return finished


def imported_evidence(project, run_id, snapshot_path, shop_id):
    snapshot = read(snapshot_path)
    raw = Path(snapshot_path).read_bytes()
    with closing(sqlite3.connect((project / DATABASES[0]).as_uri() + '?mode=ro', uri=True)) as con:
        con.row_factory = sqlite3.Row
        con.execute('ATTACH DATABASE ? AS images', ((project / DATABASES[1]).as_uri() + '?mode=ro',))
        con.execute('PRAGMA query_only=ON')
        run = con.execute('SELECT * FROM runs WHERE run_id=?', (run_id,)).fetchone()
        require(run is not None and run['shop_id'] == shop_id and bytes(run['snapshot_bytes']) == raw, 'Imported run is not the sealed input/shop')
        rows = list(con.execute('SELECT * FROM observations WHERE run_id=? ORDER BY view_order', (run_id,)))
        originals = {r['viewOrder']: r for r in snapshot['rows']}
        require(len(originals) == len(snapshot['rows']) == len(rows) and all(canonical_hash(json.loads(r['row_json'])) == canonical_hash(originals[r['view_order']]) for r in rows), 'Imported raw cards changed or merged')
        links = list(con.execute('SELECT l.asset_sha256,l.source_url,o.image_url,a.data,a.byte_count,a.mime,t.asset_sha256 task_sha,t.status FROM images.source_links l JOIN observations o ON o.observation_id=l.observation_id JOIN images.assets a ON a.sha256=l.asset_sha256 JOIN image_tasks t ON t.observation_id=o.observation_id WHERE l.run_id=?', (run_id,)))
        from pdd_monitor.store import _image_mime
        for link in links:
            require(link['source_url'] == link['image_url'] and link['asset_sha256'] == link['task_sha'] and link['status'] == 'saved' and
                    len(link['data']) == link['byte_count'] and hashlib.sha256(link['data']).hexdigest() == link['asset_sha256'] and _image_mime(link['data']) == link['mime'], 'Imported image binding/SHA/MIME/length mismatch')
        return {'cards': len(rows), 'eligible_gt10': sum(r['eligible_gt10'] for r in rows), 'image_refs': len(links),
                'status': run['status'], 'end_boundary_observed': bool(run['end_boundary_observed']),
                'observed_from': run['observed_from'], 'observed_to': run['observed_to'], 'all_card_json_preserved': True,
                'observation_ids': [r['observation_id'] for r in rows]}


def worker_main(project, action, payload):
    project = Path(project).resolve()
    sys.path.insert(0, str(project))
    from pdd_monitor.store import import_snapshot, _connect_readonly
    from pdd_monitor.validation import validate_store, validate_store_scopes
    from pdd_monitor import capture_control
    require(Path(sys.modules['pdd_monitor.store'].__file__).resolve().is_relative_to(project), 'Unexpected importer module')
    if action == 'capture_check':
        return capture_control.check(project, payload['snapshot'], payload['shop_id'])
    if action == 'validate':
        return validate_store(project / 'data')
    if action in ('job_status', 'finish'):
        return capture_control.status(project, payload['job_id']) if action == 'job_status' else capture_control.finish(project, payload['job_id'], payload['run_id'])
    if action == 'find_run':
        with closing(_connect_readonly(project / 'data')) as con:
            return con.execute('SELECT 1 FROM runs WHERE run_id=?', (payload['run_id'],)).fetchone() is not None
    if action in ('import_validate', 'validate_run'):
        imported = import_snapshot(project / 'data', payload['snapshot'], payload.get('image_archive'), payload.get('image_queue'), payload.get('detail_note')) if action == 'import_validate' else None
        run_id = imported['run_id'] if imported else payload['run_id']
        one, all_rows = validate_store_scopes(project / 'data', run_id)
        require(one.get('ok') and all_rows.get('ok'), 'Run/global validation failed')
        return {'import': imported, 'run_validation': one, 'global_validation': all_rows,
                'new_run': imported_evidence(project, run_id, payload['snapshot'], payload['shop_id'])}
    raise ReleaseError('Unsupported internal action')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    p = commands.add_parser('prepare')
    for name in ('project', 'snapshot', 'workspace'):
        p.add_argument('--' + name, type=Path, required=True)
    p.add_argument('--expected-sha256', required=True)
    p.add_argument('--shop-id', required=True)
    p.add_argument('--incremental-backup', action='store_true',
                   help='Opt in to the local restic v2 backup and paired-database rehearsal')
    for name in ('image-archive', 'image-queue', 'detail-note'):
        p.add_argument('--' + name, type=Path)
    for name in ('check', 'apply'):
        p = commands.add_parser(name)
        p.add_argument('--workspace', type=Path, required=True)
        if name == 'apply':
            p.add_argument('--job-id', required=True)
            p.add_argument('--execute', action='store_true')
    p = commands.add_parser('_worker', help=argparse.SUPPRESS)
    p.add_argument('--project', type=Path, required=True)
    p.add_argument('--action', required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == '_worker':
            result = {'ok': True, 'result': worker_main(args.project, args.action, json.load(sys.stdin))}
        elif args.command == 'prepare':
            result = prepare(args.project, args.snapshot, args.expected_sha256, args.shop_id, args.workspace,
                             image_archive=args.image_archive, image_queue=args.image_queue, detail_note=args.detail_note,
                             incremental_backup=args.incremental_backup)
        elif args.command == 'check':
            result = check(args.workspace)
        else:
            result = apply(args.workspace, args.job_id, execute=args.execute)
        print(json.dumps(result, ensure_ascii=False, allow_nan=False))
        return 0
    except Exception as error:
        # Never emit arbitrary project exceptions or credential-bearing tracebacks.
        print(json.dumps({'ok': False, 'error_type': type(error).__name__, 'message': str(error) if isinstance(error, ReleaseError) else 'Operation failed; stage evidence retained; no automatic rollback or blind retry'}, ensure_ascii=False))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
