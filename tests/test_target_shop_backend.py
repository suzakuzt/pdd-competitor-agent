"""Final publication scope checks; two synthetic shops in temporary directories."""
import base64
from contextlib import closing
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile

from pdd_monitor import capture_control, collection_log
from pdd_monitor.collection_service import CollectionService, verify_shop_release
from pdd_monitor.sku_store import SkuBatchBackup, save_capture, read_latest
from pdd_monitor.store import import_snapshot, shop_identity_evidence

SHOP_URLS = {key: 'https://mobile.yangkeduo.com/mall_page.html?mall_id=' + value
             for key, value in (('A', '9000000001'), ('B', '9000000002'))}
SHOPS = {key: shop_identity_evidence({'sourceUrl': value})['shop_id'] for key, value in SHOP_URLS.items()}
PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aBf8AAAAASUVORK5CYII=')
IMAGE = 'https://example.invalid/synthetic-card.png'
COLLECT_ID = 'collect_' + 'c' * 32


def fingerprint_files(root):
    return {path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in root.rglob('*') if path.is_file()}


class TargetShopBackendTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='pdd_target_shop_backend_')
        self.addCleanup(temporary.cleanup)
        self.project = Path(temporary.name).resolve()
        (self.project / 'AGENTS.md').write_text('SYNTHETIC TEST PROJECT', encoding='utf-8')
        self.runs, self.observations = {}, {}
        for key in ('A', 'B'):
            row = {'viewOrder': 1, 'title': 'SYNTHETIC ' + key, 'goodsId': '123',
                   'salesRaw': '已拼5件', 'imageUrl': IMAGE}
            value = {'synthetic': True, 'shopName': 'SYNTHETIC ' + key,
                     'sourceUrl': SHOP_URLS[key], 'observedFrom': '2026-10-07T00:01:00Z',
                     'observedTo': '2026-10-07T00:02:00Z', 'sort': '上新',
                     'status': 'complete', 'endBoundaryObserved': True, 'rows': [row]}
            snapshot = self.project / (key + '.json')
            snapshot.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
            archive = self.project / (key + '.zip')
            with zipfile.ZipFile(archive, 'w') as files:
                files.writestr('card.png', PNG)
                files.writestr('manifest.json', json.dumps({'items': [{'viewOrder': 1,
                    'title': row['title'], 'originalImageUrl': IMAGE, 'archivePath': 'card.png',
                    'sha256': hashlib.sha256(PNG).hexdigest()}]}))
            self.runs[key] = import_snapshot(self.project / 'data', snapshot, archive)
            with closing(sqlite3.connect(self.project / 'data/monitor.sqlite3')) as db:
                self.observations[key] = db.execute('SELECT observation_id FROM observations WHERE run_id=?',
                    (self.runs[key]['run_id'],)).fetchone()[0]
        self.before_business = fingerprint_files(self.project / 'data')

    def capture(self, **changes):
        return {'goods_id': '123', 'goods_url': 'https://mobile.yangkeduo.com/goods.html?goods_id=123',
                'observed_at': '2026-10-07T00:04:00Z', 'identity_basis': 'recorded_public_goods_link',
                'status': 'complete', 'all_combinations_visited': True, 'option_catalog_verified': True,
                'expected_combinations': 1, 'variants': [{'specs': [{'name': '款式', 'value': 'SYNTHETIC'}],
                    'selection_verified': True, 'current_price_raw': '¥5.00', 'image_url': IMAGE}], **changes}

    def backup(self, project, workspace):
        workspace.mkdir(parents=True, exist_ok=False)
        return SkuBatchBackup(project.resolve(), workspace.resolve(), workspace / 'paired_backup')

    def publish_B_sku(self):
        with patch('pdd_monitor.sku_store.prepare_batch_backup', side_effect=self.backup):
            save_capture(self.project, {'id': COLLECT_ID, 'shop_id': SHOPS['B'],
                'observation_id': self.observations['B'], 'source_run_id': self.runs['B']['run_id']},
                self.capture(), {IMAGE: {'mime': 'image/png', 'base64': base64.b64encode(PNG).decode()}},
                self.project / 'good_sku_rehearsal')

    def release_B(self):
        # A real synthetic journal, immutable job and sealed session are read by
        # the same public status API that the service uses in production.
        with patch.object(collection_log, '_now', return_value=datetime(2026, 10, 7, 0, 0, tzinfo=timezone.utc)):
            job = capture_control.begin(self.project, shop_id=SHOPS['B'])
        session = Path(job['session_directory']); session.mkdir(parents=True)
        raw = (self.project / 'B.json').read_bytes()
        (session / 'snapshot.json').write_bytes(raw)
        digest = hashlib.sha256(raw).hexdigest()
        (session / 'session.json').write_text(json.dumps({'attemptId': job['attempt_id'],
            'phase': 'complete', 'importReady': True,
            'snapshot': {'file': 'snapshot.json', 'sha256': digest, 'status': 'complete'}}), encoding='utf-8')
        _, token = capture_control._owner(self.project, job['job_id'])
        with patch.object(collection_log, '_now', return_value=datetime(2026, 10, 7, 0, 3, tzinfo=timezone.utc)):
            collection_log.finish_attempt(self.project / 'state/collection_attempts', job['attempt_id'], token,
                status='complete', run_id=self.runs['B']['run_id'], snapshot_path=session / 'snapshot.json',
                data_dir=self.project / 'data')
        receipt = {'ok': True, 'status': 'finished', 'job_id': job['job_id'], 'shop_id': SHOPS['B'],
            'run_id': self.runs['B']['run_id'], 'snapshot_sha256': digest, 'snapshot_status': 'complete',
            'new_run': {'status': 'complete', 'cards': 1, 'image_refs': 1},
            'journal': capture_control.status(self.project, job['job_id'])}
        directory = self.project / 'state/local_collection' / COLLECT_ID
        directory.mkdir(parents=True)
        (directory / 'request.json').write_text(json.dumps({'kind': 'shop',
            'capture': {'job_id': job['job_id']}}), encoding='utf-8')
        return {'id': COLLECT_ID, 'shop_id': SHOPS['B'], 'kind': 'shop'}, {
            'status': 'complete', 'capture_job_id': job['job_id'], 'receipt': receipt}

    def test_sku_save_rejects_foreign_original_card_run_and_payload_without_any_write(self):
        self.publish_B_sku()
        before_pointer = fingerprint_files(self.project / 'sources/sku_captures')
        base = {'id': 'collect_' + 'd' * 32, 'shop_id': SHOPS['B'],
                'observation_id': self.observations['B'], 'source_run_id': self.runs['B']['run_id']}
        cases = [({**base, 'observation_id': self.observations['A']}, self.capture()),
                 ({**base, 'source_run_id': self.runs['A']['run_id']}, self.capture()),
                 (base, self.capture(shop_id=SHOPS['A'])),
                 (base, self.capture(observation_id=self.observations['A'])),
                 (base, self.capture(source_card_evidence={'shop_source_url': SHOP_URLS['A']}))]
        for index, (job, value) in enumerate(cases):
            workspace = self.project / ('bad_sku_' + str(index))
            with self.subTest(case=index), patch('pdd_monitor.sku_store.prepare_batch_backup') as backup:
                with self.assertRaises(ValueError): save_capture(self.project, job, value, {}, workspace)
                backup.assert_not_called()
            self.assertFalse(workspace.exists())
            self.assertEqual(before_pointer, fingerprint_files(self.project / 'sources/sku_captures'))
            self.assertEqual(self.before_business, fingerprint_files(self.project / 'data'))
        self.assertIsNone(read_latest(self.project, SHOPS['A'], self.observations['B']))
        self.assertEqual(read_latest(self.project, SHOPS['B'], self.observations['B'])['shop_id'], SHOPS['B'])

    def test_foreign_run_changed_sha_and_missing_legacy_chain_do_not_publish_dashboard(self):
        self.publish_B_sku()
        job, result = self.release_B()
        before = fingerprint_files(self.project)
        service = CollectionService(self.project, autostart=False)
        directory = service.directory / job['id']
        bad_run = deepcopy(result)
        foreign = self.runs['A']
        bad_run['receipt'].update(run_id=foreign['run_id'], snapshot_sha256=foreign['snapshot_sha256'])
        bad_run['receipt']['journal']['run_id'] = foreign['run_id']
        bad_run['receipt']['journal']['session']['snapshot']['sha256'] = foreign['snapshot_sha256']
        bad_sha = deepcopy(result)
        bad_sha['receipt']['journal']['session']['snapshot']['sha256'] = 'f' * 64
        bad_job = deepcopy(result); bad_job['capture_job_id'] = 'capture_' + 'f' * 32
        legacy = deepcopy(result); del legacy['receipt']['journal']
        bad_counts = deepcopy(result); bad_counts['receipt']['new_run']['image_refs'] = 0
        for index, invalid in enumerate((bad_run, bad_sha, bad_job, legacy, bad_counts)):
            (directory / 'result.json').write_text(json.dumps(invalid), encoding='utf-8')
            with self.subTest(case=index), patch('pdd_monitor.collection_service.subprocess.run',
                    return_value=SimpleNamespace(returncode=0)), patch.object(service, 'publisher') as publisher:
                actual = service._execute(job)
            publisher.assert_not_called()
            self.assertEqual((actual['status'], actual['reason']), ('manual_review', 'release_receipt_unconfirmed'))
            self.assertEqual(self.before_business, fingerprint_files(self.project / 'data'))
            self.assertEqual({key: value for key, value in before.items() if key.startswith('sources/sku_captures')},
                {key: value for key, value in fingerprint_files(self.project).items() if key.startswith('sources/sku_captures')})
        (directory / 'result.json').write_text(json.dumps(result), encoding='utf-8')
        with patch('pdd_monitor.collection_service.subprocess.run', return_value=SimpleNamespace(returncode=0)), \
                patch.object(service, 'publisher') as publisher:
            actual = service._execute(job)
        publisher.assert_called_once_with(self.project)
        self.assertEqual(actual['status'], 'complete'); self.assertTrue(actual['dashboard_built'])
        self.assertEqual(self.before_business, fingerprint_files(self.project / 'data'))

    def test_current_journal_and_committed_run_are_independent_of_receipt_claims(self):
        job, result = self.release_B()
        current = capture_control.status(self.project, result['capture_job_id'])
        current['session']['snapshot']['sha256'] = 'f' * 64
        with patch('pdd_monitor.collection_service.capture_control.status', return_value=current):
            with self.assertRaises(ValueError): verify_shop_release(self.project, job, result)
        run = deepcopy(result); run['receipt']['shop_id'] = SHOPS['A']
        with self.assertRaises(ValueError): verify_shop_release(self.project, job, run)
        with self.assertRaises((ValueError, FileNotFoundError)):
            verify_shop_release(self.project, {**job, 'id': 'collect_' + 'f' * 32}, result)
        sealed = self.project / 'sources' / result['capture_job_id'] / 'snapshot.json'
        original = sealed.read_bytes(); sealed.write_bytes(original + b' ')
        with self.assertRaises(ValueError): verify_shop_release(self.project, job, result)
        sealed.write_bytes(original)
        self.assertEqual(self.before_business, fingerprint_files(self.project / 'data'))

    def test_worker_return_cannot_replace_B_target_fields(self):
        def wrong_target(job):
            return {'status': 'complete', 'shop_id': SHOPS['A'],
                    'shop_name': 'SYNTHETIC A', 'kind': 'sku',
                    'observation_id': self.observations['A']}
        service = CollectionService(self.project, runner=wrong_target, autostart=False)
        started = service.start({'shop_id': SHOPS['B'], 'kind': 'shop'})[1]['job']
        service.tick()
        actual = service.status(SHOPS['B'])['jobs'][0]
        self.assertEqual((actual['id'], actual['shop_id'], actual['shop_name'], actual['kind']),
                         (started['id'], SHOPS['B'], 'SYNTHETIC B', 'shop'))
        self.assertEqual((actual['status'], actual['reason']), ('manual_review', 'worker_target_conflict'))
        self.assertEqual(service.status(SHOPS['A'])['jobs'], [])
        self.assertEqual(self.before_business, fingerprint_files(self.project / 'data'))


if __name__ == '__main__':
    unittest.main(verbosity=2)
