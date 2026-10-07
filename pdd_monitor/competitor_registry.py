"""Explicit local target registration. No website requests or business DB writes."""
import hashlib
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit, parse_qs, unquote
from .store import shop_identity_evidence
from .shop_tracking import load_tracking, save_tracking, target_tracking_fields

HOSTS = {'mobile.yangkeduo.com', 'mobile.pinduoduo.com', 'yangkeduo.com',
         'www.yangkeduo.com', 'pinduoduo.com', 'www.pinduoduo.com', 'p.pinduoduo.com'}
STOREFRONT_PATHS = {'/mall_page.html'}
CONTROLS = re.compile(r'[\x00-\x1f\x7f-\x9f]')


def write_new_json(path, value):
    """Commit an entire new JSON via a hard link; never replace an existing file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = None
    try:
        with tempfile.NamedTemporaryFile('w', encoding='utf-8', newline='\n', dir=path.parent,
                                         suffix='.tmp', delete=False) as stream:
            temp = Path(stream.name)
            json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write('\n'); stream.flush(); os.fsync(stream.fileno())
        os.link(temp, path)
    finally:
        if temp is not None:
            temp.unlink(missing_ok=True)


def _target(name, source_url):
    if (name is not None and not isinstance(name, str)) or (source_url is not None and not isinstance(source_url, str)):
        raise ValueError('店铺名称和链接必须是文字或空值')
    name, source_url = name or '', source_url or ''
    if CONTROLS.search(name) or CONTROLS.search(source_url) or CONTROLS.search(unquote(source_url)):
        raise ValueError('店铺名称或链接不能包含控制字符')
    name, source_url = name.strip(), source_url.strip()
    if not name and not source_url:
        raise ValueError('Provide a shop name or a Pinduoduo shop link')
    if len(name) > 200 or len(source_url) > 8192:
        raise ValueError('Target name or link is too long')
    identity, kind = None, 'name_only'
    if source_url:
        parsed = urlsplit(source_url)
        if parsed.scheme not in ('https', 'http') or parsed.hostname not in HOSTS or parsed.username or parsed.password or parsed.port not in (None, 80, 443):
            raise ValueError('Use a Pinduoduo shop/share link with no credentials')
        query = parse_qs(parsed.query, keep_blank_values=True)
        if 'goods_id' in {key.lower() for key in query} or 'goods' in unquote(parsed.path).lower():
            raise ValueError('This is a product link; provide the shop link')
        # A share route may contain unverified/tracking parameters named mall_id.
        # Keep it unresolved; only the known storefront route provides an ID clue.
        storefront = parsed.hostname != 'p.pinduoduo.com' and parsed.path in STOREFRONT_PATHS
        if storefront and any(key in query for key in ('mall_id', 'mall_sn')):
            identity = shop_identity_evidence({'sourceUrl': source_url})
            key = identity['identity_kind'] + ':' + identity['stable_identifier']
            kind = 'stable_url'
        else:
            key = 'unresolved_url:' + source_url
            kind = 'unresolved_share_link'
    else:
        key = 'unresolved_name:' + name
    target_id = 'target_' + hashlib.sha256(key.encode()).hexdigest()[:24]
    return {'target_id': target_id, 'platform': 'pdd', 'display_name': name or '待核实店名',
            'source_url': source_url or None, 'shop_id': identity['shop_id'] if identity else None,
            'identity_status': kind, 'identity_kind': identity['identity_kind'] if identity else None,
            'stable_identifier': identity['stable_identifier'] if identity else None,
            'status': 'pending_capture' if identity else 'needs_identity'}


def register_target(project, name=None, source_url=None):
    row = _target(name, source_url)
    path = Path(project) / 'state/competitors/targets' / (row['target_id'] + '.json')
    if row['shop_id']:
        for candidate in path.parent.glob('*.json'):
            existing = _read_target(candidate)
            if (existing['shop_id'] == row['shop_id'] and
                    (existing['identity_kind'], existing['stable_identifier']) !=
                    (row['identity_kind'], row['stable_identifier'])):
                raise ValueError('Registered shop has conflicting typed storefront identities')
    row['registered_at'] = datetime.now(timezone.utc).isoformat(timespec='seconds').replace('+00:00','Z')
    if path.exists():
        existing = _read_target(path)
        if (existing['identity_kind'], existing['stable_identifier'], existing['shop_id']) != (row['identity_kind'],row['stable_identifier'],row['shop_id']):
            raise ValueError('Existing target identity conflicts')
        return {'status': 'unchanged', 'target': existing, 'path': str(path), 'website_collection_performed': False}
    try:
        write_new_json(path, row)
    except FileExistsError:
        return register_target(project, name, source_url)
    return {'status': 'registered', 'target': row, 'path': str(path), 'website_collection_performed': False}


def _read_target(path):
    row = json.loads(path.read_text(encoding='utf-8-sig'))
    if not isinstance(row, dict) or not isinstance(row.get('registered_at'), str):
        raise ValueError('Invalid target registration')
    expected = _target(row.get('display_name'), row.get('source_url'))
    for key in ('target_id','platform','source_url','shop_id','identity_status','identity_kind','stable_identifier','status'):
        if row.get(key) != expected[key]:
            raise ValueError('Registered target does not match its original identity')
    if path.name != row['target_id'] + '.json':
        raise ValueError('Target filename and identity differ')
    stamp = datetime.fromisoformat(row['registered_at'].replace('Z','+00:00'))
    if stamp.tzinfo is None:
        raise ValueError('Registered time requires an offset')
    return row


def _verified_share_projections(project, rows):
    """Resolve UI rows from immutable intake evidence, never rewrite a target.

    Import the existing strict consumer at call time to avoid the registry /
    capture-control module cycle. All public intake files it can read are part
    of this export's fingerprint and are checked again after validation.
    """
    pending = {row['source_url'] for row in rows
               if row['identity_status'] == 'unresolved_share_link' and row['source_url']}
    if not pending:
        return {}, {}
    from .capture_control import _verified_intake_name, _no_redirect
    from scripts.record_verified_intake import decode, ordinary

    project = Path(project).resolve()
    root = project / 'state/competitors/intakes'
    paths = sorted(root.glob('*.json')) + sorted((root / 'evidence').glob('*.json'))
    if not paths:
        return {}, {}
    raw_files, receipts, projections = {}, [], {}
    invalid = {'identity_conflict': True, 'verification_status': 'needs_review'}
    stable = {row['target_id']: row for row in rows if row['identity_status'] == 'stable_url'}
    try:
        for path in paths:
            _no_redirect(project, path)
            ordinary(path)
            raw_files[path] = path.read_bytes()
            if path.parent == root:
                receipts.append((path, decode(raw_files[path])))
        names = {}
        for original in pending:
            candidates = [(path, receipt) for path, receipt in receipts
                          if receipt.get('original_input_url') == original]
            if not candidates:
                continue
            try:
                identities, verified = set(), []
                for path, receipt in candidates:
                    target = stable.get(receipt.get('stable_target_id'))
                    if target is None or target['shop_id'] != receipt.get('shop_id'):
                        raise ValueError('Intake stable registration missing or different')
                    if target['target_id'] not in names:
                        names[target['target_id']] = _verified_intake_name(project, target)
                    name = names[target['target_id']]
                    if not name:
                        raise ValueError('Intake has no verified visible name')
                    identities.add((target['identity_kind'], target['stable_identifier']))
                    verified.append((datetime.fromisoformat(receipt['observed_at'].replace('Z', '+00:00')),
                                     path, receipt, target, name))
                if len(identities) != 1:
                    raise ValueError('Original share has conflicting typed resolutions')
                _, _, latest, target, name = max(verified, key=lambda item: (item[0], str(item[1])))
                latest_time = max(item[0] for item in verified)
                matched_names = {receipt['observed_shop_name'].strip()
                                 for when, _, receipt, _, _ in verified if when == latest_time}
                if len(matched_names) != 1:
                    raise ValueError('Original share has conflicting visible names')
                name = matched_names.pop()
                projections[original] = {
                    'shop_id': target['shop_id'], 'identity_kind': target['identity_kind'],
                    'stable_identifier': target['stable_identifier'], 'identity_status': 'stable_url',
                    'status': 'pending_capture', 'display_name': name, 'observed_shop_name': name,
                    'resolved_source_url': latest['resolved_storefront_url'],
                    'resolved_target_id': target['target_id'], 'verification_status': 'verified',
                    'verification_method': 'authorized_browser_visible_storefront',
                    'verification_observed_at': latest['observed_at'],
                    'verification_receipt_sha256': sorted(path.stem for _, path, _, _, _ in verified),
                    'verification_host_sha256': sorted({receipt['host_evidence']['sha256']
                                                       for _, _, receipt, _, _ in verified}),
                }
            except (OSError, ValueError, TypeError, KeyError, AttributeError):
                projections[original] = dict(invalid)
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        projections = {original: dict(invalid) for original in pending}
    if (paths != sorted(root.glob('*.json')) + sorted((root / 'evidence').glob('*.json'))
            or any(path.read_bytes() != raw for path, raw in raw_files.items())):
        raise ValueError('Intake evidence changed during target export')
    return projections, {str(path): hashlib.sha256(raw).hexdigest() for path, raw in raw_files.items()}


def build_targets(project, runs, attempts=None):
    paths = sorted((Path(project) / 'state/competitors/targets').glob('*.json'))
    rows, digests = [], {}
    for path in paths:
        raw = path.read_bytes()
        rows.append(_read_target(path))
        if raw != path.read_bytes():
            raise ValueError('Target registry changed during export')
        digests[str(path)] = hashlib.sha256(raw).hexdigest()
    resolutions, intake_digests = _verified_share_projections(project, rows)
    digests.update(intake_digests)
    for row in rows:
        if row['identity_status'] == 'unresolved_share_link' and row['source_url'] in resolutions:
            row['registered_display_name'] = row['display_name']
            row.update(resolutions[row['source_url']])
    by_shop = {}
    for run in runs:
        by_shop.setdefault(run['shop_id'], []).append(run)
    registered_identities = {}
    for row in rows:
        if row['shop_id']:
            registered_identities.setdefault(row['shop_id'], set()).add((row['identity_kind'], row['stable_identifier']))
    conflicting_shops = {shop for shop, identities in registered_identities.items() if len(identities) > 1}
    bound = set()
    for row in rows:
        shop_runs = by_shop.get(row['shop_id'], [])
        kinds = set()
        for run in shop_runs:
            original = run.get('snapshot_metadata') or json.loads(run.get('snapshot_json') or '{}')
            if not original:
                original = {'sourceUrl':run.get('source_url')}
            try:
                kinds.add(shop_identity_evidence(original)['identity_kind'])
            except ValueError:
                kinds.add('unverified_legacy_identity')
        if row['shop_id'] in conflicting_shops or shop_runs and kinds != {row['identity_kind']}:
            row['candidate_shop_id'] = row['shop_id']
            row['shop_id'] = None
            row['status'] = 'needs_identity'
            row['identity_conflict'] = True
            if 'resolved_target_id' in row:
                row.pop('resolved_target_id')
                row.pop('resolved_source_url')
                row['verification_status'] = 'needs_review'
            shop_runs = []
        row['run_count'] = len(shop_runs)
        if shop_runs:
            row['status'] = 'observed'; bound.add(row['shop_id'])
            row['observed_shop_name'] = max(shop_runs,key=lambda run:run['observed_to_epoch'])['shop_name']
    for shop, shop_runs in sorted(by_shop.items()):
        if shop in bound:
            continue
        latest = max(shop_runs,key=lambda run:run['observed_to_epoch'])
        observed = {'target_id': 'observed_' + shop, 'platform': 'pdd', 'display_name': latest['shop_name'],
                     'source_url': latest.get('source_url'), 'shop_id': shop, 'identity_status': 'observed_source',
                     'status': 'observed', 'registered_at': None, 'run_count': len(shop_runs)}
        if shop in conflicting_shops:
            observed.update(candidate_shop_id=shop, shop_id=None, status='needs_identity', identity_conflict=True)
        rows.append(observed)
    settings, setting_digests = load_tracking(project)
    digests.update(setting_digests)
    for row in rows:
        row.update(target_tracking_fields(row, runs, settings.get(row.get('shop_id')), attempts))
    return rows, digests


def set_target_tracking(project, runs, target_id, status, expected_revision):
    """Resolve a known target before persisting explicit per-shop intent."""
    if not isinstance(target_id, str) or not re.fullmatch(r'(?:target_[0-9a-f]{24}|observed_shop_[0-9a-f]{24})', target_id):
        raise ValueError('目标身份格式无效')
    rows, _ = build_targets(project, runs)
    target = next((row for row in rows if row['target_id'] == target_id), None)
    if target is None:
        raise ValueError('目标不存在；请先登记并核对店铺')
    if not target['can_configure_tracking']:
        raise ValueError('店铺身份尚未核实，不能开启或暂停跟踪')
    saved = save_tracking(project, target['shop_id'], status, expected_revision)
    setting = saved['setting']
    return {'operation': 'tracking', 'setting_status': saved['status'], 'target_id': target_id,
            'shop_id': target['shop_id'], 'target_status': target['status'],
            'tracking_status': setting['status'], 'tracking_revision': setting['revision'],
            'tracking_updated_at': setting['updated_at'], 'tracking_connection_status': 'connection_pending',
            'website_collection_performed': False, 'baseline_changed': False, 'history_deleted': False,
            'setting_path': saved['path']}


def list_followed_targets(project, runs, attempts=None):
    """Read-only host work plan; one stable shop once, not a running scheduler."""
    rows, fingerprints = build_targets(project, runs, attempts)
    shops = {}
    for row in rows:
        if row['tracking_status'] == 'enabled' and row['can_configure_tracking']:
            shops.setdefault(row['shop_id'], row)
    return {'status': 'plan_only', 'targets': list(shops.values()),
            'target_count': len(shops), 'tracking_connection_status': 'connection_pending',
            'website_collection_performed': False, 'settings_files_sha256': fingerprints,
            'message': '这是已明确开启店铺的只读执行清单，不代表任务已调度或网站已采集。'}
