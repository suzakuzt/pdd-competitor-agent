"""Small, source-backed constant-speed scenarios. No training or ledger writes."""
from collections import Counter, defaultdict
from copy import deepcopy
from datetime import date
from pathlib import Path
import re

from .history import _complete, _run_time, _time_evidence
from .trend_signals import RULES, _edge, _ewma, _point_valid
from .warehouse_focus import _day, _pairs, _yipin


FORECAST_VERSION = 'recent2plus_elapsed_ewma_scenarios_v1'
MINIMUM_INTERVALS = 2
RECENT_INTERVALS = 3
PARAMETERS = {'minimum_intervals': MINIMUM_INTERVALS, 'recent_intervals': RECENT_INTERVALS,
              'ewma_alpha_24h': RULES['ewma_alpha'], 'minimum_interval_hours': 6,
              'maximum_interval_hours': 48, 'horizons_days': [1, 7],
              'ewma_method': 'elapsed_time_adjusted_24h_alpha',
              'scenario_method': 'recent_interval_min_max_rate_times_horizon',
              'sales_metric': 'exact_yipin_or_yiqiang_items'}
LIMITATIONS = [
    '估算只对本店当前有效完整轮、已核验主图和精确已拼/已抢正销量的原卡生成；其余商品仍保留真实原值和不足原因。',
    '至少3个连续有效日期、末端2段有序且6至48小时的明确读取区间；缺采、部分轮、身份冲突、下降或时间精度不足切断连续段，不补零。',
    '使用最近最多3段、有2段即可的展示速度，按实际时长折算24小时，以24小时alpha=0.4的时距校正EWMA作初步估算；假设该速度未来不变，推算1天和7天展示增量。',
    '情景下限及上限只是实际采用的最近2或3段最低及最高展示速度延伸到未来，不是置信区间，不保证未来落在范围内。',
    '已确认商品和同标题同原图展示线索分开；不确认同一SKU，不将独立原卡合并。',
    '本方法未经未来效果验证，不提供成功概率、实际订单或收益预测；不修改历史推荐台账或原7天验证协议。',
]
REASONS = {
    'estimated': '按最近有效区间的平滑速度初步估算',
    'not_positive_exact_sales': '等待精确已拼/已抢正销量',
    'image_unverified': '当前原卡主图尚未核验',
    'incomplete_snapshot': '等待本店完整采集',
    'insufficient_contiguous_intervals': '连续有效区间不足2段',
    'missing_or_invalid_point': '历史原卡、完整轮或读取时间不足',
    'missing_day': '相邻日期缺采，需重新积累连续区间',
    'identity_unverified': '相邻原卡身份链尚未核实',
    'source_identity_conflict': '相邻原始来源与展示对应关系冲突',
    'negative_display_anomaly': '展示销量下降，需核对来源后重新积累',
    'time_unverified': '读取时间无法严格比较',
    'precision_unknown': '读取时刻精度不足，不能折算速度',
    'window_only': '仅有读取时间窗口，不能折算单值速度',
    'irregular_interval': '读取间隔不在6至48小时范围内',
    'invalid_delta_evidence': '展示差值与原卡证据不一致',
}


def build_trend_forecasts(focus_items, display_history, focus_summary, runs, observations):
    """Pure projection over canonical current cards and their original source rows."""
    run_map = {row['run_id']: row for row in runs}
    raw = {row['observation_id']: row for row in observations}
    summaries = {row['shop_id']: row for row in focus_summary}
    if len(run_map) != len(runs) or len(raw) != len(observations) or len(summaries) != len(focus_summary):
        raise ValueError('Duplicate forecast source identity')
    by_run, by_shop, by_day = defaultdict(list), defaultdict(list), defaultdict(list)
    times = {key: _run_time(row) for key, row in run_map.items()}
    for row in observations:
        if row['run_id'] not in run_map:
            raise ValueError('Forecast observation without source run')
        by_run[row['run_id']].append(row)
    for run in runs:
        by_shop[run['shop_id']].append(run)
        by_day[run['shop_id'], _day(run).isoformat()].append(run)
    current_runs = {}
    for shop, versions in by_shop.items():
        ordered = sorted(versions, key=lambda r: (*reversed(times[r['run_id']]), r['run_id']), reverse=True)
        current_runs[shop] = next((r for r in ordered if _complete(r)), ordered[0])
    daily_runs = {}
    for key, versions in by_day.items():
        ordered = sorted(versions, key=lambda r: (*reversed(times[r['run_id']]), r['run_id']), reverse=True)
        daily_runs[key] = next((r for r in ordered if _complete(r)), ordered[0])['run_id']
    for shop, summary in summaries.items():
        current = current_runs.get(shop)
        if (current is None or summary.get('run_id') != current['run_id']
                or summary.get('date') != _day(current).isoformat()
                or summary.get('is_complete') is not _complete(current)
                or summary.get('snapshot_sha256') != current.get('snapshot_sha256')):
            raise ValueError('Forecast summary is not the current canonical shop snapshot')
    items = {}
    compare_fields = ('title', 'goods_id', 'identity_status', 'image_url', 'asset_sha256',
                      'image_content_status', 'sales_raw', 'sales_value', 'sales_label',
                      'sales_unit', 'sales_precision', 'observed_at', 'observed_at_precision')
    for item in focus_items:
        key = item['shop_id'], item['run_id'], item['observation_id']
        original, run, summary = raw.get(item['observation_id']), run_map.get(item['run_id']), summaries.get(item['shop_id'])
        if key in items:
            raise ValueError('Duplicate current forecast card')
        if (not original or not run or not summary or original['run_id'] != run['run_id']
                or run['shop_id'] != item['shop_id'] or current_runs[item['shop_id']]['run_id'] != run['run_id']
                or summary.get('run_id') != run['run_id'] or summary.get('date') != item['date']
                or _day(run).isoformat() != item['date'] or item.get('is_complete') is not _complete(run)
                or summary.get('snapshot_sha256') != run.get('snapshot_sha256')):
            raise ValueError('Forecast focus is not the current canonical shop snapshot')
        if any(item.get(field) != original.get(field) for field in compare_fields):
            raise ValueError('Forecast anchor differs from original card')
        items[key] = item
    grouped, seen = defaultdict(list), set()
    pair_cache = {}

    def pair(current, previous):
        key = current, previous
        if key not in pair_cache:
            pair_cache[key] = _pairs(by_run[current], by_run[previous])
        return pair_cache[key]

    for point in display_history:
        key = point['shop_id'], point['anchor_run_id'], point['anchor_observation_id']
        if key not in items:
            raise ValueError('Forecast history anchor is outside current shop scope')
        item, run = items[key], run_map.get(point['run_id'])
        point_key = (*key, point['date'])
        date.fromisoformat(point['date'])
        if point_key in seen or point['date'] > item['date'] or point.get('anchor_date') != item['date']:
            raise ValueError('Duplicate, future or mismatched forecast history date')
        seen.add(point_key)
        if (not run or run['shop_id'] != item['shop_id'] or _day(run).isoformat() != point['date']
                or daily_runs[item['shop_id'], point['date']] != run['run_id']
                or (point['date'] == item['date'] and run['run_id'] != item['run_id'])
                or point.get('snapshot_sha256') != run.get('snapshot_sha256')
                or point.get('is_complete') is not _complete(run)):
            raise ValueError('Forecast history has cross-shop or mismatched run evidence')
        original = raw.get(point.get('observation_id'))
        if point.get('observation_id') is not None:
            if original is None or original['run_id'] != run['run_id']:
                raise ValueError('Forecast history original card does not belong to cited run')
            timing = _time_evidence(original, run)
            expected = {'title': original.get('title'), 'sales_raw': original.get('sales_raw'),
                        'sales_label': original.get('sales_label'), 'sales_unit': original.get('sales_unit'),
                        'observed_at': timing['observed_at'], 'observed_at_precision': timing['precision'],
                        'time_valid': timing['valid'], 'time_lower_epoch': timing['lower'], 'time_upper_epoch': timing['upper'],
                        'cumulative_yipin': _yipin(original) if timing['valid'] else None}
            if any(point.get(field) != value for field, value in expected.items()):
                raise ValueError('Forecast history value or time differs from original evidence')
            if _point_valid(point):
                if point['run_id'] == item['run_id']:
                    matched, basis = original, 'current_card'
                    if original['observation_id'] != item['observation_id'] or point.get('point_status') != 'anchor':
                        raise ValueError('Forecast current history point is not its anchor')
                else:
                    matched, basis, _ = pair(item['run_id'], point['run_id'])[item['observation_id']]
                if matched is None or matched['observation_id'] != original['observation_id'] or point.get('match_basis') != basis:
                    raise ValueError('Forecast history identity differs from original card pairing')
        elif _point_valid(point):
            raise ValueError('Forecast history valid point lacks an original card')
        grouped[key].append(point)

    result = []
    for key, item in items.items():
        points = sorted(grouped[key], key=lambda point: point['date'])
        tail, edges, reason = [], [], 'insufficient_contiguous_intervals'
        if points and points[-1]['date'] == item['date'] and _point_valid(points[-1]):
            tail = [points[-1]]
            for index in range(len(points) - 2, -1, -1):
                old, new = points[index:index + 2]
                edge = _edge(old, new)
                if not edge['valid'] or edge['rate_status'] != 'normalized_exact_interval':
                    reason = edge['reason'] if not edge['valid'] else edge['rate_status']
                    break
                direct, basis, _ = pair(new['run_id'], old['run_id'])[new['observation_id']]
                if direct is None or direct['observation_id'] != old['observation_id'] or basis != edge['basis']:
                    reason = 'source_identity_conflict'
                    break
                edges.insert(0, edge)
                tail.insert(0, old)
        elif points:
            reason = 'missing_or_invalid_point'
        available = len(edges)
        edges, tail = edges[-RECENT_INTERVALS:], tail[-(RECENT_INTERVALS + 1):]
        bases = {edge['basis'] for edge in edges}
        channel = 'provisional' if 'provisional_title_image' in bases else 'confirmed' if bases else None
        value = _yipin(item)
        row = {field: item.get(field) for field in ('shop_id', 'run_id', 'observation_id', 'date', 'title',
               'view_order', 'track_id', 'image_url', 'asset_sha256', 'image_content_status', 'image_content_kind',
               'sales_raw', 'price_raw', 'category')}
        row.update(forecast_id=f"{FORECAST_VERSION}:{item['shop_id']}:{item['run_id']}:{item['observation_id']}",
                   anchor_run_id=item['run_id'], anchor_observation_id=item['observation_id'], yipin_value=value,
                   algorithm_version=FORECAST_VERSION, algorithm_parameters=deepcopy(PARAMETERS),
                   forecast_status='insufficient_data', reason=reason,
                   normalized_interval_count=available, minimum_intervals=MINIMUM_INTERVALS,
                   used_interval_count=len(edges),
                   remaining_intervals=max(0, MINIMUM_INTERVALS - available),
                   evidence_channel=channel, identity_confirmed=channel == 'confirmed', same_sku_verified=False,
                   forecast_validated=False, scenario_range_kind='historical_speed_scenarios',
                   assumption=f'未来展示增长速度保持最近{len(edges)}段的平滑水平' if edges else None,
                   ewma_rate24=None, recent_min_rate24=None, recent_max_rate24=None,
                   forecast_increment_1d=None, forecast_increment_7d=None,
                   scenario_low_1d=None, scenario_high_1d=None, scenario_low_7d=None, scenario_high_7d=None,
                   forecast_from=tail[-1].get('observed_at') if tail else None,
                   evidence_observation_ids=[p['observation_id'] for p in tail],
                   evidence_run_ids=[p['run_id'] for p in tail], evidence_dates=[p['date'] for p in tail],
                   evidence_snapshot_sha256=[p['snapshot_sha256'] for p in tail],
                   evidence_time_window={'from': tail[0].get('observed_at'), 'to': tail[-1].get('observed_at')} if tail else None,
                   evidence_intervals=[{'from_observation_id': old['observation_id'], 'to_observation_id': new['observation_id'],
                       'from_run_id': old['run_id'], 'to_run_id': new['run_id'], 'from_date': old['date'], 'to_date': new['date'],
                       'from_time': old['observed_at'], 'to_time': new['observed_at'],
                       'interval_hours': edge['interval_hours'], 'display_delta': edge['delta'],
                       'rate24': edge['rate24'], 'match_basis': edge['basis']}
                       for old, new, edge in zip(tail, tail[1:], edges)])
        if value is None or value <= 0:
            row.update(forecast_status='excluded', reason='not_positive_exact_sales')
        elif item.get('is_complete') is not True:
            row['reason'] = 'incomplete_snapshot'
        elif item.get('image_content_status') != 'verified_local' or not re.fullmatch(r'[0-9a-f]{64}', str(item.get('asset_sha256', ''))):
            row.update(forecast_status='excluded', reason='image_unverified')
        elif available >= MINIMUM_INTERVALS:
            speed, low, high = _ewma(edges), min(edge['rate24'] for edge in edges), max(edge['rate24'] for edge in edges)
            row.update(forecast_status='estimated', reason='estimated', ewma_rate24=speed,
                       recent_min_rate24=low, recent_max_rate24=high,
                       forecast_increment_1d=speed, forecast_increment_7d=speed * 7,
                       scenario_low_1d=low, scenario_high_1d=high, scenario_low_7d=low * 7, scenario_high_7d=high * 7)
        row['reason_label'] = REASONS.get(row['reason'], '证据不足，暂不估算')
        result.append(row)
    summary_rows = []
    for shop, summary in sorted(summaries.items()):
        rows = [row for row in result if row['shop_id'] == shop]
        counts = Counter(row['forecast_status'] for row in rows)
        summary_rows.append(dict(shop_id=shop, date=summary['date'], reference_run_id=summary['run_id'],
            is_complete=summary.get('is_complete') is True, total_card_count=len(rows),
            positive_card_count=sum(row['yipin_value'] is not None and row['yipin_value'] > 0 for row in rows),
            estimated_count=counts['estimated'], insufficient_count=counts['insufficient_data'], excluded_count=counts['excluded'],
            confirmed_estimated_count=sum(row['forecast_status'] == 'estimated' and row['evidence_channel'] == 'confirmed' for row in rows),
            provisional_estimated_count=sum(row['forecast_status'] == 'estimated' and row['evidence_channel'] == 'provisional' for row in rows),
            maximum_normalized_interval_count=max((row['normalized_interval_count'] for row in rows), default=0),
            minimum_intervals=MINIMUM_INTERVALS, algorithm_version=FORECAST_VERSION, forecast_validated=False))
    return {'trend_forecasts': result, 'trend_forecast_summary': summary_rows}


def build_forecast_queries(queries):
    result = build_trend_forecasts(*(queries[key]['rows'] for key in
        ('warehouse_focus_items', 'warehouse_display_history', 'warehouse_focus_summary', 'runs', 'observations')))
    source = deepcopy(queries['warehouse_display_history']['source'])
    source.update(classification='derived',
        files=list(dict.fromkeys([*source.get('files', []), str(Path(__file__).resolve()),
                                  str(Path(__file__).with_name('trend_signals.py').resolve())])),
        filters=LIMITATIONS[:2], caveats=[*source.get('caveats', []), *LIMITATIONS],
        derivation='pdd_monitor.trend_forecast.build_trend_forecasts(focus_items, display_history, focus_summary, runs, observations)',
        metricDefinitions=[{'label': '近期速度延续估算', 'definition': '；'.join(LIMITATIONS),
                            'sourceLineage': [{'tables': ['runs', 'observations', 'image_tasks', 'images.source_links']}]}])
    return {key: {'rows': rows, 'source': dict(source,
                label='当前原卡未来展示增量情景' if key == 'trend_forecasts' else '本店近期估算数据就绪情况',
                grain='one current original card' if key == 'trend_forecasts' else 'one current canonical shop snapshot'),
                'methods': [{'language': 'python', 'code': 'from pdd_monitor.trend_forecast import build_forecast_queries\nbuild_forecast_queries(queries)[' + repr(key) + ']'}]}
            for key, rows in result.items()}
