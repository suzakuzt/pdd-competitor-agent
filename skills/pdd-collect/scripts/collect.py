"""Run the maintained project entry point; collection logic stays in the project."""
import os
import subprocess
import sys
from pathlib import Path


def main():
    project = Path(os.environ['PDD_COLLECT_PROJECT']).resolve() if os.environ.get('PDD_COLLECT_PROJECT') else Path(__file__).resolve().parents[3]
    entry = project / 'scripts/collection_skill.py'
    python = project / 'runtime/python/python.exe'
    if not entry.is_file() or not python.is_file():
        print('{"status":"unavailable","reason":"project_unavailable","message":"未找到原项目或其运行环境，未启动采集。"}')
        return 2
    result = subprocess.run([str(python), '-B', '-X', 'utf8', str(entry), '--project', str(project), *sys.argv[1:]],
        cwd=project, capture_output=True, text=True, encoding='utf-8',
        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    print(result.stdout, end='')
    if result.stderr:
        print(result.stderr, end='', file=sys.stderr)
    return result.returncode


if __name__ == '__main__':
    sys.exit(main())
