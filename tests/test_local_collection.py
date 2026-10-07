from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from types import SimpleNamespace
import base64
import sqlite3
import subprocess
import shutil
import hashlib
import zipfile
from contextlib import closing

from pdd_monitor.collection_service import (CollectionService, next_due, validate_schedule,
    public_probe_url, confirmed_shop_release, released_image_summary)
from pdd_monitor.sku_store import price_value, validate_capture, _write_capture, _read_capture

A='shop_'+'a'*24
B='shop_'+'b'*24
POINT=datetime(2026,10,5,0,0,tzinfo=timezone.utc)


def sealed_result(value):
    """Synthetic worker-return fixture; real persistence is tested separately."""
    capture_id = 'capture_' + 'c' * 32
    digest = 'd' * 64
    receipt = value['receipt']
    receipt.update(job_id=capture_id, snapshot_sha256=digest, run_id='run_' + digest[:24])
    receipt['journal'] = {'job_id': capture_id, 'shop_id': receipt['shop_id'],
        'run_id': receipt['run_id'], 'attempt_id': 'attempt_' + 'e' * 32,
        'accepted_into_history': True, 'journal_status': 'complete',
        'session': {'phase': 'complete', 'importReady': True, 'snapshot': {'sha256': digest}}}
    return {**value, 'capture_job_id': capture_id}


class Service(CollectionService):
    def _shop(self, shop_id):
        if shop_id not in (A,B):
            raise ValueError('unknown shop')
        return {'shop_name':shop_id, 'source_url':'https://mobile.yangkeduo.com/mall_page.html?mall_id=1'}


class CollectionTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)
        self.time=POINT
        self.calls=[]
        self.result={'status':'complete','message':'test'}
        self.service=Service(self.root,runner=self.run_worker,clock=lambda:self.time,autostart=False)
        # These worker-return unit tests do not create business/capture journals.
        # TargetShopBackendTests covers the independent committed-data boundary.
        def verify_fixture(project, job, result):
            if not confirmed_shop_release(job, result):
                raise ValueError('Unconfirmed synthetic release')
            return released_image_summary(result['receipt'], result.get('image_summary'))
        verifier = patch('pdd_monitor.collection_service.verify_shop_release', side_effect=verify_fixture)
        verifier.start(); self.addCleanup(verifier.stop)

    def tearDown(self):
        self.temp.cleanup()

    def run_worker(self,job):
        self.calls.append(job)
        return self.result

    def schedule(self,shop=A,**overrides):
        payload={'shop_id':shop,'revision':0,'enabled':True,'mode':'daily','times':['08:01','20:00'],'at':''}
        payload.update(overrides)
        return self.service.schedule(payload)

    def test_beijing_times_and_strict_future(self):
        value={'enabled':True,'mode':'daily','times':['08:00','20:00']}
        self.assertEqual(datetime.fromisoformat(next_due(value,POINT)),POINT+timedelta(hours=12))
        self.assertIsNone(next_due({**value,'enabled':False},POINT))

    def test_invalid_clocks_modes_extra_fields(self):
        for override in ({'times':['25:00']},{'times':['08:00','08:00']},{'times':[]},{'mode':'weekly'},{'revision':True},{'url':'https://evil.example'}):
            with self.subTest(override=override), self.assertRaises((ValueError,TypeError)):
                self.schedule(**override)

    def test_once_is_future_and_one_execution(self):
        code,result=self.schedule(mode='once',times=[],at='2026-10-05T08:01')
        self.assertEqual(code,200)
        self.time+=timedelta(minutes=1)
        self.service.tick();self.service.tick()
        self.assertEqual(len(self.calls),1)
        self.assertFalse(self.service.status(A)['schedule']['enabled'])
        with self.assertRaises(ValueError):
            self.schedule(mode='once',at='2026-10-05T07:00')

    def test_revision_conflict_and_shop_independence(self):
        self.schedule();self.schedule(B,times=['09:00'])
        self.assertEqual(self.schedule(times=['10:00'])[0],409)
        self.assertEqual(self.service.status(A)['schedule']['times'],['08:01','20:00'])
        self.assertEqual(self.service.status(B)['schedule']['times'],['09:00'])

    def test_schedule_persists_without_enabling_defaults(self):
        self.assertFalse(self.service.status(A)['schedule']['enabled'])
        self.schedule()
        restored=Service(self.root,runner=self.run_worker,clock=lambda:self.time,autostart=False)
        self.assertEqual(restored.status(A)['schedule']['revision'],1)

    def test_manual_double_click_is_idempotent(self):
        first=self.service.start({'shop_id':A,'kind':'shop'})[1]['job']
        second=self.service.start({'shop_id':A,'kind':'shop'})[1]['job']
        self.assertEqual(first['id'],second['id'])
        self.service.tick();self.service.tick()
        self.assertEqual(len(self.calls),1)

    def test_due_once_and_no_historical_replay(self):
        self.schedule();self.time+=timedelta(minutes=1)
        self.service.tick();self.service.tick()
        self.assertEqual(len(self.calls),1)
        self.assertEqual(self.service.status(A)['jobs'][0]['trigger'],'scheduled')

    def test_missed_due_is_recorded_and_not_run(self):
        self.schedule();self.time+=timedelta(hours=2)
        self.service.tick()
        self.assertEqual(self.calls,[])
        self.assertEqual(self.service.status(A)['jobs'][0]['status'],'missed')

    def test_login_stops_automatic_retries_manual_can_clear(self):
        self.schedule();self.result={'status':'needs_login','message':'login'}
        self.time+=timedelta(minutes=1);self.service.tick()
        self.assertTrue(self.service.status(A)['schedule']['blocked_by'])
        self.time+=timedelta(days=1);self.service.tick()
        self.assertEqual(len(self.calls),1)
        self.result={'status':'complete','message':'ok'}
        self.service.start({'shop_id':A,'kind':'shop'});self.service.tick()
        self.assertIsNone(self.service.status(A)['schedule']['blocked_by'])

    def test_cancel_queue_and_wrong_shop(self):
        job=self.service.start({'shop_id':A,'kind':'shop'})[1]['job']
        self.assertEqual(self.service.cancel({'shop_id':B,'id':job['id']})[0],409)
        self.assertEqual(self.service.cancel({'shop_id':A,'id':job['id']})[0],200)
        self.service.tick();self.assertEqual(self.calls,[])

    def test_chrome_permission_stops_without_login_or_link_replacement(self):
        self.schedule()
        self.result={'status':'needs_browser','reason':'connection_required','message':'Allow Chrome connection'}
        self.time+=timedelta(minutes=1);self.service.tick()
        value=self.service.status(A)
        self.assertEqual(value['latest_job']['status'],'needs_browser')
        self.assertEqual(value['recovery_reason'],'connection_required')
        self.assertFalse(value['entry']['configured'])
        self.assertTrue(value['schedule']['blocked_by'])
        self.time+=timedelta(days=1);self.service.tick()
        self.assertEqual(len(self.calls),1)
        self.service.start({'shop_id':A,'kind':'shop'});self.service.tick()
        self.assertEqual(len(self.calls),2)

    def test_restart_does_not_adopt_running_owner(self):
        job=self.service.start({'shop_id':A,'kind':'shop'})[1]['job']
        self.service.state['jobs'][0]['status']='running';self.service._save()
        restored=Service(self.root,runner=self.run_worker,clock=lambda:self.time,autostart=False)
        self.assertEqual(restored.status(A)['jobs'][0]['status'],'interrupted')
        restored.tick();self.assertEqual(self.calls,[])

    def test_unknown_scope_and_urls_rejected(self):
        for payload in ({'shop_id':'wrong','kind':'shop'},{'shop_id':A,'kind':'shop','url':'https://evil'},{'shop_id':A,'kind':'sku'},{'shop_id':A,'kind':'other'}):
            with self.assertRaises(ValueError):self.service.start(payload)

    def test_worker_failure_is_explicit(self):
        self.service.runner=lambda job: (_ for _ in ()).throw(OSError('synthetic'))
        self.service.start({'shop_id':A,'kind':'shop'});self.service.tick()
        self.assertEqual(self.service.status(A)['jobs'][0]['status'],'failed')
        self.assertIsNone(self.service.active)

    def test_fixed_pipeline_metrics_and_observed_review_cause_are_preserved(self):
        self.result = {'status':'needs_login','reason':'login_or_session_required','message':'synthetic login'}
        self.service.start({'shop_id':A,'kind':'shop'})
        with patch('pdd_monitor.collection_service.time.perf_counter', side_effect=[10.0, 12.75]):
            self.service.tick()
        result = self.service.status(A)['jobs'][0]
        self.assertEqual((result['elapsed_seconds'],result['ai_requests'],result['execution_mode']),(2.75,0,'fixed_program'))
        self.assertEqual((result['status'],result['reason'],result['message']),('needs_login','login_or_session_required','synthetic login'))

    def test_dashboard_build_requires_confirmed_same_shop_release(self):
        job = self.service.start({'shop_id':A,'kind':'shop'})[1]['job']
        directory = self.service.directory/job['id']; directory.mkdir(parents=True)
        valid = {'status':'complete','message':'saved','elapsed_seconds':2.5,
                 'receipt':{'ok':True,'status':'finished','shop_id':A,'run_id':'run_synthetic',
                            'snapshot_status':'complete','new_run':{'status':'complete','cards':3,'image_refs':3}}}
        valid = sealed_result(valid)
        for change, returncode in (({'receipt':{}},0), ({'receipt':{**valid['receipt'],'shop_id':B}},0), ({},1)):
            with self.subTest(change=change,returncode=returncode):
                (directory/'result.json').write_text(json.dumps({**valid,**change}),encoding='utf-8')
                with patch('pdd_monitor.collection_service.subprocess.run',return_value=SimpleNamespace(returncode=returncode)),patch.object(self.service,'publisher') as publish:
                    result=self.service._execute(job)
                publish.assert_not_called()
                self.assertEqual(result['status'],'manual_review')
                self.assertEqual(result['reason'],'release_receipt_unconfirmed')
        (directory/'result.json').write_text(json.dumps(valid),encoding='utf-8')
        with patch('pdd_monitor.collection_service.subprocess.run',return_value=SimpleNamespace(returncode=0)),patch.object(self.service,'publisher',side_effect=OSError('SYNTHETIC build failure')) as publish:
            result=self.service._execute(job)
        publish.assert_called_once_with(self.root)
        self.assertEqual(result['status'],'complete')
        self.assertFalse(result['dashboard_built'])
        self.assertEqual(result['reason'],'dashboard_build_failed')
        self.assertEqual(result['worker_elapsed_seconds'],2.5)
        self.assertGreaterEqual(result['dashboard_elapsed_seconds'],0)
        with patch('pdd_monitor.collection_service.subprocess.run',return_value=SimpleNamespace(returncode=0)),patch.object(self.service,'publisher') as publish:
            def inspect_stage(project):
                phase=json.loads((directory/'progress.json').read_text(encoding='utf-8'))
                self.assertEqual(phase['phase'],'正在更新数据舱')
                self.assertEqual(phase['stage'],'dashboard_build')
                self.assertEqual((phase['image_total'],phase['image_saved'],phase['image_missing']),(3,3,0))
                self.assertEqual(phase['at'],self.time.isoformat())
                return {'timings': {'snapshot_seconds': 2.12, 'canonical_build_seconds': 5.5,
                                   'total_seconds': 8.25, 'decision_seconds': float('nan'),
                                   'history_export_seconds': -1, 'history_preflight_seconds': True,
                                   'raw_private_key': 'SYNTHETIC_PRIVATE'}}
            publish.side_effect=inspect_stage
            result=self.service._execute(job)
        publish.assert_called_once_with(self.root)
        self.assertTrue(result['dashboard_built'])
        self.assertEqual(result['dashboard_stages_seconds'],
                         {'snapshot_seconds':2.12,'canonical_build_seconds':5.5,'total_seconds':8.25})
        self.assertGreaterEqual(result['dashboard_elapsed_seconds'],0)

    def test_public_probe_does_not_bind_shop_or_publish(self):
        url='https://mobile.yangkeduo.com/mall_page.html?ps=SYNTHETIC_ONLY'
        job=self.service.probe({'url':url})[1]['job']
        self.assertIsNone(job['shop_id'])
        self.assertEqual(self.service.probe({'url':url})[1]['job']['id'],job['id'])
        self.service.tick()
        self.assertEqual(self.service.probe_status(job['id'])['status'],'complete')
        self.assertEqual(self.service.status(A)['jobs'],[])
        shop_job=self.service.start({'shop_id':A,'kind':'shop'})[1]['job']
        with self.assertRaises(ValueError):self.service.probe_status(shop_job['id'])

    def test_missing_images_publish_complete_card_release_and_keep_partial_status(self):
        job = self.service.start({'shop_id': A, 'kind': 'shop'})[1]['job']
        directory = self.service.directory / job['id']; directory.mkdir(parents=True)
        result = {'status': 'partial', 'reason': 'images_missing', 'message': '3 cards; 1 missing',
                  'image_summary': {'total': 3, 'saved': 2, 'missing': 1, 'status': 'partial'},
                  'receipt': {'ok': True, 'status': 'finished', 'shop_id': A, 'run_id': 'run_synthetic',
                              'snapshot_status': 'complete', 'new_run': {'status': 'complete', 'cards': 3, 'image_refs': 2}}}
        result = sealed_result(result)
        (directory / 'result.json').write_text(json.dumps(result), encoding='utf-8')
        with patch('pdd_monitor.collection_service.subprocess.run', return_value=SimpleNamespace(returncode=0)), patch.object(self.service, 'publisher') as publish:
            actual = self.service._execute(job)
        publish.assert_called_once_with(self.root)
        self.assertEqual((actual['status'], actual['image_summary']['missing']), ('partial', 1))
        self.assertTrue(actual['dashboard_built'])
        for change in ({'receipt': {**result['receipt'], 'shop_id': B}},
                       {'receipt': {**result['receipt'], 'snapshot_status': 'partial'}},
                       {'image_summary': {**result['image_summary'], 'saved': 3}},
                       {'image_summary': {**result['image_summary'], 'missing': 0}}):
            with self.subTest(change=change):
                (directory / 'result.json').write_text(json.dumps({**result, **change}), encoding='utf-8')
                with patch('pdd_monitor.collection_service.subprocess.run', return_value=SimpleNamespace(returncode=0)), patch.object(self.service, 'publisher') as publish:
                    actual = self.service._execute(job)
                publish.assert_not_called()
                self.assertEqual(actual['reason'], 'release_receipt_unconfirmed')
        (directory / 'result.json').write_text(json.dumps({'status': 'partial', 'message': 'scan incomplete'}), encoding='utf-8')
        with patch('pdd_monitor.collection_service.subprocess.run', return_value=SimpleNamespace(returncode=0)), patch.object(self.service, 'publisher') as publish:
            actual = self.service._execute(job)
        publish.assert_not_called()
        self.assertEqual(actual['status'], 'partial')

    def test_worker_complete_without_independent_image_counts_cannot_claim_completion(self):
        job = self.service.start({'shop_id': A, 'kind': 'shop'})[1]['job']
        directory = self.service.directory / job['id']; directory.mkdir(parents=True)
        receipt = {'ok': True, 'status': 'finished', 'shop_id': A, 'run_id': 'run_synthetic',
                   'snapshot_status': 'complete', 'new_run': {'status': 'complete', 'cards': 3, 'image_refs': 2}}
        result = {'status': 'complete', 'receipt': receipt}
        result = sealed_result(result)
        (directory / 'result.json').write_text(json.dumps(result), encoding='utf-8')
        with patch('pdd_monitor.collection_service.subprocess.run', return_value=SimpleNamespace(returncode=0)), patch.object(self.service, 'publisher') as publish:
            actual = self.service._execute(job)
        self.assertEqual(actual['status'], 'partial')
        self.assertEqual(actual['image_summary']['missing'], 1)
        publish.assert_called_once()
        for bad in ({**receipt, 'new_run': {'status': 'complete', 'cards': 3}},
                    {**receipt, 'new_run': {'status': 'complete', 'cards': 3, 'image_refs': True}}):
            (directory / 'result.json').write_text(json.dumps({**result, 'receipt': bad}), encoding='utf-8')
            with patch('pdd_monitor.collection_service.subprocess.run', return_value=SimpleNamespace(returncode=0)), patch.object(self.service, 'publisher') as publish:
                actual = self.service._execute(job)
            self.assertEqual(actual['status'], 'manual_review'); publish.assert_not_called()

    def test_image_browser_stop_blocks_schedule_after_card_publication(self):
        self.schedule()
        for status in ('complete', 'partial'):
            for stop in ('needs_login', 'needs_browser', 'manual_review', 'cancelled', 'interrupted', 'failed'):
                with self.subTest(status=status, stop=stop):
                    self.result = {'status': status, 'image_repair_stopped': True,
                                   'image_repair': {'status': 'partial', 'stop_status': stop}}
                    job = self.service.start({'shop_id': A, 'kind': 'shop'})[1]['job']
                    self.service.tick()
                    self.assertEqual(self.service.status(A)['schedule']['blocked_by'], job['id'])
                    calls = len(self.calls); self.time += timedelta(days=1); self.service.tick()
                    self.assertEqual(len(self.calls), calls)
        self.result = {'status': 'partial', 'reason': 'images_missing'}
        self.service.start({'shop_id': A, 'kind': 'shop'}); self.service.tick()
        self.assertIsNone(self.service.status(A)['schedule']['blocked_by'])

    def test_image_stop_new_and_legacy_flags_both_block_schedule_without_sku_work(self):
        self.schedule()
        for fields in ({'image_repair_stopped': True}, {'sku_followup_available': False}):
            self.result = {'status': 'complete', **fields}
            job = self.service.start({'shop_id': A, 'kind': 'shop'})[1]['job']
            self.service.tick()
            self.assertEqual(self.service.status(A)['schedule']['blocked_by'], job['id'])

    def test_public_probe_rejects_external_and_secret_fields(self):
        for url in ('https://evil.example/mall_page.html?ps=test',
                    'https://mobile.yangkeduo.com@evil.example/mall_page.html?ps=test',
                    'https://mobile.yangkeduo.com/mall_page.html?ps=test&ps=other',
                    'https://mobile.yangkeduo.com/mall_page.html?mall_sn=test&token=secret',
                    'https://mobile.yangkeduo.com/login.html?ps=test',
                    'http://mobile.yangkeduo.com/mall_page.html?ps=test'):
            with self.subTest(url=url),self.assertRaises(ValueError):public_probe_url(url)
        url='https://mobile.yangkeduo.com/mall_page.html?mall_sn=abc%2Bdef%3D&decrypt_mall_sn=1&force_use_web_bundle=1'
        self.assertEqual(public_probe_url(url),url)

    def test_right_panel_policy_blocks_external_worker_and_timers(self):
        self.schedule()
        policy=self.root/'state/local_collection/browser_policy.json'
        policy.write_text(json.dumps({'mode':'codex_iab'}),encoding='utf-8')
        code, result = self.service.start({'shop_id':A,'kind':'shop'})
        self.assertEqual(code,202)
        self.assertEqual(result['job']['status'],'awaiting_host')
        self.assertEqual(self.service.probe({'url':'https://mobile.yangkeduo.com/goods1.html?ps=test'})[0],409)
        self.assertEqual(self.schedule(revision=1)[0],409)
        self.time+=timedelta(minutes=2);self.service.tick()
        self.assertEqual(self.calls,[])
        self.assertFalse(self.service.status(A)['local_worker_available'])
        self.assertFalse(self.service.status(A)['scheduler_running'])
        from pdd_monitor.collection_worker import work
        with patch('pdd_monitor.collection_worker.subprocess.run') as process:
            result=work(self.root,'collect_'+'a'*32)
        self.assertEqual(result['status'],'manual_review');process.assert_not_called()

    def test_timer_dispatches_other_shops_during_active_worker(self):
        started=threading.Event();finish=threading.Event()
        def slow(job):
            started.set();finish.wait(3);return {'status':'complete','message':'done'}
        self.service.runner=slow;self.service.start({'shop_id':A,'kind':'shop'});self.service.tick(background=True)
        self.assertTrue(started.wait(2))
        self.schedule(B);self.time+=timedelta(minutes=1);self.service.tick(background=True)
        self.assertEqual(self.service.status(B)['jobs'][0]['status'],'queued')
        self.assertEqual(self.service.active,self.service.state['jobs'][0]['id'])
        finish.set()
        for thread in threading.enumerate():
            if thread.name=='pdd-collection-worker':thread.join(3)


class SkuTests(unittest.TestCase):
    def capture(self):
        return {'goods_id':'123','goods_url':'https://mobile.yangkeduo.com/goods.html?goods_id=123','observed_at':'2026-10-05T00:00:00Z','identity_basis':'recorded_public_goods_link','status':'complete','all_combinations_visited':True,'option_catalog_verified':True,'variants':[{'specs':[{'name':'款式','value':'A'}],'price_raw':'券后¥4.50','selection_verified':True,'available':True,'sku_id':None}]}

    def test_price_ranges_and_multiple_amounts_unknown(self):
        for raw in ('¥3-5','¥3—¥5','¥3至5','原价¥5 券后¥3',None,'3.50','¥3.123'):
            self.assertIsNone(price_value(raw),raw)
        self.assertEqual(price_value('券后¥4.50'),'4.50')

    def test_selected_variant_price_binding(self):
        value=self.capture();self.assertEqual(validate_capture(value)['variants'][0]['price_yuan'],'4.50')
        value['variants'][0]['selection_verified']=False
        self.assertIsNone(validate_capture(value)['variants'][0]['price_yuan'])

    def test_completion_and_duplicate_specs_rejected(self):
        value=self.capture();value['all_combinations_visited']=False
        with self.assertRaises(ValueError):validate_capture(value)
        value=self.capture();value['variants']*=2
        with self.assertRaises(ValueError):validate_capture(value)

    def test_wrong_product_host_id_rejected(self):
        for url in ('https://evil.example/?goods_id=123','https://mobile.yangkeduo.com/goods.html?goods_id=456','https://mobile.yangkeduo.com/goods.html?goods_id=123&goods_id=123'):
            value=self.capture();value['goods_url']=url
            with self.assertRaises(ValueError):validate_capture(value)

    def test_immutable_image_blob_is_hash_verified_on_read(self):
        with tempfile.TemporaryDirectory() as name:
            directory=Path(name)/'SYNTHETIC_ONLY'
            value=self.capture();value['variants'][0]['image_url']='https://img.pddpic.com/SYNTHETIC_ONLY.png'
            value.update(capture_id='synthetic_only',shop_id=A,observation_id=1)
            png=base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aBf8AAAAASUVORK5CYII=')
            _write_capture(directory,value,{'https://img.pddpic.com/SYNTHETIC_ONLY.png':{'mime':'image/png','base64':base64.b64encode(png).decode()}})
            self.assertTrue(_read_capture(directory)['variants'][0]['image_data_url'].startswith('data:image/png;base64,'))
            with closing(sqlite3.connect(directory/'images.sqlite3')) as db:
                db.execute("UPDATE assets SET data=?",(b'CORRUPTED_SYNTHETIC_ONLY',))
                db.commit()
            with self.assertRaises(ValueError):_read_capture(directory)


class WorkerRequestTests(unittest.TestCase):
    def test_dashboard_launcher_selects_chrome_without_default_browser(self):
        from pdd_monitor.dashboard_runtime import _open_chrome
        with tempfile.TemporaryDirectory() as name:
            chrome=Path(name)/'chrome.exe';chrome.touch()
            with patch.dict('os.environ',{'PDD_CHROME':str(chrome)},clear=True),patch('pdd_monitor.dashboard_runtime.shutil.which',return_value=None),patch('pdd_monitor.dashboard_runtime.subprocess.Popen') as launch:
                _open_chrome('http://127.0.0.1:8878/')
            self.assertEqual(launch.call_args.args[0],[str(chrome),'http://127.0.0.1:8878/'])

    def test_dashboard_launcher_reports_missing_chrome_without_fallback(self):
        from pdd_monitor.dashboard_runtime import _open_chrome
        with tempfile.TemporaryDirectory() as name:
            with patch.dict('os.environ',{'LOCALAPPDATA':name,'PROGRAMFILES':name,'PROGRAMFILES(X86)':name},clear=True),patch('pdd_monitor.dashboard_runtime.shutil.which',return_value=None),patch('pdd_monitor.dashboard_runtime.subprocess.Popen') as launch:
                with self.assertRaisesRegex(ValueError,'Chrome'):_open_chrome('http://127.0.0.1:8878/')
                launch.assert_not_called()

    def test_verified_share_entry_preserves_shop_and_canonical_scope(self):
        from pdd_monitor.collection_worker import storefront_entry
        with tempfile.TemporaryDirectory() as name:
            project=Path(name);folder=project/'state/competitors/intakes';folder.mkdir(parents=True)
            canonical='https://mobile.yangkeduo.com/mall_page.html?mall_id=1'
            entry='https://mobile.yangkeduo.com/mall_page.html?ps=SYNTHETIC_ONLY'
            record={'shop_id':A,'resolved_storefront_url':canonical,'original_input_url':entry}
            (folder/'synthetic.json').write_text(json.dumps(record),encoding='utf-8')
            self.assertEqual(storefront_entry(project,A,canonical),entry)
            self.assertEqual(storefront_entry(project,B,canonical),canonical)
            self.assertEqual(storefront_entry(project,A,canonical+'&other=1'),canonical+'&other=1')
            for unsafe in ('https://evil.example/mall_page.html?ps=x','https://mobile.yangkeduo.com/goods.html?ps=x','https://mobile.yangkeduo.com/mall_page.html?ps=x&ps=y'):
                record['original_input_url']=unsafe
                (folder/'synthetic.json').write_text(json.dumps(record),encoding='utf-8')
                self.assertEqual(storefront_entry(project,A,canonical),canonical)

    def test_browser_request_excludes_business_snapshot_bytes(self):
        from pdd_monitor.collection_worker import work
        with tempfile.TemporaryDirectory() as name:
            project=Path(name).resolve();job_id='collect_'+'c'*32;directory=project/'state/local_collection'/job_id;directory.mkdir(parents=True)
            (directory.parent/'settings.json').write_text(json.dumps({'jobs':[{'id':job_id,'status':'running','shop_id':A,'kind':'shop'}]}),encoding='utf-8')
            (directory/'browser_result.json').write_text(json.dumps({'status':'needs_login','message':'synthetic login','reason':'login'}),encoding='utf-8')
            prepared={'acquired':True,'job_id':'capture_'+'d'*32,'session_directory':str(project/'sources/capture_synthetic'),'session_options':{'shopName':'SYNTHETIC_ONLY'}}
            resolved={'shop_name':'SYNTHETIC_ONLY','source_url':'https://mobile.yangkeduo.com/mall_page.html?mall_id=1','latest':{'snapshot_bytes':b'NOT_SERIALIZABLE'}}
            with patch('pdd_monitor.collection_worker.capture_control._resolve_shop',return_value=resolved),patch('pdd_monitor.collection_worker.capture_control.begin',return_value=prepared),patch('pdd_monitor.collection_worker.capture_control.abandon') as abandon,patch('pdd_monitor.collection_worker._start_shop_browser',return_value=SimpleNamespace(returncode=0)),patch('pdd_monitor.collection_worker._close_shop_browser'):
                result=work(project,job_id)
                self.assertEqual(result['status'],'needs_login');abandon.assert_called_once()
            request=json.loads((directory/'request.json').read_text(encoding='utf-8'))
            self.assertEqual(set(request['shop']),{'shop_name','source_url'})
            self.assertEqual(request['capture']['session_options']['browserTool'],'pdd_local_chrome_worker')


class FixedWorkerTests(unittest.TestCase):
    def test_timeout_missing_runtime_and_unexpected_errors_have_distinct_safe_reasons(self):
        from pdd_monitor.collection_worker import work
        cases = [(subprocess.TimeoutExpired('synthetic',1),'manual_review','browser_process_timeout'),
                 (FileNotFoundError('DO_NOT_EXPOSE_PRIVATE_PATH'),'failed','local_runtime_or_receipt_missing'),
                 (ValueError('DO_NOT_EXPOSE_PRIVATE_DATA'),'failed','local_worker_failed_requires_review')]
        for error,status,reason in cases:
            with self.subTest(reason=reason),patch('pdd_monitor.collection_worker._work',side_effect=error),patch('pdd_monitor.collection_worker.time.perf_counter',side_effect=[20,20,23,23]):
                result=work(Path('.'),'collect_'+'a'*32)
            self.assertEqual((result['status'],result['reason']),(status,reason))
            self.assertEqual((result['ai_requests'],result['elapsed_seconds']),(0,3))
            self.assertNotIn('DO_NOT_EXPOSE',json.dumps(result))

    def test_publish_renewal_failure_stops_before_images_or_business_writes(self):
        from pdd_monitor.collection_worker import work
        with tempfile.TemporaryDirectory() as name:
            project=Path(name).resolve();job_id='collect_'+'e'*32;directory=project/'state/local_collection'/job_id;directory.mkdir(parents=True)
            session=project/'sources/capture_synthetic';session.mkdir(parents=True)
            (directory.parent/'settings.json').write_text(json.dumps({'jobs':[{'id':job_id,'status':'running','shop_id':A,'kind':'shop'}]}),encoding='utf-8')
            (directory/'browser_result.json').write_text(json.dumps({'status':'complete'}),encoding='utf-8')
            (session/'session.json').write_text(json.dumps({'snapshot':{'file':'snapshot.json','sha256':'a'*64},'importReady':True}),encoding='utf-8')
            prepared={'acquired':True,'job_id':'capture_'+'f'*32,'session_directory':str(session),'session_options':{'shopName':'SYNTHETIC_ONLY'}}
            resolved={'shop_name':'SYNTHETIC_ONLY','source_url':'https://mobile.yangkeduo.com/mall_page.html?mall_id=1'}
            with patch('pdd_monitor.collection_worker.capture_control._resolve_shop',return_value=resolved),patch('pdd_monitor.collection_worker.capture_control.begin',return_value=prepared),patch('pdd_monitor.collection_worker.capture_control.abandon') as abandon,patch('pdd_monitor.collection_worker._start_shop_browser',return_value=SimpleNamespace(returncode=0)),patch('pdd_monitor.collection_worker._close_shop_browser'),patch('pdd_monitor.collection_worker.capture_control.check',return_value={'ready_for_rehearsal':True,'captured_card_count':1}),patch('pdd_monitor.collection_worker.capture_control.renew',side_effect=ValueError('SYNTHETIC lost owner')) as renew,patch('pdd_monitor.collection_worker.image_package') as images:
                result=work(project,job_id)
            renew.assert_called_once_with(project,prepared['job_id']);images.assert_not_called();abandon.assert_called_once()
            self.assertEqual(result['status'],'failed')
            self.assertEqual(result['ai_requests'],0)
            self.assertFalse((project/'data').exists())


class ImagePackageTests(unittest.TestCase):
    def test_recovered_images_use_current_verified_blobs_and_preserve_original_receipt(self):
        from pdd_monitor import store
        from pdd_monitor.collection_service import current_run_image_summary
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            png = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aBf8AAAAASUVORK5CYII=')
            digest = hashlib.sha256(png).hexdigest()
            snapshot = {'synthetic': True, 'shopName': 'SYNTHETIC_ONLY', 'sourceUrl': 'https://example.invalid/shop?mall_id=9000000001',
                'sort': '上新', 'status': 'complete', 'endBoundaryObserved': True,
                'observedFrom': '2026-10-04T01:00:00Z', 'observedTo': '2026-10-04T01:10:00Z',
                'rows': [{'viewOrder': i, 'title': f'SYNTHETIC {i}', 'cardText': f'SYNTHETIC {i}',
                          'imageUrl': f'https://img.pddpic.com/SYNTHETIC_{i}.png'} for i in (1, 2)]}
            source=root/'snapshot.json'; source.write_text(json.dumps(snapshot), encoding='utf-8')
            archive=root/'images.zip'
            with zipfile.ZipFile(archive, 'w') as output:
                output.writestr('images/a.png', png)
                output.writestr('manifest.json', json.dumps({'items': [
                    {'viewOrder': row['viewOrder'], 'originalImageUrl': row['imageUrl'], 'archivePath': 'images/a.png', 'sha256': digest}
                    for row in snapshot['rows']]}))
            imported=store.import_snapshot(root/'data', source, archive)
            with closing(sqlite3.connect(root/'data/monitor.sqlite3')) as db:
                shop=db.execute('SELECT shop_id FROM runs WHERE run_id=?', (imported['run_id'],)).fetchone()[0]
                db.execute("UPDATE image_tasks SET previous_attempt_blocked=1, previous_attempt_reason='SYNTHETIC reviewed historical Chrome error'")
                db.commit()
            job_id='collect_'+'d'*32
            recovery={'run_id': imported['run_id'], 'added_cards': 2, 'original_times_preserved': True}
            job={'id': job_id, 'shop_id': shop, 'kind': 'shop', 'status': 'complete', 'image_recovery': recovery,
                 'receipt': {'ok': True, 'status': 'finished', 'shop_id': shop, 'run_id': imported['run_id'],
                             'snapshot_status': 'complete', 'new_run': {'status': 'complete', 'cards': 2, 'image_refs': 0}}}
            service=CollectionService(root, autostart=False)
            directory=service.directory/job_id; directory.mkdir(parents=True)
            evidence=directory/'image_recovery.json'; evidence.write_text(json.dumps(recovery), encoding='utf-8')
            with patch('pdd_monitor.collection_service.current_run_image_summary', wraps=current_run_image_summary) as check:
                first=service._public_job(job); second=service._public_job(job)
                self.assertEqual(check.call_count, 1)
            self.assertEqual(first['image_summary'], {'total': 2, 'saved': 2, 'missing': 0, 'status': 'complete'})
            self.assertTrue(first['image_verification']['verified'])
            self.assertEqual(second['receipt']['new_run']['image_refs'], 0)
            self.assertEqual(job['receipt']['new_run']['image_refs'], 0)
            with closing(sqlite3.connect(root/'data/images.sqlite3')) as db:
                db.execute('UPDATE assets SET data=?', (b'x' * len(png),)); db.commit()
            self.assertFalse(service._public_job(job)['image_verification']['verified'])
            evidence.write_text('{}', encoding='utf-8')
            self.assertFalse(service._public_job(job)['image_verification']['verified'])

    def test_loaded_exact_url_images_import_and_preserve_historical_blocks(self):
        from pdd_monitor.collection_worker import image_package
        from pdd_monitor import store
        with tempfile.TemporaryDirectory() as name:
            root=Path(name);project=root/'synthetic_project';project.mkdir()
            shutil.copytree(Path(__file__).resolve().parents[1]/'pdd_monitor',project/'pdd_monitor',ignore=shutil.ignore_patterns('__pycache__'))
            (project/'scripts').mkdir()
            shutil.copy2(Path(__file__).resolve().parents[1]/'scripts/package_cached_images.py',project/'scripts/package_cached_images.py')
            shutil.copy2(Path(__file__).resolve().parents[1]/'scripts/image_cache_review.py',project/'scripts/image_cache_review.py')
            image_url='https://img.pddpic.com/SYNTHETIC_ONLY.png'
            blocked_url='https://img.pddpic.com/SYNTHETIC_BLOCKED.png'
            png=base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aBf8AAAAASUVORK5CYII=')
            def snapshot(path,hour,urls):
                value={'synthetic':True,'shopName':'SYNTHETIC_ONLY','sourceUrl':'https://example.invalid/shop?mall_id=9000000001','sort':'上新','status':'partial','endBoundaryObserved':False,'observedFrom':f'2026-10-04T{hour:02}:00:00Z','observedTo':f'2026-10-04T{hour:02}:10:00Z','rows':[{'viewOrder':i,'title':f'SYNTHETIC card {i}','cardText':f'SYNTHETIC card {i}','imageUrl':url,'goodsId':None,'goodsUrl':None,'salesRaw':None,'priceRaw':None} for i,url in enumerate(urls,1)]}
                path.write_text(json.dumps(value),encoding='utf-8');return value
            seed=root/'seed.json';snapshot(seed,1,[blocked_url])
            queue=root/'seed_queue.json';queue.write_text(json.dumps({'blockedPreviousAttempts':[{'viewOrder':1,'imageUrl':blocked_url,'previousAttemptBlocked':True,'previousAttemptReason':'SYNTHETIC historical block'}]}),encoding='utf-8')
            store.import_snapshot(project/'data',seed,image_queue_path=queue)
            before={f:hashlib.sha256((project/'data'/f).read_bytes()).hexdigest() for f in ('monitor.sqlite3','images.sqlite3')}
            missing_url='https://img.pddpic.com/SYNTHETIC_MISSING.png'
            current=root/'current.json';snapshot(current,2,[image_url,image_url,blocked_url,missing_url,None])
            assets={url:{'mime':'image/png','base64':base64.b64encode(png).decode()} for url in (image_url,blocked_url)}
            archive,image_queue,summary=image_package(project,current,assets,root/'image_output')
            self.assertEqual(summary, {'total': 5, 'saved': 2, 'missing': 3, 'status': 'partial',
                'missing_by_reason': {'blocked_previous_attempt': 1, 'pending_image_stage': 1, 'missing_source_url': 1}})
            with zipfile.ZipFile(archive) as z:
                items=json.loads(z.read('manifest.json'))['items']
                self.assertEqual([i['viewOrder'] for i in items],[1,2])
                self.assertTrue(all(i['originalImageUrl']==image_url for i in items))
            copy=root/'rehearsal';shutil.copytree(project/'data',copy)
            receipt=store.import_snapshot(copy,current,archive,image_queue)
            with closing(sqlite3.connect(copy/'monitor.sqlite3')) as db:
                self.assertEqual(db.execute('SELECT COUNT(*) FROM image_tasks WHERE run_id=? AND status=?',(receipt['run_id'],'saved')).fetchone()[0],2)
                self.assertEqual(db.execute('SELECT COUNT(*) FROM image_tasks WHERE run_id=? AND previous_attempt_blocked=1',(receipt['run_id'],)).fetchone()[0],1)
            self.assertEqual(before,{f:hashlib.sha256((project/'data'/f).read_bytes()).hexdigest() for f in before})

    def test_image_release_summary_requires_actual_imported_reference_counts(self):
        from pdd_monitor.collection_worker import verify_image_release
        summary = {'total': 5, 'saved': 2, 'missing': 3, 'status': 'partial'}
        receipt = {'ok': True, 'status': 'finished', 'snapshot_status': 'complete', 'new_run': {'status': 'complete', 'cards': 5, 'image_refs': 2}}
        self.assertEqual(verify_image_release(receipt, summary), summary)
        for bad in ({'ok': False}, {'status': 'prepared'}, {'new_run': {**receipt['new_run'], 'image_refs': 3}},
                    {'new_run': {**receipt['new_run'], 'cards': 4}}, {'snapshot_status': 'partial'}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                verify_image_release({**receipt, **bad}, summary)

    def test_browser_cache_policy_uses_reviewed_cache_rules_without_exposing_internal_evidence(self):
        from pdd_monitor.collection_worker import browser_image_policy
        with tempfile.TemporaryDirectory() as name:
            root=Path(name); (root/'data').mkdir()
            for file in ('monitor.sqlite3', 'images.sqlite3'):
                (root/'data'/file).touch()
            candidates = {'exact': [{'asset_sha256': 'a'}, {'asset_sha256': 'a'}],
                          'conflict': [{'asset_sha256': 'a'}, {'asset_sha256': 'b'}],
                          'restricted': [{'asset_sha256': 'a'}]}
            blocked = {'restricted': [{}]}
            calls = []
            def policy(verified, restrictions):
                calls.append((verified, restrictions))
                return {'skip_image_urls': ['exact', 'restricted'], 'blocked_image_urls': [],
                        'reviewed_cache_evidence': [{'internal': 'do not send to browser'}]}
            module = SimpleNamespace(load_store=lambda _: None,
                                     read_cache=lambda *_: (candidates, blocked, {}, {}), cache_reuse_policy=policy)
            with patch.dict('sys.modules', {'package_cached_images': module}):
                policy = browser_image_policy(root)
            self.assertEqual(calls, [(candidates, blocked)])
            self.assertEqual(policy, {'skip_image_urls': ['exact', 'restricted'], 'blocked_image_urls': []})


if __name__=='__main__':unittest.main()
