"""Local dashboard, reviewed model queries and explicit per-shop collection jobs."""
from __future__ import annotations

import argparse
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from urllib.error import URLError
from urllib.request import urlopen

PROJECT = Path(__file__).resolve().parents[1]


def _replace_snapshot_bytes(path, content):
    """Publish whole snapshot bytes; concurrent read-only requests see old or new JSON."""
    path = Path(path)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='wb', dir=path.parent, prefix=path.name + '.',
                                         suffix='.tmp', delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _node():
    if os.environ.get('PDD_NODE'):
        candidate = Path(os.environ['PDD_NODE'])
        if not candidate.is_file():
            raise ValueError('PDD_NODE must identify an existing node executable')
        return str(candidate)
    bundled = Path.home() / '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node.exe'
    if bundled.is_file():
        return str(bundled)
    executable = shutil.which('node')
    if executable:
        return executable
    raise ValueError('Node runtime unavailable; set PDD_NODE to the installed executable')


def build_dashboard(project=PROJECT, data_dir=None):
    from .dashboard_data import build_dashboard_snapshot
    from .snapshot_json import dumps_bytes
    from .dashboard_history import retain_static_history
    started = time.perf_counter()
    timings = {}
    project = Path(project).resolve()
    selected_data = Path(data_dir).resolve() if data_dir is not None else project / 'data'
    # The local image endpoint is rooted in this project's paired image store.
    # An explicit alternate-data build remains self-contained, as before.
    image_delivery = 'local_http' if selected_data == project / 'data' else 'inline'
    app = project / 'dashboard'
    snapshot = app / 'src/data.json'
    if not (app / 'src/content/dashboard/DashboardContent.jsx').is_file():
        raise ValueError('Dashboard source missing; restore the delivered dashboard folder')
    plugin = Path(os.environ.get('PDD_DATA_PLUGIN', str(Path.home() / '.codex/plugins/cache/openai-curated-remote/data-analytics/1.0.11')))
    builder = plugin / 'scripts/data-app.mjs'
    if not builder.is_file():
        raise ValueError('Installed Data builder missing; set PDD_DATA_PLUGIN to the installed plugin folder')
    exporter = project / 'scripts/export_history.mjs'
    if not exporter.is_file():
        raise ValueError('History exporter missing; restore scripts/export_history.mjs')
    node = _node()
    original = snapshot.read_bytes() if snapshot.exists() else None
    reports = project / 'reports/dashboard'
    reports.mkdir(parents=True, exist_ok=True)
    build_log = []

    def run_step(arguments, label, timing_key):
        step_started = time.perf_counter()
        result = subprocess.run(arguments, cwd=project, capture_output=True, text=True,
                                encoding='utf-8', errors='replace')
        timings[timing_key] = round(time.perf_counter() - step_started, 6)
        build_log.append(label + '\n' + result.stdout + result.stderr)
        (reports / 'latest_build.txt').write_text('\n'.join(build_log), encoding='utf-8')
        if result.returncode:
            raise ValueError(label + ' failed; see reports/dashboard/latest_build.txt')
        return result

    snapshot_published = False
    try:
        step_started = time.perf_counter()
        payload = build_dashboard_snapshot(selected_data, snapshot, write_output=False,
                                           image_delivery=image_delivery)
        timings['snapshot_seconds'] = round(time.perf_counter() - step_started, 6)
        # Freeze analysis-time cohorts only through explicit publication. The
        # standalone snapshot exporter remains read-only. These records mean
        # analysis recorded, not external notification delivered or a purchase.
        step_started = time.perf_counter()
        if 'trend_signals' in payload.get('queries', {}):
            from .trend_decisions import record_decisions, build_decision_queries
            from .trend_backtest import build_backtest_queries
            ledger = record_decisions(project, payload['queries'])
            payload['queries'].update(build_decision_queries(payload['queries'], project, ledger=ledger))
            payload['queries'].update(build_backtest_queries(payload['queries'], project, ledger=ledger))
            payload['metadata']['trend_decision_ledger_revision'] = ledger['revision']
        timings['decision_seconds'] = round(time.perf_counter() - step_started, 6)
        payload['buildStatus'] = 'complete'
        step_started = time.perf_counter()
        _replace_snapshot_bytes(snapshot, dumps_bytes(payload))
        snapshot_published = True
        timings['snapshot_publish_seconds'] = round(time.perf_counter() - step_started, 6)
        # Refuse a timestamp report conflict before the builder replaces the old dist directory.
        run_step([node, str(exporter), '--project-root', str(project), '--check-only'], 'History export preflight', 'history_preflight_seconds')
        with retain_static_history(app):
            run_step([node, str(builder), 'build', '--project-dir', str(app), '--separate-data'], 'Dashboard build', 'canonical_build_seconds')
        result = run_step([node, str(exporter), '--project-root', str(project)], 'History export', 'history_export_seconds')
        history_exports = json.loads(result.stdout)
        if not isinstance(history_exports, dict) or history_exports.get('status') not in ('exported', 'unchanged'):
            raise ValueError('History export did not confirm success; see reports/dashboard/latest_build.txt')
        from .trend_dashboard import trend_status_manifest
        step_started = time.perf_counter()
        _replace_snapshot_bytes(app / 'dist/trend-status.json',
            (json.dumps(trend_status_manifest(payload), ensure_ascii=False) + '\n').encode('utf-8'))
        timings['trend_status_publish_seconds'] = round(time.perf_counter() - step_started, 6)
    except Exception:
        if original is not None and snapshot_published:
            _replace_snapshot_bytes(snapshot, original)
        raise
    timings['total_seconds'] = round(time.perf_counter() - started, 6)
    return {'status': 'built', 'path': str(app / 'dist/index.html'),
            'history_exports': history_exports,
            'timings': timings,
            'observation_count': len(payload['queries']['observations']['rows']),
            'default_run_id': payload['metadata']['default_run_id'],
            'website_collection_performed': False}


def _health(port, project):
    try:
        with urlopen(f'http://127.0.0.1:{port}/__pdd_dashboard_health', timeout=1) as response:
            value = json.load(response)
        return isinstance(value, dict) and value.get('app') == 'pdd-competitor-dashboard' and value.get('project') == str(Path(project).resolve())
    except (OSError, URLError, ValueError):
        return False


def serve(project, port):
    project = Path(project).resolve()
    directory = project / 'dashboard/dist'
    if not (directory / 'index.html').is_file():
        raise ValueError('Build the dashboard before serving it')
    from .artist_heat_service import ArtistHeatService, allowed_refresh_request
    from .profit_service import ProfitService, allowed_profit_request
    from .agent_service import AgentService, allowed_agent_request
    from .competitor_service import CompetitorService, allowed_competitor_request, decode_competitor_payload
    from .dashboard_images import ImageReader, serve_image
    from urllib.parse import urlsplit, parse_qs
    publish_lock = threading.Lock()
    image_reader = ImageReader(project / 'data')

    def publish(root):
        with publish_lock:
            return build_dashboard(root)

    heat = ArtistHeatService(project, publisher=publish)
    profit = ProfitService(project, publisher=publish)
    agent_search = AgentService(project)
    competitor_intake = CompetitorService(project, publisher=publish)
    from .collection_service import CollectionService
    collection = CollectionService(project, publisher=publish)

    class Handler(SimpleHTTPRequestHandler):
        def send_json(self, status, value):
            body = json.dumps(value, ensure_ascii=False, allow_nan=False).encode('utf-8')
            self.send_response(status)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            if self.path in ('/__pdd_collection_onboard', '/__pdd_collection_onboard_cancel', '/__pdd_collection_onboard_collect'):
                if not allowed_competitor_request(self.headers, port, write=True):
                    return self.send_json(403, {'status': 'failed', 'message': '仅接受本机面板的店铺接入请求。'})
                try:
                    payload = decode_competitor_payload(self.rfile.read(int(self.headers['Content-Length'])))
                    action = {'/__pdd_collection_onboard': collection.onboard,
                              '/__pdd_collection_onboard_cancel': collection.onboard_cancel,
                              '/__pdd_collection_onboard_collect': collection.onboard_collect}[self.path]
                    status, result = action(payload)
                    return self.send_json(status, result)
                except (ValueError, TypeError, KeyError):
                    return self.send_json(422, {'status': 'failed', 'message': '店铺接入目标或任务编号无效，请核对已登记链接。'})
                except OSError:
                    return self.send_json(503, {'status': 'failed', 'message': '本机接入状态暂不可用，请先查询本次任务，勿重复提交。'})
            if self.path in ('/__pdd_collection_start', '/__pdd_collection_schedule', '/__pdd_collection_cancel', '/__pdd_collection_probe', '/__pdd_collection_host_claim', '/__pdd_collection_entry'):
                if not allowed_competitor_request(self.headers, port, write=True):
                    return self.send_json(403, {'message': '仅接受本机面板的采集设置请求。'})
                try:
                    payload = decode_competitor_payload(self.rfile.read(int(self.headers['Content-Length'])))
                    action = {'/__pdd_collection_start': collection.start, '/__pdd_collection_schedule': collection.schedule, '/__pdd_collection_cancel': collection.cancel, '/__pdd_collection_probe': collection.probe, '/__pdd_collection_host_claim': collection.host_claim, '/__pdd_collection_entry': collection.update_entry}[self.path]
                    status, result = action(payload)
                    return self.send_json(status, result)
                except (ValueError, TypeError, KeyError):
                    return self.send_json(422, {'message': '采集范围、版本或时间无效；请核对北京时间及当前店铺。'})
                except OSError:
                    return self.send_json(503, {'message': '本机采集状态暂不可用，保存尚未确认。'})
            if self.path == '/__pdd_competitor_tracking':
                if not allowed_competitor_request(self.headers, port, write=True):
                    return self.send_json(403, {'status':'failed', 'error_code':'request_forbidden', 'message':'仅接受本机面板的有限大小 JSON 跟踪设置请求。'})
                try:
                    payload = decode_competitor_payload(self.rfile.read(int(self.headers['Content-Length'])))
                    status, result = competitor_intake.update_tracking(payload)
                    return self.send_json(status, result)
                except (ValueError, TypeError, KeyError):
                    return self.send_json(422, {'status':'failed', 'error_code':'invalid_tracking_setting', 'message':'跟踪设置无效，请核实目标身份、启用或暂停状态及当前版本；没有确认保存。'})
                except OSError:
                    return self.send_json(503, {'status':'failed', 'error_code':'local_file_unavailable', 'message':'本机跟踪设置暂不可用，是否保存尚未确认；请保留当前选择并刷新核对。'})
            if self.path == '/__pdd_competitor_add':
                if not allowed_competitor_request(self.headers, port, write=True):
                    return self.send_json(403, {'status':'failed', 'error_code':'request_forbidden', 'message':'仅接受本机面板的有限大小 JSON 登记请求。'})
                try:
                    payload = decode_competitor_payload(self.rfile.read(int(self.headers['Content-Length'])))
                    status, result = competitor_intake.start(payload)
                    return self.send_json(status, result)
                except (ValueError, TypeError, KeyError):
                    return self.send_json(422, {'status':'failed', 'error_code':'invalid_target', 'message':'店铺名称或链接无效，请提供拼多多店铺或分享链接，不接受商品链接。'})
                except OSError:
                    return self.send_json(503, {'status':'failed', 'error_code':'local_file_unavailable', 'message':'本机登记文件暂不可用，未确认登记，请保留输入。'})
            if self.path == '/__pdd_agent_query':
                if not allowed_agent_request(self.headers, port, write=True):
                    return self.send_json(403, {'status':'failed','message':'仅接受本机页面的有限大小JSON请求'})
                try:
                    payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])),
                                         parse_constant=lambda value: (_ for _ in ()).throw(ValueError('非法数值')))
                    status, result = agent_search.start(payload)
                    return self.send_json(status, result)
                except (ValueError, TypeError, KeyError, UnicodeDecodeError):
                    return self.send_json(422, {'status':'failed','message':'问题或店铺范围无效，请重新检查输入。'})
                except OSError:
                    return self.send_json(503, {'status':'unavailable','message':'本机模型或数据暂不可用，原有手动筛选仍可使用。'})
            if self.path in ('/__pdd_profit_preview', '/__pdd_profit_save'):
                if not allowed_profit_request(self.headers, port):
                    return self.send_json(403, {'status':'failed', 'message':'仅接受本机面板的有限大小JSON请求'})
                try:
                    payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])),
                                         parse_constant=lambda value: (_ for _ in ()).throw(ValueError('金额不得为NaN或无穷大')))
                    if self.path == '/__pdd_profit_preview':
                        return self.send_json(200, profit.preview(payload))
                    status, result = profit.start(payload)
                    return self.send_json(status, result)
                except (ValueError, TypeError, KeyError) as exc:
                    return self.send_json(422, {'status':'failed', 'message':str(exc)[:400]})
                except OSError:
                    return self.send_json(500, {'status':'failed', 'message':'本机文件读写失败；未确认保存，请保留当前输入。'})
            if self.path != '/__pdd_artist_heat_refresh':
                return self.send_json(404, {'status':'failed', 'message':'未知操作'})
            if not allowed_refresh_request(self.headers, port):
                return self.send_json(403, {'status':'failed', 'message':'仅接受本机面板发起的更新'})
            try:
                payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                if payload != {}:
                    raise ValueError('Unexpected input')
            except (ValueError, UnicodeDecodeError):
                return self.send_json(400, {'status':'failed', 'message':'此操作不接受网址或其他参数'})
            status, result = heat.start()
            return self.send_json(status, result)

        def do_GET(self):
            self._image_response = False
            route = urlsplit(self.path)
            if route.path.startswith('/__pdd_image/'):
                return self.local_image(route)
            if route.path == '/__pdd_shop_directory':
                if not allowed_competitor_request(self.headers, port):
                    return self.send_json(403, {'status': 'failed', 'message': '仅接受本机店铺列表查询。'})
                try:
                    if route.query or route.fragment:
                        raise ValueError('Directory accepts no query scope')
                    return self.send_json(200, collection.shop_directory())
                except (ValueError, KeyError, TypeError):
                    return self.send_json(422, {'status': 'failed', 'message': '店铺列表状态尚未通过核验，请刷新后核对。'})
                except (OSError, RuntimeError, sqlite3.Error):
                    return self.send_json(503, {'status': 'failed', 'message': '本机店铺列表暂不可读，已登记目标保留。'})
            if route.path == '/__pdd_collection_onboard':
                if not allowed_competitor_request(self.headers, port):
                    return self.send_json(403, {'status': 'failed', 'message': '仅接受本机店铺接入查询。'})
                try:
                    params = parse_qs(route.query, keep_blank_values=True)
                    if route.fragment or set(params) not in ({'id'}, {'target_id'}) or any(len(values) != 1 for values in params.values()):
                        raise ValueError('Invalid onboarding scope')
                    return self.send_json(200, collection.onboard_status(**{key: values[0] for key, values in params.items()}))
                except (ValueError, KeyError, TypeError):
                    return self.send_json(422, {'status': 'failed', 'message': '店铺接入任务或目标编号无效。'})
                except (OSError, RuntimeError):
                    return self.send_json(503, {'status': 'failed', 'message': '本机接入状态暂不可读，已保存证据保留。'})
            if route.path == '/__pdd_collection_probe':
                if not allowed_competitor_request(self.headers, port):
                    return self.send_json(403, {'message': '仅接受本机链接检查查询。'})
                try:
                    params = parse_qs(route.query, keep_blank_values=True)
                    if set(params) != {'id'} or len(params['id']) != 1:
                        raise ValueError('Invalid probe scope')
                    return self.send_json(200, collection.probe_status(params['id'][0]))
                except (ValueError, KeyError, TypeError):
                    return self.send_json(422, {'message': '链接检查编号无效。'})
            if route.path == '/__pdd_trend_status':
                if not allowed_agent_request(self.headers, port):
                    return self.send_json(403, {'message': '仅接受本机面板查询趋势提醒。'})
                try:
                    parameters = parse_qs(route.query, keep_blank_values=True)
                    if set(parameters) != {'shop_id'} or len(parameters['shop_id']) != 1:
                        raise ValueError('Invalid trend scope')
                    from .trend_dashboard import read_trend_status
                    return self.send_json(200, read_trend_status(project, parameters['shop_id'][0]))
                except (ValueError, KeyError, TypeError):
                    return self.send_json(422, {'message': '当前店铺趋势范围无效。'})
                except OSError:
                    return self.send_json(503, {'message': '趋势提醒暂不可读，当前数据保留。'})
            if route.path == '/__pdd_collection_status':
                if not allowed_competitor_request(self.headers, port):
                    return self.send_json(403, {'message': '仅接受本机采集状态查询。'})
                try:
                    params = parse_qs(route.query, keep_blank_values=True)
                    if set(params) not in ({'shop_id'}, {'shop_id', 'observation_id'}) or any(len(values) != 1 for values in params.values()):
                        raise ValueError('Invalid collection scope')
                    observation_id = int(params['observation_id'][0]) if 'observation_id' in params else None
                    return self.send_json(200, collection.status(params['shop_id'][0], observation_id))
                except (ValueError, KeyError, TypeError):
                    return self.send_json(422, {'message': '当前店铺或原卡范围无效，不能显示别店规格。'})
                except (OSError, RuntimeError):
                    return self.send_json(503, {'message': '本机采集证据暂不可用，请保留原文件复核。'})
            if route.path == '/__pdd_competitor_status':
                if not allowed_competitor_request(self.headers, port):
                    return self.send_json(403, {'status':'failed', 'error_code':'request_forbidden', 'message':'仅接受本机面板查询登记任务。'})
                parameters = parse_qs(route.query, keep_blank_values=True)
                ids = parameters.get('job_id', [])
                if set(parameters) != {'job_id'} or len(ids) != 1 or not ids[0].startswith('intake_') or len(ids[0]) != 39 or any(c not in '0123456789abcdef' for c in ids[0][7:]):
                    return self.send_json(400, {'status':'failed', 'error_code':'invalid_job_id', 'message':'登记任务编号无效。'})
                status, result = competitor_intake.status(ids[0])
                return self.send_json(status, result)
            if route.path in ('/__pdd_agent_status','/__pdd_agent_result'):
                if not allowed_agent_request(self.headers, port):
                    return self.send_json(403, {'status':'failed','message':'仅接受本机页面查询'})
                if route.path == '/__pdd_agent_status':
                    return self.send_json(200, agent_search.status())
                ids = parse_qs(route.query).get('id', [])
                if len(ids)!=1 or len(ids[0])!=48 or any(c not in '0123456789abcdef' for c in ids[0]):
                    return self.send_json(400, {'status':'failed','message':'查询编号无效'})
                status, result = agent_search.result(ids[0])
                return self.send_json(status, result)
            if self.path == '/__pdd_profit_status':
                return self.send_json(200, profit.status())
            if self.path == '/__pdd_artist_heat_status':
                return self.send_json(200, heat.status())
            if self.path == '/__pdd_artist_heat_data':
                try:
                    return self.send_json(200, heat.data())
                except (OSError, ValueError, KeyError):
                    return self.send_json(500, {'status':'failed', 'message':'历史观察无法读取，保留原文件，请检查记录完整性。'})
            if self.path == '/__pdd_dashboard_health':
                body = json.dumps({'app': 'pdd-competitor-dashboard', 'project': str(project)}).encode('utf-8')
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            super().do_GET()

        def local_image(self, route, head_only=False):
            if not allowed_competitor_request(self.headers, port):
                return self.send_error(403, 'Local dashboard requests only')
            if route.query or route.fragment:
                return self.send_error(400, 'Invalid image path')
            self._image_response = True
            return serve_image(self, image_reader, route.path.removeprefix('/__pdd_image/'), head_only=head_only)

        def do_HEAD(self):
            self._image_response = False
            route = urlsplit(self.path)
            if route.path.startswith('/__pdd_image/'):
                return self.local_image(route, head_only=True)
            super().do_HEAD()

        def end_headers(self):
            if not getattr(self, '_image_response', False):
                self.send_header('Cache-Control', 'no-cache')
            super().end_headers()

    server = ThreadingHTTPServer(('127.0.0.1', port), partial(Handler, directory=str(directory)))
    server.serve_forever()


def start_dashboard(project=PROJECT, port=8878, open_browser=True):
    project = Path(project).resolve()
    if not (project / 'dashboard/dist/index.html').is_file():
        raise ValueError('Dashboard build missing; run dashboard-build first')
    if not _health(port, project):
        reports = project / 'reports/dashboard'
        reports.mkdir(parents=True, exist_ok=True)
        with (reports / 'server.log').open('ab') as log:
            flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0) | getattr(subprocess, 'DETACHED_PROCESS', 0)
            child = subprocess.Popen([sys.executable, '-B', '-m', 'pdd_monitor.dashboard_runtime', 'serve',
                                      '--project', str(project), '--port', str(port)], cwd=project,
                                     stdin=subprocess.DEVNULL, stdout=log, stderr=log, creationflags=flags)
        for _ in range(30):
            if _health(port, project):
                break
            if child.poll() is not None:
                raise ValueError('Preview could not start; port may be occupied. See reports/dashboard/server.log')
            time.sleep(0.1)
        else:
            child.terminate()
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=5)
            raise ValueError('Preview startup timed out; see reports/dashboard/server.log')
    url = f'http://127.0.0.1:{port}/'
    from .collection_service import browser_mode
    if open_browser and browser_mode(project) != 'codex_iab':
        _open_chrome(url)
    return {'status': 'ready', 'url': url, 'website_collection_performed': False}


def _open_chrome(url):
    """Respect the user's Chrome-only preference, including desktop launchers."""
    candidates = [os.environ.get('PDD_CHROME')]
    for variable, fallback in (('LOCALAPPDATA', ''), ('PROGRAMFILES', 'C:/Program Files'), ('PROGRAMFILES(X86)', 'C:/Program Files (x86)')):
        candidates.append(str(Path(os.environ.get(variable, fallback)) / 'Google/Chrome/Application/chrome.exe'))
    candidates.extend(shutil.which(name) for name in ('google-chrome', 'google-chrome-stable'))
    chrome = next((p for p in candidates if p and Path(p).name.lower() in ('chrome.exe', 'google-chrome', 'google-chrome-stable') and Path(p).is_file()), None)
    if not chrome:
        raise ValueError('未找到Google Chrome；面板服务已就绪，请安装Chrome后打开面板，不自动切换浏览器。')
    subprocess.Popen([chrome, url], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['serve'])
    parser.add_argument('--project', type=Path, default=PROJECT)
    parser.add_argument('--port', type=int, default=8878)
    options = parser.parse_args()
    serve(options.project, options.port)
