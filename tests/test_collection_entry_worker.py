"""Replacement-entry worker boundary, entirely synthetic, no browser or business DB."""
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from pdd_monitor.collection_worker import work


class EntryWorkerTests(unittest.TestCase):
    def run_failure(self, status, reason):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            job_id = 'collect_' + 'a' * 32
            directory = root / 'state/local_collection' / job_id
            directory.mkdir(parents=True)
            entry = 'https://mobile.yangkeduo.com/mall_page.html?ps=FRESH_SYNTHETIC'
            canonical = 'https://mobile.yangkeduo.com/mall_page.html?mall_id=123'
            job = {'id': job_id, 'shop_id': 'shop_' + 'b' * 24, 'status': 'running', 'kind': 'shop', 'entry_url': entry}
            (directory.parent / 'settings.json').write_text(json.dumps({'jobs': [job]}), encoding='utf-8')
            (directory / 'browser_result.json').write_text(json.dumps({'status': status, 'reason': reason}), encoding='utf-8')
            capture = {'acquired': True, 'job_id': 'capture_' + 'c' * 32,
                       'session_directory': str(root / 'sources/synthetic'), 'session_options': {'sourceUrl': canonical}}
            with patch('pdd_monitor.collection_worker.capture_control._resolve_shop', return_value={'shop_name': 'SYNTHETIC_ONLY', 'source_url': canonical}), \
                 patch('pdd_monitor.collection_worker.capture_control.begin', return_value=capture), \
                 patch('pdd_monitor.collection_worker.capture_control.abandon') as abandon, \
                 patch('pdd_monitor.collection_worker._start_shop_browser', return_value=SimpleNamespace(returncode=0)), patch('pdd_monitor.collection_worker._close_shop_browser'), \
                 patch('pdd_monitor.collection_worker.capture_control.check') as gate, \
                 patch('pdd_monitor.collection_worker.image_package') as images:
                result = work(root, job_id)
            request = json.loads((directory / 'request.json').read_text(encoding='utf-8'))
            self.assertEqual(request['entry_url'], entry)
            self.assertEqual(request['shop']['source_url'], canonical)
            self.assertEqual(request['capture']['session_options']['sourceUrl'], canonical)
            self.assertEqual(result['status'], status)
            self.assertEqual(result['reason'], reason)
            abandon.assert_called_once_with(root, capture['job_id'], reason)
            gate.assert_not_called()
            images.assert_not_called()
            self.assertFalse((root / 'data').exists())

    def test_zero_products_keeps_business_data_and_identity(self):
        self.run_failure('needs_url', 'zero_products')

    def test_wrong_shop_does_not_rebind_history(self):
        self.run_failure('needs_url', 'identity_mismatch')

    def test_expired_login_does_not_publish(self):
        self.run_failure('needs_login', 'login_required')


if __name__ == '__main__':
    unittest.main()
