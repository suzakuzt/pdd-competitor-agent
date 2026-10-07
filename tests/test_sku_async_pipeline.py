"""Isolated concurrency and publication guards; no browser or real backup runs."""
from contextlib import closing, contextmanager, ExitStack
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from pdd_monitor import collection_worker, sku_store, sku_append
from scripts.backup_store import _inventory
from tests.test_sku_pipeline import A, B, JOB, SHOP, assets, capture, synthetic_db


def synthetic_project(project):
    synthetic_db(project)
    images = project / 'data/images.sqlite3'
    images.unlink()  # The older scope fixture deliberately uses a text sentinel.
    with closing(sqlite3.connect(images)) as db:
        db.execute('CREATE TABLE synthetic_images(value TEXT)')
        db.execute('INSERT INTO synthetic_images VALUES (?)', ('original image archive',))
        db.commit()
    (project / 'schema.sql').write_text('-- synthetic schema\n', encoding='utf-8')
    (project / 'sources').mkdir()
    (project / 'sources/original.json').write_text('{"synthetic":true}', encoding='utf-8')


def synthetic_backup(project, workspace):
    workspace.mkdir(parents=True, exist_ok=False)
    return sku_store.SkuBatchBackup(project.resolve(), workspace.resolve(),
                                   workspace / 'paired_backup', _inventory(project))


class SingleSkuAsyncTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='pdd_sku_async_test_')
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name).resolve()
        self.project = root / 'project'
        synthetic_project(self.project)
        self.workspace = root / 'prepared'
        self.directory = self.project / 'state/local_collection' / JOB
        self.directory.mkdir(parents=True)
        self.job = {'id': JOB, 'shop_id': A, 'kind': 'sku',
                    'observation_id': 1, 'status': 'running'}
        self._write_job()
        self.gates = []

    def _write_job(self):
        (self.directory.parent / 'settings.json').write_text(
            json.dumps({'jobs': [self.job]}), encoding='utf-8')

    def _event(self):
        event = threading.Event()
        self.gates.append(event)
        return event

    def _wait(self, event, label='event'):
        self.assertTrue(event.wait(5), f'Timed out waiting for {label}')

    def _browser_result(self, status='complete', returncode=0):
        result = {'status': status, 'website_collection_performed': True,
                  'sku': capture(), 'images': assets()}
        if status == 'needs_login':
            result['reason'] = 'login_required'
        (self.directory / 'browser_result.json').write_text(json.dumps(result), encoding='utf-8')
        return SimpleNamespace(returncode=returncode)

    def _assert_unpublished(self):
        self.assertFalse((self.project / 'sources/sku_captures' / JOB).exists())
        self.assertFalse((self.project / 'sources/sku_captures/index' / A / '1.json').exists())

    @contextmanager
    def _background(self, prepare, browser, save=None):
        done = threading.Event()
        phases = []
        phase_events = {name: threading.Event() for name in
                        ('sku_read_and_backup', 'sku_backup_wait', 'sku_validation')}
        outcome = {}
        real_atomic = collection_worker.atomic

        def observe_progress(path, value):
            real_atomic(path, value)
            if Path(path).name == 'progress.json':
                phases.append(deepcopy(value))
                if value.get('stage') in phase_events:
                    phase_events[value['stage']].set()

        def execute():
            try:
                outcome['result'] = collection_worker.work(self.project, JOB)
            except BaseException as error:
                outcome['error'] = error
            finally:
                done.set()

        if save is None:
            save = Mock(return_value={'status': 'complete', 'variant_count': 1})
        with ExitStack() as stack:
            stack.enter_context(patch.object(collection_worker.capture_control, '_resolve_shop', return_value=SHOP))
            stack.enter_context(patch.object(collection_worker, '_sku_workspace', return_value=self.workspace))
            stack.enter_context(patch.object(collection_worker, 'atomic', side_effect=observe_progress))
            backup_mock = stack.enter_context(patch.object(sku_append, 'prepare_append_protection',
                                                          side_effect=lambda project, workspace, job: prepare(project, workspace)))
            browser_mock = stack.enter_context(patch.object(collection_worker.subprocess, 'run', side_effect=browser))
            save_mock = stack.enter_context(patch.object(sku_store, 'save_capture', side_effect=save))
            thread = threading.Thread(target=execute, name='synthetic-sku-worker', daemon=True)
            thread.start()
            run = SimpleNamespace(done=done, outcome=outcome, thread=thread, phases=phases,
                                  phase_events=phase_events, backup=backup_mock,
                                  browser=browser_mock, save=save_mock)
            try:
                yield run
            finally:
                for gate in self.gates:
                    gate.set()
                thread.join(7)
                self.assertFalse(thread.is_alive(), 'Worker left a running branch after test cleanup')
                if 'error' in outcome:
                    raise outcome['error']

    def _finished(self, run):
        self._wait(run.done, 'worker result')
        run.thread.join(2)
        self.assertFalse(run.thread.is_alive())
        if 'error' in run.outcome:
            raise run.outcome['error']
        return run.outcome['result']

    def test_browser_and_backup_overlap_then_real_save_completes_under_paired_locks(self):
        backup_started, browser_started = self._event(), self._event()
        backup_exit, browser_exit = self._event(), self._event()
        release_backup, save_entered, release_save = self._event(), self._event(), self._event()
        saved = self._event()
        thread_ids = {}
        before = {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                  for path in (self.project / 'data').iterdir()}
        real_save, real_publish = sku_store.save_capture, sku_store._publish_latest
        lock_checks = []

        def assert_paired_locks(label):
            for database in ('monitor.sqlite3', 'images.sqlite3'):
                with closing(sqlite3.connect(self.project / 'data' / database,
                                             timeout=0, isolation_level=None)) as db:
                    with self.assertRaisesRegex(sqlite3.OperationalError, 'locked'):
                        db.execute('BEGIN IMMEDIATE')
            lock_checks.append(label)

        def checked_inventory(project):
            assert_paired_locks('inventory')
            return _inventory(project)

        def checked_publish(project, value):
            assert_paired_locks('publish')
            return real_publish(project, value)

        def prepare(project, workspace):
            thread_ids['backup'] = threading.get_ident()
            backup_started.set()
            self._wait(browser_started, 'browser branch starting while backup is active')
            result = synthetic_backup(project, workspace)
            self._wait(release_backup, 'backup release')
            backup_exit.set()
            return result

        def browser(*args, **kwargs):
            thread_ids['browser'] = threading.get_ident()
            browser_started.set()
            self._wait(backup_started, 'backup branch starting while browser is active')
            result = self._browser_result()
            browser_exit.set()
            return result

        def save(*args, **kwargs):
            self.assertTrue(backup_exit.is_set() and browser_exit.is_set())
            self.assertEqual(threading.get_ident(), thread_ids['browser'])
            self.assertEqual(args[4], self.workspace / 'item')
            self.assertTrue(kwargs['parallel_prepared'])
            self.assertEqual(kwargs['cancel_file'], self.directory / 'cancel.json')
            self.assertIsInstance(kwargs['batch_backup'], sku_store.SkuBatchBackup)
            save_entered.set()
            self._wait(release_save, 'save release')
            result = real_save(*args, **kwargs)
            saved.set()
            return result

        with patch('scripts.backup_store._inventory', side_effect=checked_inventory), \
             patch.object(sku_store, '_publish_latest', side_effect=checked_publish), \
             self._background(prepare, browser, save) as run:
            self._wait(run.phase_events['sku_backup_wait'], 'waiting-for-backup phase')
            self.assertTrue(backup_started.is_set() and browser_exit.is_set())
            self.assertFalse(backup_exit.is_set())
            self.assertFalse(run.done.is_set())
            run.save.assert_not_called()
            self._assert_unpublished()
            release_backup.set()
            self._wait(save_entered, 'save entered')
            self.assertFalse(run.done.is_set())
            self.assertFalse(saved.is_set())
            self._assert_unpublished()
            release_save.set()
            result = self._finished(run)
            self.assertTrue(saved.is_set())
            run.browser.assert_called_once()
            self.assertEqual(run.backup.call_count, 1)
            self.assertEqual(run.backup.call_args.args[:2], (self.project, self.workspace))
            self.assertEqual(run.backup.call_args.args[2]['observation_id'], 1)
            run.save.assert_called_once()

        self.assertNotEqual(thread_ids['backup'], thread_ids['browser'])
        self.assertEqual(result['status'], 'complete')
        self.assertEqual(result['ai_requests'], 0)
        self.assertTrue(result['parallel_backup_prepared'])
        self.assertTrue(result['receipt']['source_baseline_rechecked'])
        self.assertFalse(result['receipt']['backup_reused_for_batch'])
        self.assertEqual(set(result['stages_seconds']),
                         {'browser_read', 'backup_prepare', 'backup_wait', 'save', 'parallel_total'})
        self.assertTrue(all(value >= 0 for value in result['stages_seconds'].values()))
        self.assertEqual([phase['stage'] for phase in run.phases],
                         ['sku_read_and_backup', 'sku_backup_wait', 'sku_validation'])
        self.assertEqual([phase['backup_status'] for phase in run.phases],
                         ['preparing', 'awaiting_result', 'verified'])
        self.assertEqual(lock_checks, ['inventory', 'publish'])
        latest = sku_store.read_latest(self.project, A, 1)
        self.assertEqual(latest['capture_id'], JOB)
        self.assertEqual(latest['variants'][0]['current_price_yuan'], '14.50')
        self.assertTrue(latest['variants'][0]['image_sha256'])
        self.assertEqual(before, {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                                  for path in (self.project / 'data').iterdir()})
        for database in ('monitor.sqlite3', 'images.sqlite3'):
            with closing(sqlite3.connect(self.project / 'data' / database, timeout=0)) as db:
                db.execute('BEGIN IMMEDIATE')
                db.rollback()

    def test_browser_timeout_still_waits_for_backup_and_never_saves(self):
        self._browser_failure('timeout', 'manual_review', 'browser_process_timeout')

    def test_browser_nonzero_exit_still_waits_for_backup_and_never_saves(self):
        self._browser_failure('nonzero', 'manual_review', 'browser_process_failed')

    def test_browser_login_stop_still_waits_for_backup_and_never_saves(self):
        self._browser_failure('login', 'needs_login', 'login_required')

    def _browser_failure(self, mode, status, reason):
        backup_started, backup_exit, release_backup = self._event(), self._event(), self._event()

        def prepare(project, workspace):
            backup_started.set()
            result = synthetic_backup(project, workspace)
            self._wait(release_backup, 'failure backup release')
            backup_exit.set()
            return result

        def browser(*args, **kwargs):
            self._wait(backup_started, 'backup start')
            if mode == 'timeout':
                raise subprocess.TimeoutExpired('synthetic-browser', 1)
            return self._browser_result('needs_login' if mode == 'login' else 'complete',
                                        returncode=1 if mode == 'nonzero' else 0)

        with self._background(prepare, browser) as run:
            self._wait(run.phase_events['sku_backup_wait'], 'failure join phase')
            self.assertFalse(backup_exit.is_set())
            self.assertFalse(run.done.is_set())
            run.save.assert_not_called()
            release_backup.set()
            result = self._finished(run)
            self.assertTrue(backup_exit.is_set())
            run.save.assert_not_called()
            run.browser.assert_called_once()
        self.assertEqual((result['status'], result['reason']), (status, reason))
        self.assertTrue(result['parallel_backup_prepared'])
        self.assertNotIn('save', result['stages_seconds'])
        self._assert_unpublished()

    def test_backup_failure_does_not_return_until_browser_exits(self):
        browser_started, release_browser = self._event(), self._event()
        backup_exit, browser_exit = self._event(), self._event()

        def prepare(project, workspace):
            self._wait(browser_started, 'browser start')
            backup_exit.set()
            raise ValueError('synthetic failed restore verification')

        def browser(*args, **kwargs):
            browser_started.set()
            self._wait(release_browser, 'browser release after backup failed')
            result = self._browser_result()
            browser_exit.set()
            return result

        with self._background(prepare, browser) as run:
            self._wait(backup_exit, 'failed backup exit')
            self.assertFalse(browser_exit.is_set())
            self.assertFalse(run.done.is_set())
            run.save.assert_not_called()
            release_browser.set()
            result = self._finished(run)
            self.assertTrue(browser_exit.is_set())
            run.save.assert_not_called()
        self.assertEqual((result['status'], result['reason']), ('manual_review', 'sku_backup_failed'))
        self.assertEqual(result['backup_error_type'], 'ValueError')
        self.assertFalse(result['parallel_backup_prepared'])
        self._assert_unpublished()

    def test_cancel_during_reading_joins_both_branches_without_saving(self):
        backup_started, browser_started = self._event(), self._event()
        release_backup, release_browser = self._event(), self._event()
        backup_exit, browser_exit = self._event(), self._event()

        def prepare(project, workspace):
            backup_started.set()
            result = synthetic_backup(project, workspace)
            self._wait(release_backup, 'cancelled backup release')
            backup_exit.set()
            return result

        def browser(*args, **kwargs):
            browser_started.set()
            self._wait(release_browser, 'cancelled browser release')
            result = self._browser_result()
            browser_exit.set()
            return result

        with self._background(prepare, browser) as run:
            self._wait(backup_started, 'backup start')
            self._wait(browser_started, 'browser start')
            (self.directory / 'cancel.json').write_text('{}', encoding='utf-8')
            self.assertFalse(run.done.is_set())
            release_browser.set()
            self._wait(run.phase_events['sku_backup_wait'], 'cancelled join phase')
            self.assertTrue(browser_exit.is_set())
            self.assertFalse(backup_exit.is_set())
            self.assertFalse(run.done.is_set())
            run.save.assert_not_called()
            release_backup.set()
            result = self._finished(run)
            self.assertTrue(backup_exit.is_set())
            run.save.assert_not_called()
        self.assertEqual((result['status'], result['reason']), ('cancelled', 'cancelled'))
        self._assert_unpublished()

    def test_prestart_cancel_starts_neither_branch(self):
        (self.directory / 'cancel.json').write_text('{}', encoding='utf-8')
        with self._background(Mock(), Mock()) as run:
            result = self._finished(run)
            run.backup.assert_not_called()
            run.browser.assert_not_called()
            run.save.assert_not_called()
        self.assertEqual(result['status'], 'cancelled')
        self.assertFalse(result['website_collection_performed'])
        self.assertFalse(result['parallel_backup_prepared'])
        self._assert_unpublished()

    def test_invalid_scope_rejected_before_either_branch_starts(self):
        for changes in ({'shop_id': B}, {'observation_id': 3}, {'observation_id': 4},
                        {'observation_id': 6}, {'observation_id': 99}):
            with self.subTest(changes=changes):
                self.job.update(shop_id=A, observation_id=1)
                self.job.update(changes)
                self._write_job()
                with self._background(Mock(), Mock()) as run:
                    result = self._finished(run)
                    run.backup.assert_not_called()
                    run.browser.assert_not_called()
                    run.save.assert_not_called()
                self.assertEqual(result['status'], 'failed')
                self.assertEqual(result['error_type'], 'ValueError')
                self._assert_unpublished()

    def test_source_changed_while_reading_is_manual_review_with_real_save(self):
        baseline_ready = self._event()

        def prepare(project, workspace):
            result = synthetic_backup(project, workspace)
            baseline_ready.set()
            return result

        def browser(*args, **kwargs):
            self._wait(baseline_ready, 'complete source baseline')
            (self.project / 'sources/original.json').write_text('{"changed":true}', encoding='utf-8')
            return self._browser_result()

        with self._background(prepare, browser, sku_store.save_capture) as run:
            result = self._finished(run)
            run.save.assert_called_once()
        self.assertEqual((result['status'], result['reason']),
                         ('manual_review', 'sku_backup_source_changed'))
        self.assertTrue(result['parallel_backup_prepared'])
        self._assert_unpublished()

    def test_cancellation_after_staging_is_reported_without_publishing(self):
        real_read = sku_store._read_capture

        def cancel_at_final_read(directory):
            result = real_read(directory)
            if directory.name.startswith('.' + JOB + '.'):
                (self.directory / 'cancel.json').write_text('{}', encoding='utf-8')
            return result

        with patch.object(sku_store, '_read_capture', side_effect=cancel_at_final_read), \
             self._background(synthetic_backup, lambda *a, **kw: self._browser_result(),
                              sku_store.save_capture) as run:
            result = self._finished(run)
            run.save.assert_called_once()
        self.assertEqual((result['status'], result['reason']), ('cancelled', 'cancelled'))
        self.assertTrue((self.workspace / 'item/sku_rehearsal/capture.json').is_file())
        self._assert_unpublished()


class ParallelSkuSourceGuardTests(unittest.TestCase):
    def test_all_backed_up_source_changes_reject_publication(self):
        for changed in ('monitor', 'images', 'schema', 'source_modified', 'source_added', 'source_removed'):
            with self.subTest(changed=changed), tempfile.TemporaryDirectory(prefix='pdd_sku_guard_test_') as name:
                root = Path(name).resolve()
                project, workspace = root / 'project', root / 'prepared'
                synthetic_project(project)
                backup = synthetic_backup(project, workspace)
                source = project / 'sources/original.json'
                if changed in ('monitor', 'images'):
                    database = project / 'data' / (changed + '.sqlite3')
                    with closing(sqlite3.connect(database)) as db:
                        if changed == 'monitor':
                            db.execute('UPDATE shops SET shop_name=? WHERE shop_id=?', ('changed other shop', B))
                        else:
                            db.execute('INSERT INTO synthetic_images VALUES (?)', ('additional archive',))
                        db.commit()
                elif changed == 'schema':
                    (project / 'schema.sql').write_text('-- changed schema\n', encoding='utf-8')
                elif changed == 'source_modified':
                    source.write_text('{"changed":true}', encoding='utf-8')
                elif changed == 'source_added':
                    (project / 'sources/new.json').write_text('{}', encoding='utf-8')
                else:
                    source.unlink()
                with self.assertRaises(sku_store.SkuSourceChanged):
                    sku_store.save_capture(project, {'id': JOB, 'shop_id': A, 'observation_id': 1},
                                           capture(), assets(), workspace / 'item',
                                           batch_backup=backup, parallel_prepared=True)
                self.assertFalse((project / 'sources/sku_captures' / JOB).exists())
                self.assertFalse((project / 'sources/sku_captures/index' / A / '1.json').exists())

    def test_missing_or_partial_source_inventory_cannot_prove_baseline(self):
        for baseline_kind in ('missing', 'database_only'):
            with self.subTest(baseline_kind=baseline_kind), tempfile.TemporaryDirectory(prefix='pdd_sku_guard_test_') as name:
                root = Path(name).resolve()
                project, workspace = root / 'project', root / 'prepared'
                synthetic_project(project)
                workspace.mkdir()
                inventory = None if baseline_kind == 'missing' else {
                    key: value for key, value in _inventory(project).items() if key.startswith('data/')}
                backup = sku_store.SkuBatchBackup(project, workspace, workspace / 'paired_backup', inventory)
                with self.assertRaises(sku_store.SkuSourceChanged):
                    sku_store.save_capture(project, {'id': JOB, 'shop_id': A, 'observation_id': 1},
                                           capture(), assets(), workspace / 'item',
                                           batch_backup=backup, parallel_prepared=True)
                self.assertFalse((project / 'sources/sku_captures' / JOB).exists())

    def test_prepare_requires_matching_complete_manifest_and_never_retries(self):
        for defect in ('none', 'missing_files', 'missing_source_after', 'missing_hash', 'different_hash'):
            with self.subTest(defect=defect), tempfile.TemporaryDirectory(prefix='pdd_sku_manifest_test_') as name:
                root = Path(name).resolve()
                project, workspace = root / 'project', root / 'prepared'
                synthetic_project(project)
                inventory = _inventory(project)
                manifest = {'status': 'complete', 'source_project': str(project),
                            'backup_validation_ok': True, 'restore_validation_ok': True,
                            'source_unchanged': True, 'source_before': deepcopy(inventory),
                            'source_after': deepcopy(inventory), 'files': deepcopy(inventory)}
                if defect == 'missing_files':
                    manifest.pop('files')
                elif defect == 'missing_source_after':
                    manifest.pop('source_after')
                elif defect == 'missing_hash':
                    for key in ('source_before', 'source_after', 'files'):
                        manifest[key]['sources/original.json'].pop('sha256')
                elif defect == 'different_hash':
                    manifest['files']['sources/original.json']['sha256'] = 'f' * 64

                def original_entrypoint(command, **kwargs):
                    self.assertEqual(Path(command[4]), project / 'scripts/backup_store.py')
                    backup_directory = workspace / 'paired_backup'
                    backup_directory.mkdir()
                    (backup_directory / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
                    return SimpleNamespace(returncode=0)

                with patch.object(sku_store.subprocess, 'run', side_effect=original_entrypoint) as original:
                    if defect == 'none':
                        result = sku_store.prepare_batch_backup(project, workspace)
                        self.assertEqual(result.source_inventory, inventory)
                        self.assertIn('sources/original.json', result.source_inventory)
                    else:
                        with self.assertRaises(ValueError):
                            sku_store.prepare_batch_backup(project, workspace)
                    original.assert_called_once()


if __name__ == '__main__':
    unittest.main()
