"""Durable, shop-bound manual capture control; never drives a browser or imports data.

Only the authorized host operates capture_session.mjs. Public job receipts never
contain the journal's owner token. Business stores are opened read-only here.
"""
from __future__ import annotations

import argparse
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import re
from pathlib import Path, PurePosixPath, PureWindowsPath
import uuid
from urllib.parse import urlsplit

from . import collection_log as journal
from .competitor_registry import _read_target, _target, write_new_json
from .store import _connect_readonly, shop_identity_evidence

JOB_ID = re.compile(r'capture_[0-9a-f]{32}')
MAX_FRESH_RETRIES = 1
TERMINAL_PHASES = frozenset(('complete', 'partial', 'failed', 'manual_review', 'needs_login'))


def _read(path):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('Duplicate JSON key')
            result[key] = value
        return result
    raw = Path(path).read_bytes()
    if len(raw) > 32 * 1024 * 1024:
        raise ValueError('Capture control file exceeds limit')
    value = json.loads(raw.decode('utf-8'), object_pairs_hook=unique,
                       parse_constant=lambda _: (_ for _ in ()).throw(ValueError('Non-finite JSON')))
    if not isinstance(value, dict):
        raise ValueError('Capture control requires a JSON object')
    return value


def _job_dir(project, job_id):
    if not isinstance(job_id, str) or not JOB_ID.fullmatch(job_id):
        raise ValueError('Invalid capture job ID')
    root = Path(project).resolve() / 'state' / 'capture_jobs'
    result = root / job_id
    _no_redirect(Path(project).resolve(), result)
    return result


def _no_redirect(project, path):
    relative = path.relative_to(project)
    current = project
    for part in relative.parts:
        current = current / part
        if current.is_symlink() or (hasattr(current, 'is_junction') and current.is_junction()):
            raise ValueError('Capture paths must not be redirected')


def _job(project, job_id, *, read_migrated=False):
    result = _read(_job_dir(project, job_id) / 'job.json')
    if result.get('job_id') != job_id or result.get('schema_version') != 1:
        raise ValueError('Capture job identity/schema mismatch')
    path = Path(project).resolve() / 'sources' / job_id
    _no_redirect(Path(project).resolve(), path)
    if result.get('session_directory') != str(path):
        # A migrated, closed receipt keeps its original absolute path as raw
        # evidence. Read only the paired sources/<job_id> in this checkout;
        # never follow the foreign path or let it authorize a mutation.
        saved = result.get('session_directory')
        old = (PureWindowsPath(saved) if isinstance(saved, str) and
               re.match(r'^[A-Za-z]:[\\/]', saved) else
               PurePosixPath(saved) if isinstance(saved, str) else None)
        attempt = _attempt(project, result.get('attempt_id')) if read_migrated else None
        valid = (old is not None and old.is_absolute() and '..' not in old.parts
                 and len(old.parts) >= 4
                 and old.parts[-3].casefold() == Path(project).resolve().name.casefold()
                 and old.parts[-2:] == ('sources', job_id)
                 and attempt is not None and attempt.get('shop_id') == result.get('shop_id')
                 and attempt.get('status') in ('complete', 'partial', 'failed'))
        if not valid:
            raise ValueError('Capture session directory conflicts with its job')
        result = {**result, 'session_directory': str(path), 'migrated_receipt_read_only': True}
    identity = shop_identity_evidence({'sourceUrl': result['source_url']})
    if identity['shop_id'] != result['shop_id']:
        raise ValueError('Capture job shop identity conflicts')
    return result


def _attempt(project, attempt_id):
    rows = journal.get_status(Path(project) / 'state/collection_attempts')['attempts']
    return next((row for row in rows if row['attempt_id'] == attempt_id), None)


def _known_shop(project, shop_id):
    with closing(_connect_readonly(Path(project) / 'data')) as conn:
        rows = conn.execute('SELECT r.*, s.shop_name, (SELECT COUNT(*) FROM observations o '
                            'WHERE o.run_id=r.run_id) AS card_count FROM runs r JOIN shops s '
                            'ON s.shop_id=r.shop_id WHERE r.shop_id=? ORDER BY observed_to_epoch DESC',
                            (shop_id,)).fetchall()
    if not rows:
        raise ValueError('Unknown shop: first verify a stable storefront URL and shop name')
    rows = [dict(row) for row in rows]
    latest = rows[0]
    identities = set()
    for row in rows:
        evidence = shop_identity_evidence(json.loads(row['snapshot_json']))
        if evidence['shop_id'] != shop_id:
            raise ValueError('Existing shop evidence conflicts')
        identities.add((evidence['identity_kind'], evidence['stable_identifier']))
    if len(identities) != 1:
        raise ValueError('Existing shop has conflicting typed storefront identities')
    return latest, next((row for row in rows if row['status'] == 'complete'
                        and row['end_boundary_observed'] == 1), None)


def _verified_intake_name(project, target):
    """Consume preserved host evidence without rewriting a target or its URL."""
    from scripts.record_verified_intake import decode, ordinary, selected_markup
    root = project / 'state/competitors/intakes'
    verified = []
    for path in sorted(root.glob('*.json')):
        _no_redirect(project, path)
        ordinary(path)
        raw = path.read_bytes()
        receipt = decode(raw)
        if receipt.get('shop_id') != target['shop_id']:
            continue
        canonical = json.dumps(receipt, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
        if path.name != hashlib.sha256(canonical).hexdigest() + '.json':
            raise ValueError('Verified intake receipt hash mismatch')
        name, source = receipt.get('observed_shop_name'), receipt.get('resolved_storefront_url')
        resolved = _target(name, source)
        original = _target(None, receipt.get('original_input_url'))
        expected_origin, observed_origin = urlsplit(target['source_url']), urlsplit(source)
        identity = (target['identity_kind'], target['stable_identifier'])
        if (type(receipt.get('schema_version')) is not int or receipt['schema_version'] != 1 or receipt.get('verification_method') != 'authorized_browser_visible_storefront'
                or receipt.get('selected_sort') != '上新' or resolved['identity_status'] != 'stable_url'
                or (resolved['identity_kind'], resolved['stable_identifier']) != identity
                or (receipt.get('identity_kind'), receipt.get('stable_identifier')) != identity
                or resolved['shop_id'] != target['shop_id'] or receipt.get('stable_target_id') != target['target_id']
                or (expected_origin.scheme, expected_origin.netloc, expected_origin.path) !=
                   (observed_origin.scheme, observed_origin.netloc, observed_origin.path)
                or original['identity_status'] == 'stable_url' and
                   (original['identity_kind'], original['stable_identifier']) != identity):
            raise ValueError('Verified intake storefront identity mismatch')
        host = receipt.get('host_evidence') or {}
        digest = host.get('sha256')
        if (not isinstance(digest, str) or not re.fullmatch('[0-9a-f]{64}', digest)
                or host.get('file') != 'evidence/' + digest + '.json'
                or type(host.get('bytes')) is not int or not 0 < host['bytes'] <= 4 * 1024 * 1024):
            raise ValueError('Verified intake host evidence reference invalid')
        evidence_path = root / host['file']
        _no_redirect(project, evidence_path)
        ordinary(evidence_path)
        evidence_raw = evidence_path.read_bytes()
        if len(evidence_raw) != host['bytes'] or hashlib.sha256(evidence_raw).hexdigest() != digest:
            raise ValueError('Verified intake host evidence hash mismatch')
        evidence = decode(evidence_raw)
        header = evidence.get('header')
        if (not isinstance(name, str) or not name.strip() or not isinstance(header, str)
                or name != evidence.get('shopName') or name.strip() not in {line.strip() for line in header.splitlines()}
                or evidence.get('sourceUrl') != source or evidence.get('userShareUrl') != original['source_url']
                or evidence.get('observedAt') != receipt.get('observed_at')):
            raise ValueError('Verified intake visible storefront evidence mismatch')
        selected_markup(evidence)
        stamp = datetime.fromisoformat(receipt['observed_at'].replace('Z', '+00:00'))
        if stamp.tzinfo is None or stamp > datetime.now(timezone.utc):
            raise ValueError('Verified intake observation time invalid')
        if path.read_bytes() != raw or evidence_path.read_bytes() != evidence_raw:
            raise ValueError('Verified intake changed during validation')
        verified.append((stamp, name.strip()))
    if not verified:
        return None
    newest = max(stamp for stamp, _ in verified)
    names = {name for stamp, name in verified if stamp == newest}
    if len(names) != 1:
        raise ValueError('Verified intake storefront names conflict')
    return names.pop()


def _resolve_shop(project, shop_id):
    """Resolve history or one stable registration without borrowing another shop."""
    if not isinstance(shop_id, str) or not re.fullmatch(r'shop_[0-9a-f]{24}', shop_id):
        raise ValueError('Invalid shop ID')
    project = Path(project).resolve()
    registered = []
    for path in sorted((project / 'state/competitors/targets').glob('*.json')):
        _no_redirect(project, path)
        row = _read_target(path)
        if row.get('shop_id') == shop_id:
            registered.append(row)
    identities = {(row['identity_kind'], row['stable_identifier']) for row in registered}
    if len(identities) > 1:
        raise ValueError('Registered shop has conflicting mall_id/mall_sn identities; resolve the source explicitly')
    try:
        latest, reference = _known_shop(project, shop_id)
    except ValueError as error:
        if not str(error).startswith('Unknown shop:'):
            raise
        latest = reference = None
    if latest is not None:
        evidence = shop_identity_evidence(json.loads(latest['snapshot_json']))
        if identities and identities != {(evidence['identity_kind'], evidence['stable_identifier'])}:
            raise ValueError('Registered identity conflicts with observed shop history')
        return {'shop_name': latest['shop_name'], 'source_url': latest['source_url'],
                'latest': latest, 'reference': reference, 'target_status': 'observed',
                'identity_kind': evidence['identity_kind'], 'stable_identifier': evidence['stable_identifier'],
                'identity_basis': 'observed_snapshot'}
    if len(registered) != 1 or registered[0]['identity_status'] != 'stable_url':
        raise ValueError('Unknown shop: register and verify one stable storefront URL and shop name first')
    target = registered[0]
    verified_name = _verified_intake_name(project, target)
    if not verified_name and target['display_name'] == '待核实店名':
        raise ValueError('Unknown shop: verify the visible storefront name before starting')
    return {'shop_name': verified_name or target['display_name'], 'source_url': target['source_url'],
            'latest': None, 'reference': None, 'target_status': 'pending_capture',
            'identity_kind': target['identity_kind'], 'stable_identifier': target['stable_identifier'],
            'identity_basis': 'verified_intake' if verified_name else 'registered_stable_url'}


def plan(project, shop_id):
    project = Path(project).resolve()
    resolved = _resolve_shop(project, shop_id)
    latest, reference = resolved['latest'], resolved['reference']
    state = journal.get_status(project / 'state/collection_attempts')
    jobs = []
    for path in sorted((project / 'state/capture_jobs').glob('capture_*/job.json')):
        job = _job(project, path.parent.name, read_migrated=True)
        if job['shop_id'] == shop_id:
            identity = shop_identity_evidence({'sourceUrl': job['source_url']})
            if (identity['identity_kind'], identity['stable_identifier']) != (resolved['identity_kind'], resolved['stable_identifier']):
                raise ValueError('Capture job typed identity conflicts with the selected shop')
            jobs.append(status(project, job['job_id']))
    return {'status': 'plan_only', 'shop_id': shop_id, 'shop_name': resolved['shop_name'],
            'source_url': resolved['source_url'], 'target_status': resolved['target_status'],
            'identity_basis': resolved['identity_basis'],
            'latest_run_id': latest['run_id'] if latest else None,
            'latest_card_count': latest['card_count'] if latest else None,
            'latest_status': latest['status'] if latest else None,
            'reference_run_id': reference['run_id'] if reference else None,
            'reference_card_count': reference['card_count'] if reference else None,
            'active_lock': state['active_lock'], 'jobs': jobs,
            'website_collection_performed': False, 'scheduled_collection_connected': False,
            'next_action': '核对店铺与上新顶部，begin取得本店尝试，再由授权浏览器执行会话；完整性验收后备份、副本验证、入库。'}


def begin(project, *, shop_id=None, source_url=None, shop_name=None, retry_job=None):
    project = Path(project).resolve()
    if not (project / 'AGENTS.md').is_file() or not (project / 'data/monitor.sqlite3').is_file() or not (project / 'data/images.sqlite3').is_file():
        raise ValueError('Use an existing project with both business databases; no empty replacement is created')
    parent, parent_session, retry_index = None, None, 0
    if retry_job:
        # A first capture may fail before any business run exists. Its verified
        # immutable job, not historical runs, supplies the retry's target.
        parent = _job(project, retry_job)
        for supplied, expected, label in ((shop_id, parent['shop_id'], 'shop ID'),
                                           (source_url, parent['source_url'], 'source URL'),
                                           (shop_name, parent['shop_name'], 'shop name')):
            if supplied is not None and supplied != expected:
                raise ValueError('Retry conflicts with its parent ' + label)
        shop_id, source_url, shop_name = parent['shop_id'], parent['source_url'], parent['shop_name']
    elif shop_id and not source_url:
        resolved = _resolve_shop(project, shop_id)
        source_url = resolved['source_url']
        if shop_name is None:
            shop_name = resolved['shop_name']
    if not isinstance(shop_name, str) or not shop_name.strip():
        raise ValueError('Verified storefront name is required')
    target = _target(shop_name, source_url)
    if target['identity_status'] != 'stable_url' or not target['shop_id']:
        raise ValueError('Resolve the share URL in the authorized browser before starting')
    if shop_id is not None and shop_id != target['shop_id']:
        raise ValueError('Requested shop differs from the stable storefront URL')
    shop_id = target['shop_id']
    if retry_job:
        previous = _attempt(project, parent['attempt_id'])
        if parent['shop_id'] != shop_id or parent['source_url'] != source_url:
            raise ValueError('Retry must preserve its original shop and source URL')
        if not previous or previous['status'] not in ('partial', 'failed'):
            raise ValueError('Finish or explicitly abandon the prior attempt before a fresh retry')
        retry_index = parent['fresh_retry_index'] + 1
        if retry_index > MAX_FRESH_RETRIES:
            raise ValueError('Fresh retry limit reached: inspect login/layout/stop evidence before a new reviewed cycle')
        if (_job_dir(project, retry_job) / 'retry_claim.json').exists():
            raise ValueError('This parent already has a retry; inspect that child instead of creating siblings')
        path = Path(parent['session_directory']) / 'session.json'
        if path.exists():
            prior = _read(path)
            if prior.get('attemptId') != parent['attempt_id']:
                raise ValueError('Parent session attempt does not match the journal job')
            # The host must recover and seal an interrupted session first.
            if prior.get('operation') or prior.get('phase') not in TERMINAL_PHASES:
                raise ValueError('Recover/seal the previous browser session before retry; old batches are preserved')
            if prior.get('phase') == 'complete':
                raise ValueError('A captured complete session must be reviewed/imported, not called an incomplete retry')
            if (prior.get('retry') or {}).get('allowed') is not True:
                raise ValueError('The prior session requires login/layout/permission review; do not blindly retry')
            parent_session = {'directory': parent['session_directory']}
    job_id = 'capture_' + uuid.uuid4().hex
    directory = _job_dir(project, job_id)
    directory.mkdir(parents=True, exist_ok=False)
    session_dir = project / 'sources' / job_id
    _no_redirect(project, session_dir)
    acquired = journal.begin_attempt(project / 'state/collection_attempts', trigger_kind='manual',
                                    shop_id=shop_id, owner_label='capture-control', lease_minutes=120)
    if not acquired['acquired']:
        return {**acquired, 'website_collection_performed': False}
    attempt = acquired['attempt']
    options = {'sourceUrl': source_url, 'shopName': shop_name,
               'checkpointReference': 'sources/' + job_id + '/batches',
               'attemptId': attempt['attempt_id']}
    if parent_session:
        options['parentSession'] = parent_session
    value = {'schema_version': 1, 'job_id': job_id, 'shop_id': shop_id,
             'shop_name': shop_name, 'source_url': source_url, 'attempt_id': attempt['attempt_id'],
             'created_at': attempt['started_at'], 'parent_job_id': retry_job,
             'fresh_retry_index': retry_index, 'session_directory': str(session_dir),
             'session_options': options, 'website_collection_performed': False}
    try:
        write_new_json(directory / 'owner.private.json', {'attempt_id': attempt['attempt_id'],
                                                        'owner_token': acquired['owner_token']})
        if retry_job:
            # Exclusive durable claim under the already-acquired global lease.
            # A crashed child still consumes the budget; no sibling retry loop.
            write_new_json(_job_dir(project, retry_job) / 'retry_claim.json',
                           {'parent_job_id': retry_job, 'child_job_id': job_id,
                            'child_attempt_id': attempt['attempt_id']})
        write_new_json(directory / 'job.json', value)
    except BaseException:
        journal.finish_attempt(project / 'state/collection_attempts', attempt['attempt_id'],
                               acquired['owner_token'], status='failed', reason='capture_job_receipt_write_failed')
        raise
    return {'acquired': True, **value, 'status': 'prepared',
            'message': '已建立本店尝试；尚未读网页。保持登录窗口，由授权宿主创建新采集会话。'}


def status(project, job_id):
    job = _job(project, job_id, read_migrated=True)
    attempt = _attempt(project, job['attempt_id'])
    if not attempt or attempt.get('shop_id') != job['shop_id']:
        raise ValueError('Journal and job shop do not agree')
    session_file = Path(job['session_directory']) / 'session.json'
    session = _read(session_file) if session_file.is_file() else None
    if session and session.get('attemptId') != job['attempt_id']:
        raise ValueError('Browser session and journal attempt do not agree')
    # Never return arbitrary state/private content from a public status command.
    progress = {key: session.get(key) for key in ('sessionId', 'phase', 'statusLabel', 'importReady', 'progress',
                'snapshot', 'lastError', 'retry', 'updatedAt')} if session else None
    return {'job_id': job_id, 'shop_id': job['shop_id'], 'shop_name': job['shop_name'],
            'attempt_id': job['attempt_id'], 'parent_job_id': job['parent_job_id'],
            'fresh_retry_index': job['fresh_retry_index'], 'journal_status': attempt['status'],
            'run_id': attempt.get('run_id'), 'reason': attempt.get('reason'),
            'lease_expires_at': attempt.get('lease_expires_at'), 'session': progress,
            'accepted_into_history': attempt['status'] in ('complete', 'partial') and bool(attempt.get('run_id')),
            'website_collection_started': bool(session and (session.get('progress') or {}).get('batches')),
            'scheduled_collection_connected': False,
            'migrated_receipt_read_only': job.get('migrated_receipt_read_only', False)}


def _owner(project, job_id):
    job = _job(project, job_id)
    owner = _read(_job_dir(project, job_id) / 'owner.private.json')
    if owner.get('attempt_id') != job['attempt_id'] or not isinstance(owner.get('owner_token'), str):
        raise ValueError('Private job owner does not match the attempt')
    return job, owner['owner_token']


def renew(project, job_id):
    job, token = _owner(project, job_id)
    journal.renew_attempt(Path(project) / 'state/collection_attempts', job['attempt_id'], token, 120)
    return status(project, job_id)


def abandon(project, job_id, reason):
    """Operator has stopped the host session; preserve its source checkpoints."""
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError('Record the actual reason for stopping')
    job, token = _owner(project, job_id)
    session_file = Path(job['session_directory']) / 'session.json'
    if session_file.exists():
        session = _read(session_file)
        if session.get('attemptId') != job['attempt_id']:
            raise ValueError('Session attempt differs; journal lock was not released')
        if session.get('operation') or session.get('phase') not in TERMINAL_PHASES:
            raise ValueError('Stop/recover/seal the browser session before abandoning its journal lock')
        if session.get('phase') == 'complete':
            raise ValueError('Complete captured data awaits acceptance; use finish after validation/import')
    journal.finish_attempt(Path(project) / 'state/collection_attempts', job['attempt_id'], token,
                           status='failed', reason=reason,
                           checkpoint_path=job['session_directory'] if session_file.exists() else None)
    return status(project, job_id)


def check(project, snapshot_path, expected_shop_id=None):
    from .capture_integrity import verify_capture
    result = verify_capture(snapshot_path, expected_shop_id=expected_shop_id)
    snapshot = _read(snapshot_path)
    shop_id = shop_identity_evidence(snapshot)['shop_id']
    reference = None
    try:
        _, reference = _known_shop(project, shop_id)
    except ValueError as exc:
        if not str(exc).startswith('Unknown shop:'):
            raise
    count = len(snapshot.get('rows') or [])
    dropped = bool(reference and snapshot.get('status') == 'complete' and count < reference['card_count'])
    result.update(reference_run_id=reference['run_id'] if reference else None,
                  reference_card_count=reference['card_count'] if reference else None,
                  captured_card_count=count, coverage_drop_requires_review=dropped,
                  ready_for_rehearsal=bool(result.get('valid') and result.get('status') == 'passed' and not dropped),
                  business_databases_written=False,
                  count_review_note='本轮比最近完整轮少，需核查是否漏采或店铺变化；不能直接判为下架。' if dropped else None)
    return result


def finish(project, job_id, run_id):
    job, token = _owner(project, job_id)
    session = _read(Path(job['session_directory']) / 'session.json')
    if session.get('attemptId') != job['attempt_id'] or session.get('operation') or session.get('phase') not in TERMINAL_PHASES or session.get('importReady') is not True:
        raise ValueError('Session is not sealed for this attempt')
    sealed = session.get('snapshot') or {}
    if sealed.get('file') not in ('snapshot.json', 'snapshot.recovered.json'):
        raise ValueError('Session has no recognized sealed snapshot')
    snapshot_path = Path(job['session_directory']) / sealed['file']
    _no_redirect(Path(project).resolve(), snapshot_path)
    if hashlib.sha256(snapshot_path.read_bytes()).hexdigest() != sealed.get('sha256'):
        raise ValueError('Sealed snapshot SHA differs from the session receipt')
    receipt = check(project, snapshot_path, job['shop_id'])
    if not receipt.get('valid') or receipt.get('status') != 'passed':
        raise ValueError('Capture evidence failed verification; journal was not marked complete')
    snapshot = _read(snapshot_path)
    if snapshot['status'] != sealed.get('status') or (snapshot['status'] == 'complete' and session['phase'] != 'complete'):
        raise ValueError('Snapshot completion contradicts the sealed session state')
    if (snapshot.get('collectionEvidence') or {}).get('attemptId') != job['attempt_id']:
        raise ValueError('Snapshot does not belong to this attempt')
    journal.finish_attempt(Path(project) / 'state/collection_attempts', job['attempt_id'], token,
                           status=snapshot['status'], run_id=run_id, snapshot_path=snapshot_path,
                           data_dir=Path(project) / 'data',
                           reason=(snapshot.get('collectionEvidence') or {}).get('stopReason')
                                  if snapshot['status'] == 'partial' else None,
                           checkpoint_path=job['session_directory'])
    return status(project, job_id)


def main(argv=None):
    parser = argparse.ArgumentParser(description='逐店可靠采集流程控制；网页仍由授权浏览器宿主操作')
    parser.add_argument('--project', type=Path, default=Path(__file__).resolve().parents[1])
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('plan'); p.add_argument('--shop-id', required=True)
    p = sub.add_parser('begin'); p.add_argument('--shop-id'); p.add_argument('--source-url'); p.add_argument('--shop-name'); p.add_argument('--retry-job')
    for name in ('status', 'renew', 'abandon', 'finish'):
        p = sub.add_parser(name); p.add_argument('job_id')
        if name == 'abandon': p.add_argument('--reason', required=True)
        if name == 'finish': p.add_argument('--run-id', required=True)
    p = sub.add_parser('check'); p.add_argument('snapshot', type=Path); p.add_argument('--expected-shop-id')
    args = parser.parse_args(argv)
    try:
        if args.command == 'plan': result = plan(args.project, args.shop_id)
        elif args.command == 'begin': result = begin(args.project, shop_id=args.shop_id, source_url=args.source_url, shop_name=args.shop_name, retry_job=args.retry_job)
        elif args.command == 'status': result = status(args.project, args.job_id)
        elif args.command == 'renew': result = renew(args.project, args.job_id)
        elif args.command == 'abandon': result = abandon(args.project, args.job_id, args.reason)
        elif args.command == 'finish': result = finish(args.project, args.job_id, args.run_id)
        else: result = check(args.project, args.snapshot, args.expected_shop_id)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 2 if result.get('valid') is False or result.get('acquired') is False or result.get('coverage_drop_requires_review') else 0
    except (ValueError, OSError) as exc:
        print(json.dumps({'status': 'failed', 'message': str(exc), 'website_collection_performed': False}, ensure_ascii=False))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
