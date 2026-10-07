"""SKU scope, prices and independent publication using temporary synthetic data."""
import base64
from contextlib import closing
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from pdd_monitor.collection_service import CollectionService
from pdd_monitor.collection_worker import (sku_observations, _persist_sku_batch,
    collect_shop_skus, work, _start_shop_browser, _close_shop_browser,
    latest_complete_sku_run, pending_sku_observations, with_verified_product_ref)
from pdd_monitor.sku_store import (sku_eligible, validate_capture, save_capture,
    prepare_batch_backup, SkuBatchBackup, read_latest, _write_capture, validate_card_binding)
from pdd_monitor.store import shop_identity_evidence

SHOP_URL = 'https://mobile.yangkeduo.com/mall_page.html?mall_id=1'
OTHER_SHOP_URL = 'https://mobile.yangkeduo.com/mall_page.html?mall_id=2'
A = shop_identity_evidence({'sourceUrl': SHOP_URL})['shop_id']
B = shop_identity_evidence({'sourceUrl': OTHER_SHOP_URL})['shop_id']
JOB = 'collect_' + 'c' * 32
PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aBf8AAAAASUVORK5CYII=')
SHOP = {'shop_name': 'SYNTHETIC', 'source_url': SHOP_URL}


def capture(*, status='complete'):
    return {'goods_id': '123', 'goods_url': 'https://mobile.yangkeduo.com/goods.html?goods_id=123',
        'observed_at': '2026-10-06T02:00:00Z', 'identity_basis': 'recorded_public_goods_link',
        'status': status, 'all_combinations_visited': status == 'complete', 'option_catalog_verified': True,
        'expected_combinations': 1 if status == 'complete' else 2,
        'variants': [{'specs': [{'name': '款式', 'value': 'SYNTHETIC A'}], 'selection_verified': True,
                      'current_price_raw': '券后¥14.50', 'original_price_raw': '券前¥19.50',
                      'original_price_evidence': 'explicit_before_coupon_price',
                      'original_price_label': '券前价', 'current_price_label': '券后价',
                      'available': True, 'image_url': 'https://img.pddpic.com/SYNTHETIC.png'}]}


def observation(observation_id=1, **changes):
    return {'observation_id': observation_id, 'goods_id': '123', 'run_id': 'run_new',
            'view_order': observation_id, 'title': 'SYNTHETIC A', 'image_url': 'https://img.pddpic.com/SYNTHETIC.png',
            'sales_label': '已抢', 'sales_value': 5, 'sales_unit': '件',
            'sales_precision': 'exact_display', **changes}


def modal_observation(observation_id=1, **changes):
    return observation(observation_id, **{'goods_id': None, 'goods_url': None,
        'shop_source_url': SHOP['source_url'], 'shop_name': SHOP['shop_name'],
        'current_unique_card_candidate_count': 1, **changes})


def modal_capture(row=None, *, status='complete'):
    row = row or modal_observation()
    return {**capture(status=status), 'goods_id': None, 'goods_url': None,
        'identity_basis': 'current_unique_card_modal', 'capture_source': 'shop_card_modal',
        'source_card_evidence': {key: row[key] for key in ('title', 'image_url', 'view_order', 'shop_source_url')},
        'list_modal_evidence': {**{key: row[key] for key in (
            'shop_source_url', 'shop_name', 'title', 'image_url', 'view_order', 'observation_id')},
            'matched_card_count': 1, 'opened_from_verified_plus': True, 'modal_continuity_verified': True}}


def assets():
    return {'https://img.pddpic.com/SYNTHETIC.png': {'mime': 'image/png', 'base64': base64.b64encode(PNG).decode()}}


def prepare_synthetic_backup(project, workspace):
    workspace.mkdir(parents=True)
    return SkuBatchBackup(project.resolve(), workspace.resolve(), workspace / 'paired_backup')


def prepare_synthetic_full_backup(project, workspace):
    # The asynchronous single-item guard needs a complete synthetic baseline.
    from scripts.backup_store import _inventory
    backup = prepare_synthetic_backup(project, workspace)
    return SkuBatchBackup(backup.project, backup.workspace, backup.backup, _inventory(project))


def synthetic_backup_inputs(project):
    (project / 'data/images.sqlite3').unlink()
    with closing(sqlite3.connect(project / 'data/images.sqlite3')) as db:
        db.execute('CREATE TABLE synthetic_images(value TEXT)')
        db.commit()
    (project / 'schema.sql').write_text('-- SYNTHETIC backup scope\n', encoding='utf-8')
    (project / 'sources').mkdir(exist_ok=True)


def synthetic_db(project):
    (project / 'data').mkdir(parents=True)
    with closing(sqlite3.connect(project / 'data/monitor.sqlite3')) as db:
        db.execute('CREATE TABLE shops(shop_id TEXT PRIMARY KEY,shop_name TEXT)')
        db.executemany('INSERT INTO shops VALUES (?,?)', [(A, 'SYNTHETIC'), (B, 'SYNTHETIC B')])
        db.execute('CREATE TABLE runs(run_id TEXT PRIMARY KEY,shop_id TEXT,status TEXT,end_boundary_observed INTEGER,observed_to_epoch INTEGER,observed_from_epoch INTEGER,source_url TEXT)')
        db.execute('CREATE TABLE observations(observation_id INTEGER PRIMARY KEY,run_id TEXT,view_order INTEGER,goods_id TEXT,title TEXT,image_url TEXT,sales_label TEXT,sales_value INTEGER,sales_unit TEXT,sales_precision TEXT)')
        db.executemany('INSERT INTO runs VALUES (?,?,?,?,?,?,?)', [('run_new', A, 'complete', 1, 20, 19, SHOP_URL), ('run_old', A, 'complete', 1, 10, 9, SHOP_URL), ('run_other', B, 'complete', 1, 50, 49, OTHER_SHOP_URL), ('run_partial', A, 'partial', 0, 30, 29, SHOP_URL)])
        rows = [observation(1, sales_label='已拼', sales_value=11), observation(2, sales_value=10),
                observation(3, sales_value=0), observation(4, sales_value=None),
                observation(5, sales_label='总售', sales_value=88), observation(6, sales_value=20, sales_precision='lower_bound'),
                observation(7, sales_value=1, sales_unit='人'), observation(8, run_id='run_old'),
                observation(9, run_id='run_other'), observation(10, run_id='run_partial')]
        for row in rows:
            if row['observation_id'] != 1:
                row['title'] = 'SYNTHETIC card ' + str(row['observation_id'])
        fields = ('observation_id', 'run_id', 'view_order', 'goods_id', 'title', 'image_url', 'sales_label', 'sales_value', 'sales_unit', 'sales_precision')
        db.executemany('INSERT INTO observations VALUES (?,?,?,?,?,?,?,?,?,?)', [tuple(row[key] for key in fields) for row in rows])
        db.commit()
    (project / 'data/images.sqlite3').write_bytes(b'SYNTHETIC untouched business image DB')


class SkuScopeTests(unittest.TestCase):
    def test_exact_positive_alias_scope_and_released_run(self):
        with tempfile.TemporaryDirectory() as name:
            project = Path(name).resolve(); synthetic_db(project)
            self.assertEqual([row['observation_id'] for row in sku_observations(project, A, 'run_new')], [1, 2])
            for shop, run in ((B, 'run_new'), (A, 'run_partial'), (A, 'missing')):
                with self.subTest(shop=shop, run=run), self.assertRaises(ValueError):
                    sku_observations(project, shop, run)
        self.assertFalse(sku_eligible(observation(sales_value=True)))

    def test_original_and_current_prices_require_selected_semantic_evidence(self):
        value = validate_capture(capture())['variants'][0]
        self.assertEqual((value['current_price_yuan'], value['price_yuan'], value['original_price_yuan']), ('14.50', '14.50', '19.50'))
        for changes in ({'selection_verified': False}, {'original_price_evidence': None}, {'original_price_raw': '原价¥19 券后¥14'}):
            payload = capture(); payload['variants'][0].update(changes)
            row = validate_capture(payload)['variants'][0]
            self.assertIsNone(row['original_price_yuan'])
        payload = capture(); payload['variants'][0]['current_price_raw'] = '¥10-20'
        self.assertIsNone(validate_capture(payload)['variants'][0]['current_price_yuan'])

    def test_completion_timestamp_and_public_product_scope(self):
        for changes in ({'expected_combinations': 2}, {'expected_combinations': True}, {'observed_at': '2026-10-06'},
                        {'goods_url': 'https://mobile.yangkeduo.com/mall_page.html?goods_id=123'},
                        {'goods_url': 'https://user:password@mobile.yangkeduo.com/goods.html?goods_id=123'}):
            payload = capture(); payload.update(changes)
            with self.subTest(changes=changes), self.assertRaises(ValueError): validate_capture(payload)

    def test_manual_sku_rejects_zero_and_status_reports_eligibility(self):
        class SyntheticService(CollectionService):
            def _shop(self, shop_id):
                return {'shop_name': 'SYNTHETIC', 'source_url': 'https://mobile.yangkeduo.com/mall_page.html?mall_id=1'}
        with tempfile.TemporaryDirectory() as name:
            project = Path(name).resolve(); synthetic_db(project)
            service = SyntheticService(project, autostart=False)
            self.assertEqual(service.start({'kind': 'sku', 'shop_id': A, 'observation_id': 3})[0], 409)
            self.assertEqual(service.start({'kind': 'sku', 'shop_id': A, 'observation_id': 2})[0], 202)
            self.assertTrue(service.status(A, 2)['sku_eligibility']['eligible'])
            self.assertFalse(service.status(A, 3)['sku_eligibility']['eligible'])
            with self.assertRaises(ValueError): service.status(B, 2)

    def test_identical_title_image_cards_require_recorded_product_id(self):
        payload = capture()
        row = observation(goods_id=None, current_unique_card_candidate_count=2)
        payload['source_card_evidence'] = {key: row[key] for key in ('title', 'image_url', 'view_order')}
        with self.assertRaises(ValueError): validate_card_binding(payload, row)
        validate_card_binding(payload, {**row, 'current_unique_card_candidate_count': 1})
        validate_card_binding(payload, {**row, 'goods_id': '123'})
        with self.assertRaises(ValueError): validate_card_binding(payload, {**row, 'goods_id': '999'})


class ListModalScopeTests(unittest.TestCase):
    def test_list_modal_keeps_unknown_id_and_requires_its_own_verified_evidence(self):
        value = validate_capture(modal_capture())
        self.assertIsNone(value['goods_id']); self.assertIsNone(value['goods_url'])
        self.assertEqual(value['variants'][0]['current_price_yuan'], '14.50')
        validate_card_binding(value, modal_observation())
        # An original card ID is retained on the row, but is not copied into a
        # popup that exposed no public ID, nor used instead of card evidence.
        validate_card_binding(value, modal_observation(goods_id='123'))
        for changes in ({'goods_id': '123'}, {'goods_url': capture()['goods_url']},
                {'capture_source': None}, {'identity_basis': 'current_unique_card_candidate'}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                validate_capture({**modal_capture(), **changes})
        for changes in ({'goods_id': None, 'goods_url': None}, {'capture_source': 'shop_card_modal'}):
            with self.subTest(old_detail=changes), self.assertRaises(ValueError):
                validate_capture({**capture(), **changes})

    def test_modal_evidence_rejects_missing_continuity_ambiguous_cards_and_invalid_shop(self):
        for changes in ({'matched_card_count': 2}, {'matched_card_count': True},
                {'opened_from_verified_plus': False}, {'opened_from_verified_plus': 1},
                {'modal_continuity_verified': False}, {'modal_continuity_verified': 1},
                {'observation_id': True}, {'view_order': 0}, {'shop_name': ''}):
            payload = modal_capture(); payload['list_modal_evidence'].update(changes)
            with self.subTest(changes=changes), self.assertRaises(ValueError): validate_capture(payload)
        for key in modal_capture()['list_modal_evidence']:
            payload = modal_capture(); del payload['list_modal_evidence'][key]
            with self.subTest(missing=key), self.assertRaises(ValueError): validate_capture(payload)
        for source in ('https://example.invalid/mall_page.html?mall_id=1',
                'https://mobile.yangkeduo.com/mall_search_result.html?mall_id=1',
                'https://mobile.yangkeduo.com/mall_page.html?ps=SYNTHETIC',
                'https://mobile.yangkeduo.com/mall_page.html?mall_id=1&mall_id=1',
                'https://mobile.yangkeduo.com/mall_page.html?mall_sn=',
                'https://mobile.yangkeduo.com/mall_page.html?mall_id=1#unknown'):
            payload = modal_capture(modal_observation(shop_source_url=source))
            with self.subTest(source=source), self.assertRaises(ValueError): validate_capture(payload)
        validate_capture(modal_capture(modal_observation(
            shop_source_url='https://mobile.yangkeduo.com/mall_page.html?mall_sn=SYNTHETIC')))

    def test_modal_card_binding_rejects_other_card_or_shop_even_with_an_original_goods_id(self):
        payload = modal_capture()
        for changes in ({'observation_id': 2}, {'view_order': 2}, {'title': 'OTHER'},
                {'image_url': 'https://img.pddpic.com/OTHER.png'}, {'shop_name': 'OTHER'},
                {'shop_source_url': 'https://mobile.yangkeduo.com/mall_page.html?mall_id=2'},
                {'current_unique_card_candidate_count': 2}, {'current_unique_card_candidate_count': True}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                validate_card_binding(payload, modal_observation(goods_id='123', **changes))
        for key in ('title', 'image_url', 'view_order', 'shop_source_url'):
            wrong = modal_capture(); wrong['source_card_evidence'][key] = 'OTHER'
            with self.subTest(source_key=key), self.assertRaises(ValueError): validate_capture(wrong)

    def test_list_modal_roundtrip_and_resume_do_not_create_a_public_navigation_reference(self):
        with tempfile.TemporaryDirectory() as name:
            project = Path(name).resolve(); synthetic_db(project)
            before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in (project / 'data').iterdir()}
            job = {'id': JOB, 'shop_id': A, 'observation_id': 1}
            with patch('pdd_monitor.sku_store.prepare_batch_backup', side_effect=prepare_synthetic_backup):
                receipt = save_capture(project, job, modal_capture(), assets(), project / 'rehearsal')
            self.assertEqual((receipt['status'], receipt['image_count']), ('complete', 1))
            with patch('pathlib.Path.glob', side_effect=AssertionError('Must use exact card index')):
                saved = read_latest(project, A, 1)
            self.assertIsNone(saved['goods_id']); self.assertIsNone(saved['goods_url'])
            self.assertEqual(saved['variants'][0]['image_sha256'], hashlib.sha256(PNG).hexdigest())
            self.assertIn('未取得公开商品ID', saved['row_scope_note'])
            row = modal_observation()
            self.assertEqual(with_verified_product_ref(project, A, row), row)
            self.assertEqual(pending_sku_observations(project, A, [row])[0], [])
            self.assertEqual(with_verified_product_ref(project, A, row, previous={**saved, 'status': 'partial'}), row)
            for ref in (None, {'goods_id': '123', 'goods_url': capture()['goods_url'], 'capture_id': JOB}):
                with self.subTest(ref=ref), self.assertRaises(ValueError):
                    with_verified_product_ref(project, A, {**row, 'verified_product_ref': ref})
            with self.assertRaises(ValueError):
                with_verified_product_ref(project, B, row, previous=saved)
            with self.assertRaises(ValueError):
                with_verified_product_ref(project, A, modal_observation(2), previous=saved)
            with patch('pdd_monitor.sku_store.prepare_batch_backup') as backup, self.assertRaises(ValueError):
                save_capture(project, {**job, 'id': 'collect_'+'e'*32, 'observation_id': 2},
                    modal_capture(), assets(), project / 'wrong_rehearsal')
            backup.assert_not_called()
            self.assertEqual(before, {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in (project / 'data').iterdir()})

    def test_single_and_batch_workers_supply_the_resolved_shop_scope_before_publishing(self):
        for kind in ('sku', 'sku_batch'):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as name:
                project = Path(name).resolve(); synthetic_db(project)
                synthetic_backup_inputs(project)
                with closing(sqlite3.connect(project / 'data/monitor.sqlite3')) as db:
                    db.execute("UPDATE observations SET goods_id=NULL,title='UNIQUE_'||observation_id WHERE observation_id IN (1,2)")
                    db.commit()
                before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in (project / 'data').iterdir()}
                directory = project / 'state/local_collection' / JOB; directory.mkdir(parents=True)
                job = {'id': JOB, 'shop_id': A, 'kind': kind, 'status': 'running',
                       **({'observation_id': 1} if kind == 'sku' else {'source_run_id': 'run_new'})}
                (directory.parent / 'settings.json').write_text(json.dumps({'jobs': [job]}), encoding='utf-8')
                def browser_run(command, **kwargs):
                    request = json.loads(Path(command[-1]).read_text(encoding='utf-8'))
                    rows = request.get('observations', [request.get('observation')])
                    for row in rows:
                        self.assertEqual(row['shop_source_url'], SHOP['source_url'])
                        self.assertEqual(row['shop_name'], SHOP['shop_name'])
                        self.assertEqual(row['current_unique_card_candidate_count'], 1)
                        self.assertNotIn('verified_product_ref', row)
                    output = {'status': 'complete', 'website_collection_performed': True}
                    if kind == 'sku':
                        output.update(sku=modal_capture(rows[0]), images=assets())
                    else:
                        output['sku_batch'] = {'status': 'complete', 'items': [
                            {'observation_id': row['observation_id'], 'status': 'complete',
                             'sku': modal_capture(row), 'images': assets()} for row in rows]}
                    (Path(request['directory']) / 'browser_result.json').write_text(json.dumps(output), encoding='utf-8')
                    return SimpleNamespace(returncode=0)
                persist, save = _persist_sku_batch, save_capture
                def publish(project_arg, job_arg, rows, result, batch_dir, unused_workspace):
                    return persist(project_arg, job_arg, rows, result, batch_dir, project / 'batch_rehearsal')
                def single_save(project_arg, job_arg, value, image_assets, unused_workspace, **kwargs):
                    return save(project_arg, job_arg, value, image_assets, unused_workspace, **kwargs)
                with patch('pdd_monitor.collection_worker.capture_control._resolve_shop', return_value=SHOP), \
                     patch('pdd_monitor.collection_worker.subprocess.run', side_effect=browser_run) as browser, \
                     patch('pdd_monitor.collection_worker._persist_sku_batch', side_effect=publish), \
                     patch('pdd_monitor.collection_worker._sku_workspace', return_value=project / 'single_rehearsal'), \
                     patch('pdd_monitor.sku_store.save_capture', side_effect=single_save), \
                     patch('pdd_monitor.sku_store.prepare_batch_backup', side_effect=prepare_synthetic_full_backup) as backup, \
                     patch('pdd_monitor.sku_append.prepare_append_protection', side_effect=lambda p,w,j:prepare_synthetic_full_backup(p,w)) as append_backup:
                    result = work(project, JOB)
                self.assertEqual(result['status'], 'complete', result)
                browser.assert_called_once()
                (backup if kind == 'sku_batch' else append_backup).assert_called_once()
                self.assertEqual(read_latest(project, A, 1)['identity_basis'], 'current_unique_card_modal')
                if kind == 'sku_batch': self.assertEqual(result['sku_summary']['completed'], 2)
                self.assertEqual(before, {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in (project / 'data').iterdir()})


class SkuPublicationTests(unittest.TestCase):
    def test_one_batch_backup_per_multiple_immutable_captures_and_unchanged_business_db(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name).resolve(); project = root / 'project'; synthetic_db(project)
            before = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in (project / 'data').iterdir()}
            directory = project / 'state/batch'; directory.mkdir(parents=True)
            result = {'status': 'complete', 'sku_batch': {'status': 'complete', 'items': [
                {'observation_id': 1, 'status': 'complete', 'sku': capture(), 'images': assets()},
                {'observation_id': 2, 'status': 'complete', 'sku': capture(), 'images': assets()}]}}
            with patch('pdd_monitor.sku_store.prepare_batch_backup', side_effect=prepare_synthetic_backup) as backup:
                summary = _persist_sku_batch(project, {'id': JOB, 'shop_id': A}, [observation(1), observation(2)], result, directory, root / 'rehearsal')
            self.assertEqual(backup.call_count, 1)
            self.assertEqual((summary['status'], summary['completed'], summary['variant_count']), ('complete', 2, 2))
            with patch('pathlib.Path.glob', side_effect=AssertionError('Index must avoid scanning other captures')):
                self.assertEqual(read_latest(project, A, 2)['variants'][0]['original_price_yuan'], '19.50')
            self.assertTrue(read_latest(project, A, 1)['variants'][0]['image_data_url'].startswith('data:image/png'))
            self.assertEqual(before, {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in (project / 'data').iterdir()})
            self.assertIsNone(read_latest(project, B, 1))

    def test_backup_failure_stops_batch_publication_without_retrying_each_product(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name).resolve(); directory = root / 'batch'; directory.mkdir()
            result = {'sku_batch': {'items': [{'observation_id': key, 'status': 'complete', 'sku': capture()} for key in (1, 2)]}}
            with patch('pdd_monitor.sku_store.prepare_batch_backup', side_effect=ValueError('SYNTHETIC backup failed')) as backup:
                summary = _persist_sku_batch(root, {'id': JOB, 'shop_id': A}, [observation(1), observation(2)], result, directory, root / 'rehearsal')
            backup.assert_called_once()
            self.assertEqual((summary['status'], summary['failed'], summary['remaining']), ('manual_review', 1, 1))
            self.assertFalse((root / 'sources/sku_captures').exists())

    def test_partial_results_survive_later_login_stop_and_wrong_goods_are_not_published(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name).resolve(); project = root / 'project'; synthetic_db(project)
            directory = root / 'batch'; directory.mkdir()
            result = {'status': 'needs_login', 'sku_batch': {'items': [
                {'observation_id': 1, 'status': 'partial', 'sku': capture(status='partial'), 'images': assets()},
                {'observation_id': 2, 'status': 'complete', 'sku': capture(), 'images': assets()},
                {'observation_id': 3, 'status': 'needs_login', 'reason': 'login_required'}]}}
            with patch('pdd_monitor.sku_store.prepare_batch_backup', side_effect=prepare_synthetic_backup):
                summary = _persist_sku_batch(project, {'id': JOB, 'shop_id': A},
                    [observation(1), observation(2, goods_id='999'), observation(3), observation(4)], result, directory, root / 'rehearsal')
            self.assertEqual((summary['status'], summary['completed'], summary['partial'], summary['failed'], summary['remaining']), ('needs_login', 0, 1, 2, 1))
            self.assertIsNotNone(read_latest(project, A, 1))
            self.assertIsNone(read_latest(project, A, 2))

    def test_no_id_requires_exact_source_card_evidence_and_per_item_file_is_bounded(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name).resolve(); project = root / 'project'; synthetic_db(project)
            directory = root / 'batch'; directory.mkdir()
            payload = capture(); payload['identity_basis'] = 'current_unique_card_candidate'
            payload['source_card_evidence'] = {key: observation()[key] for key in ('title', 'image_url', 'view_order')}
            (directory / 'item.json').write_text(json.dumps({'observation_id': 1, 'sku': payload, 'images': assets()}), encoding='utf-8')
            result = {'status': 'complete', 'sku_batch': {'items': [{'observation_id': 1, 'status': 'complete', 'capture_file': 'item.json'},
                {'observation_id': 2, 'status': 'complete', 'sku': capture()},
                {'observation_id': 3, 'status': 'complete', 'capture_file': '../outside.json'}]}}
            with patch('pdd_monitor.sku_store.prepare_batch_backup', side_effect=prepare_synthetic_backup):
                summary = _persist_sku_batch(project, {'id': JOB, 'shop_id': A},
                    [observation(1, goods_id=None), observation(2, goods_id=None), observation(3)], result, directory, root / 'rehearsal')
            self.assertEqual((summary['completed'], summary['failed']), (1, 2))

    def test_duplicate_or_other_card_rejected_before_any_save(self):
        for ids in ([1, 1], [1, 9], [True]):
            with self.subTest(ids=ids), tempfile.TemporaryDirectory() as name:
                root = Path(name)
                result = {'sku_batch': {'items': [{'observation_id': key, 'status': 'complete', 'sku': capture()} for key in ids]}}
                with patch('pdd_monitor.sku_store.prepare_batch_backup') as backup, self.assertRaises(ValueError):
                    _persist_sku_batch(root, {'id': JOB, 'shop_id': A}, [observation()], result, root, root / 'rehearsal')
                backup.assert_not_called()

    def test_unrelated_images_are_not_saved_and_mime_is_verified(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            extra = {**assets(), 'https://img.pddpic.com/OTHER.png': {'mime': 'image/png', 'base64': 'invalid'}}
            _write_capture(root / 'valid', capture(), extra)
            with closing(sqlite3.connect(root / 'valid/images.sqlite3')) as db:
                self.assertEqual(db.execute('SELECT COUNT(*) FROM assets').fetchone()[0], 1)
            with self.assertRaises(ValueError):
                _write_capture(root / 'invalid', capture(), {'https://img.pddpic.com/SYNTHETIC.png': {'mime': 'image/jpeg', 'base64': base64.b64encode(PNG).decode()}})


class SkuWorkerTests(unittest.TestCase):
    def test_batch_cancellation_after_shop_release_never_opens_chrome(self):
        with tempfile.TemporaryDirectory() as name:
            project = Path(name).resolve(); synthetic_db(project)
            directory = project / 'state/local_collection' / JOB; directory.mkdir(parents=True)
            (directory / 'cancel.json').write_text('{}')
            with patch('pdd_monitor.collection_worker.subprocess.run') as process:
                summary = collect_shop_skus(project, {'id': JOB, 'shop_id': A, 'kind': 'sku_batch'}, SHOP, 'https://unused.invalid', {'run_id': 'run_new'}, directory, {})
            self.assertEqual((summary['status'], summary['total'], summary['remaining']), ('cancelled', 2, 2))
            process.assert_not_called()

    def test_shop_release_success_finishes_without_automatic_sku_or_old_queue_resume(self):
        self._shop_release_result(images_missing=False, browser_stopped=False)

    def test_shop_missing_images_remain_partial_and_sku_never_starts(self):
        self._shop_release_result(images_missing=True, browser_stopped=False)

    def test_shop_browser_stopped_during_image_repair_never_reconnects_for_sku(self):
        self._shop_release_result(images_missing=True, browser_stopped=True)

    def _shop_release_result(self, *, images_missing, browser_stopped):
        with tempfile.TemporaryDirectory() as name:
            project = Path(name).resolve(); directory = project / 'state/local_collection' / JOB; directory.mkdir(parents=True)
            (directory.parent / 'settings.json').write_text(json.dumps({'jobs': [{'id': JOB, 'shop_id': A, 'status': 'running', 'kind': 'shop'}]}), encoding='utf-8')
            (directory / 'browser_result.json').write_text(json.dumps({'status': 'complete', 'entry_verified': True,
                'sku_followup_available': not browser_stopped,
                'image_repair': {'status': 'partial', 'stop_status': 'needs_login', 'reason': 'login_required'} if browser_stopped else {}}), encoding='utf-8')
            session = project / 'sources/session'; session.mkdir(parents=True)
            (session / 'session.json').write_text(json.dumps({'snapshot': {'file': 'snapshot.json', 'sha256': 'a' * 64}, 'importReady': True}), encoding='utf-8')
            summary = {'total': 2, 'saved': 1 if images_missing else 2, 'missing': 1 if images_missing else 0,
                       'status': 'partial' if images_missing else 'complete'}
            release = {'ok': True, 'status': 'finished', 'shop_id': A, 'run_id': 'run_new',
                       'snapshot_status': 'complete', 'new_run': {'status': 'complete', 'cards': 2, 'image_refs': summary['saved']}}
            prepared = {'acquired': True, 'job_id': 'capture_synthetic', 'session_directory': str(session), 'session_options': {}}
            resolved = {'shop_name': 'SYNTHETIC', 'source_url': 'https://mobile.yangkeduo.com/mall_page.html?mall_id=1'}
            legacy = {name: ('SYNTHETIC unchanged ' + name).encode() for name in
                      ('sku_queue.json', 'sku_request.json', 'sku_receipt.json', 'sku_followup_error.json')}
            for filename, data in legacy.items():
                (directory / filename).write_bytes(data)
            def inspect_preparation(*args, **kwargs):
                progress = json.loads((directory / 'progress.json').read_text(encoding='utf-8'))
                self.assertEqual(progress['stage'], 'release_preparation')
                self.assertEqual((progress['image_total'], progress['image_saved'], progress['image_missing']),
                                 (2, summary['saved'], summary['missing']))
            def inspect_commit(*args, **kwargs):
                progress = json.loads((directory / 'progress.json').read_text(encoding='utf-8'))
                self.assertEqual(progress['stage'], 'release_commit')
                self.assertEqual((progress['image_saved'], progress['image_missing']), (summary['saved'], summary['missing']))
                return release
            def inspect_images(*args, **kwargs):
                progress = json.loads((directory / 'progress.json').read_text(encoding='utf-8'))
                self.assertEqual(progress['stage'], 'image_validation')
                return Path('synthetic.zip'), Path('synthetic.json'), summary
            fake_release = SimpleNamespace(prepare=inspect_preparation, apply=inspect_commit)
            with patch.dict('sys.modules', {'release_snapshot': fake_release}), \
                 patch('pdd_monitor.collection_worker.capture_control._resolve_shop', return_value=resolved), \
                 patch('pdd_monitor.collection_worker.capture_control.begin', return_value=prepared), \
                 patch('pdd_monitor.collection_worker.capture_control.renew'), \
                 patch('pdd_monitor.collection_worker.capture_control.check', return_value={'ready_for_rehearsal': True, 'captured_card_count': 2}), \
                 patch('pdd_monitor.collection_worker.capture_control.abandon') as abandon, \
                 patch('pdd_monitor.collection_worker._start_shop_browser', return_value=SimpleNamespace(returncode=0)), patch('pdd_monitor.collection_worker._close_shop_browser'), \
                 patch('pdd_monitor.collection_worker.image_package', side_effect=inspect_images), \
                 patch('pdd_monitor.collection_worker.collect_shop_skus', side_effect=AssertionError('Shop cannot start SKU')) as sku_stage:
                result = work(project, JOB)
            self.assertEqual(result['status'], summary['status'])
            self.assertEqual(result['image_summary'], summary)
            self.assertEqual(result['receipt'], release)
            self.assertTrue({'image_validation', 'release_preparation', 'release_commit', 'browser_cleanup'}.issubset(result['stages_seconds']))
            self.assertTrue(all(value >= 0 for value in result['stages_seconds'].values()))
            sku_stage.assert_not_called()
            self.assertNotIn('sku_summary', result)
            self.assertNotIn('sku_followup', result['stages_seconds'])
            self.assertEqual(result['sku_collection_mode'], 'on_demand')
            request = json.loads((directory / 'request.json').read_text(encoding='utf-8'))
            self.assertNotIn('followup_sku', request)
            self.assertEqual(legacy, {name: (directory / name).read_bytes() for name in legacy})
            saved = json.loads((directory / 'result.json').read_text(encoding='utf-8'))
            self.assertEqual(saved['sku_collection_mode'], 'on_demand')
            if browser_stopped:
                self.assertTrue(saved['image_repair_stopped'])
                self.assertEqual(saved['image_repair']['stop_status'], 'needs_login')
            self.assertEqual(result['ai_requests'], 0)
            abandon.assert_not_called()


class SkuOnlyResumeTests(unittest.TestCase):
    def _saved(self, project, observation_id, *, images=True, status='complete', original=True):
        value = capture(status=status)
        if not original:
            value['variants'][0].update(original_price_raw=None, original_price_evidence=None)
        value.update(shop_id=A, observation_id=observation_id,
                     capture_id='collect_' + f'{observation_id:032x}')
        _write_capture(project / 'sources/sku_captures' / value['capture_id'], value, assets() if images else {})
        return value['capture_id']

    def test_verified_reference_is_exact_card_only_and_preserves_original_fields(self):
        with tempfile.TemporaryDirectory() as name:
            project = Path(name).resolve(); synthetic_db(project)
            capture_id = self._saved(project, 1, status='partial')
            row = observation(1, goods_url=None)
            enriched = with_verified_product_ref(project, A, row)
            reference = {'goods_id': '123', 'goods_url': capture()['goods_url'], 'capture_id': capture_id}
            self.assertEqual(enriched['verified_product_ref'], reference)
            self.assertNotIn('verified_product_ref', row)
            self.assertEqual((enriched['goods_id'], enriched['goods_url']), ('123', None))
            self.assertEqual(with_verified_product_ref(project, A, enriched), enriched)
            self.assertNotIn('verified_product_ref', with_verified_product_ref(project, A, observation(2)))
            for wrong in ({**reference, 'goods_id': '999'}, None):
                with self.subTest(wrong=wrong), self.assertRaises(ValueError):
                    with_verified_product_ref(project, A, {**row, 'verified_product_ref': wrong})
            with self.assertRaises(ValueError):
                with_verified_product_ref(project, A, {**observation(2), 'verified_product_ref': reference})

    def test_verified_reference_rejects_unsafe_url_wrong_id_shop_card_and_binding(self):
        with tempfile.TemporaryDirectory() as name:
            project = Path(name).resolve(); synthetic_db(project)
            self._saved(project, 1, status='partial')
            original = read_latest(project, A, 1)
            for changes in ({'shop_id': B}, {'observation_id': 2}, {'capture_id': '../outside'},
                    {'goods_url': 'https://example.invalid/goods.html?goods_id=123'},
                    {'goods_url': 'https://mobile.yangkeduo.com/goods.html?goods_id=999'}, {'goods_id': '999'}):
                with self.subTest(changes=changes), self.assertRaises(ValueError):
                    with_verified_product_ref(project, A, observation(), previous={**original, **changes})
            unknown = observation(goods_id=None)
            with self.assertRaises(ValueError):
                with_verified_product_ref(project, A, unknown, previous=original)
            bound = {**original, 'identity_basis': 'current_unique_card_candidate',
                'source_card_evidence': {key: unknown[key] for key in ('title', 'image_url', 'view_order')}}
            enriched = with_verified_product_ref(project, A, unknown, previous=bound)
            self.assertIsNone(enriched['goods_id'])
            self.assertEqual(enriched['verified_product_ref']['goods_id'], '123')
            with self.assertRaises(ValueError):
                with_verified_product_ref(project, A, {**unknown, 'current_unique_card_candidate_count': 2}, previous=bound)

    def test_single_and_batch_requests_use_same_saved_reference_without_changing_database(self):
        with tempfile.TemporaryDirectory() as name:
            project = Path(name).resolve(); synthetic_db(project)
            synthetic_backup_inputs(project)
            self._saved(project, 1, status='partial')
            before = hashlib.sha256((project / 'data/monitor.sqlite3').read_bytes()).hexdigest()
            directory = project / 'state/local_collection' / JOB; directory.mkdir(parents=True)
            (directory.parent / 'settings.json').write_text(json.dumps({'jobs': [{'id': JOB,
                'shop_id': A, 'kind': 'sku', 'status': 'running', 'observation_id': 1}]}), encoding='utf-8')
            resolved = {'shop_name': 'SYNTHETIC', 'source_url': 'https://mobile.yangkeduo.com/mall_page.html?mall_id=1'}
            def browser_run(command, **kwargs):
                request = json.loads(Path(command[-1]).read_text(encoding='utf-8'))
                rows = request['observations'] if request['kind'] == 'sku_batch' else [request['observation']]
                self.assertEqual(rows[0]['verified_product_ref']['goods_id'], '123')
                output = {'status': 'needs_login', 'reason': 'login_required'}
                (Path(request['directory']) / 'browser_result.json').write_text(json.dumps(output), encoding='utf-8')
                return SimpleNamespace(returncode=0)
            with patch('pdd_monitor.collection_worker.capture_control._resolve_shop', return_value=resolved), \
                 patch('pdd_monitor.collection_worker._sku_workspace', return_value=project / 'single_rehearsal'), \
                 patch('pdd_monitor.sku_store.prepare_batch_backup', side_effect=prepare_synthetic_full_backup), \
                 patch('pdd_monitor.collection_worker.subprocess.run', side_effect=browser_run) as browser:
                self.assertEqual(work(project, JOB)['status'], 'needs_login')
                summary = collect_shop_skus(project, {'id': JOB, 'shop_id': A, 'kind': 'sku_batch'}, resolved,
                    resolved['source_url'], {'run_id': 'run_new'}, directory, {})
                self.assertEqual(summary['status'], 'needs_login')
            self.assertEqual(browser.call_count, 2)
            pending, _ = pending_sku_observations(project, A, sku_observations(project, A, 'run_new'))
            self.assertEqual(pending[0]['verified_product_ref']['goods_id'], '123')
            self.assertEqual(before, hashlib.sha256((project / 'data/monitor.sqlite3').read_bytes()).hexdigest())

    def test_service_pins_latest_full_run_and_exposes_separate_batch_status(self):
        class SyntheticService(CollectionService):
            def _shop(self, shop_id):
                return {'shop_name': 'SYNTHETIC', 'source_url': 'https://mobile.yangkeduo.com/mall_page.html?mall_id=1'}
        with tempfile.TemporaryDirectory() as name:
            project = Path(name).resolve(); synthetic_db(project)
            with closing(sqlite3.connect(project / 'data/monitor.sqlite3')) as db:
                db.execute('INSERT INTO runs VALUES (?,?,?,?,?,?,?)', ('run_unended', A, 'complete', 0, 100, 99, SHOP_URL))
                db.commit()
            self.assertEqual(latest_complete_sku_run(project, A), 'run_new')
            service = SyntheticService(project, autostart=False)
            code, started = service.start({'shop_id': A, 'kind': 'sku_batch'})
            self.assertEqual((code, started['job']['source_run_id']), (202, 'run_new'))
            self.assertEqual(service.start({'shop_id': A, 'kind': 'sku_batch'})[1]['job']['id'], started['job']['id'])
            state = service.status(A)
            self.assertIsNone(state['latest_job'])
            self.assertEqual(state['latest_sku_batch_job']['id'], started['job']['id'])
            self.assertEqual(service.status(A, 2)['sku_pipeline']['id'], started['job']['id'])
            for extra in ({'observation_id': 1}, {'source_run_id': 'run_other'}, {'entry_url': 'https://mobile.yangkeduo.com/mall_page.html?ps=SYNTHETIC'}):
                with self.subTest(extra=extra), self.assertRaises(ValueError):
                    service.start({'shop_id': A, 'kind': 'sku_batch', **extra})
            with self.assertRaises(ValueError):
                latest_complete_sku_run(project, 'shop_' + 'f' * 24)

    def test_skip_requires_complete_selected_images_current_price_not_fabricated_original(self):
        with tempfile.TemporaryDirectory() as name:
            project = Path(name).resolve(); synthetic_db(project)
            self._saved(project, 1, original=False)
            self._saved(project, 2, images=False)
            self._saved(project, 8, status='partial')
            pending, skipped = pending_sku_observations(project, A, sku_observations(project, A, 'run_new'))
            self.assertEqual([row['observation_id'] for row in pending], [2])
            self.assertEqual([row['observation_id'] for row in skipped], [1])
            pending, skipped = pending_sku_observations(project, A, [observation(8)])
            self.assertEqual([row['observation_id'] for row in pending], [8])
            self.assertEqual(skipped, [])
            latest = read_latest(project, A, 1)
            latest['variants'][0]['selection_verified'] = False
            with patch('pdd_monitor.sku_store.read_latest', return_value=latest):
                self.assertEqual(pending_sku_observations(project, A, [observation(1)])[1], [])
            latest['variants'][0]['selection_verified'] = True
            latest['variants'][0]['current_price_yuan'] = None
            with patch('pdd_monitor.sku_store.read_latest', return_value=latest):
                self.assertEqual(pending_sku_observations(project, A, [observation(1)])[1], [])
            with self.assertRaises(ValueError):
                pending_sku_observations(project, A, [observation(1, goods_id='999')])

    def test_worker_runs_one_batch_skips_saved_then_resume_is_no_browser_no_business_write(self):
        with tempfile.TemporaryDirectory() as name:
            project = Path(name).resolve(); synthetic_db(project)
            self._saved(project, 1)
            before = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in (project / 'data').iterdir()}
            directory = project / 'state/local_collection' / JOB; directory.mkdir(parents=True)
            state = {'jobs': [{'id': JOB, 'shop_id': A, 'kind': 'sku_batch', 'source_run_id': 'run_new', 'status': 'running'}]}
            (directory.parent / 'settings.json').write_text(json.dumps(state), encoding='utf-8')
            resolved = {'shop_name': 'SYNTHETIC', 'source_url': 'https://mobile.yangkeduo.com/mall_page.html?mall_id=1'}
            def browser_run(command, **kwargs):
                request = json.loads(Path(command[-1]).read_text(encoding='utf-8'))
                self.assertEqual(request['kind'], 'sku_batch')
                self.assertTrue(request['sku_only'])
                self.assertFalse(request['reuse_shop'])
                self.assertEqual([row['observation_id'] for row in request['observations']], [2])
                self.assertEqual(request['skipped_observation_ids'], [1])
                output = {'status': 'complete', 'website_collection_performed': True,
                    'sku_batch': {'status': 'complete', 'items': [
                        {'observation_id': 2, 'status': 'complete', 'sku': capture(), 'images': assets()}]}}
                (Path(request['directory']) / 'browser_result.json').write_text(json.dumps(output), encoding='utf-8')
                return SimpleNamespace(returncode=0)
            persist = _persist_sku_batch
            def publish(project_arg, job, rows, result, batch_dir, unused_workspace):
                return persist(project_arg, job, rows, result, batch_dir, project / 'SYNTHETIC_rehearsal')
            with patch('pdd_monitor.collection_worker.capture_control._resolve_shop', return_value=resolved), \
                 patch('pdd_monitor.collection_worker.capture_control.begin', side_effect=AssertionError('No full shop capture')), \
                 patch('pdd_monitor.collection_worker.subprocess.run', side_effect=browser_run) as process, \
                 patch('pdd_monitor.collection_worker._persist_sku_batch', side_effect=publish), \
                 patch('pdd_monitor.sku_store.prepare_batch_backup', side_effect=prepare_synthetic_backup) as backup:
                result = work(project, JOB)
            self.assertEqual((result['status'], result['ai_requests']), ('complete', 0))
            self.assertFalse(result['shop_collection_performed'])
            self.assertEqual((result['sku_summary']['scope_total'], result['sku_summary']['already_complete'],
                              result['sku_summary']['total'], result['sku_summary']['completed']), (2, 1, 1, 1))
            process.assert_called_once(); backup.assert_called_once()
            second = 'collect_' + 'd' * 32
            state['jobs'] = [{**state['jobs'][0], 'id': second}]
            (directory.parent / 'settings.json').write_text(json.dumps(state), encoding='utf-8')
            with patch('pdd_monitor.collection_worker.capture_control._resolve_shop', return_value=resolved), \
                 patch('pdd_monitor.collection_worker.subprocess.run', side_effect=AssertionError('All complete, no browser')):
                result = work(project, second)
            self.assertEqual((result['status'], result['sku_summary']['total'], result['sku_summary']['already_complete']), ('complete', 0, 2))
            self.assertFalse(result['website_collection_performed'])
            self.assertEqual(before, {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in (project / 'data').iterdir()})

    def test_changed_pinned_run_and_prestart_cancel_never_open_browser(self):
        for cancel in (False, True):
            with self.subTest(cancel=cancel), tempfile.TemporaryDirectory() as name:
                project = Path(name).resolve(); synthetic_db(project)
                directory = project / 'state/local_collection' / JOB; directory.mkdir(parents=True)
                (directory.parent / 'settings.json').write_text(json.dumps({'jobs': [{'id': JOB,
                    'shop_id': A, 'kind': 'sku_batch', 'status': 'running',
                    'source_run_id': 'run_new' if cancel else 'run_old'}]}), encoding='utf-8')
                if cancel: (directory / 'cancel.json').write_text('{}')
                with patch('pdd_monitor.collection_worker.capture_control._resolve_shop', return_value={
                        'shop_name': 'SYNTHETIC', 'source_url': 'https://mobile.yangkeduo.com/mall_page.html?mall_id=1'}), \
                     patch('pdd_monitor.collection_worker.subprocess.run', side_effect=AssertionError('No browser')):
                    result = work(project, JOB)
                self.assertEqual(result['status'], 'cancelled' if cancel else 'manual_review')
                self.assertFalse(result['website_collection_performed'])


class SharedBrowserHandshakeTests(unittest.TestCase):
    CHILD = '''
import json,sys,time
from pathlib import Path
parent=Path(sys.argv[1])
def write(path,value):
 temp=path.with_suffix('.tmp');temp.write_text(json.dumps(value));temp.replace(path)
write(parent/'browser_result.json',{'status':'complete','synthetic':True})
deadline=time.monotonic()+10
while time.monotonic()<deadline:
 if (parent/'sku_skip.json').exists():sys.exit(0)
 if (parent/'sku_request.json').exists():
  request=json.loads((parent/'sku_request.json').read_text(encoding='utf-8'))
  write(Path(request['directory'])/'browser_result.json',{'status':'needs_login','sku_batch':{'status':'needs_login','items':[{'observation_id':request['observations'][0]['observation_id'],'status':'needs_login','reason':'synthetic_login'}]}})
  sys.exit(0)
 time.sleep(.01)
sys.exit(2)
'''

    def test_legacy_shop_browser_cannot_receive_a_followup_sku_request(self):
        with tempfile.TemporaryDirectory() as name:
            project = Path(name).resolve(); synthetic_db(project)
            directory = project / 'state/local_collection' / JOB; directory.mkdir(parents=True)
            actual_popen = subprocess.Popen
            def launch(*args, **kwargs):
                return actual_popen([sys.executable, '-B', '-c', self.CHILD, str(directory)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            with patch('pdd_monitor.collection_worker.subprocess.Popen', side_effect=launch) as opener:
                process = _start_shop_browser(project, directory, {}, timeout=4)
            self.assertIsNone(process.poll())
            before = (directory / 'browser_result.json').read_bytes()
            try:
                with patch('pdd_monitor.collection_worker.subprocess.run') as browser, self.assertRaisesRegex(ValueError, 'explicit task'):
                    collect_shop_skus(project, {'id': JOB, 'shop_id': A, 'kind': 'shop'},
                        {'shop_name': 'SYNTHETIC', 'source_url': 'https://mobile.yangkeduo.com/mall_page.html?mall_id=1'},
                        'https://mobile.yangkeduo.com/mall_page.html?ps=SYNTHETIC', {'run_id': 'run_new'}, directory, {}, browser_process=process)
                browser.assert_not_called()
                self.assertFalse((directory / 'sku_request.json').exists())
                self.assertFalse((directory / 'sku_queue.json').exists())
                self.assertFalse((directory / 'sku_batch').exists())
                self.assertEqual(before, (directory / 'browser_result.json').read_bytes())
                opener.assert_called_once()
            finally:
                _close_shop_browser(process, directory)

    def test_release_failure_skip_releases_helper_without_product_request(self):
        with tempfile.TemporaryDirectory() as name:
            project = Path(name).resolve(); directory = project / 'job'; directory.mkdir()
            actual_popen = subprocess.Popen
            with patch('pdd_monitor.collection_worker.subprocess.Popen', side_effect=lambda *a, **kw:
                       actual_popen([sys.executable, '-B', '-c', self.CHILD, str(directory)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)):
                process = _start_shop_browser(project, directory, {}, timeout=4)
            _close_shop_browser(process, directory)
            self.assertEqual(process.returncode, 0)
            self.assertTrue((directory / 'sku_skip.json').is_file())
            self.assertFalse((directory / 'sku_request.json').exists())

    def test_existing_receipt_cannot_be_adopted_by_a_new_process(self):
        with tempfile.TemporaryDirectory() as name:
            directory = Path(name)
            (directory / 'browser_result.json').write_text('{"status":"complete"}')
            with patch('pdd_monitor.collection_worker.subprocess.Popen') as process, self.assertRaises(ValueError):
                _start_shop_browser(directory, directory, {})
            process.assert_not_called()

    def test_legacy_followup_error_is_preserved_but_cannot_resume_the_batch(self):
        with tempfile.TemporaryDirectory() as name:
            project = Path(name).resolve(); synthetic_db(project)
            directory = project / 'state/local_collection' / JOB; directory.mkdir(parents=True)
            (directory / 'sku_followup_error.json').write_text(json.dumps({'status': 'manual_review', 'reason': 'synthetic_followup_failure'}), encoding='utf-8')
            before = (directory / 'sku_followup_error.json').read_bytes()
            process = SimpleNamespace(poll=lambda: None, wait=lambda **kwargs: 0, returncode=None)
            with patch('pdd_monitor.collection_worker.subprocess.run') as browser, self.assertRaisesRegex(ValueError, 'explicit task'):
                collect_shop_skus(project, {'id': JOB, 'shop_id': A, 'kind': 'sku_batch'},
                    {'shop_name': 'SYNTHETIC', 'source_url': 'https://mobile.yangkeduo.com/mall_page.html?mall_id=1'},
                    'https://mobile.yangkeduo.com/mall_page.html?ps=SYNTHETIC', {'run_id': 'run_new'}, directory, {}, browser_process=process)
            browser.assert_not_called()
            self.assertEqual(before, (directory / 'sku_followup_error.json').read_bytes())
            self.assertFalse((directory / 'sku_request.json').exists())


if __name__ == '__main__':
    unittest.main()
