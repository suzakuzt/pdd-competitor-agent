"""Actual staged tracking/intake routes on an ephemeral loopback port only."""
import http.client
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import socket
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

sys.dont_write_bytecode = True
STAGE = Path(__file__).resolve().parent
sys.path.insert(0, str(STAGE.parent))
from pdd_monitor import dashboard_runtime
from pdd_monitor.competitor_service import CompetitorService


class OtherServiceStub:
    def __init__(self, project, publisher=None):
        self.publisher = publisher

    def status(self):
        return {'status': 'idle', 'message': 'SYNTHETIC offline stub'}


class IntakeHTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='intake_http_synthetic_', dir=STAGE)
        self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name)
        dist = self.project / 'dashboard/dist'; dist.mkdir(parents=True)
        (dist / 'index.html').write_text('<!doctype html><title>SYNTHETIC test</title>', encoding='utf-8')
        source = self.project / 'dashboard/src'; source.mkdir(parents=True)
        (source / 'data.json').write_text(json.dumps({'queries': {'runs': {'rows': []}}}), encoding='utf-8')
        self.publisher_result = {'status': 'built'}
        self.publisher_calls = []
        self.published = threading.Event()
        self.services = []
        self.patchers = []
        self.errors = []
        self.server = None
        ready = threading.Event()
        with socket.socket() as reservation:
            reservation.bind(('127.0.0.1', 0))
            self.port = reservation.getsockname()[1]
        real_connect = socket.create_connection

        def loopback_only(address, *args, **kwargs):
            if address[0] != '127.0.0.1' or address[1] != self.port:
                raise AssertionError('External network is forbidden in intake tests')
            return real_connect(address, *args, **kwargs)

        def factory(address, handler):
            self.assertEqual(address, ('127.0.0.1', self.port))
            self.server = ThreadingHTTPServer(address, handler)
            ready.set()
            return self.server

        def stub(project, publisher=None):
            service = OtherServiceStub(project, publisher)
            self.services.append(service)
            return service

        def publish(project):
            self.publisher_calls.append(Path(project))
            self.published.set()
            if isinstance(self.publisher_result, Exception):
                raise self.publisher_result
            return self.publisher_result

        for patcher in (
            patch.object(sqlite3, 'connect', side_effect=AssertionError('Database use forbidden')),
            patch.object(socket, 'create_connection', side_effect=loopback_only),
            patch('pdd_monitor.artist_heat_service.ArtistHeatService', side_effect=stub),
            patch('pdd_monitor.profit_service.ProfitService', side_effect=stub),
            patch('pdd_monitor.agent_service.AgentService', side_effect=stub),
            patch.object(dashboard_runtime, 'build_dashboard', side_effect=publish),
            patch.object(dashboard_runtime, 'ThreadingHTTPServer', side_effect=factory),
        ):
            patcher.start(); self.patchers.append(patcher)

        def serving():
            try:
                dashboard_runtime.serve(self.project, self.port)
            except Exception as error:
                self.errors.append(str(error)); ready.set()
        self.thread = threading.Thread(target=serving, daemon=True)
        self.thread.start()
        self.addCleanup(self.cleanup_server)
        self.assertTrue(ready.wait(3), 'Loopback test server did not start')
        self.assertFalse(self.errors)

    def cleanup_server(self):
        if self.server:
            self.server.shutdown()
            self.server.server_close()
        self.thread.join(3)
        for patcher in reversed(self.patchers):
            patcher.stop()

    def request(self, method, url, payload=None, headers=None, raw=None):
        body = raw if raw is not None else (json.dumps(payload).encode() if payload is not None else None)
        all_headers = {'Origin': f'http://127.0.0.1:{self.port}', 'Content-Type': 'application/json', 'Sec-Fetch-Site': 'same-origin'}
        all_headers.update(headers or {})
        connection = http.client.HTTPConnection('127.0.0.1', self.port, timeout=3)
        try:
            connection.request(method, url, body=body, headers=all_headers)
            response = connection.getresponse()
            data = response.read()
            return response.status, json.loads(data), dict(response.getheaders())
        finally:
            connection.close()

    def poll(self, job_id):
        for _ in range(100):
            code, body, headers = self.request('GET', '/__pdd_competitor_status?job_id=' + job_id)
            self.assertEqual(code, 200)
            if body['status'] != 'running':
                return body
            time.sleep(0.01)
        self.fail('Publisher not completed')

    def test_actual_http_register_poll_and_repeat(self):
        code, body, headers = self.request('POST', '/__pdd_competitor_add', {'name': 'SYNTHETIC shop'})
        self.assertEqual(code, 202)
        self.assertFalse(body['receipt']['dashboard_built'])
        final = self.poll(body['job_id'])
        self.assertEqual(final['status'], 'succeeded')
        self.assertTrue(final['receipt']['dashboard_built'])
        self.assertFalse(final['receipt']['website_collection_performed'])
        self.assertEqual(final['receipt']['target_status'], 'needs_identity')
        self.assertIsNone(final['receipt']['shop_id'])
        code, duplicate, _ = self.request('POST', '/__pdd_competitor_add', {'name': 'SYNTHETIC shop'})
        self.assertEqual(code, 202)
        repeated = self.poll(duplicate['job_id'])
        self.assertEqual(repeated['receipt']['registration_status'], 'unchanged')
        self.assertEqual(repeated['receipt']['target_id'], final['receipt']['target_id'])
        self.assertEqual(len(self.publisher_calls), 2)
        self.assertTrue(all(root == self.project for root in self.publisher_calls))
        self.assertFalse((self.project / 'data').exists())
        self.assertEqual(headers['Cache-Control'], 'no-cache')
        self.assertNotIn('Access-Control-Allow-Origin', headers)

    def test_trend_status_is_published_shop_scoped_and_read_only(self):
        manifest = {'version': 1, 'shops': [{'shop_id': 'SYNTHETIC_A',
                    'reference_run_id': 'synthetic_run', 'model_version': 'v1',
                    'selected_count': 2, 'ledger_revision': 1}]}
        path = self.project / 'dashboard/dist/trend-status.json'
        path.write_text(json.dumps(manifest), encoding='utf-8')
        before = path.read_bytes()
        code, body, _ = self.request('GET', '/__pdd_trend_status?shop_id=SYNTHETIC_A')
        self.assertEqual(code, 200); self.assertEqual(body['reference_run_id'], 'synthetic_run')
        for suffix in ('?shop_id=OTHER', '?shop_id=SYNTHETIC_A&shop_id=OTHER', '?unexpected=x'):
            code, _, _ = self.request('GET', '/__pdd_trend_status' + suffix)
            self.assertEqual(code, 422)
        code, _, _ = self.request('GET', '/__pdd_trend_status?shop_id=SYNTHETIC_A',
                                  headers={'Origin': 'https://foreign.invalid'})
        self.assertEqual(code, 403)
        self.assertEqual(before, path.read_bytes()); self.assertFalse(self.publisher_calls)

    def test_actual_http_rejects_untrusted_or_malformed_requests(self):
        cases = [({'name': 'x'}, {'Origin': 'https://foreign.invalid'}, None, 403),
                 ({'name': 'x'}, {'Content-Type': 'text/plain'}, None, 403),
                 ({'name': 'x'}, {'Host': f'foreign.invalid:{self.port}'}, None, 403),
                 (None, {}, b'{"name":"x","name":"y"}', 422),
                 (None, {}, b'{"name":NaN}', 422),
                 ({'name': ['not text']}, {}, None, 422),
                 ({'url': 'https://mobile.yangkeduo.com/goods.html?goods_id=5'}, {}, None, 422)]
        for payload, headers, raw, expected in cases:
            code, body, _ = self.request('POST', '/__pdd_competitor_add', payload, headers, raw)
            self.assertEqual(code, expected)
            self.assertEqual(body['status'], 'failed')
        self.assertFalse((self.project / 'state').exists())
        self.assertFalse(self.publisher_calls)

    def test_actual_http_failed_build_keeps_registration(self):
        self.publisher_result = RuntimeError('SYNTHETIC no external build')
        code, body, _ = self.request('POST', '/__pdd_competitor_add', {'url': 'https://p.pinduoduo.com/share?mall_id=101'})
        self.assertEqual(code, 202)
        final = self.poll(body['job_id'])
        self.assertEqual(final['status'], 'failed')
        self.assertEqual(final['error_code'], 'dashboard_build_failed')
        self.assertEqual(final['receipt']['registration_status'], 'registered')
        self.assertFalse(final['receipt']['dashboard_built'])
        path = self.project / 'state/competitors/targets' / (final['receipt']['target_id'] + '.json')
        self.assertTrue(path.is_file())
        self.assertIsNone(json.loads(path.read_text(encoding='utf-8'))['shop_id'])

    def test_job_validation_existing_endpoints_and_shared_publisher(self):
        for suffix in ('', '?job_id=', '?job_id=x', '?job_id=x&job_id=y', '?job_id=x&other=y'):
            code, body, _ = self.request('GET', '/__pdd_competitor_status' + suffix)
            self.assertEqual(code, 400)
        code, body, _ = self.request('GET', '/__pdd_competitor_status?job_id=intake_' + 'a' * 32)
        self.assertEqual(code, 404)
        self.assertEqual(body['error_code'], 'job_not_found')
        code, body, _ = self.request('GET', '/__pdd_dashboard_health')
        self.assertEqual(code, 200)
        self.assertEqual(body['project'], str(self.project.resolve()))
        for url in ('/__pdd_artist_heat_status', '/__pdd_profit_status', '/__pdd_agent_status'):
            code, body, _ = self.request('GET', url)
            self.assertEqual(code, 200)
            self.assertEqual(body['message'], 'SYNTHETIC offline stub')
        publishers = [service.publisher for service in self.services if service.publisher is not None]
        self.assertEqual(len(publishers), 2)
        self.assertIs(publishers[0], publishers[1])
        closure = dict(zip(publishers[0].__code__.co_freevars, (cell.cell_contents for cell in publishers[0].__closure__)))
        self.assertIn('publish_lock', closure)

    def register_stable(self):
        code, body, _ = self.request('POST', '/__pdd_competitor_add',
            {'name': 'SYNTHETIC stable target', 'url': 'https://mobile.yangkeduo.com/mall_page.html?mall_id=99123'})
        self.assertEqual(code, 202)
        return self.poll(body['job_id'])['receipt']

    def test_tracking_enable_revision_conflict_pause_preserves_history(self):
        target = self.register_stable()
        baseline = self.project / 'state/new_arrivals/immutable_synthetic.json'
        baseline.parent.mkdir(parents=True); baseline.write_bytes(b'SYNTHETIC fixed baseline unchanged')
        history = self.project / 'dashboard/src/data.json'
        before = history.read_bytes()
        payload = {'target_id': target['target_id'], 'status': 'enabled', 'expected_revision': 0}
        code, body, _ = self.request('POST', '/__pdd_competitor_tracking', payload)
        self.assertEqual(code, 202)
        self.assertFalse(body['receipt']['dashboard_built'])
        final = self.poll(body['job_id'])
        self.assertEqual(final['status'], 'succeeded')
        receipt = final['receipt']
        self.assertEqual(receipt['operation'], 'tracking')
        self.assertEqual(receipt['tracking_status'], 'enabled')
        self.assertEqual(receipt['tracking_revision'], 1)
        self.assertTrue(receipt['dashboard_built'])
        self.assertEqual(receipt['tracking_connection_status'], 'connection_pending')
        for field in ('website_collection_performed', 'baseline_changed', 'history_deleted'):
            self.assertIs(receipt[field], False)
        code, conflict, _ = self.request('POST', '/__pdd_competitor_tracking', {**payload, 'status': 'paused'})
        self.assertEqual(code, 409)
        self.assertEqual(conflict['error_code'], 'revision_conflict')
        code, body, _ = self.request('POST', '/__pdd_competitor_tracking', {**payload, 'status': 'paused', 'expected_revision': 1})
        self.assertEqual(code, 202)
        paused = self.poll(body['job_id'])['receipt']
        self.assertEqual(paused['tracking_status'], 'paused')
        self.assertEqual(paused['tracking_revision'], 2)
        setting = self.project / 'state/competitors/tracking' / (target['shop_id'] + '.json')
        self.assertEqual(json.loads(setting.read_text(encoding='utf-8'))['status'], 'paused')
        self.assertEqual(baseline.read_bytes(), b'SYNTHETIC fixed baseline unchanged')
        self.assertEqual(history.read_bytes(), before)
        self.assertFalse((self.project / 'data').exists())

    def test_tracking_route_rejects_origin_and_strict_json_without_writes(self):
        target = self.register_stable()
        payload = {'target_id': target['target_id'], 'status': 'enabled', 'expected_revision': 0}
        cases = [(payload, {'Origin': 'https://foreign.invalid'}, None, 403),
                 (payload, {'Host': f'foreign.invalid:{self.port}'}, None, 403),
                 (payload, {'Content-Type': 'text/plain'}, None, 403),
                 (None, {}, b'{"status":"enabled","status":"paused"}', 422),
                 (None, {}, b'{"expected_revision":NaN}', 422),
                 (None, {}, b'\xff', 422),
                 ({**payload, 'expected_revision': True}, {}, None, 422),
                 ({**payload, 'expected_revision': -1}, {}, None, 422),
                 ({**payload, 'status': 'unconfigured'}, {}, None, 422),
                 ({**payload, 'shop_id': 'invented'}, {}, None, 422),
                 ({**payload, 'target_id': 'unknown'}, {}, None, 422)]
        count = len(self.publisher_calls)
        for value, headers, raw, expected in cases:
            code, body, _ = self.request('POST', '/__pdd_competitor_tracking', value, headers, raw)
            self.assertEqual(code, expected, (value, body))
            self.assertEqual(body['status'], 'failed')
        self.assertFalse((self.project / 'state/competitors/tracking').exists())
        self.assertEqual(len(self.publisher_calls), count)

    def test_tracking_build_failure_retains_saved_setting_but_never_claims_success(self):
        target = self.register_stable()
        self.publisher_result = RuntimeError('SYNTHETIC offline publisher failure')
        code, body, _ = self.request('POST', '/__pdd_competitor_tracking',
            {'target_id': target['target_id'], 'status': 'enabled', 'expected_revision': 0})
        self.assertEqual(code, 202)
        final = self.poll(body['job_id'])
        self.assertEqual(final['status'], 'failed')
        self.assertEqual(final['error_code'], 'dashboard_build_failed')
        self.assertFalse(final['receipt']['dashboard_built'])
        self.assertEqual(final['receipt']['setting_status'], 'saved')
        setting = self.project / 'state/competitors/tracking' / (target['shop_id'] + '.json')
        self.assertEqual(json.loads(setting.read_text(encoding='utf-8'))['status'], 'enabled')

    def test_tracking_storage_failure_returns_503_without_build_success(self):
        with patch.object(CompetitorService, 'update_tracking', side_effect=OSError('SYNTHETIC denied')):
            code, body, _ = self.request('POST', '/__pdd_competitor_tracking',
                {'target_id': 'synthetic', 'status': 'enabled', 'expected_revision': 0})
        self.assertEqual(code, 503)
        self.assertEqual(body['error_code'], 'local_file_unavailable')
        self.assertNotIn('receipt', body)
        self.assertFalse(self.publisher_calls)


if __name__ == '__main__':
    unittest.main(verbosity=2)
