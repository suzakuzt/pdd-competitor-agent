"""Exception paths must preserve both original validation report contracts."""
from contextlib import closing
import sqlite3
import unittest
from unittest.mock import patch

from pdd_monitor import validation
from tests import test_validation_scopes as fixtures


class ValidationScopeErrorTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.SharedValidationTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.data = self.fixture.data
        self.first = self.fixture.first
        self.second = self.fixture.second

    def assert_reports_match(self, run_id):
        expected = (validation.validate_store(self.data, run_id), validation.validate_store(self.data))
        with patch.object(validation.sqlite3, 'connect', wraps=sqlite3.connect) as connect:
            actual = validation.validate_store_scopes(self.data, run_id)
        self.assertEqual(connect.call_count, 2, 'Execution errors may reread the requested scope to preserve its report')
        self.assertEqual(actual, expected)

    def test_global_execution_error_keeps_requested_run_detail(self):
        with closing(sqlite3.connect(self.data / 'images.sqlite3')) as db:
            db.execute("UPDATE source_links SET manifest_json='[]'")
            db.commit()
        report = validation.validate_store(self.data, self.first)
        self.assertFalse(report['ok'])
        self.assertTrue(any(check['name'] == 'validation_execution' for check in report['checks']))
        self.assert_reports_match(self.first)

    def damage_second_run(self):
        with closing(sqlite3.connect(self.data / 'monitor.sqlite3')) as db:
            db.execute("UPDATE observations SET row_json='0' WHERE run_id=?", (self.second,))
            db.commit()

    def test_other_run_execution_error_does_not_pollute_healthy_scope(self):
        self.damage_second_run()
        self.assertTrue(validation.validate_store(self.data, self.first)['ok'])
        self.assertFalse(validation.validate_store(self.data)['ok'])
        self.assert_reports_match(self.first)

    def test_requested_run_execution_error_keeps_only_its_local_checks(self):
        self.damage_second_run()
        report = validation.validate_store(self.data, self.second)
        self.assertFalse(report['ok'])
        self.assertTrue(any(check['name'] == 'validation_execution' for check in report['checks']))
        self.assert_reports_match(self.second)


if __name__ == '__main__':
    unittest.main()
