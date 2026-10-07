"""Local target registration followed by a canonical dashboard publication.

The service does not collect websites, resolve shares, open credentials, start
a scheduler, or write business databases. Reviewed runs are read from the current
snapshot. The injected publisher owns ordinary dashboard export/build.
"""
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import secrets
import threading
import time
from urllib.parse import urlsplit

from .competitor_registry import _target, build_targets, register_target, set_target_tracking
from .shop_tracking import TrackingBusy, TrackingConflict

MAX_BODY_BYTES = 32768
MAX_SNAPSHOT_BYTES = 256 * 1024 * 1024
JOB_LIFETIME_SECONDS = 3600
MAX_FINISHED_JOBS = 32


def _now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z')


def allowed_competitor_request(headers, port, *, write=False):
    """Require same-origin loopback traffic; never enable cross-origin writes."""
    host = headers.get('Host', '')
    if host not in (f'127.0.0.1:{port}', f'localhost:{port}'):
        return False
    origin = 'http://' + host
    if headers.get('Origin') and headers['Origin'] != origin:
        return False
    if headers.get('Sec-Fetch-Site') in ('cross-site', 'same-site'):
        return False
    if headers.get('Referer'):
        try:
            reference = urlsplit(headers['Referer'])
        except ValueError:
            return False
        if reference.scheme + '://' + reference.netloc != origin:
            return False
    if write:
        if headers.get('Origin') != origin or headers.get('Transfer-Encoding'):
            return False
        if headers.get('Content-Type', '').split(';', 1)[0].strip().lower() != 'application/json':
            return False
        length = headers.get('Content-Length', '')
        if not isinstance(length, str) or not re.fullmatch(r'[0-9]{1,6}', length):
            return False
        return 0 < int(length) <= MAX_BODY_BYTES
    return True


def decode_competitor_payload(raw):
    """Strict bounded JSON decoder for the HTTP route (not arbitrary coercion)."""
    if not isinstance(raw, bytes) or not 0 < len(raw) <= MAX_BODY_BYTES:
        raise ValueError('登记请求必须为有限大小的 JSON')

    def pairs(values):
        result = {}
        for key, value in values:
            if key in result:
                raise ValueError('登记请求不能有重复字段')
            result[key] = value
        return result

    def invalid_constant(value):
        raise ValueError('登记请求不能包含非法数值')

    try:
        return json.loads(raw.decode('utf-8'), object_pairs_hook=pairs, parse_constant=invalid_constant)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError('登记请求不是有效的 UTF-8 JSON') from error


def _read_runs(project):
    file = Path(project) / 'dashboard/src/data.json'
    if not file.is_file() or file.stat().st_size > MAX_SNAPSHOT_BYTES:
        raise ValueError('已审面板快照不存在或超过读取限制')
    snapshot = json.loads(file.read_text(encoding='utf-8-sig'))
    runs = snapshot.get('queries', {}).get('runs', {}).get('rows') if isinstance(snapshot, dict) else None
    if not isinstance(runs, list):
        raise ValueError('已审面板快照缺少轮次记录')
    return runs


class CompetitorService:
    def __init__(self, project, publisher, runs_provider=None):
        self.project = Path(project)
        if not callable(publisher):
            raise TypeError('canonical publisher is required')
        self.publisher = publisher
        self.runs_provider = runs_provider or _read_runs
        self.lock = threading.Lock()
        self.jobs = {}
        self.job_clock = {}
        self.busy = False

    def _prune(self):
        now = time.monotonic()
        finished = [key for key, value in self.jobs.items() if value['status'] != 'running']
        for key in finished:
            if now - self.job_clock[key] > JOB_LIFETIME_SECONDS:
                del self.jobs[key]
                del self.job_clock[key]
        finished = sorted((key for key, value in self.jobs.items() if value['status'] != 'running'), key=self.job_clock.get)
        for key in finished[:-MAX_FINISHED_JOBS]:
            del self.jobs[key]
            del self.job_clock[key]

    def status(self, job_id):
        with self.lock:
            self._prune()
            if not isinstance(job_id, str) or job_id not in self.jobs:
                return 404, {'status': 'failed', 'error_code': 'job_not_found', 'message': '登记任务不存在或已过期，请核对当前目标。'}
            return 200, deepcopy(self.jobs[job_id])

    def _runs(self):
        runs = self.runs_provider(self.project)
        if not isinstance(runs, list):
            raise ValueError('已审轮次格式无效')
        for run in runs:
            if not isinstance(run, dict) or not isinstance(run.get('shop_id'), str) or not run['shop_id']:
                raise ValueError('已审轮次缺少店铺身份')
        return runs

    def start(self, payload):
        if not isinstance(payload, dict) or set(payload) - {'name', 'url'}:
            raise ValueError('登记只接受 name 和 url 字段')
        name, url = payload.get('name'), payload.get('url')
        _target(name, url)  # Validate all untrusted inputs before any write.
        with self.lock:
            self._prune()
            if self.busy:
                return 409, {'status': 'running', 'error_code': 'registration_busy', 'message': '另一个目标正在登记或更新面板，请稍后再试。'}
            runs = self._runs()
            build_targets(self.project, runs)  # Fail closed on unreadable existing registry.
            result = register_target(self.project, name, url)
            target = result['target']
            job_id = 'intake_' + secrets.token_hex(16)
            receipt = {'registration_status': result['status'], 'target_id': target['target_id'],
                       'shop_id': target.get('shop_id'), 'target_status': target['status'],
                       'website_collection_performed': False, 'dashboard_built': False}
            self.jobs[job_id] = {'status': 'running', 'job_id': job_id, 'started_at': _now(),
                                 'message': '目标已登记，正在更新面板；尚未启动网站采集。', 'receipt': receipt}
            self.job_clock[job_id] = time.monotonic()
            try:
                rows, _ = build_targets(self.project, runs)
                derived = next(row for row in rows if row['target_id'] == target['target_id'])
                receipt.update(shop_id=derived.get('shop_id'), target_status=derived['status'])
                self.busy = True
                response = deepcopy(self.jobs[job_id])
                threading.Thread(target=self._publish, args=(job_id,), daemon=True, name='competitor-intake-publish').start()
                return 202, response
            except Exception:
                self.busy = False
                self.jobs[job_id].update(status='failed', finished_at=_now(), error_code='registration_finalize_failed',
                                         message='目标已登记，但面板更新任务未能启动；登记已保留，可重试。')
                return 503, deepcopy(self.jobs[job_id])

    def update_tracking(self, payload):
        """Save explicit per-shop settings, then use the same canonical publisher."""
        if not isinstance(payload, dict) or set(payload) != {'target_id', 'status', 'expected_revision'}:
            raise ValueError('跟踪设置只接受 target_id、status 和 expected_revision')
        with self.lock:
            self._prune()
            if self.busy:
                return 409, {'status': 'running', 'error_code': 'registration_busy',
                             'message': '另一个店铺设置或面板正在更新，请稍后再试。'}
            try:
                receipt = set_target_tracking(self.project, self._runs(), **payload)
            except TrackingConflict as error:
                return 409, {'status': 'failed', 'error_code': 'revision_conflict', 'message': str(error),
                             'current_revision': error.current['revision'], 'current_status': error.current['status']}
            except TrackingBusy as error:
                return 409, {'status': 'failed', 'error_code': 'tracking_busy', 'message': str(error)}
            receipt['dashboard_built'] = False
            job_id = 'intake_' + secrets.token_hex(16)
            self.jobs[job_id] = {'status': 'running', 'job_id': job_id, 'started_at': _now(),
                                 'message': '本店跟踪设置已保存，正在更新面板；尚未启动自动采集。', 'receipt': receipt}
            self.job_clock[job_id] = time.monotonic()
            try:
                self.busy = True
                response = deepcopy(self.jobs[job_id])
                threading.Thread(target=self._publish, args=(job_id,), daemon=True, name='competitor-tracking-publish').start()
                return 202, response
            except Exception:
                self.busy = False
                self.jobs[job_id].update(status='failed', finished_at=_now(), error_code='tracking_finalize_failed',
                                         message='跟踪设置已保存，但面板更新任务未能启动；设置仍保留，请刷新后重试。')
                return 503, deepcopy(self.jobs[job_id])

    def _publish(self, job_id):
        tracking = self.jobs[job_id]['receipt'].get('operation') == 'tracking'
        try:
            built = self.publisher(self.project)
            if not isinstance(built, dict) or built.get('status') != 'built':
                raise ValueError('canonical publication did not succeed')
            with self.lock:
                self.jobs[job_id].update(status='succeeded', finished_at=_now(),
                                         message=('本店跟踪设置与面板已更新；自动采集尚未接通，历史和固定起点均保留。' if tracking else
                                                  '目标与面板已更新；登记不等于身份核实或网站采集。'))
                self.jobs[job_id]['receipt']['dashboard_built'] = True
                self.job_clock[job_id] = time.monotonic()
        except Exception:
            with self.lock:
                self.jobs[job_id].update(status='failed', finished_at=_now(), error_code='dashboard_build_failed',
                                         message=('跟踪设置已保存，但面板构建失败；请刷新并按当前版本重试，设置和历史均保留。' if tracking else
                                                  '目标已登记，但面板构建失败；登记已保留，重试不会重复创建。'))
                self.job_clock[job_id] = time.monotonic()
        finally:
            with self.lock:
                self.busy = False
