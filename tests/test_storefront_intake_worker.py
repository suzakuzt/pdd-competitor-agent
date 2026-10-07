"""New-store probe handoff; synthetic files and a mocked Node process only."""
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from pdd_monitor import collection_worker as worker


class StorefrontIntakeWorkerTests(unittest.TestCase):
    def run_probe(self, flag=None, *, returncode=0, status='complete', reason=None):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            job_id = 'collect_' + 'd' * 32
            directory = project / 'state/local_collection' / job_id
            directory.mkdir(parents=True)
            job = {'id': job_id, 'status': 'running', 'kind': 'probe',
                   'url': 'https://mobile.yangkeduo.com/mall_page.html?ps=SYNTHETIC_SHARE'}
            if flag is not None:
                job['verify_storefront'] = flag
            (directory.parent / 'settings.json').write_text(json.dumps({'jobs': [job]}), encoding='utf-8')
            evidence = {'shopName': 'SYNTHETIC ONLY', 'userShareUrl': job['url'],
                        'sourceUrl': 'https://mobile.yangkeduo.com/mall_page.html?mall_id=123',
                        'header': 'SYNTHETIC ONLY', 'selectedMarkup': '<li class="current_x">上新</li>',
                        'observedAt': '2026-10-07T00:00:00Z'}
            receipt = {'status': status, 'intakeEvidence': evidence, 'website_collection_performed': False}
            if reason:
                receipt['reason'] = reason
            (directory / 'browser_result.json').write_text(json.dumps(receipt), encoding='utf-8')
            with patch.object(worker.subprocess, 'run', return_value=SimpleNamespace(returncode=returncode)) as process, \
                    patch.object(worker.capture_control, '_resolve_shop') as resolve, \
                    patch.object(worker.capture_control, 'begin') as begin, \
                    patch.object(worker, 'image_package') as images:
                result = worker.work(project, job_id)
            process.assert_called_once()
            resolve.assert_not_called()
            begin.assert_not_called()
            images.assert_not_called()
            self.assertFalse((project / 'data').exists())
            request = json.loads((directory / 'request.json').read_text(encoding='utf-8'))
            self.assertEqual(request['entry_url'], job['url'])
            self.assertEqual(result['execution_mode'], 'fixed_program')
            self.assertEqual(result['ai_requests'], 0)
            self.assertEqual(result['intakeEvidence'], evidence)
            return request, result

    def test_verified_probe_forwards_true_and_finishes_without_capture_or_publish(self):
        request, result = self.run_probe(True)
        self.assertIs(request['verify_storefront'], True)
        self.assertEqual(result['status'], 'complete')

    def test_only_explicit_boolean_true_enables_verification(self):
        for flag in (None, False, 'true', 1):
            with self.subTest(flag=flag):
                request, result = self.run_probe(flag)
                self.assertNotIn('verify_storefront', request)
                self.assertEqual(result['status'], 'complete')

    def test_nonzero_exit_cannot_confirm_a_saved_success_receipt(self):
        _, result = self.run_probe(True, returncode=7)
        self.assertEqual(result['status'], 'manual_review')
        self.assertEqual(result['reason'], 'browser_process_failed')

    def test_login_and_restriction_receipts_keep_their_specific_failure(self):
        for status, reason in [('needs_login', 'login_required'), ('manual_review', 'access_restricted')]:
            with self.subTest(status=status):
                _, result = self.run_probe(True, returncode=1, status=status, reason=reason)
                self.assertEqual(result['status'], status)
                self.assertEqual(result['reason'], reason)

    def test_ordinary_public_probe_keeps_its_existing_contract(self):
        request, result = self.run_probe(returncode=7)
        self.assertNotIn('verify_storefront', request)
        self.assertEqual(result['status'], 'complete')


if __name__ == '__main__':
    unittest.main()
