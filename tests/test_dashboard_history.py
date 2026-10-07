"""Only synthetic temporary directories; no build, browser or production writes."""
from pathlib import Path
import shutil
import tempfile
import types
import unittest
from unittest.mock import patch

from pdd_monitor.dashboard_history import HistoryRetentionError, retain_static_history


class StaticHistoryRetentionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='pdd_history_retain_test_')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.app = self.root / 'dashboard'
        self.dist = self.app / 'dist'
        self.history = self.dist / 'history-exports'
        self.history.mkdir(parents=True)
        self.file = self.history / 'synthetic-comparison.json'
        self.file.write_bytes(b'{"synthetic":true,"records":[1,2]}\n')
        (self.dist / 'index.html').write_text('SYNTHETIC OLD', encoding='utf-8')

    def retained(self):
        return list(self.app.glob('.history-exports-retained-*/history-exports'))

    def test_builder_replaces_dist_and_original_independent_files_return_without_rewrite(self):
        expected = self.file.read_bytes()
        original_stat = self.file.stat()
        immutable = self.root / 'timestamp-report.json'
        immutable.write_bytes(expected)
        with retain_static_history(self.app):
            self.assertFalse(self.history.exists())
            retained = self.retained()
            self.assertEqual(len(retained), 1)
            self.assertTrue(retained[0].is_relative_to(self.app))
            self.assertEqual((retained[0] / self.file.name).stat().st_ino, original_stat.st_ino)
            self.dist.rename(self.app / 'previous_dist')
            self.dist.mkdir()
            (self.dist / 'index.html').write_text('SYNTHETIC NEW', encoding='utf-8')
        self.assertEqual(self.file.read_bytes(), expected)
        self.assertEqual((self.file.stat().st_ino, self.file.stat().st_mtime_ns),
                         (original_stat.st_ino, original_stat.st_mtime_ns))
        self.assertFalse(self.file.samefile(immutable))
        self.assertEqual(immutable.read_bytes(), expected)
        self.assertEqual((self.dist / 'index.html').read_text(encoding='utf-8'), 'SYNTHETIC NEW')
        self.assertEqual(self.retained(), [])
        self.assertEqual(list(self.app.glob('.history-exports-retained-*')), [])

    def test_failed_builder_restores_history_and_preserves_original_exception(self):
        failure = RuntimeError('SYNTHETIC BUILD FAILED')
        with self.assertRaises(RuntimeError) as caught:
            with retain_static_history(self.app):
                self.assertFalse(self.history.exists())
                raise failure
        self.assertIs(caught.exception, failure)
        self.assertTrue(self.file.is_file())
        self.assertEqual(self.retained(), [])

    def test_failed_builder_with_missing_dist_restores_history_at_original_path(self):
        with self.assertRaisesRegex(RuntimeError, 'SYNTHETIC'):
            with retain_static_history(self.app):
                self.dist.rename(self.app / 'builder_previous')
                raise RuntimeError('SYNTHETIC removed dist before failure')
        self.assertTrue(self.file.is_file())
        self.assertEqual(self.retained(), [])
        self.assertTrue((self.app / 'builder_previous/index.html').is_file())

    def test_no_previous_history_is_a_noop_and_accepts_new_builder_content(self):
        self.history.rename(self.root / 'unrelated_old_history')
        with retain_static_history(self.app):
            self.assertEqual(self.retained(), [])
            self.history.mkdir()
            (self.history / 'builder-created.json').write_text('{}', encoding='utf-8')
        self.assertEqual(list(self.history.iterdir()), [self.history / 'builder-created.json'])
        self.assertEqual(self.retained(), [])

    def test_first_build_without_dist_does_not_create_a_holding_directory(self):
        self.dist.rename(self.root / 'unrelated_old_dist')
        with retain_static_history(self.app):
            self.assertFalse(self.dist.exists())
            self.assertEqual(self.retained(), [])
            self.dist.mkdir()
        self.assertFalse(self.history.exists())

    def test_new_target_conflict_preserves_both_copies_and_exposes_retained_path(self):
        original = self.file.read_bytes()
        with self.assertRaisesRegex(HistoryRetentionError, 'original files retained at') as caught:
            with retain_static_history(self.app):
                self.history.mkdir()
                self.file.write_bytes(b'NEW CONFLICT MUST SURVIVE')
        retained = self.retained()
        self.assertEqual(len(retained), 1)
        self.assertIn(str(retained[0]), str(caught.exception))
        self.assertEqual((retained[0] / self.file.name).read_bytes(), original)
        self.assertEqual(self.file.read_bytes(), b'NEW CONFLICT MUST SURVIVE')

    def test_restore_rename_failure_preserves_all_original_files_for_recovery(self):
        original_rename = Path.rename
        def fail_restore(source, target):
            if source.parent.name.startswith('.history-exports-retained-'):
                raise PermissionError('SYNTHETIC restoration denied')
            return original_rename(source, target)
        with patch.object(Path, 'rename', fail_restore), \
             self.assertRaisesRegex(HistoryRetentionError, 'original files retained at'):
            with retain_static_history(self.app):
                pass
        retained = self.retained()
        self.assertEqual(len(retained), 1)
        self.assertTrue((retained[0] / self.file.name).is_file())
        self.assertFalse(self.history.exists())

    def test_replaced_holding_history_is_not_adopted_or_deleted(self):
        with self.assertRaisesRegex(HistoryRetentionError, 'replaced'):
            with retain_static_history(self.app):
                retained = self.retained()[0]
                original = retained.with_name('original_history_evidence')
                retained.rename(original)
                shutil.copytree(original, retained)
        self.assertFalse(self.history.exists())
        self.assertTrue((original / self.file.name).is_file())
        self.assertTrue((retained / self.file.name).is_file())

    def test_relative_application_path_is_rejected_before_mutation(self):
        with self.assertRaisesRegex(HistoryRetentionError, 'absolute'):
            with retain_static_history(Path('dashboard')):
                self.fail('An unsafe app path cannot reach the build')
        self.assertTrue(self.file.is_file())
        self.assertEqual(self.retained(), [])

    def test_files_or_reparse_paths_are_rejected_before_mutation(self):
        nested = self.history / 'nested'
        nested.mkdir()
        for unsafe in (self.app, self.dist, self.history, nested, self.file):
            with self.subTest(unsafe=unsafe):
                original_lstat = Path.lstat
                def reparse_lstat(path, *args, **kwargs):
                    info = original_lstat(path, *args, **kwargs)
                    if path == unsafe:
                        return types.SimpleNamespace(st_mode=info.st_mode,
                            st_file_attributes=getattr(info, 'st_file_attributes', 0) | 0x400)
                    return info
                with patch.object(Path, 'lstat', reparse_lstat), self.assertRaises(HistoryRetentionError):
                    with retain_static_history(self.app):
                        self.fail('A reparse path cannot reach the build')
                self.assertTrue(self.file.is_file())
                self.assertEqual(self.retained(), [])
        self.history.rename(self.root / 'original_history')
        self.history.write_text('not a directory', encoding='utf-8')
        with self.assertRaises(HistoryRetentionError):
            with retain_static_history(self.app):
                self.fail('A file cannot be used as the history directory')
        self.assertEqual(self.history.read_text(encoding='utf-8'), 'not a directory')


if __name__ == '__main__':
    unittest.main()
