"""Install the pinned, self-contained official Chrome connector locally."""
import base64
import hashlib
import io
import json
from pathlib import Path
import tarfile
import urllib.request

VERSION = '1.10.1'
INTEGRITY = 'Klw6HWDqHC/XS1JwZldd2r49aUhbUJN9m9Mvcx4SEueIPXtzuQX+QelxAViobv8YUkDZ7HWDrmViR6LeYK0wAw=='
URL = f'https://registry.npmjs.org/chrome-devtools-mcp/-/chrome-devtools-mcp-{VERSION}.tgz'


def install(project):
    destination = project / 'runtime/chrome-devtools-mcp/node_modules/chrome-devtools-mcp'
    manifest = destination / 'package.json'
    if manifest.exists():
        if json.loads(manifest.read_text(encoding='utf-8'))['version'] != VERSION:
            raise ValueError('Unexpected connector version; refusing overwrite')
        return {'version': VERSION, 'installed': False}
    with urllib.request.urlopen(URL, timeout=60) as response:
        archive = response.read(32 * 1024 * 1024)
    if base64.b64encode(hashlib.sha512(archive).digest()).decode() != INTEGRITY:
        raise ValueError('Official package integrity mismatch')
    with tarfile.open(fileobj=io.BytesIO(archive), mode='r:gz') as source:
        members = source.getmembers()
        for member in members:
            parts = Path(member.name).parts
            if (not parts or parts[0] != 'package' or '..' in parts
                    or Path(member.name).is_absolute() or not (member.isfile() or member.isdir())):
                raise ValueError('Unsafe package archive')
        for member in members:
            relative = Path(*Path(member.name).parts[1:])
            target = destination / relative
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with source.extractfile(member) as stream:
                    target.write_bytes(stream.read())
    return {'version': VERSION, 'installed': True, 'integrity': 'sha512-' + INTEGRITY}


if __name__ == '__main__':
    print(json.dumps(install(Path(__file__).resolve().parent.parent)))
