"""Record host-verified share -> storefront provenance and register once.

No browser, HTTP, business database, automatic tracking or pending-target rewrite.
Input is an explicit host evidence JSON, not an instruction to verify the website.
"""
from __future__ import annotations
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import re
import stat
import sys
import tempfile

sys.dont_write_bytecode = True
MAX_BYTES = 4 * 1024 * 1024
PRIVATE_KEYS = {'ownertoken', 'accesstoken', 'refreshtoken', 'apikey', 'password', 'cookies', 'authorization', 'credentials'}


class IntakeError(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise IntakeError(message)


def ordinary(path, directory=False):
    info = path.lstat()
    require(not path.is_symlink() and not getattr(info, 'st_file_attributes', 0) & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0), 'Redirected path refused')
    require(stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode), 'Ordinary file/directory required')


def unique(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, 'Duplicate JSON field')
        result[key] = value
    return result


def public(value):
    if isinstance(value, dict):
        require(not any(re.sub('[^a-z]', '', str(key).lower()) in PRIVATE_KEYS for key in value), 'Private field refused; supply public host evidence only')
        for item in value.values(): public(item)
    elif isinstance(value, list):
        for item in value: public(item)


def decode(raw):
    require(len(raw) <= MAX_BYTES, 'Evidence exceeds size limit')
    value = json.loads(raw.decode('utf-8-sig'), object_pairs_hook=unique,
                       parse_constant=lambda _: (_ for _ in ()).throw(IntakeError('Non-finite JSON')))
    require(isinstance(value, dict), 'Evidence must be an object')
    public(value)
    return value


class SelectedSort(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []
        self.selected = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        classes = attrs.get('class', '').lower().split()
        active = attrs.get('aria-selected', '').lower() == 'true' or any(re.match(r'^(?:current|active)(?:_|$)', token) for token in classes)
        self.stack.append([tag, active, []])

    def handle_data(self, data):
        for frame in self.stack: frame[2].append(data)

    def handle_endtag(self, tag):
        for index in range(len(self.stack)-1, -1, -1):
            if self.stack[index][0] == tag:
                closed = self.stack[index:]
                self.stack = self.stack[:index]
                self.selected.extend(''.join(frame[2]).strip() for frame in closed if frame[1])
                break


def selected_markup(evidence):
    if isinstance(evidence.get('selectedMarkup'), str):
        values = [evidence['selectedMarkup']]
    else:
        elements = evidence.get('sortElements')
        require(isinstance(elements, list), 'Host evidence lacks selected sort markup')
        require(all(isinstance(item, dict) and isinstance(item.get('html'), str) for item in elements), 'Invalid selected sort evidence')
        values = [item['html'] for item in elements]
    parser = SelectedSort()
    for value in values: parser.feed(value)
    parser.close()
    require(parser.selected and set(parser.selected) == {'上新'}, 'Host evidence does not show an unambiguous selected 上新 control')
    return values


@contextmanager
def lock(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a+b') as handle:
        if path.stat().st_size == 0:
            handle.write(b'0'); handle.flush()
        handle.seek(0)
        if os.name == 'nt':
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        try: yield
        finally:
            handle.seek(0)
            if os.name == 'nt': msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else: fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def copy_evidence(raw, path):
    if path.exists():
        ordinary(path)
        require(path.read_bytes() == raw, 'Evidence hash path contains different bytes')
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile('wb', dir=path.parent, delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(raw); handle.flush(); os.fsync(handle.fileno())
        os.link(temporary, path)
    finally:
        if temporary is not None: temporary.unlink(missing_ok=True)


def record_verified_intake(project, evidence_path, original_url, expected_shop_id):
    project = Path(project).expanduser().resolve()
    evidence_path = Path(evidence_path).expanduser().absolute()
    ordinary(project, directory=True); ordinary(evidence_path)
    require((project / 'AGENTS.md').is_file(), 'Use an explicit existing project')
    require(isinstance(expected_shop_id, str) and re.fullmatch(r'shop_[0-9a-f]{24}', expected_shop_id), 'Expected stable shop ID is required')
    require(isinstance(original_url, str), 'Original URL must be explicit text')
    sys.path.insert(0, str(project))
    from pdd_monitor import competitor_registry as registry
    require(Path(registry.__file__).resolve().is_relative_to(project), 'Unexpected registry module; use a fresh project CLI process')
    original = registry._target(None, original_url)
    raw = evidence_path.read_bytes()
    evidence = decode(raw)
    require(evidence.get('userShareUrl') == original['source_url'], 'Original share URL differs from host evidence')
    name, source = evidence.get('shopName'), evidence.get('sourceUrl')
    target = registry._target(name, source)
    require(target['identity_status'] == 'stable_url' and target['shop_id'] == expected_shop_id, 'Resolved URL is not the expected stable storefront')
    if original['identity_status'] == 'stable_url':
        require((original['identity_kind'], original['stable_identifier']) == (target['identity_kind'], target['stable_identifier']), 'A stable original URL cannot silently resolve to another shop')
    header = evidence.get('header')
    require(isinstance(header, str) and isinstance(name, str) and name.strip() and name.strip() in {line.strip() for line in header.splitlines()}, 'Host header does not independently show the exact observed shop name')
    markup = selected_markup(evidence)
    stamp = evidence.get('observedAt')
    require(isinstance(stamp, str), 'Host observation time is required')
    observed = datetime.fromisoformat(stamp.replace('Z', '+00:00'))
    require(observed.tzinfo is not None and observed <= datetime.now(timezone.utc), 'Host observation time must be timezone-aware and not future')
    root = project / 'state/competitors/intakes'
    for candidate in (project/'state', project/'state/competitors', root, root/'evidence', project/'state/competitors/targets'):
        if candidate.exists(): ordinary(candidate, directory=True)
    digest = hashlib.sha256(raw).hexdigest()
    receipt = {'schema_version': 1, 'original_input_url': original['source_url'], 'resolved_storefront_url': target['source_url'],
               'observed_shop_name': name, 'observed_at': stamp, 'identity_kind': target['identity_kind'],
               'stable_identifier': target['stable_identifier'], 'shop_id': target['shop_id'], 'stable_target_id': target['target_id'],
               'verification_method': 'authorized_browser_visible_storefront', 'selected_sort': '上新',
               'host_evidence': {'file': 'evidence/' + digest + '.json', 'sha256': digest, 'bytes': len(raw)},
               'verification_scope': '交叉核对宿主提供的可见店名、店铺URL和上新选中证据；本脚本未重新访问网站',
               'website_collection_performed': False, 'business_databases_written': False,
               'pending_target_rewritten': False, 'tracking_changed': False}
    receipt_digest = hashlib.sha256(json.dumps(receipt, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode('utf-8')).hexdigest()
    destination = root / (receipt_digest + '.json')
    with lock(root / '.intake.lock'):
        existing = []
        for path in sorted((project / 'state/competitors/targets').glob('*.json')):
            ordinary(path)
            row = registry._read_target(path)
            if row['shop_id'] == target['shop_id']:
                require((row['identity_kind'], row['stable_identifier']) == (target['identity_kind'], target['stable_identifier']), 'Existing typed identity conflicts with resolved storefront')
            existing.append(row)
        for path in sorted(root.glob('*.json')):
            ordinary(path)
            old = decode(path.read_bytes())
            require(hashlib.sha256(json.dumps(old, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode('utf-8')).hexdigest() == path.stem, 'Existing intake content hash does not match filename')
            if old.get('original_input_url') == receipt['original_input_url']:
                require((old.get('identity_kind'), old.get('stable_identifier')) == (target['identity_kind'], target['stable_identifier']), 'Original share has a conflicting verified resolution; review explicitly')
        if destination.exists():
            require(decode(destination.read_bytes()) == receipt, 'Existing intake conflicts with its content hash')
        require(evidence_path.read_bytes() == raw, 'Host evidence changed during validation')
        # Existing short targets remain unresolved; no fabricated resolution UI.
        pending_ids = [row['target_id'] for row in existing if row['source_url'] == original['source_url'] and row['identity_status'] != 'stable_url']
        registered = registry.register_target(project, name, source)
        copy_evidence(raw, root / receipt['host_evidence']['file'])
        created = not destination.exists()
        if created: registry.write_new_json(destination, receipt)
        return {'status': 'recorded' if created else 'unchanged', 'receipt_path': str(destination), 'receipt_sha256': receipt_digest,
                'target_id': target['target_id'], 'shop_id': target['shop_id'], 'registration_status': registered['status'],
                'existing_pending_target_ids': pending_ids,
                'pending_note': '已有分享目标仍保持待核验；本次只保留核实证据并登记稳定目标，未修改派生UI' if pending_ids else None,
                'website_collection_performed': False, 'business_databases_written': False, 'tracking_changed': False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', type=Path, required=True)
    parser.add_argument('--evidence', type=Path, required=True)
    parser.add_argument('--original-url', required=True)
    parser.add_argument('--expected-shop-id', required=True)
    args = parser.parse_args(argv)
    try:
        result = record_verified_intake(args.project, args.evidence, args.original_url, args.expected_shop_id)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except Exception as error:
        print(json.dumps({'ok': False, 'error_type': type(error).__name__, 'message': str(error) if isinstance(error, IntakeError) else '登记来源校验失败；未执行网页采集'}, ensure_ascii=False))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
