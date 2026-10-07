"""Incremental protocol tests use only synthetic temporary stores."""
import json
import hashlib
import io
import os
from pathlib import Path
import shutil
import tempfile
import tarfile
import time
import unittest
from unittest.mock import patch

from scripts import backup_store, restic_backup, release_snapshot
from tests import test_backup_store as fixtures


class FakeRepository:
    snapshots = {}
    mutate_source = False
    tamper_restore = False

    def __init__(self, project, *, create=False):
        self.project = Path(project)
        self.state = self.project / 'runtime/restic/.state'
        self.state.mkdir(parents=True, exist_ok=True)

    def initialize(self):
        return 'b' * 64

    def repository_id(self):
        return 'b' * 64

    def archive(self):
        files = backup_store._inventory(self.project)
        sid = f'{len(self.snapshots) + 1:064x}'
        self.snapshots[sid] = {name: (self.project / name).read_bytes() for name in files}
        if self.mutate_source:
            (self.project / 'sources/unexpected.json').write_text('{}', encoding='utf-8')
        return {'snapshot_id': sid}

    def verify_paths(self, snapshot_id, files):
        restic_backup.require(set(self.snapshots[snapshot_id]) == set(files), 'Snapshot inventory differs')
        return {'snapshot_id': snapshot_id, 'tree_id': 'c' * 64, 'file_count': len(files),
                'total_bytes': sum(row['bytes'] for row in files.values())}

    def restore(self, snapshot_id, destination, *, paired_only):
        for name, data in self.snapshots[snapshot_id].items():
            if paired_only and name not in restic_backup.MATERIALIZED:
                continue
            target = destination / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        if self.tamper_restore:
            (destination / 'schema.sql').write_text('tampered', encoding='utf-8')

    def run(self, *arguments):
        assert arguments in (('check', '--read-data'), ('check',))
        return {}

    def verify_content(self, snapshot_id, files):
        return {'all_files_sha256_match': True, 'file_count': len(files),
                'inventory_sha256': restic_backup.inventory_digest(files)}


class IncrementalBackupTests(unittest.TestCase):
    def setUp(self):
        fixtures.BackupAcceptance.setUp(self)
        FakeRepository.snapshots = {}
        FakeRepository.mutate_source = FakeRepository.tamper_restore = False
        self.engine = patch.object(restic_backup, 'LocalRepository', FakeRepository)
        self.engine.start()
        self.addCleanup(self.engine.stop)

    def make_backup(self, **kwargs):
        return backup_store.create_backup(self.project, self.output, incremental=True, **kwargs)

    def test_v2_archive_has_all_sources_but_only_independent_pair_is_materialized(self):
        before = backup_store._inventory(self.project)
        retained = self.root / 'retained'
        manifest = self.make_backup(retain_restore=retained)
        self.assertEqual(manifest['format_version'], 2)
        self.assertEqual(manifest['files'], before)
        self.assertEqual(manifest['source_before'], manifest['source_after'])
        self.assertEqual(set(manifest['materialized_files']), set(restic_backup.MATERIALIZED))
        self.assertFalse((self.output / 'sources').exists())
        self.assertFalse((retained / 'sources').exists())
        self.assertTrue(manifest['full_restore_drill']['files_sha256_match'])
        self.assertTrue(manifest['full_restore_drill']['repository_read_data_ok'])
        self.assertEqual(release_snapshot.verify_backup(self.output), manifest)
        self.assertTrue(release_snapshot.verify_retained_restore(retained, self.output, manifest)['ok'])
        self.assertEqual(before, backup_store._inventory(self.project))
        self.assertFalse((self.output / 'INCOMPLETE.json').exists())

    def test_warm_backup_keeps_full_inventory_and_periodic_restore_is_due(self):
        first = self.make_backup()
        self.output = self.root / 'second'
        second = self.make_backup()
        self.assertIsNone(second['full_restore_drill'])
        self.assertEqual(second['files'], first['files'])
        marker = self.project / 'runtime/restic/.state/restore-drill.json'
        state = json.loads(marker.read_text(encoding='utf-8'))
        state['verified_at_epoch'] = 0
        marker.write_text(json.dumps(state), encoding='utf-8')
        self.output = self.root / 'third'
        self.assertTrue(self.make_backup()['full_restore_drill']['validation_ok'])

    def test_source_change_during_archive_fails_without_success_marker(self):
        FakeRepository.mutate_source = True
        with self.assertRaisesRegex(backup_store.BackupError, 'Source changed|Full restored'):
            self.make_backup()
        self.assertTrue((self.output / 'INCOMPLETE.json').exists())
        self.assertFalse((self.output / 'manifest.json').exists())

    def test_corrupt_restored_pair_fails_without_success_marker(self):
        FakeRepository.tamper_restore = True
        with self.assertRaisesRegex(backup_store.BackupError, 'hash mismatch'):
            self.make_backup()
        self.assertFalse((self.output / 'manifest.json').exists())

    def test_consumer_rejects_archive_identity_and_materialized_tampering(self):
        manifest = self.make_backup()
        manifest['archive']['repository_id'] = 'd' * 64
        (self.output / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
        with self.assertRaisesRegex(restic_backup.ResticBackupError, 'identity'):
            release_snapshot.verify_backup(self.output)
        manifest['archive']['repository_id'] = 'b' * 64
        (self.output / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
        (self.output / 'schema.sql').write_text('bad', encoding='utf-8')
        with self.assertRaises(release_snapshot.ReleaseError):
            release_snapshot.verify_backup(self.output)

    def test_consumer_rejects_incomplete_materialized_declaration(self):
        manifest = self.make_backup()
        del manifest['materialized_files']['schema.sql']
        (self.output / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
        with self.assertRaisesRegex(release_snapshot.ReleaseError, 'materialized'):
            release_snapshot.verify_backup(self.output)

    def test_opt_in_required_and_v1_default_remains_full(self):
        result = backup_store.create_backup(self.project, self.output)
        self.assertEqual(result['format_version'], 1)
        self.assertTrue((self.output / 'sources/synthetic.json').exists())
        self.assertEqual(release_snapshot.verify_backup(self.output), result)

    def test_invalid_or_unbound_periodic_marker_forces_another_full_drill(self):
        self.make_backup()
        state = self.project / 'runtime/restic/.state'
        marker = state / 'restore-drill.json'
        value = json.loads(marker.read_text(encoding='utf-8'))
        self.assertTrue(restic_backup.load_drill(state, 'b' * 64))
        for changed in ({'verified_at_epoch': float('nan')}, {'verified_at_epoch': time.time() + 86400},
                        {'completed_since_full': -1}, {'completed_since_full': 0.5},
                        {'receipt_sha256': 'f' * 64}, {'snapshot_id': '../bad'}):
            with self.subTest(changed=changed):
                marker.write_text(json.dumps({**value, **changed}), encoding='utf-8')
                self.assertEqual(restic_backup.load_drill(state, 'b' * 64), {})


class ResticProtocolTests(unittest.TestCase):
    def test_streaming_archive_verification_rejects_same_size_wrong_content_and_missing_pack(self):
        repo = object.__new__(restic_backup.LocalRepository)
        repo.project = Path('.')
        payloads = {name: b'abc' for name in restic_backup.MATERIALIZED}
        payloads['sources/source.json'] = b'AAA'
        files = {name: {'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()} for name, data in payloads.items()}
        def process_for(payloads, code=0):
            binary = io.BytesIO()
            with tarfile.open(fileobj=binary, mode='w') as archive:
                for name, data in payloads.items():
                    member = tarfile.TarInfo(name)
                    member.size = len(data)
                    archive.addfile(member, io.BytesIO(data))
            class Process:
                stdout = io.BytesIO(binary.getvalue())
                def wait(self, **kwargs): return code
                def poll(self): return code
                def kill(self): pass
            return Process()
        with patch.object(repo, 'command', return_value=(['synthetic-restic'], {})):
            with patch.object(restic_backup.subprocess, 'Popen', return_value=process_for(payloads)):
                self.assertTrue(repo.verify_content('a' * 64, files)['all_files_sha256_match'])
            corrupt = {**payloads, 'sources/source.json': b'BBB'}
            with patch.object(restic_backup.subprocess, 'Popen', return_value=process_for(corrupt)):
                with self.assertRaisesRegex(restic_backup.ResticBackupError, 'SHA256'):
                    repo.verify_content('a' * 64, files)
            with patch.object(restic_backup.subprocess, 'Popen', return_value=process_for(payloads, 1)):
                with self.assertRaisesRegex(restic_backup.ResticBackupError, 'stream failed'):
                    repo.verify_content('a' * 64, files)

    def test_ls_requires_exact_snapshot_all_paths_sizes_and_no_special_files(self):
        repo = object.__new__(restic_backup.LocalRepository)
        files = {name: {'bytes': 3, 'sha256': 'e' * 64} for name in restic_backup.MATERIALIZED}
        files['sources/example.json'] = {'bytes': 4, 'sha256': 'f' * 64}
        nodes = [{'struct_type': 'snapshot', 'id': 'a' * 64, 'tree': 'c' * 64}]
        nodes += [{'struct_type': 'node', 'type': 'file', 'path': '/' + name, 'size': value['bytes']}
                  for name, value in files.items()]
        with patch.object(repo, 'run', return_value=nodes):
            self.assertEqual(repo.verify_paths('a' * 64, files)['file_count'], 4)
        for changed in (nodes[:-1], nodes + [nodes[-1]],
                        [{**nodes[0], 'id': 'b' * 64}, *nodes[1:]],
                        [*nodes, {'struct_type': 'node', 'type': 'symlink', 'path': '/sources/link'}]):
            with self.subTest(changed=changed), patch.object(repo, 'run', return_value=changed):
                with self.assertRaises(restic_backup.ResticBackupError):
                    repo.verify_paths('a' * 64, files)

    def test_archive_forces_content_read_and_uses_exact_summary_id(self):
        repo = object.__new__(restic_backup.LocalRepository)
        with patch.object(repo, 'run', return_value=[{'message_type': 'summary', 'snapshot_id': 'a' * 64}]) as run:
            self.assertEqual(repo.archive()['snapshot_id'], 'a' * 64)
        self.assertIn('--force', run.call_args.args)
        self.assertNotIn('data', run.call_args.args)
        self.assertIn('data/monitor.sqlite3', run.call_args.args)

    def test_wrong_binary_checksum_refused_before_any_execution(self):
        with tempfile.TemporaryDirectory() as folder:
            project = Path(folder)
            binary = project / 'runtime/restic/0.19.1/restic.exe'
            binary.parent.mkdir(parents=True)
            binary.write_bytes(b'synthetic wrong binary')
            with patch.object(restic_backup.subprocess, 'run') as run:
                with self.assertRaisesRegex(restic_backup.ResticBackupError, 'checksum'):
                    restic_backup.LocalRepository(project, create=True)
                run.assert_not_called()


@unittest.skipUnless(os.name == 'nt' and os.environ.get('PDD_RUN_RESTIC_INTEGRATION') == '1',
                     'Explicit isolated Windows restic integration run required')
class RealResticReleaseIntegration(unittest.TestCase):
    def test_real_v2_prepare_restores_pair_and_rehearses_without_old_source_files(self):
        from tests import test_release_snapshot as release_fixtures
        fixture = release_fixtures.ReleaseTests('test_complete_end_to_end_preserves_old_images_and_fixed_state')
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        project = fixture.project
        shutil.copyfile(fixtures.SOURCE_PROJECT / 'scripts/restic_backup.py', project / 'scripts/restic_backup.py')
        runtime = project / 'runtime/restic/0.19.1'
        runtime.mkdir(parents=True)
        shutil.copyfile(fixtures.SOURCE_PROJECT / 'runtime/restic/0.19.1/restic.exe', runtime / 'restic.exe')
        before = backup_store._inventory(project)
        original_run = release_fixtures.release.subprocess.run
        def diagnosed_run(*args, **kwargs):
            result = original_run(*args, **kwargs)
            if result.returncode and any('backup_store.py' in str(value) for value in args[0]):
                raise AssertionError('Isolated backup failed: ' + result.stdout + result.stderr)
            return result
        with patch.object(release_fixtures.release.subprocess, 'run', side_effect=diagnosed_run):
            ready = fixture.prepare(incremental_backup=True)
        self.assertEqual(ready['status'], 'prepared')
        attempt = fixture.workspace / ready['attempt']
        manifest = json.loads((attempt / 'paired_backup/manifest.json').read_text(encoding='utf-8'))
        self.assertEqual(manifest['format_version'], 2)
        self.assertTrue(manifest['full_restore_drill']['files_sha256_match'])
        self.assertFalse((attempt / 'recovery_project/sources/old901.json').exists())
        self.assertEqual(release_fixtures.release.check(fixture.workspace)['status'], 'preflight_passed')
        self.assertEqual(before, backup_store._inventory(project))
        warm = backup_store.create_backup(project, fixture.root / 'warm_backup', incremental=True)
        self.assertIsNone(warm['full_restore_drill'])
        self.assertTrue(warm['archive']['content_verification']['all_files_sha256_match'])


if __name__ == '__main__':
    unittest.main()
