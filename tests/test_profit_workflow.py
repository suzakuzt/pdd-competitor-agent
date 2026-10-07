"""Temporary two-shop trial integration. No fabricated rows in production."""
import hashlib
import io
import json
from contextlib import redirect_stdout
from pathlib import Path
import tempfile
import unittest
import uuid
from unittest.mock import patch

from pdd_monitor.cli import main as cli_main
from pdd_monitor.competitor_export import export_competitor
from pdd_monitor.dashboard_data import build_dashboard_snapshot
from pdd_monitor.profit_lab import save_trial
from pdd_monitor.profit_service import ProfitService, validate_trial_reference
from pdd_monitor.store import import_snapshot


class ProfitWorkflowAcceptance(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='pdd_profit_workflow_synthetic_')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.data = self.root / 'data'
        for mall, hour in ((101, 1), (202, 2)):
            source = {
                'synthetic': True, 'shopName': 'SYNTHETIC SAME SHOP NAME',
                'sourceUrl': f'https://mobile.yangkeduo.com/mall_page.html?mall_id={mall}',
                'observedFrom': f'2026-10-04T{hour:02}:00:00Z',
                'observedTo': f'2026-10-04T{hour:02}:05:00Z',
                'status': 'complete', 'endBoundaryObserved': True,
                'rows': [{'viewOrder': 1, 'title': 'SYNTHETIC SAME PRODUCT 立牌',
                    'goodsId': '999', 'goodsUrl': 'https://mobile.yangkeduo.com/goods.html?goods_id=999',
                    'salesRaw': '已拼11件', 'priceRaw': '券后¥4.5',
                    'imageUrl': 'https://example.invalid/shared.png',
                    'observedAt': f'2026-10-04T{hour:02}:02:00Z', 'observedAtPrecision': 'batch_read'}]}
            path = self.root / f'{mall}.json'
            path.write_text(json.dumps(source), encoding='utf-8')
            import_snapshot(self.data, path)
        self.initial = self.snapshot()
        runs = {row['run_id']: row for row in self.initial['queries']['runs']['rows']}
        self.cards = {runs[row['run_id']]['shop_id']: row for row in self.initial['queries']['observations']['rows']}
        self.assertEqual(len(self.cards), 2)
        self.shops = sorted(self.cards)

    def snapshot(self):
        return build_dashboard_snapshot(self.data, self.root / 'dashboard/data.json')

    def hashes(self):
        result = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in self.data.glob('*.sqlite3')}
        self.assertEqual(set(result), {'monitor.sqlite3', 'images.sqlite3'})
        return result

    def trial(self, shop):
        return {'trial_id': str(uuid.uuid4()), 'name': 'SYNTHETIC own trial',
            'shop_id': shop, 'observation_id': self.cards[shop]['observation_id'],
            'status': 'draft', 'channel': 'SYNTHETIC own channel',
            'own_product_ref': 'SYNTHETIC own SKU', 'plan': {}, 'actual': {}, 'checks': {},
            'notes': 'SYNTHETIC private trial for ' + shop}

    def save_both(self):
        trials = {shop: self.trial(shop) for shop in self.shops}
        for trial in trials.values():
            validate_trial_reference(self.root, trial)
            first = save_trial(self.root, trial, 0)
            trial['notes'] += ' / second revision'
            second = save_trial(self.root, trial, 1)
            self.assertEqual((first['revision'], second['revision']), (1, 2))
        return trials

    def test_real_reference_validator_accepts_int_rejects_other_shop_and_strings(self):
        before = self.hashes()
        for shop in self.shops:
            trial = self.trial(shop)
            self.assertIs(type(trial['observation_id']), int)
            validate_trial_reference(self.root, trial)
            other = next(value for value in self.shops if value != shop)
            with self.assertRaises(ValueError):
                validate_trial_reference(self.root, {**trial, 'shop_id': other})
            for invalid in (str(trial['observation_id']), True, 0, 999999):
                with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                    validate_trial_reference(self.root, {**trial, 'observation_id': invalid})
        self.assertEqual(before, self.hashes())

    def test_preview_uses_real_validator_evaluator_without_writing_any_trial_or_db(self):
        before = self.hashes()
        publish_calls = []
        service = ProfitService(self.root, publisher=lambda root: publish_calls.append(root))
        response = service.preview({'trial': self.trial(self.shops[0])})
        self.assertEqual(response['status'], 'ok')
        self.assertFalse(response['saved'])
        self.assertIsNone(response['analysis']['actual_result']['actual_profit'])
        self.assertIsNone(response['analysis']['plan_result']['contribution_per_order'])
        self.assertEqual(response['analysis']['recommendation']['code'], 'missing_info')
        self.assertFalse((self.root / 'state/profit_lab').exists())
        self.assertEqual(publish_calls, [])
        self.assertEqual(before, self.hashes())

    def test_save_then_dashboard_preserves_each_shop_trial_and_all_revision_sources(self):
        before = self.hashes()
        trials = self.save_both()
        queries = self.snapshot()['queries']
        rows = queries['profit_trials']['rows']
        self.assertEqual(len(rows), 2)
        for shop, trial in trials.items():
            own = [row for row in rows if row['shop_id'] == shop]
            self.assertEqual(len(own), 1)
            self.assertEqual((own[0]['trial_id'], own[0]['observation_id'], own[0]['revision']),
                (trial['trial_id'], self.cards[shop]['observation_id'], 2))
            self.assertIsNone(own[0]['analysis']['actual_result']['actual_profit'])
            summary = next(row for row in queries['profit_trial_summary']['rows'] if row['shop_id'] == shop)
            self.assertEqual(summary['trial_count'], 1)
            self.assertFalse(summary['financial_totals_aggregated'])
        for key in ('profit_trials', 'profit_trial_summary'):
            files = queries[key]['source']['files']
            self.assertEqual(len(files), 4)
            for path, digest in files.items():
                self.assertEqual(hashlib.sha256(Path(path).read_bytes()).hexdigest(), digest)
        self.assertEqual(before, self.hashes())

    def test_shop_export_excludes_other_trial_rows_and_all_other_revision_sources(self):
        before = self.hashes()
        trials = self.save_both()
        snapshot = self.snapshot()
        original = json.dumps(snapshot, sort_keys=True)
        for shop in self.shops:
            other = next(value for value in self.shops if value != shop)
            own_id, other_id = trials[shop]['trial_id'], trials[other]['trial_id']
            result = export_competitor(snapshot, shop, self.root / f'{shop}-profit.json')
            package = json.loads(Path(result['path']).read_text(encoding='utf-8'))
            serialized = json.dumps(package, ensure_ascii=False, sort_keys=True)
            self.assertFalse(other_id in serialized, 'Export leaked another shop trial ID in rows or source files')
            self.assertFalse(trials[other]['notes'] in serialized, 'Export leaked another shop private trial notes')
            for key in ('profit_trials', 'profit_trial_summary'):
                entry = package['queries'][key]
                self.assertEqual(entry['rows'], [row for row in snapshot['queries'][key]['rows'] if row['shop_id'] == shop])
                self.assertEqual(entry['export_filter'], {'shop_id': shop, 'scope': 'user_trial_queues'})
                self.assertNotIn('run_ids', entry['export_filter'])
                self.assertNotIn('observationWindows', entry['source'])
                files = entry['source']['files']
                expected = {path: digest for path, digest in snapshot['queries'][key]['source']['files'].items()
                    if Path(path).parent.name == own_id}
                self.assertEqual(files, expected)
                self.assertEqual({Path(path).name for path in files}, {'000001.json', '000002.json'})
                self.assertTrue(all(Path(path).parent.name == own_id for path in files))
            self.assertEqual(len(package['queries']['profit_trials']['rows']), 1)
            self.assertEqual(package['queries']['profit_trials']['rows'][0]['trial_id'], own_id)
        self.assertEqual(json.dumps(snapshot, sort_keys=True), original)
        self.assertEqual(before, self.hashes())

    def test_cli_saved_revision_survives_build_failure_with_failure_receipt_and_exit_two(self):
        before = self.hashes()
        for failure in ('exception', 'failed_result'):
            with self.subTest(failure=failure):
                trial = self.trial(self.shops[0])
                path = self.root / f'own-trial-{failure}.json'
                path.write_text(json.dumps(trial), encoding='utf-8')
                behavior = {'side_effect': RuntimeError('synthetic build failure')} if failure == 'exception' else {'return_value': {'status': 'failed'}}
                captured = io.StringIO()
                with patch('pdd_monitor.dashboard_runtime.build_dashboard', **behavior), redirect_stdout(captured):
                    exit_code = cli_main(['--data-dir', str(self.data), 'profit-save', str(path), '--expected-revision', '0'])
                receipt = json.loads(captured.getvalue())
                self.assertEqual(exit_code, 2)
                self.assertEqual(receipt['status'], 'saved_but_build_failed')
                self.assertFalse(receipt['dashboard_built'])
                self.assertEqual((receipt['trial_id'], receipt['revision']), (trial['trial_id'], 1))
                stored_path = Path(receipt['path'])
                self.assertTrue(stored_path.is_file())
                stored = json.loads(stored_path.read_text(encoding='utf-8'))
                self.assertEqual(stored['record_sha256'], receipt['record_sha256'])
                self.assertEqual(stored['trial']['trial_id'], trial['trial_id'])
                self.assertEqual(list(stored_path.parent.glob('*.json')), [stored_path])
        self.assertEqual(before, self.hashes())


if __name__ == '__main__':
    unittest.main()
