"""Consistent paired SQLite backup, source checks, and a temporary restore drill.

Only DELETE journal mode is supported. BEGIN IMMEDIATE on one attached
connection reserves both databases before either is copied. No SQL data or
schema is changed; ROLLBACK releases the locks. Existing writers cause a bounded
wait or failure. Do not run collection/import during backup. No network access.

Usage: python backup_store.py --project PROJECT --output NEW_OR_EMPTY_DIRECTORY
The output contains data/{monitor,images}.sqlite3, sources/, schema.sql, validation
evidence, and a success manifest written last. On any failure, INCOMPLETE.json
remains and manifest.json is absent. Restore only into a separate empty directory;
never copy one database over an active project independently of the other.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sqlite3
import stat
import sys
import tempfile
import time


DATABASES = ("data/monitor.sqlite3", "data/images.sqlite3")
IO_WORKERS = 4


class BackupError(RuntimeError):
    """The backup cannot be proven complete and consistent."""


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _hash(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _regular(path, *, directory=False):
    attributes = path.lstat()
    if path.is_symlink() or getattr(attributes, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0):
        raise BackupError(f"Symbolic links/reparse points are not supported: {path}")
    if not (stat.S_ISDIR(attributes.st_mode) if directory else stat.S_ISREG(attributes.st_mode)):
        raise BackupError(f"Expected an ordinary {'directory' if directory else 'file'}: {path}")


def _ordinary_directory_path(path):
    """Check lexical ancestors before resolve can hide a link or junction."""
    path = Path(os.path.abspath(Path(path).expanduser()))
    for parent in reversed((path, *path.parents)):
        if os.path.lexists(parent):
            _regular(parent, directory=True)
    return path


def _directory_identity(path):
    _regular(path, directory=True)
    info = path.stat()
    return {"device": info.st_dev, "inode": info.st_ino}


@contextmanager
def _restore_directory(retain_restore):
    if retain_restore is None:
        with tempfile.TemporaryDirectory(prefix="pdd_restore_drill_") as temporary:
            yield Path(temporary)
    else:
        _ordinary_directory_path(retain_restore.parent)
        retain_restore.mkdir(parents=True, exist_ok=False)
        yield retain_restore


def _inventory(project):
    paths = [project / name for name in (*DATABASES, "schema.sql")]
    sources = project / "sources"
    _regular(project / "data", directory=True)
    _regular(sources, directory=True)
    for folder, dirs, files in os.walk(sources, followlinks=False):
        for name in dirs:
            _regular(Path(folder) / name, directory=True)
        paths.extend(Path(folder) / name for name in files)
    def inspect(path):
        _regular(path)
        return path.relative_to(project).as_posix(), {"bytes": path.stat().st_size, "sha256": _hash(path)}
    # Read every byte as before; only independent file IO overlaps. Ordered map
    # keeps the same deterministic inventory and drains workers on any failure.
    with ThreadPoolExecutor(max_workers=IO_WORKERS, thread_name_prefix='pdd-backup-hash') as pool:
        return dict(pool.map(inspect, sorted(paths)))


def _write_json(path, value):
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())


def _copy_file(source, target):
    target.parent.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    with source.open("rb") as reader, target.open("xb") as writer:
        shutil.copyfileobj(reader, writer, 1024 * 1024)
        writer.flush()
        syncing = time.perf_counter()
        os.fsync(writer.fileno())
        synced = time.perf_counter()
    return {'copy_seconds': syncing - started, 'fsync_seconds': synced - syncing}


def _copy_inventory(source, target, inventory):
    """Copy independent files with bounded IO while the caller holds both locks.

    Each file keeps its exclusive create and fsync. On failure drain all active
    writers before returning, so neither locks nor the restore directory can be
    released while a worker still writes. The manifest is still committed last.
    """
    started = time.perf_counter()
    totals = {'copy_worker_seconds': 0.0, 'fsync_worker_seconds': 0.0}
    with ThreadPoolExecutor(max_workers=IO_WORKERS, thread_name_prefix='pdd-backup-copy') as pool:
        futures = [pool.submit(_copy_file, source / name, target / name) for name in inventory]
        try:
            for future in as_completed(futures):
                metrics = future.result()
                if metrics:
                    totals['copy_worker_seconds'] += metrics['copy_seconds']
                    totals['fsync_worker_seconds'] += metrics['fsync_seconds']
        except BaseException:
            for future in futures:
                future.cancel()
            raise
    # Worker totals overlap and must not be presented as wall-clock duration.
    return {**{k: round(v, 3) for k, v in totals.items()},
            'elapsed_seconds': round(time.perf_counter() - started, 3)}


def _load_validator(project):
    path = project / "pdd_monitor" / "validation.py"
    _regular(path)
    spec = importlib.util.spec_from_file_location("_pdd_backup_validation", path)
    if spec is None or spec.loader is None:
        raise BackupError("Cannot load the project's validate_store function")
    module = importlib.util.module_from_spec(spec)
    # Loading a validator must not add __pycache__ files to the source project.
    prior = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = prior
    return module.validate_store, _hash(path)


@contextmanager
def _paired_locks(project):
    for name in DATABASES:
        path = project / name
        for suffix in ("-wal", "-shm", "-journal"):
            if Path(str(path) + suffix).exists():
                raise BackupError(f"SQLite sidecar exists; stop writers and review recovery before backup: {path.name}{suffix}")
    connection = sqlite3.connect((project / DATABASES[0]).as_uri() + "?mode=rw", uri=True,
                                 timeout=5, isolation_level=None)
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        connection.execute("ATTACH DATABASE ? AS images", ((project / DATABASES[1]).as_uri() + "?mode=rw",))
        for schema in ("main", "images"):
            mode = connection.execute(f"PRAGMA {schema}.journal_mode").fetchone()[0]
            if mode.lower() != "delete":
                raise BackupError(f"{schema} uses {mode}; only DELETE journal mode is supported, no conversion attempted")
        # SQLite acquires RESERVED locks for all attached writable databases.
        # Any earlier writer must complete before this returns. No write follows.
        connection.execute("BEGIN IMMEDIATE")
        yield connection
    finally:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        connection.close()


def create_backup(project, output, *, retain_restore=None, incremental=False, full_restore=False):
    if incremental:
        if __package__:
            from .restic_backup import create_incremental_backup, ResticBackupError
        else:
            from restic_backup import create_incremental_backup, ResticBackupError
        try:
            return create_incremental_backup(project, output, retain_restore=retain_restore,
                                             full_restore=full_restore, helpers=sys.modules[__name__])
        except ResticBackupError as error:
            raise BackupError(str(error)) from error
    if full_restore:
        raise BackupError('--full-restore requires --incremental')
    started = time.perf_counter()
    timings = {}
    project, output = Path(project).expanduser().resolve(), Path(output).expanduser().resolve()
    _regular(project, directory=True)
    if project.is_relative_to(output) or output.is_relative_to(project / "data") or output.is_relative_to(project / "sources"):
        raise BackupError("Output must be separate from the project root, data/, and sources/")
    if output.exists():
        _regular(output, directory=True)
        if any(output.iterdir()):
            raise BackupError("Output is not empty; existing backups are never overwritten")
    if retain_restore is not None:
        retain_restore = _ordinary_directory_path(retain_restore)
        if os.path.lexists(retain_restore):
            raise BackupError("Retained restore directory must be new; existing directories are never reused")
        for protected in (project, output):
            if retain_restore.is_relative_to(protected) or protected.is_relative_to(retain_restore):
                raise BackupError("Retained restore directory must be independent of project and backup")
    # Validate inputs before making even an incomplete output directory.
    original = _inventory(project)
    validate_store, validator_sha = _load_validator(project)
    output.mkdir(parents=True, exist_ok=True)
    incomplete = output / "INCOMPLETE.json"
    _write_json(incomplete, {"status": "incomplete", "started_at": _now(), "source_project": str(project)})
    try:
        with _paired_locks(project) as connection:
            locked_before = _inventory(project)
            if locked_before != original:
                raise BackupError("Sources changed while acquiring locks; retry only after reviewing active writers")
            versions = {schema: connection.execute(f"PRAGMA {schema}.user_version").fetchone()[0]
                        for schema in ("main", "images")}
            stage_started = time.perf_counter()
            timings['backup_copy'] = _copy_inventory(project, output, original)
            copied = _inventory(output)
            if copied != original:
                raise BackupError("A copied file differs from the locked source")
            backup_validation = validate_store(output / "data")
            _write_json(output / "backup_validation.json", backup_validation)
            if not backup_validation.get("ok"):
                raise BackupError("Backup validate_store failed; see backup_validation.json")
            timings['backup_copy_and_validation_seconds'] = round(time.perf_counter() - stage_started, 3)
            # Restore exactly the bounded inventory into an independent temporary
            # directory. The real data directory is never a restoration target.
            stage_started = time.perf_counter()
            with _restore_directory(retain_restore) as restored:
                restore_identity = _directory_identity(restored)
                timings['restore_copy'] = _copy_inventory(output, restored, original)
                restored_inventory = _inventory(restored)
                if restored_inventory != original:
                    raise BackupError("Restored copy differs from the backup")
                restore_validation = validate_store(restored / "data")
                if not restore_validation.get("ok"):
                    raise BackupError("Restored-copy validate_store failed")
                if retain_restore is not None and _inventory(restored) != original:
                    raise BackupError("Retained restored files changed during validation")
                # The optional output is a real independent copy, never a link
                # farm. Its path identity is bound into the commit manifest.
                _ordinary_directory_path(restored)
                if _directory_identity(restored) != restore_identity:
                    raise BackupError("Restored directory changed during verification")
                for name in original:
                    _regular(restored / name)
                    if (restored / name).stat().st_nlink != 1 or os.path.samefile(restored / name, output / name):
                        raise BackupError("Restored files must be independent copies, not hard links")
            timings['restore_copy_and_validation_seconds'] = round(time.perf_counter() - stage_started, 3)
            _write_json(output / "restore_validation.json", {
                "restored_files_match_backup": True,
                "temporary_restore_directory_removed": retain_restore is None,
                "retained_restore_directory": str(retain_restore) if retain_restore is not None else None,
                "validation": restore_validation,
            })
            locked_after = _inventory(project)
            if locked_after != original:
                raise BackupError("Source files changed during the locked backup")
        # Detect even an unexpected rollback-recovery or a writer racing lock
        # release. Conservatively reject instead of claiming source invariance.
        after = _inventory(project)
        if after != original:
            raise BackupError("Source files changed before backup completion")
        manifest = {
            "format_version": 1,
            "status": "complete",
            "completed_at": _now(),
            "source_project": str(project),
            "lock_strategy": "one attached connection, mode=rw, BEGIN IMMEDIATE, no data/schema writes, ROLLBACK",
            "journal_modes": {"main": "delete", "images": "delete"},
            "schema_versions": versions,
            "source_unchanged": original == after,
            "source_before": original,
            "source_after": after,
            "files": copied,
            "validator_source_sha256": validator_sha,
            "backup_validation_ok": True,
            "restore_validation_ok": True,
            "timings": {**timings, "total_seconds": round(time.perf_counter() - started, 3)},
            "notes": ["This is a backup and copy-only restore drill, not completion of scheduled failure recovery T12.",
                      "Restore the matched two database files and sources together into an empty directory; validate before any live replacement.",
                      "SQLite WAL and existing sidecars are refused; do not remove sidecars to force this script to run."],
        }
        if retain_restore is not None:
            _ordinary_directory_path(retain_restore)
            if _directory_identity(retain_restore) != restore_identity:
                raise BackupError("Retained restore directory changed before backup completion")
            manifest['retained_restore'] = {
                'path': str(retain_restore), 'directory_identity': restore_identity,
                'files': restored_inventory, 'validation_ok': True, 'independent_copy': True,
            }
        # The success manifest is the commit marker. Rename one complete,
        # fsynced marker rather than exposing a partially written success file.
        _write_json(output / ".backup-commit.tmp", manifest)
        incomplete.unlink()
        (output / ".backup-commit.tmp").rename(output / "manifest.json")
        return manifest
    except BaseException:
        # A crash may leave partial files, but never the success commit marker.
        # Keep any partial output for explicit inspection, never auto-delete it.
        if not incomplete.exists() and not (output / "manifest.json").exists():
            _write_json(incomplete, {"status": "incomplete", "source_project": str(project)})
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--retain-restore", type=Path,
                        help="Keep the validated independent restore in this new directory for release rehearsal")
    parser.add_argument('--incremental', action='store_true',
                        help='Use local restic v2: archive all sources, materialize and restore paired databases/schema')
    parser.add_argument('--full-restore', action='store_true',
                        help='Force a full archive restore drill for an incremental backup')
    args = parser.parse_args(argv)
    try:
        manifest = create_backup(args.project, args.output, retain_restore=args.retain_restore,
                                 incremental=args.incremental, full_restore=args.full_restore)
    except (OSError, sqlite3.Error, BackupError) as error:
        print(json.dumps({"ok": False, "error": f"{type(error).__name__}: {error}"}, ensure_ascii=False))
        return 1
    print(json.dumps({"ok": True, "output": str(args.output.resolve()), "files": len(manifest["files"]),
                      "source_unchanged": True, "backup_validation_ok": True, "restore_validation_ok": True}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
