"""Actual append-only SKU protection, isolated stores and immutable captures."""
from contextlib import closing
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from pdd_monitor.sku_append import prepare_append_protection
from pdd_monitor.sku_store import save_capture, read_latest, SkuSourceChanged, SkuSaveCancelled
from tests.test_sku_async_pipeline import synthetic_project
from tests.test_sku_pipeline import A, B, JOB, assets, capture


class SingleSkuAppendTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='pdd_sku_append_')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.project = self.root / 'project'
        synthetic_project(self.project)
        self.job = {'id': JOB, 'shop_id': A, 'observation_id': 1}

    def hash_files(self, root):
        return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in root.rglob('*') if p.is_file()}

    def prepare(self, name='protection'):
        return prepare_append_protection(self.project, self.root / name, self.job)

    def save(self, protection, *, job=None, value=None, cancel=None):
        return save_capture(self.project, job or self.job, value or capture(), assets(),
                            protection.workspace / 'item', batch_backup=protection,
                            parallel_prepared=True, cancel_file=cancel)

    def test_single_sku_no_historical_copy_and_untouched_pair(self):
        before = self.hash_files(self.project)
        with patch('pdd_monitor.sku_store.prepare_batch_backup', side_effect=AssertionError('Untouched pair must not be copied')):
            protection = self.prepare()
            result = self.save(protection)
        self.assertEqual(result['backup_scope'], 'sku_append_only')
        self.assertFalse(result['main_database_writes'])
        self.assertEqual(result['image_count'], 1)
        self.assertEqual(read_latest(self.project, A, 1)['variants'][0]['current_price_yuan'], '14.50')
        self.assertIsNone(read_latest(self.project, B, 1))
        after = self.hash_files(self.project)
        self.assertTrue(all(after[name] == digest for name, digest in before.items()))
        self.assertFalse((protection.backup / 'data').exists())

    def test_recollection_preserves_previous_capture_and_pointer_recovery_copy(self):
        self.save(self.prepare('first'))
        old_directory = self.project / 'sources/sku_captures' / JOB
        old_hashes = self.hash_files(old_directory)
        pointer = self.project / 'sources/sku_captures/index' / A / '1.json'
        old_pointer = pointer.read_bytes()
        self.job['id'] = 'collect_' + 'b' * 32
        protection = self.prepare('second')
        self.assertEqual((protection.backup / 'previous_pointer.json').read_bytes(), old_pointer)
        value = capture()
        value['observed_at'] = '2026-10-06T19:00:00+08:00'
        self.save(protection, value=value)
        self.assertEqual(self.hash_files(old_directory), old_hashes)
        self.assertEqual(read_latest(self.project, A, 1)['capture_id'], self.job['id'])

    def test_changed_paired_store_or_schema_stops_publication(self):
        for name in ('data/monitor.sqlite3', 'data/images.sqlite3', 'schema.sql'):
            with self.subTest(name=name):
                protection = self.prepare('change_' + Path(name).stem)
                path = self.project / name
                original = path.read_bytes()
                try:
                    if name.endswith('.sqlite3'):
                        with closing(sqlite3.connect(path)) as connection:
                            connection.execute('CREATE TABLE unrelated_change(x INTEGER)')
                            connection.commit()
                    else:
                        path.write_text('-- changed synthetic schema', encoding='utf-8')
                    with self.assertRaises(SkuSourceChanged):
                        self.save(protection)
                    self.assertFalse((self.project / 'sources/sku_captures' / JOB).exists())
                finally:
                    path.write_bytes(original)

    def test_created_or_changed_pointer_blocks_late_publication(self):
        protection = self.prepare()
        pointer = self.project / 'sources/sku_captures/index' / A / '1.json'
        pointer.parent.mkdir(parents=True)
        pointer.write_text('{}', encoding='utf-8')
        with self.assertRaises((ValueError, SkuSourceChanged)):
            self.save(protection)
        self.assertFalse((self.project / 'sources/sku_captures' / JOB).exists())
        self.assertEqual(pointer.read_text(), '{}')

    def test_other_card_scope_and_cancel_are_rejected(self):
        protection = self.prepare()
        with self.assertRaises(ValueError):
            self.save(protection, job={**self.job, 'observation_id': 2})
        cancel = self.root / 'cancel.json'
        cancel.write_text('{}', encoding='utf-8')
        with self.assertRaises(SkuSaveCancelled):
            self.save(protection, cancel=cancel)
        self.assertFalse((self.project / 'sources/sku_captures' / JOB).exists())

    def test_failed_pointer_write_preserves_old_capture_and_new_orphan(self):
        self.save(self.prepare('first'))
        pointer = self.project / 'sources/sku_captures/index' / A / '1.json'
        old_pointer = pointer.read_bytes()
        self.job['id'] = 'collect_' + 'c' * 32
        protection = self.prepare('second')
        with patch('pdd_monitor.sku_store._publish_latest', side_effect=OSError('Synthetic disk failure')):
            with self.assertRaises(OSError):
                self.save(protection)
        self.assertEqual(pointer.read_bytes(), old_pointer)
        self.assertTrue((self.project / 'sources/sku_captures' / self.job['id'] / 'capture.json').is_file())
        self.assertEqual(read_latest(self.project, A, 1)['capture_id'], JOB)

    def test_failed_first_pointer_write_does_not_expose_orphan_as_latest(self):
        protection = self.prepare()
        with patch('pdd_monitor.sku_store._publish_latest', side_effect=OSError('Synthetic disk failure')):
            with self.assertRaises(OSError):
                self.save(protection)
        self.assertTrue((self.project / 'sources/sku_captures' / JOB / 'capture.json').is_file())
        self.assertIsNone(read_latest(self.project, A, 1))

    def test_cancel_after_directory_commit_preserves_unpublished_orphan(self):
        protection = self.prepare()
        cancel = self.root / 'cancel.json'
        real_rename = Path.rename
        def cancelled_rename(source, destination):
            result = real_rename(source, destination)
            if Path(destination).name == JOB:
                cancel.write_text('{}', encoding='utf-8')
            return result
        with patch.object(Path, 'rename', cancelled_rename):
            with self.assertRaises(SkuSaveCancelled):
                self.save(protection, cancel=cancel)
        self.assertIsNone(read_latest(self.project, A, 1))
        self.assertFalse((self.project / 'sources/sku_captures/index' / A / '1.json').exists())

    def test_corrupted_previous_capture_is_not_overwritten(self):
        self.save(self.prepare('first'))
        self.job['id'] = 'collect_' + 'e' * 32
        protection = self.prepare('second')
        previous = self.project / 'sources/sku_captures' / JOB / 'capture.json'
        previous.write_text('{}', encoding='utf-8')
        with self.assertRaises(SkuSourceChanged):
            self.save(protection)
        self.assertEqual(previous.read_text(), '{}')

    def test_corrupt_pointer_backup_and_corrupt_sku_images_are_rejected(self):
        self.save(self.prepare('first'))
        self.job['id'] = 'collect_' + 'd' * 32
        protection = self.prepare('second')
        (protection.backup / 'previous_pointer.json').write_text('{}', encoding='utf-8')
        with self.assertRaises(SkuSourceChanged):
            self.save(protection)
        self.assertFalse((self.project / 'sources/sku_captures' / self.job['id']).exists())
