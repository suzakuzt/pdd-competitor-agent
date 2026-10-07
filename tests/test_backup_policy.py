import json
from pathlib import Path
import tempfile
import unittest

from pdd_monitor.backup_policy import incremental_backup_enabled


class BackupPolicyTests(unittest.TestCase):
    def test_explicit_opt_in_and_fail_closed(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            self.assertFalse(incremental_backup_enabled(root))
            (root / 'state').mkdir()
            path = root / 'state/backup_policy.json'
            for mode, expected in [('full', False), ('incremental', True)]:
                path.write_text(json.dumps({'schema': 'pdd-backup-policy-v1', 'mode': mode}), encoding='utf-8')
                self.assertEqual(incremental_backup_enabled(root), expected)
            for value in ({'mode': 'incremental'}, {'schema': 'pdd-backup-policy-v1', 'mode': 'unknown'}, [], None):
                path.write_text(json.dumps(value), encoding='utf-8')
                with self.assertRaises(ValueError):
                    incremental_backup_enabled(root)
            path.write_text('{', encoding='utf-8')
            with self.assertRaises(ValueError):
                incremental_backup_enabled(root)
