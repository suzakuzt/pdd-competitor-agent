import importlib.util
from contextlib import closing
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace
import tempfile
import unittest

MODULE = Path(__file__).resolve().parents[1]/'scripts/open_saved_project.py'
spec = importlib.util.spec_from_file_location('portable_launcher', MODULE)
launch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launch)


class LauncherTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='pdd_portable_')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def fixture(self):
        for name in ('AGENTS.md','TASKS.md','schema.sql','pdd_monitor/__main__.py'):
            path = self.root/name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('synthetic', encoding='utf-8')
        for folder in ('data','sources','state','reports','dashboard/src','dashboard/dist'):
            (self.root/folder).mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.root/'data/monitor.sqlite3')) as con:
            con.execute('CREATE TABLE observations(id INTEGER)')
            con.execute('INSERT INTO observations VALUES (1)')
            con.commit()
        with closing(sqlite3.connect(self.root/'data/images.sqlite3')) as con:
            con.execute('CREATE TABLE assets(id INTEGER)')
            con.commit()
        snapshot = {'id':'synthetic-only', 'queries':{'observations':{'rows':[{'id':1}]}}}
        content = json.dumps(snapshot).encode()
        (self.root/'dashboard/src/data.json').write_bytes(content)
        (self.root/'dashboard/dist/snapshot.json').write_bytes(content)
        (self.root/'dashboard/dist/index.html').write_text('synthetic-only', encoding='utf-8')
        build = {'kind':'separate-data-v1'}
        for key, filename in [('html','index.html'),('snapshot','snapshot.json')]:
            path = self.root/'dashboard/dist'/filename
            build[key] = {'path':filename,'sha256':launch.digest(path),'bytes':path.stat().st_size}
        build['sourceSnapshotSha256'] = build['snapshot']['sha256']
        (self.root/'dashboard/dist/data-app-build.json').write_text(json.dumps(build), encoding='utf-8')

    def valid_runner(self, command, **kwargs):
        self.assertEqual(command[-3:], ['--data-dir','data','validate'])
        self.assertEqual(kwargs['cwd'], self.root)
        self.assertEqual(kwargs['env']['PDD_NODE'], str(self.root/'runtime/node/bin/node.exe'))
        self.assertEqual(kwargs['env']['PDD_DATA_PLUGIN'], str(self.root/'runtime/data-analytics/1.0.11'))
        self.assertNotIn('PYTHONPATH', kwargs['env'])
        return SimpleNamespace(returncode=0, stdout='{"ok":true}')

    def test_missing_project_does_not_create_database(self):
        with self.assertRaises(ValueError):
            launch.inspect_project(self.root, runner=self.valid_runner)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_one_missing_paired_database_is_rejected(self):
        self.fixture()
        (self.root/'data/images.sqlite3').unlink()
        with self.assertRaises(ValueError):
            launch.inspect_project(self.root, runner=self.valid_runner)
        self.assertFalse((self.root/'data/images.sqlite3').exists())

    def test_empty_observation_store_is_rejected(self):
        self.fixture()
        with closing(sqlite3.connect(self.root/'data/monitor.sqlite3')) as con:
            con.execute('DELETE FROM observations')
            con.commit()
        with self.assertRaisesRegex(ValueError,'empty database'):
            launch.inspect_project(self.root, runner=self.valid_runner)

    def test_validation_failure_prevents_open(self):
        self.fixture()
        with self.assertRaisesRegex(ValueError,'did not pass'):
            launch.inspect_project(self.root, runner=lambda *args,**kwargs:SimpleNamespace(returncode=0,stdout='{"ok":false}'))

    def test_corrupt_published_snapshot_is_rejected(self):
        self.fixture()
        (self.root/'dashboard/dist/snapshot.json').write_text('changed', encoding='utf-8')
        with self.assertRaisesRegex(ValueError,'checksum mismatch'):
            launch.inspect_project(self.root, runner=self.valid_runner)

    def test_existing_complete_project_validation_is_readonly(self):
        self.fixture()
        before = {str(p):launch.digest(p) for p in self.root.rglob('*') if p.is_file()}
        result = launch.inspect_project(self.root, runner=self.valid_runner)
        self.assertEqual(result['observation_count'],1)
        self.assertFalse(result['database_files_created'])
        self.assertEqual(before,{str(p):launch.digest(p) for p in self.root.rglob('*') if p.is_file()})


    def test_refresh_accepts_valid_new_store_but_open_rejects_stale_rows(self):
        self.fixture()
        with closing(sqlite3.connect(self.root/'data/monitor.sqlite3')) as con:
            con.execute('INSERT INTO observations VALUES (2)');con.commit()
        with self.assertRaises(ValueError):launch.inspect_project(self.root, runner=lambda *args,**kwargs: SimpleNamespace(returncode=0,stdout=json.dumps({'ok':True}),stderr=''))
        checked=launch.inspect_project(self.root,runner=lambda *args,**kwargs: SimpleNamespace(returncode=0,stdout=json.dumps({'ok':True}),stderr=''),allow_stale_snapshot=True)
        self.assertEqual(checked['observation_count'],2)
        self.assertFalse(checked['snapshot_synchronized'])

    def test_refresh_precheck_does_not_claim_changed_source_is_published(self):
        self.fixture()
        path=self.root/'dashboard/src/data.json'
        payload=json.loads(path.read_text(encoding='utf-8'));payload['changed_source']=True
        path.write_text(json.dumps(payload),encoding='utf-8')
        with self.assertRaises(ValueError):launch.inspect_project(self.root,runner=lambda *args,**kwargs: SimpleNamespace(returncode=0,stdout=json.dumps({'ok':True}),stderr=''))
        checked=launch.inspect_project(self.root,runner=lambda *args,**kwargs: SimpleNamespace(returncode=0,stdout=json.dumps({'ok':True}),stderr=''),allow_stale_snapshot=True)
        self.assertFalse(checked['snapshot_synchronized'])

if __name__ == '__main__':
    unittest.main(verbosity=2)
