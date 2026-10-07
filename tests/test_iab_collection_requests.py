"""Synthetic request lifecycle; never opens a website or starts a browser."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack, redirect_stdout
from http.server import ThreadingHTTPServer
import hashlib
import http.client
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from pdd_monitor import capture_control, dashboard_runtime
from pdd_monitor.collection_service import CollectionService, main
from pdd_monitor.store import initialize, shop_identity_evidence

URL = 'https://mobile.yangkeduo.com/mall_page.html?mall_id=9000000001'
SHOP = shop_identity_evidence({'sourceUrl': URL})['shop_id']
URL_B = URL.replace('0001', '0002')
SHOP_B = shop_identity_evidence({'sourceUrl': URL_B})['shop_id']
NAME = 'SYNTHETIC SHOP'


class SyntheticService(CollectionService):
    def _shop(self, shop_id):
        if shop_id not in (SHOP, SHOP_B):
            raise ValueError('Unknown synthetic shop')
        return {'shop_name': NAME, 'source_url': URL if shop_id == SHOP else URL_B}


class IabRequestTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='iab_request_SYNTHETIC_')
        self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name)
        (self.project/'AGENTS.md').write_text('SYNTHETIC', encoding='utf-8')
        initialize(self.project/'data')
        self.policy = self.project/'state/local_collection/browser_policy.json'
        self.policy.parent.mkdir(parents=True)
        self.policy.write_text('{"mode":"codex_iab"}', encoding='utf-8')
        self.calls = []
        self.service = SyntheticService(self.project, runner=lambda job: self.calls.append(job), autostart=False)

    def start(self, shop=SHOP):
        return self.service.start({'shop_id': shop, 'kind': 'shop'})[1]['job']

    def payload(self, job, **overrides):
        return {'id': job['id'], 'shop_id': SHOP, 'verified_shop_name': NAME,
                'verified_source_url': URL, 'browser_mode': 'codex_iab', **overrides}

    def claim(self, job):
        code, body = self.service.host_claim(self.payload(job))
        self.assertEqual(code, 200)
        return body['job']

    def hashes(self):
        return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in (self.project/'data').glob('*.sqlite3')}

    def test_request_persists_idempotently_without_browser_or_business_write(self):
        before = self.hashes()
        with ThreadPoolExecutor(max_workers=2) as executor:
            jobs = list(executor.map(lambda _: self.start(), range(2)))
        self.assertEqual(jobs[0]['id'], jobs[1]['id'])
        restored = SyntheticService(self.project, runner=lambda job: self.calls.append(job), autostart=False)
        job = restored.status(SHOP)['jobs'][0]
        self.assertEqual(job['status'], 'awaiting_host')
        self.assertIsNone(job['ai_requests'])
        self.assertTrue(job['can_cancel'])
        self.assertIn(job['id'], job['handoff_text'])
        self.assertIn(SHOP, job['handoff_text'])
        self.service.tick(); restored.tick()
        self.assertEqual(self.calls, [])
        self.assertFalse((self.project/'state/capture_jobs').exists())
        self.assertEqual(before, self.hashes())

    def test_request_does_not_run_when_policy_changes(self):
        job = self.start()
        self.policy.write_text('{"mode":"local_chrome"}')
        self.service.tick()
        self.assertEqual(self.calls, [])
        replacement = self.start()
        self.assertNotEqual(replacement['id'], job['id'])
        self.assertEqual(self.service.state['jobs'][0]['status'], 'cancelled')
        self.assertEqual(self.service.state['jobs'][0]['superseded_by'], replacement['id'])
        self.assertEqual(self.service.host_claim(self.payload(job))[0], 409)
        with self.assertRaises(ValueError):
            self.service._run_job(self.service.state['jobs'][0])
        with self.assertRaises(ValueError):
            self.service._execute(self.service.state['jobs'][0])
        # Even a manually invoked worker cannot consume an IAB-labelled job.
        self.service.state['jobs'][0]['status'] = 'running'; self.service._save()
        from pdd_monitor.collection_worker import work
        with patch('pdd_monitor.collection_worker.subprocess.run') as process:
            self.assertEqual(work(self.project, job['id'])['reason'], 'iab_host_only')
            process.assert_not_called()

    def test_existing_local_job_blocks_duplicate_iab_request(self):
        self.policy.write_text('{"mode":"local_chrome"}')
        local = self.start()
        self.policy.write_text('{"mode":"codex_iab"}')
        self.assertEqual(self.start()['id'], local['id'])
        self.service.tick()
        self.assertEqual(self.calls, [])

    def test_cancel_is_shop_bound_and_cancelled_request_cannot_be_claimed(self):
        job = self.start()
        self.assertEqual(self.service.cancel({'shop_id': SHOP_B, 'id': job['id']})[0], 409)
        self.assertEqual(self.service.cancel({'shop_id': SHOP, 'id': job['id']})[0], 200)
        self.assertEqual(self.service.host_claim(self.payload(job))[0], 409)
        self.assertNotEqual(self.start()['id'], job['id'])

    def test_global_pending_limit_is_enforced(self):
        for i in range(12):
            self.service.state['jobs'].append({'id': f'collect_{i:032x}', 'shop_id': SHOP_B,
                'shop_name': NAME, 'kind': 'shop', 'browser_mode': 'codex_iab',
                'status': 'awaiting_host', 'created_at': '2026-10-06T00:00:00Z'})
        self.assertEqual(self.service.start({'shop_id': SHOP, 'kind': 'shop'})[0], 409)

    def test_claim_checks_real_shop_name_url_mode_before_creating_job(self):
        job = self.start()
        cases = ({'verified_shop_name': 'OTHER'}, {'verified_source_url': URL_B},
                 {'verified_source_url': 'https://evil.invalid/mall_page.html?mall_id=9000000001'},
                 {'unexpected': True})
        for changed in cases:
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                self.service.host_claim(self.payload(job, **changed))
        self.assertEqual(self.service.host_claim(self.payload(job, browser_mode='local_chrome'))[0], 409)
        self.assertFalse((self.project/'state/capture_jobs').exists())

    def test_real_claim_is_idempotent_and_prepared_is_not_running(self):
        before = self.hashes()
        job = self.start()
        first = self.claim(job); second = self.claim(job)
        self.assertEqual(first['capture_job_id'], second['capture_job_id'])
        self.assertEqual(first['status'], 'awaiting_browser')
        self.assertFalse(first['website_collection_started'])
        self.assertFalse(first['accepted_into_history'])
        self.assertEqual(len(list((self.project/'state/capture_jobs').glob('*/job.json'))), 1)
        restored = SyntheticService(self.project, autostart=False)
        self.assertEqual(restored.status(SHOP)['jobs'][0]['status'], 'awaiting_browser')
        self.assertEqual(self.service.cancel({'shop_id': SHOP, 'id': job['id']})[0], 409)
        self.assertEqual(before, self.hashes())
        output = json.dumps(first)
        for secret in ('owner_token', 'source_url', 'session_directory', 'mall_id=', 'lastError'):
            self.assertNotIn(secret, output)

    def test_shop_b_lock_conflict_does_not_fake_claim(self):
        self.claim(self.start())
        b = self.start(SHOP_B)
        code, body = self.service.host_claim(self.payload(b, shop_id=SHOP_B, verified_source_url=URL_B))
        self.assertEqual(code, 409)
        self.assertEqual(body['job']['status'], 'awaiting_host')
        self.assertNotIn('capture_job_id', body['job'])

    def test_complete_session_waits_for_real_accepted_history(self):
        job = self.claim(self.start())
        capture = capture_control._job(self.project, job['capture_job_id'])
        directory = Path(capture['session_directory']); directory.mkdir(parents=True)
        (directory/'session.json').write_text(json.dumps({'attemptId': capture['attempt_id'],
            'phase': 'complete', 'progress': {'cards': 40, 'verifiedBatches': 2, 'pending': 0, 'batches': 2},
            'lastError': {'message': 'PRIVATE RAW URL'}, 'owner': {'token': 'PRIVATE'}}))
        before = self.service.path.read_bytes()
        public = self.service.status(SHOP)['jobs'][0]
        self.assertEqual(public['status'], 'validating')
        self.assertEqual(public['progress']['cards'], 40)
        self.assertFalse(public['accepted_into_history'])
        self.assertFalse(public['dashboard_built'])
        self.assertNotIn('PRIVATE', json.dumps(public))
        self.assertEqual(before, self.service.path.read_bytes())
        accepted = {'shop_id': SHOP, 'journal_status': 'complete', 'accepted_into_history': True,
                    'run_id': 'run_synthetic', 'session': {'phase': 'complete', 'progress': {'cards': 40}}}
        with patch.object(capture_control, 'status', return_value=accepted):
            self.assertEqual(self.service.status(SHOP)['jobs'][0]['status'], 'complete')
            self.assertNotEqual(self.start()['id'], job['id'])

    def test_unavailable_evidence_fails_closed_and_blocks_duplicate(self):
        job = self.claim(self.start())
        with patch.object(capture_control, 'status', side_effect=ValueError('PRIVATE')):
            public = self.service.status(SHOP)['jobs'][0]
            self.assertEqual(public['status'], 'manual_review')
            self.assertTrue(public['host_status_unavailable'])
            self.assertNotIn('PRIVATE', json.dumps(public))
            self.assertEqual(self.start()['id'], job['id'])

    def test_interrupted_claim_cannot_be_reclaimed_after_restart(self):
        job = self.start()
        self.service.state['jobs'][0]['status'] = 'host_claiming'; self.service._save()
        restored = SyntheticService(self.project, autostart=False)
        self.assertTrue(restored.status(SHOP)['jobs'][0]['host_status_unavailable'])
        self.assertEqual(restored.host_claim(self.payload(job))[0], 409)
        self.assertEqual(restored.start({'shop_id': SHOP, 'kind': 'shop'})[1]['job']['id'], job['id'])

    def test_stopped_session_with_open_journal_blocks_new_request(self):
        job = self.claim(self.start())
        capture = capture_control._job(self.project, job['capture_job_id'])
        directory = Path(capture['session_directory']); directory.mkdir(parents=True)
        (directory/'session.json').write_text(json.dumps({'attemptId': capture['attempt_id'],
            'phase': 'manual_review', 'progress': {'cards': 60, 'verifiedBatches': 3, 'pending': 0, 'batches': 3},
            'operation': None, 'importReady': False}))
        public = self.service.status(SHOP)['jobs'][0]
        self.assertTrue(public['host_attempt_open'])
        self.assertEqual(public['status'], 'manual_review')
        self.assertEqual(self.start()['id'], job['id'])
        capture_control.abandon(self.project, job['capture_job_id'], 'SYNTHETIC stopped session reviewed')
        self.assertNotEqual(self.start()['id'], job['id'])

    def test_registered_unique_retry_child_is_followed_without_rewriting_request(self):
        job = self.claim(self.start())
        parent_id = job['capture_job_id']
        capture_control.abandon(self.project, parent_id, 'SYNTHETIC reviewed failure')
        child = capture_control.begin(self.project, retry_job=parent_id)
        before = self.service.path.read_bytes()
        public = self.service.status(SHOP)['jobs'][0]
        self.assertEqual(public['capture_job_id'], child['job_id'])
        self.assertEqual(public['status'], 'awaiting_browser')
        self.assertEqual(self.start()['id'], job['id'])
        self.assertEqual(before, self.service.path.read_bytes())
        self.assertEqual(self.service.state['jobs'][0]['capture_job_id'], parent_id)

    def test_wrong_retry_child_fails_closed_without_leaking_claim(self):
        job = self.claim(self.start())
        claim = capture_control._job_dir(self.project, job['capture_job_id'])/'retry_claim.json'
        claim.write_text(json.dumps({'parent_job_id': job['capture_job_id'],
                                    'child_job_id': 'capture_'+'f'*32, 'child_attempt_id': 'SECRET'}))
        public = self.service.status(SHOP)['jobs'][0]
        self.assertTrue(public['host_status_unavailable'])
        self.assertEqual(public['status'], 'manual_review')
        self.assertEqual(self.start()['id'], job['id'])
        self.assertNotIn('SECRET', json.dumps(public))

    def test_failed_request_save_does_not_claim_durability_in_memory(self):
        with patch.object(self.service, '_save', side_effect=OSError('SYNTHETIC disk unavailable')):
            with self.assertRaises(OSError):
                self.start()
        self.assertEqual(self.service.state['jobs'], [])

    def test_http_route_and_cli_use_service_and_reject_cross_origin(self):
        dist = self.project/'dashboard/dist'; dist.mkdir(parents=True)
        (dist/'index.html').write_text('<!doctype html><title>SYNTHETIC</title>')
        server_ready = threading.Event(); servers = []
        # Handler closes over port, so reserve an explicit ephemeral port first.
        import socket
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
        def factory(address, handler):
            server = ThreadingHTTPServer(address, handler)
            servers.append(server); server_ready.set(); return server
        class Stub:
            def __init__(self, *args, **kwargs): pass
        with ExitStack() as stack:
            for name in ('artist_heat_service.ArtistHeatService', 'profit_service.ProfitService',
                         'agent_service.AgentService', 'competitor_service.CompetitorService'):
                stack.enter_context(patch('pdd_monitor.'+name, Stub))
            stack.enter_context(patch('pdd_monitor.collection_service.CollectionService', return_value=self.service))
            stack.enter_context(patch.object(dashboard_runtime, 'ThreadingHTTPServer', side_effect=factory))
            thread = threading.Thread(target=dashboard_runtime.serve, args=(self.project, port), daemon=True)
            thread.start(); self.assertTrue(server_ready.wait(3))
            try:
                job = self.start(); output = io.StringIO()
                with redirect_stdout(output):
                    result = main(['--port', str(port), 'host-claim', '--id', job['id'], '--shop-id', SHOP,
                                   '--verified-shop-name', NAME, '--verified-source-url', URL])
                self.assertEqual(result, 0)
                self.assertEqual(json.loads(output.getvalue())['job']['status'], 'awaiting_browser')
                self.assertNotIn(URL, output.getvalue())
                connection = http.client.HTTPConnection('127.0.0.1', port, timeout=3)
                connection.request('POST', '/__pdd_collection_host_claim', body=json.dumps(self.payload(job)),
                                   headers={'Origin': 'https://foreign.invalid', 'Content-Type': 'application/json'})
                response = connection.getresponse(); self.assertEqual(response.status, 403); response.read(); connection.close()
                output = io.StringIO()
                with redirect_stdout(output):
                    self.assertEqual(main(['--port', str(port), 'show', '--shop-id', SHOP]), 0)
                self.assertEqual(json.loads(output.getvalue())['jobs'][0]['id'], job['id'])
            finally:
                servers[0].shutdown(); servers[0].server_close(); thread.join(3)


if __name__ == '__main__':
    unittest.main()
