"""Synthetic onboarding through the real queue and intake verifier; no browser/DB.

The intake CLI uses a separate interpreter only to load the fixture project's
module paths, preserving record_verified_intake's real project-origin check.
"""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from pdd_monitor.collection_service import CollectionService, atomic, stamp
from pdd_monitor.competitor_registry import register_target, _read_target, _target
from pdd_monitor.shop_onboarding import read_evidence
from tests.test_local_collection import sealed_result

SOURCE = Path(__file__).resolve().parents[1]
SHARE = 'https://mobile.yangkeduo.com/mall_page.html?ps=SYNTHETIC_ONBOARD'
STORE = 'https://mobile.yangkeduo.com/mall_page.html?mall_id=1234567'
REAL_RUN = subprocess.run


class FixtureService(CollectionService):
    def _shop(self, shop_id):
        rows = [_read_target(path) for path in (self.project / 'state/competitors/targets').glob('*.json')]
        rows = [row for row in rows if row['shop_id'] == shop_id]
        if len(rows) != 1:
            raise ValueError('Unknown fixture shop')
        return {'shop_name': rows[0]['display_name'], 'source_url': rows[0]['source_url']}


class ShopOnboardingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='pdd_onboarding_SYNTHETIC_')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        shutil.copytree(SOURCE / 'pdd_monitor', self.root / 'pdd_monitor', ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
        (self.root / 'AGENTS.md').write_text('Synthetic isolated intake fixture', encoding='utf-8')
        self.target = register_target(self.root, None, SHARE)['target']
        self.target_path = self.root / 'state/competitors/targets' / (self.target['target_id'] + '.json')
        self.original_target = self.target_path.read_bytes()
        self.clock = lambda: datetime.now(timezone.utc) - timedelta(seconds=1)
        self.calls, self.publishes = [], []
        self.result_override = None
        self.evidence_patch = {}
        self.process_returncode = 0
        self.skip_public_page = False
        self.after_probe = None
        self.service = self.make_service()
        intake = patch('pdd_monitor.shop_onboarding.record_intake', side_effect=self.record_fixture_intake)
        intake.start(); self.addCleanup(intake.stop)

    def make_service(self):
        return FixtureService(self.root, runner=self.runner, publisher=lambda *_: self.publishes.append(True), clock=self.clock, autostart=False)

    def record_fixture_intake(self, project, evidence_path, original_url, expected_shop_id):
        result = REAL_RUN([sys.executable, '-B', '-X', 'utf8', str(SOURCE / 'scripts/record_verified_intake.py'),
                          '--project', str(project), '--evidence', str(evidence_path), '--original-url', original_url,
                          '--expected-shop-id', expected_shop_id], capture_output=True, text=True, encoding='utf-8')
        data = json.loads(result.stdout)
        if result.returncode:
            raise ValueError(data.get('message', 'Fixture intake failed'))
        return data

    def evidence(self):
        return {'userShareUrl': SHARE, 'shopName': '合成核验店', 'sourceUrl': STORE,
                'header': '搜索店铺商品\n合成核验店\n全部商品', 'selectedMarkup': '<li class="current_test">上新</li>',
                'observedAt': datetime.now(timezone.utc).isoformat(), **self.evidence_patch}

    def runner(self, job):
        self.calls.append(deepcopy(job))
        if job['kind'] == 'probe':
            directory = self.root / 'state/local_collection' / job['id']
            directory.mkdir(parents=True, exist_ok=True)
            result = self.result_override or {'status': 'complete', 'intakeEvidence': self.evidence(),
                                              'website_collection_performed': False, 'website_page_read_performed': True}
            atomic(directory / 'browser_result.json', result)
            atomic(directory / 'result.json', result)
            if not self.skip_public_page:
                atomic(directory / 'public_page.json', {'url': result.get('intakeEvidence', {}).get('sourceUrl'), 'text': '合成公开店铺页面'})
            with patch('pdd_monitor.collection_service.subprocess.run', return_value=SimpleNamespace(returncode=self.process_returncode)):
                verified = self.service._execute(job)
            if self.after_probe:
                self.after_probe(job, directory, verified)
            return verified
        if self.result_override:
            return deepcopy(self.result_override)
        return sealed_result({'status': 'complete', 'message': '合成全店与主图已保存', 'dashboard_built': True,
            'receipt': {'ok': True, 'status': 'finished', 'snapshot_status': 'complete', 'shop_id': job['shop_id'],
                        'new_run': {'status': 'complete', 'cards': 2, 'image_refs': 2}}})

    def start(self):
        code, row = self.service.onboard({'target_id': self.target['target_id']})
        self.assertEqual(code, 202)
        return row

    def status(self, row):
        return self.service.onboard_status(id=row['id'])

    def test_real_intake_stops_ready_and_only_explicit_collect_enters_shop_queue(self):
        row = self.start()
        self.assertEqual(self.calls, [])
        self.assertEqual(row['stage'], 'verify_storefront')
        self.assertTrue(self.service.state['jobs'][0]['verify_storefront'])
        self.assertEqual(self.service.onboard({'target_id': self.target['target_id']})[1]['id'], row['id'])
        self.service.tick()
        checking = self.status(row)
        self.assertEqual(checking['stage'], 'ready')
        self.assertEqual(checking['status'], 'ready')
        self.assertNotIn('collection_job_id', checking)
        self.assertEqual(len(self.service.state['jobs']), 1)
        self.assertEqual(checking['shop_id'], _target('合成核验店', STORE)['shop_id'])
        self.assertNotEqual(checking['resolved_target_id'], self.target['target_id'])
        self.assertEqual([job['kind'] for job in self.calls], ['probe'])
        self.assertEqual(self.target_path.read_bytes(), self.original_target)
        self.assertEqual(self.publishes, [], 'Registration must not trigger a full dashboard build')
        self.service.tick()
        self.assertEqual([job['kind'] for job in self.calls], ['probe'])
        self.service = self.make_service()
        self.assertEqual(self.status(row)['status'], 'ready')
        code, collecting = self.service.onboard_collect({'id': row['id']})
        self.assertEqual(code, 202)
        self.assertEqual(collecting['stage'], 'collect_shop')
        self.assertEqual(self.service.state['jobs'][1]['entry_url'], SHARE)
        self.assertEqual(self.service.onboard_collect({'id': row['id']})[1]['collection_job_id'], collecting['collection_job_id'])
        self.service.tick()
        finished = self.status(row)
        self.assertEqual(finished['status'], 'complete')
        self.assertTrue(finished['job']['dashboard_built'])
        self.assertEqual([job['kind'] for job in self.calls], ['probe', 'shop'])
        self.service = self.make_service()
        code, repeated = self.service.onboard({'target_id': self.target['target_id']})
        self.assertEqual(code, 200); self.assertEqual(repeated['id'], row['id'])
        self.service.tick(); self.assertEqual(len(self.calls), 2)
        self.assertFalse((self.root / 'data').exists())

    def test_legacy_probe_never_registers_or_chains_and_cannot_request_verification(self):
        plain = self.service.probe({'url': SHARE})[1]['job']
        with self.assertRaises(ValueError):
            self.service.probe({'url': SHARE, 'verify_storefront': True})
        onboard = self.start()
        self.assertNotEqual(plain['id'], onboard['verification_job_id'])
        self.service.tick()
        self.assertEqual(len(list((self.root / 'state/competitors/targets').glob('*.json'))), 1)
        self.assertEqual([job['kind'] for job in self.service.state['jobs']], ['probe', 'probe'])

    def test_direct_url_registers_without_build_and_repeats_the_original_pending_target(self):
        code, first = self.service.onboard({'name': None, 'url': SHARE})
        self.assertEqual(code, 202); self.assertEqual(first['target_id'], self.target['target_id'])
        code, repeated = self.service.onboard({'name': '新的界面备注', 'url': SHARE})
        self.assertEqual(repeated['id'], first['id'])
        self.assertEqual(self.target_path.read_bytes(), self.original_target)
        self.assertEqual(len(self.service.state['jobs']), 1)
        self.assertEqual(self.publishes, [])

    def test_same_name_different_explicit_links_have_independent_targets_and_jobs(self):
        first = self.service.onboard({'name': '合成同名店', 'url': SHARE})[1]
        other_url = 'https://mobile.yangkeduo.com/mall_page.html?ps=SYNTHETIC_OTHER'
        second = self.service.onboard({'name': '合成同名店', 'url': other_url})[1]
        self.assertNotEqual(first['target_id'], second['target_id'])
        self.assertNotEqual(first['verification_job_id'], second['verification_job_id'])
        self.assertEqual([job['url'] for job in self.service.state['jobs']], [SHARE, other_url])
        self.assertEqual(self.calls, [])

    def test_direct_url_rejects_invalid_or_mixed_fields_before_registration(self):
        cases = [
            {'name': None, 'url': SHARE, 'target_id': self.target['target_id']},
            {'name': 5, 'url': SHARE}, {'name': None, 'url': None},
            {'name': 'n' * 201, 'url': SHARE}, {'name': None, 'url': 'x' * 4097},
            {'name': 'name-only', 'url': ''}, {'name': None, 'url': 'https://example.com/mall_page.html?ps=test'},
            {'name': None, 'url': 'https://mobile.yangkeduo.com/goods.html?goods_id=1'},
        ]
        for payload in cases:
            with self.subTest(payload=payload), self.assertRaises(ValueError): self.service.onboard(payload)
        self.assertEqual(len(list((self.root / 'state/competitors/targets').glob('*.json'))), 1)
        self.assertEqual(self.service.state['jobs'], [])

    def test_probe_failures_missing_files_or_nonzero_process_never_chain(self):
        for case in ('login', 'cancelled', 'missing_evidence', 'missing_page', 'nonzero'):
            with self.subTest(case=case):
                self.service.state.pop('onboardings', None); self.service.state['jobs'] = []; self.service._save()
                self.result_override = {'status': 'needs_login', 'reason': 'login_required'} if case == 'login' else {'status': 'cancelled'} if case == 'cancelled' else {'status': 'complete'} if case == 'missing_evidence' else None
                self.skip_public_page = case == 'missing_page'
                self.process_returncode = 1 if case == 'nonzero' else 0
                row = self.start(); self.service.tick()
                self.assertIn(self.status(row)['status'], ('needs_login', 'cancelled', 'manual_review'))
                self.assertNotIn('collection_job_id', self.status(row))
                self.assertEqual(len(self.service.state['jobs']), 1)
                self.assertEqual(len(list((self.root / 'state/competitors/targets').glob('*.json'))), 1)

    def test_full_identity_evidence_rejects_wrong_share_header_sort_stale_and_untyped_url(self):
        bad = [
            {'userShareUrl': SHARE + 'X'}, {'header': '其他店铺'},
            {'selectedMarkup': '<li class="current_test">销量</li>'},
            {'observedAt': '2000-01-01T00:00:00Z'}, {'sourceUrl': SHARE},
            {'observedAt': None},
        ]
        for change in bad:
            with self.subTest(change=change):
                self.service.state.pop('onboardings', None); self.service.state['jobs'] = []; self.service._save()
                self.evidence_patch = change
                row = self.start(); self.service.tick()
                self.assertEqual(self.status(row)['status'], 'manual_review')
                self.assertNotIn('collection_job_id', self.status(row))
                self.assertEqual(len(list((self.root / 'state/competitors/targets').glob('*.json'))), 1)

    def test_stable_original_cannot_resolve_to_a_different_typed_shop(self):
        self.target = register_target(self.root, '原稳定店', 'https://mobile.yangkeduo.com/mall_page.html?mall_id=888')['target']
        self.evidence_patch = {'userShareUrl': self.target['source_url']}
        row = self.start(); self.service.tick()
        self.assertEqual(self.status(row)['status'], 'manual_review')
        self.assertFalse(any(job['kind'] == 'shop' for job in self.service.state['jobs']))

    def test_tampering_result_or_public_page_after_worker_receipt_prevents_registration(self):
        for filename in ('browser_result.json', 'public_page.json'):
            with self.subTest(filename=filename):
                self.service.state.pop('onboardings', None); self.service.state['jobs'] = []; self.service._save()
                self.after_probe = lambda job, directory, result: (directory / filename).write_text('{}', encoding='utf-8')
                row = self.start(); self.service.tick()
                self.assertEqual(self.status(row)['status'], 'manual_review')
                self.assertNotIn('collection_job_id', self.status(row))

    def test_registration_mutation_is_not_accepted_as_the_original_request(self):
        row = self.start()
        original = json.loads(self.target_path.read_text(encoding='utf-8'))
        original['display_name'] = '事后修改的备注'
        atomic(self.target_path, original)
        self.service.tick()
        self.assertEqual(self.status(row)['status'], 'manual_review')
        self.assertEqual(self.calls, [])

    def test_cancel_queued_probe_prevents_any_browser_work(self):
        row = self.start()
        code, state = self.service.onboard_cancel({'id': row['id']})
        self.assertEqual(code, 200); self.assertEqual(state['status'], 'cancelled')
        self.service.tick(); self.assertEqual(self.calls, [])
        self.assertEqual(self.service.state['jobs'][0]['status'], 'cancelled')

    def test_cancel_running_probe_waits_for_worker_and_does_not_chain(self):
        entered, release = threading.Event(), threading.Event()
        original_runner = self.runner
        def blocked(job):
            entered.set(); self.assertTrue(release.wait(3)); return original_runner(job)
        self.service.runner = blocked
        row = self.start(); self.service.tick(background=True)
        self.assertTrue(entered.wait(3))
        self.service.onboard_cancel({'id': row['id']})
        pending = self.status(row)
        self.assertTrue(pending['cancel_requested']); self.assertIsNotNone(self.service.active)
        self.assertNotEqual(pending['status'], 'cancelled')
        release.set()
        for _ in range(100):
            if self.service.active is None:
                break
            threading.Event().wait(0.01)
        self.assertIsNone(self.service.active)
        self.assertEqual(self.status(row)['status'], 'cancelled')
        self.assertNotIn('collection_job_id', self.status(row))

    def test_restart_interrupted_probe_stops_without_replaying_browser(self):
        row = self.start()
        self.service.state['jobs'][0].update(status='running', started_at=stamp(self.clock()))
        self.service._save(); self.service = self.make_service(); self.service.tick()
        self.assertEqual(self.status(row)['status'], 'interrupted')
        self.assertEqual(self.calls, [])

    def test_crash_after_probe_enqueue_recovers_child_id_without_second_probe(self):
        row = self.start(); saved = self.service.state['onboardings'][0]
        del saved['verification_job_id']; self.service._save()
        self.service = self.make_service(); self.service.tick()
        self.assertEqual(len([j for j in self.service.state['jobs'] if j['kind'] == 'probe']), 1)
        self.assertEqual(self.status(row)['stage'], 'ready')

    def test_crash_after_shop_enqueue_recovers_child_id_and_completed_result(self):
        row = self.start(); self.service.tick(); self.service.onboard_collect({'id': row['id']}); self.service.tick()
        saved = self.service.state['onboardings'][0]
        saved.update(status='running', stage='register_storefront'); saved.pop('collection_job_id')
        self.service._save(); self.service = self.make_service(); self.service.tick()
        self.assertEqual(self.status(row)['status'], 'complete')
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(len([j for j in self.service.state['jobs'] if j['kind'] == 'shop']), 1)

    def test_collection_stop_and_unconfirmed_complete_are_not_reported_as_success(self):
        row = self.start(); self.service.tick()
        self.service.onboard_collect({'id': row['id']})
        self.result_override = {'status': 'complete', 'message': 'unconfirmed synthetic claim'}
        self.service.tick()
        self.assertEqual(self.status(row)['status'], 'manual_review')
        self.assertEqual(self.status(row)['reason'], 'release_receipt_unconfirmed')

    def test_status_has_no_cross_target_fallback_and_no_probe_body(self):
        other = register_target(self.root, '仅名称目标', None)['target']
        self.assertEqual(self.service.onboard_status(target_id=other['target_id'])['status'], 'idle')
        row = self.start(); self.service.tick()
        public = self.status(row)
        self.assertEqual(public['target_id'], self.target['target_id'])
        self.assertNotIn('intakeEvidence', public['job']); self.assertNotIn('page', public['job'])
        with self.assertRaises(ValueError): self.service.onboard_status(id='onboard_' + 'f' * 32)
        with self.assertRaises(ValueError): self.service.onboard_status(target_id='target_' + 'f' * 24)
        with self.assertRaises(ValueError): self.service.onboard({'target_id': other['target_id']})
        with self.assertRaises(ValueError): self.service.onboard({'target_id': self.target['target_id'], 'shop_id': 'untrusted'})
        with self.assertRaises(ValueError): self.service.onboard_status(id=row['id'], target_id=self.target['target_id'])

    def test_public_status_reports_actual_worker_progress_without_exposing_source_body(self):
        row = self.start(); child = self.service.state['jobs'][0]
        child.update(status='running', started_at=stamp(self.clock()))
        directory = self.root / 'state/local_collection' / child['id']
        atomic(directory / 'progress.json', {'phase': '正在读取店铺身份', 'stage': 'verify_storefront'})
        public = self.status(row)
        self.assertEqual(public['job']['status'], 'running')
        self.assertEqual(public['job']['progress']['phase'], '正在读取店铺身份')
        self.assertNotIn('original_url', public)

    def test_saved_collection_with_failed_publication_is_not_onboarding_complete(self):
        row = self.start(); self.service.tick()
        self.service.onboard_collect({'id': row['id']})
        capture = self.service.state['jobs'][-1]
        self.result_override = {**self.runner(capture), 'dashboard_built': False, 'reason': 'dashboard_build_failed'}
        self.service.tick()
        current = self.status(row)
        self.assertEqual(current['status'], 'manual_review')
        self.assertEqual(current['reason'], 'dashboard_build_failed')
        self.assertEqual(current['job']['status'], 'complete')
        self.assertFalse(current['job']['dashboard_built'])

    def test_partial_images_preserve_partial_and_original_image_counts(self):
        row = self.start(); self.service.tick()
        self.service.onboard_collect({'id': row['id']})
        capture = self.service.state['jobs'][-1]
        partial = self.runner(capture)
        partial.update(status='partial', reason='images_missing', image_summary={'total': 2, 'saved': 1, 'missing': 1, 'status': 'partial'})
        partial['receipt']['new_run']['image_refs'] = 1
        self.result_override = partial
        self.service.tick()
        current = self.status(row)
        self.assertEqual(current['status'], 'partial')
        self.assertEqual(current['job']['image_summary']['missing'], 1)

    def test_cancel_queued_collection_preserves_intake_without_starting_capture(self):
        row = self.start(); self.service.tick()
        self.service.onboard_collect({'id': row['id']})
        current = self.status(row)
        self.assertIn('resolved_target_id', current)
        self.service.onboard_cancel({'id': row['id']}); self.service.tick()
        self.assertEqual(self.status(row)['status'], 'cancelled')
        self.assertEqual([job['kind'] for job in self.calls], ['probe'])
        self.assertTrue(list((self.root / 'state/competitors/intakes').glob('*.json')))

    def test_existing_independent_shop_job_is_not_adopted_or_duplicated(self):
        row = self.start(); self.service.tick()
        self.service.onboard_collect({'id': row['id']})
        capture = self.service.state['jobs'][-1]
        parent = self.service.state['onboardings'][0]
        # Simulate an independent same-shop job before the enqueue boundary.
        capture.pop('onboarding_id'); parent.pop('collection_job_id'); parent['stage'] = 'register_storefront'
        self.service._save()
        with self.service.lock:
            self.service.onboarding.advance()
        current = self.status(row)
        self.assertEqual(current['status'], 'manual_review')
        self.assertEqual(current['reason'], 'collection_not_queued')
        self.assertEqual(len([job for job in self.service.state['jobs'] if job['kind'] == 'shop']), 1)
        self.assertEqual(capture['status'], 'queued', 'An unrelated explicit job must not be cancelled')

    def test_completed_probe_on_restart_continues_once_from_hashed_evidence(self):
        row = self.start(); child = self.service.state['jobs'][0]
        child.update(status='running', started_at=stamp(self.clock()))
        child.update(self.runner(deepcopy(child)), ended_at=stamp(self.clock()))
        self.service._save(); self.service = self.make_service()
        self.service.tick()
        self.assertEqual(self.status(row)['status'], 'ready')
        self.assertEqual([job['kind'] for job in self.calls], ['probe'])
        self.service.tick(); self.assertEqual(len(self.calls), 1)

    def test_only_explicit_retry_creates_new_attempt_after_login_failure(self):
        self.result_override = {'status': 'needs_login', 'reason': 'login_required', 'message': '合成登录暂停'}
        row = self.start(); self.service.tick()
        old = deepcopy(self.service.state['onboardings'][0])
        self.assertEqual(self.service.onboard({'target_id': self.target['target_id']})[1]['id'], row['id'])
        self.service = self.make_service()
        self.assertEqual(self.service.onboard_status(target_id=self.target['target_id'])['id'], row['id'])
        code, retry = self.service.onboard({'target_id': self.target['target_id'], 'retry': True})
        self.assertEqual(code, 202); self.assertNotEqual(retry['id'], row['id'])
        self.assertEqual(retry['retry_of'], row['id'])
        self.assertEqual(self.service.state['onboardings'][0], old)
        self.assertEqual(self.service.onboard_status(target_id=self.target['target_id'])['id'], retry['id'])
        self.assertEqual(self.service.onboard({'name': None, 'url': SHARE})[1]['id'], retry['id'])
        self.result_override = None
        self.service.tick(); self.service.tick()
        self.assertEqual(self.status(retry)['status'], 'ready')
        self.assertEqual(self.status(row)['status'], 'needs_login')

    def test_retry_refuses_active_or_complete_attempts_and_nonboolean_permission(self):
        row = self.start()
        self.assertEqual(self.service.onboard({'target_id': self.target['target_id'], 'retry': True})[0], 409)
        for retry in (False, 1, 'true', None):
            with self.subTest(retry=retry), self.assertRaises(ValueError):
                self.service.onboard({'target_id': self.target['target_id'], 'retry': retry})
        self.service.tick(); self.service.tick()
        self.assertEqual(self.status(row)['status'], 'ready')
        self.assertEqual(self.service.onboard({'target_id': self.target['target_id'], 'retry': True})[0], 409)
        self.assertEqual(len(self.service.state['onboardings']), 1)

    def test_retry_waits_for_every_original_child_even_after_parent_terminal(self):
        row = self.start()
        self.service.state['onboardings'][0]['status'] = 'manual_review'
        self.service.state['jobs'][0]['status'] = 'running'
        code, denied = self.service.onboard({'target_id': self.target['target_id'], 'retry': True})
        self.assertEqual(code, 409); self.assertEqual(denied['id'], row['id'])
        self.assertEqual(len(self.service.state['onboardings']), 1)

    def test_explicit_retry_after_build_failure_keeps_saved_receipt_and_old_attempt(self):
        row = self.start(); self.service.tick()
        self.service.onboard_collect({'id': row['id']})
        capture = self.service.state['jobs'][-1]
        self.result_override = {**self.runner(capture), 'dashboard_built': False, 'reason': 'dashboard_build_failed'}
        self.service.tick()
        saved = deepcopy(capture)
        code, retry = self.service.onboard({'target_id': self.target['target_id'], 'retry': True})
        self.assertEqual(code, 202); self.assertEqual(retry['retry_of'], row['id'])
        self.assertEqual(capture, saved)
        self.assertEqual(self.status(row)['reason'], 'dashboard_build_failed')
        self.assertEqual(len([job for job in self.service.state['jobs'] if job['kind'] == 'shop']), 1)

    def test_simultaneous_same_link_requests_enqueue_one_verification(self):
        barrier = threading.Barrier(2)
        outcomes, errors = [], []
        def submit():
            try:
                barrier.wait(3)
                outcomes.append(self.service.onboard({'name': None, 'url': SHARE}))
            except Exception as error:
                errors.append(error)
        threads = [threading.Thread(target=submit) for _ in range(2)]
        for thread in threads: thread.start()
        for thread in threads: thread.join(3)
        self.assertEqual(errors, []); self.assertEqual(len(outcomes), 2)
        self.assertEqual(outcomes[0][1]['id'], outcomes[1][1]['id'])
        self.assertEqual(len(self.service.state['jobs']), 1)
        self.assertEqual(len(self.service.state['onboardings']), 1)

    def test_failed_verification_enqueue_leaves_no_runnable_orphan(self):
        original_save, saves = self.service._save, []
        def fail_second_save():
            saves.append(True)
            if len(saves) == 2:
                raise OSError('SYNTHETIC queue save denied')
            original_save()
        with patch.object(self.service, '_save', side_effect=fail_second_save):
            row = self.start()
        self.assertEqual(self.status(row)['status'], 'manual_review')
        self.assertEqual(self.service.state['jobs'], [])
        self.service.tick(); self.assertEqual(self.calls, [])
        self.service = self.make_service()
        self.assertEqual(self.status(row)['status'], 'manual_review')

    def test_legacy_pending_workflow_does_not_chain_without_new_explicit_click(self):
        row = self.start()
        self.service.state['onboardings'][0].pop('mode')
        self.service._save(); self.service = self.make_service()
        self.service.tick(); self.service.tick()
        self.assertEqual(self.status(row)['status'], 'ready')
        self.assertEqual([job['kind'] for job in self.service.state['jobs']], ['probe'])
        self.assertFalse((self.root / 'data').exists())

    def test_collect_rejects_unknown_ambiguous_and_not_ready_scope(self):
        row = self.start()
        for payload in ({}, {'id': 'onboard_' + 'f' * 32}, {'target_id': self.target['target_id']},
                        {'id': row['id'], 'shop_id': 'shop_' + 'f' * 24}, {'id': None}):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                self.service.onboard_collect(payload)
        self.assertEqual(self.service.onboard_collect({'id': row['id']})[0], 409)
        self.assertEqual(len(self.service.state['jobs']), 1)

    def test_internal_start_cannot_bypass_explicit_collection_marker(self):
        row = self.start(); self.service.tick()
        ready = self.status(row)
        with self.assertRaises(ValueError):
            self.service.start({'shop_id': ready['shop_id'], 'kind': 'shop', 'entry_url': SHARE}, onboarding_id=row['id'])
        self.assertEqual([job['kind'] for job in self.service.state['jobs']], ['probe'])

    def test_collect_revalidates_original_target_and_probe_evidence(self):
        row = self.start(); self.service.tick()
        result = self.service.directory / row['verification_job_id'] / 'browser_result.json'
        result.write_text('{}', encoding='utf-8')
        with self.assertRaises(ValueError): self.service.onboard_collect({'id': row['id']})
        self.assertEqual([job['kind'] for job in self.service.state['jobs']], ['probe'])

    def test_collection_consent_save_failure_leaves_ready_without_queue(self):
        row = self.start(); self.service.tick()
        with patch.object(self.service, '_save', side_effect=OSError('SYNTHETIC denied')):
            with self.assertRaises(OSError): self.service.onboard_collect({'id': row['id']})
        self.assertEqual(self.status(row)['status'], 'ready')
        self.assertNotIn('collection_requested_at', self.status(row))
        self.service = self.make_service(); self.service.tick()
        self.assertEqual([job['kind'] for job in self.service.state['jobs']], ['probe'])

    def test_explicit_collect_marker_recovers_after_restart_without_duplicate(self):
        row = self.start(); self.service.tick()
        with patch.object(self.service.onboarding, 'advance'):
            code, queued = self.service.onboard_collect({'id': row['id']})
        self.assertEqual(code, 202)
        self.assertIn('collection_requested_at', queued)
        self.assertEqual(len(self.service.state['jobs']), 1)
        self.service = self.make_service(); self.service.tick()
        self.assertEqual(self.status(row)['status'], 'complete')
        self.assertEqual([job['kind'] for job in self.calls], ['probe', 'shop'])

    def test_completed_legacy_onboarding_return_is_unchanged_and_does_not_recapture(self):
        row = self.start(); self.service.tick(); self.service.onboard_collect({'id': row['id']}); self.service.tick()
        old = self.service.state['onboardings'][0]
        old.pop('mode'); old.pop('collection_requested_at')
        self.service._save()
        original = deepcopy(old)
        self.service = self.make_service()
        self.assertEqual(self.service.onboard({'name': None, 'url': SHARE})[1]['status'], 'complete')
        self.assertEqual(self.service.onboard_collect({'id': row['id']})[1]['status'], 'complete')
        self.service.tick()
        self.assertEqual(self.service.state['onboardings'][0], original)
        self.assertEqual(len(self.calls), 2)

    def test_directory_shows_new_identity_before_build_and_does_not_capture(self):
        row = self.start()
        pending = self.service.shop_directory()
        self.assertEqual(pending['status'], 'ok')
        self.assertEqual(pending['targets'][0]['target_id'], self.target['target_id'])
        self.assertEqual(pending['targets'][0]['onboarding']['id'], row['id'])
        self.assertIsNone(pending['targets'][0]['latest_collection'])
        self.service.tick()
        before = self.service.path.read_bytes()
        with patch.object(self.service, '_public_job', side_effect=AssertionError('No heavy content audit')):
            directory = self.service.shop_directory()
        original = next(target for target in directory['targets'] if target['target_id'] == self.target['target_id'])
        stable = next(target for target in directory['targets'] if target['target_id'] == self.status(row)['resolved_target_id'])
        self.assertEqual(original['shop_id'], stable['shop_id'])
        self.assertEqual(original['display_name'], '合成核验店')
        self.assertEqual(original['run_count'], 0)
        self.assertEqual(stable['onboarding']['status'], 'ready')
        self.assertTrue(stable['onboarding']['can_collect'])
        self.assertEqual(len(directory['onboardings']), 1)
        self.assertEqual(self.service.path.read_bytes(), before)
        self.assertEqual(self.target_path.read_bytes(), self.original_target)
        self.assertEqual(self.publishes, [])
        self.assertFalse((self.root / 'data').exists())

    def test_directory_reads_current_runs_and_keeps_each_shop_counts_and_jobs_separate(self):
        from pdd_monitor.store import import_snapshot
        row = self.start(); self.service.tick()
        ready = self.status(row)
        other = register_target(self.root, '另一合成店', 'https://mobile.yangkeduo.com/mall_page.html?mall_id=887766')['target']
        for name, source, count in (('合成核验店', STORE, 2), ('另一合成店', other['source_url'], 3)):
            snapshot = {'synthetic': True, 'shopName': name, 'sourceUrl': source,
                        'observedFrom': '2026-10-04T00:00:00Z', 'observedTo': '2026-10-04T00:05:00Z',
                        'status': 'partial', 'endBoundaryObserved': False,
                        'rows': [{'viewOrder': index + 1, 'title': 'SYNTHETIC CARD', 'salesRaw': '已拼11件', 'priceRaw': '¥1'} for index in range(count)]}
            source_file = self.root / (str(count) + '.json')
            source_file.write_text(json.dumps(snapshot), encoding='utf-8')
            import_snapshot(self.root / 'data', source_file)
        self.service.state['jobs'].append({'id': 'collect_' + 'c' * 32, 'shop_id': other['shop_id'],
                                           'kind': 'shop', 'status': 'cancelled', 'message': 'OTHER SYNTHETIC TASK'})
        before = {path: path.read_bytes() for path in (self.root / 'data').glob('*.sqlite3')}
        directory = self.service.shop_directory()
        own = next(target for target in directory['targets'] if target['target_id'] == self.target['target_id'])
        foreign = next(target for target in directory['targets'] if target['target_id'] == other['target_id'])
        self.assertEqual((own['shop_id'], own['run_count'], own['latest_row_count']), (ready['shop_id'], 1, 2))
        self.assertEqual(own['status'], 'observed')
        self.assertIsNone(own['latest_collection'])
        self.assertEqual(foreign['latest_row_count'], 3)
        self.assertEqual(foreign['latest_collection']['message'], 'OTHER SYNTHETIC TASK')
        self.assertIsNone(foreign['onboarding'])
        self.assertEqual(before, {path: path.read_bytes() for path in (self.root / 'data').glob('*.sqlite3')})
        self.assertEqual(self.publishes, [])
