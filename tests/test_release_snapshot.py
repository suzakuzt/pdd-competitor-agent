"""Portable release tests: every DB, state and simulated job is temporary."""
import base64
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

SOURCE_PROJECT = Path(os.environ.get('PDD_TEST_PROJECT', Path(__file__).resolve().parents[1]))
SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/release_snapshot.py'
spec = importlib.util.spec_from_file_location('release_snapshot', SCRIPT)
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)
PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jh3sAAAAASUVORK5CYII=')


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False), encoding='utf-8')


def project_call(project, code, payload=None):
    script = 'import sys,json;from pathlib import Path;sys.path.insert(0,sys.argv[1]);project=Path(sys.argv[1]);payload=json.load(sys.stdin);' + code
    result = subprocess.run([sys.executable, '-B', '-X', 'utf8', '-c', script, str(project)], input=json.dumps(payload), text=True, encoding='utf-8', capture_output=True)
    if result.returncode:
        raise AssertionError(result.stderr)
    return json.loads(result.stdout)


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='pdd_release_SYNTHETIC_')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.project = self.root / 'project'
        (self.project / 'scripts').mkdir(parents=True)
        shutil.copytree(SOURCE_PROJECT / 'pdd_monitor', self.project / 'pdd_monitor', ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
        for name in ('schema.sql', 'AGENTS.md', 'scripts/backup_store.py'):
            shutil.copyfile(SOURCE_PROJECT / name, self.project / name)
        self.old_sources = self.project / 'sources'
        self.old_sources.mkdir()
        for number in (901, 902):
            write(self.old_sources / f'old{number}.json', {'synthetic': True, 'shopName': 'SYNTHETIC OLD',
                'sourceUrl': f'https://mobile.yangkeduo.com/mall_page.html?mall_id={number}',
                'observedFrom': '2026-10-04T01:00:00Z', 'observedTo': '2026-10-04T01:01:00Z',
                'status': 'complete', 'endBoundaryObserved': True,
                'rows': [{'viewOrder': 1, 'title': 'SYNTHETIC OLD CARD', 'salesRaw': '已拼11件',
                          'imageUrl': 'https://example.invalid/old.png', 'observedAt': '2026-10-04T01:00:00Z'}]})
        archive = self.root / 'old_image.zip'
        with zipfile.ZipFile(archive, 'w') as z:
            z.writestr('one.png', PNG)
            z.writestr('manifest.json', json.dumps({'items': [{'viewOrder': 1, 'originalImageUrl': 'https://example.invalid/old.png', 'archivePath': 'one.png', 'sha256': hashlib.sha256(PNG).hexdigest()}]}))
        project_call(self.project, 'from pdd_monitor.store import import_snapshot;print(json.dumps([import_snapshot(project/"data",project/"sources/old901.json",payload),import_snapshot(project/"data",project/"sources/old902.json")]))', str(archive))
        write(self.project / 'state/new_arrivals/fixed.json', {'synthetic': True, 'baseline': ['never_move_this']})
        write(self.project / 'state/competitors/tracking.json', {'synthetic': True, 'enabled': True, 'revision': 3})
        self.job = project_call(self.project, 'from pdd_monitor.capture_control import begin;print(json.dumps(begin(project,source_url="https://mobile.yangkeduo.com/mall_page.html?mall_id=903",shop_name="SYNTHETIC NEW SHOP")))')
        self.capture = Path(self.job['session_directory'])
        self.workspace = self.root / 'release_workspace'
        self.snapshot = self.capture / 'snapshot.json'
        self.make_capture()

    def make_capture(self, partial=False):
        at = release.now()
        identity = {'identity_kind': 'mall_id', 'stable_identifier': '903', 'source_mall_id': '903', 'source_mall_sn': None,
                    'origin': 'https://mobile.yangkeduo.com', 'source_url': self.job['source_url']}
        cards = []
        for index in range(2):
            title = f'SYNTHETIC NEW CARD {index}'
            cards.append({'slot': f'{index}:0', 'domColumn': index, 'domRow': 0, 'title': title, 'identityTitle': title,
                'imageUrl': f'https://example.invalid/new{index}.png', 'rawText': title,
                'goodsUrl': None, 'goodsId': None, 'salesRaw': '已拼12件' if index == 0 else None, 'priceRaw': '¥4.50',
                'ready': True, 'pendingReasons': [], 'position': {'top': 100, 'left': 10 + 400*index, 'bottom': 200, 'viewportTop': 100, 'viewportBottom': 200}, 'inViewport': True})
        batch = {'batch': 1, 'driverVersion': 5, 'at': at, 'pageUrl': self.job['source_url'],
                 'shopVerified': True, 'shopIdentityVerified': True, 'listPresent': True, 'columnLayoutVerified': True,
                 'observedShopIdentity': identity, 'expectedShopIdentity': identity,
                 'scrollTop': 0, 'scrollHeight': 900, 'viewportHeight': 720, 'viewportWidth': 1280,
                 'columnSlotCounts': [1, 1], 'loadedCardCount': 2, 'cards': cards,
                 'slotManifest': [{k: c[k] for k in ('slot', 'domColumn', 'domRow', 'position', 'pendingReasons')} | {'state': 'ready'} for c in cards],
                 'boundaries': [] if partial else [{'top': 300, 'bottom': 340, 'documentTop': 300, 'visible': True, 'rendered': True}],
                 'recommendations': [], 'endBoundaryObserved': not partial, 'scrollEvidence': None, 'readCoverageBottom': 200,
                 'added': 2, 'seen': 2, 'pending': 0, 'stop': None, 'complete': not partial, 'noProgressReads': 0}
        write(self.capture / 'batches/batch_0001.json', batch)
        rows = [{**c, 'viewOrder': index + 1, 'recordKey': f'position_100_{c["position"]["left"]}',
                 'firstRenderedAt': at, 'firstObservedAt': at, 'observedAt': at, 'observedAtPrecision': 'batch_read',
                 'firstObservedAtPrecision': 'batch_read', 'observationTimeMeaning': 'latest actual rendered DOM batch read; not listing time', 'latestBatch': 1} for index, c in enumerate(cards)]
        snap = {'synthetic': True, 'shopName': self.job['shop_name'], 'sourceUrl': self.job['source_url'], 'shopIdentityEvidence': identity,
                'status': 'partial' if partial else 'complete', 'endBoundaryObserved': not partial, 'observedFrom': at, 'observedTo': at,
                'collectionEvidence': {'driverVersion': 5, 'attemptId': self.job['attempt_id'], 'mode': 'all rendered nonhidden including offviewport',
                    'batchCount': 1, 'verifiedBatchCount': 1, 'pendingSlotCount': 0, 'pendingSlots': [], 'stopReason': 'synthetic_interrupted' if partial else None,
                    'checkpointDirectory': f'sources/{self.job["job_id"]}/batches', 'maxScrollViewports': 3,
                    'lastAttemptAt': at, 'shopIdentityChecks': [{'at': at, 'pageUrl': batch['pageUrl'], 'verified': True, 'stop': None}]},
                'scrolls': [{'at': at, 'count': 2, 'pending': 0, 'scrollTop': 0, 'end': not partial}], 'rows': rows}
        write(self.snapshot, snap)
        self.sha = release.fingerprint(self.snapshot)['sha256']
        write(self.capture / 'session.json', {'attemptId': self.job['attempt_id'], 'operation': None, 'phase': snap['status'], 'importReady': True,
                    'snapshot': {'file': 'snapshot.json', 'sha256': self.sha, 'status': snap['status']}})

    def prepare(self, **kwargs):
        return release.prepare(self.project, self.snapshot, self.sha, self.job['shop_id'], self.workspace, **kwargs)

    def test_complete_end_to_end_preserves_old_images_and_fixed_state(self):
        before = release.row_baseline(self.project)
        state = release.public_state(self.project)
        prepared = self.prepare()
        self.assertEqual(before, release.row_baseline(self.project))
        self.assertEqual('preflight_passed', release.apply(self.workspace, self.job['job_id'])['status'])
        with patch.object(release, 'verify_backup', wraps=release.verify_backup) as verified:
            result = release.apply(self.workspace, self.job['job_id'], execute=True)
        self.assertEqual(2, verified.call_count, 'Verify at entry and immediately before import, without redundant scans')
        self.assertEqual('finished', result['status'])
        self.assertEqual(2, result['new_run']['cards'])
        self.assertEqual('complete', result['journal']['journal_status'])
        self.assertFalse(result['dashboard_built'])
        self.assertTrue(all(release.preserved(before, release.row_baseline(self.project)).values()))
        self.assertEqual(state, release.public_state(self.project))
        databases = release.db_hashes(self.project)
        self.assertEqual('already_finished', release.apply(self.workspace, self.job['job_id'], execute=True)['status'])
        self.assertEqual(databases, release.db_hashes(self.project))
        public = self.project / prepared['source_relative']
        self.assertTrue((public / 'batches/batch_0001.json').exists())
        self.assertFalse((public / 'session.json').exists())
        self.assertGreaterEqual(result['timings']['apply_seconds'], 0)

    def test_prepare_reuses_verified_restore_without_copying_history_again(self):
        original = release.copy_exact
        copied = []
        def record_copy(source, destination):
            copied.append(Path(source))
            self.assertNotIn('paired_backup', Path(source).parts,
                             'The backup already produced the independent verified restoration')
            return original(source, destination)
        with patch.object(release, 'copy_exact', side_effect=record_copy):
            prepared = self.prepare()
        self.assertTrue(prepared['validated_restore_reused'])
        attempt = self.workspace / prepared['attempt']
        manifest = release.read(attempt / 'paired_backup/manifest.json')
        recovery = attempt / 'recovery_project'
        self.assertEqual(manifest['retained_restore']['path'], str(recovery))
        self.assertEqual(manifest['retained_restore']['files'], manifest['files'])
        old = Path('sources/old901.json')
        self.assertEqual((recovery / old).read_bytes(), (self.project / old).read_bytes())
        self.assertFalse(os.path.samefile(recovery / old, attempt / 'paired_backup' / old))
        self.assertTrue(all(value >= 0 for value in prepared['timings'].values()))
        self.assertTrue(copied)

    def fail_changed_restore(self, mutate):
        original = release.subprocess.run
        before = release.db_hashes(self.project)
        def change_after_backup(args, **kwargs):
            result = original(args, **kwargs)
            if str(self.project / 'scripts/backup_store.py') in args and result.returncode == 0:
                recovery = Path(args[args.index('--retain-restore') + 1])
                mutate(recovery)
            return result
        with patch.object(release.subprocess, 'run', side_effect=change_after_backup):
            with self.assertRaises(release.ReleaseError):
                self.prepare()
        self.assertFalse((self.workspace / 'prepared.json').exists())
        self.assertEqual(before, release.db_hashes(self.project))

    def test_restore_changed_after_backup_is_rejected_before_rehearsal(self):
        self.fail_changed_restore(lambda recovery: (recovery / 'sources/old901.json').write_bytes(b'SYNTHETIC CHANGED'))

    def test_restore_extra_file_is_rejected_by_complete_inventory(self):
        self.fail_changed_restore(lambda recovery: (recovery / 'unexpected.txt').write_text('SYNTHETIC EXTRA', encoding='utf-8'))

    def test_restore_replaced_directory_with_same_bytes_is_rejected(self):
        def replace(recovery):
            moved = recovery.with_name('original_restore')
            recovery.rename(moved)
            shutil.copytree(moved, recovery)
        self.fail_changed_restore(replace)

    def test_restore_hard_link_is_rejected_even_with_identical_bytes(self):
        def link(recovery):
            path = recovery / 'sources/old901.json'
            path.unlink()
            os.link(recovery.parent / 'paired_backup/sources/old901.json', path)
        self.fail_changed_restore(link)

    def test_backup_hard_link_to_live_database_is_rejected_before_import(self):
        prepared = self.prepare()
        backup_database = self.workspace / prepared['attempt'] / 'paired_backup' / release.DATABASES[0]
        backup_database.unlink()
        os.link(self.project / release.DATABASES[0], backup_database)
        before = release.db_hashes(self.project)
        with self.assertRaisesRegex(release.ReleaseError, 'hard links'):
            release.apply(self.workspace, self.job['job_id'], execute=True)
        self.assertFalse((self.workspace / 'commit_intent.json').exists())
        self.assertEqual(before, release.db_hashes(self.project))

    def test_changes_while_copying_inputs_are_rejected_at_final_import_boundary(self):
        prepared = self.prepare()
        before = release.db_hashes(self.project)
        backup_source = self.workspace / prepared['attempt'] / 'paired_backup/sources/old901.json'
        source = self.project / prepared['source_relative']
        original = release.copy_exact
        for target in (self.old_sources / 'old901.json', backup_source, self.snapshot):
            saved = target.read_bytes()
            changed = False
            def change_during_copy(src, destination):
                nonlocal changed
                result = original(src, destination)
                if not changed and Path(destination).is_relative_to(source):
                    target.write_bytes(saved + b' ')
                    changed = True
                return result
            try:
                with self.subTest(target=target), patch.object(release, 'copy_exact', side_effect=change_during_copy):
                    with self.assertRaises(release.ReleaseError):
                        release.apply(self.workspace, self.job['job_id'], execute=True)
                    self.assertTrue(changed)
                    self.assertFalse((self.workspace / 'committed.json').exists())
                    self.assertFalse((self.workspace / 'finished.json').exists())
                    self.assertEqual(before, release.db_hashes(self.project))
            finally:
                target.write_bytes(saved)

    def test_published_copy_changed_during_final_backup_scan_is_not_imported(self):
        prepared = self.prepare()
        before = release.db_hashes(self.project)
        published = self.project / prepared['source_relative'] / 'snapshot.json'
        original = release.verify_backup
        calls = 0
        def change_published(backup):
            nonlocal calls
            result = original(backup)
            calls += 1
            if calls == 2:
                published.write_bytes(published.read_bytes() + b' ')
            return result
        with patch.object(release, 'verify_backup', side_effect=change_published):
            with self.assertRaisesRegex(release.ReleaseError, 'Published source changed'):
                release.apply(self.workspace, self.job['job_id'], execute=True)
        self.assertEqual(2, calls)
        self.assertEqual(before, release.db_hashes(self.project))
        self.assertFalse((self.workspace / 'committed.json').exists())
        self.assertFalse((self.workspace / 'finished.json').exists())

    def test_partial_is_preserved_and_finish_does_not_promote(self):
        self.make_capture(partial=True)
        self.prepare()
        result = release.apply(self.workspace, self.job['job_id'], execute=True)
        self.assertEqual('partial', result['snapshot_status'])
        self.assertEqual('partial', result['journal']['journal_status'])
        self.assertEqual('synthetic_interrupted', result['journal']['reason'])

    def test_failure_after_commit_resumes_finish_without_second_import(self):
        self.prepare()
        original = release.worker
        def fail_finish(project, action, payload):
            if action == 'finish':
                raise release.ReleaseError('SYNTHETIC INTERRUPTED FINISH')
            return original(project, action, payload)
        with patch.object(release, 'worker', side_effect=fail_finish):
            with self.assertRaises(release.ReleaseError):
                release.apply(self.workspace, self.job['job_id'], execute=True)
        self.assertTrue((self.workspace / 'committed.json').exists())
        databases = release.db_hashes(self.project)
        self.assertEqual('finish_existing_import', release.apply(self.workspace, self.job['job_id'])['next_action'])
        self.assertEqual('finished', release.apply(self.workspace, self.job['job_id'], execute=True)['status'])
        self.assertEqual(databases, release.db_hashes(self.project))

    def test_backup_failure_retained_and_prepare_retry_uses_new_attempt(self):
        original = release.subprocess.run
        def fail_backup(args, **kwargs):
            if str(self.project / 'scripts/backup_store.py') in args:
                return subprocess.CompletedProcess(args, 1, '', 'SYNTHETIC FAILURE')
            return original(args, **kwargs)
        with patch.object(release.subprocess, 'run', side_effect=fail_backup):
            with self.assertRaises(release.ReleaseError): self.prepare()
        self.assertFalse((self.workspace / 'prepared.json').exists())
        self.assertEqual('prepared', self.prepare()['status'])
        self.assertEqual(2, len(list((self.workspace / 'attempts').iterdir())))

    def test_protected_state_or_old_source_change_blocks_apply(self):
        self.prepare()
        before = release.db_hashes(self.project)
        original = (self.project / 'state/new_arrivals/fixed.json').read_bytes()
        write(self.project / 'state/new_arrivals/fixed.json', {'baseline': ['tampered']})
        with self.assertRaises(release.ReleaseError): release.apply(self.workspace, self.job['job_id'], execute=True)
        (self.project / 'state/new_arrivals/fixed.json').write_bytes(original)
        write(self.old_sources / 'old901.json', {'changed': True})
        with self.assertRaises(release.ReleaseError): release.apply(self.workspace, self.job['job_id'], execute=True)
        self.assertEqual(before, release.db_hashes(self.project))

    def test_new_database_change_requires_new_backup(self):
        self.prepare()
        project_call(self.project, 'import sqlite3;c=sqlite3.connect(project/"data/monitor.sqlite3");c.execute("UPDATE shops SET shop_name=?",("SYNTHETIC CHANGED",));c.commit();c.close();print("true")')
        before = release.db_hashes(self.project)
        with self.assertRaises(release.ReleaseError): release.apply(self.workspace, self.job['job_id'], execute=True)
        self.assertEqual(before, release.db_hashes(self.project))

    def test_snapshot_or_batch_change_blocks_rehearsed_write(self):
        self.prepare()
        before = release.db_hashes(self.project)
        path = self.capture / 'batches/batch_0001.json'
        path.write_bytes(path.read_bytes() + b' ')
        with self.assertRaises(release.ReleaseError): release.apply(self.workspace, self.job['job_id'], execute=True)
        self.assertEqual(before, release.db_hashes(self.project))

    def test_public_source_whitelist_skips_private_neighbors_and_rejects_private_json(self):
        write(self.capture / 'owner.private.json', {'owner_token': 'SYNTHETIC ONLY NEVER REAL'})
        # Remove it from the official project's sources before backup; external
        # capture neighbors are not enumerated by the release public whitelist.
        external = self.root / 'external_capture'
        shutil.copytree(self.capture, external)
        (self.capture / 'owner.private.json').unlink()
        self.snapshot = external / 'snapshot.json'
        prepared = self.prepare()
        self.assertFalse(any('private' in key for key in prepared['input_hashes']))
        with self.assertRaises(release.ReleaseError):
            release.public_json({'nested': [{'Owner_Token': 'SYNTHETIC'}]})

    def test_wrong_job_and_changed_importer_are_blocked(self):
        self.prepare()
        with self.assertRaises(release.ReleaseError): release.apply(self.workspace, 'capture_' + '0'*32, execute=True)
        code = self.project / 'pdd_monitor/store.py'
        code.write_bytes(code.read_bytes() + b'\n# changed after rehearsal\n')
        with self.assertRaises(release.ReleaseError): release.apply(self.workspace, self.job['job_id'], execute=True)

    def test_repeated_prepare_is_readonly_and_workspace_not_inside_project(self):
        self.prepare()
        before = release.db_hashes(self.project)
        self.assertEqual('preflight_passed', self.prepare()['status'])
        self.assertEqual(before, release.db_hashes(self.project))
        with self.assertRaises(release.ReleaseError):
            release.prepare(self.project, self.snapshot, self.sha, self.job['shop_id'], self.project/'unsafe')

    def test_new_image_bundle_keeps_per_card_references_and_reuses_existing_blob(self):
        archive = self.root / 'new_images.zip'
        with zipfile.ZipFile(archive, 'w') as z:
            z.writestr('same_content.png', PNG)
            z.writestr('manifest.json', json.dumps({'items': [
                {'viewOrder': i+1, 'originalImageUrl': f'https://example.invalid/new{i}.png', 'archivePath': 'same_content.png', 'sha256': hashlib.sha256(PNG).hexdigest()}
                for i in range(2)]}))
        before = release.row_baseline(self.project)
        self.prepare(image_archive=archive)
        result = release.apply(self.workspace, self.job['job_id'], execute=True)
        self.assertEqual(2, result['new_run']['image_refs'])
        after = release.row_baseline(self.project)
        self.assertEqual(before['images.assets']['count'], after['images.assets']['count'])
        self.assertEqual(before['images.source_links']['count'] + 2, after['images.source_links']['count'])

    def test_import_committed_before_receipt_can_be_recovered_without_reimport(self):
        self.prepare()
        original = release.worker
        def interrupt_after_write(project, action, payload):
            result = original(project, action, payload)
            if Path(project) == self.project and action == 'import_validate':
                raise release.ReleaseError('SYNTHETIC INTERRUPT AFTER COMMIT BEFORE RECEIPT')
            return result
        with patch.object(release, 'worker', side_effect=interrupt_after_write):
            with self.assertRaises(release.ReleaseError): release.apply(self.workspace, self.job['job_id'], execute=True)
        self.assertTrue((self.workspace / 'commit_intent.json').exists())
        self.assertFalse((self.workspace / 'committed.json').exists())
        before = release.db_hashes(self.project)
        self.assertEqual('finished', release.apply(self.workspace, self.job['job_id'], execute=True)['status'])
        self.assertEqual(before, release.db_hashes(self.project))

    def test_windows_long_workspace_rejected_before_backup_or_workspace_write(self):
        self.workspace = self.root / ('long_workspace_' * 10)
        before = release.db_hashes(self.project)
        original = release.subprocess.run
        def backup_must_not_start(args, **kwargs):
            self.assertNotIn(str(self.project / 'scripts/backup_store.py'), args)
            return original(args, **kwargs)
        budget = release.windows_path_preflight
        with patch.object(release.subprocess, 'run', side_effect=backup_must_not_start), \
                patch.object(release, 'windows_path_preflight', side_effect=lambda *a: budget(*a, windows=True)):
            with self.assertRaisesRegex(release.ReleaseError, 'Windows 路径预算不足.*较短'):
                self.prepare()
        self.assertFalse(self.workspace.exists())
        self.assertEqual(before, release.db_hashes(self.project))

    def test_windows_budget_includes_atomic_temporary_name_and_restore_temp(self):
        before = release.db_hashes(self.project)
        accepted = release.windows_path_preflight(self.project, self.workspace,
                                                  ['snapshot.json', 'batches/batch_0001.json'], windows=True)
        self.assertEqual(accepted['status'], 'passed')
        self.assertTrue(accepted['includes_atomic_copy_names'])
        self.assertTrue(accepted['includes_system_temp_restore'])
        # The final state filename can fit while its atomic-copy sibling cannot.
        recovery = self.workspace / 'attempts' / ('0' * 32) / 'recovery_project'
        length = 245 - len(str(recovery / 'state/competitors')) - 1 - len('.json')
        self.assertGreater(length, 0)
        target = self.project / 'state/competitors' / ('f' * length + '.json')
        write(target, {'synthetic': True})
        final = recovery / target.relative_to(self.project)
        self.assertLessEqual(len(str(final)), 259)
        with self.assertRaisesRegex(release.ReleaseError, 'Windows 路径预算不足'):
            release.windows_path_preflight(self.project, self.workspace, windows=True)
        self.assertEqual(before, release.db_hashes(self.project))


if __name__ == '__main__':
    unittest.main()
