"""Evidence for the 30-day watch window; no listing-date or weak-ID inference."""
from collections import Counter, defaultdict
from datetime import datetime

from .history import SHANGHAI, _complete, _goods_id, _run_time
from .new_arrivals import _first_time

OBSERVATION_DAYS = 30


def monitoring_evidence(runs, observations, arrivals=(), summaries=()):
    """Return metadata by original card ID, retaining every weak card separately.

    Initial complete catalogues and the existing fixed tracking baselines are
    reference stock, not products proved to be more than 30 days old. Subsequent
    records inherit a watch anchor only through a unique same-shop goods ID.
    """
    run_map = {r['run_id']: r for r in runs}
    by_run, by_goods = defaultdict(list), defaultdict(list)
    for row in observations:
        by_run[row['run_id']].append(row)
    reliable = {}
    for run_id, rows in by_run.items():
        counts = Counter(_goods_id(r) for r in rows if _goods_id(r))
        for row in rows:
            good = _goods_id(row)
            reliable[row['observation_id']] = bool(good and counts[good] == 1 and row.get('identity_status') == 'unique_goods_id')
            if reliable[row['observation_id']]:
                by_goods[run_map[run_id]['shop_id'], good].append(row)

    initial, baseline = {}, defaultdict(set)
    for run in sorted(runs, key=lambda r: (*reversed(_run_time(r)), r['run_id'])):
        if _complete(run) and run['shop_id'] not in initial:
            initial[run['shop_id']] = run
            baseline[run['shop_id']].add(run['run_id'])
    for summary in summaries:
        baseline[summary['shop_id']].update(summary.get('baseline_run_ids', []))
    anchors = {item['observation_id']: item for item in arrivals}
    result = {}
    for row in observations:
        run = run_map[row['run_id']]
        shop = run['shop_id']
        peers = by_goods[shop, _goods_id(row)] if reliable[row['observation_id']] else [row]
        peers = sorted(peers, key=lambda r: (*reversed(_run_time(run_map[r['run_id']])), r['observation_id']))
        first = peers[0]
        first_run = run_map[first['run_id']]
        timing = _first_time(first, first_run)
        # A run window is retained as a window; it cannot start an exact age.
        precise = timing['first_seen_time_basis'] == 'recorded_read_time'
        first_at = timing['first_observed_at'] if precise else None
        first_date = datetime.fromisoformat(first_at.replace('Z', '+00:00')).astimezone(SHANGHAI).date().isoformat() if first_at else None
        origin, reason, anchor = 'pending', 'identity_or_newness_unverified', None
        if any(p['run_id'] in baseline[shop] for p in peers):
            origin, reason = 'reference_stock', 'initial_catalogue_or_fixed_baseline'
        else:
            candidate = anchors.get(first['observation_id'])
            if candidate:
                anchor = candidate['arrival_item_id']
                if candidate.get('coverage_expansion_possible'):
                    reason = 'possible_coverage_expansion'
                elif candidate.get('historical_references') or candidate.get('possible_change_reasons'):
                    reason = 'possible_existing_card_change'
                elif candidate.get('first_time_order_status') != 'observed_run_order' or not precise:
                    reason = 'first_time_unverified'
                elif shop not in initial:
                    reason = 'no_complete_catalogue'
                elif _run_time(first_run)[0] <= _run_time(initial[shop])[1]:
                    reason = 'not_after_initial_catalogue'
                else:
                    origin, reason = 'first_observed_candidate', 'first_observed_not_listing_date'
        result[row['observation_id']] = {
            'monitor_origin': origin, 'monitor_reason': reason,
            'monitor_first_observation_id': first['observation_id'],
            'monitor_first_date': first_date, 'monitor_first_observed_at': first_at,
            'monitor_first_window': timing['first_observation_window'],
            'monitor_anchor_id': anchor, 'monitor_window_days': OBSERVATION_DAYS,
            'listing_date': None, 'listing_date_status': 'unverified',
        }
    return result
