"""Opt-in local restic archive with paired SQLite restore validation.

The v2 manifest retains every source hash, but only its explicitly declared
materialized_files exist beside it. Consumers must understand v2 before using it.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import stat
import subprocess
import tarfile
import tempfile
import threading
import time

VERSION = '0.19.1'
EXECUTABLE_SHA256 = 'b0dd1fd21eea5d8fe1325f55f7118213c21f36de8a261e04c0624a5ab9fd7830'
MATERIALIZED = ('data/monitor.sqlite3', 'data/images.sqlite3', 'schema.sql')
REPOSITORY_RELATIVE = 'runtime/restic/.state/repository'
FULL_DRILL_SECONDS = 24 * 60 * 60
FULL_DRILL_INTERVAL = 7


class ResticBackupError(ValueError):
    pass


def require(value, message):
    if not value:
        raise ResticBackupError(message)


def regular(path, *, directory=False):
    info = path.lstat()
    require(not path.is_symlink() and not getattr(info, 'st_file_attributes', 0) &
            getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0), 'Linked restic path refused')
    require(stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode), 'Ordinary restic path required')


def ordinary_path(path):
    path = Path(os.path.abspath(path))
    for parent in reversed((path, *path.parents)):
        if os.path.lexists(parent):
            regular(parent, directory=True)
    return path


def digest(path):
    regular(path)
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def file_record(path):
    return {'bytes': path.stat().st_size, 'sha256': digest(path)}


def validate_inventory(files):
    require(isinstance(files, dict) and set(MATERIALIZED) <= files.keys(), 'Incomplete v2 source inventory')
    for name, record in files.items():
        require(isinstance(name, str), 'Invalid archive path')
        relative = PurePosixPath(name)
        require(not relative.is_absolute() and '..' not in relative.parts and ':' not in name and '\\' not in name
                and (name in MATERIALIZED or name.startswith('sources/')), 'Unsafe archive path')
        require(isinstance(record, dict) and type(record.get('bytes')) is int and record['bytes'] >= 0
                and isinstance(record.get('sha256'), str) and re.fullmatch('[0-9a-f]{64}', record['sha256']),
                'Invalid archive file fingerprint')


def private_directory(path):
    ordinary_path(path)
    if path.exists():
        regular(path, directory=True)
    else:
        path.mkdir(parents=True, mode=0o700)
    protect_private_path(path, directory=True)


def protect_private_path(path, *, directory=False):
    if os.name == 'nt':
        # Set a fresh ACL before creating any secret. No secret is passed on the
        # command line, and this is a fixed local ACL operation, not downloaded code.
        literal = str(path).replace("'", "''")
        kind = 'DirectorySecurity' if directory else 'FileSecurity'
        io_kind = 'Directory' if directory else 'File'
        inheritance = "'ContainerInherit,ObjectInherit','None'," if directory else ''
        command = (f"$ErrorActionPreference='Stop'; $a=New-Object System.Security.AccessControl.{kind}; "
                   "$a.SetAccessRuleProtection($true,$false); "
                   "$s=[System.Security.Principal.WindowsIdentity]::GetCurrent().User; "
                   f"$r=New-Object System.Security.AccessControl.FileSystemAccessRule($s,'FullControl',{inheritance}'Allow'); "
                   f"$a.AddAccessRule($r); [IO.{io_kind}]::SetAccessControl('{literal}',$a)")
        done = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', command],
                              capture_output=True, timeout=30)
        require(done.returncode == 0, 'Cannot restrict local restic credential directory')
    else:
        path.chmod(0o700 if directory else 0o600)


@contextmanager
def state_lock(state):
    path = state / 'operation.lock'
    if path.exists():
        regular(path)
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


class LocalRepository:
    def __init__(self, project, *, create=False):
        self.project = Path(project).resolve()
        self.exe = self.project / 'runtime/restic' / VERSION / 'restic.exe'
        ordinary_path(self.exe.parent)
        require(digest(self.exe) == EXECUTABLE_SHA256, 'Pinned restic executable checksum mismatch')
        self.state = self.project / 'runtime/restic/.state'
        self.repo = self.project / REPOSITORY_RELATIVE
        self.password = self.state / 'private/repository-password.txt'
        self.cache = self.state / 'cache'
        ordinary_path(self.state)
        if create:
            private_directory(self.state)
        else:
            regular(self.state, directory=True)

    def initialize(self):
        private_directory(self.password.parent)
        if not self.password.exists():
            require(not self.repo.exists(), 'Repository exists without its local recovery credential')
            with self.password.open('x', encoding='ascii') as stream:
                stream.write(secrets.token_urlsafe(48))
                stream.flush()
                os.fsync(stream.fileno())
        regular(self.password)
        protect_private_path(self.password)
        if not self.repo.exists():
            self.run('init')
        ordinary_path(self.repo)
        regular(self.repo, directory=True)
        return self.repository_id()

    def command(self, *arguments):
        regular(self.password)
        ordinary_path(self.repo)
        ordinary_path(self.cache)
        env = {key: value for key, value in os.environ.items() if not key.startswith('RESTIC_')}
        command = [str(self.exe), '--repo', str(self.repo), '--password-file', str(self.password),
                   '--cache-dir', str(self.cache), '--json', *arguments]
        return command, env

    def run(self, *arguments):
        command, env = self.command(*arguments)
        try:
            completed = subprocess.run(command, cwd=self.project, env=env, capture_output=True,
                                       encoding='utf-8', errors='replace', timeout=900)
        except subprocess.TimeoutExpired as error:
            raise ResticBackupError('Local restic operation timed out; evidence retained') from error
        require(completed.returncode == 0, 'Local restic operation failed: ' + arguments[0])
        try:
            return json.loads(completed.stdout)
        except json.JSONDecodeError:
            try:
                return [json.loads(line) for line in completed.stdout.splitlines() if line.strip()]
            except json.JSONDecodeError as error:
                raise ResticBackupError('Invalid restic JSON response') from error

    def repository_id(self):
        config = self.run('cat', 'config')
        require(isinstance(config, dict) and re.fullmatch('[0-9a-f]{64}', config.get('id', '')), 'Invalid repository ID')
        return config['id']

    def archive(self):
        lines = self.run('backup', '--force', '--host', 'pdd-local-paired-backup', '--tag', 'paired-backup-v2',
                         *MATERIALIZED, 'sources')
        lines = lines if isinstance(lines, list) else [lines]
        summaries = [line for line in lines if line.get('message_type') == 'summary']
        require(len(summaries) == 1 and re.fullmatch('[0-9a-f]{64}', summaries[0].get('snapshot_id', '')),
                'Archive did not return one exact snapshot ID')
        return summaries[0]

    def verify_paths(self, snapshot_id, files):
        validate_inventory(files)
        require(isinstance(snapshot_id, str) and re.fullmatch('[0-9a-f]{64}', snapshot_id), 'Exact snapshot ID required')
        nodes = self.run('ls', '--long', snapshot_id)
        nodes = nodes if isinstance(nodes, list) else [nodes]
        snapshots = [node for node in nodes if node.get('struct_type') == 'snapshot']
        require(len(snapshots) == 1 and snapshots[0].get('id') == snapshot_id, 'Snapshot identity mismatch')
        archived = {}
        for node in nodes:
            if node.get('struct_type') != 'node':
                continue
            require(node.get('type') in ('file', 'dir'), 'Archive contains a special file')
            if node['type'] == 'file':
                path = node.get('path', '')
                require(path.startswith('/') and path[1:] not in archived, 'Ambiguous archive path')
                archived[path[1:]] = node.get('size')
        require(archived == {name: record['bytes'] for name, record in files.items()},
                'Snapshot path inventory differs from the paired source inventory')
        return {'snapshot_id': snapshot_id, 'tree_id': snapshots[0]['tree'], 'file_count': len(archived),
                'total_bytes': sum(archived.values())}

    def restore(self, snapshot_id, destination, *, paired_only):
        arguments = ['restore', snapshot_id, '--target', str(destination), '--verify']
        if paired_only:
            for name in MATERIALIZED:
                arguments.extend(['--include', '/' + name])
        return self.run(*arguments)

    def verify_content(self, snapshot_id, files):
        """Hash the exact decrypted archive bytes without writing 3,000 files."""
        validate_inventory(files)
        command, env = self.command('dump', '--archive', 'tar', snapshot_id, '/')
        actual = {}
        with tempfile.TemporaryFile() as errors:
            process = subprocess.Popen(command, cwd=self.project, env=env, stdout=subprocess.PIPE, stderr=errors)
            deadline = threading.Timer(900, process.kill)
            deadline.daemon = True
            deadline.start()
            try:
                with tarfile.open(fileobj=process.stdout, mode='r|') as archive:
                    for member in archive:
                        if member.isdir():
                            continue
                        require(member.isfile(), 'Archive stream contains a special file')
                        name = member.name.lstrip('/')
                        require(name in files and name not in actual, 'Archive stream path differs from manifest')
                        stream = archive.extractfile(member)
                        digest_value = hashlib.sha256()
                        size = 0
                        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                            digest_value.update(chunk)
                            size += len(chunk)
                        actual[name] = {'bytes': size, 'sha256': digest_value.hexdigest()}
                while process.stdout.read(1024 * 1024):
                    pass
                require(process.wait(timeout=30) == 0, 'Archive content stream failed')
                require(actual == files, 'Archive content SHA256 differs from manifest')
            finally:
                deadline.cancel()
                if process.poll() is None:
                    process.kill()
                process.wait()
                process.stdout.close()
        return {'all_files_sha256_match': True, 'file_count': len(actual),
                'inventory_sha256': inventory_digest(files)}


def inventory_digest(files):
    return hashlib.sha256(json.dumps(files, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')).hexdigest()


def verify_archive(project, archive, files):
    require(isinstance(archive, dict) and archive.get('engine') == 'restic' and archive.get('version') == VERSION
            and archive.get('repository_relative') == REPOSITORY_RELATIVE and archive.get('forced_content_read') is True,
            'Unsupported v2 archive evidence')
    repo = LocalRepository(project)
    require(repo.repository_id() == archive.get('repository_id'), 'Archive repository identity changed')
    evidence = repo.verify_paths(archive.get('snapshot_id'), files)
    require(evidence == archive.get('inventory'), 'Archive snapshot inventory evidence changed')
    require(archive.get('content_verification') == {'all_files_sha256_match': True, 'file_count': len(files),
                                                  'inventory_sha256': inventory_digest(files)},
            'Archive content fingerprint evidence changed')
    require(repo.verify_content(archive['snapshot_id'], files) == archive['content_verification'],
            'Current archive bytes differ from their content verification')
    repo.run('check')
    return evidence


def load_drill(state, repository_id):
    """A malformed, stale or unbound policy cache must trigger another full drill."""
    try:
        path = state / 'restore-drill.json'
        regular(path)
        value = json.loads(path.read_text(encoding='utf-8'), parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
        epoch, count, sid = value.get('verified_at_epoch'), value.get('completed_since_full'), value.get('snapshot_id')
        require(value.get('repository_id') == repository_id and isinstance(sid, str) and re.fullmatch('[0-9a-f]{64}', sid), 'Unbound full drill')
        require(type(epoch) in (int, float) and math.isfinite(epoch) and 0 < epoch <= time.time()
                and type(count) is int and 0 <= count < FULL_DRILL_INTERVAL, 'Invalid full drill age/count')
        receipt = state / 'full-drills' / (sid + '.json')
        ordinary_path(receipt.parent)
        require(digest(receipt) == value.get('receipt_sha256'), 'Full drill receipt changed')
        evidence = json.loads(receipt.read_text(encoding='utf-8'))
        require(all(evidence.get(key) == value[key] for key in ('repository_id', 'snapshot_id', 'verified_at_epoch'))
                and all(evidence.get(key) is True for key in ('files_sha256_match', 'validation_ok', 'repository_read_data_ok')),
                'Full drill receipt is not verified')
        return value
    except (OSError, ValueError, TypeError, KeyError):
        return {}


def create_incremental_backup(project, output, *, retain_restore=None, full_restore=False, helpers):
    started = time.perf_counter()
    timings = {}
    project, output = ordinary_path(project).resolve(), ordinary_path(output).resolve()
    helpers._regular(project, directory=True)
    require(not (project.is_relative_to(output) or output.is_relative_to(project / 'data') or
                 output.is_relative_to(project / 'sources') or output.is_relative_to(project / 'runtime/restic')),
            'Incremental output must be independent of data, sources and restic runtime')
    ordinary_path(output)
    require(not output.exists() or not any(output.iterdir()), 'Existing backup output cannot be overwritten')
    if retain_restore is not None:
        retain_restore = ordinary_path(retain_restore)
        require(not os.path.lexists(retain_restore), 'Retained restore directory must be new')
        require(all(not (retain_restore.is_relative_to(p) or p.is_relative_to(retain_restore))
                    for p in (project, output)), 'Retained restore must be independent')
    original = helpers._inventory(project)
    validate_inventory(original)
    validate_store, validator_sha = helpers._load_validator(project)
    materialized = {name: original[name] for name in MATERIALIZED}
    output.mkdir(parents=True, exist_ok=True)
    helpers._write_json(output / 'INCOMPLETE.json', {'status': 'incomplete', 'source_project': str(project)})
    repo = LocalRepository(project, create=True)
    with state_lock(repo.state):
        repository_id = repo.initialize()
        drill_path = repo.state / 'restore-drill.json'
        drill = load_drill(repo.state, repository_id)
        full_due = (full_restore or drill.get('repository_id') != repository_id or
                    time.time() - drill.get('verified_at_epoch', 0) >= FULL_DRILL_SECONDS or
                    drill.get('completed_since_full', FULL_DRILL_INTERVAL) >= FULL_DRILL_INTERVAL - 1)
        with helpers._paired_locks(project) as connection:
            require(helpers._inventory(project) == original, 'Source changed while acquiring paired locks')
            versions = {schema: connection.execute(f'PRAGMA {schema}.user_version').fetchone()[0]
                        for schema in ('main', 'images')}
            stage = time.perf_counter()
            summary = repo.archive()
            snapshot_id = summary['snapshot_id']
            archive_inventory = repo.verify_paths(snapshot_id, original)
            content_verification = repo.verify_content(snapshot_id, original)
            repo.run('check')
            timings['archive_seconds'] = round(time.perf_counter() - stage, 3)
            stage = time.perf_counter()
            repo.restore(snapshot_id, output, paired_only=True)
            require({name: file_record(output / name) for name in MATERIALIZED} == materialized,
                    'Materialized paired backup hash mismatch')
            backup_validation = validate_store(output / 'data')
            helpers._write_json(output / 'backup_validation.json', backup_validation)
            require(backup_validation.get('ok'), 'Materialized paired backup validation failed')
            timings['paired_backup_restore_and_validation_seconds'] = round(time.perf_counter() - stage, 3)
            stage = time.perf_counter()
            with helpers._restore_directory(retain_restore) as restored:
                identity = helpers._directory_identity(restored)
                repo.restore(snapshot_id, restored, paired_only=True)
                restored_inventory = {name: file_record(restored / name) for name in MATERIALIZED}
                require(restored_inventory == materialized, 'Paired restore hash mismatch')
                restore_validation = validate_store(restored / 'data')
                require(restore_validation.get('ok'), 'Paired restored-copy validation failed')
                require({name: file_record(restored / name) for name in MATERIALIZED} == materialized,
                        'Restored files changed during validation')
                require(helpers._directory_identity(restored) == identity, 'Restored directory changed')
                for name in MATERIALIZED:
                    require((restored / name).stat().st_nlink == 1 and
                            not os.path.samefile(restored / name, output / name), 'Independent restored copies required')
            timings['paired_rehearsal_restore_and_validation_seconds'] = round(time.perf_counter() - stage, 3)
            full_evidence = None
            if full_due:
                stage = time.perf_counter()
                with tempfile.TemporaryDirectory(prefix='pdd_full_', dir=project.parent) as folder:
                    full = Path(folder)
                    repo.restore(snapshot_id, full, paired_only=False)
                    require(helpers._inventory(full) == original, 'Full restored sources hash mismatch')
                    full_validation = validate_store(full / 'data')
                    require(full_validation.get('ok'), 'Full restored database validation failed')
                repo.run('check', '--read-data')
                full_evidence = {'snapshot_id': snapshot_id, 'repository_id': repository_id,
                                 'files_sha256_match': True, 'validation_ok': True, 'repository_read_data_ok': True}
                timings['full_restore_drill_seconds'] = round(time.perf_counter() - stage, 3)
            helpers._write_json(output / 'restore_validation.json', {
                'scope': 'paired_databases_and_schema', 'restored_files_match_backup': True,
                'temporary_restore_directory_removed': retain_restore is None,
                'retained_restore_directory': str(retain_restore) if retain_restore else None,
                'validation': restore_validation, 'full_archive_restore': full_evidence})
            require(helpers._inventory(project) == original, 'Source changed during locked incremental backup')
        after = helpers._inventory(project)
        require(after == original, 'Source changed before incremental backup completion')
        manifest = {
            'format_version': 2, 'status': 'complete', 'completed_at': helpers._now(), 'source_project': str(project),
            'lock_strategy': 'one attached connection, mode=rw, BEGIN IMMEDIATE, ROLLBACK',
            'journal_modes': {'main': 'delete', 'images': 'delete'}, 'schema_versions': versions,
            'source_unchanged': True, 'source_before': original, 'source_after': after, 'files': original,
            'materialized_files': materialized, 'validator_source_sha256': validator_sha,
            'backup_validation_ok': True, 'restore_validation_ok': True,
            'restore_scope': 'paired_databases_and_schema',
            'archive': {'engine': 'restic', 'version': VERSION, 'repository_relative': REPOSITORY_RELATIVE,
                        'repository_id': repository_id, 'snapshot_id': snapshot_id, 'forced_content_read': True,
                        'inventory': archive_inventory, 'content_verification': content_verification},
            'full_restore_drill': full_evidence,
            'full_restore_policy': {'max_age_seconds': FULL_DRILL_SECONDS, 'max_following_backups': FULL_DRILL_INTERVAL},
            'timings': {**timings, 'total_seconds': round(time.perf_counter() - started, 3)},
            'notes': ['All sources exist in the exact local archive snapshot; only paired databases/schema are materialized.',
                      'Recovery requires the local repository and its separate private credential; migrate both.',
                      'This is stored-evidence validation, not proof of live sales or unattended collection.'],
        }
        if retain_restore is not None:
            require(helpers._directory_identity(retain_restore) == identity, 'Retained restore directory changed')
            manifest['retained_restore'] = {'path': str(retain_restore), 'directory_identity': identity,
                                          'files': materialized, 'validation_ok': True, 'independent_copy': True}
        next_drill = ({'repository_id': repository_id, 'snapshot_id': snapshot_id, 'verified_at_epoch': time.time(),
                       'completed_since_full': 0} if full_due else
                      {**drill, 'completed_since_full': drill.get('completed_since_full', 0) + 1})
        if full_due:
            receipt = repo.state / 'full-drills' / (snapshot_id + '.json')
            ordinary_path(receipt.parent)
            receipt.parent.mkdir(exist_ok=True)
            helpers._write_json(receipt, {**full_evidence, 'verified_at_epoch': next_drill['verified_at_epoch']})
            next_drill['receipt_sha256'] = digest(receipt)
        temporary = repo.state / ('restore-drill-' + secrets.token_hex(8) + '.tmp')
        helpers._write_json(temporary, next_drill)
        os.replace(temporary, drill_path)
        manifest['last_full_restore'] = {key: next_drill[key] for key in
                                         ('repository_id', 'snapshot_id', 'verified_at_epoch', 'completed_since_full', 'receipt_sha256')}
        try:
            helpers._write_json(output / '.backup-commit.tmp', manifest)
            (output / 'INCOMPLETE.json').unlink()
            (output / '.backup-commit.tmp').rename(output / 'manifest.json')
        except BaseException:
            if not (output / 'manifest.json').exists() and not (output / 'INCOMPLETE.json').exists():
                helpers._write_json(output / 'INCOMPLETE.json', {'status': 'incomplete', 'source_project': str(project)})
            raise
        return manifest
