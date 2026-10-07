"""One explicit local refresh job for the public work chart; never PDD collection."""
from __future__ import annotations

from datetime import datetime, timezone
import threading
import time

from .artist_research import load_catalog
from .artist_heat import build_heat_queries, fetch_iqiyi_snapshot


def _now():
    return datetime.now(timezone.utc).isoformat()


class ArtistHeatService:
    def __init__(self, project, collector=None, clock=time.monotonic, cooldown=60, publisher=None):
        self.project = project
        self.collector = collector or fetch_iqiyi_snapshot
        self.clock = clock
        self.cooldown = cooldown
        self.publisher = publisher
        self.lock = threading.Lock()
        self.last_started = None
        self.job = {'status': 'idle', 'message': '可手动更新爱奇艺公开作品榜；不会更新拼多多商品。'}

    def status(self):
        with self.lock:
            return dict(self.job)

    def start(self):
        with self.lock:
            if self.job['status'] == 'running':
                return 409, dict(self.job)
            if self.last_started is not None and self.clock() - self.last_started < self.cooldown:
                return 429, {**self.job, 'message': '刚刚已请求更新，请至少间隔一分钟；历史记录仍可查看。'}
            self.last_started = self.clock()
            self.job = {'status': 'running', 'message': '正在读取公开作品榜并保存独立观察…', 'started_at': _now()}
            result = dict(self.job)
            try:
                threading.Thread(target=self._collect, daemon=True, name='artist-heat-refresh').start()
            except RuntimeError:
                self.job = {**self.job, 'status': 'failed', 'finished_at': _now(),
                            'message': '本机更新任务未能启动，已保留原观察。'}
                return 503, dict(self.job)
            return 202, result

    def _collect(self):
        try:
            receipt = self.collector(self.project)
            if not isinstance(receipt, dict) or receipt.get('status') not in ('saved', 'duplicate'):
                raise ValueError('collector did not return a verified success receipt')
            if self.publisher is not None:
                with self.lock:
                    self.job = {**self.job, 'message': '公开作品榜已保存，正在更新面板与来源证据…'}
                publication = self.publisher(self.project)
                if not isinstance(publication, dict) or publication.get('status') != 'built':
                    raise ValueError('dashboard publication did not succeed')
                receipt = {**receipt, 'dashboard_built': True}
            with self.lock:
                self.job = {**self.job, 'status': 'succeeded', 'finished_at': _now(), 'receipt': receipt,
                            'message': '公开作品榜已核对并保存；重复内容不会变成新一期。'}
        except Exception:
            # Do not expose upstream HTML, request tokens or tracebacks through the UI.
            with self.lock:
                self.job = {**self.job, 'status': 'failed', 'finished_at': _now(),
                            'message': '本次榜单读取或面板发布未能完成，已保留历史观察。没有自动重试；成功保存的原始记录可供复核。'}

    def data(self):
        catalog, _ = load_catalog(self.project)
        return {'status': 'ok', 'queries': build_heat_queries(self.project, catalog=catalog)}


def allowed_refresh_request(headers, port):
    """Only an explicit same-origin JSON request may start the fixed collector."""
    host = headers.get('Host', '')
    if host not in (f'127.0.0.1:{port}', f'localhost:{port}'):
        return False
    if headers.get('Origin') != 'http://' + host:
        return False
    if headers.get('Content-Type', '').split(';', 1)[0].strip().lower() != 'application/json':
        return False
    try:
        size = int(headers.get('Content-Length', '-1'))
    except (ValueError, TypeError):
        return False
    return 0 < size <= 64
