"""Real dashboard HTTP dispatch with synthetic, offline onboarding services only."""
from copy import deepcopy
import http.client
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import re
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
import webbrowser

sys.dont_write_bytecode = True
STAGE = Path(__file__).resolve().parent
sys.path.insert(0, str(STAGE.parent))
from pdd_monitor import dashboard_runtime


TARGET_ID = 'target_' + 'a1' * 12
OTHER_TARGET_ID = 'target_' + 'b2' * 12
ONBOARD_ID = 'onboard_' + 'c3' * 16
SHARE_URL = 'https://mobile.yangkeduo.com/mall_page.html?ps=Synthetic7A'
ONBOARD_ROUTE = '/__pdd_collection_onboard'
CANCEL_ROUTE = '/__pdd_collection_onboard_cancel'
COLLECT_ROUTE = '/__pdd_collection_onboard_collect'
DIRECTORY_ROUTE = '/__pdd_shop_directory'


class OtherServiceStub:
    def __init__(self, project, publisher=None):
        self.project = Path(project)
        self.publisher = publisher


class CollectionStub(OtherServiceStub):
    """Model the service boundary; validation internals have separate unit tests."""
    def __init__(self, project, publisher=None):
        super().__init__(project, publisher)
        self.calls = []
        self.errors = {}
        self.task = {'id': ONBOARD_ID, 'target_id': TARGET_ID, 'status': 'running',
                     'phase': 'probe', 'message': 'SYNTHETIC offline task'}
        self.start_reply = (202, self.task)
        self.cancel_reply = (202, {**self.task, 'status': 'cancelling'})
        self.target_has_task = False

    def _record(self, method, value):
        self.calls.append((method, deepcopy(value)))
        if method in self.errors:
            raise self.errors[method]

    @staticmethod
    def _identifier(value, prefix, length):
        if not isinstance(value, str) or not re.fullmatch(prefix + r'[0-9a-f]{' + str(length) + '}', value):
            raise ValueError('SYNTHETIC invalid identifier')

    @classmethod
    def _payload(cls, payload, field, prefix, length):
        if not isinstance(payload, dict) or set(payload) != {field}:
            raise ValueError('SYNTHETIC unexpected payload fields')
        cls._identifier(payload[field], prefix, length)

    def onboard(self, payload):
        self._record('onboard', payload)
        if not isinstance(payload, dict):
            raise ValueError('SYNTHETIC onboarding requires an object')
        fields = set(payload)
        if fields == {'target_id'}:
            self._identifier(payload['target_id'], 'target_', 24)
        elif fields == {'target_id', 'retry'}:
            self._identifier(payload['target_id'], 'target_', 24)
            if payload['retry'] is not True:
                raise ValueError('SYNTHETIC retry requires explicit true')
        elif fields == {'name', 'url'}:
            if ((payload['name'] is not None and not isinstance(payload['name'], str))
                    or not isinstance(payload['url'], str)):
                raise ValueError('SYNTHETIC invalid registration field types')
        else:
            raise ValueError('SYNTHETIC unexpected onboarding fields')
        return deepcopy(self.start_reply)

    def onboard_status(self, **kwargs):
        # Record only supplied keyword arguments: an extra None changes the contract.
        self._record('onboard_status', kwargs)
        if set(kwargs) == {'id'}:
            self._identifier(kwargs['id'], 'onboard_', 32)
            return deepcopy(self.task)
        if set(kwargs) == {'target_id'}:
            self._identifier(kwargs['target_id'], 'target_', 24)
            if self.target_has_task:
                return deepcopy(self.task)
            return {'status': 'idle', 'target_id': kwargs['target_id']}
        raise ValueError('SYNTHETIC ambiguous query')

    def onboard_cancel(self, payload):
        self._record('onboard_cancel', payload)
        self._payload(payload, 'id', 'onboard_', 32)
        return deepcopy(self.cancel_reply)

    def onboard_collect(self, payload):
        self._record('onboard_collect', payload)
        self._payload(payload, 'id', 'onboard_', 32)
        return deepcopy(self.start_reply)

    def shop_directory(self):
        self._record('shop_directory', {})
        return {'status': 'ok', 'targets': [], 'onboardings': [deepcopy(self.task)]}


class OnboardingHTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='onboarding_http_synthetic_', dir=STAGE)
        self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name)
        dist = self.project / 'dashboard/dist'
        dist.mkdir(parents=True)
        (dist / 'index.html').write_text('<!doctype html><title>SYNTHETIC HTTP test</title>', encoding='utf-8')
        self.collection = CollectionStub(self.project)
        self.patchers = []
        self.server = None
        self.thread = None
        self.errors = []
        ready = threading.Event()
        with socket.socket() as reservation:
            reservation.bind(('127.0.0.1', 0))
            self.port = reservation.getsockname()[1]
        real_connect = socket.create_connection

        def loopback_only(address, *args, **kwargs):
            if address != ('127.0.0.1', self.port):
                raise AssertionError('Only this ephemeral loopback server is allowed')
            return real_connect(address, *args, **kwargs)

        def factory(address, handler):
            self.assertEqual(address, ('127.0.0.1', self.port))
            # Suppress request logs while preserving the actual application Handler.
            handler.func.log_message = lambda *args: None
            self.server = ThreadingHTTPServer(address, handler)
            ready.set()
            return self.server

        def collection_factory(project, publisher=None):
            self.assertEqual(Path(project), self.project.resolve())
            self.collection.publisher = publisher
            return self.collection

        self.addCleanup(self.cleanup_server)
        for patcher in (
            patch.object(sqlite3, 'connect', side_effect=AssertionError('Business database access is forbidden')),
            patch.object(socket, 'create_connection', side_effect=loopback_only),
            patch.object(subprocess, 'Popen', side_effect=AssertionError('External processes are forbidden')),
            patch.object(webbrowser, 'open', side_effect=AssertionError('Browser access is forbidden')),
            patch('pdd_monitor.artist_heat_service.ArtistHeatService', OtherServiceStub),
            patch('pdd_monitor.profit_service.ProfitService', OtherServiceStub),
            patch('pdd_monitor.agent_service.AgentService', OtherServiceStub),
            patch('pdd_monitor.competitor_service.CompetitorService', OtherServiceStub),
            patch('pdd_monitor.collection_service.CollectionService', side_effect=collection_factory),
            patch.object(dashboard_runtime, 'build_dashboard', side_effect=AssertionError('Publishing is forbidden')),
            patch.object(dashboard_runtime, 'ThreadingHTTPServer', side_effect=factory),
        ):
            patcher.start()
            self.patchers.append(patcher)

        def serving():
            try:
                dashboard_runtime.serve(self.project, self.port)
            except Exception as error:
                self.errors.append(str(error))
                ready.set()

        self.thread = threading.Thread(target=serving, daemon=True)
        self.thread.start()
        self.assertTrue(ready.wait(3), 'Ephemeral loopback server did not start')
        self.assertFalse(self.errors)

    def cleanup_server(self):
        if self.server is not None:
            self.server.shutdown()
            self.server.server_close()
        if self.thread is not None:
            self.thread.join(3)
        for patcher in reversed(self.patchers):
            patcher.stop()

    def request(self, method, url, payload=None, headers=None, raw=None):
        body = raw if raw is not None else (json.dumps(payload).encode('utf-8') if payload is not None else None)
        actual_headers = {'Origin': f'http://127.0.0.1:{self.port}',
                          'Content-Type': 'application/json', 'Sec-Fetch-Site': 'same-origin'}
        for key, value in (headers or {}).items():
            if value is None:
                actual_headers.pop(key, None)
            else:
                actual_headers[key] = value
        actual_headers.setdefault('Host', f'127.0.0.1:{self.port}')
        actual_headers.setdefault('Connection', 'close')
        if body is not None:
            actual_headers.setdefault('Content-Length', str(len(body)))
        head = f'{method} {url} HTTP/1.1\r\n' + ''.join(
            f'{key}: {value}\r\n' for key, value in actual_headers.items()) + '\r\n'
        packet = head.encode('ascii') + (body or b'')
        # HTTPConnection.request sends headers and body separately. A header-gate
        # rejection can close the socket before that second send on Windows.
        # Send this small, complete request once; do not retry failed assertions.
        with socket.create_connection(('127.0.0.1', self.port), timeout=3) as connection:
            connection.sendall(packet)
            response = http.client.HTTPResponse(connection)
            try:
                response.begin()
                return response.status, json.loads(response.read()), dict(response.getheaders())
            finally:
                response.close()

    def test_same_origin_start_forwards_only_target_and_returns_flat_task(self):
        code, body, headers = self.request('POST', ONBOARD_ROUTE, {'target_id': TARGET_ID})
        self.assertEqual(code, 202)
        self.assertEqual(body, self.collection.task)
        self.assertNotIn('job', body)
        self.assertEqual(self.collection.calls, [('onboard', {'target_id': TARGET_ID})])
        self.assertEqual(headers['Cache-Control'], 'no-cache')
        self.assertNotIn('Access-Control-Allow-Origin', headers)
        self.assertTrue(callable(self.collection.publisher))
        self.assertFalse((self.project / 'state').exists())
        self.assertFalse((self.project / 'data').exists())

    def test_job_query_forwards_only_id_and_returns_flat_task(self):
        code, body, _ = self.request('GET', ONBOARD_ROUTE + '?id=' + ONBOARD_ID)
        self.assertEqual(code, 200)
        self.assertEqual(body, self.collection.task)
        self.assertEqual(self.collection.calls, [('onboard_status', {'id': ONBOARD_ID})])

    def test_raw_share_url_with_null_or_named_shop_is_forwarded_exactly(self):
        expected_calls = []
        for name in (None, 'SYNTHETIC 原始店名'):
            payload = {'name': name, 'url': SHARE_URL}
            with self.subTest(name=name):
                code, body, _ = self.request('POST', ONBOARD_ROUTE, payload)
                self.assertEqual(code, 202)
                self.assertEqual(body, self.collection.task)
                expected_calls.append(('onboard', payload))
                self.assertEqual(self.collection.calls, expected_calls)
        self.assertFalse((self.project / 'state').exists())
        self.assertFalse((self.project / 'data').exists())

    def test_explicit_retry_is_forwarded_with_only_target_and_true(self):
        payload = {'target_id': TARGET_ID, 'retry': True}
        code, body, _ = self.request('POST', ONBOARD_ROUTE, payload)
        self.assertEqual(code, 202)
        self.assertEqual(body, self.collection.task)
        self.assertEqual(self.collection.calls, [('onboard', payload)])

    def test_registration_and_retry_payloads_reject_mixed_or_ambiguous_fields(self):
        cases = [{'target_id': TARGET_ID, 'url': SHARE_URL},
                 {'target_id': TARGET_ID, 'name': None, 'url': SHARE_URL},
                 {'target_id': TARGET_ID, 'retry': True, 'url': SHARE_URL},
                 {'name': None, 'url': SHARE_URL, 'retry': True},
                 {'name': None, 'url': SHARE_URL, 'extra': 'synthetic'},
                 {'url': SHARE_URL}, {'name': None},
                 {'name': False, 'url': SHARE_URL}, {'name': None, 'url': None},
                 {'target_id': TARGET_ID, 'retry': False},
                 {'target_id': TARGET_ID, 'retry': 1},
                 {'target_id': TARGET_ID, 'retry': 'true'},
                 {'target_id': TARGET_ID, 'retry': None}, {'retry': True}]
        for payload in cases:
            with self.subTest(payload=payload):
                code, body, _ = self.request('POST', ONBOARD_ROUTE, payload)
                self.assertEqual(code, 422, body)
                self.assertNotIn('id', body)

    def test_target_without_task_returns_idle_and_exact_target(self):
        code, body, _ = self.request('GET', ONBOARD_ROUTE + '?target_id=' + OTHER_TARGET_ID)
        self.assertEqual(code, 200)
        self.assertEqual(body, {'status': 'idle', 'target_id': OTHER_TARGET_ID})
        self.assertNotIn('id', body)
        self.assertEqual(self.collection.calls, [('onboard_status', {'target_id': OTHER_TARGET_ID})])

    def test_target_with_task_returns_existing_task_without_starting_one(self):
        self.collection.target_has_task = True
        code, body, _ = self.request('GET', ONBOARD_ROUTE + '?target_id=' + TARGET_ID)
        self.assertEqual(code, 200)
        self.assertEqual(body, self.collection.task)
        self.assertEqual(self.collection.calls, [('onboard_status', {'target_id': TARGET_ID})])

    def test_cancel_forwards_only_id_and_returns_flat_task(self):
        code, body, _ = self.request('POST', CANCEL_ROUTE, {'id': ONBOARD_ID})
        self.assertEqual(code, 202)
        self.assertEqual(body, self.collection.cancel_reply[1])
        self.assertEqual(self.collection.calls, [('onboard_cancel', {'id': ONBOARD_ID})])

    def test_explicit_collect_is_a_separate_write_and_exact_id(self):
        code, body, _ = self.request('POST', COLLECT_ROUTE, {'id': ONBOARD_ID})
        self.assertEqual(code, 202)
        self.assertEqual(body, self.collection.task)
        self.assertEqual(self.collection.calls, [('onboard_collect', {'id': ONBOARD_ID})])
        for payload in ({}, {'id': ONBOARD_ID, 'shop_id': 'wrong'}, {'target_id': TARGET_ID}, {'id': None}):
            self.assertEqual(self.request('POST', COLLECT_ROUTE, payload)[0], 422)

    def test_directory_is_read_only_no_query_and_returns_current_targets(self):
        code, body, _ = self.request('GET', DIRECTORY_ROUTE)
        self.assertEqual(code, 200)
        self.assertEqual(body['onboardings'], [self.collection.task])
        self.assertEqual(self.collection.calls, [('shop_directory', {})])
        self.assertEqual(self.request('GET', DIRECTORY_ROUTE + '?shop_id=wrong')[0], 422)
        self.assertEqual(self.collection.calls, [('shop_directory', {})])
        self.assertFalse((self.project / 'data').exists())

    def test_localhost_same_origin_is_allowed(self):
        headers = {'Host': f'localhost:{self.port}', 'Origin': f'http://localhost:{self.port}',
                   'Referer': f'http://localhost:{self.port}/?view=warehouse'}
        code, _, _ = self.request('POST', ONBOARD_ROUTE, {'target_id': TARGET_ID}, headers)
        self.assertEqual(code, 202)
        self.assertEqual(self.collection.calls, [('onboard', {'target_id': TARGET_ID})])

    def test_cross_site_requests_never_reach_service(self):
        routes = [('POST', ONBOARD_ROUTE, {'target_id': TARGET_ID}),
                  ('GET', ONBOARD_ROUTE + '?id=' + ONBOARD_ID, None),
                  ('POST', CANCEL_ROUTE, {'id': ONBOARD_ID}),
                  ('POST', COLLECT_ROUTE, {'id': ONBOARD_ID}),
                  ('GET', DIRECTORY_ROUTE, None)]
        headers = [{'Origin': 'https://foreign.invalid'},
                   {'Host': f'foreign.invalid:{self.port}'},
                   {'Referer': 'https://foreign.invalid/path'},
                   {'Sec-Fetch-Site': 'cross-site'}, {'Sec-Fetch-Site': 'same-site'}]
        for method, route, payload in routes:
            for header in headers:
                with self.subTest(method=method, route=route, headers=header):
                    code, body, response_headers = self.request(method, route, payload, header)
                    self.assertEqual(code, 403, body)
                    self.assertNotIn('Access-Control-Allow-Origin', response_headers)
        self.assertEqual(self.collection.calls, [])

    def test_write_transport_requires_origin_json_and_bounded_length(self):
        invalid_headers = [{'Origin': None}, {'Content-Type': 'text/plain'},
                           {'Content-Length': '32769'}, {'Content-Length': '0'},
                           {'Content-Length': '-1'}, {'Transfer-Encoding': 'chunked'}]
        for route, payload in ((ONBOARD_ROUTE, {'target_id': TARGET_ID}), (CANCEL_ROUTE, {'id': ONBOARD_ID}), (COLLECT_ROUTE, {'id': ONBOARD_ID})):
            for headers in invalid_headers:
                with self.subTest(route=route, headers=headers):
                    code, body, _ = self.request('POST', route, payload, headers)
                    self.assertEqual(code, 403, body)
        self.assertEqual(self.collection.calls, [])

    def test_strict_json_errors_do_not_reach_service(self):
        invalid_raw = [b'{"target_id":"x","target_id":"y"}',
                       b'{"id":"x","id":"y"}', b'{"x":NaN}',
                       b'{"x":Infinity}', b'{"x":-Infinity}', b'\xff',
                       b'{"target_id":', b'{} {}', b'{"outer":{"x":1,"x":2}}']
        for route in (ONBOARD_ROUTE, CANCEL_ROUTE, COLLECT_ROUTE):
            for raw in invalid_raw:
                with self.subTest(route=route, raw=raw):
                    code, body, _ = self.request('POST', route, raw=raw)
                    self.assertEqual(code, 422, body)
        self.assertEqual(self.collection.calls, [])

    def test_payload_validation_errors_become_422_without_success_claim(self):
        # Payload shape belongs to the service; this verifies its errors are translated.
        cases = [(ONBOARD_ROUTE, {'target_id': TARGET_ID, 'url': 'https://foreign.invalid'}),
                 (ONBOARD_ROUTE, {'shop_id': 'synthetic'}),
                 (ONBOARD_ROUTE, {'target_id': True}), (ONBOARD_ROUTE, {'target_id': 'target_bad'}),
                 (CANCEL_ROUTE, {'id': ONBOARD_ID, 'target_id': TARGET_ID}),
                 (CANCEL_ROUTE, {'id': ['not text']}), (CANCEL_ROUTE, {'id': 'onboard_bad'})]
        for route, payload in cases:
            with self.subTest(route=route, payload=payload):
                code, body, _ = self.request('POST', route, payload)
                self.assertEqual(code, 422, body)
                self.assertNotIn('id', body)
        for route in (ONBOARD_ROUTE, CANCEL_ROUTE):
            for raw in (b'null', b'[]', b'"target"', b'1', b'{}'):
                with self.subTest(route=route, raw=raw):
                    code, body, _ = self.request('POST', route, raw=raw)
                    self.assertEqual(code, 422, body)

    def test_extra_duplicate_or_ambiguous_query_is_rejected_before_dispatch(self):
        queries = ['', '?', '?unexpected=x', '?id=' + ONBOARD_ID + '&extra=1',
                   '?target_id=' + TARGET_ID + '&extra=',
                   '?id=' + ONBOARD_ID + '&id=' + ONBOARD_ID,
                   '?id=' + ONBOARD_ID + '&id=',
                   '?target_id=' + TARGET_ID + '&target_id=' + OTHER_TARGET_ID,
                   '?id=' + ONBOARD_ID + '&target_id=' + TARGET_ID]
        for suffix in queries:
            with self.subTest(query=suffix):
                code, body, _ = self.request('GET', ONBOARD_ROUTE + suffix)
                self.assertEqual(code, 422, body)
        self.assertEqual(self.collection.calls, [])

    def test_invalid_query_identifiers_are_rejected(self):
        queries = ['?id=', '?id=onboard_bad', '?id=' + 'onboard_' + 'A' * 32,
                   '?id=' + ONBOARD_ID + '%2F..', '?target_id=', '?target_id=target_bad',
                   '?target_id=' + 'target_' + 'a' * 25]
        for suffix in queries:
            with self.subTest(query=suffix):
                code, body, _ = self.request('GET', ONBOARD_ROUTE + suffix)
                self.assertEqual(code, 422, body)

    def test_post_query_is_not_a_second_operation_entry(self):
        for route, payload in ((ONBOARD_ROUTE, {'target_id': TARGET_ID}), (CANCEL_ROUTE, {'id': ONBOARD_ID})):
            code, _, _ = self.request('POST', route + '?id=' + ONBOARD_ID, payload)
            self.assertEqual(code, 404)
        self.assertEqual(self.collection.calls, [])

    def test_service_io_failures_return_503_for_start_status_and_cancel(self):
        secret = 'SYNTHETIC private path must not be sent to the client'
        for method in ('onboard', 'onboard_status', 'onboard_cancel'):
            self.collection.errors[method] = OSError(secret)
        requests = [('POST', ONBOARD_ROUTE, {'target_id': TARGET_ID}),
                    ('GET', ONBOARD_ROUTE + '?id=' + ONBOARD_ID, None),
                    ('GET', ONBOARD_ROUTE + '?target_id=' + TARGET_ID, None),
                    ('POST', CANCEL_ROUTE, {'id': ONBOARD_ID})]
        for method, route, payload in requests:
            with self.subTest(method=method, route=route):
                code, body, _ = self.request(method, route, payload)
                self.assertEqual(code, 503, body)
                self.assertNotIn(secret, json.dumps(body))
                self.assertNotIn('id', body)
        self.assertFalse((self.project / 'state').exists())
        self.assertFalse((self.project / 'data').exists())

    def test_returned_service_status_and_result_are_preserved(self):
        conflict = {'status': 'conflict', 'reason': 'SYNTHETIC different task active'}
        self.collection.start_reply = (409, conflict)
        code, body, _ = self.request('POST', ONBOARD_ROUTE, {'target_id': TARGET_ID})
        self.assertEqual((code, body), (409, conflict))
        missing = {'status': 'not_found', 'reason': 'SYNTHETIC missing task'}
        self.collection.cancel_reply = (404, missing)
        code, body, _ = self.request('POST', CANCEL_ROUTE, {'id': ONBOARD_ID})
        self.assertEqual((code, body), (404, missing))


if __name__ == '__main__':
    unittest.main(verbosity=2)
