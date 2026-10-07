"""Local collection Skill entry point. The existing service owns the crawler."""
from __future__ import annotations
import argparse
import json
import socket
import sqlite3
import sys
import time
from contextlib import closing
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

PROJECT = Path(__file__).resolve().parents[1]
ACTIVE = {'queued', 'running', 'host_claiming', 'awaiting_host', 'awaiting_browser', 'validating'}
PROGRESS_FIELDS = {'phase', 'stage', 'cards', 'batches', 'steps', 'elapsed_seconds',
    'total', 'processed', 'completed', 'partial', 'failed', 'unprocessed',
    'image_total', 'image_saved', 'image_missing', 'sku_count', 'page_count', 'scroll_steps'}
SKU_FIELDS = {'status', 'reason', 'message', 'total', 'completed', 'partial', 'failed',
    'unprocessed', 'skipped', 'sku_count', 'image_count', 'price_count'}


class CollectionError(Exception):
    def __init__(self, reason, message):
        super().__init__(message)
        self.reason = reason


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def shops(project):
    project = Path(project).resolve()
    if not (project / 'pdd_monitor/collection_service.py').is_file():
        raise CollectionError('project_unavailable', '未找到原采集项目，请指定完整项目目录。')
    database = project / 'data/monitor.sqlite3'
    if not database.is_file():
        raise CollectionError('database_unavailable', '原商品库不存在，不能创建空库代替。')
    with closing(sqlite3.connect(database.as_uri() + '?mode=ro', uri=True)) as connection:
        connection.execute('PRAGMA query_only=ON')
        rows = connection.execute('SELECT s.shop_id,s.shop_name FROM shops s '
            'WHERE EXISTS(SELECT 1 FROM runs r WHERE r.shop_id=s.shop_id) ORDER BY s.shop_name').fetchall()
    return [{'shop_id': row[0], 'shop_name': row[1]} for row in rows]


def resolve_shop(project, selector):
    candidates = shops(project)
    selector = selector.strip()
    matches = [shop for shop in candidates if selector in (shop['shop_id'], shop['shop_name'])]
    if len(matches) != 1:
        raise CollectionError('shop_unresolved', '店铺未唯一确认，请使用已采集店铺的准确名称或编号。')
    return matches[0]


class Client:
    def __init__(self, port=8878, timeout=10):
        if not isinstance(port, int) or not 1 <= port <= 65535:
            raise CollectionError('invalid_port', '本机端口无效。')
        self.origin = f'http://127.0.0.1:{port}'
        self.timeout = timeout
        self.opener = build_opener(ProxyHandler({}), NoRedirect())

    def request(self, path, payload=None):
        if path.split('?', 1)[0] not in {
            '/__pdd_collection_status', '/__pdd_collection_start', '/__pdd_collection_cancel'}:
            raise CollectionError('unsupported_action', '此入口只支持采集、进度和停止。')
        body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode('utf-8')
        request = Request(self.origin + path, data=body, headers={
            'Origin': self.origin, 'Content-Type': 'application/json', 'Accept': 'application/json'})
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                data = response.read(4 * 1024 * 1024 + 1)
                if len(data) > 4 * 1024 * 1024:
                    raise CollectionError('invalid_response', '采集状态响应过大，已停止读取。')
                value = json.loads(data)
                if not isinstance(value, dict):
                    raise ValueError('invalid JSON object')
                return value
        except HTTPError as error:
            raise CollectionError('service_rejected', f'本机服务未接受请求（HTTP {error.code}），请查看页面提示。') from error
        except (URLError, TimeoutError, socket.timeout) as error:
            if payload is not None:
                raise CollectionError('submission_unknown', '请求结果尚未确认；先查询进度，不能直接重复提交。') from error
            raise CollectionError('service_unavailable', '本机服务暂不可用，请先启动原服务。') from error
        except (ValueError, UnicodeError) as error:
            raise CollectionError('invalid_response', '本机采集服务响应无效，不能判断已完成。') from error

    def status(self, shop_id):
        return self.request('/__pdd_collection_status?' + urlencode({'shop_id': shop_id}))


def select_job(state, job_id=None, kind='shop'):
    jobs = state.get('jobs') or []
    for name in ('latest_job', 'latest_sku_batch_job'):
        if isinstance(state.get(name), dict):
            jobs = jobs + [state[name]]
    if job_id:
        return next((job for job in jobs if job.get('id') == job_id), None)
    return state.get('latest_sku_batch_job' if kind == 'sku_batch' else 'latest_job')


def validate_status_scope(state, shop_id):
    if not isinstance(state, dict) or not isinstance(state.get('jobs'), list):
        raise CollectionError('invalid_response', '采集服务没有返回有效的店铺状态。')
    entry = state.get('entry') or {}
    jobs = state['jobs'] + [state.get('latest_job'), state.get('latest_sku_batch_job')]
    if (state.get('shop_id') not in (None, shop_id)
            or not isinstance(entry, dict) or entry.get('shop_id') not in (None, shop_id)
            or any(not isinstance(job, dict) or job.get('shop_id') != shop_id
                   for job in jobs if job is not None)):
        raise CollectionError('scope_mismatch', '服务状态与明确指定的采集店铺不一致，未提交采集。')
    return state


def summary(shop, state, job=None):
    result = {**shop, 'browser_mode': state.get('browser_mode'),
        'program_available': state.get('local_worker_available') is True,
        'entry_status': (state.get('entry') or {}).get('status')}
    if job is None:
        result['job'] = None
        return result
    if job.get('shop_id') != shop['shop_id']:
        raise CollectionError('scope_mismatch', '采集任务与所选店铺不一致，已停止。')
    compact = {key: job[key] for key in ('id', 'kind', 'status', 'reason', 'message',
        'created_at', 'started_at', 'ended_at', 'elapsed_seconds', 'execution_mode',
        'ai_requests', 'dashboard_built') if key in job}
    # Terminal image coverage is the verified result, not the earlier repair counter.
    compact['progress'] = {k: v for k, v in (job.get('progress') or {}).items()
                           if k in PROGRESS_FIELDS} if job.get('status') in ACTIVE else {}
    compact['images'] = {k: v for k, v in (job.get('image_summary') or {}).items()
                         if k in {'total', 'saved', 'missing', 'status'}}
    compact['sku'] = {k: v for k, v in (job.get('sku_summary') or {}).items() if k in SKU_FIELDS}
    verification = job.get('image_verification') or {}
    if verification:
        compact['images_verified'] = verification.get('verified') is True
    result['job'] = compact
    return result


def run(args, client=None, sleeper=time.sleep, clock=time.monotonic):
    if args.command == 'shops':
        return {'shops': shops(args.project)}
    shop = resolve_shop(args.project, args.shop)
    client = client or Client(args.port)
    state = validate_status_scope(client.status(shop['shop_id']), shop['shop_id'])
    if args.command in ('check', 'status'):
        job = select_job(state, args.job, args.kind)
        if args.job and job is None:
            raise CollectionError('job_unavailable', '当前店铺未找到指定任务，不能判断其结果。')
        return summary(shop, state, job)
    if args.command == 'start':
        active = next((job for job in state['jobs']
                       if job.get('kind') == args.kind and job.get('status') in ACTIVE), None)
        if active is None:
            latest = select_job(state, kind=args.kind)
            active = latest if latest and latest.get('status') in ACTIVE else None
        if active is not None:
            result = summary(shop, state, active)
            result['submission_message'] = '目标店铺已有采集任务，已读取进度，未重复提交。'
            return result
        if state.get('browser_mode') != 'local_chrome' or state.get('local_worker_available') is not True:
            raise CollectionError('program_unavailable', '当前未配置普通Chrome后台采集，不能自动换浏览器。')
        response = client.request('/__pdd_collection_start', {'shop_id': shop['shop_id'], 'kind': args.kind})
        job = response.get('job')
        if not isinstance(job, dict) or not job.get('id'):
            raise CollectionError('submission_unknown', '请求已发送但任务编号未确认；请查询进度，不直接重试。')
        result = summary(shop, state, job)
        result['submission_message'] = response.get('message')
        return result
    if args.command == 'cancel':
        job = select_job(state, args.job)
        if job is None or job.get('shop_id') != shop['shop_id']:
            raise CollectionError('job_unavailable', '当前店铺未找到指定任务，不停止其他店铺任务。')
        response = client.request('/__pdd_collection_cancel', {'shop_id': shop['shop_id'], 'id': args.job})
        return {'shop_id': shop['shop_id'], 'job_id': args.job, 'message': response.get('message'),
                'cancellation_requested': True}
    if args.command == 'wait':
        deadline = clock() + args.seconds
        while True:
            job = select_job(state, args.job)
            if job is None:
                raise CollectionError('job_unavailable', '当前店铺未找到指定任务，不能判断已结束。')
            if job.get('status') not in ACTIVE or clock() >= deadline:
                result = summary(shop, state, job)
                result['still_running'] = job.get('status') in ACTIVE
                return result
            sleeper(min(2, max(0, deadline - clock())))
            state = validate_status_scope(client.status(shop['shop_id']), shop['shop_id'])
    raise CollectionError('unsupported_action', '未知采集操作。')


def parser():
    result = argparse.ArgumentParser(description='店铺采集：启动原后台程序，查询进度或明确停止任务。')
    result.add_argument('--project', type=Path, default=PROJECT)
    result.add_argument('--port', type=int, default=8878)
    commands = result.add_subparsers(dest='command', required=True)
    commands.add_parser('shops', help='列出已采集店铺，不启动浏览器')
    for action in ('check', 'status', 'start', 'wait', 'cancel'):
        item = commands.add_parser(action)
        item.add_argument('--shop', required=True, help='用户明确指定的完整店铺名称或shop_id')
        if action != 'cancel':
            item.add_argument('--kind', choices=('shop', 'sku_batch'), default='shop')
        if action in ('check', 'status', 'wait', 'cancel'):
            item.add_argument('--job', required=action in ('wait', 'cancel'))
        if action == 'wait':
            item.add_argument('--seconds', type=int, choices=range(0, 46), metavar='0..45', default=25)
    return result


def main(argv=None):
    try:
        result = run(parser().parse_args(argv))
        code = 0
    except CollectionError as error:
        result = {'status': 'unavailable', 'reason': error.reason, 'message': str(error)}
        code = 2
    except (OSError, sqlite3.Error):
        result = {'status': 'unavailable', 'reason': 'project_read_failed', 'message': '无法读取原项目，未启动采集。'}
        code = 2
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))
    return code


if __name__ == '__main__':
    sys.exit(main())
