"""Lifecycle/failure tests: only synthetic temporary projects, no website requests."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from pdd_monitor import capture_control as control
from pdd_monitor import collection_log as journal
from pdd_monitor.store import import_snapshot, initialize, shop_identity_evidence

URL = 'https://mobile.yangkeduo.com/mall_page.html?mall_id=9000000001'
SHOP = shop_identity_evidence({'sourceUrl': URL})['shop_id']


class CaptureControlAcceptance(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='pdd_capture_control_SYNTHETIC_')
        self.addCleanup(self.tmp.cleanup)
        self.project = Path(self.tmp.name)
        (self.project / 'AGENTS.md').write_text('SYNTHETIC TEST PROJECT', encoding='utf-8')
        initialize(self.project / 'data')

    def begin(self, **kwargs):
        return control.begin(self.project, source_url=URL, shop_name='SYNTHETIC TEST SHOP', **kwargs)

    def hashes(self):
        return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in (self.project / 'data').glob('*.sqlite3')}

    def session(self, job, phase='partial', **extra):
        path = Path(job['session_directory'])
        path.mkdir(parents=True)
        value = {'attemptId': job['attempt_id'], 'phase': phase, 'sessionId': 'SYNTHETIC',
                 'progress': {'batches': 1, 'cards': 20}, 'operation': None,
                 'retry': {'allowed': True}, 'importReady': True, **extra}
        (path / 'session.json').write_text(json.dumps(value), encoding='utf-8')
        return path

    def old_run(self, cards=2, status='complete', stamp='2026-10-04T00:00:00Z'):
        doc = {'shopName': 'SYNTHETIC TEST SHOP', 'sourceUrl': URL, 'sort': '上新',
               'observedFrom': stamp, 'observedTo': stamp, 'status': status,
               'endBoundaryObserved': status == 'complete',
               'rows': [{'viewOrder': i + 1, 'title': f'SYNTHETIC {i}', 'salesRaw': None,
                         'imageUrl': None} for i in range(cards)]}
        path = self.project / f'{cards}_{status}.json'
        path.write_text(json.dumps(doc), encoding='utf-8')
        return path, import_snapshot(self.project / 'data', path)

    def test_begin_keeps_token_private_and_does_not_create_browser_session(self):
        before = self.hashes(); job = self.begin()
        self.assertTrue(job['acquired']); self.assertNotIn('owner_token', json.dumps(job))
        self.assertFalse(Path(job['session_directory']).exists())
        private = control._read(control._job_dir(self.project, job['job_id']) / 'owner.private.json')
        self.assertTrue(private['owner_token'])
        self.assertEqual(self.hashes(), before)
        self.assertEqual(control.status(self.project, job['job_id'])['journal_status'], 'running')

    def test_two_starts_are_serialized_by_global_journal(self):
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: self.begin(), range(2)))
        self.assertEqual(sum(r['acquired'] for r in results), 1)
        self.assertEqual(next(r for r in results if not r['acquired'])['outcome'], 'collection_locked')

    def test_repeated_start_for_different_store_does_not_steal_lock(self):
        first = self.begin()
        second = control.begin(self.project, source_url=URL.replace('0001', '0002'), shop_name='SYNTHETIC B')
        self.assertFalse(second['acquired'])
        self.assertEqual(second['active_attempt_id'], first['attempt_id'])

    def test_unresolved_share_link_and_wrong_shop_are_rejected(self):
        for url, shop in [('https://mobile.yangkeduo.com/mall_page.html?ps=synthetic', None),
                          (URL, 'shop_' + 'a' * 24), ('https://example.invalid/?mall_id=1', None)]:
            with self.assertRaises(ValueError):
                control.begin(self.project, source_url=url, shop_id=shop, shop_name='SYNTHETIC')
        self.assertFalse((self.project / 'state/collection_attempts').exists())

    def test_fresh_retry_is_new_attempt_new_directory_and_preserves_partial(self):
        first = self.begin(); path = self.session(first)
        (path / 'raw-preserved.txt').write_text('SYNTHETIC ORIGINAL')
        control.abandon(self.project, first['job_id'], 'SYNTHETIC read interrupted; sealed prefix retained')
        second = self.begin(retry_job=first['job_id'])
        self.assertNotEqual(first['attempt_id'], second['attempt_id'])
        self.assertNotEqual(first['session_directory'], second['session_directory'])
        self.assertEqual(second['session_options']['parentSession']['directory'], str(path))
        self.assertEqual(second['fresh_retry_index'], 1)
        self.assertEqual((path / 'raw-preserved.txt').read_text(), 'SYNTHETIC ORIGINAL')

    def test_retry_does_not_override_running_attempt(self):
        job = self.begin()
        with self.assertRaises(ValueError): self.begin(retry_job=job['job_id'])
        self.assertEqual(control.status(self.project, job['job_id'])['journal_status'], 'running')

    def test_retry_cannot_change_shops(self):
        job = self.begin(); control.abandon(self.project, job['job_id'], 'SYNTHETIC zero card failure')
        with self.assertRaises(ValueError):
            control.begin(self.project, source_url=URL.replace('0001', '0002'), shop_name='B', retry_job=job['job_id'])

    def test_retry_is_bounded(self):
        one = self.begin(); control.abandon(self.project, one['job_id'], 'SYNTHETIC first failure')
        two = self.begin(retry_job=one['job_id']); control.abandon(self.project, two['job_id'], 'SYNTHETIC second failure')
        with self.assertRaises(ValueError): self.begin(retry_job=two['job_id'])
        with self.assertRaises(ValueError): self.begin(retry_job=one['job_id'])

    def test_cannot_abandon_live_session_or_operation(self):
        job = self.begin(); path = self.session(job, 'running')
        with self.assertRaises(ValueError): control.abandon(self.project, job['job_id'], 'test')
        state = control._read(path / 'session.json')
        state['phase'] = 'ready'; state['operation'] = None
        (path / 'session.json').write_text(json.dumps(state))
        with self.assertRaises(ValueError): control.abandon(self.project, job['job_id'], 'test')
        state = control._read(path / 'session.json'); state.update(phase='partial', operation={'kind': 'step'})
        (path / 'session.json').write_text(json.dumps(state))
        with self.assertRaises(ValueError): control.abandon(self.project, job['job_id'], 'test')

    def test_captured_complete_is_not_abandoned_as_incomplete(self):
        job = self.begin(); self.session(job, 'complete')
        with self.assertRaises(ValueError): control.abandon(self.project, job['job_id'], 'test')
        status = control.status(self.project, job['job_id'])
        self.assertFalse(status['accepted_into_history'])

    def test_status_never_returns_private_state(self):
        job = self.begin(); self.session(job, 'partial', owner={'token': 'SYNTHETIC SECRET'})
        result = control.status(self.project, job['job_id'])
        self.assertNotIn('SECRET', json.dumps(result)); self.assertNotIn('owner', json.dumps(result))
        self.assertTrue(result['website_collection_started'])

    def test_renew_returns_no_token_and_keeps_business_databases_unchanged(self):
        job = self.begin(); before = self.hashes()
        result = control.renew(self.project, job['job_id'])
        self.assertNotIn('owner_token', json.dumps(result)); self.assertEqual(before, self.hashes())

    def test_receipt_failure_closes_journal_with_failed_state(self):
        with patch.object(control, 'write_new_json', side_effect=OSError('SYNTHETIC disk write failed')):
            with self.assertRaises(OSError): self.begin()
        state = journal.get_status(self.project / 'state/collection_attempts')
        self.assertIsNone(state['active_lock']); self.assertEqual(state['attempts'][0]['status'], 'failed')

    def test_unknown_job_or_path_traversal_rejected(self):
        for name in ('../capture_bad', 'capture_' + 'a' * 32 + '/x', 'x'):
            with self.assertRaises(ValueError): control.status(self.project, name)

    def test_status_rejects_cross_attempt_session(self):
        job = self.begin(); self.session(job, attemptId='attempt_' + 'a' * 32)
        with self.assertRaises(ValueError): control.status(self.project, job['job_id'])
        with self.assertRaises(ValueError): control.abandon(self.project, job['job_id'], 'SYNTHETIC mismatch')
        state = journal.get_status(self.project / 'state/collection_attempts')
        self.assertEqual(state['attempts'][0]['status'], 'running')
        self.assertEqual(state['active_lock']['attempt_id'], job['attempt_id'])

    def test_plan_preserves_latest_partial_and_complete_reference(self):
        self.old_run(20)
        self.old_run(2, 'partial', '2026-10-04T01:00:00Z')
        before = self.hashes(); result = control.plan(self.project, SHOP)
        self.assertEqual(result['latest_card_count'], 2)
        self.assertEqual(result['reference_card_count'], 20)
        self.assertEqual(self.hashes(), before)
        self.assertFalse((self.project / 'state').exists())

    def test_begin_known_store_uses_own_source(self):
        self.old_run()
        result = control.begin(self.project, shop_id=SHOP)
        self.assertEqual(result['source_url'], URL)
        self.assertEqual(result['session_options']['shopName'], 'SYNTHETIC TEST SHOP')

    def migrated_job(self, job, saved):
        file = control._job_dir(self.project, job['job_id']) / 'job.json'
        value = control._read(file)
        value['session_directory'] = saved
        file.write_text(json.dumps(value), encoding='utf-8')
        return file

    def test_closed_migrated_receipt_reads_local_sources_without_rewriting(self):
        self.old_run(); job = self.begin(); self.session(job)
        control.abandon(self.project, job['job_id'], 'SYNTHETIC closed before migration')
        for root in (f'D:\\previous\\{self.project.name}', f'/old/{self.project.name}'):
            saved = root + '/sources/' + job['job_id']
            file = self.migrated_job(job, saved); original = file.read_bytes()
            result = control.plan(self.project, SHOP)
            self.assertTrue(result['jobs'][0]['migrated_receipt_read_only'])
            self.assertEqual(result['jobs'][0]['session']['progress']['cards'], 20)
            self.assertEqual(file.read_bytes(), original)
            for action in (lambda: control.renew(self.project, job['job_id']),
                           lambda: control.abandon(self.project, job['job_id'], 'again'),
                           lambda: self.begin(retry_job=job['job_id'])):
                with self.assertRaises(ValueError): action()

    def test_foreign_running_receipt_cannot_rebase_or_release_lock(self):
        job = self.begin()
        self.migrated_job(job, f'D:/old/{self.project.name}/sources/' + job['job_id'])
        with self.assertRaises(ValueError): control.status(self.project, job['job_id'])
        self.assertEqual(journal.get_status(self.project / 'state/collection_attempts')['active_lock']['attempt_id'], job['attempt_id'])

    def test_migration_does_not_accept_arbitrary_paths_or_other_jobs(self):
        job = self.begin(); control.abandon(self.project, job['job_id'], 'SYNTHETIC closed')
        for saved in (f'D:/old/other_project/sources/{job["job_id"]}',
                      f'D:/old/{self.project.name}/sources/capture_' + 'a' * 32,
                      f'D:/old/../{self.project.name}/sources/{job["job_id"]}',
                      f'sources/{job["job_id"]}'):
            self.migrated_job(job, saved)
            with self.assertRaises(ValueError): control.status(self.project, job['job_id'])

    def test_count_drop_is_review_required_even_with_valid_end(self):
        self.old_run(20)
        path, _ = self.old_run(2, 'complete', '2026-10-04T01:00:00Z')
        # Compare a fresh unimported input against the actual latest complete.
        doc = control._read(path); doc['rows'] = doc['rows'][:1]
        path.write_text(json.dumps(doc))
        with patch('pdd_monitor.capture_integrity.verify_capture', return_value={'valid': True}):
            result = control.check(self.project, path, SHOP)
        self.assertTrue(result['coverage_drop_requires_review'])
        self.assertFalse(result['ready_for_rehearsal'])

    def test_cannot_finish_unimported_or_old_snapshot(self):
        job = self.begin(); path = self.session(job, 'complete')
        old, imported = self.old_run()
        doc = control._read(old)
        doc['collectionEvidence'] = {'attemptId': job['attempt_id']}
        raw = json.dumps(doc).encode()
        (path / 'snapshot.json').write_bytes(raw)
        state = control._read(path / 'session.json')
        state['snapshot'] = {'file': 'snapshot.json', 'sha256': hashlib.sha256(raw).hexdigest(), 'status': 'complete'}
        (path / 'session.json').write_text(json.dumps(state))
        with patch.object(control, 'check', return_value={'valid': True, 'status': 'passed'}):
            with self.assertRaises(ValueError): control.finish(self.project, job['job_id'], imported['run_id'])
        self.assertEqual(control.status(self.project, job['job_id'])['journal_status'], 'running')

    def test_finish_selects_recovered_partial_not_uncommitted_old_complete(self):
        job = self.begin(); path = self.session(job, 'partial')
        (path / 'snapshot.json').write_text('{"status":"complete"}')
        doc = {'status': 'partial', 'collectionEvidence': {'attemptId': job['attempt_id'], 'stopReason': 'interrupted_session'}}
        raw = json.dumps(doc).encode(); (path / 'snapshot.recovered.json').write_bytes(raw)
        state = control._read(path / 'session.json')
        state['snapshot'] = {'file': 'snapshot.recovered.json', 'sha256': hashlib.sha256(raw).hexdigest(), 'status': 'partial'}
        (path / 'session.json').write_text(json.dumps(state))
        with patch.object(control, 'check', return_value={'valid': True, 'status': 'passed'}), patch.object(journal, 'finish_attempt') as finish:
            control.finish(self.project, job['job_id'], 'run_SYNTHETIC')
        self.assertEqual(finish.call_args.kwargs['snapshot_path'].name, 'snapshot.recovered.json')
        self.assertEqual(finish.call_args.kwargs['status'], 'partial')

    def test_review_required_session_not_blindly_retried(self):
        job = self.begin(); self.session(job, 'needs_login', retry={'allowed': False})
        control.abandon(self.project, job['job_id'], 'SYNTHETIC login expired')
        with self.assertRaises(ValueError): self.begin(retry_job=job['job_id'])


if __name__ == '__main__':
    unittest.main()
