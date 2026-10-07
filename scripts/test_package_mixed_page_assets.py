"""Synthetic local acceptance; never requests a URL or writes a source database."""
from __future__ import annotations
import base64
from datetime import datetime, timezone
import hashlib
from importlib import import_module
import json
import os
from pathlib import Path
import shutil
import socket
import sqlite3
import struct
import tempfile
import unittest
from unittest.mock import patch
import uuid
import zipfile
import zlib

from package_mixed_page_assets import prepare_mixed_bundle
from package_cached_images import load_store, read_cache, sha_file

PROJECT = Path(os.environ.get('PDD_PACKAGE_TEST_PROJECT', Path(__file__).resolve().parents[1])).resolve()
PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jh3sAAAAASUVORK5CYII=')
def alternate_png():
    def chunk(kind, payload):
        return struct.pack('>I',len(payload))+kind+payload+struct.pack('>I',zlib.crc32(kind+payload))
    return b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',1,1,8,6,0,0,0))+chunk(b'IDAT',zlib.compress(b'\x00\xff\x00\x00\xff'))+chunk(b'IEND',b'')
OTHER = alternate_png()
def url(name): return f'https://example.invalid/SYNTHETIC_ONLY/{name}.png'

class MixedBundleAcceptance(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='synthetic_image_package_',dir=os.environ.get('PDD_PACKAGE_TEST_TMPDIR'))
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.data=self.root/'data';self.number=0
        self.store=load_store(PROJECT)
        self.addCleanup(patch.stopall)
        patch.object(socket,'create_connection',side_effect=AssertionError('No network allowed')).start()
        self.seed=self.snapshot([url('cached'),url('blocked')],hour=1)
        archive=self.root/'seed.zip'
        with zipfile.ZipFile(archive,'w') as out:
            out.writestr('image.png',PNG)
            out.writestr('manifest.json',json.dumps({'items':[{'viewOrder':1,'originalImageUrl':url('cached'),'archivePath':'image.png','sha256':hashlib.sha256(PNG).hexdigest()}]}))
        queue=self.root/'seed-queue.json'
        queue.write_text(json.dumps({'items':[], 'blockedPreviousAttempts':[{'viewOrder':2,'imageUrl':url('blocked'),'previousAttemptBlocked':True,'previousAttemptReason':'SYNTHETIC historical restriction'}]}),encoding='utf-8')
        self.store.import_snapshot(self.data,self.seed,archive,queue)
        self.db_hashes={name:sha_file(self.data/name) for name in ('monitor.sqlite3','images.sqlite3')}

    def snapshot(self, urls, *,hour=2,status='partial',end=True):
        self.number+=1
        rows=[{'viewOrder':i,'domIndex':i-1,'recordKey':f'synthetic-{i}','title':f'SYNTHETIC card {i}','cardText':f'SYNTHETIC card {i}',
               'imageUrl':u,'goodsId':None,'goodsUrl':None,'salesRaw':None,'priceRaw':None,'observedAt':f'2026-10-04T{hour:02}:00:00Z','observedAtPrecision':'batch_read'} for i,u in enumerate(urls,1)]
        data={'synthetic':True,'shopName':'SYNTHETIC_ONLY','sourceUrl':'https://example.invalid/shop?mall_id=9000000001','sort':'上新',
              'observedFrom':f'2026-10-04T{hour:02}:00:00Z','observedTo':f'2026-10-04T{hour:02}:10:00Z','status':status,'endBoundaryObserved':end,'rows':rows}
        p=self.root/f'snapshot-{self.number}.json';p.write_text(json.dumps(data,ensure_ascii=False),encoding='utf-8');return p

    def receipt(self, entries=(), failures=()):
        root=self.root/'browser-use'/'assets'/str(uuid.uuid4());root.mkdir(parents=True)
        assets=[]
        for i,(u,data) in enumerate(entries):
            p=root/f'{i}.opaque';p.write_bytes(data)
            assets.append({'kind':'image','url':u,'path':str(p),'contentType':'image/png'})
        receipt={'directoryPath':str(root),'assets':assets,'failures':list(failures),
                 'summary':{'downloadedCount':len(assets),'failedCount':len(failures),'requestedCount':len(assets)+len(failures)}}
        p=self.root/f'receipt-{root.name}.json';p.write_text(json.dumps(receipt),encoding='utf-8');return p

    def build(self,snapshot,receipts=(),output=None,review=None,variant_review=None):
        return prepare_mixed_bundle(PROJECT,snapshot,receipts,output or self.root/'output',data_dir=self.data,sealed=True,review=review,variant_review=variant_review)

    def variant_fixture(self):
        original='https://img-2.pddpic.com/mms-goods-image/SYNTHETIC_ONLY/test.png?format=webp'
        actual='https://img.pddpic.com/mms-goods-image/SYNTHETIC_ONLY/test.png?format=jpeg'
        source='https://mobile.yangkeduo.com/mall_page.html?mall_sn=SYNTHETIC_ONLY&page_id=synthetic_page_123'
        def snapshot(hour):
            path=self.snapshot([original],hour=hour);value=json.loads(path.read_text());value['sourceUrl']=source
            path.write_text(json.dumps(value),encoding='utf-8');return path
        old=snapshot(3);queue=self.root/'variant-old-queue.json'
        queue.write_text(json.dumps({'items':[],'blockedPreviousAttempts':[{'viewOrder':1,'imageUrl':original,'previousAttemptBlocked':True,
            'previousAttemptReason':'safety_rejection_of_chrome_error_protocol_after_prior_failure'}]}),encoding='utf-8')
        old_run=self.store.import_snapshot(self.data,old,image_queue_path=queue)['run_id']
        con=sqlite3.connect(self.data/'monitor.sqlite3')
        try:old_id=con.execute('SELECT observation_id FROM observations WHERE run_id=?',(old_run,)).fetchone()[0]
        finally:con.close()
        self.db_hashes={name:sha_file(self.data/name) for name in self.db_hashes}
        current=snapshot(4);receipt=self.receipt([(actual,OTHER)]);raw=json.loads(receipt.read_text())
        search='https://mobile.yangkeduo.com/mall_search_result.html?mall_id=123456&refer_page_id=synthetic_page_123&refer_page_name=mall_page'
        inventory=self.root/'variant-inventory.json';inventory.write_text(json.dumps({'pageUrl':search,'assets':[{'kind':'image','url':actual,'sources':[{'kind':'attribute','property':'src'}]}]}),encoding='utf-8')
        navigation=self.root/'variant-navigation.json';navigation.write_text(json.dumps({'tool':'cua_repl','fromPageUrl':source,'searchMallId':'123456',
            'storefrontPageId':'synthetic_page_123','sameStoreConfirmed':True,'actions':['SYNTHETIC normal store search click, fill, Enter'],
            'observedAt':'2026-10-04T04:10:00Z'}),encoding='utf-8')
        value={'schemaVersion':1,'snapshotSha256':sha_file(current),'tool':'cua_repl.pageAssets','basis':'normal_storefront_search_variant',
            'reviewedAt':datetime.now(timezone.utc).isoformat(),'navigationEvidence':{'path':str(navigation),'sha256':sha_file(navigation)},
            'items':[{'originalImageUrl':original,'image_acquisition_url':actual,'viewOrders':[1],'title':'SYNTHETIC card 1','searchTitle':'SYNTHETIC card 1',
                'searchPageUrl':search,'inventoryPath':str(inventory),'inventorySha256':sha_file(inventory),'receiptSha256':sha_file(receipt),
                'assetIndex':0,'assetSha256':sha_file(raw['assets'][0]['path']),'acquiredAt':'2026-10-04T04:10:00Z','historicalObservationIds':[old_id]}]}
        review=self.root/'variant-review.json';review.write_text(json.dumps(value),encoding='utf-8')
        return {'snapshot':current,'receipt':receipt,'review':review,'original':original,'actual':actual,'old_id':old_id,'old_run':old_run}

    def set_legacy_reason(self, reason='download_timeout_followed_by_chrome_error_page'):
        # Synthetic fixture only: no production database is opened writable.
        con=sqlite3.connect(self.data/'monitor.sqlite3')
        try:
            for task_id, source in con.execute('SELECT task_id,source_state_json FROM image_tasks WHERE previous_attempt_blocked=1').fetchall():
                value=json.loads(source);value['previousAttemptReason']=reason
                con.execute('UPDATE image_tasks SET previous_attempt_reason=?,source_state_json=? WHERE task_id=?',(reason,json.dumps(value),task_id))
            con.commit()
        finally:con.close()
        self.db_hashes={name:sha_file(self.data/name) for name in self.db_hashes}

    def review(self, snapshot, receipt):
        source=json.loads(snapshot.read_text());raw=json.loads(receipt.read_text())
        index=next(i for i,a in enumerate(raw['assets']) if a['url']==url('blocked'))
        con=sqlite3.connect(self.data/'monitor.sqlite3')
        try:ids=[r[0] for r in con.execute('SELECT observation_id FROM image_tasks WHERE source_url=? AND previous_attempt_blocked=1',(url('blocked'),))]
        finally:con.close()
        value={'schemaVersion':1,'snapshotSha256':sha_file(snapshot),'tool':'cua_repl.pageAssets',
               'basis':'normal_storefront_page_asset','pageUrl':source['sourceUrl'],
               'reviewedAt':datetime.now(timezone.utc).isoformat(),
               'items':[{'imageUrl':url('blocked'),'historicalObservationIds':ids,
                         'receiptSha256':sha_file(receipt),'assetIndex':index,
                         'assetSha256':sha_file(raw['assets'][index]['path']),'acquiredAt':source['observedTo']}]}
        path=self.root/f'review-{uuid.uuid4().hex}.json';path.write_text(json.dumps(value),encoding='utf-8');return path

    def assert_inputs_unchanged(self):
        self.assertEqual(self.db_hashes,{name:sha_file(self.data/name) for name in self.db_hashes})
        self.assertFalse(any(self.data.glob('*-wal')))

    def test_mixed_batches_cache_failure_missing_keep_cards_importable(self):
        snapshot=self.snapshot([url('cached'),url('cached'),url('blocked'),url('new-a'),url('new-b'),url('failed'),url('not-requested'),None])
        r1=self.receipt([(url('new-a'),OTHER)], [{'url':url('failed'),'error':'SYNTHETIC download timeout'}])
        r2=self.receipt([(url('new-b'),PNG)])
        source_hashes={str(p):sha_file(p) for p in (snapshot,r1,r2)}
        out=self.root/'output';result=self.build(snapshot,[r1,r2],out)
        self.assertEqual((result['card_count'],result['card_images'],result['unique_image_blobs'],result['missing_card_images']),(8,4,2,4))
        self.assertEqual(result['card_counts'],{'reused_verified_cache':2,'blocked_previous_attempt':1,'saved_authorized_page_assets':2,'failed_page_assets':1,'pending_image_stage':1,'missing_source_url':1})
        self.assertTrue(result['end_boundary_observed']);self.assertEqual(result['snapshot_status'],'partial')
        with zipfile.ZipFile(out/'images.zip') as z:
            items=json.loads(z.read('manifest.json'))['items'];self.assertEqual([x['viewOrder'] for x in items],[1,2,4,5])
            self.assertEqual(items[0]['archivePath'],items[1]['archivePath'])
        for receipt in result['original_receipts']:
            self.assertEqual((out/receipt['archive_copy']).read_bytes(),Path(receipt['path']).read_bytes())
        self.assertEqual(source_hashes,{p:sha_file(p) for p in source_hashes});self.assert_inputs_unchanged()
        # Actual importer sees all 8 original cards even with four missing images.
        imported=self.root/'import-copy';shutil.copytree(self.data,imported)
        got=self.store.import_snapshot(imported,snapshot,out/'images.zip',out/'image_queue.json')
        conn=sqlite3.connect(imported/'monitor.sqlite3')
        try:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM observations WHERE run_id=?',(got['run_id'],)).fetchone()[0],8)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM image_tasks WHERE run_id=? AND status=?',(got['run_id'],'saved')).fetchone()[0],4)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM image_tasks WHERE run_id=? AND previous_attempt_blocked=1',(got['run_id'],)).fetchone()[0],1)
        finally:conn.close()
        self.assert_inputs_unchanged()

    def test_same_url_conflicting_new_bytes_require_review_not_selected(self):
        snapshot=self.snapshot([url('conflict'),url('conflict'),url('cached')])
        result=self.build(snapshot,[self.receipt([(url('conflict'),PNG)]),self.receipt([(url('conflict'),OTHER)])])
        self.assertEqual(result['card_images'],1);self.assertEqual(result['requires_review_card_count'],2)
        d=result['decisions'][0];self.assertEqual(d['status'],'unknown_source_conflict');self.assertFalse(d['eligibleForAuthorizedAcquisition']);self.assertFalse(d['automaticRetryAllowed'])
        self.assertEqual(len(d['candidate_sha256']),2);self.assert_inputs_unchanged()

    def test_cache_vs_new_hash_conflict_not_overwritten(self):
        result=self.build(self.snapshot([url('cached')]),[self.receipt([(url('cached'),OTHER)])])
        self.assertEqual(result['card_images'],0);self.assertEqual(result['card_counts'],{'unknown_source_conflict':1});self.assert_inputs_unchanged()

    def test_identical_receipts_same_sha_dedup_bytes_not_card_references(self):
        r1=self.receipt([(url('same'),PNG)]);r2=self.receipt([(url('same'),PNG)])
        result=self.build(self.snapshot([url('same'),url('same')]),[r1,r1,r2])
        self.assertEqual((len(result['original_receipts']),result['card_images'],result['unique_image_blobs']),(2,2,1))
        self.assertEqual(len(result['decisions'][0]['page_asset_references']),2)

    def test_historically_blocked_download_cannot_be_laundered(self):
        with self.assertRaisesRegex(ValueError,'Historical blocked'):
            self.build(self.snapshot([url('blocked')]),[self.receipt([(url('blocked'),PNG)])])
        self.assertFalse((self.root/'output').exists());self.assert_inputs_unchanged()

    def test_explicit_normal_page_review_saves_new_card_and_preserves_old_block(self):
        for number,reason in enumerate(('download_timeout_followed_by_chrome_error_page',
                                       'safety_rejection_of_chrome_error_protocol_after_prior_failure')):
            with self.subTest(reason=reason):
                self.set_legacy_reason(reason)
                snapshot=self.snapshot([url('blocked'),url('blocked')]);receipt=self.receipt([(url('blocked'),PNG)])
                review=self.review(snapshot,receipt);out=self.root/f'reviewed-output-{number}'
                result=self.build(snapshot,[receipt],out,review)
                self.assertEqual((result['card_images'],result['reviewed_legacy_card_count']),(2,2))
                self.assertEqual(result['card_counts'],{'saved_authorized_page_assets':2})
                self.assertEqual(result['decisions'][0]['blocked_source_references'][0]['previous_attempt_reason'],reason)
                self.assertEqual((out/result['legacy_review']['archive_copy']).read_bytes(),review.read_bytes())
                with zipfile.ZipFile(out/'images.zip') as z:
                    items=json.loads(z.read('manifest.json'))['items']
                    self.assertEqual(items[0]['historicalReview']['reviewSha256'],sha_file(review))
                # Real importer on a disposable copy: new refs saved, old blocked row untouched.
                copy=self.root/f'import-reviewed-{number}';shutil.copytree(self.data,copy)
                imported=self.store.import_snapshot(copy,snapshot,out/'images.zip',out/'image_queue.json')
                con=sqlite3.connect(copy/'monitor.sqlite3')
                try:
                    old=con.execute('SELECT status,previous_attempt_reason FROM image_tasks WHERE observation_id=2').fetchone()
                    self.assertEqual(old,('blocked_previous_attempt',reason))
                    self.assertEqual(con.execute('SELECT COUNT(*) FROM image_tasks WHERE run_id=? AND status=? AND previous_attempt_blocked=0',(imported['run_id'],'saved')).fetchone()[0],2)
                finally:con.close()
                self.assert_inputs_unchanged()

    def test_review_rejects_other_reasons_and_does_not_authorize_old_snapshot(self):
        snapshot=self.snapshot([url('blocked')]);receipt=self.receipt([(url('blocked'),PNG)])
        with self.assertRaisesRegex(ValueError,'other historical'):
            self.build(snapshot,[receipt],review=self.review(snapshot,receipt))
        self.set_legacy_reason()
        with self.assertRaisesRegex(ValueError,'new snapshot'):
            self.build(self.seed,[receipt],review=self.review(self.seed,receipt))
        self.assertFalse((self.root/'output').exists());self.assert_inputs_unchanged()

    def test_review_rejects_wrong_snapshot_page_time_url_ids_receipt_index_or_bytes(self):
        self.set_legacy_reason()
        snapshot=self.snapshot([url('blocked')]);receipt=self.receipt([(url('blocked'),PNG)])
        mutations=[lambda r:r.update(snapshotSha256='0'*64),
                   lambda r:r.update(pageUrl='chrome-error://chromewebdata/'),
                   lambda r:r.update(pageUrl='https://other.invalid/shop?mall_id=9000000001'),
                   lambda r:r.update(pageUrl='https://example.invalid/shop?mall_id=9000000002'),
                   lambda r:r.update(reviewedAt='2020-01-01T00:00:00Z'),
                   lambda r:r.update(reviewedAt='2999-01-01T00:00:00Z'),
                   lambda r:r['items'][0].update(imageUrl=url('different')),
                   lambda r:r['items'][0].update(historicalObservationIds=[]),
                   lambda r:r['items'][0].update(receiptSha256='0'*64),
                   lambda r:r['items'][0].update(assetIndex=9),
                   lambda r:r['items'][0].update(assetSha256='0'*64),
                   lambda r:r['items'][0].update(acquiredAt='2020-01-01T00:00:00Z')]
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                path=self.review(snapshot,receipt);raw=json.loads(path.read_text());mutate(raw);path.write_text(json.dumps(raw))
                with self.assertRaises(ValueError):self.build(snapshot,[receipt],review=path)
        self.assertFalse((self.root/'output').exists());self.assert_inputs_unchanged()

    def test_review_requires_actual_matching_success_and_rejects_current_failure(self):
        self.set_legacy_reason()
        snapshot=self.snapshot([url('blocked')]);receipt=self.receipt([(url('blocked'),PNG)]);review=self.review(snapshot,receipt)
        with self.assertRaisesRegex(ValueError,'original successful receipt'):
            self.build(snapshot,[],review=review)
        for failure in ({'url':url('blocked'),'code':'policy_blocked'},
                        {'url':url('blocked'),'error':'SYNTHETIC timeout'}):
            with self.subTest(failure=failure),self.assertRaisesRegex(ValueError,'current pageAssets failure'):
                self.build(snapshot,[receipt,self.receipt(failures=[failure])],review=review)
        with self.assertRaisesRegex(ValueError,'conflicting current image bytes'):
            self.build(snapshot,[receipt,self.receipt([(url('blocked'),OTHER)])],review=review)
        self.assertFalse((self.root/'output').exists());self.assert_inputs_unchanged()

    def test_search_variant_keeps_original_card_and_actual_acquisition_separate(self):
        case=self.variant_fixture();result=self.build(case['snapshot'],[case['receipt']],variant_review=case['review'])
        self.assertEqual((result['card_images'],result['variant_card_count'],result['missing_card_images']),(1,1,0))
        self.assertEqual(result['card_counts'],{'saved_authorized_store_search_variant':1})
        out=self.root/'output'
        with zipfile.ZipFile(out/'images.zip') as z:item=json.loads(z.read('manifest.json'))['items'][0]
        self.assertEqual(item['originalImageUrl'],case['original']);self.assertEqual(item['image_acquisition_url'],case['actual'])
        self.assertEqual(item['variant_provenance']['asset_sha256'],item['sha256'])
        self.assertEqual(item['acquisitionMethod'],'authorized_store_search_variant')
        for evidence in result['variant_evidence']:
            self.assertEqual((out/evidence['archive_copy']).read_bytes(),Path(evidence['path']).read_bytes())
        copy=self.root/'variant-import';shutil.copytree(self.data,copy)
        imported=self.store.import_snapshot(copy,case['snapshot'],out/'images.zip',out/'image_queue.json')
        con=sqlite3.connect(copy/'monitor.sqlite3')
        try:
            self.assertEqual(con.execute('SELECT image_url FROM observations WHERE run_id=?',(imported['run_id'],)).fetchone()[0],case['original'])
            self.assertEqual(con.execute('SELECT status FROM image_tasks WHERE observation_id=?',(case['old_id'],)).fetchone()[0],'blocked_previous_attempt')
            state=json.loads(con.execute('SELECT source_state_json FROM image_tasks WHERE run_id=?',(imported['run_id'],)).fetchone()[0])
            self.assertEqual(state['image_acquisition_url'],case['actual'])
            self.assertEqual(con.execute('SELECT previous_attempt_blocked FROM image_tasks WHERE run_id=?',(imported['run_id'],)).fetchone()[0],0)
        finally:con.close()
        candidates,blocked,assets,_=read_cache(copy,self.store)
        self.assertNotIn(case['original'],candidates);self.assertNotIn(case['actual'],candidates)
        self.assertIn(case['original'],blocked);self.assertIn(item['sha256'],assets)
        validator=import_module('.validation',self.store.__package__).validate_store
        self.assertTrue(validator(copy)['ok'])
        con=sqlite3.connect(copy/'images.sqlite3')
        try:
            item['variant_provenance']['search_title']='SYNTHETIC tampering'
            con.execute('UPDATE source_links SET manifest_json=? WHERE run_id=?',(json.dumps(item),imported['run_id']));con.commit()
        finally:con.close()
        self.assertFalse(validator(copy)['ok'])
        self.assert_inputs_unchanged()

    def test_search_variant_rejects_unbound_title_navigation_inventory_or_card(self):
        case=self.variant_fixture();original=json.loads(case['review'].read_text())
        mutations=[lambda r:r.update(snapshotSha256='0'*64),
            lambda r:r['items'][0].update(searchTitle='Different card'),lambda r:r['items'][0].update(viewOrders=[2]),
            lambda r:r['items'][0].update(historicalObservationIds=[]),lambda r:r['items'][0].update(inventorySha256='0'*64),
            lambda r:r['items'][0].update(assetSha256='0'*64),lambda r:r['navigationEvidence'].update(sha256='0'*64)]
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                value=json.loads(json.dumps(original));mutate(value);case['review'].write_text(json.dumps(value))
                with self.assertRaises(ValueError):self.build(case['snapshot'],[case['receipt']],variant_review=case['review'])
        self.assertFalse((self.root/'output').exists());self.assert_inputs_unchanged()

    def test_search_variant_needs_current_success_and_preserves_denials(self):
        case=self.variant_fixture()
        with self.assertRaisesRegex(ValueError,'original successful receipt'):
            self.build(case['snapshot'],[],variant_review=case['review'])
        for target in (case['original'],case['actual']):
            failed=self.receipt(failures=[{'url':target,'code':'policy_blocked'}])
            with self.subTest(target=target),self.assertRaisesRegex(ValueError,'failure|denial'):
                self.build(case['snapshot'],[case['receipt'],failed],variant_review=case['review'])
        # A successful alternate image is not enough to override an actual legacy policy restriction.
        con=sqlite3.connect(self.data/'monitor.sqlite3')
        try:con.execute('UPDATE image_tasks SET previous_attempt_reason=? WHERE observation_id=?',('actual_policy_denial',case['old_id']));con.commit()
        finally:con.close()
        with self.assertRaisesRegex(ValueError,'other restrictions'):
            self.build(case['snapshot'],[case['receipt']],variant_review=case['review'])
        self.assertFalse((self.root/'output').exists())

    def test_importer_rejects_variant_path_title_sha_or_unlabelled_acquisition(self):
        case=self.variant_fixture();self.build(case['snapshot'],[case['receipt']],variant_review=case['review'])
        with zipfile.ZipFile(self.root/'output'/'images.zip') as z:
            content={name:z.read(name) for name in z.namelist()};manifest=json.loads(content['manifest.json'])
        mutations=[lambda i:i.pop('variant_provenance'),lambda i:i.update(image_acquisition_url='https://img.pddpic.com/other.png'),
                   lambda i:i['variant_provenance'].update(search_title='not this card'),lambda i:i['variant_provenance'].update(snapshot_sha256='0'*64),
                   lambda i:i['variant_provenance'].update(asset_sha256='0'*64)]
        rows=self.store._prepare_snapshot(json.loads(case['snapshot'].read_text()))[0]
        for n,mutate in enumerate(mutations):
            value=json.loads(json.dumps(manifest));mutate(value['items'][0]);archive=self.root/f'invalid-variant-{n}.zip'
            with zipfile.ZipFile(archive,'w') as z:
                for name,data in content.items():z.writestr(name,json.dumps(value) if name=='manifest.json' else data)
            with self.subTest(mutation=n),self.assertRaises(ValueError):self.store._load_archive(archive,rows)
        # Altering both asserted hashes cannot rebind the bytes to another run.
        manifest['snapshotSha256']='0'*64;manifest['items'][0]['variant_provenance']['snapshot_sha256']='0'*64
        archive=self.root/'wrong-snapshot-variant.zip'
        with zipfile.ZipFile(archive,'w') as z:
            for name,data in content.items():z.writestr(name,json.dumps(manifest) if name=='manifest.json' else data)
        with self.assertRaisesRegex(ValueError,'snapshot SHA'):
            self.store._load_archive(archive,rows,snapshot_sha=sha_file(case['snapshot']))
        self.assert_inputs_unchanged()

    def test_failure_only_receipt_retains_explicit_block_and_no_retry(self):
        result=self.build(self.snapshot([url('denied'),url('failed')]),[self.receipt(failures=[{'url':url('denied'),'code':'policy_blocked','message':'SYNTHETIC deny'}, {'url':url('failed'),'error':'Timeout'}])])
        self.assertEqual(result['card_images'],0);self.assertEqual(result['card_counts'],{'blocked_previous_attempt':1,'failed_page_assets':1})
        self.assertTrue(all(not d['eligibleForAuthorizedAcquisition'] for d in result['decisions']))

    def test_complete_requires_end_partial_end_is_allowed(self):
        with self.assertRaisesRegex(ValueError,'Complete requires'):
            self.build(self.snapshot([url('cached')],status='complete',end=False))
        self.assertEqual(self.build(self.snapshot([url('cached')],status='partial',end=True))['card_images'],1)

    def test_source_path_outside_explicit_page_assets_root_rejected(self):
        r=self.receipt([(url('new'),PNG)]);raw=json.loads(r.read_text());raw['assets'][0]['path']=str(self.seed);r.write_text(json.dumps(raw))
        with self.assertRaisesRegex(ValueError,'outside'):
            self.build(self.snapshot([url('new')]),[r])

    def test_mime_sha_count_summary_and_duplicate_key_rejected(self):
        mutations=[lambda r:r['assets'][0].update(contentType='image/jpeg'),lambda r:r['assets'][0].update(sha256='0'*64),
                   lambda r:r['assets'][0].update(byteCount=1),lambda r:r['summary'].update(failedCount=1)]
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                p=self.receipt([(url('new'),PNG)]);raw=json.loads(p.read_text());mutate(raw);p.write_text(json.dumps(raw))
                with self.assertRaises(ValueError):self.build(self.snapshot([url('new')]),[p])
        p=self.receipt();p.write_text('{"assets":[],"assets":[]}')
        with self.assertRaisesRegex(ValueError,'Repeated JSON key'):self.build(self.snapshot([url('new')]),[p])

    def test_unknown_urls_ignored_without_reading_unreferenced_path(self):
        p=self.receipt([(url('unreferenced'),PNG)]);raw=json.loads(p.read_text());Path(raw['assets'][0]['path']).unlink();p.write_text(json.dumps(raw))
        result=self.build(self.snapshot([url('cached')]),[p])
        self.assertEqual(result['card_images'],1);self.assertEqual(len(result['ignored_unreferenced_entries']),1)
        self.assertEqual(result['local_asset_files_sha256'],{})

    def test_output_cannot_replace_existing_evidence(self):
        snapshot=self.snapshot([url('cached')]);self.build(snapshot)
        sha=sha_file(self.root/'output'/'bundle_acceptance.json')
        with self.assertRaises(FileExistsError):self.build(snapshot)
        self.assertEqual(sha,sha_file(self.root/'output'/'bundle_acceptance.json'))

    def test_explicit_workspace_relative_output_is_portable(self):
        workspace=self.root/'authorized-workspace';workspace.mkdir()
        result=prepare_mixed_bundle(PROJECT,self.snapshot([url('cached')]),[],'nested/bundle',data_dir=self.data,sealed=True,workspace=workspace)
        self.assertEqual(result['explicit_workspace'],str(workspace.resolve()))
        self.assertEqual(result['output_directory'],str((workspace/'nested'/'bundle').resolve()))
        self.assertTrue((workspace/'nested'/'bundle'/'images.zip').is_file());self.assert_inputs_unchanged()

    def test_explicit_workspace_rejects_outside_equal_or_parent_traversal(self):
        workspace=self.root/'authorized-workspace';workspace.mkdir();snapshot=self.snapshot([url('cached')])
        for output in (self.root/'outside',workspace,Path('../escaped')):
            with self.subTest(output=output),self.assertRaises(ValueError):
                prepare_mixed_bundle(PROJECT,snapshot,[],output,data_dir=self.data,sealed=True,workspace=workspace)
        self.assertFalse((self.root/'outside').exists());self.assertFalse((self.root/'escaped').exists())

    def test_relative_output_without_explicit_workspace_is_rejected(self):
        with self.assertRaisesRegex(ValueError,'explicit authorized absolute'):
            self.build(self.snapshot([url('cached')]),output=Path('unexpected-relative-output'))

    def test_output_in_source_project_or_cache_directory_is_rejected(self):
        snapshot=self.snapshot([url('cached')])
        for output in (PROJECT,PROJECT/'reports'/'synthetic-forbidden-output',self.data,self.data/'synthetic-forbidden-output'):
            with self.subTest(output=output),self.assertRaisesRegex(ValueError,'outside the source project'):
                self.build(snapshot,output=output)
        self.assert_inputs_unchanged()

    def test_missing_source_database_not_created(self):
        absent=self.root/'absent-data'
        with self.assertRaisesRegex(ValueError,'Both existing'):
            prepare_mixed_bundle(PROJECT,self.snapshot([url('cached')]),[],self.root/'output',data_dir=absent,sealed=True)
        self.assertFalse(absent.exists())

    def test_corrupt_cached_blob_rejected(self):
        conn=sqlite3.connect(self.data/'images.sqlite3')
        try:conn.execute('UPDATE assets SET data=?,byte_count=?',(OTHER,len(OTHER)));conn.commit()
        finally:conn.close()
        with self.assertRaisesRegex(ValueError,'BLOB size, SHA256 or MIME'):
            self.build(self.snapshot([url('cached')]))

if __name__=='__main__':unittest.main(verbosity=2)
