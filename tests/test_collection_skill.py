import json
import sqlite3
import tempfile
import threading
import unittest
from contextlib import closing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.error import URLError

from scripts.collection_skill import Client, CollectionError, parser, resolve_shop, run, shops, summary


class CollectionSkillTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        (self.root / 'pdd_monitor').mkdir()
        (self.root / 'pdd_monitor/collection_service.py').write_text('# fixture')
        (self.root / 'data').mkdir()
        with closing(sqlite3.connect(self.root / 'data/monitor.sqlite3')) as db:
            db.executescript('CREATE TABLE shops(shop_id TEXT,shop_name TEXT); CREATE TABLE runs(run_id TEXT,shop_id TEXT);')
            db.executemany('INSERT INTO shops VALUES (?,?)', [('shop_a', '甲店'), ('shop_b', '乙店'), ('shop_empty', '待核验')])
            db.executemany('INSERT INTO runs VALUES (?,?)', [('run_a', 'shop_a'), ('run_b', 'shop_b')])
            db.commit()
        self.job = {'id': 'collect_fixture', 'shop_id': 'shop_a', 'kind': 'shop', 'status': 'queued'}
        self.state = {'browser_mode': 'local_chrome', 'local_worker_available': True,
                      'jobs': [self.job], 'latest_job': self.job}
        self.requests = []
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def reply(self, value):
                body = json.dumps(value).encode()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                owner.requests.append(('GET', self.path, self.headers.get('Origin')))
                self.reply(owner.state)

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                owner.requests.append(('POST', self.path, body))
                self.reply({'message': '已提交', 'job': owner.job})

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop_server)
        self.client = Client(self.server.server_port)

    def stop_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)

    def args(self, *values):
        return parser().parse_args(['--project', str(self.root), *values])

    def test_shop_list_only_observed_shops_readonly(self):
        before = (self.root / 'data/monitor.sqlite3').read_bytes()
        self.assertEqual({shop['shop_id'] for shop in shops(self.root)}, {'shop_a', 'shop_b'})
        self.assertEqual(before, (self.root / 'data/monitor.sqlite3').read_bytes())

    def test_missing_database_not_created(self):
        other = self.root / 'other'
        (other / 'pdd_monitor').mkdir(parents=True)
        (other / 'pdd_monitor/collection_service.py').write_text('# fixture')
        with self.assertRaises(CollectionError):
            shops(other)
        self.assertFalse((other / 'data/monitor.sqlite3').exists())

    def test_unknown_and_ambiguous_names_refused(self):
        with self.assertRaises(CollectionError):
            resolve_shop(self.root, '甲')
        with closing(sqlite3.connect(self.root / 'data/monitor.sqlite3')) as db:
            db.execute('UPDATE shops SET shop_name=? WHERE shop_id=?', ('甲店', 'shop_b'))
            db.commit()
        with self.assertRaises(CollectionError):
            resolve_shop(self.root, '甲店')
        self.assertEqual(resolve_shop(self.root, 'shop_a')['shop_id'], 'shop_a')

    def test_ordinal_current_other_and_empty_targets_never_access_service(self):
        for selector in ('一店', '二店', '当前店铺', '其他店铺', '', '【填写店铺完整名称或编号】'):
            with self.subTest(selector=selector), self.assertRaises(CollectionError):
                run(self.args('start', '--shop', selector), self.client)
        self.assertEqual(self.requests, [])

    def test_explicit_other_shop_controls_status_and_submission(self):
        self.job['shop_id'] = 'shop_b'
        self.state['shop_id'] = 'shop_b'
        self.state['entry'] = {'shop_id': 'shop_b'}
        self.state.update(jobs=[], latest_job=None)
        value = run(self.args('start', '--shop', '乙店'), self.client)
        self.assertEqual(value['shop_id'], 'shop_b')
        self.assertIn('shop_id=shop_b', self.requests[0][1])
        self.assertEqual(self.requests[-1], ('POST', '/__pdd_collection_start', {'shop_id': 'shop_b', 'kind': 'shop'}))

    def test_foreign_hidden_job_rejects_start_before_post(self):
        self.state['jobs'].append({**self.job, 'id': 'other_task', 'shop_id': 'shop_b'})
        with self.assertRaises(CollectionError) as caught:
            run(self.args('start', '--shop', '甲店'), self.client)
        self.assertEqual(caught.exception.reason, 'scope_mismatch')
        self.assertTrue(all(request[0] == 'GET' for request in self.requests))

    def test_foreign_entry_or_response_scope_rejects_start(self):
        for value in ({'shop_id': 'shop_b'}, {'entry': {'shop_id': 'shop_b'}}):
            with self.subTest(value=value):
                original = self.state.copy()
                self.state.update(value)
                with self.assertRaises(CollectionError) as caught:
                    run(self.args('start', '--shop', '甲店'), self.client)
                self.assertEqual(caught.exception.reason, 'scope_mismatch')
                self.state = original
        self.assertTrue(all(request[0] == 'GET' for request in self.requests))

    def test_wait_rejects_scope_changed_in_later_poll(self):
        def changed_scope(_seconds):
            self.state['jobs'].append({**self.job, 'id': 'foreign_task', 'shop_id': 'shop_b'})
        with self.assertRaises(CollectionError) as caught:
            run(self.args('wait', '--shop', '甲店', '--job', self.job['id'], '--seconds', '3'),
                self.client, sleeper=changed_scope)
        self.assertEqual(caught.exception.reason, 'scope_mismatch')
        self.assertEqual(len(self.requests), 2)
        self.assertTrue(all(request[0] == 'GET' for request in self.requests))

    def test_status_never_posts(self):
        value = run(self.args('status', '--shop', '甲店'), self.client)
        self.assertEqual(value['job']['status'], 'queued')
        self.assertTrue(all(request[0] == 'GET' for request in self.requests))
        self.assertEqual(self.requests[0][2], self.client.origin)

    def test_start_uses_existing_endpoint_once(self):
        self.state.update(jobs=[], latest_job=None)
        value = run(self.args('start', '--shop', '甲店'), self.client)
        posts = [request for request in self.requests if request[0] == 'POST']
        self.assertEqual(posts, [('POST', '/__pdd_collection_start', {'shop_id': 'shop_a', 'kind': 'shop'})])
        self.assertEqual(value['job']['id'], self.job['id'])
        self.assertEqual(value['job']['status'], 'queued')

    def test_iab_mode_does_not_post_or_fallback(self):
        self.state['browser_mode'] = 'codex_iab'
        self.state.update(jobs=[], latest_job=None)
        with self.assertRaises(CollectionError):
            run(self.args('start', '--shop', '甲店'), self.client)
        self.assertTrue(all(request[0] == 'GET' for request in self.requests))

    def test_sku_batch_uses_same_service(self):
        self.job['kind'] = 'sku_batch'
        self.state.update(jobs=[], latest_job=None)
        run(self.args('start', '--shop', '甲店', '--kind', 'sku_batch'), self.client)
        self.assertEqual(self.requests[-1][2], {'shop_id': 'shop_a', 'kind': 'sku_batch'})

    def test_existing_target_task_is_only_queried_without_submission(self):
        for kind in ('shop', 'sku_batch'):
            with self.subTest(kind=kind):
                self.job['kind'] = kind
                self.state['latest_sku_batch_job'] = self.job if kind == 'sku_batch' else None
                value = run(self.args('start', '--shop', '甲店', '--kind', kind), self.client)
                self.assertEqual(value['job']['id'], self.job['id'])
                self.assertIn('未重复提交', value['submission_message'])
        self.assertTrue(all(request[0] == 'GET' for request in self.requests))

    def test_cancel_rejects_other_shop_task(self):
        self.job['shop_id'] = 'shop_b'
        with self.assertRaises(CollectionError):
            run(self.args('cancel', '--shop', '甲店', '--job', self.job['id']), self.client)
        self.assertTrue(all(request[0] == 'GET' for request in self.requests))

    def test_partial_images_and_sku_remain_partial(self):
        self.job.update(status='partial', image_summary={'total': 10, 'saved': 9, 'missing': 1, 'status': 'partial'},
                        sku_summary={'status': 'not_started', 'reason': 'images_missing'})
        self.job['progress'] = {'cards': 10, 'cookie': 'fixture_secret', 'url': 'fixture_url'}
        value = summary({'shop_id': 'shop_a', 'shop_name': '甲店'}, self.state, self.job)
        self.assertEqual(value['job']['images']['missing'], 1)
        self.assertEqual(value['job']['sku']['status'], 'not_started')
        self.assertNotIn('fixture_secret', json.dumps(value))
        self.assertNotIn('fixture_url', json.dumps(value))

    def test_cross_shop_status_rejected(self):
        self.job['shop_id'] = 'shop_b'
        with self.assertRaises(CollectionError):
            run(self.args('status', '--shop', '甲店'), self.client)

    def test_terminal_summary_uses_verified_coverage_instead_of_stale_counter(self):
        self.job.update(status='complete', progress={'image_total': 10, 'image_saved': 9},
                        image_summary={'total': 10, 'saved': 10, 'missing': 0},
                        image_verification={'verified': True},
                        sku_summary={'status': 'not_started'})
        value = summary({'shop_id': 'shop_a', 'shop_name': '甲店'}, self.state, self.job)['job']
        self.assertEqual(value['progress'], {})
        self.assertEqual(value['images']['saved'], 10)
        self.assertTrue(value['images_verified'])
        self.assertEqual(value['sku']['status'], 'not_started')

    def test_wait_bounded_no_start_requests(self):
        value = run(self.args('wait', '--shop', '甲店', '--job', self.job['id'], '--seconds', '0'), self.client)
        self.assertTrue(value['still_running'])
        self.assertTrue(all(request[0] == 'GET' for request in self.requests))

    def test_post_timeout_never_retries(self):
        with patch.object(self.client.opener, 'open', side_effect=URLError('timeout')) as call:
            with self.assertRaises(CollectionError) as caught:
                self.client.request('/__pdd_collection_start', {'shop_id': 'shop_a', 'kind': 'shop'})
            self.assertEqual(caught.exception.reason, 'submission_unknown')
            self.assertEqual(call.call_count, 1)

    def test_no_redirects_and_only_supported_routes(self):
        with self.assertRaises(CollectionError):
            self.client.request('/arbitrary')
        self.assertEqual(self.requests, [])


if __name__ == '__main__':
    unittest.main()
