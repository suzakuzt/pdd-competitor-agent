"""Synthetic local-file handoff failures; never starts a browser or real job."""
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import call, patch

from pdd_monitor import collection_worker as worker


def denied(winerror, filename=None):
    error = PermissionError(13, 'SYNTHETIC_PRIVATE_REQUEST_AND_TOKEN', filename)
    error.winerror = winerror
    return error


class JsonReadTests(unittest.TestCase):
    def test_sharing_and_lock_violations_recover_with_bounded_delays(self):
        with patch.object(worker, '_read_json_text', side_effect=[denied(32), denied(33), '{"status":"complete"}']) as read, \
                patch.object(worker.time, 'sleep') as sleep:
            self.assertEqual(worker.load(Path('synthetic.json')), {'status': 'complete'})
        self.assertEqual(read.call_count, 3)
        self.assertEqual(sleep.call_args_list, [call(0.05), call(0.1)])

    def test_sharing_violation_stops_after_four_reads(self):
        error = denied(32)
        with patch.object(worker, '_read_json_text', side_effect=error) as read, \
                patch.object(worker.time, 'sleep') as sleep:
            with self.assertRaises(PermissionError) as caught:
                worker.load(Path('synthetic.json'))
        self.assertIs(caught.exception, error)
        self.assertEqual(read.call_count, 4)
        self.assertEqual(sleep.call_args_list, [call(0.05), call(0.1), call(0.2)])

    def test_permanent_access_denial_and_bad_json_do_not_retry(self):
        for failure in (denied(5), PermissionError(13, 'SYNTHETIC_PRIVATE'), 'invalid json'):
            with self.subTest(failure_type=type(failure).__name__), \
                    patch.object(worker, '_read_json_text', side_effect=failure if isinstance(failure, Exception) else None,
                                 return_value=failure) as read, patch.object(worker.time, 'sleep') as sleep:
                with self.assertRaises((PermissionError, json.JSONDecodeError)):
                    worker.load(Path('synthetic.json'))
            self.assertEqual(read.call_count, 1)
            sleep.assert_not_called()

    def test_browser_handoff_uses_the_retried_loader_without_relaunch(self):
        with TemporaryDirectory() as name:
            project = Path(name)
            directory = project / 'synthetic_job'
            directory.mkdir()
            process = SimpleNamespace(poll=lambda: None)

            def spawn(*args, **kwargs):
                (directory / 'browser_result.json').write_text('{"status":"complete"}', encoding='utf-8')
                return process

            with patch.object(worker.subprocess, 'Popen', side_effect=spawn) as launch, \
                    patch.object(worker, '_read_json_text', side_effect=[denied(32), '{"status":"complete"}']) as read, \
                    patch.object(worker.time, 'sleep') as sleep:
                actual = worker._start_shop_browser(project, directory, {})
            self.assertIs(actual, process)
            launch.assert_called_once()
            self.assertEqual(read.call_count, 2)
            sleep.assert_called_once_with(0.05)


@unittest.skipUnless(os.name == 'nt', 'Native Windows sharing semantics')
class NativeWindowsReadTests(unittest.TestCase):
    def exclusive(self, path):
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.CreateFileW.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                      wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE)
        kernel.CreateFileW.restype = wintypes.HANDLE
        kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
        kernel.CloseHandle.restype = wintypes.BOOL
        handle = kernel.CreateFileW(str(path), 0x80000000, 0, None, 3, 0x80, None)
        if handle == ctypes.c_void_p(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        return kernel, handle

    def test_real_temporary_share_lock_recovers_without_changing_json(self):
        import threading
        with TemporaryDirectory() as name:
            file = Path(name) / 'synthetic_receipt.json'
            file.write_text('{"status":"complete","synthetic":true}', encoding='utf-8')
            kernel, handle = self.exclusive(file)
            wait = worker.time.sleep
            first_retry = threading.Event()
            def unlock():
                first_retry.wait()
                wait(0.02)
                kernel.CloseHandle(handle)
            def retry_wait(delay):
                first_retry.set()
                wait(delay)
            closer = threading.Thread(target=unlock)
            closer.start()
            try:
                with patch.object(worker.time, 'sleep', side_effect=retry_wait) as sleep:
                    self.assertEqual(worker.load(file), {'status': 'complete', 'synthetic': True})
                self.assertIn(call(0.05), sleep.call_args_list)
            finally:
                first_retry.set()
                closer.join()

    def test_real_persistent_share_lock_retains_winerror_and_stops_after_four_reads(self):
        with TemporaryDirectory() as name:
            file = Path(name) / 'synthetic_receipt.json'
            file.write_text('{"synthetic":true}', encoding='utf-8')
            kernel, handle = self.exclusive(file)
            try:
                with patch.object(worker, '_read_json_text', wraps=worker._read_json_text) as read, \
                        patch.object(worker.time, 'sleep', wraps=worker.time.sleep) as sleep:
                    with self.assertRaises(PermissionError) as caught:
                        worker.load(file)
                self.assertEqual(caught.exception.winerror, 32)
                self.assertEqual(read.call_count, 4)
                self.assertEqual(sleep.call_args_list, [call(0.05), call(0.1), call(0.2)])
            finally:
                kernel.CloseHandle(handle)

    def test_native_access_denied5_does_not_retry(self):
        # OPEN_EXISTING without directory privileges deterministically returns
        # real ERROR_ACCESS_DENIED; no ACL or real file permission is changed.
        with TemporaryDirectory() as name, patch.object(worker.time, 'sleep') as sleep:
            with self.assertRaises(PermissionError) as caught:
                worker.load(Path(name))
            self.assertEqual(caught.exception.winerror, 5)
            sleep.assert_not_called()

    def test_real_bad_json_does_not_retry(self):
        with TemporaryDirectory() as name:
            file = Path(name) / 'synthetic_bad.json'
            file.write_text('invalid json', encoding='utf-8')
            with patch.object(worker.time, 'sleep') as sleep:
                with self.assertRaises(json.JSONDecodeError):
                    worker.load(file)
            sleep.assert_not_called()


class WorkerDiagnosticTests(unittest.TestCase):
    def test_capture_failure_keeps_stage_job_id_and_safe_file_location(self):
        with TemporaryDirectory() as name:
            project = Path(name).resolve()
            job_id = 'collect_' + 'a' * 32
            capture_id = 'capture_' + 'b' * 32
            shop_id = 'shop_' + 'c' * 24
            directory = project / 'state/local_collection' / job_id
            directory.mkdir(parents=True)
            (directory.parent / 'settings.json').write_text(json.dumps({'jobs': [
                {'id': job_id, 'status': 'running', 'shop_id': shop_id, 'kind': 'shop'}]}), encoding='utf-8')
            capture = {'acquired': True, 'job_id': capture_id,
                       'session_directory': str(project / 'sources' / capture_id), 'session_options': {}}
            shop = {'shop_name': 'SYNTHETIC_ONLY',
                    'source_url': 'https://mobile.yangkeduo.com/mall_page.html?mall_id=1'}
            error = denied(32, str(directory / 'browser_result.json'))
            with patch.object(worker, 'browser_mode', return_value='local_chrome'), \
                    patch.object(worker.capture_control, '_resolve_shop', return_value=shop), \
                    patch.object(worker.capture_control, 'begin', return_value=capture), \
                    patch.object(worker.capture_control, 'abandon') as abandon, \
                    patch.object(worker, 'browser_image_policy', return_value={}), \
                    patch.object(worker, '_start_shop_browser', side_effect=error), \
                    patch.object(worker, '_close_shop_browser'), \
                    patch.object(worker.capture_control, 'check') as check:
                result = worker.work(project, job_id)
            self.assertEqual(result['reason'], 'local_file_access_failed')
            self.assertEqual(result['capture_job_id'], capture_id)
            details = result['error_details']
            self.assertEqual(details['stage'], 'browser_capture_and_receipt_wait')
            self.assertEqual((details['errno'], details['winerror']), (13, 32))
            self.assertEqual(details['filename'], f'state/local_collection/{job_id}/browser_result.json')
            self.assertTrue(any(frame['file'] == 'pdd_monitor/collection_worker.py'
                                and type(frame['line']) is int for frame in details['frames']))
            serialized = json.dumps(result)
            self.assertNotIn('SYNTHETIC_PRIVATE', serialized)
            self.assertNotIn(str(project), serialized)
            self.assertNotIn('source_url', serialized)
            check.assert_not_called()
            abandon.assert_called_once_with(project, capture_id, 'local_worker_failed_requires_review')
            self.assertFalse((project / 'data').exists())

    def test_external_and_private_paths_are_omitted_from_errors(self):
        with TemporaryDirectory() as name:
            project = Path(name) / 'project'
            for filename in (Path(name) / 'outside/private.json',
                             project / 'state/capture_jobs/owner.private.json',
                             project / 'state/SYNTHETIC_TOKEN.json'):
                with self.subTest(filename=filename.name), \
                        patch.object(worker, '_work', side_effect=denied(5, str(filename))):
                    result = worker.work(project, 'collect_' + 'd' * 32)
                self.assertIsNone(result['error_details']['filename'])
                self.assertEqual(result['error_details']['stage'], 'initializing')
                self.assertNotIn('SYNTHETIC_PRIVATE', json.dumps(result))
                self.assertNotIn('owner.private', json.dumps(result))
                self.assertNotIn('SYNTHETIC_TOKEN', json.dumps(result))
                self.assertNotIn(str(Path(name)), json.dumps(result))


if __name__ == '__main__':
    unittest.main()
