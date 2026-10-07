"""Only temporary synthetic stores; no writes to the supplied real project."""

from __future__ import annotations

import argparse
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import shutil
import sqlite3
import sys
import tempfile
import threading
import types
import unittest
from unittest.mock import patch

SOURCE_PROJECT = Path(__file__).resolve().parents[1]
if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", type=Path, default=SOURCE_PROJECT)
    args, remaining = parser.parse_known_args()
    SOURCE_PROJECT = args.project.resolve()
sys.dont_write_bytecode = True
sys.path.insert(0, str(SOURCE_PROJECT))
from scripts import backup_store
from pdd_monitor.store import import_snapshot


class BackupAcceptance(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="pdd_backup_test_")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.project = self.root / "synthetic_project"
        (self.project / "sources").mkdir(parents=True)
        (self.project / "pdd_monitor").mkdir()
        shutil.copyfile(SOURCE_PROJECT / "schema.sql", self.project / "schema.sql")
        shutil.copyfile(SOURCE_PROJECT / "pdd_monitor" / "validation.py", self.project / "pdd_monitor" / "validation.py")
        shutil.copyfile(SOURCE_PROJECT / "pdd_monitor" / "image_variants.py", self.project / "pdd_monitor" / "image_variants.py")
        snapshot = self.project / "sources" / "synthetic.json"
        snapshot.write_text(json.dumps({
            "synthetic": True,
            "shopName": "SYNTHETIC_TEST_ONLY",
            "sourceUrl": "https://example.invalid/synthetic?mall_id=900000001",
            "sort": "上新", "observedFrom": "2026-10-04T01:00:00Z", "observedTo": "2026-10-04T01:10:00Z",
            "status": "complete", "endBoundaryObserved": True,
            "rows": [{"viewOrder": 1, "title": "SYNTHETIC_TEST_ONLY card", "goodsId": None,
                      "salesRaw": "已拼11件", "priceRaw": "¥4.5", "observedAt": "2026-10-04T01:00:00Z"}],
        }, ensure_ascii=False), encoding="utf-8")
        import_snapshot(self.project / "data", snapshot)
        self.output = self.root / "backup"

    def test_standalone_validator_uses_its_project_sibling_without_cache_or_path_changes(self):
        fake = types.ModuleType('pdd_monitor.image_variants')
        fake.validate_image_variant = lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError('wrong project'))
        original_path = list(sys.path)
        with patch.dict(sys.modules, {'pdd_monitor.image_variants': fake}):
            validate, digest = backup_store._load_validator(self.project)
            self.assertTrue(validate(self.project / 'data')['ok'])
            function = validate.__globals__['validate_image_variant']
            self.assertEqual(Path(function.__code__.co_filename), self.project / 'pdd_monitor/image_variants.py')
            self.assertIsNone(function({}, original_url='https://example.invalid/image', title='test'))
            with self.assertRaisesRegex(ValueError, 'requires variant provenance'):
                function({'image_acquisition_url':'https://example.invalid/other'}, original_url='https://example.invalid/image', title='test')
        self.assertEqual(sys.path, original_path)
        self.assertFalse((self.project / 'pdd_monitor/__pycache__').exists())
        self.assertEqual(digest, backup_store._hash(self.project / 'pdd_monitor/validation.py'))

    def test_paired_backup_restore_validate_and_source_unchanged(self):
        before = backup_store._inventory(self.project)
        validate, digest = backup_store._load_validator(self.project)
        validated_directories = []
        def record_validation(directory):
            self.assertTrue(directory.is_dir())
            validated_directories.append(directory)
            return validate(directory)
        with patch.object(backup_store, '_load_validator', return_value=(record_validation, digest)):
            result = backup_store.create_backup(self.project, self.output)
        self.assertEqual(len(validated_directories), 2)
        self.assertEqual(validated_directories[0], self.output / 'data')
        self.assertFalse(validated_directories[1].parent.exists(), 'Default restore must really be deleted')
        self.assertEqual(before, backup_store._inventory(self.project))
        self.assertEqual(before, backup_store._inventory(self.output))
        self.assertEqual(result["source_before"], result["source_after"])
        self.assertTrue(result["source_unchanged"])
        self.assertTrue(result["backup_validation_ok"])
        self.assertTrue(result["restore_validation_ok"])
        restore = json.loads((self.output / "restore_validation.json").read_text(encoding="utf-8"))
        self.assertTrue(restore["restored_files_match_backup"])
        self.assertTrue(restore["temporary_restore_directory_removed"])
        self.assertTrue(restore["validation"]["ok"])
        self.assertEqual(restore["validation"]["metrics"]["card_count"], 1)
        self.assertFalse((self.output / "INCOMPLETE.json").exists())

    def test_retained_restore_is_independent_complete_and_validated_twice(self):
        retained = self.root / 'retained_restore'
        before = backup_store._inventory(self.project)
        validate, digest = backup_store._load_validator(self.project)
        validated_directories = []
        def record_validation(directory):
            self.assertFalse((self.output / 'manifest.json').exists())
            validated_directories.append(directory)
            return validate(directory)
        with patch.object(backup_store, '_load_validator', return_value=(record_validation, digest)):
            result = backup_store.create_backup(self.project, self.output, retain_restore=retained)
        self.assertEqual(validated_directories, [self.output / 'data', retained / 'data'])
        expected_retained = {
            'path': str(retained.resolve()), 'files': before,
            'validation_ok': True, 'independent_copy': True,
        }
        self.assertEqual({key: result['retained_restore'][key] for key in expected_retained}, expected_retained)
        self.assertEqual(before, backup_store._inventory(self.project))
        self.assertEqual(before, backup_store._inventory(self.output))
        self.assertEqual(before, backup_store._inventory(retained))
        for relative in before:
            with self.subTest(relative=relative):
                self.assertFalse((retained / relative).samefile(self.output / relative))
                self.assertFalse((retained / relative).samefile(self.project / relative))
        restored = json.loads((self.output / 'restore_validation.json').read_text(encoding='utf-8'))
        self.assertFalse(restored['temporary_restore_directory_removed'])
        self.assertTrue(restored['restored_files_match_backup'])
        self.assertTrue(restored['validation']['ok'])
        self.assertEqual(restored['validation']['metrics']['card_count'], 1)
        self.assertFalse((self.output / 'INCOMPLETE.json').exists())
        committed = json.loads((self.output / 'manifest.json').read_text(encoding='utf-8'))
        self.assertEqual(committed, result)
        (retained / 'sources/synthetic.json').write_text('retained-copy-only change', encoding='utf-8')
        self.assertEqual(before, backup_store._inventory(self.output))
        self.assertEqual(before, backup_store._inventory(self.project))

    def test_existing_retained_restore_is_refused_even_when_empty(self):
        for nonempty in (False, True):
            with self.subTest(nonempty=nonempty):
                retained = self.root / ('retained_full' if nonempty else 'retained_empty')
                retained.mkdir()
                sentinel = retained / 'keep.txt'
                if nonempty:
                    sentinel.write_text('keep original', encoding='utf-8')
                output = self.root / ('backup_full' if nonempty else 'backup_empty')
                with patch.object(backup_store, '_copy_inventory') as copying, \
                     self.assertRaises(backup_store.BackupError):
                    backup_store.create_backup(self.project, output, retain_restore=retained)
                copying.assert_not_called()
                self.assertFalse((output / 'manifest.json').exists())
                self.assertEqual(list(retained.iterdir()), [sentinel] if nonempty else [])
                if nonempty:
                    self.assertEqual(sentinel.read_text(encoding='utf-8'), 'keep original')

    def test_retained_restore_cannot_overlap_project_or_backup_in_either_direction(self):
        cases = [
            (self.project, self.root / 'backup_project_equal'),
            (self.project / 'restore', self.root / 'backup_project_child'),
            (self.root, self.root / 'backup_project_parent'),
            (self.root / 'backup_equal', self.root / 'backup_equal'),
            (self.root / 'backup_parent/restore', self.root / 'backup_parent'),
            (self.root / 'restore_parent', self.root / 'restore_parent/backup'),
        ]
        before = backup_store._inventory(self.project)
        for retained, output in cases:
            with self.subTest(retained=retained, output=output), \
                 patch.object(backup_store, '_copy_inventory') as copying, \
                 self.assertRaises(backup_store.BackupError):
                backup_store.create_backup(self.project, output, retain_restore=retained)
            copying.assert_not_called()
            self.assertFalse((output / 'manifest.json').exists())
        self.assertEqual(before, backup_store._inventory(self.project))

    def test_retained_restore_reparse_ancestor_is_refused_before_copying(self):
        ancestor = self.root / 'synthetic_reparse'
        ancestor.mkdir()
        retained = ancestor / 'nested/restore'
        original_lstat = Path.lstat
        def reparse_lstat(path, *args, **kwargs):
            attributes = original_lstat(path, *args, **kwargs)
            if path == ancestor:
                return types.SimpleNamespace(st_mode=attributes.st_mode,
                    st_file_attributes=getattr(attributes, 'st_file_attributes', 0) | 0x400)
            return attributes
        with patch.object(Path, 'lstat', reparse_lstat), \
             patch.object(backup_store, '_copy_inventory') as copying, \
             self.assertRaises(backup_store.BackupError):
            backup_store.create_backup(self.project, self.output, retain_restore=retained)
        copying.assert_not_called()
        self.assertFalse(retained.exists())
        self.assertFalse((self.output / 'manifest.json').exists())

    def test_retained_restore_validation_failure_keeps_evidence_without_manifest(self):
        retained = self.root / 'failed_restore'
        before = backup_store._inventory(self.project)
        validate, digest = backup_store._load_validator(self.project)
        calls = []
        def fail_restored_validation(directory):
            calls.append(directory)
            if directory == retained / 'data':
                return {'ok': False, 'synthetic_failure': True}
            return validate(directory)
        with patch.object(backup_store, '_load_validator', return_value=(fail_restored_validation, digest)), \
             self.assertRaises(backup_store.BackupError):
            backup_store.create_backup(self.project, self.output, retain_restore=retained)
        self.assertEqual(calls, [self.output / 'data', retained / 'data'])
        self.assertEqual(before, backup_store._inventory(retained))
        self.assertFalse((self.output / 'manifest.json').exists())
        self.assertTrue((self.output / 'INCOMPLETE.json').exists())
        self.assertEqual(before, backup_store._inventory(self.project))

    def test_retained_restore_copy_tampering_keeps_evidence_without_manifest(self):
        retained = self.root / 'tampered_restore'
        before = backup_store._inventory(self.project)
        original_copy = backup_store._copy_file
        def tamper_restore(source, target):
            original_copy(source, target)
            if target == retained / 'sources/synthetic.json':
                with target.open('a', encoding='utf-8') as stream:
                    stream.write('\nSYNTHETIC tampering')
        with patch.object(backup_store, '_copy_file', tamper_restore), \
             self.assertRaises(backup_store.BackupError):
            backup_store.create_backup(self.project, self.output, retain_restore=retained)
        self.assertTrue((retained / 'sources/synthetic.json').is_file())
        self.assertNotEqual(before, backup_store._inventory(retained))
        self.assertFalse((self.output / 'manifest.json').exists())
        self.assertTrue((self.output / 'INCOMPLETE.json').exists())
        self.assertEqual(before, backup_store._inventory(self.project))

    def test_retained_restore_changed_during_validation_cannot_get_success_manifest(self):
        retained = self.root / 'validation_tampered_restore'
        before = backup_store._inventory(self.project)
        validate, digest = backup_store._load_validator(self.project)
        def validate_then_change(directory):
            result = validate(directory)
            if directory == retained / 'data':
                with (retained / 'sources/synthetic.json').open('a', encoding='utf-8') as stream:
                    stream.write('\n')
            return result
        with patch.object(backup_store, '_load_validator', return_value=(validate_then_change, digest)), \
             self.assertRaises(backup_store.BackupError):
            backup_store.create_backup(self.project, self.output, retain_restore=retained)
        self.assertTrue(retained.is_dir())
        self.assertFalse((self.output / 'manifest.json').exists())
        self.assertTrue((self.output / 'INCOMPLETE.json').exists())
        self.assertEqual(before, backup_store._inventory(self.project))

    def test_cli_accepts_retained_restore_and_publishes_its_verified_path(self):
        retained = self.root / 'cli_retained_restore'
        output = io.StringIO()
        with redirect_stdout(output):
            code = backup_store.main(['--project', str(self.project), '--output', str(self.output),
                                      '--retain-restore', str(retained)])
        self.assertEqual(code, 0)
        self.assertTrue(json.loads(output.getvalue())['ok'])
        manifest = json.loads((self.output / 'manifest.json').read_text(encoding='utf-8'))
        self.assertEqual(manifest['retained_restore']['path'], str(retained.resolve()))
        self.assertTrue(manifest['retained_restore']['validation_ok'])
        self.assertEqual(backup_store._inventory(retained), manifest['files'])

    def test_existing_nonempty_target_never_overwritten(self):
        self.output.mkdir()
        sentinel = self.output / "existing.bin"
        sentinel.write_bytes(b"KEEP EXISTING CONTENT")
        before = backup_store._inventory(self.project)
        with self.assertRaisesRegex(backup_store.BackupError, "not empty"):
            backup_store.create_backup(self.project, self.output)
        self.assertEqual(sentinel.read_bytes(), b"KEEP EXISTING CONTENT")
        self.assertEqual(list(self.output.iterdir()), [sentinel])
        self.assertEqual(before, backup_store._inventory(self.project))

    def test_parallel_copy_keeps_file_bytes_and_exclusive_creation(self):
        source, target = self.root / 'copy_source', self.root / 'copy_target'
        source.mkdir()
        names = ['a.bin', 'b.bin', 'c.bin', 'd.bin']
        for name in names:
            (source / name).write_bytes(name.encode() * 100)
        barrier = threading.Barrier(4, timeout=10)
        original = backup_store._copy_file
        def overlapping_copy(src, dst):
            barrier.wait()
            original(src, dst)
        with patch.object(backup_store, '_copy_file', overlapping_copy):
            backup_store._copy_inventory(source, target, names)
        self.assertTrue(all((source / name).read_bytes() == (target / name).read_bytes() for name in names))
        before = (target / names[0]).read_bytes()
        with self.assertRaises(FileExistsError):
            backup_store._copy_inventory(source, target, names)
        self.assertEqual((target / names[0]).read_bytes(), before)

    def test_copy_failure_drains_other_writers_before_returning(self):
        started = threading.Barrier(4, timeout=10)
        failed, release = threading.Event(), threading.Event()
        finished, errors = [], []
        def worker(src, dst):
            started.wait()
            if src.name == 'fail':
                failed.set()
                raise OSError('SYNTHETIC copy failure')
            self.assertTrue(release.wait(10))
            finished.append(src.name)
        def caller():
            try:
                backup_store._copy_inventory(self.root, self.output, ['fail', 'one', 'two', 'three'])
            except BaseException as error:
                errors.append(error)
        with patch.object(backup_store, '_copy_file', worker):
            thread = threading.Thread(target=caller)
            thread.start()
            try:
                self.assertTrue(failed.wait(10))
                self.assertTrue(thread.is_alive(), 'caller must retain locks until every in-flight writer exits')
                self.assertEqual(finished, [])
            finally:
                release.set()
                thread.join(10)
        self.assertFalse(thread.is_alive())
        self.assertCountEqual(finished, ['one', 'two', 'three'])
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], OSError)

    def test_interrupted_copy_has_no_success_manifest(self):
        original_copy = backup_store._copy_file
        calls = 0
        def interrupted(source, target):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("SYNTHETIC injected interruption")
            return original_copy(source, target)
        before = backup_store._inventory(self.project)
        with patch.object(backup_store, "_copy_file", interrupted), self.assertRaises(OSError):
            backup_store.create_backup(self.project, self.output)
        self.assertFalse((self.output / "manifest.json").exists())
        self.assertTrue((self.output / "INCOMPLETE.json").exists())
        self.assertEqual(before, backup_store._inventory(self.project))

    def test_both_database_writers_blocked_until_copy_complete(self):
        original_copy = backup_store._copy_file
        checked = False
        def verify_locks(source, target):
            nonlocal checked
            if not checked:
                checked = True
                for relative in backup_store.DATABASES:
                    connection = sqlite3.connect(self.project / relative, timeout=0, isolation_level=None)
                    try:
                        with self.assertRaisesRegex(sqlite3.OperationalError, "locked"):
                            connection.execute("BEGIN IMMEDIATE")
                    finally:
                        connection.close()
            return original_copy(source, target)
        with patch.object(backup_store, "_copy_file", verify_locks):
            backup_store.create_backup(self.project, self.output)
        self.assertTrue(checked)
        for relative in backup_store.DATABASES:
            connection = sqlite3.connect(self.project / relative, timeout=0, isolation_level=None)
            try:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute("ROLLBACK")
            finally:
                connection.close()

    def test_wal_refused_without_conversion_or_source_change(self):
        connection = sqlite3.connect(self.project / backup_store.DATABASES[0])
        try:
            self.assertEqual(connection.execute("PRAGMA journal_mode=WAL").fetchone()[0], "wal")
        finally:
            connection.close()
        before = backup_store._inventory(self.project)
        with self.assertRaisesRegex(backup_store.BackupError, "only DELETE"):
            backup_store.create_backup(self.project, self.output)
        self.assertFalse((self.output / "manifest.json").exists())
        self.assertEqual(before, backup_store._inventory(self.project))

    def test_validation_failure_never_publishes_success_manifest(self):
        before = backup_store._inventory(self.project)
        with patch.object(backup_store, "_load_validator", return_value=(lambda directory: {"ok": False}, "test")):
            with self.assertRaisesRegex(backup_store.BackupError, "validate_store failed"):
                backup_store.create_backup(self.project, self.output)
        self.assertFalse((self.output / "manifest.json").exists())
        self.assertTrue((self.output / "INCOMPLETE.json").exists())
        self.assertEqual(before, backup_store._inventory(self.project))

    def test_sources_changed_during_copy_refused(self):
        original_copy = backup_store._copy_file
        def mutate_source(source, target):
            original_copy(source, target)
            if source == self.project / "sources" / "synthetic.json":
                with source.open("a", encoding="utf-8") as stream:
                    stream.write("\n")
        with patch.object(backup_store, "_copy_file", mutate_source), self.assertRaisesRegex(backup_store.BackupError, "Source files changed"):
            backup_store.create_backup(self.project, self.output)
        self.assertFalse((self.output / "manifest.json").exists())
        self.assertTrue((self.output / "INCOMPLETE.json").exists())


if __name__ == "__main__":
    unittest.main(argv=[sys.argv[0], *remaining])
