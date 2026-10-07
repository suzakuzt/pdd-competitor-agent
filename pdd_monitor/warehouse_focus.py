"""Whole-catalogue sales classes and explicit, unmerged daily comparison clues."""
from .sales_metrics import SALES_METRIC_LABELS, same_sales_metric

from collections import Counter, defaultdict
from copy import deepcopy
from datetime import datetime, timedelta
import hashlib
from pathlib import Path

from .history import SHANGHAI, _complete, _exact, _goods_id, _run_time, _time_evidence
from .warehouse_discovery import DISCOVERY_LIMITATIONS, build_discovery_evidence, discovery_counts


CATEGORIES = ('yipin_gt10', 'yipin_1to10', 'yipin_zero', 'other_label', 'unknown')
PERIODS = (('yesterday', 1), ('day_before_yesterday', 2))
LIMITATIONS = [
    '新品指本店上新栏目全部原卡，不按首次发现或30天年龄筛选；默认本店最近完整且有结束边界的一轮。',
    '互斥分类为单卡精确已拼/已抢>10、1—10、0、其他销量标签、缺失或模糊；不跨卡相加。',
    '昨天和前天相对所展示完整轮的北京时间日期，分别只取对应日期的最近完整轮；缺基线不借用其他日期。',
    '可靠商品ID须同店、在两轮各自唯一且无身份冲突；同标题同原图在两轮均唯一时仅显示待核验的变化线索，原卡不合并。',
    '变化只比较同销量指标和单位的精确已拼/已抢件数和有序实际观察时间；负差值为异常，不能当作负订单。',
    '快速增长规则为展示值增加至少10件且增幅至少30%；零基期增至至少10件单列零基期增长，增长率不计算。',
    '图片就绪数只统计已校验本地图片内容，远程图片URL不代表已经保存或全部可用。',
] + DISCOVERY_LIMITATIONS


def _day(run):
    return datetime.fromtimestamp(_run_time(run)[1], SHANGHAI).date()


def _yipin(row):
    raw = row.get('sales_raw')
    return row['sales_value'] if (_exact(row) and row.get('sales_label') in SALES_METRIC_LABELS
        and row.get('sales_unit') == '件' and isinstance(raw, str) and raw.strip()) else None


def _category(row):
    value = _yipin(row)
    if value is not None:
        return 'yipin_gt10' if value > 10 else 'yipin_1to10' if value > 0 else 'yipin_zero'
    return 'other_label' if row.get('sales_label') not in (None, '', *SALES_METRIC_LABELS) else 'unknown'


def _clue_keys(row):
    title = row.get('title')
    if not isinstance(title, str) or not title.strip():
        return ()
    keys = []
    url = row.get('image_url')
    if isinstance(url, str) and url.strip():
        keys.append((title, 'exact_image_url', url))
    sha = row.get('asset_sha256')
    if (isinstance(sha, str) and len(sha) == 64 and row.get('image_content_status') == 'verified_local'
            and row.get('image_content_kind') != 'store_search_variant' and not row.get('image_variant_provenance')):
        keys.append((title, 'verified_image_sha256', sha))
    return keys


def _pairs(current, baseline):
    """Pair evidence for display only. Reserve confirmed pairs before weak clues."""
    counts = [Counter(_goods_id(r) for r in rows if _goods_id(r)) for rows in (current, baseline)]
    def reliable(row, side):
        goods = _goods_id(row)
        return bool(goods and counts[side][goods] == 1 and row.get('identity_status') == 'unique_goods_id')
    def conflict(row, side):
        goods = _goods_id(row)
        return str(row.get('identity_status', '')).startswith('conflict_') or bool(goods and counts[side][goods] > 1)
    old_ids = {_goods_id(r): r for r in baseline if reliable(r, 1)}
    pairs, reserved = {}, set()
    for row in current:
        old = old_ids.get(_goods_id(row)) if reliable(row, 0) else None
        if old:
            pairs[row['observation_id']] = (old, 'confirmed_goods_id', None)
            reserved.add(old['observation_id'])
    indexes = []
    for rows in (current, baseline):
        index = defaultdict(dict)
        for row in rows:
            for key in _clue_keys(row):
                index[key][row['observation_id']] = row
        indexes.append(index)
    def matches(row, index):
        return {oid: candidate for key in _clue_keys(row) for oid, candidate in index.get(key, {}).items()}
    for row in current:
        oid = row['observation_id']
        if oid in pairs:
            continue
        candidates = matches(row, indexes[1])
        status = 'unmatched' if not candidates else 'ambiguous_identity'
        if conflict(row, 0):
            status = 'identity_conflict'
        elif len(candidates) == 1:
            old = next(iter(candidates.values()))
            if conflict(old, 1):
                status = 'identity_conflict'
            elif _goods_id(row) and _goods_id(old) and _goods_id(row) != _goods_id(old):
                status = 'identity_conflict'
            elif old['observation_id'] not in reserved and len(matches(old, indexes[0])) == 1:
                pairs[oid] = (old, 'provisional_title_image', None)
                continue
        pairs[oid] = (None, None, status)
    return pairs


def _comparison(row, current_run, baseline_run, pair, baseline_date, unavailable, time_evidence=None):
    result = dict(baseline_date=baseline_date, baseline_run_id=baseline_run['run_id'] if baseline_run else None,
                  baseline_observation_id=None, baseline_value=None, match_basis=None, status=unavailable,
                  delta=None, growth_rate=None, rapid_growth=False)
    if unavailable:
        return result
    old, basis, reason = pair
    if old is None:
        result['status'] = reason
        return result
    result.update(baseline_observation_id=old['observation_id'], baseline_value=_yipin(old), match_basis=basis)
    evidence = time_evidence or _time_evidence
    old_time, new_time = evidence(old, baseline_run), evidence(row, current_run)
    if not (old_time['valid'] and new_time['valid'] and old_time['upper'] < new_time['lower']):
        result['status'] = 'time_unverified'
    elif _yipin(row) is None or _yipin(old) is None:
        result['status'] = 'sales_not_comparable'
    else:
        change = _yipin(row) - _yipin(old)
        if change < 0:
            result['status'] = 'negative_anomaly'
        else:
            zero = _yipin(old) == 0
            result.update(status='zero_baseline' if zero else 'comparable', delta=change,
                          growth_rate=None if zero else change / _yipin(old),
                          rapid_growth=change >= 10 and (zero or change * 10 >= _yipin(old) * 3))
    return result


def build_warehouse_focus(runs, observations, *, discovery=None):
    """One current original-card row per shop; exact previous dates never substituted."""
    run_map = {r['run_id']: r for r in runs}
    if len(run_map) != len(runs):
        raise ValueError('Duplicate run identity')
    by_run, by_shop = defaultdict(list), defaultdict(list)
    observation_ids = set()
    for row in observations:
        if row['run_id'] not in run_map:
            raise ValueError('Observation without source run')
        if row['observation_id'] in observation_ids:
            raise ValueError('Duplicate observation identity')
        observation_ids.add(row['observation_id'])
        by_run[row['run_id']].append(row)
    for run in runs:
        by_shop[run['shop_id']].append(run)
    if discovery is None:
        discovery = build_discovery_evidence(runs, observations)
    summaries, items = [], []
    time_cache = {}

    def time_evidence(row, run):
        # Reuse validated card times across comparison periods only in this build.
        key = (run['run_id'], row['observation_id'])
        if key not in time_cache:
            time_cache[key] = _time_evidence(row, run)
        return time_cache[key]

    fields = ('observation_id', 'run_id', 'view_order', 'title', 'goods_id', 'goods_url', 'url', 'price_raw',
              'sales_raw', 'sales_label', 'sales_unit', 'sales_value', 'sales_precision', 'identity_status',
              'observed_at', 'observed_at_precision', 'asset_sha256', 'image_content_status', 'image_url',
              'image_content_kind', 'image_content_mime', 'image_acquisition_url', 'image_variant_provenance', 'image_original_bytes_verified')
    for shop, shop_runs in sorted(by_shop.items()):
        ordered = sorted(shop_runs, key=lambda r: (*reversed(_run_time(r)), r['run_id']), reverse=True)
        target = next((r for r in ordered if _complete(r)), ordered[0])
        date = _day(target)
        rows = sorted(by_run[target['run_id']], key=lambda r: (r['view_order'], r['observation_id']))
        goods_counts = Counter(_goods_id(r) for r in rows if _goods_id(r))
        summary = dict(shop_id=shop, shop_name=target.get('shop_name'), date=date.isoformat(),
                       run_id=target['run_id'], snapshot_sha256=target['snapshot_sha256'],
                       observed_from=target['observed_from'], observed_to=target['observed_to'],
                       is_complete=_complete(target), selection='latest_complete' if _complete(target) else 'partial_fallback',
                       latest_observed_run_id=ordered[0]['run_id'], latest_observed_date=_day(ordered[0]).isoformat(),
                       latest_observed_card_count=len(by_run[ordered[0]['run_id']]), card_count=len(rows),
                       image_ready_count=sum(r.get('image_content_status') == 'verified_local' for r in rows),
                       rapid_min_delta=10, rapid_min_growth_rate=.3)
        counts = Counter(_category(r) for r in rows)
        summary.update({category + '_count': counts[category] for category in CATEGORIES})
        summary.update(discovery_counts(rows, discovery, date.isoformat()))
        comparisons = {}
        for prefix, days_ago in PERIODS:
            baseline_date = (date - timedelta(days=days_ago)).isoformat()
            on_day = [r for r in ordered if _day(r).isoformat() == baseline_date]
            baseline = next((r for r in on_day if _complete(r)), None)
            unavailable = ('target_incomplete' if not _complete(target) else
                           'partial_baseline' if on_day and not baseline else
                           'missing_baseline' if not baseline else None)
            pairs = _pairs(rows, by_run[baseline['run_id']]) if baseline and not unavailable else {}
            comparisons[prefix] = (baseline, baseline_date, unavailable, pairs)
            summary.update({prefix + '_baseline_date': baseline_date,
                            prefix + '_baseline_run_id': baseline['run_id'] if baseline else None,
                            prefix + '_status': unavailable or 'available',
                            prefix + '_confirmed_comparable_count': 0, prefix + '_provisional_comparable_count': 0,
                            prefix + '_rapid_confirmed_count': 0, prefix + '_rapid_provisional_count': 0})
        for row in rows:
            item = {key: row.get(key) for key in fields}
            reliable = bool(_goods_id(row) and goods_counts[_goods_id(row)] == 1 and row.get('identity_status') == 'unique_goods_id')
            track_parts = (shop, 'goods', _goods_id(row)) if reliable else (shop, 'observation', row['observation_id'])
            item.update(shop_id=shop, date=date.isoformat(), is_complete=_complete(target),
                        category=_category(row), yipin_value=_yipin(row),
                        track_id='track_' + hashlib.sha256('|'.join(map(str, track_parts)).encode()).hexdigest()[:24],
                        identity_basis='verified_goods_id' if reliable else 'independent_card')
            item.update(discovery[row['observation_id']])
            for prefix, (baseline, baseline_date, unavailable, pairs) in comparisons.items():
                comparison = _comparison(row, target, baseline, pairs.get(row['observation_id']), baseline_date, unavailable, time_evidence)
                item.update({prefix + '_' + key: value for key, value in comparison.items()})
                if comparison['status'] in ('comparable', 'zero_baseline'):
                    certainty = 'confirmed' if comparison['match_basis'] == 'confirmed_goods_id' else 'provisional'
                    summary[prefix + '_' + certainty + '_comparable_count'] += 1
                    if comparison['rapid_growth']:
                        summary[prefix + '_rapid_' + certainty + '_count'] += 1
            items.append(item)
        summaries.append(summary)
    return {'warehouse_focus_summary': summaries, 'warehouse_focus_items': items}


def build_focus_queries(queries, *, discovery=None):
    result = build_warehouse_focus(queries['runs']['rows'], queries['observations']['rows'], discovery=discovery)
    exported = {}
    for key, rows in result.items():
        source = deepcopy(queries['observations']['source'])
        source['files'] = list(dict.fromkeys([*source.get('files', []), str(Path(__file__).resolve()),
                                             str(Path(__file__).with_name('warehouse_discovery.py').resolve())]))
        source.update(label='上新全店分类概览' if key.endswith('summary') else '上新重点原卡与逐日变化线索',
                      classification='derived', grain='one latest reference per shop' if key.endswith('summary') else 'one unmerged original card in each shop latest reference',
                      sql=queries['runs']['source']['sql'] + ';\n' + queries['observations']['source']['sql'],
                      tables=['runs', 'shops', 'observations', 'image_tasks'], filters=LIMITATIONS[:3], caveats=LIMITATIONS,
                      derivation='pdd_monitor.warehouse_focus.build_warehouse_focus(runs, observations)',
                      metricDefinitions=[{'label': '全量分类与快速增长', 'definition': '；'.join(LIMITATIONS),
                                          'sourceLineage': [{'tables': ['runs', 'observations']}]}])
        exported[key] = {'rows': rows, 'source': source, 'methods': [{'language': 'python',
            'code': 'from pdd_monitor.warehouse_focus import build_warehouse_focus\nrows = build_warehouse_focus(runs, observations)[' + repr(key) + ']'}]}
    return exported
