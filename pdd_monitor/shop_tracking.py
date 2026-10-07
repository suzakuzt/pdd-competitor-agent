"""Explicit per-shop preferences and evidence-based next steps; never collect.

These settings are separate from immutable first-observation baselines. Missing
settings are unconfigured. Enabling only records intent: no scheduler is wired.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import tempfile

SCHEMA_VERSION = 1
SHOP_ID = re.compile(r'shop_[0-9a-f]{24}')
SETTING_KEYS = {'schema_version', 'shop_id', 'status', 'revision', 'created_at', 'updated_at'}
STATUSES = {'enabled', 'paused'}
CONNECTION_STATUS = 'connection_pending'


class TrackingConflict(ValueError):
    def __init__(self, current):
        super().__init__('跟踪设置已被其他操作更新，请刷新后重试。')
        self.current = current


class TrackingBusy(ValueError):
    pass


def _date(value):
    if not isinstance(value, str):
        raise ValueError('跟踪时间必须包含时区')
    result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if result.tzinfo is None:
        raise ValueError('跟踪时间必须包含时区')
    return result


def _path(project, shop_id):
    if not isinstance(shop_id, str) or not SHOP_ID.fullmatch(shop_id):
        raise ValueError('跟踪设置需要已核对的稳定店铺身份')
    return Path(project) / 'state/competitors/tracking' / (shop_id + '.json')


def _validate(value, shop_id):
    if not isinstance(value, dict) or set(value) != SETTING_KEYS:
        raise ValueError('跟踪设置字段无效，请核对原文件；不会自动重置')
    if type(value['schema_version']) is not int or value['schema_version'] != SCHEMA_VERSION:
        raise ValueError('跟踪设置版本不受支持')
    if value['shop_id'] != shop_id or not isinstance(value['status'], str) or value['status'] not in STATUSES:
        raise ValueError('跟踪设置的店铺或状态无效')
    if type(value['revision']) is not int or value['revision'] < 1:
        raise ValueError('跟踪设置版本号无效')
    if _date(value['updated_at']) < _date(value['created_at']):
        raise ValueError('跟踪设置时间顺序无效')
    return value


def _pairs(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError('跟踪设置不能包含重复字段')
        value[key] = item
    return value


def read_tracking(project, shop_id):
    """Return a validated setting and optional content hash without any writes."""
    path = _path(project, shop_id)
    if not path.exists():
        return {'shop_id': shop_id, 'status': 'unconfigured', 'revision': 0,
                'created_at': None, 'updated_at': None}, None
    raw = path.read_bytes()
    if len(raw) > 16384:
        raise ValueError('跟踪设置超过允许大小')
    value = _validate(json.loads(raw.decode('utf-8-sig'), object_pairs_hook=_pairs), shop_id)
    return value, hashlib.sha256(raw).hexdigest()


def load_tracking(project):
    """Validate every persisted setting, including shops absent from this export."""
    rows, hashes = {}, {}
    for path in sorted((Path(project) / 'state/competitors/tracking').glob('*.json')):
        value, sha = read_tracking(project, path.stem)
        rows[value['shop_id']] = value
        hashes[str(path)] = sha
    return rows, hashes


@contextmanager
def _write_lock(path):
    """Cross-process compare-and-swap guard; never silently steal a stale lock."""
    lock = path.with_suffix('.lock')
    try:
        descriptor = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as error:
        raise TrackingBusy('该店跟踪设置正在保存；若持续阻塞，请核对未完成写入。') from error
    try:
        os.write(descriptor, str(os.getpid()).encode('ascii'))
        os.fsync(descriptor)
        yield
    finally:
        os.close(descriptor)
        lock.unlink()


def _atomic_replace(path, value):
    temporary = None
    try:
        with tempfile.NamedTemporaryFile('w', encoding='utf-8', newline='\n', dir=path.parent,
                                         suffix='.tmp', delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write('\n'); stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def save_tracking(project, shop_id, status, expected_revision):
    """Save explicit intent only. Caller must first resolve a reviewed target."""
    path = _path(project, shop_id)
    if not isinstance(status, str) or status not in STATUSES:
        raise ValueError('跟踪状态只能为 enabled 或 paused')
    if type(expected_revision) is not int or expected_revision < 0:
        raise ValueError('expected_revision 必须为非负整数')
    path.parent.mkdir(parents=True, exist_ok=True)
    with _write_lock(path):
        current, _ = read_tracking(project, shop_id)
        if current['revision'] != expected_revision:
            raise TrackingConflict(current)
        if current['status'] == status:
            return {'status': 'unchanged', 'setting': current, 'path': str(path)}
        now = datetime.now(timezone.utc).isoformat(timespec='microseconds').replace('+00:00', 'Z')
        value = {'schema_version': SCHEMA_VERSION, 'shop_id': shop_id, 'status': status,
                 'revision': current['revision'] + 1, 'created_at': current['created_at'] or now,
                 'updated_at': now}
        _validate(value, shop_id)
        _atomic_replace(path, value)
    return {'status': 'saved', 'setting': value, 'path': str(path)}


def _run_time(run):
    epoch = run.get('observed_to_epoch')
    if type(epoch) in (int, float) and math.isfinite(epoch):
        return epoch
    try:
        return _date(run.get('observed_to')).timestamp()
    except (TypeError, ValueError):
        return float('-inf')


def _complete(run):
    boundary = run.get('end_boundary_observed')
    return run.get('status') == 'complete' and type(boundary) in (bool, int) and boundary == 1


def _attempt_time(attempt):
    for field in ('finished_at', 'started_at', 'recorded_at'):
        try:
            return _date(attempt.get(field)).timestamp()
        except (TypeError, ValueError):
            pass
    return float('-inf')


def target_tracking_fields(target, runs, setting=None, attempts=None):
    """Derive workflow from one shop's evidence; no inferred foreign failures."""
    shop_id = target.get('shop_id')
    known = isinstance(shop_id, str) and SHOP_ID.fullmatch(shop_id) is not None and not target.get('identity_conflict')
    known = known and target.get('status') != 'needs_identity'
    own = [run for run in runs if known and run.get('shop_id') == shop_id]
    latest = max(own, key=_run_time, default=None)
    complete = [run for run in own if _complete(run)]
    reference = max(complete, key=_run_time, default=None)
    setting = setting or {'status': 'unconfigured', 'revision': 0, 'updated_at': None}
    result = {
        'tracking_status': setting['status'], 'tracking_revision': setting['revision'],
        'tracking_updated_at': setting['updated_at'], 'can_configure_tracking': bool(known),
        'tracking_connection_status': CONNECTION_STATUS,
        'tracking_connection_note': '逐店自动采集尚未接通；开启仅保存跟踪意愿，不代表已开始采集。',
        'latest_run_id': latest.get('run_id') if latest else None,
        'latest_run_status': latest.get('status') if latest else None,
        'latest_end_boundary_observed': bool(latest and type(latest.get('end_boundary_observed')) in (bool, int) and latest['end_boundary_observed'] == 1),
        'latest_is_complete': _complete(latest) if latest else False,
        'latest_row_count': latest.get('card_count', latest.get('row_count')) if latest else None,
        'latest_observed_from': latest.get('observed_from') if latest else None,
        'latest_observed_to': latest.get('observed_to') if latest else None,
        'reference_run_id': reference.get('run_id') if reference else None,
        'reference_observed_from': reference.get('observed_from') if reference else None,
        'reference_observed_to': reference.get('observed_to') if reference else None,
        'reference_row_count': reference.get('card_count', reference.get('row_count')) if reference else None,
        'has_complete_run': bool(reference), 'needs_full_scan': bool(known and (latest is None or not _complete(latest))),
        'last_attempt_id': None, 'last_attempt_status': None, 'last_attempt_reason': None,
        'baseline_changed': False, 'history_deleted': False,
    }
    own_ids = {run['run_id'] for run in own}
    bound_attempts = [a for a in (attempts or []) if known and
                      ((a.get('shop_id') == shop_id and (not a.get('run_id') or a['run_id'] in own_ids)) or
                       (a.get('run_id') in own_ids and a.get('shop_id') in (None, shop_id)))]
    attempt = max(bound_attempts, key=_attempt_time, default=None)
    if attempt:
        result.update(last_attempt_id=attempt.get('attempt_id'), last_attempt_status=attempt.get('status'),
                      last_attempt_reason=attempt.get('reason'))
    fresh_attempt = attempt and (latest is None or _attempt_time(attempt) >= _run_time(latest))
    if not known:
        stage, label, action = 'needs_identity', '待核实店铺身份', '核对店铺页名称与稳定店铺链接；登记名称不等于已完成采集。'
    elif setting['status'] == 'paused':
        stage, label, action = 'tracking_paused', '跟踪已暂停', '需要继续时重新开启；已保存商品、历史和固定起点均保留。'
    elif fresh_attempt and attempt.get('status') == 'running':
        stage, label, action = 'collection_running', '本店采集正在进行', '等待该次真实采集收据；当前状态不代表完整扫描成功。'
    elif fresh_attempt and attempt.get('status') == 'failed':
        evidence = attempt.get('evidence') or {}
        code = evidence.get('error_code') if isinstance(evidence, dict) else None
        login = code in ('login_required', 'session_expired') or attempt.get('reason') in ('login_required', 'session_expired')
        stage = 'waiting_login' if login else 'collection_failed'
        label = '等待登录后继续' if login else '本次采集失败'
        action = '在真实店铺页面完成登录与身份复核，再按正常浏览采集。' if login else '检查本次失败证据，再进行正常浏览；不得把失败或部分轮记作完整。'
    elif latest is None:
        stage, label, action = 'awaiting_first_complete', '待首轮完整采集', '核对本店身份并正常逐屏浏览至真实结束边界，再备份、验证、入库。'
    elif not _complete(latest):
        stage, label, action = 'needs_rescan', '待补充完整扫描', '最新轮只有部分数据；重新逐屏采集并记录结束边界，保留已有部分轮。'
    elif setting['status'] == 'enabled':
        stage, label, action = 'tracking_enabled', '跟踪已开启，自动连接待完成', '本轮完整证据已保留；自动采集尚未接通，后续仍需真实采集和验收入库。'
    else:
        stage, label, action = 'tracking_unconfigured', '已有完整数据，跟踪未设置', '可明确开启或暂停本店跟踪；不设置不会自动开启。'
    result.update(sop_stage=stage, sop_label=label, next_action=action)
    return result
