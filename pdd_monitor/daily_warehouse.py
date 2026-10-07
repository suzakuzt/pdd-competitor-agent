"""Daily canonical views over immutable observations. No writes or title merging."""
from .sales_metrics import SALES_METRIC_LABELS, same_sales_metric

from collections import Counter, defaultdict
from copy import deepcopy
from datetime import datetime, timedelta
import hashlib
from pathlib import Path

from .history import SHANGHAI, _complete, _exact, _goods_id, _run_time, _time_evidence
from .competitor_model import parse_display_price
from .monitoring_cohorts import monitoring_evidence
from .warehouse_focus import CATEGORIES, _category, _yipin, build_focus_queries
from .warehouse_discovery import DISCOVERY_LIMITATIONS, build_discovery_evidence, discovery_counts
from .warehouse_display_history import build_display_history_query

LIMITATIONS = [
    '北京时间按采集结束日期归档；每天每店选择最新完整且有结束边界的一轮，无完整轮时明确展示部分轮。',
    '同日更新只替换数仓展示版本，原始快照与所有原卡永久保留；失败任务不生成新业务记录。',
    '稳定商品ID须在本轮唯一且无冲突；缺ID的原卡各自独立，不按标题、图片或位置拼接曲线。',
    '累计已拼曲线只使用单卡精确已拼/已抢件数；缺失、其他标签及缺采日期保留未知，不补零。',
    '日增量只比较连续日、同店可靠ID、同销量指标和单位和有序实际观察时间；负差值标异常。',
    '展示价格为卡片单一公开金额，券后与未注明券的金额分别展示，不代表SKU价格或实际到手成本。',
    '当前业务中的新品指店铺上新栏目全部商品；30天仅用于时间线范围，不作为主表商品准入或退出条件。',
    'monitor_*为历史首见观察档案元数据：曾用30个北京时间自然日记录观察阶段，首次监测不等于上架日期。这些字段不决定当前新品范围；继承仍只按本店可靠商品ID，缺ID原卡独立保留。',
] + DISCOVERY_LIMITATIONS


def _id(*parts):
    return 'track_' + hashlib.sha256('|'.join(map(str, parts)).encode()).hexdigest()[:24]


def build_daily_warehouse(runs, observations, arrivals=(), summaries=(), *, discovery=None):
    by_run, by_day = defaultdict(list), defaultdict(list)
    run_map = {r['run_id']: r for r in runs}
    if len(run_map) != len(runs):
        raise ValueError('Duplicate run identity')
    for row in observations:
        if row['run_id'] not in run_map:
            raise ValueError('Observation without source run')
        by_run[row['run_id']].append(row)
    monitor = monitoring_evidence(runs, observations, arrivals, summaries)
    if discovery is None:
        discovery = build_discovery_evidence(runs, observations)
    for run in runs:
        start, end = _run_time(run)
        day = datetime.fromtimestamp(end, SHANGHAI).date().isoformat()
        by_day[run['shop_id'], day].append((end, start, run))
    days, records, tracks, points = [], [], {}, []
    for (shop, day), versions in sorted(by_day.items()):
        ordered = sorted(versions, key=lambda v: (v[0], v[1], v[2]['run_id']), reverse=True)
        full = [v for v in ordered if _complete(v[2])]
        run = (full or ordered)[0][2]
        rows = sorted(by_run[run['run_id']], key=lambda r: (r['view_order'], r['observation_id']))
        goods_counts = Counter(_goods_id(r) for r in rows if _goods_id(r))
        verified = lambda r: bool(_goods_id(r) and goods_counts[_goods_id(r)] == 1 and r.get('identity_status') == 'unique_goods_id')
        days.append({'shop_id': shop, 'shop_name': run.get('shop_name'), 'date': day,
                     'run_id': run['run_id'], 'observed_from': run['observed_from'], 'observed_to': run['observed_to'],
                     'snapshot_sha256': run['snapshot_sha256'], 'is_complete': _complete(run),
                     'selection': 'latest_complete' if full else 'partial_fallback',
                     'latest_observed_run_id': ordered[0][2]['run_id'], 'version_count': len(versions),
                     'all_run_ids': [v[2]['run_id'] for v in ordered], 'card_count': len(rows),
                     'verified_identity_count': sum(verified(r) for r in rows)})
        categories = Counter(_category(row) for row in rows)
        days[-1].update({category + '_count': categories[category] for category in CATEGORIES})
        days[-1].update(discovery_counts(rows, discovery, day))
        for row in rows:
            reliable = verified(row)
            track = _id(shop, 'goods', _goods_id(row)) if reliable else _id(shop, 'observation', row['observation_id'])
            identity = 'verified_goods_id' if reliable else 'independent_card'
            price = parse_display_price(row.get('price_raw'))
            time = _time_evidence(row, run)
            sales = row.get('sales_value') if (_exact(row) and row.get('sales_label') in SALES_METRIC_LABELS and row.get('sales_unit') == '件' and row.get('sales_raw')) else None
            record = {key: row.get(key) for key in ('observation_id', 'run_id', 'view_order', 'title', 'goods_id', 'url', 'price_raw',
                       'sales_raw', 'sales_label', 'sales_unit', 'sales_value', 'sales_precision', 'identity_status',
                       'observed_at', 'observed_at_precision', 'asset_sha256', 'image_content_status', 'image_url',
                       'image_content_kind', 'image_content_mime', 'image_acquisition_url', 'image_variant_provenance', 'image_original_bytes_verified')}
            record.update(shop_id=shop, date=day, track_id=track, identity_basis=identity,
                          is_complete=_complete(run), category=_category(row), yipin_value=_yipin(row),
                          **monitor[row['observation_id']])
            record.update(discovery[row['observation_id']])
            records.append(record)
            point = {**record, 'cumulative_yipin': sales if time['valid'] else None,
                     'display_price_yuan': price['fen'] / 100 if price['fen'] is not None and time['valid'] else None,
                     'price_condition': price['condition'], 'price_parse_status': price['status'],
                     'time_valid': time['valid'], 'time_lower_epoch': time['lower'], 'time_upper_epoch': time['upper'],
                     'is_complete': _complete(run), 'snapshot_sha256': run['snapshot_sha256'],
                     'daily_delta': None, 'delta_status': 'no_previous_day', 'baseline_observation_id': None}
            points.append(point)
            tracks[track] = {'track_id': track, 'shop_id': shop, 'title': row.get('title'), 'goods_id': _goods_id(row) if reliable else None,
                             'identity_basis': identity, 'latest_date': day, 'latest_observation_id': row['observation_id']}
    previous, counts = {}, Counter(p['track_id'] for p in points)
    for point in sorted(points, key=lambda p: (p['date'], p['track_id'])):
        old = previous.get(point['track_id'])
        if point['identity_basis'] != 'verified_goods_id':
            point['delta_status'] = 'identity_unverified'
        elif old:
            point['baseline_observation_id'] = old['observation_id']
            if datetime.fromisoformat(point['date']) - datetime.fromisoformat(old['date']) != timedelta(days=1):
                point['delta_status'] = 'missing_previous_day'
            elif not (old['time_valid'] and point['time_valid'] and old['time_upper_epoch'] < point['time_lower_epoch']):
                point['delta_status'] = 'time_unverified'
            elif old['cumulative_yipin'] is None or point['cumulative_yipin'] is None:
                point['delta_status'] = 'sales_not_comparable'
            else:
                change = point['cumulative_yipin'] - old['cumulative_yipin']
                point['delta_status'] = 'negative_anomaly' if change < 0 else 'comparable'
                point['daily_delta'] = change if change >= 0 else None
        previous[point['track_id']] = point
    for track in tracks.values():
        track['recorded_day_count'] = counts[track['track_id']]
    watch_records = list(records)
    selected_ids = {r['observation_id'] for r in records}
    # A same-day replacement must not erase an independent discovery anchor.
    # Keep such cards explicitly archived, outside the canonical daily counts.
    for row in observations:
        meta = monitor[row['observation_id']]
        if row['observation_id'] in selected_ids or meta['monitor_origin'] != 'first_observed_candidate' or meta['monitor_first_observation_id'] != row['observation_id']:
            continue
        run = run_map[row['run_id']]
        reliable = row.get('identity_status') == 'unique_goods_id' and sum(_goods_id(p) == _goods_id(row) for p in by_run[row['run_id']]) == 1 and bool(_goods_id(row))
        archived = {key: row.get(key) for key in records[0] if key in row} if records else dict(row)
        archived.update(shop_id=run['shop_id'], date=datetime.fromtimestamp(_run_time(run)[1], SHANGHAI).date().isoformat(),
                        track_id=_id(run['shop_id'], 'goods', _goods_id(row)) if reliable else _id(run['shop_id'], 'observation', row['observation_id']),
                        identity_basis='verified_goods_id' if reliable else 'independent_card', archived_anchor=True, **meta)
        archived.update(category=_category(row), yipin_value=_yipin(row), is_complete=_complete(run),
                        **discovery[row['observation_id']])
        watch_records.append(archived)
    return {'warehouse_days': days, 'warehouse_records': records, 'warehouse_watch_records': watch_records,
            'warehouse_tracks': list(tracks.values()), 'warehouse_points': points}


def build_warehouse_queries(queries):
    discovery = build_discovery_evidence(queries['runs']['rows'], queries['observations']['rows'])
    result = build_daily_warehouse(queries['runs']['rows'], queries['observations']['rows'],
                                   queries.get('new_arrival_items', {}).get('rows', []),
                                   queries.get('new_arrival_summary', {}).get('rows', []), discovery=discovery)
    grains = {'warehouse_days': 'one canonical daily snapshot per shop and Beijing date',
              'warehouse_records': 'one original card from the selected daily snapshot',
              'warehouse_watch_records': 'canonical daily cards plus independent archived first-observation anchors; not current unique goods count',
              'warehouse_tracks': 'one verified same-shop goods ID, or one isolated original card',
              'warehouse_points': 'one recorded daily point per track; never sum cards'}
    labels = {'warehouse_days': '逐店每日有效版本', 'warehouse_records': '每日原卡清单',
              'warehouse_watch_records': '持续监测档案与未入每日版本的首见锚点',
              'warehouse_tracks': '商品身份与独立卡片目录', 'warehouse_points': '逐商品每日展示记录'}
    exported = {}
    for key, rows in result.items():
        source = deepcopy(queries['observations']['source'])
        source['files'] = list(dict.fromkeys([*source.get('files', []),
            *queries.get('new_arrival_items', {}).get('source', {}).get('files', []),
            str(Path(__file__).resolve()), str(Path(__file__).with_name('monitoring_cohorts.py').resolve()),
            str(Path(__file__).with_name('warehouse_discovery.py').resolve()),
            str(Path(__file__).with_name('warehouse_focus.py').resolve())]))
        source.update(label=labels[key], classification='derived', grain=grains[key],
                      sql=queries['runs']['source']['sql'] + ';\n' + queries['observations']['source']['sql'],
                      tables=['runs', 'shops', 'observations', 'image_tasks'],
                      filters=LIMITATIONS[:3], caveats=LIMITATIONS,
                      derivation='pdd_monitor.daily_warehouse.build_daily_warehouse(runs, observations, new_arrival_items, new_arrival_summary)',
                      metricDefinitions=[{'label': '每日有效版本与曲线', 'definition': '；'.join(LIMITATIONS),
                                          'sourceLineage': [{'tables': ['runs', 'observations']}]},
                                         {'label':'历史首见观察档案（非当前新品筛选依据）', 'definition':'monitor_origin、monitor_first_date与monitor_window_days保留此前首见观察档案及其30天阶段元数据；初始完整清单、原固定起点和独立原卡不改。当前新品业务范围为店铺上新栏目全部商品，不按这些字段或首次监测年龄筛选或退出。首次监测不是上架时间，覆盖扩展与旧卡改动仍待核验。'}])
        exported[key] = {'rows': rows, 'source': source, 'methods': [{'language': 'python',
                         'code': 'from pdd_monitor.daily_warehouse import build_daily_warehouse\nrows = build_daily_warehouse(runs, observations, new_arrival_items, new_arrival_summary)[' + repr(key) + ']'}]}
    exported.update(build_focus_queries(queries, discovery=discovery))
    exported.update(build_display_history_query({**queries, **exported}))
    return exported
