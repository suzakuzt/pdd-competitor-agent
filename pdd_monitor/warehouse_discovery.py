"""First-observed original-card evidence; never product listing dates or merges."""
from collections import Counter, defaultdict
from datetime import datetime

from .history import SHANGHAI, _complete, _goods_id, _run_time


DISCOVERY_STATUSES = ('initial_catalogue', 'existing', 'first_observed_after_complete',
                      'first_observed_candidate', 'identity_unresolved')
DISCOVERY_LIMITATIONS = [
    '每日新增记录按同店原卡首次观察的北京时间归档日期统计，不是实际上架日期；首轮建档不算新增。',
    '首次记录有前序完整且原卡键覆盖齐全的清单才标较完整清单首见；只有部分旧轮、键覆盖不足、改标题或变图均仅为候选。',
    '可靠商品ID仅限同店每轮唯一且无冲突；缺ID只用完整标题与原始主图URL精确组合识别既有线索，不按图片变体、内容哈希或位置合并原卡。',
    '重复既有组合仍逐卡保留；新重复组合、商品ID冲突和重叠观察时间保持未确认，不能算确认新商品。',
    '每日分组只含所选店、所选日期有效轮的原卡；同日复采继承首见证据，部分轮首次记录消失后不混入有效轮数量。',
]


def _date(run):
    return datetime.fromtimestamp(_run_time(run)[1], SHANGHAI).date().isoformat()


def _combo(row):
    title, image = row.get('title'), row.get('image_url')
    return (title, image) if all(isinstance(v, str) and v.strip() for v in (title, image)) else None


def build_discovery_evidence(runs, observations):
    """Return display evidence per observation without introducing a shared track."""
    run_map = {r['run_id']: r for r in runs}
    if len(run_map) != len(runs):
        raise ValueError('Duplicate run identity')
    by_run, by_shop = defaultdict(list), defaultdict(list)
    seen = set()
    for row in observations:
        if row['run_id'] not in run_map:
            raise ValueError('Observation without source run')
        if row['observation_id'] in seen:
            raise ValueError('Duplicate observation identity')
        seen.add(row['observation_id'])
        by_run[row['run_id']].append(row)
    times = {key: _run_time(run) for key, run in run_map.items()}
    for run in runs:
        by_shop[run['shop_id']].append(run)
    goods_counts = {key: Counter(_goods_id(r) for r in rows if _goods_id(r)) for key, rows in by_run.items()}
    combo_counts = {key: Counter(_combo(r) for r in rows if _combo(r)) for key, rows in by_run.items()}

    def conflict(row):
        goods = _goods_id(row)
        return (str(row.get('identity_status', '')).startswith('conflict_')
                or bool(goods and (goods_counts[row['run_id']][goods] > 1
                                   or row.get('identity_status') != 'unique_goods_id')))

    def reliable(row):
        return bool(_goods_id(row) and not conflict(row))

    coverage = {key: {'goods': all(reliable(row) for row in by_run[key]),
                      'combo': all(not conflict(row) and _combo(row) for row in by_run[key])}
                for key in run_map}

    def fresh(row, run, status, basis, reason, baseline=None):
        unresolved = status == 'identity_unresolved'
        return dict(discovery_status=status, discovery_basis=basis, discovery_reason=reason,
                    first_observed_date=None if unresolved else _date(run),
                    first_observation_id=None if unresolved else row['observation_id'],
                    first_observed_run_id=None if unresolved else run['run_id'],
                    discovery_baseline_run_id=baseline['run_id'] if baseline else None,
                    discovery_baseline_date=_date(baseline) if baseline else None,
                    discovery_baseline_snapshot_sha256=baseline.get('snapshot_sha256') if baseline else None,
                    listing_date=None, listing_date_status='unverified')

    evidence = {}
    for shop_runs in by_shop.values():
        ordered = sorted(shop_runs, key=lambda r: (*times[r['run_id']], r['run_id']))
        prior_goods, prior_combos, prior_titles, prior_images = (defaultdict(list) for _ in range(4))
        all_keys = defaultdict(list)
        for run in ordered:
            for row in by_run[run['run_id']]:
                if _goods_id(row):
                    all_keys['goods', _goods_id(row)].append(row)
                if _combo(row):
                    all_keys['combo', _combo(row)].append(row)
        previous = []
        for run in ordered:
            rows = by_run[run['run_id']]
            start, end = times[run['run_id']]
            completed = sorted((old for old in previous if _complete(old) and times[old['run_id']][1] < start),
                               key=lambda old: (*reversed(times[old['run_id']]), old['run_id']))
            current_combo_goods = defaultdict(set)
            for row in rows:
                if _combo(row) and _goods_id(row):
                    current_combo_goods[_combo(row)].add(_goods_id(row))
            for row in rows:
                oid, goods, combo = row['observation_id'], _goods_id(row), _combo(row)
                baseline = next((old for old in reversed(completed) if
                    (reliable(row) and coverage[old['run_id']]['goods']) or
                    (combo and coverage[old['run_id']]['combo'])), None)
                old_ids, old_combos = prior_goods[goods] if goods else [], prior_combos[combo] if combo else []
                basis = 'verified_goods_id' if reliable(row) else 'exact_title_original_image' if combo else None
                related = {r['observation_id']: r for r in [*old_ids, *old_combos]}
                combo_goods = {_goods_id(r) for r in old_combos if _goods_id(r)} | current_combo_goods[combo]
                id_conflict = (conflict(row) or any(conflict(r) for r in related.values())
                               or len(combo_goods) > 1 or bool(goods and combo_goods - {goods}))
                peers = [*(all_keys['goods', goods] if goods else []),
                         *(all_keys['combo', combo] if combo else [])]
                overlap = any(r['run_id'] != run['run_id'] and
                              times[r['run_id']][0] <= end and times[r['run_id']][1] >= start for r in peers)
                if id_conflict or overlap or basis is None:
                    reason = 'identity_conflict' if id_conflict else 'overlapping_observation_windows' if overlap else 'missing_identity_clue'
                    value = fresh(row, run, 'identity_unresolved', basis, reason)
                elif old_ids or old_combos:
                    matches = old_ids if reliable(row) and old_ids else old_combos
                    basis = 'verified_goods_id' if reliable(row) and old_ids else 'exact_title_original_image'
                    earliest = min(matches, key=lambda r: (*times[r['run_id']], str(r['observation_id'])))
                    anchor_run = run_map[earliest['run_id']]
                    anchor = evidence[earliest['observation_id']]
                    value = dict(anchor, discovery_basis=basis)
                    if basis == 'exact_title_original_image' and anchor['first_observed_run_id'] != earliest['run_id']:
                        # A weak clue cannot inherit an earlier, different title/image through an ID chain.
                        value = fresh(earliest, anchor_run, 'existing', basis, 'existing_original_card_clue')
                    # A repeated weak clue does not select one of its sibling original cards.
                    ambiguous_anchor = basis == 'exact_title_original_image' and combo_counts[earliest['run_id']][combo] > 1
                    if anchor['discovery_status'] == 'identity_unresolved' or ambiguous_anchor:
                        value = fresh(row, anchor_run, 'existing', basis, 'existing_original_card_clue')
                        value['first_observation_id'] = None
                    elif value['first_observed_date'] != _date(run):
                        value.update(discovery_status='existing', discovery_reason='previously_observed')
                    if combo and combo_counts[run['run_id']][combo] > 1 and basis != 'verified_goods_id':
                        value.update(discovery_status='existing', discovery_reason='repeated_existing_original_card_clue')
                elif combo and combo_counts[run['run_id']][combo] > 1:
                    value = fresh(row, run, 'identity_unresolved', basis, 'repeated_new_original_card_clue')
                elif not previous:
                    value = fresh(row, run, 'initial_catalogue', basis, 'initial_catalogue')
                else:
                    changed = bool(prior_titles[row.get('title')] or prior_images[row.get('image_url')])
                    status = 'first_observed_after_complete' if baseline and not changed else 'first_observed_candidate'
                    reason = ('title_or_image_changed' if changed else 'absent_from_prior_complete_card_list' if baseline
                              else 'prior_complete_key_coverage_insufficient' if completed else 'no_prior_complete_card_list')
                    value = fresh(row, run, status, basis, reason, baseline)
                evidence[oid] = value
            # Update only after a whole run: sibling cards can never become a prior observation.
            for row in rows:
                if _goods_id(row):
                    prior_goods[_goods_id(row)].append(row)
                if _combo(row):
                    prior_combos[_combo(row)].append(row)
                if row.get('title'):
                    prior_titles[row['title']].append(row)
                if row.get('image_url'):
                    prior_images[row['image_url']].append(row)
            previous.append(run)
    return evidence


def discovery_counts(rows, evidence, date):
    """Counts are card counts in this single selected shop/date/run, never shop history totals."""
    counts = Counter(evidence[r['observation_id']]['discovery_status'] for r in rows)
    result = {status + '_count': counts[status] for status in DISCOVERY_STATUSES}
    result['first_observed_count'] = sum(evidence[r['observation_id']]['discovery_status'] in
        ('first_observed_after_complete', 'first_observed_candidate') and
        evidence[r['observation_id']]['first_observed_date'] == date for r in rows)
    return result
