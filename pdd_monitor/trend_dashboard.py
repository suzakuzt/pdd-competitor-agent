"""Read-only dashboard projections of local, explainable trend signals."""
from copy import deepcopy
from pathlib import Path
import json

from .trend_signals import build_trend_signals
from .trend_decisions import build_decision_queries, PROTOCOL
from .trend_backtest import build_backtest_queries
from .trend_forecast import build_forecast_queries


def build_trend_queries(queries, project):
    result = build_trend_signals(queries['warehouse_focus_items']['rows'],
        queries['warehouse_display_history']['rows'], queries['warehouse_focus_summary']['rows'])
    exported = {}
    for key, label in [('trend_signals', '逐店商品展示趋势与判断依据'),
                       ('trend_signal_summary', '趋势数据积累与模型就绪状态')]:
        source = deepcopy(queries['warehouse_display_history']['source'])
        source.update(label=label, classification='derived',
            grain='one current original card' if key == 'trend_signals' else 'one shop reference date',
            files=list(dict.fromkeys([*source.get('files', []), str(Path(__file__).with_name('trend_signals.py'))])),
            filters=['按店铺和完整轮隔离；只使用本次观察及之前数据；正销量进入关注。'],
            derivation='build_trend_signals(focus_items, display_history, focus_summary)',
            caveats=[*source.get('caveats', []),
                '单日增长只作观察；持续/加速规则未经本项目历史效果验证，不是爆款概率。',
                '24小时展示速度需要明确读取时刻；相邻采集差值不自动等于一天销量。',
                '采集完成后在本机重新构建即更新本面板；没有独立驱动右侧浏览器或外部推送。'],
            metricDefinitions=[{'label': '可解释趋势信号', 'definition': '增长速度、持续性、近段与基线对比；数据完整性和身份依据分别标注，不给伪概率。'},
                               {'label': '7日结果门槛', 'definition': PROTOCOL['description']}])
        exported[key] = {'rows': result[key], 'source': source, 'methods': [{'language': 'python',
            'code': 'build_trend_signals(warehouse_focus_items, warehouse_display_history, warehouse_focus_summary)'}]}
    exported.update(build_decision_queries({**queries, **exported}, project))
    exported.update(build_backtest_queries({**queries, **exported}, project))
    exported.update(build_forecast_queries(queries))
    return exported


def trend_status_manifest(payload):
    queries = payload.get('queries', {})
    signals = queries.get('trend_signals', {}).get('rows', [])
    return {'version': 1, 'shops': [{
        'shop_id': row['shop_id'],
        'reference_run_id': row.get('reference_run_id') or row.get('run_id'),
        'model_version': row.get('model_version'),
        'selected_count': sum(s.get('selected_for_review') is True for s in signals if s['shop_id'] == row['shop_id']),
        'ledger_revision': payload.get('metadata', {}).get('trend_decision_ledger_revision', 0),
        'date': row.get('date')}
        for row in queries.get('trend_signal_summary', {}).get('rows', [])]}


def read_trend_status(project, shop_id):
    if not isinstance(shop_id, str) or not shop_id or len(shop_id) > 100:
        raise ValueError('Invalid shop')
    path = Path(project) / 'dashboard/dist/trend-status.json'
    if not path.exists():
        return {'shop_id': shop_id, 'reference_run_id': None, 'model_version': None,
                'selected_count': 0, 'ledger_revision': 0}
    value = json.loads(path.read_text(encoding='utf-8'))
    if value.get('version') != 1 or not isinstance(value.get('shops'), list):
        raise ValueError('Trend status unavailable')
    matches = [row for row in value['shops'] if row.get('shop_id') == shop_id]
    if len(matches) != 1:
        raise ValueError('Unknown shop')
    return matches[0]
