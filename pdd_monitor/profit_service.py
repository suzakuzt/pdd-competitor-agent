"""Loopback-only user-owned trial records, separate from public competitor evidence."""
from datetime import datetime, timezone
from pathlib import Path
import threading

from .profit_lab import evaluate_trial, save_trial
from .store import _connect_readonly


def _now():
    return datetime.now(timezone.utc).isoformat()


def allowed_profit_request(headers, port):
    host = headers.get('Host', '')
    if host not in (f'127.0.0.1:{port}', f'localhost:{port}'):
        return False
    if headers.get('Origin') != 'http://' + host:
        return False
    if headers.get('Content-Type', '').split(';', 1)[0].strip().lower() != 'application/json':
        return False
    try:
        size = int(headers.get('Content-Length', '-1'))
    except (TypeError, ValueError):
        return False
    return 0 < size <= 65536


def validate_trial_reference(project, trial):
    if not isinstance(trial, dict):
        raise ValueError('试品内容必须为对象')
    shop = trial.get('shop_id')
    observation = trial.get('observation_id')
    if not isinstance(shop, str) or not shop or isinstance(observation, bool) or not isinstance(observation, int):
        raise ValueError('请选择有真实观察的店铺和原卡，原卡编号必须是整数')
    connection = _connect_readonly(Path(project) / 'data')
    try:
        row = connection.execute('SELECT r.shop_id FROM observations o JOIN runs r ON r.run_id=o.run_id WHERE o.observation_id=?', (observation,)).fetchone()
    finally:
        connection.close()
    if row is None or row['shop_id'] != shop:
        raise ValueError('原卡不存在或不属于所选店铺；不得绑定到其他店铺')


class ProfitService:
    def __init__(self, project, publisher, validator=validate_trial_reference, saver=save_trial, evaluator=evaluate_trial):
        self.project = Path(project)
        self.publisher = publisher
        self.validator = validator
        self.saver = saver
        self.evaluator = evaluator
        self.lock = threading.Lock()
        self.job = {'status': 'idle', 'message': '试品记录保存在本机项目；不自动下单、投放或修改平台商品。'}

    def status(self):
        with self.lock:
            return dict(self.job)

    def preview(self, payload):
        if not isinstance(payload, dict) or set(payload) != {'trial'}:
            raise ValueError('预览只接受 trial 字段')
        self.validator(self.project, payload['trial'])
        analysis = self.evaluator(payload['trial'])
        return {'status': 'ok', 'analysis': analysis, 'saved': False}

    def start(self, payload):
        if not isinstance(payload, dict) or set(payload) != {'trial', 'expected_revision'}:
            raise ValueError('保存需完整试品及原版本号')
        revision = payload['expected_revision']
        if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
            raise ValueError('原版本号必须为非负整数')
        self.validator(self.project, payload['trial'])
        self.evaluator(payload['trial'])
        with self.lock:
            if self.job['status'] == 'running':
                return 409, dict(self.job)
            receipt = self.saver(self.project, payload['trial'], revision)
            if not isinstance(receipt, dict) or receipt.get('status') not in ('saved', 'unchanged', 'duplicate'):
                raise ValueError('未取得有效的保存收据')
            self.job = {'status': 'running', 'started_at': _now(), 'receipt': receipt,
                        'message': '试品版本已保存，正在同步面板与来源…'}
            result = dict(self.job)
            try:
                threading.Thread(target=self._publish, daemon=True, name='profit-trial-publish').start()
            except RuntimeError:
                self.job = {**self.job, 'status': 'failed', 'finished_at': _now(),
                            'message': '试品记录已保存，但面板更新任务未能启动。原版本和新版本均保留。'}
                return 503, dict(self.job)
            return 202, result

    def _publish(self):
        try:
            result = self.publisher(self.project)
            if not isinstance(result, dict) or result.get('status') != 'built':
                raise ValueError('publication failed')
            with self.lock:
                self.job = {**self.job, 'status': 'succeeded', 'finished_at': _now(),
                            'receipt': {**self.job['receipt'], 'dashboard_built': True},
                            'message': '试品记录与面板已更新；建议供人工决定，不会自动花费预算。'}
        except Exception:
            with self.lock:
                self.job = {**self.job, 'status': 'failed', 'finished_at': _now(),
                            'message': '试品版本已保存，但面板构建失败；历史未丢失，请重新构建面板后核对。'}
