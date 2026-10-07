"""Optional attempt shop identity; synthetic journals and snapshots only."""
from contextlib import redirect_stdout, closing
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from pdd_monitor import collection_log as journal
from pdd_monitor.shop_tracking import target_tracking_fields
from pdd_monitor.store import import_snapshot

A = 'shop_' + 'a' * 24
B = 'shop_' + 'b' * 24

class CollectionShopBindingAcceptance(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='pdd_attempt_shop_synthetic_')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.state = self.root / 'state'
        self.clock = patch.object(journal, '_now', return_value=datetime(2026, 10, 4, 0, 0, tzinfo=timezone.utc))
        self.now = self.clock.start(); self.addCleanup(self.clock.stop)

    def begin(self, **kwargs):
        return journal.begin_attempt(self.state, trigger_kind='manual', owner_label='SYNTHETIC', **kwargs)

    def finish(self, result, **kwargs):
        return journal.finish_attempt(self.state, result['attempt']['attempt_id'], result['owner_token'], **kwargs)

    def sop(self, shop, attempts):
        return target_tracking_fields({'shop_id': shop, 'status': 'observed'}, [], attempts=attempts)

    def test_invalid_shop_id_fails_before_any_write(self):
        for value in ('', True, 1, [], {}, 'shop_' + 'a' * 23, 'shop_' + 'A' * 24, '../shop_' + 'a' * 24):
            with self.subTest(value=value), self.assertRaises(journal.CollectionLogError):
                self.begin(shop_id=value)
            self.assertFalse(self.state.exists())

    def test_legacy_call_stays_global_and_global_lock_behavior_unchanged(self):
        old = self.begin()
        self.assertIsNone(old['attempt']['shop_id'])
        self.assertEqual(old['attempt']['evidence'], {})
        self.assertNotIn('shop_id', old['attempt']['events'][0]['payload'])
        self.assertEqual(self.begin(shop_id=A)['outcome'], 'collection_locked')
        for shop in (A, B):
            self.assertEqual(self.sop(shop, [old['attempt']])['sop_stage'], 'awaiting_first_complete')
        finished = self.finish(old, status='failed', reason='SYNTHETIC failure')['attempt']
        self.assertIsNone(finished['shop_id'])
        self.assertEqual(finished['evidence'], {})
        with closing(sqlite3.connect(self.state / 'journal.sqlite3')) as connection:
            self.assertEqual(connection.execute('PRAGMA user_version').fetchone()[0], 2)
            self.assertNotIn('shop_id', [row[1] for row in connection.execute('PRAGMA table_info(attempts)')])

    def test_public_projection_begin_event_and_zero_card_failure_keep_shop(self):
        started = self.begin(shop_id=A)
        attempt = started['attempt']
        self.assertEqual(attempt['shop_id'], A)
        self.assertEqual(attempt['evidence']['shop_id'], A)
        self.assertEqual(attempt['events'][0]['payload']['shop_id'], A)
        persisted = json.loads(Path(attempt['attempt_json']).read_text(encoding='utf-8'))
        self.assertEqual(persisted['shop_id'], A)
        self.assertNotIn('owner_token', persisted)
        self.assertEqual(self.sop(A, [attempt])['sop_stage'], 'collection_running')
        self.assertEqual(self.sop(B, [attempt])['sop_stage'], 'awaiting_first_complete')
        failed = self.finish(started, status='failed', reason='SYNTHETIC no cards')['attempt']
        self.assertIsNone(failed['run_id'])
        self.assertEqual(failed['shop_id'], A)
        self.assertEqual(failed['evidence']['shop_id'], A)
        self.assertEqual(self.sop(A, [failed])['sop_stage'], 'collection_failed')
        self.assertEqual(self.sop(B, [failed])['sop_stage'], 'awaiting_first_complete')
        self.assertEqual(journal.get_status(self.state)['attempts'][0]['shop_id'], A)

    def test_recovery_retains_declared_shop(self):
        started = self.begin(shop_id=B)
        recovered = journal.recover_attempt(self.state, started['attempt']['attempt_id'],
            reason='SYNTHETIC abandon', confirm_abandon=True, owner_token=started['owner_token'])
        self.assertEqual(recovered['shop_id'], B)
        self.assertEqual(recovered['evidence']['shop_id'], B)

    def snapshot(self, status):
        path = self.root / f'{status}.json'
        path.write_text(json.dumps({'synthetic': True, 'shopName': 'SYNTHETIC run shop',
            'sourceUrl': 'https://example.invalid/mall?mall_id=' + ('8080' if status == 'complete' else '8081'), 'observedFrom': '2026-10-04T00:01:00Z',
            'observedTo': '2026-10-04T00:02:00Z', 'status': status, 'endBoundaryObserved': status == 'complete',
            'rows': [{'viewOrder': 1, 'title': 'SYNTHETIC original card', 'salesRaw': None}]}), encoding='utf-8')
        imported = import_snapshot(self.root / 'data', path)
        return path, imported

    def test_complete_and_partial_cannot_finish_with_foreign_shop_run(self):
        for status in ('complete', 'partial'):
            with self.subTest(status=status):
                self.state = self.root / status
                self.now.return_value = datetime(2026, 10, 4, 0, 0, tzinfo=timezone.utc)
                started = self.begin(shop_id=A)
                snapshot, imported = self.snapshot(status)
                self.now.return_value = datetime(2026, 10, 4, 0, 3, tzinfo=timezone.utc)
                before = hashlib.sha256((self.state / 'journal.sqlite3').read_bytes()).hexdigest()
                with self.assertRaisesRegex(journal.CollectionLogError, 'shop_id conflicts'):
                    self.finish(started, status=status, run_id=imported['run_id'], snapshot_path=snapshot,
                        data_dir=self.root / 'data', reason='SYNTHETIC partial' if status == 'partial' else None)
                self.assertEqual(before, hashlib.sha256((self.state / 'journal.sqlite3').read_bytes()).hexdigest())
                self.assertEqual(journal.get_status(self.state)['attempts'][0]['status'], 'running')
                self.assertIsNotNone(journal.get_status(self.state)['active_lock'])

    def test_matching_run_is_accepted_and_retains_declared_shop(self):
        snapshot, imported = self.snapshot('complete')
        with closing(sqlite3.connect(self.root / 'data/monitor.sqlite3')) as connection:
            shop_id = connection.execute('SELECT shop_id FROM runs WHERE run_id=?', (imported['run_id'],)).fetchone()[0]
        started = self.begin(shop_id=shop_id)
        self.now.return_value = datetime(2026, 10, 4, 0, 3, tzinfo=timezone.utc)
        finished = self.finish(started, status='complete', run_id=imported['run_id'], snapshot_path=snapshot,
            data_dir=self.root / 'data')['attempt']
        self.assertEqual(finished['shop_id'], shop_id)
        self.assertEqual(finished['status'], 'complete')

    def test_cli_optional_shop_id_and_invalid_id_leave_no_state(self):
        stream = io.StringIO()
        with redirect_stdout(stream):
            code = journal.main(['--state-dir', str(self.state), 'begin', '--manual', '--owner-label', 'SYNTHETIC', '--shop-id', B])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(stream.getvalue())['attempt']['shop_id'], B)
        absent = self.root / 'invalid_cli'
        with redirect_stdout(io.StringIO()):
            code = journal.main(['--state-dir', str(absent), 'begin', '--manual', '--owner-label', 'SYNTHETIC', '--shop-id', 'bad'])
        self.assertEqual(code, 2)
        self.assertFalse(absent.exists())

if __name__ == '__main__':
    unittest.main(verbosity=2)
