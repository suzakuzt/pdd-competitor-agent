"""Pure, per-shop display tables. Never aggregate sales or infer listing dates."""
from .sales_metrics import SALES_METRIC_LABELS, same_sales_metric

from collections import Counter, defaultdict
from copy import deepcopy
from datetime import datetime
import hashlib
import re
from urllib.parse import parse_qs, urlsplit

UPSTREAM = ('runs', 'observations', 'new_arrival_items', 'new_arrival_summary')
LIMITATIONS = [
    '热销表仅指本店最近完整且有结束边界轮中的单卡精确已拼/已抢>10件，不代表今天热卖或已盈利。',
    '新品关注只是固定起点后的首见线索；首次观察不等于实际上架，newness始终未知。',
    '起点部分造成的补扫首见单列为覆盖复核，可能是旧存量，不进入新品关注。',
    '缺ID原卡不合并；同店同轮同标题且同原图URL或同已校验图SHA仅可折叠查看，不确认同SKU、不相加销量。',
    '新关注、覆盖复核和热销可能引用相同原卡；各表数量不能相加为唯一商品数。',
    '可靠ID展示归并仍保留所有原观察ID；原始历史不变，显示销量只取代表原卡，不求和。',
]


def _require(condition, message):
    if not condition:
        raise ValueError('product_tables: ' + message)


def _rows(queries, key):
    value = queries.get(key, {})
    result = value.get('rows', []) if isinstance(value, dict) else value
    _require(isinstance(result, list) and all(isinstance(row, dict) for row in result), key + ' rows must be objects')
    return result


def _key(value):
    _require(type(value) in (str, int) and value != '' and (type(value) is str or value > 0), 'invalid original ID')
    return str(value)


def _index(rows, field):
    result = {}
    for row in rows:
        key = _key(row.get(field))
        _require(key not in result, 'duplicate ' + field)
        result[key] = row
    return result


def _epoch(value):
    try:
        value = datetime.fromisoformat(value.replace('Z', '+00:00'))
        _require(value.tzinfo is not None, 'timestamp needs timezone')
        return value.timestamp()
    except (TypeError, AttributeError, OverflowError) as error:
        raise ValueError('product_tables: invalid timestamp') from error


def _window(run):
    first, last = _epoch(run.get('observed_from')), _epoch(run.get('observed_to'))
    _require(first <= last, 'reversed run window')
    return first, last


def _complete(run):
    return run.get('status') == 'complete' and (run.get('end_boundary_observed') is True or
        type(run.get('end_boundary_observed')) is int and run['end_boundary_observed'] == 1)


def _hot(row):
    return (row.get('sales_label') in SALES_METRIC_LABELS and row.get('sales_unit') == '件'
        and row.get('sales_precision') == 'exact_display' and type(row.get('sales_value')) is int
        and row['sales_value'] > 10 and isinstance(row.get('sales_raw'), str) and bool(row['sales_raw'].strip()))


def _goods(row):
    value = row.get('goods_id')
    return value if isinstance(value, str) and re.fullmatch(r'[1-9][0-9]*', value) else None


def _reliable(row, counts):
    goods = _goods(row)
    if not goods or counts[row['run_id']][goods] != 1 or row.get('identity_status') != 'unique_goods_id':
        return False
    try:
        ids = parse_qs(urlsplit(row.get('goods_url') or '').query, keep_blank_values=True).get('goods_id', [])
    except (TypeError, ValueError):
        return False
    return not ids or len(ids) == 1 and ids[0] == goods


def _oid(row):
    return _key(row['observation_id'])


def _valid_time(value, run):
    if not isinstance(value, str):
        return None
    try:
        point = _epoch(value)
        return value if _window(run)[0] <= point <= _window(run)[1] else None
    except ValueError:
        return None


def _card_time(row, run, first=False):
    raw = row.get('original') if isinstance(row.get('original'), dict) else {}
    last = _valid_time(row.get('observed_at'), run)
    if first:
        candidate = _valid_time(raw.get('firstObservedAt'), run)
        if candidate and last and _epoch(candidate) <= _epoch(last) and raw.get('firstObservedAtPrecision') in ('card_read', 'batch_read'):
            return candidate, raw['firstObservedAtPrecision']
    return last, row.get('observed_at_precision') or ('legacy_unspecified' if last else 'unknown')


def _title(row):
    value = row.get('normalized_title') or row.get('title') or ''
    return value if isinstance(value, str) and value.strip() else ''


def _fold_count(rows):
    """Count only same-lane/shop/run/title + same nonempty image display groups."""
    parents = list(range(len(rows)))
    known_hashes = [{row['asset_sha256']} if row.get('asset_sha256') and row.get('image_content_status') == 'verified_local' else set() for row in rows]
    def root(index):
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index
    seen = {}
    for index, row in enumerate(rows):
        if not row['normalized_title']:
            continue
        for kind, value in [('url', row.get('image_url')),
                ('sha', row.get('asset_sha256') if row.get('image_content_status') == 'verified_local' else None)]:
            if not isinstance(value, str) or not value.strip():
                continue
            key = row['shop_id'], row['run_id'], row['lane'], row['normalized_title'], kind, value
            for old in seen.get(key, []):
                left, right = root(index), root(old)
                combined = known_hashes[left] | known_hashes[right]
                # A no-SHA intermediary must not bridge conflicting stored bytes.
                if len(combined) <= 1:
                    parents[left] = right
                    known_hashes[right] = combined
            seen.setdefault(key, []).append(index)
    return len({root(index) for index in range(len(rows))})


def _arrival_time(item, anchor, observations, runs):
    status = item.get('first_time_order_status', 'observed_run_order')
    _require(status in ('observed_run_order', 'overlapping_runs_unknown'), 'invalid first time-order status')
    candidates = [observations[_key(oid)] for oid in item.get('first_observation_id_candidates', [anchor['observation_id']])]
    _require(candidates and _oid(anchor) in {_oid(row) for row in candidates}, 'first time evidence must retain anchor')
    run_ids = {row['run_id'] for row in candidates}
    if status == 'observed_run_order':
        _require(run_ids == {anchor['run_id']}, 'ordered first time spans different runs')
    else:
        _require(len(run_ids) > 1 and item.get('first_observed_at') is None, 'overlapping first time must remain unknown')
        component = {anchor['run_id']}
        while True:
            expanded = component | {rid for rid in run_ids if any(_window(runs[rid])[0] < _window(runs[old])[1]
                and _window(runs[old])[0] < _window(runs[rid])[1] for old in component)}
            if expanded == component:
                break
            component = expanded
        _require(component == run_ids, 'first time overlap evidence is disconnected')
    first_window = item.get('first_observation_window')
    _require(isinstance(first_window, dict), 'first observation window missing')
    expected = min(_window(runs[rid])[0] for rid in run_ids), max(_window(runs[rid])[1] for rid in run_ids)
    _require((_epoch(first_window.get('from')), _epoch(first_window.get('to'))) == expected, 'first observation window differs from original run evidence')
    point = item.get('first_observed_at')
    if point is not None:
        _require(_valid_time(point, runs[anchor['run_id']]) is not None, 'first observed point outside anchor or lacks timezone')


def build_product_table_queries(queries):
    """Derive reviewed rows without I/O, mutation, truncation, or current-clock data.

    Bad joins abort export rather than publishing cross-shop or invented rows.
    Missing identity/baseline/images are disclosed gaps, not failed invariants.
    """
    _require(isinstance(queries, dict), 'queries must be an object')
    runs = _index(_rows(queries, 'runs'), 'run_id')
    observations = _index(_rows(queries, 'observations'), 'observation_id')
    summaries = _index(_rows(queries, 'new_arrival_summary'), 'shop_id')
    arrivals = _index(_rows(queries, 'new_arrival_items'), 'arrival_item_id')
    by_shop, by_run = defaultdict(list), defaultdict(list)
    for run in runs.values():
        _key(run.get('shop_id')); _window(run)
        by_shop[run['shop_id']].append(run)
    for row in observations.values():
        _require(row.get('run_id') in runs, 'observation references unknown run')
        run = runs[row['run_id']]
        _require(row.get('shop_id') in (None, run['shop_id']), 'observation shop differs from run')
        _require(type(row.get('view_order')) is int and row['view_order'] > 0, 'invalid card position')
        by_run[row['run_id']].append(row)
    for run_id, run in runs.items():
        rows = by_run[run_id]
        _require(len({row['view_order'] for row in rows}) == len(rows), 'duplicate card positions')
        if 'card_count' in run:
            _require(type(run['card_count']) is int and run['card_count'] == len(rows), 'run card count differs from observations')
    counts = {rid: Counter(_goods(row) for row in rows if _goods(row)) for rid, rows in by_run.items()}
    reliable = {oid: _reliable(row, counts) for oid, row in observations.items()}
    identity_history = defaultdict(list)
    for oid, row in observations.items():
        if reliable[oid]:
            identity_history[(runs[row['run_id']]['shop_id'], _goods(row))].append(row)

    tracking = {}
    for shop, group in by_shop.items():
        summary = summaries.get(shop, {})
        configured = bool(summary.get('tracking_id')) and summary.get('state') not in ('not_configured', 'no_baseline')
        baseline = summary.get('baseline_run_ids') or []
        _require(isinstance(baseline, list) and len(set(baseline)) == len(baseline), 'invalid baseline run list')
        if configured:
            _require(baseline and isinstance(summary.get('started_at'), str), 'configured baseline lacks fixed evidence')
            _epoch(summary['started_at'])
            _require(all(rid in runs and runs[rid]['shop_id'] == shop for rid in baseline), 'baseline crosses shops or references missing run')
            count = sum(len(by_run[rid]) for rid in baseline)
            _require(type(summary.get('baseline_observation_count')) is int and count == summary['baseline_observation_count'], 'baseline original count mismatch')
        else:
            _require(not baseline and not summary.get('tracking_id'), 'unconfigured baseline has contradictory evidence')
        tracking[shop] = (summary, configured, set(baseline))
    _require(all(shop in by_shop for shop in summaries), 'baseline references unobserved shop')

    arrivals_by_card, arrivals_by_shop = defaultdict(list), defaultdict(list)
    related = {}
    for aid, item in arrivals.items():
        anchor = observations.get(_key(item.get('observation_id')))
        _require(anchor is not None and anchor['run_id'] == item.get('run_id'), 'arrival anchor missing or wrong run')
        shop = runs[anchor['run_id']]['shop_id']
        _require(item.get('shop_id') == shop, 'arrival crosses shops')
        summary, configured, baseline = tracking[shop]
        _require(configured and item.get('tracking_id') == summary['tracking_id'], 'arrival lacks matching fixed baseline')
        _require(anchor['run_id'] not in baseline and _window(runs[anchor['run_id']])[0] >= _epoch(summary['started_at']), 'arrival is not after fixed baseline')
        _require(item.get('discovery_kind') in ('first_observed_id_candidate', 'new_card_clue'), 'unknown discovery kind')
        _require(item.get('newness') in (None, 'unknown'), 'arrival cannot assert confirmed listing')
        _require(type(item.get('coverage_expansion_possible')) is bool, 'arrival must explicitly distinguish coverage expansion')
        if item['discovery_kind'] == 'first_observed_id_candidate':
            _require(reliable[_oid(anchor)], 'ID arrival anchor is not unique and reliable')
        linked = {_oid(anchor)}
        for field in ('historical_references', 'subsequent_references'):
            for reference in item.get(field, []):
                card = observations.get(_key(reference.get('observation_id')))
                _require(card is not None and card['run_id'] == reference.get('run_id')
                         and runs[card['run_id']]['shop_id'] == shop, 'arrival reference crosses shop/run')
                if field == 'subsequent_references':
                    if reference.get('match_basis') == 'same_unique_goods_id':
                        _require(reliable[_oid(anchor)] and reliable[_oid(card)] and _goods(anchor) == _goods(card), 'unreliable subsequent ID reference')
                    elif reference.get('match_basis') == 'exact_title_original_image_url_clue_only':
                        _require(bool(anchor.get('image_url')) and (anchor.get('title'), anchor.get('image_url')) == (card.get('title'), card.get('image_url')), 'weak subsequent clue differs from original card')
                    else:
                        raise ValueError('product_tables: unknown subsequent reference basis')
                    linked.add(_oid(card))
        for field in ('latest_observation_ids', 'first_observation_id_candidates'):
            for oid in item.get(field, []):
                _require(_key(oid) in linked, field + ' lacks linked original evidence')
        _arrival_time(item, anchor, observations, runs)
        related[aid] = linked
        for oid in linked:
            arrivals_by_card[oid].append(item)
        arrivals_by_shop[shop].append(item)

    output, shop_rows = [], []
    for shop, group in sorted(by_shop.items()):
        ordered = sorted(group, key=lambda run: (*reversed(_window(run)), run['run_id']), reverse=True)
        latest = ordered[0]
        reference = next((run for run in ordered if _complete(run)), None)
        summary, configured, baseline = tracking[shop]
        candidates = []
        for item in arrivals_by_shop[shop]:
            candidates.append(('coverage_review' if item['coverage_expansion_possible'] else 'new_watch', observations[_key(item['observation_id'])], item))
        if reference:
            candidates.extend(('hot', row, None) for row in by_run[reference['run_id']] if _hot(row))
        buckets = defaultdict(list)
        for lane, row, item in candidates:
            key = ('goods', _goods(row)) if reliable[_oid(row)] else ('observation', _oid(row))
            buckets[(lane, key)].append((row, item))
        local = []
        for (lane, identity_key), entries in buckets.items():
            strong = identity_key[0] == 'goods'
            evidence = identity_history[(shop, identity_key[1])] if strong else [entries[0][0]]
            entry_items = {item['arrival_item_id']: item for _, item in entries if item}
            # A reference heat row always displays that exact reference card.
            # Weak clue links never replace/merge the anchor with a later card.
            if lane == 'hot':
                row = entries[0][0]
            elif strong:
                row = max(evidence, key=lambda r: (*reversed(_window(runs[r['run_id']])), -r['view_order'], _oid(r)))
            else:
                row = entries[0][0]
            for card in evidence:
                for item in arrivals_by_card[_oid(card)]:
                    entry_items[item['arrival_item_id']] = item
            evidence = sorted(evidence, key=lambda r: (*_window(runs[r['run_id']]), r['view_order'], _oid(r)))
            first, last = evidence[0], max(evidence, key=lambda r: (*reversed(_window(runs[r['run_id']])), -r['view_order'], _oid(r)))
            first_at, first_precision = _card_time(first, runs[first['run_id']], True)
            last_at, last_precision = _card_time(last, runs[last['run_id']])
            first_window = {'from': runs[first['run_id']]['observed_from'], 'to': runs[first['run_id']]['observed_to']}
            last_window = {'from': runs[last['run_id']]['observed_from'], 'to': runs[last['run_id']]['observed_to']}
            time_overlap = any(_window(runs[a['run_id']])[0] < _window(runs[b['run_id']])[1]
                and _window(runs[b['run_id']])[0] < _window(runs[a['run_id']])[1]
                for i, a in enumerate(evidence) for b in evidence[i+1:] if a['run_id'] != b['run_id'])
            if time_overlap:
                first_at = last_at = None; first_precision = last_precision = 'unknown'
                first_window = last_window = {'from': min((runs[r['run_id']]['observed_from'] for r in evidence), key=_epoch),
                    'to': max((runs[r['run_id']]['observed_to'] for r in evidence), key=_epoch)}
            if not strong and entry_items:
                anchor_item = next((item for item in entry_items.values() if _key(item['observation_id']) == _oid(row)), None)
                if anchor_item:
                    first_at = anchor_item.get('first_observed_at')
                    first_precision = anchor_item.get('first_observed_at_precision') or 'unknown'
                    first_window = deepcopy(anchor_item.get('first_observation_window') or first_window)
                    time_overlap = time_overlap or anchor_item.get('first_time_order_status') == 'overlapping_runs_unknown'
                    if time_overlap:
                        first_at = None; first_precision = 'unknown'
            in_baseline = any(card['run_id'] in baseline for card in evidence)
            age = '新旧未知' if not configured else '已在起点观察' if in_baseline else '起点后首见待核验' if entry_items else '新旧未知'
            expansion = lane == 'coverage_review' or any(item['coverage_expansion_possible'] for item in entry_items.values())
            label = '补采覆盖线索，可能为旧存量' if expansion else '固定起点后首见待核验' if entry_items else age
            reason = ('本店最近完整轮，单卡精确已拼/已抢>10件；历史展示参考' if lane == 'hot' else
                      '固定起点覆盖不足，补扫首次看到可能是旧存量' if lane == 'coverage_review' else
                      '本店固定起点后已有首次观察证据，实际上架时间未知')
            if not configured:
                reason += '；未设置固定起点，首轮或历史新旧未知'
            values = {key: deepcopy(row.get(key)) for key in ('observation_id', 'run_id', 'title', 'goods_id', 'identity_status',
                'asset_sha256', 'image_status', 'image_content_status', 'image_url', 'goods_url', 'price_raw',
                'sales_raw', 'sales_value', 'sales_label', 'sales_unit', 'sales_precision', 'view_order')}
            if type(values['sales_value']) is not int or values['sales_value'] < 0:
                values['sales_value'] = None
            item_id = 'product_table_' + hashlib.sha256(f'{shop}|{lane}|{identity_key[0]}|{identity_key[1]}'.encode()).hexdigest()[:24]
            values.update(id=item_id, shop_id=shop, lane=lane, normalized_title=_title(row),
                source_observation_ids=[card['observation_id'] for card in evidence],
                source_run_ids=list(dict.fromkeys(card['run_id'] for card in evidence)),
                related_observation_ids=sorted({observations[oid]['observation_id'] for aid in entry_items for oid in related[aid]}, key=str),
                merge_basis='same_unique_goods_id' if strong and len(evidence) > 1 else 'original_card',
                first_seen_at=first_at, last_seen_at=last_at, first_seen_precision=first_precision, last_seen_precision=last_precision,
                first_seen_window=first_window, last_seen_window=last_window,
                time_order_status='overlapping_runs_unknown' if time_overlap else 'observed_run_order',
                newness='unknown', newness_label=label, age_label=age, reason=reason,
                coverage_expansion_possible=expansion, arrival_item_ids=sorted(entry_items),
                observation_window={'from': runs[row['run_id']]['observed_from'], 'to': runs[row['run_id']]['observed_to']},
                run_status=runs[row['run_id']]['status'], run_is_complete=_complete(runs[row['run_id']]),
                identity_reliable=reliable[_oid(row)], automatic_image_retry_allowed=False)
            local.append(values)
        local.sort(key=lambda row: ({'new_watch': 0, 'hot': 1, 'coverage_review': 2}[row['lane']],
            -row['sales_value'] if row['lane'] == 'hot' else -_epoch(row['first_seen_window']['to']), row['view_order'], str(row['observation_id'])))
        lanes = {lane: [row for row in local if row['lane'] == lane] for lane in ('new_watch', 'coverage_review', 'hot')}
        population = {_key(oid) for row in local for oid in row['source_observation_ids']}
        appearances = Counter(_key(oid) for lane in lanes.values() for oid in {oid for row in lane for oid in row['source_observation_ids']})
        history = [row for run in group for row in by_run[run['run_id']]]
        reads = [stamp for row in history if (stamp := _valid_time(row.get('last_dom_read_at'), runs[row['run_id']]))]
        ref_rows = by_run[reference['run_id']] if reference else []
        checks = [
            {'id': 'same_shop_sources', 'label': '所有来源原卡属于本店', 'passed': all(runs[observations[oid]['run_id']]['shop_id'] == shop for oid in population), 'detail': f'{len(population)}条独立来源观察'},
            {'id': 'complete_reference_threshold', 'label': '热销范围与单卡门槛核对', 'passed': all(reference is not None and row['run_id'] == reference['run_id'] and _hot(row) for row in lanes['hot']), 'detail': '最近完整且有结束边界轮；精确已拼/已抢件数>10'},
            {'id': 'baseline_and_arrival_scope', 'label': '固定起点与首见来源核对', 'passed': configured or not (lanes['new_watch'] or lanes['coverage_review']), 'detail': '无固定起点不制造新品；扩覆盖线索独立分类'},
            {'id': 'all_history_retained', 'label': '历史计数来自全部原轮次', 'passed': len(history) == sum(len(by_run[run['run_id']]) for run in group), 'detail': f'{len(group)}轮、{len(history)}条观察，不是唯一商品数'},
        ]
        shop_rows.append({'shop_id': shop, 'shop_name': latest.get('shop_name'),
            'reference_run_id': reference['run_id'] if reference else None, 'reference_card_count': len(ref_rows),
            'reference_observed_from': reference['observed_from'] if reference else None,
            'reference_observed_to': reference['observed_to'] if reference else None, 'reference_is_complete': reference is not None,
            'reference_state': 'complete_reference' if reference else 'no_complete_reference',
            'latest_run_id': latest['run_id'], 'latest_card_count': len(by_run[latest['run_id']]),
            'latest_observed_from': latest['observed_from'], 'latest_observed_to': latest['observed_to'],
            'latest_is_complete': _complete(latest), 'history_run_count': len(group), 'history_observation_count': len(history),
            'last_read_at': max(reads, key=_epoch) if reads else None,
            'baseline_state': 'configured' if configured else 'no_baseline', 'baseline_run_ids': sorted(baseline),
            'baseline_started_at': summary.get('started_at') if configured else None,
            'baseline_observation_count': summary.get('baseline_observation_count', 0) if configured else 0,
            'baseline_coverage_status': summary.get('baseline_coverage_status') if configured else 'no_baseline',
            'baseline_coverage_note': summary.get('baseline_coverage_note') if configured else '尚未设置固定起点；本店首轮和历史卡片新旧未知，不制造新品。',
            'arrival_state': summary.get('state', 'not_configured'),
            **{lane + '_count': len(rows) for lane, rows in lanes.items()},
            **{lane + '_display_group_count': _fold_count(rows) for lane, rows in lanes.items()},
            'folding_rule': 'same shop, lane, run, exact normalized_title and same nonempty original image_url or verified local asset_sha256; display only',
            'overlap_observation_count': sum(count > 1 for count in appearances.values()),
            'distinct_display_observation_count': len(population), 'display_row_count': len(local),
            'missing_image_count': sum(not observations[oid].get('asset_sha256') or observations[oid].get('image_content_status') != 'verified_local' for oid in population),
            'missing_identity_count': sum(not reliable[oid] for oid in population),
            'reference_missing_image_count': sum(not row.get('asset_sha256') or row.get('image_content_status') != 'verified_local' for row in ref_rows),
            'reference_missing_identity_count': sum(not reliable[_oid(row)] for row in ref_rows),
            'counting_scope': 'missing counts use distinct source_observation_ids; lanes overlap and sales are never summed',
            'check_state': 'passed' if all(check['passed'] for check in checks) else 'needs_review', 'check_results': checks,
            'source_observation_ids': [observations[oid]['observation_id'] for oid in sorted(population, key=lambda oid: (observations[oid]['run_id'], observations[oid]['view_order']))],
            'history_run_ids': [run['run_id'] for run in ordered], 'limitations': LIMITATIONS[:]})
        output.extend(local)
    sources = [queries[key].get('source', {}) for key in UPSTREAM if isinstance(queries.get(key), dict)]
    source = {'label': '逐店新品关注、覆盖复核与完整参考热销表', 'provider': 'Local reviewed queries',
        'classification': 'derived', 'upstreamQueryIds': list(UPSTREAM),
        'files': sorted({path for src in sources for path in src.get('files', [])}),
        'tables': sorted({table for src in sources for table in src.get('tables', []) if isinstance(table, str)}),
        'timezone': 'Asia/Shanghai', 'grain': 'one lane display row; original cards retained by source_observation_ids',
        'filters': ['逐店独立参考；热销仅最近完整有边界轮。无固定起点不产生新品关注；补扫扩覆盖单列。'],
        'caveats': LIMITATIONS[:], 'derivation': 'pdd_monitor.product_tables.build_product_table_queries(queries); pure, no I/O',
        'sourceQueryRowIds': {'observations': sorted({oid for row in output for oid in row['source_observation_ids']}, key=str),
                              'new_arrival_items': sorted({aid for row in output for aid in row['arrival_item_ids']})},
        'metricDefinitions': [{'id': 'hot_count', 'label': '完整参考热销', 'definition': '本店最近complete且有结束边界轮中的单卡精确已拼/已抢件数>10；不累计销量。'},
                              {'id': 'new_watch_count', 'label': '新品关注', 'definition': '合法固定起点后首见，排除coverage_expansion_possible；不是确认新品。'},
                              {'id': 'coverage_review_count', 'label': '补采覆盖复核', 'definition': '保留可能由部分起点补扫产生的首见锚点；可能是旧存量。'}]}
    methods = [{'language': 'python', 'code': 'build_product_table_queries(queries): validate run/shop/baseline/arrival joins; select latest complete per shop; preserve per-card sales; merge only same-shop per-run-unique reliable goods IDs; keep missing-ID cards independent.'}]
    return {'product_table_shops': {'rows': shop_rows, 'source': dict(deepcopy(source), grain='one observed shop'), 'methods': deepcopy(methods)},
            'product_table_items': {'rows': output, 'source': source, 'methods': methods}}
