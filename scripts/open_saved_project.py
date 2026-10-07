"""Open an existing complete project using its original validated CLI.

No installation, database initialization, login, credential access or network
fetch occurs in this wrapper. --check-only is strictly read-only.
"""
from __future__ import annotations
import argparse
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys


def digest(path):
    result = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(block)
    return result.hexdigest()


def require_file(path):
    if not path.is_file() or path.is_symlink() or not path.stat().st_size:
        raise ValueError('Required original project file is missing, empty or linked: '+str(path.name))


def environment(root):
    env = {key: value for key, value in os.environ.items()
           if key.upper() not in {'PYTHONHOME','PYTHONPATH','PYTHONSTARTUP','NODE_OPTIONS','NODE_PATH'}}
    env['PYTHONUTF8'] = '1'
    env['PYTHONDONTWRITEBYTECODE'] = '1'
    # Process-local choices: no global PATH/configuration changes.
    env['PDD_PYTHON'] = sys.executable
    env['PDD_NODE'] = str(root/'runtime/node/bin/node.exe')
    env['PDD_DATA_PLUGIN'] = str(root/'runtime/data-analytics/1.0.11')
    return env


def cli(root, arguments, *, runner=subprocess.run):
    command = [sys.executable, '-B', '-X', 'utf8', '-m', 'pdd_monitor', '--data-dir', 'data', *arguments]
    completed = runner(command, cwd=root, env=environment(root), text=True,
                       encoding='utf-8', errors='replace', capture_output=True,
                       timeout=600, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if completed.returncode:
        # Commands here are local validate/build/open only; never authenticate.
        raise ValueError('Project command failed: '+arguments[0]+'. Review reports/dashboard when present.')
    try:
        result = json.loads(completed.stdout)
    except (ValueError, TypeError):
        raise ValueError('Project command returned invalid JSON: '+arguments[0]) from None
    return result


def inspect_project(root, *, runner=subprocess.run, allow_stale_snapshot=False):
    required = ['AGENTS.md','TASKS.md','schema.sql','pdd_monitor/__main__.py',
                'data/monitor.sqlite3','data/images.sqlite3','dashboard/src/data.json',
                'dashboard/dist/index.html','dashboard/dist/data-app-build.json']
    for name in required:
        require_file(root/name)
    for folder in ('sources','state','reports'):
        if not (root/folder).is_dir():
            raise ValueError('Incomplete project directory: '+folder)
    databases = [root/'data/monitor.sqlite3', root/'data/images.sqlite3']
    before = [digest(path) for path in databases]
    with closing(sqlite3.connect(databases[0].as_uri()+'?mode=ro', uri=True)) as connection:
        connection.execute('PRAGMA query_only=ON')
        cards = connection.execute('SELECT COUNT(*) FROM observations').fetchone()[0]
        if cards <= 0:
            raise ValueError('An empty database cannot replace the delivered original observations')
    validation = cli(root, ['validate'], runner=runner)
    if validation.get('ok') is not True:
        raise ValueError('The original paired databases did not pass project validation')
    build = json.loads((root/'dashboard/dist/data-app-build.json').read_text(encoding='utf-8'))
    if build.get('kind') != 'separate-data-v1':
        raise ValueError('Expected the delivered separate-data build receipt')
    for key in ('html','snapshot'):
        entry = build[key]
        name = entry['path']
        if not isinstance(name, str) or Path(name).name != name or '/' in name or '\\' in name:
            raise ValueError('Invalid dashboard build filename')
        path = root/'dashboard/dist'/name
        require_file(path)
        if path.stat().st_size != entry['bytes'] or digest(path) != entry['sha256']:
            raise ValueError('Dashboard build checksum mismatch: '+key)
    source_matches = digest(root/'dashboard/src/data.json') == build['sourceSnapshotSha256']
    if not source_matches and not allow_stale_snapshot:
        raise ValueError('Dashboard source and published snapshot differ; finish a verified build first')
    snapshot = json.loads((root/'dashboard/src/data.json').read_text(encoding='utf-8'))
    rows_match = len(snapshot['queries']['observations']['rows']) == cards
    if not rows_match and not allow_stale_snapshot:
        raise ValueError('Published snapshot has a different observation count from the original database')
    if [digest(path) for path in databases] != before:
        raise ValueError('Business databases changed during validation; wait for current work to finish')
    return {'status':'checked', 'snapshot_synchronized':source_matches and rows_match, 'observation_count':cards, 'database_sha256':dict(zip(['monitor.sqlite3','images.sqlite3'],before)),
            'app_id':snapshot.get('id'), 'snapshot_sha256':build['sourceSnapshotSha256'],
            'website_collection_performed':False, 'database_files_created':False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check-only', action='store_true')
    parser.add_argument('--refresh', action='store_true')
    parser.add_argument('--no-browser', action='store_true')
    parser.add_argument('--port', type=int, default=8878)
    args = parser.parse_args(argv)
    if not 1024 <= args.port <= 65535:
        parser.error('Port must be between 1024 and 65535')
    if args.check_only and args.refresh:
        parser.error('--check-only cannot rebuild files')
    root = Path(__file__).resolve().parents[1]
    try:
        result = inspect_project(root, allow_stale_snapshot=args.refresh)
        if args.refresh:
            for name in ('runtime/node/bin/node.exe','runtime/data-analytics/1.0.11/scripts/data-app.mjs'):
                require_file(root/name)
            cli(root, ['dashboard-build'])
            result = inspect_project(root)
        if not args.check_only:
            arguments = ['dashboard-open','--port',str(args.port)]
            if args.no_browser:
                arguments.append('--no-browser')
            result['dashboard'] = cli(root, arguments)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (OSError, ValueError, KeyError, sqlite3.Error, subprocess.SubprocessError) as error:
        print(json.dumps({'status':'failed','message':str(error), 'database_files_created':False}, ensure_ascii=False))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
