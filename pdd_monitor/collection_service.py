"""Durable per-shop local collection jobs. One browser worker, explicit schedules.

The worker owns capture_control and the existing paired-backup release pipeline.
The service never treats queued/running jobs as new business observations.
"""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import threading
import time
import uuid
from urllib.parse import parse_qs, urlsplit

from .capture_control import _resolve_shop
from . import capture_control

BEIJING = timezone(timedelta(hours=8))
TERMINAL = {'complete', 'partial', 'needs_login', 'needs_url', 'needs_browser', 'manual_review', 'failed', 'cancelled', 'interrupted', 'missed'}
JOB_SCOPE_FIELDS = ('id', 'shop_id', 'shop_name', 'kind', 'observation_id', 'source_run_id',
                    'browser_mode', 'entry_url', 'entry_revision', 'trigger', 'created_at',
                    'verify_storefront', 'onboarding_id')


def scoped_worker_result(job, result):
    """A worker return describes progress; it cannot rebind the requested target."""
    conflicts = [key for key in JOB_SCOPE_FIELDS if key in result
                 and (key not in job or type(result[key]) is not type(job[key]) or result[key] != job[key])]
    if conflicts:
        return {'status': 'manual_review', 'reason': 'worker_target_conflict',
                'conflicting_fields': conflicts,
                'message': '执行结果与指定采集店铺或原卡范围不一致；目标未改写，保留证据待复核。'}
    return {key: value for key, value in result.items() if key not in JOB_SCOPE_FIELDS}


def released_image_summary(receipt, summary=None):
    """Use independently validated committed references, never worker status alone."""
    actual = receipt.get('new_run') or {}
    total, saved = actual.get('cards'), actual.get('image_refs')
    if (receipt.get('ok') is not True or receipt.get('status') != 'finished'
            or receipt.get('snapshot_status') != 'complete' or actual.get('status') != 'complete'
            or type(total) is not int or type(saved) is not int or not 0 <= saved <= total or total <= 0):
        return None
    expected = {'total': total, 'saved': saved, 'missing': total - saved,
                'status': 'complete' if saved == total else 'partial'}
    if summary is not None:
        if (not isinstance(summary, dict)
                or any(type(summary.get(key)) is not int for key in ('total', 'saved', 'missing'))
                or any(summary.get(key) != value for key, value in expected.items())):
            return None
    return {**(summary or {}), **expected}


def confirmed_shop_release(job, result):
    """A missing-image result may publish complete card data, never a partial scan."""
    receipt = result.get('receipt') or {}
    if scoped_worker_result(job, result).get('reason') == 'worker_target_conflict':
        return False
    if not isinstance(receipt, dict):
        return False
    capture_id, digest = result.get('capture_job_id'), receipt.get('snapshot_sha256')
    journal = receipt.get('journal') or {}
    if not isinstance(journal, dict):
        return False
    session = journal.get('session') or {}
    if not isinstance(session, dict) or not isinstance(session.get('snapshot') or {}, dict):
        return False
    return (job.get('kind') == 'shop' and result.get('status') in ('complete', 'partial')
            and isinstance(capture_id, str) and re.fullmatch(r'capture_[0-9a-f]{32}', capture_id)
            and (not job.get('capture_job_id') or job['capture_job_id'] == capture_id)
            and receipt.get('job_id') == capture_id
            and isinstance(digest, str) and re.fullmatch(r'[0-9a-f]{64}', digest)
            and receipt.get('shop_id') == job.get('shop_id')
            and receipt.get('run_id') == 'run_' + digest[:24]
            and journal.get('job_id') == capture_id and journal.get('shop_id') == job.get('shop_id')
            and journal.get('run_id') == receipt['run_id'] and bool(journal.get('attempt_id'))
            and journal.get('accepted_into_history') is True and journal.get('journal_status') == 'complete'
            and session.get('phase') == 'complete' and session.get('importReady') is True
            and (session.get('snapshot') or {}).get('sha256') == digest
            and released_image_summary(receipt, result.get('image_summary')) is not None)


def verify_shop_release(project, job, result):
    """Recheck the same shop, closed attempt, sealed bytes and committed images."""
    import hashlib
    from contextlib import closing
    from .store import _connect_readonly, shop_identity_evidence
    if not confirmed_shop_release(job, result):
        raise ValueError('Release receipt does not identify this sealed shop attempt')
    receipt = result['receipt']
    project = Path(project).resolve()
    if not isinstance(job.get('id'), str) or not re.fullmatch(r'collect_[0-9a-f]{32}', job['id']):
        raise ValueError('Release receipt needs its local collection request ID')
    request_path = project / 'state/local_collection' / job['id'] / 'request.json'
    capture_control._no_redirect(project, request_path)
    request = capture_control._read(request_path)
    if (request.get('kind') != 'shop'
            or (request.get('capture') or {}).get('job_id') != result['capture_job_id']):
        raise ValueError('Release receipt belongs to another local collection request')
    current = capture_control.status(project, result['capture_job_id'])
    saved = receipt['journal']
    session = current.get('session') or {}
    if (any(current.get(key) != saved.get(key) for key in ('job_id', 'shop_id', 'run_id', 'attempt_id'))
            or current.get('accepted_into_history') is not True or current.get('journal_status') != 'complete'
            or session.get('phase') != 'complete' or session.get('importReady') is not True
            or (session.get('snapshot') or {}).get('sha256') != receipt['snapshot_sha256']):
        raise ValueError('Release receipt differs from its current closed capture attempt')
    sealed = session.get('snapshot') or {}
    if sealed.get('file') not in ('snapshot.json', 'snapshot.recovered.json'):
        raise ValueError('Release receipt has no recognized sealed snapshot')
    snapshot_path = project / 'sources' / result['capture_job_id'] / sealed['file']
    capture_control._no_redirect(project, snapshot_path)
    sealed_bytes = snapshot_path.read_bytes()
    if hashlib.sha256(sealed_bytes).hexdigest() != receipt['snapshot_sha256']:
        raise ValueError('Release receipt differs from the actual sealed snapshot bytes')
    with closing(_connect_readonly(Path(project) / 'data')) as db:
        run = db.execute('SELECT * FROM runs WHERE run_id=?', (receipt['run_id'],)).fetchone()
        if (run is None or run['shop_id'] != job['shop_id'] or run['status'] != 'complete'
                or run['end_boundary_observed'] != 1 or run['snapshot_sha256'] != receipt['snapshot_sha256']
                or bytes(run['snapshot_bytes']) != sealed_bytes):
            raise ValueError('Release receipt differs from the committed shop run')
        identity = shop_identity_evidence(json.loads(run['snapshot_json']))
        if identity['shop_id'] != job['shop_id']:
            raise ValueError('Committed snapshot belongs to another shop')
    actual = current_run_image_summary(project, job['shop_id'], receipt['run_id'], receipt['new_run']['cards'])
    if released_image_summary(receipt, actual) is None:
        raise ValueError('Release receipt differs from committed original-card images')
    return actual


def current_run_image_summary(project, shop_id, run_id, expected_cards):
    """Read-only verification of later image attachments to the same immutable run."""
    from contextlib import closing
    import hashlib
    from .store import _connect_readonly, _image_mime, MAX_IMAGE_BYTES
    with closing(_connect_readonly(Path(project) / 'data')) as db:
        run = db.execute('SELECT shop_id,status FROM runs WHERE run_id=?', (run_id,)).fetchone()
        if not run or run['shop_id'] != shop_id or run['status'] != 'complete':
            raise ValueError('Image recovery belongs to another or incomplete run')
        rows = db.execute('''SELECT o.observation_id,o.image_url,t.status,t.run_id task_run,t.source_url task_url,
            t.asset_sha256 task_sha,l.run_id link_run,l.source_url link_url,l.asset_sha256 link_sha,
            a.sha256,a.data,a.mime,a.byte_count FROM observations o
            LEFT JOIN image_tasks t ON t.observation_id=o.observation_id
            LEFT JOIN images.source_links l ON l.observation_id=o.observation_id AND l.run_id=o.run_id
            LEFT JOIN images.assets a ON a.sha256=l.asset_sha256 WHERE o.run_id=?''', (run_id,)).fetchall()
        if (type(expected_cards) is not int or expected_cards <= 0 or len(rows) != expected_cards
                or len({row['observation_id'] for row in rows}) != len(rows)):
            raise ValueError('Image recovery card count differs from the original release')
        saved, checked = 0, set()
        for row in rows:
            if row['status'] != 'saved':
                if row['link_sha'] or row['task_sha']:
                    raise ValueError('Unconfirmed task contains an image reference')
                continue
            if (row['task_run'] != run_id or row['link_run'] != run_id or not row['image_url']
                    or row['task_url'] != row['image_url'] or row['link_url'] != row['image_url']
                    or not row['sha256'] or row['task_sha'] != row['sha256'] or row['link_sha'] != row['sha256']):
                raise ValueError('Saved image is not bound to the original card')
            if row['sha256'] not in checked:
                content = bytes(row['data'])
                if (not 0 < len(content) <= MAX_IMAGE_BYTES or len(content) != row['byte_count']
                        or hashlib.sha256(content).hexdigest() != row['sha256'] or _image_mime(content) != row['mime']):
                    raise ValueError('Saved image bytes failed independent verification')
                checked.add(row['sha256'])
            saved += 1
    return {'total': len(rows), 'saved': saved, 'missing': len(rows) - saved,
            'status': 'complete' if saved == len(rows) else 'partial'}


def public_shop_entry_url(value):
    """A navigation hint only: accepting it never establishes shop identity."""
    if (not isinstance(value, str) or not 1 <= len(value) <= 4096
            or value != value.strip() or re.search(r'[\x00-\x20\x7f]', value)
            or re.search(r'%(?![0-9A-Fa-f]{2})', value)):
        raise ValueError('店铺入口无效')
    url = urlsplit(value)
    fields = {'ps', 'mall_id', 'mall_sn', 'ts', 'refer_share_channel', 'msn',
              'refer_share_id', 'refer_share_uin', 'decrypt_mall_sn', 'st_e_sn',
              'force_use_web_bundle', '_x_org', '_x_query', '_x_share_id',
              'refer_page_name', 'refer_page_id', 'refer_page_sn', 'page_id',
              'mall_tab_key', 'sort_type', 'is_back'}
    query = parse_qs(url.query, keep_blank_values=True, max_num_fields=32, errors='strict')
    if (url.scheme != 'https' or url.netloc != 'mobile.yangkeduo.com'
            or url.path != '/mall_page.html' or url.fragment
            or not query or not set(query) <= fields
            or not set(query) & {'ps', 'mall_id', 'mall_sn'}
            or any(len(items) != 1 or not 1 <= len(items[0]) <= 512
                   or re.search(r'[\x00-\x1f\x7f]', items[0]) for items in query.values())):
        raise ValueError('仅接受拼多多公开店铺分享链接，不接受商品、登录或凭据字段')
    for key in ('ps', 'mall_sn'):
        if key in query and not re.fullmatch(r'[A-Za-z0-9_+=/-]{1,512}', query[key][0]):
            raise ValueError('店铺分享标识无效')
    if 'mall_id' in query and not re.fullmatch(r'[0-9]{1,32}', query['mall_id'][0]):
        raise ValueError('店铺分享标识无效')
    return value


def public_probe_url(value):
    """Only ordinary public product/shop links; no arbitrary navigation targets."""
    if not isinstance(value, str) or len(value) > 2048:
        raise ValueError('链接无效')
    url = urlsplit(value)
    query = parse_qs(url.query, keep_blank_values=True)
    expected = 'mall_id' if url.path == '/mall_page.html' else 'goods_id'
    share_fields = {'ps', expected, 'mall_sn', 'ts', 'refer_share_channel', 'msn',
                    'refer_share_id', 'refer_share_uin', 'decrypt_mall_sn', 'st_e_sn', 'force_use_web_bundle'}
    is_share = (url.path == '/mall_page.html' and 'mall_sn' in query
                and set(query) <= share_fields
                and all(len(items) == 1 and re.fullmatch(r'[A-Za-z0-9_+=/-]{1,512}', items[0]) for items in query.values()))
    is_simple = (set(query) in ({'ps'}, {expected})
                 and all(len(items) == 1 and re.fullmatch(r'[A-Za-z0-9_-]{1,128}', items[0]) for items in query.values()))
    if (url.scheme != 'https' or url.netloc != 'mobile.yangkeduo.com'
            or url.path not in ('/mall_page.html', '/goods.html', '/goods1.html')
            or url.fragment or not (is_simple or is_share)):
        raise ValueError('仅接受拼多多公开商品或店铺分享链接')
    return value


def browser_mode(project):
    path = Path(project) / 'state/local_collection/browser_policy.json'
    if not path.exists():
        return 'local_chrome'
    mode = json.loads(path.read_text(encoding='utf-8'))['mode']
    if mode not in ('local_chrome', 'codex_iab'):
        raise ValueError('Unknown browser policy')
    return mode


def now():
    return datetime.now(timezone.utc)


def stamp(value=None):
    return (value or now()).isoformat()


def atomic(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        with temp.open('x', encoding='utf-8') as stream:
            json.dump(value, stream, ensure_ascii=False, allow_nan=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)


def next_due(schedule, after):
    """Strictly future Beijing execution; no historical catch-up on save."""
    if not schedule.get('enabled'):
        return None
    if schedule['mode'] == 'once':
        point = datetime.fromisoformat(schedule['at']).replace(tzinfo=BEIJING)
        return stamp(point.astimezone(timezone.utc)) if point > after else None
    local = after.astimezone(BEIJING)
    choices = []
    for clock in schedule['times']:
        hour, minute = map(int, clock.split(':'))
        point = local.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if point <= local:
            point += timedelta(days=1)
        choices.append(point)
    return stamp(min(choices).astimezone(timezone.utc))


def validate_schedule(payload, at):
    if set(payload) != {'shop_id', 'revision', 'enabled', 'mode', 'times', 'at'}:
        raise ValueError('定时字段无效')
    if type(payload['enabled']) is not bool or type(payload['revision']) is not int:
        raise ValueError('定时版本或开关无效')
    mode, times, point = payload['mode'], payload['times'], payload['at']
    if mode not in ('daily', 'once') or not isinstance(times, list) or not isinstance(point, str):
        raise ValueError('定时模式无效')
    if mode == 'daily':
        if not 1 <= len(times) <= 6 or len(set(times)) != len(times) or any(not isinstance(t, str) or not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d', t) for t in times):
            raise ValueError('每天设置1至6个不同的北京时间')
        point = ''
    else:
        if not re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}', point):
            raise ValueError('请设置有效的一次执行时间')
        selected = datetime.fromisoformat(point).replace(tzinfo=BEIJING)
        if payload['enabled'] and not at < selected <= at + timedelta(days=366):
            raise ValueError('一次执行时间须在未来一年内')
        times = []
    return {'enabled': payload['enabled'], 'mode': mode, 'times': sorted(times), 'at': point, 'timezone': 'Asia/Shanghai'}


class CollectionService:
    def __init__(self, project, publisher=None, *, runner=None, clock=now, autostart=True):
        self.project = Path(project).resolve()
        self.path = self.project / 'state/local_collection/settings.json'
        self.directory = self.path.parent
        self.lock = threading.RLock()
        self.wake = threading.Event()
        self.clock = clock
        self.publisher = publisher
        self.runner = runner or self._execute
        self._image_verifications = {}
        self.state = json.loads(self.path.read_text(encoding='utf-8')) if self.path.exists() else {'version': 1, 'schedules': {}, 'jobs': []}
        if self.state.get('version') != 1:
            raise ValueError('Unsupported local collection settings')
        self.state.setdefault('entries', {})
        self.active = None
        # A prior process may still hold capture_control. Never forge its owner or delete locks.
        changed = False
        for job in self.state['jobs']:
            if job.get('browser_mode') == 'codex_iab':
                if job['status'] == 'host_claiming':
                    job.update(status='manual_review', host_status_unavailable=True,
                               message='接手过程被中断；请复核采集日志和锁，不能重复接手。')
                    changed = True
                continue
            if job['status'] == 'running':
                job.update(status='interrupted', message='服务中断；旧采集证据和锁保留，请复核后重新采集。', ended_at=stamp(self.clock()))
                changed = True
        if changed:
            self._save()
        from .shop_onboarding import ShopOnboarding
        self.onboarding = ShopOnboarding(self)
        if autostart:
            threading.Thread(target=self._loop, daemon=True, name='pdd-local-collection').start()

    def _save(self):
        atomic(self.path, self.state)

    def _shop(self, shop_id):
        if not isinstance(shop_id, str) or not re.fullmatch(r'shop_[0-9a-f]{24}', shop_id):
            raise ValueError('店铺编号无效')
        return _resolve_shop(self.project, shop_id)

    def _recovered_images(self, job):
        receipt, recovery = job.get('receipt') or {}, job.get('image_recovery') or {}
        invalid = {'verified': False, 'status': 'unknown'}
        if (not re.fullmatch(r'collect_[0-9a-f]{32}', job.get('id', ''))
                or receipt.get('ok') is not True or receipt.get('status') != 'finished'
                or receipt.get('shop_id') != job.get('shop_id') or recovery.get('run_id') != receipt.get('run_id')):
            return invalid
        try:
            evidence = self.directory / job['id'] / 'image_recovery.json'
            paths = [self.project / 'data' / name for name in ('monitor.sqlite3', 'images.sqlite3')]
            # A changed database or append receipt triggers a fresh SHA/MIME check.
            fingerprints = tuple((path.stat().st_mtime_ns, path.stat().st_size) for path in [*paths, evidence])
            key = (fingerprints, json.dumps(recovery, sort_keys=True), receipt.get('run_id'), job.get('shop_id'),
                   (receipt.get('new_run') or {}).get('cards'))
            previous = self._image_verifications.get(job['id'])
            if previous and previous[0] == key:
                return deepcopy(previous[1])
            if json.loads(evidence.read_text(encoding='utf-8')) != recovery:
                return invalid
            summary = current_run_image_summary(self.project, job['shop_id'], receipt['run_id'],
                                                (receipt.get('new_run') or {}).get('cards'))
            if fingerprints != tuple((path.stat().st_mtime_ns, path.stat().st_size) for path in [*paths, evidence]):
                return invalid
            verified = {'verified': True, 'basis': 'current_run_image_refs', 'shop_id': job['shop_id'],
                        'run_id': receipt['run_id'], **summary}
            self._image_verifications[job['id']] = (key, verified)
            return deepcopy(verified)
        except (OSError, ValueError, TypeError, KeyError, sqlite3.Error):
            return invalid

    def _public_job(self, job):
        if job.get('browser_mode') != 'codex_iab':
            result = deepcopy(job)
            result.pop('entry_url', None)
            if job.get('kind') == 'shop' and job.get('image_recovery'):
                verification = self._recovered_images(job)
                result['image_verification'] = verification
                if verification.get('verified'):
                    result['image_summary'] = {key: verification[key] for key in ('total', 'saved', 'missing', 'status')}
                    if verification['missing']:
                        result.update(status='partial', reason='images_missing')
            result.update(self._recovery(result))
            return result
        result = {key: deepcopy(job[key]) for key in (
            'id', 'shop_id', 'shop_name', 'kind', 'status', 'trigger', 'created_at',
            'claimed_at', 'ended_at', 'capture_job_id', 'browser_mode', 'message',
            'host_status_unavailable', 'superseded_by') if key in job}
        result.update(execution_mode='codex_iab_host', ai_requests=None,
                      website_collection_started=False, accepted_into_history=False,
                      dashboard_built=False, can_cancel=job['status'] == 'awaiting_host')
        result['handoff_text'] = (
            f"请接手采集请求 {job['id']}，店铺 {job['shop_name']}（{job['shop_id']}）。"
            '只使用 Codex 右侧浏览器，先核对店铺后通过 collection_service host-claim 接手。'
            '用固定程序采集上新栏目全部原卡，不读取 SKU；完整性校验、双库备份和副本验收通过后更新数据舱。'
            '若无法完整采集则保留证据并说明原因，不覆盖已有完整清单。')
        if job['status'] != 'host_claimed':
            return result
        try:
            evidence = capture_control.status(self.project, job['capture_job_id'])
            if evidence.get('shop_id') != job['shop_id']:
                raise ValueError('Wrong capture scope')
            # Follow only an existing exclusive retry claim, never infer a child
            # from timestamps or scan unrelated jobs. The original request stays immutable.
            current_id = job['capture_job_id']
            for _ in range(capture_control.MAX_FRESH_RETRIES + 1):
                claim_path = capture_control._job_dir(self.project, current_id) / 'retry_claim.json'
                capture_control._no_redirect(self.project, claim_path)
                if not claim_path.exists():
                    break
                claim = capture_control._read(claim_path)
                child_id = claim.get('child_job_id')
                if claim.get('parent_job_id') != current_id or not isinstance(child_id, str):
                    raise ValueError('Invalid retry claim')
                child = capture_control.status(self.project, child_id)
                parent_index, child_index = evidence.get('fresh_retry_index'), child.get('fresh_retry_index')
                if (child.get('shop_id') != job['shop_id'] or child.get('parent_job_id') != current_id
                        or type(parent_index) is not int or type(child_index) is not int
                        or child_index != parent_index + 1 or child_index > capture_control.MAX_FRESH_RETRIES
                        or child.get('attempt_id') != claim.get('child_attempt_id')):
                    raise ValueError('Retry does not belong to this request')
                current_id, evidence = child_id, child
            else:
                raise ValueError('Retry chain exceeds the existing budget')
            result['capture_job_id'] = current_id
            session = evidence.get('session') or {}
            progress = session.get('progress') or {}
            counts = {key: progress.get(key, 0) for key in ('cards', 'verifiedBatches', 'pending')}
            if any(type(value) is not int or value < 0 for value in counts.values()):
                raise ValueError('Invalid capture counts')
            phase = session.get('phase')
            if phase not in (None, 'ready', 'running', 'complete', 'partial', 'needs_login', 'manual_review', 'failed'):
                raise ValueError('Unknown capture phase')
            result['progress'] = {'cards': counts['cards'], 'verified_batches': counts['verifiedBatches'],
                                  'pending': counts['pending'], 'phase': phase or 'prepared'}
            result['website_collection_started'] = evidence.get('website_collection_started') is True
            accepted = evidence.get('accepted_into_history') is True and bool(evidence.get('run_id'))
            journal_status = evidence.get('journal_status')
            if accepted and journal_status in ('complete', 'partial'):
                result.update(status=journal_status, accepted_into_history=True,
                              run_id=evidence['run_id'], message='本轮已入库；面板是否已更新请刷新核对。')
            elif phase == 'complete':
                result.update(status='validating', message='网页读取已封存，等待完整性校验与入库。')
            elif phase in ('partial', 'needs_login', 'manual_review', 'failed') or journal_status in ('failed', 'partial'):
                result.update(status=phase if phase in TERMINAL else 'manual_review',
                              message='本轮采集已停止，需在当前对话复核；原完整清单保留。')
                if journal_status == 'running':
                    result.update(host_attempt_open=True,
                                  message='网页采集已停止，但宿主日志尚未关闭；请在当前对话封存并结束后再重采。')
            elif phase == 'running':
                result.update(status='running', message='当前对话正在推进右侧浏览器采集。')
            elif phase in (None, 'ready') and journal_status == 'running':
                result.update(status='awaiting_browser', message='Codex 已接手，等待开始读取右侧店铺页。')
            else:
                raise ValueError('Capture state unavailable')
        except (OSError, ValueError, TypeError, KeyError):
            result.update(status='manual_review', host_status_unavailable=True,
                          message='无法核对宿主采集证据；请在当前对话复核，不能重复发起。')
        return result

    @staticmethod
    def _recovery(job):
        reason = job.get('reason')
        if job.get('status') == 'needs_login':
            reason = 'login_required'
        elif job.get('status') == 'needs_browser':
            reason = 'connection_required'
        elif job.get('status') == 'needs_url' and reason is None:
            reason = 'entry_unavailable'
        reasons = {'login_required', 'entry_unavailable', 'zero_products',
                   'identity_mismatch', 'access_restricted', 'connection_required'}
        required = job.get('status') in TERMINAL and reason in reasons
        return {'recovery_required': required, 'recovery_reason': reason if required else None}

    def _public_entry(self, shop_id):
        entry = self.state['entries'].get(shop_id) or {}
        return {'revision': entry.get('revision', 0), 'configured': bool(entry.get('entry_url')),
                'verification': entry.get('verification', 'unverified'),
                'updated_at': entry.get('updated_at'), 'verified_at': entry.get('verified_at')}

    def _entry_value(self, shop_id, url):
        previous = self.state['entries'].get(shop_id) or {}
        if previous.get('entry_url') == url:
            return deepcopy(previous)
        return {'revision': previous.get('revision', 0) + 1, 'entry_url': url,
                'verification': 'unverified', 'updated_at': stamp(self.clock())}

    def update_entry(self, payload):
        if set(payload) != {'shop_id', 'entry_url', 'revision'} or type(payload['revision']) is not int or payload['revision'] < 0:
            raise ValueError('店铺入口字段或版本无效')
        shop_id = payload['shop_id']
        self._shop(shop_id)
        url = public_shop_entry_url(payload['entry_url'])
        with self.lock:
            previous = self.state['entries'].get(shop_id)
            if payload['revision'] != (previous or {}).get('revision', 0):
                return 409, {'message': '店铺入口已更新，请刷新后再保存。', 'entry': self._public_entry(shop_id)}
            self.state['entries'][shop_id] = self._entry_value(shop_id, url)
            try:
                self._save()
            except OSError:
                if previous is None:
                    self.state['entries'].pop(shop_id, None)
                else:
                    self.state['entries'][shop_id] = previous
                raise
            return 200, {'message': '本机入口已保存，下次采集时核对实际店铺；尚未采集或确认身份。',
                         'entry': self._public_entry(shop_id)}

    def _active(self, job):
        public = self._public_job(job)
        return public.get('host_status_unavailable') is True or public.get('host_attempt_open') is True or public['status'] in (
            'queued', 'running', 'awaiting_host', 'host_claiming', 'awaiting_browser', 'validating')

    def _observation(self, shop_id, observation_id):
        if type(observation_id) is not int or observation_id < 1:
            raise ValueError('原卡编号无效')
        from contextlib import closing
        import sqlite3
        with closing(sqlite3.connect((self.project / 'data/monitor.sqlite3').as_uri() + '?mode=ro', uri=True)) as db:
            db.row_factory = sqlite3.Row
            row = db.execute('SELECT o.*, r.shop_id FROM observations o JOIN runs r ON r.run_id=o.run_id WHERE o.observation_id=? AND r.shop_id=?', (observation_id, shop_id)).fetchone()
        if row is None:
            raise ValueError('此原卡不属于当前店铺')
        return dict(row)

    def status(self, shop_id, observation_id=None):
        self._shop(shop_id)
        with self.lock:
            jobs = [self._public_job(j) for j in self.state['jobs'] if j['shop_id'] == shop_id and (observation_id is None or j.get('observation_id') == observation_id)]
            for job in jobs:
                progress = self.directory / job['id'] / 'progress.json'
                if job.get('browser_mode') != 'codex_iab' and job['status'] == 'running' and progress.exists():
                    try:
                        job['progress'] = json.loads(progress.read_text(encoding='utf-8'))
                    except (OSError, ValueError):
                        pass
            schedule = deepcopy(self.state['schedules'].get(shop_id, {'revision': 0, 'enabled': False, 'mode': 'daily', 'times': ['08:00', '20:00'], 'at': '', 'timezone': 'Asia/Shanghai', 'next_at': None}))
            result = {'schedule': schedule, 'jobs': jobs[-12:][::-1], 'scheduler_running': True, 'poll_seconds': 15,
                      'message': '电脑和本机面板服务需运行；超过10分钟的错过任务记录为错过，不连续补跑。'}
            result['browser_mode'] = browser_mode(self.project)
            result['local_worker_available'] = result['browser_mode'] == 'local_chrome'
            # Availability means this local worker is configured, not that the
            # user has already accepted Chrome's per-connection permission.
            result['browser_connector'] = 'normal_chrome_auto_connect' if result['local_worker_available'] else None
            result['scheduler_running'] = result['local_worker_available']
            current_jobs = [job for job in jobs if job.get('kind') == 'shop'
                            and job.get('browser_mode', 'local_chrome') == result['browser_mode']]
            result['latest_job'] = deepcopy(current_jobs[-1]) if current_jobs else None
            sku_jobs = [job for job in jobs if job.get('kind') == 'sku_batch'
                        and job.get('browser_mode', 'local_chrome') == result['browser_mode']]
            result['latest_sku_batch_job'] = deepcopy(sku_jobs[-1]) if sku_jobs else None
            result.update(self._recovery(result['latest_job'] or {}))
            result['entry'] = self._public_entry(shop_id)
        if observation_id is not None:
            observation = self._observation(shop_id, observation_id)
            from .sku_store import read_latest, sku_eligible
            result['sku'] = read_latest(self.project, shop_id, observation_id)
            result['sku_eligibility'] = {'eligible': sku_eligible(observation),
                'reason': '仅采集销量 1–10 和超过 10 件的商品；未出单、模糊或缺失销量暂不采集。'}
            with self.lock:
                parents = [j for j in self.state['jobs'] if j.get('shop_id') == shop_id
                           and j.get('kind') in ('shop', 'sku_batch') and j.get('browser_mode') != 'codex_iab']
                parent = self._public_job(parents[-1]) if parents else None
                if parent and parent.get('status') == 'running':
                    path = self.directory / parent['id'] / 'progress.json'
                    try:
                        parent['progress'] = json.loads(path.read_text(encoding='utf-8'))
                    except (OSError, ValueError):
                        pass
                if parent and parent.get('progress'):
                    progress = parent['progress']
                    progress.update(completed=progress.get('sku_completed'), total=progress.get('sku_total'),
                                    current_observation_id=progress.get('sku_observation_id'))
                result['sku_pipeline'] = parent
        return result

    def schedule(self, payload):
        shop_id = payload.get('shop_id')
        self._shop(shop_id)
        selected = validate_schedule(payload, self.clock())
        if selected['enabled'] and browser_mode(self.project) == 'codex_iab':
            return 409, {'message': '右侧浏览器定时执行尚未接通，不能开启后台采集；原时间设置保留。'}
        with self.lock:
            previous = self.state['schedules'].get(shop_id, {'revision': 0})
            if payload['revision'] != previous['revision']:
                return 409, {'message': '设置已更新，请刷新后再保存。'}
            selected.update(revision=previous['revision'] + 1, updated_at=stamp(self.clock()), next_at=next_due(selected, self.clock()), blocked_by=None)
            self.state['schedules'][shop_id] = selected
            self._save()
            self.wake.set()
            return 200, {'message': '本店定时设置已保存。', 'schedule': deepcopy(selected)}

    def probe(self, payload):
        if set(payload) != {'url'}:
            raise ValueError('链接检查字段无效')
        return self._probe(public_probe_url(payload['url']))

    def verify_storefront(self, url, *, onboarding_id=None):
        """Internal identity check; the public probe API cannot enable it."""
        return self._probe(public_shop_entry_url(url), verify_storefront=True, onboarding_id=onboarding_id)

    def _probe(self, url, *, verify_storefront=False, onboarding_id=None):
        if browser_mode(self.project) == 'codex_iab':
            return 409, {'message': '已改用 Codex 右侧浏览器，请在当前对话发起页面检查；不启动外部浏览器。'}
        with self.lock:
            existing = next((j for j in self.state['jobs'] if j['kind'] == 'probe'
                             and j.get('url') == url and bool(j.get('verify_storefront')) == verify_storefront
                             and j.get('onboarding_id') == onboarding_id
                             and (j['status'] in ('queued', 'running') or onboarding_id is not None)), None)
            if existing:
                return 202, {'job': deepcopy(existing)}
            if sum(j['status'] in ('queued', 'running') for j in self.state['jobs']) >= 12:
                return 409, {'message': '采集队列已满'}
            job = {'id': 'collect_' + uuid.uuid4().hex, 'kind': 'probe', 'shop_id': None,
                   'url': url, 'status': 'queued', 'trigger': 'manual', 'created_at': stamp(self.clock()),
                   'message': '等待 Chrome 检查公开页面。'}
            if verify_storefront:
                job['verify_storefront'] = True
            if onboarding_id is not None:
                job['onboarding_id'] = onboarding_id
            self.state['jobs'].append(job)
            try:
                self._save()
            except OSError:
                self.state['jobs'].remove(job)
                raise
            self.wake.set()
            return 202, {'job': deepcopy(job)}

    def probe_status(self, job_id):
        if not isinstance(job_id, str) or not re.fullmatch(r'collect_[0-9a-f]{32}', job_id):
            raise ValueError('链接检查编号无效')
        with self.lock:
            job = next((j for j in self.state['jobs'] if j['kind'] == 'probe' and j['id'] == job_id), None)
            if not job:
                raise ValueError('链接检查不存在')
            return deepcopy(job)

    def onboard(self, payload):
        return self.onboarding.start(payload)

    def onboard_status(self, *, id=None, target_id=None):
        return self.onboarding.status(id=id, target_id=target_id)

    def onboard_cancel(self, payload):
        return self.onboarding.cancel(payload)

    def onboard_collect(self, payload):
        return self.onboarding.collect(payload)

    def shop_directory(self):
        return self.onboarding.directory()

    def start(self, payload, trigger='manual', *, onboarding_id=None):
        if (not {'shop_id', 'kind'} <= set(payload)
                or set(payload) - {'shop_id', 'kind', 'observation_id', 'entry_url', 'entry_revision'}
                or ('entry_revision' in payload and 'entry_url' not in payload)):
            raise ValueError('采集字段无效')
        shop = self._shop(payload['shop_id'])
        if payload['kind'] not in ('shop', 'sku', 'sku_batch') or (payload['kind'] == 'sku') != ('observation_id' in payload):
            raise ValueError('采集类型无效')
        if payload['kind'] == 'sku_batch' and ('entry_url' in payload or trigger != 'manual'):
            raise ValueError('SKU 补采使用已保存的本店入口，仅支持手动开始')
        if payload['kind'] == 'sku':
            if 'entry_url' in payload:
                raise ValueError('店铺入口不用于 SKU 采集')
            from .sku_store import sku_eligible
            observation = self._observation(payload['shop_id'], payload['observation_id'])
            if not sku_eligible(observation):
                return 409, {'message': '此商品没有精确的正销量，暂不采集 SKU。'}
        entry_url = public_shop_entry_url(payload['entry_url']) if 'entry_url' in payload else None
        if 'entry_revision' in payload and (type(payload['entry_revision']) is not int or payload['entry_revision'] < 0):
            raise ValueError('店铺入口版本无效')
        if entry_url is not None and trigger != 'manual':
            raise ValueError('新店铺入口只接受手动提交')
        mode = browser_mode(self.project)
        if mode == 'codex_iab' and (payload['kind'] != 'shop' or trigger != 'manual'):
            return 409, {'message': '右侧浏览器仅接受手动全店采集请求；SKU 与定时独立执行尚未开启。'}
        if mode == 'codex_iab' and entry_url is not None:
            return 409, {'message': '当前为右侧浏览器模式，新入口需由宿主核对后使用。'}
        with self.lock:
            if onboarding_id is not None:
                parent = next((row for row in self.onboarding.rows if row['id'] == onboarding_id), None)
                if (not parent or parent.get('shop_id') != payload['shop_id']
                        or parent.get('original_url') != entry_url or parent.get('cancel_requested')
                        or not parent.get('collection_requested_at')
                        or payload['kind'] != 'shop' or trigger != 'manual'):
                    raise ValueError('Invalid internal onboarding collection scope')
                child = next((j for j in self.state['jobs'] if j.get('onboarding_id') == onboarding_id and j['kind'] == 'shop'), None)
                if child:
                    return 202, {'job': self._public_job(child), 'entry': self._public_entry(payload['shop_id'])}
            active = [j for j in self.state['jobs'] if j['shop_id'] == payload['shop_id'] and self._active(j)]
            replaceable = [j for j in active if mode == 'local_chrome' and trigger == 'manual'
                           and payload['kind'] == 'shop' and j.get('browser_mode') == 'codex_iab'
                           and j['status'] == 'awaiting_host' and not j.get('capture_job_id')]
            existing = next((j for j in active if j not in replaceable), None)
            if existing:
                if onboarding_id is not None and existing.get('onboarding_id') != onboarding_id:
                    return 409, {'message': '本店已有独立采集任务，请先查看该任务；本次接入不重复采集。', 'job': self._public_job(existing)}
                if entry_url is not None and existing.get('entry_url') != entry_url:
                    return 409, {'message': '本店已有未结束任务；不能在采集中替换其入口。', 'job': self._public_job(existing)}
                return 202, {'message': '本店已有未结束请求，请查看当前任务。', 'job': self._public_job(existing)}
            previous_entry = self.state['entries'].get(payload['shop_id'])
            entry_revision = (previous_entry or {}).get('revision', 0)
            if 'entry_revision' in payload and payload['entry_revision'] != entry_revision:
                return 409, {'message': '店铺入口已更新，请刷新后重新开始。', 'entry': self._public_entry(payload['shop_id'])}
            if sum(self._active(j) for j in self.state['jobs']) - len(replaceable) >= 12:
                return 409, {'message': '采集队列已满，请等待或取消任务。'}
            message = '请求已保存，等待 Codex 接手；尚未开始读取网页。' if mode == 'codex_iab' else '等待本机采集器；与其他店铺串行执行。'
            job_payload = {key: value for key, value in payload.items() if key not in ('entry_url', 'entry_revision')}
            job = {'id': 'collect_' + uuid.uuid4().hex, **job_payload, 'shop_name': shop['shop_name'],
                   'browser_mode': mode, 'status': 'awaiting_host' if mode == 'codex_iab' else 'queued',
                   'trigger': trigger, 'created_at': stamp(self.clock()), 'message': message}
            if onboarding_id is not None:
                job['onboarding_id'] = onboarding_id
            if payload['kind'] == 'sku_batch':
                from .collection_worker import latest_complete_sku_run
                job.update(source_run_id=latest_complete_sku_run(self.project, payload['shop_id']),
                           message='等待补采当前完整清单的 SKU；已完整商品将跳过。')
                if previous_entry:
                    job.update(entry_url=previous_entry['entry_url'], entry_revision=previous_entry['revision'])
                message = job['message']
            if mode == 'local_chrome' and payload['kind'] == 'shop':
                selected_entry = self._entry_value(payload['shop_id'], entry_url) if entry_url is not None else previous_entry
                if selected_entry:
                    job.update(entry_url=selected_entry['entry_url'], entry_revision=selected_entry['revision'])
                    self.state['entries'][payload['shop_id']] = selected_entry
            replaced = [(old, deepcopy(old)) for old in replaceable]
            for old, _ in replaced:
                old.update(status='cancelled', ended_at=stamp(self.clock()), superseded_by=job['id'],
                           message='用户重新开始本机 Chrome 采集，已取消尚未接手的右侧浏览器请求；此旧请求未执行。')
            self.state['jobs'].append(job)
            try:
                self._save()
            except OSError:
                self.state['jobs'].remove(job)
                for old, saved in replaced:
                    old.clear(); old.update(saved)
                if previous_entry is None:
                    self.state['entries'].pop(payload['shop_id'], None)
                else:
                    self.state['entries'][payload['shop_id']] = previous_entry
                raise
            self.wake.set()
            return 202, {'message': message, 'job': self._public_job(job), 'entry': self._public_entry(payload['shop_id'])}

    def host_claim(self, payload):
        if set(payload) != {'id', 'shop_id', 'verified_shop_name', 'verified_source_url', 'browser_mode'}:
            raise ValueError('宿主接手字段无效')
        shop = self._shop(payload['shop_id'])
        if payload['browser_mode'] != 'codex_iab' or browser_mode(self.project) != 'codex_iab':
            return 409, {'message': '当前不允许右侧浏览器接手。'}
        if payload['verified_shop_name'] != shop['shop_name']:
            raise ValueError('实际店名与登记店铺不一致')
        from .store import shop_identity_evidence
        from .competitor_registry import _target
        _target(payload['verified_shop_name'], payload['verified_source_url'])
        expected = shop_identity_evidence({'sourceUrl': shop['source_url']})
        actual = shop_identity_evidence({'sourceUrl': payload['verified_source_url']})
        if actual.get('shop_id') != payload['shop_id'] or any(actual.get(key) != expected.get(key) for key in ('identity_kind', 'stable_identifier')):
            raise ValueError('实际页面不属于当前店铺')
        with self.lock:
            job = next((j for j in self.state['jobs'] if j['id'] == payload['id'] and j['shop_id'] == payload['shop_id']), None)
            if not job or job.get('browser_mode') != 'codex_iab':
                return 409, {'message': '未找到本店右侧浏览器请求。'}
            if job['status'] == 'host_claimed':
                return 200, {'message': '此请求已接手，复用原采集任务。', 'job': self._public_job(job)}
            if job['status'] != 'awaiting_host':
                return 409, {'message': '此请求不能重复接手，请先复核当前状态。', 'job': self._public_job(job)}
            job.update(status='host_claiming', message='正在建立宿主采集任务；尚未读网页。')
            self._save()
            try:
                result = capture_control.begin(self.project, shop_id=payload['shop_id'],
                                               source_url=payload['verified_source_url'], shop_name=payload['verified_shop_name'])
                if result.get('acquired') is not True:
                    job.update(status='awaiting_host', message='其他采集任务占用中，仍等待 Codex 接手。')
                    self._save()
                    return 409, {'message': job['message'], 'job': self._public_job(job)}
                job.update(status='host_claimed', capture_job_id=result['job_id'],
                           claimed_at=stamp(self.clock()), message='Codex 已接手，尚未读取网页。')
                self._save()
            except (OSError, ValueError, TypeError, KeyError):
                job.update(status='manual_review', host_status_unavailable=True,
                           message='接手未确认，请复核采集日志和锁；不能重复建立任务。')
                self._save()
                return 409, {'message': job['message'], 'job': self._public_job(job)}
            return 200, {'message': '接手已登记；通过原 capture_control 任务继续采集。', 'job': self._public_job(job)}

    def cancel(self, payload):
        if set(payload) != {'shop_id', 'id'}:
            raise ValueError('取消字段无效')
        self._shop(payload['shop_id'])
        with self.lock:
            job = next((j for j in self.state['jobs'] if j['id'] == payload['id'] and j['shop_id'] == payload['shop_id']), None)
            if not job or job['status'] in TERMINAL:
                return 409, {'message': '任务已结束或不存在。'}
            if job.get('browser_mode') == 'codex_iab' and job['status'] != 'awaiting_host':
                return 409, {'message': 'Codex 已接手，请在当前对话停止并封存本轮；网页未发送自动停止指令。'}
            if job['status'] in ('queued', 'awaiting_host'):
                job.update(status='cancelled', ended_at=stamp(self.clock()), message='排队任务已取消，未读网页。')
            else:
                job['cancel_requested'] = True
                atomic(self.directory / job['id'] / 'cancel.json', {'requested_at': stamp(self.clock())})
                job['message'] = '将在当前批次或发布安全边界停止；已经完成的入库保留。'
            self._save()
            return 200, {'message': job['message']}

    def tick(self, *, background=False):
        with self.lock:
            point = self.clock()
            if browser_mode(self.project) == 'codex_iab':
                # Host browser tools belong to a Codex turn; a local timer cannot drive them.
                return
            for shop_id, schedule in list(self.state['schedules'].items()):
                due = schedule.get('next_at')
                if not schedule.get('enabled') or schedule.get('blocked_by') or not due or datetime.fromisoformat(due) > point:
                    continue
                lateness = (point - datetime.fromisoformat(due)).total_seconds()
                if lateness <= 600:
                    self.start({'shop_id': shop_id, 'kind': 'shop'}, 'scheduled')
                else:
                    self.state['jobs'].append({'id': 'collect_' + uuid.uuid4().hex, 'shop_id': shop_id, 'kind': 'shop', 'status': 'missed', 'trigger': 'scheduled', 'created_at': due, 'ended_at': stamp(point), 'message': '服务未及时执行，已记录错过；可手动重新采集。'})
                schedule['last_due_at'] = due
                schedule['next_at'] = next_due(schedule, point)
                if schedule['mode'] == 'once':
                    schedule['enabled'] = False
                self._save()
            if self.active:
                return
            self.onboarding.advance()
            job = next((j for j in self.state['jobs'] if j['status'] == 'queued' and j.get('browser_mode') != 'codex_iab'), None)
            if not job:
                return
            self.active = job['id']
            job.update(status='running', started_at=stamp(point), message='正在连接你已打开的 Chrome；出现连接提示时请点允许。')
            self._save()
        if background:
            threading.Thread(target=self._run_job, args=(job,), daemon=True, name='pdd-collection-worker').start()
        else:
            self._run_job(job)

    def _run_job(self, job):
        if job.get('browser_mode') == 'codex_iab':
            raise ValueError('IAB requests must never enter the external browser worker')
        started = time.perf_counter()
        try:
            result = self.runner(deepcopy(job))
            if result.get('status') not in TERMINAL:
                raise ValueError('Worker did not return terminal status')
            result = scoped_worker_result(job, result)
        except Exception as error:
            result = {'status': 'failed', 'reason': 'collection_worker_exception',
                      'error_type': type(error).__name__,
                      'message': '采集执行失败；原数据和本轮证据已保留。查看本机采集记录后重试。'}
        result = {**result, 'execution_mode': 'fixed_program', 'ai_requests': 0,
                  'elapsed_seconds': round(max(0.0, time.perf_counter() - started), 3)}
        with self.lock:
            job.update(result, ended_at=stamp(self.clock()))
            entry = self.state['entries'].get(job.get('shop_id'))
            receipt = result.get('receipt') or {}
            if (confirmed_shop_release(job, result) and result.get('entry_verified') is True
                    and entry and entry.get('revision') == job.get('entry_revision')
                    and entry.get('entry_url') == job.get('entry_url')):
                entry.update(verification='verified', verified_at=stamp(self.clock()), verified_job_id=job['id'])
            schedule = self.state['schedules'].get(job['shop_id'])
            if schedule and job['kind'] == 'shop':
                # Card data may be published even after a global browser stop in
                # image repair. That publication must never resume timed retries.
                image_stop = (result.get('image_repair_stopped') is True
                    or result.get('sku_followup_available') is False
                    or (result.get('image_repair') or {}).get('stop_status') in
                       ('needs_login', 'needs_url', 'needs_browser', 'cancelled', 'manual_review', 'interrupted', 'failed'))
                schedule['blocked_by'] = job['id'] if image_stop or result['status'] in ('needs_login', 'needs_url', 'needs_browser', 'manual_review', 'interrupted', 'failed') else None
            self.active = None
            self._save()
            self.onboarding.advance()
            # A queued request may have used its wake while this worker was busy.
            # Hand the released slot to the next request without a polling delay.
            self.wake.set()

    def _execute(self, job):
        if job.get('browser_mode') == 'codex_iab':
            raise ValueError('IAB requests must never enter the external browser worker')
        directory = self.directory / job['id']
        directory.mkdir(parents=True, exist_ok=True)
        env = dict(os.environ)
        env.pop('DEEPSEEK_API_KEY', None)
        env['PYTHONUTF8'] = '1'
        with (directory / 'worker.log').open('ab') as log:
            process = subprocess.run([sys.executable, '-B', '-X', 'utf8', '-m', 'pdd_monitor.collection_worker', '--project', str(self.project), '--job', job['id']], cwd=self.project, env=env, stdout=log, stderr=log, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        receipt = directory / 'result.json'
        if not receipt.is_file():
            return {'status': 'failed', 'message': '本机采集进程未返回收据，请检查 worker.log。'}
        result = json.loads(receipt.read_text(encoding='utf-8'))
        result = scoped_worker_result(job, result)
        if job['kind'] == 'probe' and job.get('verify_storefront') is True and result.get('status') == 'complete':
            try:
                from .shop_onboarding import read_evidence
                if process.returncode:
                    raise ValueError('Storefront worker did not finish successfully')
                saved, digest = read_evidence(self.project, receipt.parent / 'browser_result.json')
                page, page_digest = read_evidence(self.project, receipt.parent / 'public_page.json')
                if (saved.get('status') != 'complete' or not isinstance(saved.get('intakeEvidence'), dict)
                        or saved['intakeEvidence'] != result.get('intakeEvidence')
                        or page.get('url') != saved['intakeEvidence'].get('sourceUrl')):
                    raise ValueError('Storefront worker evidence missing')
                result.update(verification_worker_completed=True, verification_result_sha256=digest,
                              verification_page_sha256=page_digest)
            except (OSError, ValueError, TypeError, KeyError):
                return {'status': 'manual_review', 'reason': 'verification_receipt_unconfirmed',
                        'message': '店铺核验进程或证据未确认完成，未发起采集。'}
        if 'elapsed_seconds' in result:
            result['worker_elapsed_seconds'] = result['elapsed_seconds']
        release_claimed = job['kind'] == 'shop' and (result['status'] == 'complete'
            or (result['status'] == 'partial' and bool(result.get('receipt'))))
        if release_claimed:
            try:
                if process.returncode:
                    raise ValueError('Collection worker did not finish successfully')
                verify_shop_release(self.project, job, result)
            except (OSError, ValueError, TypeError, KeyError, sqlite3.Error):
                return {**result, 'status': 'manual_review', 'reason': 'release_receipt_unconfirmed',
                        'dashboard_built': False,
                        'message': '执行结束但未确认本店成功入库收据；保留现有面板和本轮证据待复核。'}
            result['image_summary'] = released_image_summary(result['receipt'], result.get('image_summary'))
            if result['image_summary']['missing']:
                result.update(status='partial', reason='images_missing',
                    message=f"采集未完成：仍有{result['image_summary']['missing']}张商品主图待补；已读取商品数据和历史保留。")
        if confirmed_shop_release(job, result) and self.publisher:
            publish_started = time.perf_counter()
            try:
                images = result.get('image_summary') or {}
                image_progress = ({'image_total': images['total'],
                                   'image_saved': images['saved'], 'image_missing': images['missing']}
                                  if all(key in images for key in ('total', 'saved', 'missing')) else {})
                atomic(directory / 'progress.json', {'stage': 'dashboard_build', 'phase': '正在更新数据舱',
                       'cards': (result.get('progress') or {}).get('cards',
                                 (result.get('receipt') or {}).get('new_run', {}).get('cards', 0)),
                       'at': stamp(self.clock()), **image_progress})
                publication = self.publisher(self.project)
                if isinstance(publication, dict) and isinstance(publication.get('timings'), dict):
                    allowed = ('snapshot_seconds', 'decision_seconds', 'snapshot_publish_seconds',
                               'history_preflight_seconds', 'canonical_build_seconds', 'history_export_seconds',
                               'trend_status_publish_seconds', 'total_seconds')
                    timings = {key: round(publication['timings'][key], 3) for key in allowed
                               if type(publication['timings'].get(key)) in (int, float)
                               and math.isfinite(publication['timings'][key]) and publication['timings'][key] >= 0}
                    if timings:
                        result['dashboard_stages_seconds'] = timings
                result['dashboard_built'] = True
            except Exception as error:
                result.update(dashboard_built=False, reason='dashboard_build_failed', dashboard_error_type=type(error).__name__, message='数据已入库；面板构建失败，请运行 REFRESH_DASHBOARD.cmd。')
            finally:
                result['dashboard_elapsed_seconds'] = round(max(0.0, time.perf_counter() - publish_started), 3)
        return result

    def _loop(self):
        while True:
            try:
                self.tick(background=True)
            except Exception:
                # No automatic retry of a failed active browser attempt.
                pass
            self.wake.wait(15)
            self.wake.clear()


def main(argv=None):
    """Host-side commands use the running service as the single request writer."""
    import argparse
    from urllib.parse import urlencode
    from urllib.request import Request, urlopen
    from urllib.error import HTTPError, URLError
    parser = argparse.ArgumentParser(description='通过本机服务查看或接手右侧浏览器采集请求；不直接操作浏览器')
    parser.add_argument('--port', type=int, default=8878)
    sub = parser.add_subparsers(dest='command', required=True)
    show = sub.add_parser('show')
    show.add_argument('--shop-id', required=True)
    claim = sub.add_parser('host-claim')
    claim.add_argument('--id', required=True)
    claim.add_argument('--shop-id', required=True)
    claim.add_argument('--verified-shop-name', required=True)
    claim.add_argument('--verified-source-url', required=True)
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535:
        parser.error('port must be between 1 and 65535')
    origin = f'http://127.0.0.1:{args.port}'
    if args.command == 'show':
        request = Request(origin + '/__pdd_collection_status?' + urlencode({'shop_id': args.shop_id}))
    else:
        payload = {'id': args.id, 'shop_id': args.shop_id, 'browser_mode': 'codex_iab',
                   'verified_shop_name': args.verified_shop_name, 'verified_source_url': args.verified_source_url}
        request = Request(origin + '/__pdd_collection_host_claim',
                          data=json.dumps(payload, ensure_ascii=False).encode('utf-8'),
                          headers={'Content-Type': 'application/json', 'Origin': origin, 'Referer': origin + '/'}, method='POST')
    try:
        with urlopen(request, timeout=30) as response:
            result = json.load(response)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except HTTPError as error:
        try:
            result = json.load(error)
        except (ValueError, OSError):
            result = {'message': '本机服务未返回有效收据。'}
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 2
    except (URLError, OSError, ValueError):
        print(json.dumps({'message': '本机服务不可用，未确认接手；请查看现有请求后重试。'}, ensure_ascii=False))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
