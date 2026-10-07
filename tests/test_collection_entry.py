"""Temporary-only entry recovery tests: no browser and no production data writes."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from http.server import ThreadingHTTPServer
import http.client
import json
from pathlib import Path
import socket
import tempfile
import threading
import unittest
from unittest.mock import patch

from pdd_monitor import dashboard_runtime, capture_control
from pdd_monitor.collection_service import CollectionService, confirmed_shop_release, public_shop_entry_url

A = 'shop_' + 'a' * 24
B = 'shop_' + 'b' * 24
URL = 'https://mobile.yangkeduo.com/mall_page.html?ps=SYNTHETIC_ENTRY'
OTHER = URL + '_NEW'
CANONICAL = 'https://mobile.yangkeduo.com/mall_page.html?mall_id=9000000001'


def sealed_shop_result(*, images=3):
    """Worker-return fixture; persisted release verification has separate tests."""
    capture_id = 'capture_' + 'c' * 32
    digest = 'd' * 64
    run_id = 'run_' + digest[:24]
    return {'status': 'complete' if images == 3 else 'partial', 'entry_verified': True,
        'capture_job_id': capture_id,
        'image_summary': {'total': 3, 'saved': images, 'missing': 3 - images,
                          'status': 'complete' if images == 3 else 'partial'},
        'receipt': {'ok': True, 'status': 'finished', 'shop_id': A, 'job_id': capture_id,
            'run_id': run_id, 'snapshot_sha256': digest, 'snapshot_status': 'complete',
            'new_run': {'status': 'complete', 'cards': 3, 'image_refs': images},
            'journal': {'job_id': capture_id, 'shop_id': A, 'run_id': run_id,
                'attempt_id': 'attempt_' + 'e' * 32, 'accepted_into_history': True,
                'journal_status': 'complete',
                'session': {'phase': 'complete', 'importReady': True,
                            'snapshot': {'sha256': digest}}}}}


class Service(CollectionService):
    def _shop(self, shop_id):
        if shop_id not in (A, B):
            raise ValueError('Unknown synthetic shop')
        return {'shop_name': 'SYNTHETIC ONLY', 'source_url': CANONICAL}


class CollectionEntryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='collection_entry_SYNTHETIC_')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.calls = []
        self.worker_result = {'status': 'needs_url', 'reason': 'zero_products'}
        self.service = Service(self.root, runner=self.runner, autostart=False)
        self.policy = self.service.directory / 'browser_policy.json'

    def runner(self, job):
        self.calls.append(job)
        return self.worker_result

    def mode(self, mode):
        self.policy.parent.mkdir(parents=True, exist_ok=True)
        self.policy.write_text(json.dumps({'mode': mode}), encoding='utf-8')

    def start(self, **extra):
        return self.service.start({'shop_id': A, 'kind': 'shop', **extra})

    def update(self, url=URL, revision=0, shop=A):
        return self.service.update_entry({'shop_id': shop, 'entry_url': url, 'revision': revision})

    def test_public_share_fields_and_unicode_are_accepted_without_binding(self):
        for url in (URL, CANONICAL,
                    'https://mobile.yangkeduo.com/mall_page.html?mall_sn=abc%2Bdef%3D&decrypt_mall_sn=1&force_use_web_bundle=1',
                    URL + '&_x_org=2&_x_query=%E6%B6%A6%E5%B0%91&_x_share_id=abc&refer_page_name=login&refer_page_id=10169_abc&refer_page_sn=10169&page_id=10039_abc&mall_tab_key=mall_goods&sort_type=2&is_back=1'):
            self.assertEqual(public_shop_entry_url(url), url)
        self.assertEqual(self.update()[0], 200)
        self.assertEqual(self.service._shop(A)['source_url'], CANONICAL)

    def test_entry_rejects_nonshop_unsafe_or_ambiguous_parameters(self):
        bad = ('https://evil.example/mall_page.html?ps=test',
               URL.replace('/mall_page.html', '/goods1.html'), URL.replace('https:', 'http:'),
               URL.replace('https:', 'javascript:'), URL.replace('mobile.', 'person@mobile.'),
               URL.replace('.com/', '.com:443/'), URL + '&ps=OTHER', URL + '&%70s=OTHER',
               URL + '&token=SECRET', URL + '&cookie=SECRET', URL + '&access_token=SECRET',
               URL + '&goods_id=2', URL + '#fragment', URL + '&_x_query=%QQ',
               URL + '&_x_query=%00', URL + '&_x_query=%FF', CANONICAL.replace('9000000001', 'abc'),
               'https://mobile.yangkeduo.com/mall_page.html?ts=123', ' ' + URL,
               URL + '\n', URL + '&_x_query=' + 'x' * 513)
        for url in bad:
            with self.subTest(url=url), self.assertRaises(ValueError):
                public_shop_entry_url(url)
        for extra in ({'cookie': 'SECRET'}, {'token': 'SECRET'}, {'entry_revision': 0},
                      {'entry_url': URL, 'entry_revision': True}):
            with self.assertRaises(ValueError):
                self.start(**extra)

    def test_start_saves_unverified_private_entry_and_reuses_it_after_restart(self):
        code, result = self.start(entry_url=URL, entry_revision=0)
        self.assertEqual(code, 202)
        self.assertEqual(result['entry']['revision'], 1)
        self.assertEqual(result['entry']['verification'], 'unverified')
        self.assertNotIn(URL, json.dumps(result))
        self.service.tick()
        self.assertEqual(self.calls[0]['entry_url'], URL)
        restored = Service(self.root, runner=self.runner, autostart=False)
        self.assertEqual(restored.status(A)['entry']['revision'], 1)
        self.assertNotIn(URL, json.dumps(restored.status(A)))
        restored.start({'shop_id': A, 'kind': 'shop'})
        restored.tick()
        self.assertEqual(self.calls[-1]['entry_url'], URL)
        self.assertEqual(restored.status(A)['entry']['verification'], 'unverified')
        self.assertFalse((self.root / 'data').exists())

    def test_entry_version_and_current_job_remain_fixed(self):
        job = self.start(entry_url=URL, entry_revision=0)[1]['job']
        self.assertEqual(self.start(entry_url=URL, entry_revision=0)[1]['job']['id'], job['id'])
        self.assertEqual(self.start(entry_url=OTHER, entry_revision=1)[0], 409)
        self.assertEqual(self.update(OTHER, 1)[0], 200)
        self.service.tick()
        self.assertEqual(self.calls[0]['entry_url'], URL)
        self.assertEqual(self.calls[0]['entry_revision'], 1)
        self.assertEqual(self.start(entry_url=URL, entry_revision=1)[0], 409)
        self.start(); self.service.tick()
        self.assertEqual(self.calls[-1]['entry_url'], OTHER)
        self.assertEqual(self.calls[-1]['entry_revision'], 2)

    def test_optimistic_updates_are_shop_scoped_and_atomic(self):
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda url: self.update(url)[0], (URL, OTHER)))
        self.assertEqual(sorted(results), [200, 409])
        self.assertEqual(self.service.status(A)['entry']['revision'], 1)
        self.assertFalse(self.service.status(B)['entry']['configured'])
        self.assertEqual(self.update(OTHER, shop=B)[0], 200)
        self.assertEqual(self.service.status(B)['entry']['revision'], 1)

    def test_only_new_manual_local_start_supersedes_unclaimed_iab(self):
        self.mode('codex_iab')
        old = self.start()[1]['job']
        self.mode('local_chrome')
        scheduled = self.service.start({'shop_id': A, 'kind': 'shop'}, trigger='scheduled')[1]['job']
        self.assertEqual(scheduled['id'], old['id'])
        self.service.tick(); self.assertEqual(self.calls, [])
        new = self.start(entry_url=URL)[1]['job']
        self.assertNotEqual(new['id'], old['id'])
        previous = self.service.state['jobs'][0]
        self.assertEqual(previous['browser_mode'], 'codex_iab')
        self.assertEqual(previous['status'], 'cancelled')
        self.assertEqual(previous['superseded_by'], new['id'])
        self.service.tick()
        self.assertEqual([job['id'] for job in self.calls], [new['id']])

    def test_claimed_or_uncertain_iab_cannot_be_superseded(self):
        self.mode('codex_iab'); old = self.start()[1]['job']
        stored = self.service.state['jobs'][0]
        stored.update(status='manual_review', host_status_unavailable=True)
        self.mode('local_chrome')
        self.assertEqual(self.start()[1]['job']['id'], old['id'])
        self.assertEqual(self.start(entry_url=URL)[0], 409)
        self.assertFalse(self.service.status(A)['entry']['configured'])
        self.assertEqual(len(self.service.state['jobs']), 1)
        stored.update(status='host_claimed', capture_job_id='capture_' + 'c' * 32)
        stored.pop('host_status_unavailable')
        with patch.object(capture_control, 'status', return_value={
                'shop_id': A, 'journal_status': 'running', 'session': None}):
            self.assertEqual(self.start()[1]['job']['id'], old['id'])
            self.assertEqual(self.start(entry_url=URL)[0], 409)
            self.assertEqual(stored['status'], 'host_claimed')

    def test_failed_save_rolls_back_entry_and_iab_cancellation(self):
        self.mode('codex_iab'); old = self.start()[1]['job']
        self.mode('local_chrome')
        before = self.service.path.read_bytes()
        with patch.object(self.service, '_save', side_effect=OSError('SYNTHETIC')):
            with self.assertRaises(OSError):
                self.start(entry_url=URL)
            with self.assertRaises(OSError):
                self.update()
        self.assertEqual(self.service.path.read_bytes(), before)
        self.assertEqual(self.service.state['entries'], {})
        self.assertEqual(len(self.service.state['jobs']), 1)
        self.assertEqual(self.service.state['jobs'][0]['id'], old['id'])
        self.assertEqual(self.service.state['jobs'][0]['status'], 'awaiting_host')

    def test_recovery_reasons_use_current_mode_and_do_not_guess_link_expiry(self):
        cases = [('needs_login', 'login_or_session_required', 'login_required'),
                 ('needs_url', 'entry_unavailable', 'entry_unavailable'),
                 ('needs_url', 'zero_products', 'zero_products'),
                 ('needs_url', 'identity_mismatch', 'identity_mismatch'),
                 ('manual_review', 'access_restricted', 'access_restricted'),
                 ('needs_url', 'access_restricted', 'access_restricted'),
                 ('needs_url', 'browser_disconnected', None),
                 ('failed', 'browser_disconnected', None)]
        for status, reason, recovery in cases:
            self.worker_result = {'status': status, 'reason': reason}
            self.start(); self.service.tick()
            result = self.service.status(A)
            self.assertEqual(result['recovery_reason'], recovery)
            self.assertEqual(result['recovery_required'], recovery is not None)
            self.assertEqual(result['latest_job']['recovery_reason'], recovery)
        self.mode('codex_iab')
        current = self.service.status(A)
        self.assertIsNone(current['latest_job'])
        self.assertFalse(current['recovery_required'])

    def test_needs_url_blocks_schedule_and_has_no_automatic_retry(self):
        self.service.schedule({'shop_id': A, 'revision': 0, 'enabled': True,
                               'mode': 'daily', 'times': ['08:00'], 'at': ''})
        self.start(entry_url=URL); self.service.tick(); self.service.tick()
        result = self.service.status(A)
        self.assertEqual(result['latest_job']['status'], 'needs_url')
        self.assertEqual(result['schedule']['blocked_by'], result['latest_job']['id'])
        self.assertEqual(len(self.calls), 1)

    def test_entry_verified_requires_complete_confirmed_same_shop_receipt(self):
        self.update()
        good = sealed_shop_result()
        self.assertTrue(confirmed_shop_release({'kind': 'shop', 'shop_id': A}, good))
        for result in ({**good, 'receipt': {**good['receipt'], 'snapshot_status': 'partial'}}, {**good, 'entry_verified': False},
                       {**good, 'receipt': {}}, {**good, 'receipt': {**good['receipt'], 'shop_id': B}},
                       {**good, 'capture_job_id': None}, {**good, 'receipt': {**good['receipt'], 'journal': {}}}):
            self.worker_result = result
            self.start(); self.service.tick()
            self.assertEqual(self.service.status(A)['entry']['verification'], 'unverified')
        self.worker_result = good
        self.start(); self.service.tick()
        self.assertEqual(self.service.status(A)['entry']['verification'], 'verified')

    def test_finished_old_revision_cannot_verify_replacement_entry(self):
        self.start(entry_url=URL)
        self.update(OTHER, 1)
        self.worker_result = sealed_shop_result()
        self.assertTrue(confirmed_shop_release({'kind': 'shop', 'shop_id': A}, self.worker_result))
        self.service.tick()
        self.assertEqual(self.service.status(A)['entry']['revision'], 2)
        self.assertEqual(self.service.status(A)['entry']['verification'], 'unverified')

    def test_missing_image_partial_can_verify_entry_after_complete_card_release(self):
        self.update()
        self.worker_result = sealed_shop_result(images=2)
        self.start(); self.service.tick()
        self.assertEqual(self.service.status(A)['entry']['verification'], 'verified')
        self.assertEqual(self.service.status(A)['latest_job']['status'], 'partial')

    def test_entry_route_enforces_same_origin_strict_json_and_revision(self):
        dist = self.root / 'dashboard/dist'; dist.mkdir(parents=True)
        (dist / 'index.html').write_text('<!doctype html><title>SYNTHETIC</title>')
        ready = threading.Event(); servers = []
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
        def factory(address, handler):
            server = ThreadingHTTPServer(address, handler)
            servers.append(server); ready.set(); return server
        class Stub:
            def __init__(self, *args, **kwargs): pass
        def post(body, origin=None):
            conn = http.client.HTTPConnection('127.0.0.1', port, timeout=3)
            conn.request('POST', '/__pdd_collection_entry', body=body,
                         headers={'Origin': origin or f'http://127.0.0.1:{port}', 'Content-Type': 'application/json'})
            response = conn.getresponse(); code = response.status; result = response.read(); conn.close()
            return code, result
        with ExitStack() as stack:
            for name in ('artist_heat_service.ArtistHeatService', 'profit_service.ProfitService',
                         'agent_service.AgentService', 'competitor_service.CompetitorService'):
                stack.enter_context(patch('pdd_monitor.' + name, Stub))
            stack.enter_context(patch('pdd_monitor.collection_service.CollectionService', return_value=self.service))
            stack.enter_context(patch.object(dashboard_runtime, 'ThreadingHTTPServer', side_effect=factory))
            thread = threading.Thread(target=dashboard_runtime.serve, args=(self.root, port), daemon=True)
            thread.start(); self.assertTrue(ready.wait(3))
            try:
                body = json.dumps({'shop_id': A, 'entry_url': URL, 'revision': 0})
                self.assertEqual(post(body, 'https://foreign.invalid')[0], 403)
                self.assertEqual(post(body[:-1] + ',"revision":0}')[0], 422)
                code, raw = post(body)
                self.assertEqual(code, 200); self.assertNotIn(URL.encode(), raw)
                self.assertEqual(post(body)[0], 409)
                self.assertEqual(self.service.state['jobs'], [])
            finally:
                servers[0].shutdown(); servers[0].server_close(); thread.join(3)


if __name__ == '__main__':
    unittest.main()
