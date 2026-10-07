"""Temporary-only tracking settings, workflow and publisher acceptance."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import socket
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

sys.dont_write_bytecode = True
from pdd_monitor.competitor_registry import register_target, build_targets, set_target_tracking, list_followed_targets
from pdd_monitor.competitor_service import CompetitorService, allowed_competitor_request, decode_competitor_payload
from pdd_monitor.shop_tracking import read_tracking, save_tracking, TrackingConflict, TrackingBusy


def make_run(target, n=1, *, complete=True):
    return {'run_id': f'synthetic_{target["shop_id"]}_{n}', 'shop_id': target['shop_id'],
            'shop_name': 'SYNTHETIC SAME NAME', 'source_url': target['source_url'],
            'observed_from': f'2026-10-04T0{n}:00:00Z', 'observed_to': f'2026-10-04T0{n}:05:00Z',
            'observed_to_epoch': 1791072000 + n * 3600 + 300,
            'snapshot_metadata': {'sourceUrl': target['source_url']},
            'status': 'complete' if complete else 'partial', 'end_boundary_observed': complete, 'card_count': n}


class TrackingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='tracking_synthetic_')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.a = register_target(self.root, 'SYNTHETIC A', 'https://mobile.yangkeduo.com/mall_page.html?mall_id=101')['target']
        self.b = register_target(self.root, 'SYNTHETIC B', 'https://mobile.yangkeduo.com/mall_page.html?mall_id=202')['target']
        self.runs = [make_run(self.a), make_run(self.a, 3, complete=False), make_run(self.b, 2, complete=False)]
        self.block_net = patch.object(socket, 'create_connection', side_effect=AssertionError('Network forbidden'))
        self.block_net.start(); self.addCleanup(self.block_net.stop)

    def rows(self):
        return {r['shop_id']: r for r in build_targets(self.root, self.runs)[0]}

    def set(self, target=None, status='enabled', revision=0):
        return set_target_tracking(self.root, self.runs, (target or self.a)['target_id'], status, revision)

    def setting_path(self, target=None):
        return self.root / 'state/competitors/tracking' / ((target or self.a)['shop_id'] + '.json')

    def finish(self, service, job):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            code, result = service.status(job)
            if result['status'] != 'running' and not service.busy:
                self.assertEqual(code, 200)
                return result
            time.sleep(.005)
        self.fail('Test publisher did not finish')

    def service(self, publisher=None):
        return CompetitorService(self.root, publisher or (lambda _: {'status': 'built'}), lambda _: self.runs)

    def payload(self, status='enabled', revision=0):
        return {'target_id': self.a['target_id'], 'status': status, 'expected_revision': revision}

    def test_missing_is_unconfigured_and_read_does_not_write(self):
        self.assertTrue(all(r['tracking_status'] == 'unconfigured' for r in self.rows().values()))
        self.assertFalse((self.root / 'state/competitors/tracking').exists())
        self.assertEqual(list_followed_targets(self.root, self.runs)['targets'], [])

    def test_enable_pause_are_isolated_and_preserve_all_old_state(self):
        baseline = self.root / 'state/new_arrivals/tracking_config.json'
        baseline.parent.mkdir(parents=True); baseline.write_bytes(b'fixed synthetic baseline')
        history = self.root / 'data/monitor.sqlite'; history.parent.mkdir(); history.write_bytes(b'synthetic database sentinel')
        original = {p: p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        runs = deepcopy(self.runs)
        self.assertEqual(self.set()['tracking_revision'], 1)
        self.assertEqual(self.rows()[self.b['shop_id']]['tracking_status'], 'unconfigured')
        result = self.set(status='paused', revision=1)
        self.assertEqual(result['tracking_revision'], 2)
        self.assertFalse(result['website_collection_performed'])
        self.assertFalse(result['baseline_changed']); self.assertFalse(result['history_deleted'])
        self.assertEqual(self.rows()[self.a['shop_id']]['sop_stage'], 'tracking_paused')
        self.assertTrue(self.rows()[self.a['shop_id']]['needs_full_scan'])
        self.assertEqual(self.runs, runs)
        self.assertTrue(all(p.read_bytes() == raw for p, raw in original.items()))

    def test_same_status_current_revision_is_no_write_and_stale_is_conflict(self):
        self.set(); before = self.setting_path().read_bytes()
        self.assertEqual(self.set(revision=1)['setting_status'], 'unchanged')
        self.assertEqual(self.setting_path().read_bytes(), before)
        with self.assertRaises(TrackingConflict): self.set(revision=0)
        self.assertEqual(self.setting_path().read_bytes(), before)

    def test_pending_unknown_conflicting_target_cannot_change_settings(self):
        unknown = register_target(self.root, 'SYNTHETIC unresolved')['target']
        with self.assertRaises(ValueError): self.set(unknown)
        with self.assertRaises(ValueError): set_target_tracking(self.root, self.runs, 'target_' + 'f' * 24, 'enabled', 0)
        corrupted = deepcopy(self.runs); corrupted[0]['snapshot_metadata'] = {'sourceUrl':'https://mobile.yangkeduo.com/mall_page.html?mall_sn=101'}
        with self.assertRaises(ValueError): set_target_tracking(self.root, corrupted, self.a['target_id'], 'enabled', 0)
        self.assertFalse((self.root / 'state/competitors/tracking').exists())

    def test_input_rejects_path_bool_revision_unknown_status_extra_field(self):
        service = self.service()
        for change in ({'target_id':'../x'}, {'status':True}, {'status':'unconfigured'}, {'expected_revision':True},
                       {'expected_revision':-1}, {'expected_revision':'0'}, {'extra':'value'}):
            payload = {**self.payload(), **change}
            with self.subTest(change=change), self.assertRaises(ValueError): service.update_tracking(payload)
        self.assertFalse((self.root / 'state/competitors/tracking').exists())

    def test_corrupt_settings_fail_closed_instead_of_reset(self):
        self.set(); original = self.setting_path().read_bytes()
        for mutation in ({'revision':True}, {'shop_id':self.b['shop_id']}, {'status':'auto'}, {'updated_at':'2026-10-04'}, {'schema_version':2}):
            value = json.loads(original); value.update(mutation)
            self.setting_path().write_text(json.dumps(value), encoding='utf-8')
            with self.subTest(mutation=mutation), self.assertRaises(ValueError): build_targets(self.root, self.runs)
        self.setting_path().write_bytes(original[:-2] + b',"status":"paused"}')
        with self.assertRaises(ValueError): build_targets(self.root, self.runs)

    def test_atomic_write_failure_preserves_previous_revision(self):
        self.set(); before = self.setting_path().read_bytes()
        with patch('pdd_monitor.shop_tracking.os.replace', side_effect=OSError('synthetic failure')):
            with self.assertRaises(OSError): self.set(status='paused', revision=1)
        self.assertEqual(before, self.setting_path().read_bytes())
        self.assertFalse(list(self.setting_path().parent.glob('*.tmp')))
        self.assertFalse(list(self.setting_path().parent.glob('*.lock')))

    def test_competing_writer_cannot_overwrite_revision(self):
        self.set(); barrier = threading.Barrier(2); successes=[]; errors=[]
        def writer():
            barrier.wait()
            try: successes.append(save_tracking(self.root, self.a['shop_id'], 'paused', 1))
            except (TrackingConflict, TrackingBusy) as error: errors.append(error)
        threads = [threading.Thread(target=writer) for _ in range(2)]
        for thread in threads: thread.start()
        for thread in threads: thread.join()
        self.assertEqual(len(successes), 1); self.assertEqual(len(errors), 1)
        self.assertEqual(read_tracking(self.root, self.a['shop_id'])[0]['revision'], 2)

    def test_stale_lock_is_not_silently_stolen(self):
        self.set(); lock = self.setting_path().with_suffix('.lock'); lock.write_text('synthetic stale', encoding='ascii')
        with self.assertRaises(TrackingBusy): self.set(status='paused', revision=1)
        self.assertEqual(lock.read_text(), 'synthetic stale')

    def test_partial_latest_does_not_replace_complete_reference(self):
        rows = self.rows(); a=rows[self.a['shop_id']]; b=rows[self.b['shop_id']]
        self.assertEqual(a['reference_run_id'], self.runs[0]['run_id']); self.assertEqual(a['reference_row_count'], 1)
        self.assertEqual(a['latest_run_id'], self.runs[1]['run_id']); self.assertEqual(a['latest_row_count'], 3)
        self.assertEqual(a['sop_stage'], 'needs_rescan'); self.assertFalse(a['latest_end_boundary_observed'])
        self.assertIsNone(b['reference_run_id']); self.assertEqual(b['sop_stage'], 'needs_rescan')

    def test_no_data_and_complete_boundary_stages(self):
        rows, _ = build_targets(self.root, [])
        self.assertTrue(all(r['sop_stage'] == 'awaiting_first_complete' for r in rows))
        self.set(); rows,_=build_targets(self.root, [self.runs[0]])
        a=next(r for r in rows if r['shop_id']==self.a['shop_id'])
        self.assertEqual(a['sop_stage'], 'tracking_enabled'); self.assertEqual(a['tracking_connection_status'], 'connection_pending')
        bad=deepcopy(self.runs[0]); bad['end_boundary_observed']=False
        rows,_=build_targets(self.root,[bad]); a=next(r for r in rows if r['shop_id']==self.a['shop_id'])
        self.assertEqual(a['sop_stage'], 'needs_rescan'); self.assertFalse(a['has_complete_run'])

    def test_attempts_require_own_identity_and_fresh_evidence(self):
        attempts = [{'attempt_id':'unbound', 'status':'failed', 'reason':'login_required','finished_at':'2026-10-05T00:00:00Z'},
                    {'attempt_id':'b-failed','shop_id':self.b['shop_id'],'status':'failed','reason':'login_required','finished_at':'2026-10-05T00:00:00Z'}]
        rows,_=build_targets(self.root,self.runs,attempts); by={r['shop_id']:r for r in rows}
        self.assertEqual(by[self.a['shop_id']]['sop_stage'],'needs_rescan')
        self.assertEqual(by[self.b['shop_id']]['sop_stage'],'waiting_login')
        self.assertIsNone(by[self.a['shop_id']]['last_attempt_id'])
        attempts=[{'attempt_id':'a-fail','run_id':self.runs[0]['run_id'],'status':'failed','reason':'error','finished_at':'2026-10-04T01:06:00Z'}]
        rows,_=build_targets(self.root,self.runs,attempts)
        self.assertEqual(next(r for r in rows if r['shop_id']==self.a['shop_id'])['sop_stage'],'needs_rescan')

    def test_bound_running_failure_and_misleading_reason(self):
        for status, reason, stage in [('running',None,'collection_running'),('failed','timeout','collection_failed'),
                                      ('failed','contains login but not confirmed','collection_failed')]:
            attempts=[{'attempt_id':'a','shop_id':self.a['shop_id'],'status':status,'reason':reason,'started_at':'2026-10-05T00:00:00Z'}]
            rows,_=build_targets(self.root,self.runs,attempts)
            self.assertEqual(next(r for r in rows if r['shop_id']==self.a['shop_id'])['sop_stage'],stage)

    def test_readonly_plan_includes_only_enabled_stable_shops(self):
        self.set(); self.set(self.b, status='paused')
        before={p:p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        plan=list_followed_targets(self.root,self.runs)
        self.assertEqual(plan['target_count'],1); self.assertEqual(plan['targets'][0]['shop_id'],self.a['shop_id'])
        self.assertEqual(plan['status'],'plan_only'); self.assertFalse(plan['website_collection_performed'])
        self.assertTrue(all(p.read_bytes()==raw for p,raw in before.items()))

    def test_observed_unregistered_shop_can_keep_setting_after_registration(self):
        self.set(); Path(self.root/'state/competitors/targets'/(self.a['target_id']+'.json')).unlink()
        row=next(r for r in build_targets(self.root,self.runs)[0] if r['shop_id']==self.a['shop_id'])
        self.assertEqual(row['tracking_status'],'enabled'); self.assertEqual(row['target_id'],'observed_'+self.a['shop_id'])
        receipt=set_target_tracking(self.root,self.runs,row['target_id'],'paused',1)
        self.assertEqual(receipt['tracking_revision'],2)

    def test_setting_lineage_sha_is_exact_and_not_global_schedule(self):
        self.set(); rows,hashes=build_targets(self.root,self.runs)
        self.assertEqual(hashes[str(self.setting_path())],hashlib.sha256(self.setting_path().read_bytes()).hexdigest())
        self.assertTrue(all(r['tracking_connection_status']=='connection_pending' for r in rows))

    def test_service_receipt_waits_for_publisher_and_prevents_parallel_jobs(self):
        entered=threading.Event(); release=threading.Event()
        def publish(_): entered.set(); release.wait(2); return {'status':'built'}
        service=self.service(publish); code,body=service.update_tracking(self.payload())
        self.assertEqual(code,202); self.assertFalse(body['receipt']['dashboard_built']); self.assertTrue(entered.wait(1))
        self.assertEqual(service.start({'name':'other'})[0],409)
        self.assertEqual(service.update_tracking(self.payload('paused',1))[0],409)
        release.set(); final=self.finish(service,body['job_id'])
        self.assertEqual(final['status'],'succeeded'); self.assertTrue(final['receipt']['dashboard_built'])
        self.assertFalse(final['receipt']['website_collection_performed'])

    def test_service_build_failure_keeps_setting_and_honest_receipt(self):
        service=self.service(lambda _: {'status':'unexpected'})
        code,body=service.update_tracking(self.payload()); self.assertEqual(code,202)
        final=self.finish(service,body['job_id'])
        self.assertEqual(final['status'],'failed'); self.assertEqual(final['error_code'],'dashboard_build_failed')
        self.assertFalse(final['receipt']['dashboard_built']); self.assertEqual(read_tracking(self.root,self.a['shop_id'])[0]['status'],'enabled')
        code,body=service.update_tracking(self.payload()); self.assertEqual(code,409); self.assertEqual(body['error_code'],'revision_conflict')
        self.assertEqual(body['current_revision'],1)

    def test_service_thread_failure_does_not_leave_running_job(self):
        service=self.service()
        with patch('pdd_monitor.competitor_service.threading.Thread.start', side_effect=RuntimeError('synthetic unavailable')):
            code,body=service.update_tracking(self.payload())
        self.assertEqual(code,503); self.assertEqual(body['status'],'failed'); self.assertFalse(service.busy)
        self.assertEqual(body['receipt']['tracking_revision'],1)

    def test_same_origin_json_guard_remains_strict(self):
        raw=json.dumps(self.payload()).encode(); headers={'Host':'127.0.0.1:8878','Origin':'http://127.0.0.1:8878',
                  'Content-Type':'application/json','Content-Length':str(len(raw))}
        self.assertTrue(allowed_competitor_request(headers,8878,write=True))
        for change in ({'Origin':'https://evil.invalid'}, {'Host':'evil.invalid'}, {'Content-Type':'text/plain'},
                       {'Content-Length':'32769'}, {'Transfer-Encoding':'chunked'}, {'Sec-Fetch-Site':'cross-site'}):
            self.assertFalse(allowed_competitor_request({**headers,**change},8878,write=True))
        self.assertEqual(decode_competitor_payload(raw),self.payload())
        with self.assertRaises(ValueError): decode_competitor_payload(b'{"status":"enabled","status":"paused"}')

    def test_standard_export_keeps_only_own_setting_and_lineage(self):
        from pdd_monitor.store import import_snapshot
        from pdd_monitor.dashboard_data import build_dashboard_snapshot
        from pdd_monitor.competitor_export import export_competitor
        for target in (self.a, self.b):
            snapshot={'synthetic':True,'shopName':target['display_name'],'sourceUrl':target['source_url'],
                      'observedFrom':'2026-10-04T00:00:00Z','observedTo':'2026-10-04T00:05:00Z',
                      'status':'partial','endBoundaryObserved':False,
                      'rows':[{'viewOrder':1,'title':'SYNTHETIC SAME TITLE','salesRaw':'已拼11件','priceRaw':'¥1'}]}
            path=self.root/(target['shop_id']+'.json');path.write_text(json.dumps(snapshot),encoding='utf-8')
            import_snapshot(self.root/'data',path)
        self.set(); self.set(self.b,status='paused')
        before_db={p:p.read_bytes() for p in (self.root/'data').glob('*.sqlite3')}
        snapshot=build_dashboard_snapshot(self.root/'data',self.root/'dashboard/data.json')
        source=snapshot['queries']['competitor_targets']['source']
        # Both slash forms must be isolated, including nested metric lineage.
        b_setting=str(self.setting_path(self.b)).replace('/', '\\')
        source['files'].append(b_setting)
        source['metricDefinitions'][1]['sourceLineage'][0]['tables'].append(b_setting)
        original=json.dumps(snapshot,sort_keys=True)
        for own,other,status in ((self.a,self.b,'enabled'),(self.b,self.a,'paused')):
            result=export_competitor(snapshot,own['shop_id'],self.root/(own['shop_id']+'-export.json'))
            value=json.loads(Path(result['path']).read_text(encoding='utf-8'))
            query=value['queries']['competitor_targets']
            self.assertEqual(len(query['rows']),1)
            self.assertEqual(query['rows'][0]['shop_id'],own['shop_id'])
            self.assertEqual(query['rows'][0]['tracking_status'],status)
            self.assertEqual(query['export_filter'],{'shop_id':own['shop_id'],'scope':'competitor_tracking_state'})
            serialized=json.dumps(query)
            self.assertNotIn(other['target_id'],serialized); self.assertNotIn(other['shop_id'],serialized)
            self.assertEqual(query['source']['registry_files_sha256'][str(self.setting_path(own))],
                             hashlib.sha256(self.setting_path(own).read_bytes()).hexdigest())
            self.assertEqual(len(value['queries']['observations']['rows']),1)
        self.assertEqual(json.dumps(snapshot,sort_keys=True),original)
        self.assertTrue(all(path.read_bytes()==raw for path,raw in before_db.items()))


if __name__ == '__main__':
    unittest.main()
