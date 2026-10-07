"""One consistent read produces the same two original validation contracts."""
from contextlib import closing
from pathlib import Path
import sqlite3
import unittest
from unittest.mock import patch

from pdd_monitor import validation
from pdd_monitor.store import import_snapshot
from tests import test_store as fixtures


class SharedValidationTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.StoreAcceptance()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.data = self.fixture.data
        rows = [fixtures.card(1, '已拼15件'), fixtures.card(2)]
        snapshot = self.fixture.snapshot(rows)
        self.first = import_snapshot(self.data, snapshot, self.fixture.archive(rows))['run_id']
        self.second = self.fixture.import_rows([fixtures.card(1, '已抢10件')], hour=2, status='partial')

    def compare(self, run_id):
        one = validation.validate_store(self.data, run_id)
        all_rows = validation.validate_store(self.data)
        with patch.object(validation.sqlite3, 'connect', wraps=sqlite3.connect) as connect:
            actual_one, actual_all = validation.validate_store_scopes(self.data, run_id)
        self.assertEqual(connect.call_count, 1)
        self.assertEqual((actual_one, actual_all), (one, all_rows))
        self.assertIsNot(actual_one, actual_all)
        return actual_one, actual_all

    def test_mixed_runs_images_and_warnings_keep_both_original_reports(self):
        for run_id in (self.first, self.second, 'run_missing', None):
            with self.subTest(run_id=run_id):
                self.compare(run_id)

    def test_other_run_errors_remain_global_without_changing_requested_scope(self):
        with closing(sqlite3.connect(self.data / 'monitor.sqlite3')) as db:
            db.execute('UPDATE observations SET sales_value=999 WHERE run_id=?', (self.second,))
            db.commit()
        one, all_rows = self.compare(self.first)
        self.assertTrue(one['ok'])
        self.assertFalse(all_rows['ok'])
        self.assertFalse(self.compare(self.second)[0]['ok'])

    def test_global_blob_corruption_fails_both_scopes(self):
        with closing(sqlite3.connect(self.data / 'images.sqlite3')) as db:
            db.execute('UPDATE assets SET data=zeroblob(byte_count)')
            db.commit()
        one, all_rows = self.compare(self.first)
        self.assertFalse(one['ok'])
        self.assertFalse(all_rows['ok'])

    def test_missing_store_returns_two_failures_without_creating_files(self):
        missing = self.fixture.root / 'absent'
        one, all_rows = validation.validate_store_scopes(missing, self.first)
        self.assertEqual(one, validation.validate_store(missing, self.first))
        self.assertEqual(all_rows, validation.validate_store(missing))
        self.assertFalse(missing.exists())


if __name__ == '__main__':
    unittest.main()
