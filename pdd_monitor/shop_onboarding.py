"""Durable registration -> verified storefront; collection needs a later click.

Only an explicit registered target can enter this workflow. Browser work stays in
CollectionService's existing single queue; this module never operates a browser.
"""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import stat
import uuid

from .competitor_registry import _read_target, _target, register_target

ACTIVE = {'queued', 'running'}
TARGET_ID = re.compile(r'target_[0-9a-f]{24}')
ONBOARD_ID = re.compile(r'onboard_[0-9a-f]{32}')


def read_evidence(project, path):
    """Read only a bounded ordinary file under the fixed project workspace."""
    project, path = Path(project), Path(path)
    current = project
    for part in path.relative_to(project).parts:
        current = current / part
        info = current.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0):
            raise ValueError('Redirected onboarding evidence refused')
    if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= 4 * 1024 * 1024:
        raise ValueError('Invalid onboarding evidence size/type')
    raw = path.read_bytes()
    if len(raw) != info.st_size:
        raise ValueError('Onboarding evidence changed')
    from scripts.record_verified_intake import decode
    return decode(raw), hashlib.sha256(raw).hexdigest()


def record_intake(project, evidence_path, original_url, expected_shop_id):
    from scripts.record_verified_intake import record_verified_intake
    return record_verified_intake(project, evidence_path, original_url, expected_shop_id)


class ShopOnboarding:
    def __init__(self, collection):
        self.collection = collection
        self.project = collection.project

    @property
    def rows(self):
        rows = self.collection.state.get('onboardings', [])
        if not isinstance(rows, list) or any(not isinstance(row, dict) or not ONBOARD_ID.fullmatch(str(row.get('id', ''))) for row in rows):
            raise ValueError('Invalid saved onboarding state')
        if len({row['id'] for row in rows}) != len(rows):
            raise ValueError('Duplicate onboarding state')
        return rows

    def target(self, target_id):
        if not isinstance(target_id, str) or not TARGET_ID.fullmatch(target_id):
            raise ValueError('Invalid registered target ID')
        path = self.project / 'state/competitors/targets' / (target_id + '.json')
        try:
            row, digest = read_evidence(self.project, path)
        except FileNotFoundError as error:
            raise ValueError('Unknown registered target') from error
        validated = _read_target(path)
        if row != validated or read_evidence(self.project, path)[1] != digest:
            raise ValueError('Target registration changed')
        return row, digest

    def public(self, row, *, lightweight=False):
        result = {key: deepcopy(row[key]) for key in (
            'id', 'target_id', 'resolved_target_id', 'shop_id', 'shop_name', 'status', 'stage',
            'verification_job_id', 'collection_job_id', 'created_at', 'ended_at',
            'message', 'reason', 'cancel_requested', 'retry_of', 'mode',
            'identified_at', 'collection_requested_at') if key in row}
        child_id = row.get('collection_job_id') or row.get('verification_job_id')
        child = next((job for job in self.collection.state['jobs'] if job['id'] == child_id), None)
        if child:
            # Probe bodies and evidence remain on disk; the UI needs only status.
            # The directory is a navigation/progress read, not a fresh image
            # content audit. Avoid hashing historic image BLOBs on every poll.
            job = child if lightweight else self.collection._public_job(child)
            result['job'] = {key: deepcopy(job[key]) for key in (
                'id', 'kind', 'shop_id', 'shop_name', 'status', 'message', 'reason',
                'started_at', 'ended_at', 'elapsed_seconds', 'image_summary',
                'dashboard_built', 'cancel_requested', 'progress', 'ai_requests') if key in job}
            if row['status'] in ACTIVE and child['status'] in ACTIVE:
                result['status'] = child['status']
            if child.get('status') == 'running':
                try:
                    progress, _ = read_evidence(self.project, self.collection.directory / child_id / 'progress.json')
                    result['job']['progress'] = progress
                except (OSError, ValueError):
                    pass
        result.update(execution_mode='fixed_program', ai_requests=0)
        result['can_collect'] = row['status'] == 'ready' and not row.get('cancel_requested')
        return result

    def start(self, payload):
        if not isinstance(payload, dict) or set(payload) not in ({'target_id'}, {'target_id', 'retry'}, {'name', 'url'}):
            raise ValueError('Use a registered target or one explicit shop URL')
        retry = 'retry' in payload
        if retry and payload['retry'] is not True:
            raise ValueError('A retry must be explicitly requested')
        from .collection_service import TERMINAL, browser_mode, public_shop_entry_url, stamp
        with self.collection.lock:
            if 'url' in payload:
                name, url = payload['name'], payload['url']
                if ((name is not None and (not isinstance(name, str) or len(name) > 200))
                        or not isinstance(url, str) or not 1 <= len(url) <= 4096):
                    raise ValueError('Invalid onboarding name/link')
                candidate = _target(name, url)
                public_shop_entry_url(candidate['source_url'])
                if browser_mode(self.project) != 'local_chrome':
                    return 409, {'status': 'needs_browser', 'message': '请使用本机 Chrome 连接后开始接入。'}
                registered = register_target(self.project, name, candidate['source_url'])
                payload = {'target_id': registered['target']['target_id']}
            target, digest = self.target(payload['target_id'])
            existing = next((row for row in reversed(self.rows) if row['target_id'] == target['target_id']), None)
            if existing and not retry:
                return (202 if existing['status'] in ACTIVE else 200), self.public(existing)
            if retry:
                if existing is None:
                    raise ValueError('No previous onboarding attempt to retry')
                child_ids = {existing.get('verification_job_id'), existing.get('collection_job_id')} - {None}
                children = [job for job in self.collection.state['jobs'] if job['id'] in child_ids or job.get('onboarding_id') == existing['id']]
                if (existing['status'] not in TERMINAL or existing['status'] == 'complete'
                        or any(job['status'] not in TERMINAL or job['id'] == self.collection.active for job in children)
                        or not child_ids <= {job['id'] for job in children}):
                    return 409, {**self.public(existing), 'message': '仅可在未完成接入及全部子任务结束后明确重试；已完成店铺请使用本店采集入口。'}
            url = public_shop_entry_url(target.get('source_url'))
            if browser_mode(self.project) != 'local_chrome':
                return 409, {'status': 'needs_browser', 'message': '请使用本机 Chrome 连接后开始接入。'}
            if sum(job['status'] in ACTIVE for job in self.collection.state['jobs']) >= 12:
                return 409, {'status': 'queued', 'message': '采集队列已满，请等待当前任务结束。'}
            row = {'id': 'onboard_' + uuid.uuid4().hex, 'target_id': target['target_id'],
                   'target_sha256': digest, 'original_url': url, 'status': 'queued',
                   'mode': 'identity_only',
                   'stage': 'verify_storefront', 'created_at': stamp(self.collection.clock()),
                   'message': '等待打开已登记链接并核验店铺。'}
            if retry:
                row['retry_of'] = existing['id']
            rows = self.collection.state.setdefault('onboardings', [])
            rows.append(row)
            try:
                self.collection._save()
            except OSError:
                rows.remove(row)
                raise
            self.advance()
            self.collection.wake.set()
            return 202, self.public(row)

    def collect(self, payload):
        """A separate explicit action, scoped to one verified onboarding ID.

        The durable consent marker is saved before enqueueing. Restart recovery
        may finish that exact request, but adding/retrying a link never sets it.
        Completed legacy workflows return their existing result, not a new run.
        """
        if not isinstance(payload, dict) or set(payload) != {'id'}:
            raise ValueError('Collection accepts only a verified onboarding ID')
        from .collection_service import stamp
        with self.collection.lock:
            self.status(id=payload['id'])
            row = next(row for row in self.rows if row['id'] == payload['id'])
            target, digest = self.target(row['target_id'])
            if digest != row['target_sha256'] or target.get('source_url') != row['original_url']:
                raise ValueError('Original registered target changed')
            capture = self.child(row, 'shop', 'collection_job_id')
            if capture:
                if capture.get('shop_id') != row.get('shop_id'):
                    raise ValueError('Existing collection scope differs')
                return (202 if capture['status'] in ACTIVE else 200), self.public(row)
            if row.get('collection_requested_at') and row['status'] in ACTIVE:
                return 202, self.public(row)
            latest = next(item for item in reversed(self.rows) if item['target_id'] == row['target_id'])
            if row['status'] != 'ready' or row.get('cancel_requested') or latest['id'] != row['id']:
                return 409, {**self.public(row), 'message': '请先完成此店铺识别，再点击开始采集。'}
            probe = self.child(row, 'probe', 'verification_job_id')
            if not probe or probe['status'] != 'complete':
                raise ValueError('Verified storefront worker is missing')
            identity = row.get('shop_id'), row.get('resolved_target_id')
            self.verify(row, probe)
            if identity != (row.get('shop_id'), row.get('resolved_target_id')):
                raise ValueError('Verified storefront identity changed')
            before = deepcopy(row)
            row.update(collection_requested_at=stamp(self.collection.clock()), status='queued',
                       stage='collect_shop', message='已确认采集此店铺，等待采集器。')
            row.pop('ended_at', None)
            row.pop('reason', None)
            try:
                self.collection._save()
            except OSError:
                row.clear(); row.update(before)
                raise
            self.advance()
            self.collection.wake.set()
            return (202 if row['status'] in ACTIVE else 409), self.public(row)

    def status(self, *, id=None, target_id=None):
        if (id is None) == (target_id is None):
            raise ValueError('Use one onboarding selector')
        with self.collection.lock:
            if id is not None:
                if not isinstance(id, str) or not ONBOARD_ID.fullmatch(id):
                    raise ValueError('Invalid onboarding ID')
                row = next((row for row in self.rows if row['id'] == id), None)
                if row is None:
                    raise ValueError('Unknown onboarding ID')
            else:
                self.target(target_id)
                row = next((row for row in reversed(self.rows) if row['target_id'] == target_id), None)
                if row is None:
                    return {'id': None, 'target_id': target_id, 'status': 'idle', 'stage': 'idle',
                            'message': '该登记目标尚未开始接入。'}
            return self.public(row)

    def stop(self, row, status, reason, message):
        from .collection_service import stamp
        row.update(status=status, reason=reason, message=message,
                   ended_at=stamp(self.collection.clock()))
        if status not in ('complete', 'partial'):
            for child in self.collection.state['jobs']:
                if child.get('onboarding_id') == row['id'] and child['status'] == 'queued':
                    child.update(status='cancelled', reason='onboarding_stopped', ended_at=stamp(self.collection.clock()),
                                 message='接入核验已停止，未执行此排队步骤。')

    def cancel(self, payload):
        if not isinstance(payload, dict) or set(payload) != {'id'}:
            raise ValueError('Cancel accepts only an onboarding ID')
        with self.collection.lock:
            self.status(id=payload['id'])
            row = next(row for row in self.rows if row['id'] == payload['id'])
            if row['status'] not in ACTIVE:
                return 409, self.public(row)
            row['cancel_requested'] = True
            child_id = row.get('collection_job_id') or row.get('verification_job_id')
            child = next((job for job in self.collection.state['jobs'] if job['id'] == child_id), None)
            if child and child['status'] == 'running':
                from .collection_service import atomic, stamp
                atomic(self.collection.directory / child['id'] / 'cancel.json', {'requested_at': stamp(self.collection.clock())})
                child['cancel_requested'] = True
                row['message'] = '正在停止当前步骤，等待清理；不会继续下一步。'
            else:
                if child and child['status'] == 'queued':
                    from .collection_service import stamp
                    child.update(status='cancelled', ended_at=stamp(self.collection.clock()), message='接入任务已取消，未读网页。')
                self.stop(row, 'cancelled', 'operator_cancelled', '接入已取消；已保存证据与历史保留。')
            self.collection._save()
            self.collection.wake.set()
            return 200, self.public(row)

    def child(self, row, kind, id_key):
        matches = [job for job in self.collection.state['jobs'] if job.get('onboarding_id') == row['id'] and job['kind'] == kind]
        if len(matches) > 1:
            raise ValueError('Multiple onboarding children')
        found = matches[0] if matches else None
        if row.get(id_key):
            found = next((job for job in self.collection.state['jobs'] if job['id'] == row[id_key]), None)
            if not found or found['kind'] != kind or found.get('onboarding_id') != row['id']:
                raise ValueError('Missing onboarding child')
        elif found:
            row[id_key] = found['id']
        return found

    def directory(self):
        """Small read-only directory, independent of expensive dashboard builds."""
        from .collection_service import stamp
        from .competitor_registry import build_targets
        runs = []
        database = self.project / 'data/monitor.sqlite3'
        if database.is_file():
            # Project only identity/summary fields. Never load snapshot bytes,
            # product rows or images into this navigation endpoint.
            connection = sqlite3.connect(database.as_uri() + '?mode=ro', uri=True, timeout=3)
            try:
                connection.row_factory = sqlite3.Row
                connection.execute('PRAGMA query_only = ON')
                rows = connection.execute('''
                    SELECT r.run_id, r.shop_id, r.source_url, r.status,
                           r.observed_from, r.observed_to, r.observed_to_epoch,
                           r.end_boundary_observed,
                           COALESCE(json_extract(r.snapshot_json, '$.shopName'), s.shop_name) AS shop_name,
                           json_extract(r.snapshot_json, '$.sourceUrl') AS snapshot_source,
                           json_extract(r.snapshot_json, '$.shopId') AS snapshot_shop_id,
                           json_extract(r.snapshot_json, '$.mallId') AS snapshot_mall_id,
                           (SELECT count(*) FROM observations o WHERE o.run_id = r.run_id) AS card_count
                    FROM runs r JOIN shops s ON s.shop_id = r.shop_id
                ''')
                for item in rows:
                    run = dict(item)
                    run['snapshot_metadata'] = {
                        key: value for key, value in (
                            ('sourceUrl', run.pop('snapshot_source') or run['source_url']),
                            ('shopId', run.pop('snapshot_shop_id')),
                            ('mallId', run.pop('snapshot_mall_id'))) if value is not None
                    }
                    runs.append(run)
            finally:
                connection.close()
        with self.collection.lock:
            targets, _ = build_targets(self.project, runs)
            latest = {}
            for row in self.rows:
                latest[row['target_id']] = self.public(row, lightweight=True)
            onboardings = list(latest.values())
            for target in targets:
                own = latest.get(target['target_id'])
                if own is None and target.get('shop_id') and not target.get('identity_conflict'):
                    own = next((row for row in reversed(onboardings)
                                if row.get('shop_id') == target['shop_id']
                                and row.get('resolved_target_id') == target['target_id']), None)
                target['onboarding'] = deepcopy(own)
                latest_job = next((job for job in reversed(self.collection.state['jobs'])
                                   if target.get('shop_id') and not target.get('identity_conflict')
                                   and job.get('shop_id') == target['shop_id'] and job['kind'] == 'shop'), None)
                target['latest_collection'] = ({key: deepcopy(latest_job[key]) for key in (
                    'id', 'shop_id', 'shop_name', 'kind', 'status', 'message', 'reason',
                    'created_at', 'started_at', 'ended_at', 'progress', 'image_summary',
                    'dashboard_built', 'onboarding_id') if key in latest_job} if latest_job else None)
            return {'status': 'ok', 'targets': targets, 'onboardings': onboardings,
                    'updated_at': stamp(self.collection.clock())}

    def verify(self, row, child):
        from .collection_service import atomic, public_shop_entry_url
        if (child.get('verify_storefront') is not True or child.get('url') != row['original_url']
                or child.get('verification_worker_completed') is not True):
            raise ValueError('Storefront worker completion not verified')
        directory = self.collection.directory / child['id']
        result, digest = read_evidence(self.project, directory / 'browser_result.json')
        page, page_digest = read_evidence(self.project, directory / 'public_page.json')
        if (digest != child.get('verification_result_sha256') or page_digest != child.get('verification_page_sha256')
                or result.get('status') != 'complete'):
            raise ValueError('Storefront evidence changed or incomplete')
        evidence = result.get('intakeEvidence')
        if not isinstance(evidence, dict) or set(evidence) != {'userShareUrl', 'shopName', 'sourceUrl', 'header', 'selectedMarkup', 'observedAt'}:
            raise ValueError('Storefront identity evidence missing')
        if any(not isinstance(value, str) or not value for value in evidence.values()):
            raise ValueError('Storefront identity evidence has invalid fields')
        if evidence['userShareUrl'] != row['original_url'] or page.get('url') != evidence['sourceUrl']:
            raise ValueError('Storefront evidence source differs')
        public_shop_entry_url(evidence['sourceUrl'])
        resolved = _target(evidence['shopName'], evidence['sourceUrl'])
        if resolved['identity_status'] != 'stable_url':
            raise ValueError('Storefront stable identity missing')
        observed = datetime.fromisoformat(evidence['observedAt'].replace('Z', '+00:00'))
        started = datetime.fromisoformat(child['started_at'].replace('Z', '+00:00'))
        if observed.tzinfo is None or observed < started or observed > datetime.now(timezone.utc):
            raise ValueError('Storefront evidence is not from this verification')
        evidence_path = directory / 'intake_evidence.json'
        if evidence_path.exists():
            if read_evidence(self.project, evidence_path)[0] != evidence:
                raise ValueError('Saved intake evidence differs')
        else:
            atomic(evidence_path, evidence)
        receipt = record_intake(self.project, evidence_path, row['original_url'], resolved['shop_id'])
        if (receipt.get('target_id') != resolved['target_id'] or receipt.get('shop_id') != resolved['shop_id']
                or receipt.get('status') not in ('recorded', 'unchanged')):
            raise ValueError('Verified intake receipt differs')
        if (read_evidence(self.project, directory / 'browser_result.json')[1] != digest
                or read_evidence(self.project, directory / 'public_page.json')[1] != page_digest):
            raise ValueError('Storefront evidence changed during registration')
        # The original registration is immutable. Navigation uses the resolved
        # target; the short-link target is not silently rewritten or merged.
        row.update(resolved_target_id=resolved['target_id'], shop_id=resolved['shop_id'],
                   shop_name=evidence['shopName'], intake_receipt_sha256=receipt['receipt_sha256'])

    def advance(self):
        """Called inside the collection lock, never from a client polling loop."""
        from .collection_service import TERMINAL, confirmed_shop_release
        for row in self.rows:
            if row['status'] not in ACTIVE:
                continue
            before = deepcopy(row)
            try:
                target, digest = self.target(row['target_id'])
                if digest != row['target_sha256'] or target.get('source_url') != row['original_url']:
                    raise ValueError('Original registered target changed')
                capture = self.child(row, 'shop', 'collection_job_id')
                probe = self.child(row, 'probe', 'verification_job_id')
                child = capture or probe
                if row.get('cancel_requested'):
                    if child and child['status'] == 'running':
                        continue
                    self.stop(row, 'cancelled', 'operator_cancelled', '接入已取消；已读取部分与历史保留。')
                    continue
                if capture:
                    if capture['shop_id'] != row.get('shop_id'):
                        raise ValueError('Collected shop differs from verified storefront')
                    if capture['status'] in TERMINAL:
                        status = capture['status']
                        reason = capture.get('reason')
                        message = capture.get('message') or '采集已结束，请核对实际保存范围。'
                        if status in ('complete', 'partial') and not confirmed_shop_release(capture, capture):
                            status, reason = 'manual_review', 'release_receipt_unconfirmed'
                            message = '采集结果未通过保存收据核验；原数据与本轮证据保留。'
                        elif status in ('complete', 'partial') and capture.get('dashboard_built') is not True:
                            status, reason = 'manual_review', reason or 'dashboard_build_unconfirmed'
                            message = '数据已保存，但面板更新尚未确认；请核对原采集任务，不重复采集。'
                        self.stop(row, status, reason or 'collection_finished', message)
                        row['stage'] = 'finished'
                    else:
                        row.update(status='running', stage='collect_shop', message=capture.get('message') or '正在采集已核验店铺。')
                    continue
                if probe is None:
                    code, response = self.collection.verify_storefront(row['original_url'], onboarding_id=row['id'])
                    if code != 202:
                        self.stop(row, 'failed', 'verification_not_queued', response.get('message') or '店铺核验未启动。')
                        continue
                    row['verification_job_id'] = response['job']['id']
                    row.update(status='queued', stage='verify_storefront')
                    continue
                if probe.get('cancel_requested'):
                    if probe['status'] == 'running':
                        continue
                    self.stop(row, 'cancelled', 'operator_cancelled', '核验已取消，未发起采集。')
                    continue
                if probe['status'] not in TERMINAL:
                    row.update(status=probe['status'], stage='verify_storefront', message=probe.get('message') or '正在核验店铺。')
                    continue
                if probe['status'] != 'complete':
                    self.stop(row, probe['status'], probe.get('reason') or 'verification_stopped', probe.get('message') or '店铺核验停止，未发起采集。')
                    continue
                row.update(status='running', stage='register_storefront', message='店铺已读取，正在核验并保存身份凭证。')
                self.verify(row, probe)
                if not row.get('collection_requested_at'):
                    from .collection_service import stamp
                    # Includes unfinished legacy rows: loading old state must
                    # not create a capture without the new explicit action.
                    row.update(status='ready', stage='ready', mode='identity_only',
                               identified_at=stamp(self.collection.clock()),
                               ended_at=stamp(self.collection.clock()),
                               message='店铺已识别并加入店铺列表，点击开始采集后读取商品。')
                    continue
                # Save the resolved identity before enqueueing; start's internal
                # onboarding ID reconciles a crash after the child was saved.
                self.collection._save()
                code, response = self.collection.start({'shop_id': row['shop_id'], 'kind': 'shop', 'entry_url': row['original_url']}, onboarding_id=row['id'])
                if code != 202 or not isinstance(response.get('job'), dict):
                    self.stop(row, 'manual_review', 'collection_not_queued', response.get('message') or '身份已核验，但采集未启动。')
                    continue
                capture = response['job']
                if capture.get('shop_id') != row['shop_id'] or capture.get('kind') != 'shop':
                    raise ValueError('Unexpected collection child scope')
                row.update(collection_job_id=capture['id'], status='running', stage='collect_shop', message='店铺身份已核验，等待全店采集。')
            except (OSError, ValueError, TypeError, KeyError, RuntimeError):
                self.stop(row, 'manual_review', 'onboarding_evidence_unconfirmed', '接入证据或状态未通过核验；已保存记录保留，未重复发起采集。')
            finally:
                if row != before:
                    self.collection._save()
