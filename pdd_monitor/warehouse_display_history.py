"""Daily display evidence anchored to current cards, without merging observations."""
from collections import defaultdict
from copy import deepcopy
from datetime import date, timedelta
from pathlib import Path

from .competitor_model import parse_display_price
from .history import _complete, _run_time, _time_evidence
from .warehouse_focus import _day, _pairs, _yipin, build_warehouse_focus


LIMITATIONS = [
    '每条展示历史以本店当前完整轮的一张原卡为锚点，覆盖全部已留存日期；1—10与>10都可查看，不按首次监测年龄筛选。',
    '每天只取本店最新完整且有结束边界的轮次；当天仅部分轮时显示缺口，没有采集的日期由图表断线，不补零。',
    '可靠唯一商品ID与同店两轮唯一同标题同原图的展示线索分别标注；后者不确认同一SKU、不合并原卡，不改可靠ID轨迹。',
    '同标题同图重复时不择一，标题或图片单独相同不配对；公开展示变体的SHA不当作原图相同字节证据。',
    '累计已拼仅取精确已拼/已抢件数；日增量还要求连续日期、相邻来源卡可直接配对、时间有序和同销量指标和单位。负差值为异常，不是负订单。',
    '展示价格保持原卡价格口径，券后与未注明券的价格分开；它不代表SKU价格或实际到手成本。',
]


def build_display_history(runs, observations, focus=None):
    focus = focus or build_warehouse_focus(runs, observations)
    run_map = {run['run_id']: run for run in runs}
    by_run, by_shop_day = defaultdict(list), defaultdict(list)
    for row in observations:
        by_run[row['run_id']].append(row)
    for run in runs:
        by_shop_day[run['shop_id'], _day(run).isoformat()].append(run)
    daily = {}
    for key, versions in by_shop_day.items():
        ordered = sorted(versions, key=lambda r: (*reversed(_run_time(r)), r['run_id']), reverse=True)
        daily[key] = next((run for run in ordered if _complete(run)), ordered[0])
    anchors_by_shop = defaultdict(list)
    for row in focus['warehouse_focus_items']:
        anchors_by_shop[row['shop_id']].append(row)
    source_rows = {row['observation_id']: row for row in observations}
    pair_cache = {}
    def pairs(current_run, baseline_run):
        key = current_run['run_id'], baseline_run['run_id']
        if key not in pair_cache:
            pair_cache[key] = _pairs(by_run[key[0]], by_run[key[1]])
        return pair_cache[key]
    points = []
    for summary in focus['warehouse_focus_summary']:
        shop = summary['shop_id']
        target = run_map[summary['run_id']]
        days = sorted((day, run) for (candidate_shop, day), run in daily.items()
                      if candidate_shop == shop and day <= summary['date'])
        for anchor in anchors_by_shop[shop]:
            previous = None
            for day, run in days:
                old, basis, status = None, None, None
                if not _complete(target):
                    status = 'target_incomplete'
                elif not _complete(run):
                    status = 'incomplete_day'
                elif run['run_id'] == target['run_id']:
                    old, basis, status = source_rows[anchor['observation_id']], 'current_card', 'anchor'
                else:
                    old, basis, reason = pairs(target, run)[anchor['observation_id']]
                    status = 'matched' if old is not None else reason
                time = _time_evidence(old, run)
                if old is not None and not time['valid']:
                    status = 'time_unverified'
                price = parse_display_price(old.get('price_raw')) if old is not None else {'fen': None, 'condition': None, 'status': None}
                point = {
                    'display_history_id': f"{anchor['observation_id']}:{day}",
                    'shop_id': shop, 'anchor_observation_id': anchor['observation_id'],
                    'anchor_run_id': anchor['run_id'], 'anchor_track_id': anchor['track_id'],
                    'anchor_date': anchor['date'], 'anchor_title': anchor['title'],
                    'anchor_category': anchor['category'], 'date': day, 'run_id': run['run_id'],
                    'snapshot_sha256': run['snapshot_sha256'], 'is_complete': _complete(run),
                    'observation_id': old['observation_id'] if old is not None else None,
                    'point_status': status, 'match_basis': basis,
                    'title': old.get('title') if old is not None else None,
                    'view_order': old.get('view_order') if old is not None else None,
                    'sales_raw': old.get('sales_raw') if old is not None else None,
                    'sales_label': old.get('sales_label') if old is not None else None,
                    'sales_unit': old.get('sales_unit') if old is not None else None,
                    'price_raw': old.get('price_raw') if old is not None else None,
                    'cumulative_yipin': _yipin(old) if old is not None and time['valid'] else None,
                    'display_price_yuan': price['fen'] / 100 if price['fen'] is not None and time['valid'] else None,
                    'price_condition': price['condition'], 'price_parse_status': price['status'],
                    'time_valid': time['valid'], 'time_lower_epoch': time['lower'], 'time_upper_epoch': time['upper'],
                    'observed_at': time['observed_at'], 'observed_at_precision': time['precision'],
                    'baseline_date': previous['date'] if previous is not None else None,
                    'baseline_observation_id': previous['observation_id'] if previous is not None else None,
                    'daily_delta': None, 'delta_status': 'no_previous_day', 'delta_match_basis': None,
                    'break_before': False,
                }
                if previous is not None:
                    if date.fromisoformat(day) - date.fromisoformat(previous['date']) != timedelta(days=1):
                        point.update(delta_status='missing_previous_day', break_before=True)
                    elif previous['observation_id'] is None or old is None:
                        point.update(delta_status='identity_unverified', break_before=True)
                    elif not (previous['time_valid'] and time['valid'] and previous['time_upper_epoch'] < time['lower']):
                        point.update(delta_status='time_unverified', break_before=True)
                    else:
                        direct_old, direct_basis, _ = pairs(run, run_map[previous['run_id']])[old['observation_id']]
                        if direct_old is None or direct_old['observation_id'] != previous['observation_id']:
                            point.update(delta_status='identity_unverified', break_before=True)
                        else:
                            point['delta_match_basis'] = direct_basis
                            if previous['cumulative_yipin'] is None or point['cumulative_yipin'] is None:
                                point['delta_status'] = 'sales_not_comparable'
                            else:
                                change = point['cumulative_yipin'] - previous['cumulative_yipin']
                                point.update(delta_status='negative_anomaly' if change < 0 else 'comparable',
                                             daily_delta=change if change >= 0 else None)
                points.append(point)
                previous = point
    return points


def build_display_history_query(queries):
    focus = {key: queries[key]['rows'] for key in ('warehouse_focus_summary', 'warehouse_focus_items')}
    rows = build_display_history(queries['runs']['rows'], queries['observations']['rows'], focus)
    source = deepcopy(queries['observations']['source'])
    source['files'] = list(dict.fromkeys([*source.get('files', []), str(Path(__file__).resolve()),
                                        str(Path(__file__).with_name('warehouse_focus.py').resolve())]))
    source.update(label='单张当前原卡的全监测期逐日展示历史', classification='derived',
                  grain='one current original-card anchor and one recorded shop day; historical observations remain unmerged',
                  sql=queries['runs']['source']['sql'] + ';\n' + queries['observations']['source']['sql'],
                  tables=['runs', 'shops', 'observations', 'image_tasks', 'images.source_links'],
                  filters=LIMITATIONS[:2], caveats=LIMITATIONS,
                  derivation='pdd_monitor.warehouse_display_history.build_display_history(runs, observations)',
                  metricDefinitions=[{'label': '逐日原卡展示历史', 'definition': '；'.join(LIMITATIONS),
                                      'sourceLineage': [{'tables': ['runs', 'observations', 'image_tasks', 'images.source_links']}]}])
    return {'warehouse_display_history': {'rows': rows, 'source': source, 'methods': [{'language': 'python',
        'code': 'from pdd_monitor.warehouse_display_history import build_display_history\nrows = build_display_history(runs, observations)'}]}}
