"""Offline shadow replay of frozen facts, not strategies published at that time."""
from collections import Counter, defaultdict
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path

from .history import SHANGHAI, _complete, _time_evidence
from .trend_decisions import PROTOCOL, LEGACY_PROTOCOL, _finite, _timestamp, _validated_history, read_ledger
from .warehouse_focus import _day, _goods_id, _yipin


BACKTEST_VERSION = 'frozen_common_pool_shadow_v1'
CONFIG = {'version': BACKTEST_VERSION, 'top_k': 5, 'episode_days': 7,
          'minimum_prior_complete_days': 4, 'minimum_prior_known_selected': 20,
          'common_pool_version': 'positive_local_exact_rate_and_recent3_v1',
          'weights': [.25, .5, .75]}
ALGORITHMS = {'rule': '现有规则', 'cumulative': '累计已拼', 'rate24': '最近24小时速度',
              'blend_025': '近期均速权重25%', 'blend_050': '近期均速权重50%',
              'blend_075': '近期均速权重75%'}
WEIGHTS = dict(zip(('blend_025', 'blend_050', 'blend_075'), CONFIG['weights']))
CHANNELS = ('confirmed', 'provisional')
LIMITATIONS = [
    '当前回测仅使用已拼/已抢新协议冻结记录；旧已拼协议单独保留，不混入新口径的命中率或训练。',
    '对照算法现在用冻结事实离线回放，不代表当时已发布这些推荐；参数择优也是只看当时可得资料的模拟。',
    '仅比较冻结时同店同日最早正式决策的共同合格池；不同策略不换池，不影响当前推荐。',
    '共同池要求本地图、精确已拼/已抢正数、正增量、6—48小时明确速率、至少3段合格近期均速；缺失不回退填值。',
    '每店按可靠ID、同标题原图URL/原图字节保守排除7天内重复观察；仅用于评价去重，不合并原卡或销量。',
    '到期日结束后才为成熟窗口；未知保留分母，等待中另列，同图线索不进入可靠身份命中率。',
    '择优仅用此前决策且结果在当时已经可用的完整窗口；旧结果缺可得时间不参与训练。',
    '参数选择始终为影子候选，固定门槛不构成效果验证；无概率、无上线赢家、无生产库写入。',
]


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()


def _channel(signal):
    return {'confirmed_goods_id': 'confirmed', 'provisional_title_image': 'provisional'}.get(signal.get('basis'))


def _episode_keys(card):
    keys = {'observation:' + str(card['observation_id'])}
    if _goods_id(card) and card.get('identity_status') == 'unique_goods_id':
        keys.add('goods:' + _goods_id(card))
    title = card.get('title')
    if isinstance(title, str) and title.strip():
        url = card.get('image_url')
        if isinstance(url, str) and url.strip():
            keys.add('title_url:' + _hash([title, url]))
        sha = card.get('asset_sha256')
        if (isinstance(sha, str) and len(sha) == 64 and card.get('image_content_status') == 'verified_local'
                and card.get('image_content_kind') != 'store_search_variant' and not card.get('image_variant_provenance')):
            keys.add('title_original_bytes:' + _hash([title, sha]))
    return keys


def _qualification(signal, card):
    value = _yipin(card)
    if (_channel(signal) is None or card.get('image_content_status') != 'verified_local'
            or not isinstance(card.get('asset_sha256'), str) or len(card['asset_sha256']) != 64
            or signal.get('level') not in ('priority', 'watch', 'observe')):
        return 'excluded_identity_or_image'
    if not signal.get('included_in_watch') or value is None or value <= 0 or not _finite(signal.get('latest_delta')) or signal['latest_delta'] <= 0:
        return 'excluded_nonpositive_or_unknown'
    if (not _finite(signal.get('rate24')) or signal['rate24'] <= 0
            or signal.get('rate_status') != 'normalized_exact_interval'
            or not _finite(signal.get('interval_hours')) or not 6 <= signal['interval_hours'] <= 48):
        return 'excluded_missing_rate'
    if (not _finite(signal.get('avg_recent')) or signal['avg_recent'] < 0
            or type(signal.get('normalized_delta_days')) is not int or signal['normalized_delta_days'] < 3):
        return 'excluded_missing_recent'
    if (not all(signal.get(key) for key in ('evidence_observation_ids', 'evidence_run_ids', 'evidence_dates'))
            or len(signal['evidence_observation_ids']) < max(4, signal['normalized_delta_days'] + 1)):
        return 'excluded_missing_feature_evidence'
    return None


def _validate_cohorts(ledger, runs, by_run, now):
    found, result = set(), []
    protocol_hash = _hash(PROTOCOL)
    all_observations = {row['observation_id']: row for rows in by_run.values() for row in rows}
    for cohort in ledger.get('cohorts', []):
        cid = cohort.get('cohort_id')
        if not isinstance(cid, str) or cid in found:
            raise ValueError('Duplicate or invalid backtest cohort')
        found.add(cid)
        if cohort.get('protocol') == LEGACY_PROTOCOL:
            continue  # Keep historical cohorts intact and out of the new metric pool.
        if _hash(cohort.get('protocol')) != protocol_hash:
            raise ValueError('Mixed or unsupported frozen label protocols cannot be replayed together')
        recorded = _timestamp(cohort.get('recorded_at'))
        day = date.fromisoformat(cohort['date'])
        due = date.fromisoformat(cohort['due_date'])
        source = runs.get(cohort['run_id'])
        if (recorded is None or recorded > now or recorded.astimezone(SHANGHAI).date() != day
                or due != day + timedelta(days=7)):
            raise ValueError('Future or mismatched decision time')
        if (not _complete(source) or source['shop_id'] != cohort['shop_id'] or _day(source) != day
                or source.get('snapshot_sha256') != cohort.get('snapshot_sha256')):
            raise ValueError('Frozen decision source does not match its shop/date/snapshot')
        observed, imported = _timestamp(source.get('observed_to')), _timestamp(source.get('imported_at'))
        if observed is None or observed > recorded or (imported is not None and imported > recorded):
            raise ValueError('Decision uses future observed or imported source data')
        cards = {}
        for card in cohort['candidates']:
            oid = card['observation_id']
            original = all_observations.get(oid)
            if oid in cards or card['run_id'] != cohort['run_id'] or original is None or original['run_id'] != cohort['run_id']:
                raise ValueError('Duplicate or wrong-run frozen card')
            stamp = _timestamp(card.get('observed_at'))
            if stamp is not None and stamp > recorded:
                raise ValueError('Frozen card observed after decision')
            cards[oid] = card
        signals = {}
        for signal in cohort['signals']:
            oid = signal['observation_id']
            if oid in signals or oid not in cards or signal.get('shop_id') != cohort['shop_id'] or signal.get('run_id') != cohort['run_id']:
                raise ValueError('Duplicate or mismatched frozen signal')
            for evidence_day in signal.get('evidence_dates', []):
                if date.fromisoformat(evidence_day) > day:
                    raise ValueError('Frozen feature contains a future date')
            for rid in signal.get('evidence_run_ids', []):
                evidence = runs.get(rid)
                if evidence is None or evidence['shop_id'] != cohort['shop_id']:
                    raise ValueError('Frozen feature has missing or cross-shop evidence')
                for field in ('observed_to', 'imported_at'):
                    stamp = _timestamp(evidence.get(field))
                    if stamp is not None and stamp > recorded:
                        raise ValueError('Frozen feature contains future evidence')
            ids, run_ids, dates = (signal.get(key, []) for key in ('evidence_observation_ids', 'evidence_run_ids', 'evidence_dates'))
            if any((ids, run_ids, dates)):
                if not (len(ids) == len(set(ids)) == len(run_ids) == len(set(run_ids)) == len(dates) == len(set(dates))):
                    raise ValueError('Frozen feature evidence lists are incomplete or duplicated')
                for evidence_oid, rid, evidence_day in zip(ids, run_ids, dates):
                    original, evidence = all_observations.get(evidence_oid), runs.get(rid)
                    if (original is None or evidence is None or original['run_id'] != rid
                            or evidence['shop_id'] != cohort['shop_id'] or _day(evidence).isoformat() != evidence_day):
                        raise ValueError('Frozen feature observation/run/date do not correspond')
                    observation_time = _time_evidence(original, evidence)
                    if not observation_time['valid'] or observation_time['upper'] > recorded.timestamp():
                        raise ValueError('Frozen feature has invalid or future observation evidence')
                    for value in (evidence.get('observed_to'), evidence.get('imported_at')):
                        stamp = _timestamp(value)
                        if stamp is None or stamp > recorded:
                            raise ValueError('Frozen feature has unavailable or future observation evidence')
                if _qualification(signal, cards[oid]) is None:
                    if (ids[-1] != oid or run_ids[-1] != cohort['run_id']
                            or [date.fromisoformat(value) for value in dates[-4:]] != [day + timedelta(days=offset) for offset in (-3, -2, -1, 0)]):
                        raise ValueError('Frozen normalized features need a continuous suffix ending at their anchor')
                    times = []
                    for evidence_oid, rid in zip(ids[-4:], run_ids[-4:]):
                        time = _time_evidence(all_observations[evidence_oid], runs[rid])
                        if time['precision'] not in ('card_read', 'batch_read') or time['lower'] != time['upper']:
                            raise ValueError('Frozen normalized feature lacks three precise point intervals')
                        times.append(time['lower'])
                    if any(not 6 <= (end - start) / 3600 <= 48 for start, end in zip(times, times[1:])):
                        raise ValueError('Frozen normalized feature has irregular or reversed intervals')
            signals[oid] = signal
        for key, outcome in cohort.get('evaluations', {}).items():
            if key not in {str(oid) for oid in signals}:
                raise ValueError('Outcome is outside the frozen signal pool')
            available = _timestamp(outcome.get('outcome_available_at'))
            if available is not None:
                if available > now or available <= recorded:
                    raise ValueError('Outcome availability is in the future or before its decision')
                if outcome.get('evaluation_state') == 'evaluated' and available.astimezone(SHANGHAI).date() <= due:
                    raise ValueError('Outcome closed before its complete due day ended')
                if outcome.get('evaluation_state') == 'evaluated':
                    future_ids, observation_ids = outcome.get('future_run_ids', []), outcome.get('future_observation_ids', [])
                    if len(future_ids) != 7 or len(set(future_ids)) != 7 or len(observation_ids) != 8 or len(set(observation_ids)) != 8:
                        raise ValueError('Evaluated outcome needs seven complete runs and eight original observations')
                    if str(observation_ids[0]) != key:
                        raise ValueError('Outcome does not start at its frozen original card')
                    for offset, (rid, oid) in enumerate(zip([cohort['run_id'], *future_ids], observation_ids)):
                        evidence, observation = runs.get(rid), all_observations.get(oid)
                        if (not _complete(evidence) or evidence['shop_id'] != cohort['shop_id']
                                or _day(evidence) != day + timedelta(days=offset)
                                or observation is None or observation['run_id'] != rid):
                            raise ValueError('Outcome evidence is incomplete, discontinuous or mismatched')
                        stamp = _timestamp(observation.get('observed_at'))
                        if stamp is None or stamp > available:
                            raise ValueError('Outcome card was unavailable at evaluation')
                for rid in outcome.get('future_run_ids', []):
                    future = runs.get(rid)
                    if (future is None or future['shop_id'] != cohort['shop_id']
                            or not day < _day(future) <= due):
                        raise ValueError('Outcome has cross-shop or out-of-window evidence')
                    for field in ('observed_to', 'imported_at'):
                        stamp = _timestamp(future.get(field))
                        if stamp is None or stamp > available:
                            raise ValueError('Outcome used future or unverified observation/import time')
                future_runs = set(outcome.get('future_run_ids', []))
                for oid in outcome.get('future_observation_ids', []):
                    row = all_observations.get(oid)
                    if row is None or row['run_id'] not in future_runs | {cohort['run_id']}:
                        raise ValueError('Outcome observation does not belong to its cited runs')
        result.append((cohort, recorded, cards, signals))
    return sorted(result, key=lambda entry: (entry[1], entry[0]['cohort_id']))


def _rank(pool, strategy):
    if strategy == 'rule':
        levels = {'priority': 0, 'watch': 1, 'observe': 2}
        key = lambda pair: (levels[pair[0]['level']], pair[0].get('rank', 1000000), -pair[0]['rate24'], pair[0]['observation_id'])
    else:
        def key(pair):
            signal, card = pair
            score = (_yipin(card) if strategy == 'cumulative' else signal['rate24'] if strategy == 'rate24'
                     else WEIGHTS[strategy] * signal['avg_recent'] + (1 - WEIGHTS[strategy]) * signal['rate24'])
            return -score, signal['observation_id']
    return sorted(pool, key=key)[:CONFIG['top_k']]


def _outcome(cohort, signal, channel, cutoff):
    if cutoff.astimezone(SHANGHAI).date() <= date.fromisoformat(cohort['due_date']):
        return 'waiting', None
    value = cohort.get('evaluations', {}).get(str(signal['observation_id']), {})
    available = _timestamp(value.get('outcome_available_at'))
    if available is None or available > cutoff:
        return 'unknown', 'missing_or_late_outcome_availability'
    if value.get('evaluation_state') != 'evaluated' or type(value.get('outcome')) is not bool:
        return 'unknown', value.get('outcome_label', 'unknown_outcome')
    if channel == 'confirmed' and value.get('basis') != 'confirmed_goods_id':
        return 'unknown', 'outcome_identity_not_confirmed'
    if value.get('basis') not in ('confirmed_goods_id', 'provisional_title_image'):
        return 'unknown', 'outcome_identity_unknown'
    return ('hit' if value['outcome'] else 'not_hit'), None


def _measure(cohort, chosen, channel, cutoff):
    statuses, reasons = Counter(), Counter()
    for signal, _ in chosen:
        status, reason = _outcome(cohort, signal, channel, cutoff)
        statuses[status] += 1
        if reason:
            reasons[reason] += 1
    known, unknown = statuses['hit'] + statuses['not_hit'], statuses['unknown']
    mature = known + unknown
    hit = statuses['hit']
    return dict(selected_count=len(chosen), mature_selected=mature, known=known, hit=hit,
                unknown=unknown, waiting=statuses['waiting'], unknown_reasons=dict(reasons),
                known_hit_rate=hit / known if known else None,
                coverage=known / mature if mature else None,
                lower_bound=hit / mature if mature else None,
                upper_bound=(hit + unknown) / mature if mature else None,
                full_precision=hit / len(chosen) if chosen and known == len(chosen) else None)


def _mean(values):
    present = [value for value in values if value is not None]
    return sum(present) / len(present) if present else None


def _aggregate(windows):
    counts = {key: sum(row[key] for row in windows) for key in ('selected_count', 'mature_selected', 'known', 'hit', 'unknown', 'waiting')}
    known, hit, mature, selected = (counts[key] for key in ('known', 'hit', 'mature_selected', 'selected_count'))
    matured = [row for row in windows if row['mature_selected']]
    all_mature_known = bool(matured) and all(row['full_precision'] is not None for row in matured)
    return dict(counts, windows=len(windows), mature_windows=len(matured), waiting_windows=sum(row['waiting'] > 0 for row in windows),
                known_hit_rate=hit / known if known else None,
                coverage=known / mature if mature else None,
                lower_bound=hit / mature if mature else None,
                upper_bound=(hit + counts['unknown']) / mature if mature else None,
                full_precision=hit / mature if mature and known == mature else None,
                macro_full_precision=_mean(row['full_precision'] for row in matured) if all_mature_known else None,
                macro_coverage=_mean(row['coverage'] for row in windows),
                macro_lower_bound=_mean(row['lower_bound'] for row in windows),
                macro_upper_bound=_mean(row['upper_bound'] for row in windows),
                full_precision_windows=sum(row['full_precision'] is not None for row in windows))


def build_backtest_queries(queries, project, ledger=None, now=None):
    """Never mutate the ledger or business data; comparisons are diagnostic only."""
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError('Backtest time must include a timezone')
    ledger = read_ledger(project) if ledger is None else ledger
    runs, by_run, _ = _validated_history(queries)
    entries = _validate_cohorts(ledger, runs, by_run, now)
    meta = dict(as_of_date=now.astimezone(SHANGHAI).date().isoformat(), evaluated_at=now.isoformat(),
                backtest_version=BACKTEST_VERSION, source_protocol_version=PROTOCOL['version'],
                common_pool_version=CONFIG['common_pool_version'], shadow_only=True, forecast_validated=False)
    shops = {row['shop_id'] for row in queries.get('warehouse_focus_summary', {}).get('rows', [])}
    shops.update(cohort['shop_id'] for cohort, _, _, _ in entries)
    counters = defaultdict(Counter)
    day_seen, episode_seen, prepared = set(), defaultdict(dict), []
    for cohort, recorded, cards, signals in entries:
        shop, day = cohort['shop_id'], date.fromisoformat(cohort['date'])
        day_key = shop, day
        if day_key in day_seen:
            counters[shop]['superseded_same_day_cohorts'] += 1
            continue
        day_seen.add(day_key)
        counters[shop]['recorded_decision_days'] += 1
        counters[shop]['frozen_watch_count'] += len(signals)
        pools = defaultdict(list)
        qualified = []
        for signal in signals.values():
            channel = _channel(signal)
            if channel:
                counters[shop]['channel_watch_' + channel] += 1
            card = cards[signal['observation_id']]
            reason = _qualification(signal, card)
            if reason:
                counters[shop][reason] += 1
                continue
            qualified.append((signal, card, _episode_keys(card)))
        key_counts = Counter(key for _, _, keys in qualified for key in keys)
        for signal, card, keys in qualified:
            if any(key_counts[key] > 1 for key in keys):
                counters[shop]['excluded_ambiguous_episode'] += 1
                continue
            if any(key in episode_seen[shop] and (day - episode_seen[shop][key]).days < CONFIG['episode_days'] for key in keys):
                counters[shop]['excluded_overlapping_episode'] += 1
                continue
            for key in keys:
                episode_seen[shop][key] = day
            pools[_channel(signal)].append((signal, card))
        prepared.append((cohort, recorded, pools))
    windows, selections = [], []
    prior = defaultdict(list)
    for cohort, recorded, pools in prepared:
        shop = cohort['shop_id']
        for channel in CHANNELS:
            pool = pools[channel]
            past = prior[shop, channel]
            training = []
            for old_cohort, old_pool in past:
                measures = {strategy: _measure(old_cohort, _rank(old_pool, strategy), channel, recorded) for strategy in WEIGHTS}
                if old_pool and all(value['full_precision'] is not None for value in measures.values()):
                    training.append(measures)
            candidates = {strategy: _mean(row[strategy]['full_precision'] for row in training) for strategy in WEIGHTS}
            ordered = sorted(WEIGHTS, key=lambda strategy: (-(candidates[strategy] if candidates[strategy] is not None else -1), strategy))
            best = ordered[0] if training else None
            known_count = sum(row[best]['known'] for row in training) if best else 0
            ready = len(training) >= CONFIG['minimum_prior_complete_days'] and known_count >= CONFIG['minimum_prior_known_selected']
            selections.append(dict(meta, shop_id=shop, evidence_channel=channel, date=cohort['date'], cohort_id=cohort['cohort_id'],
                training_cutoff=recorded.isoformat(), training_windows=len(training), eligible_prior_windows=len(past),
                training_known_selected=known_count, chosen_parameter=WEIGHTS[best] if ready else None,
                candidate_parameter=WEIGHTS[best] if best else None,
                shadow_strategy=best if ready else None,
                shadow_selected_ids=[s['observation_id'] for s, _ in _rank(pool, best)] if ready else [],
                readiness='shadow_candidate' if ready else 'insufficient_history',
                candidate_scores=candidates, threshold_config=deepcopy(CONFIG),
                reason='仅历史完整结果形成的影子候选，未验证效果' if ready else '此前完整、可用结果不足，参数不投入使用'))
            for strategy, name in ALGORITHMS.items():
                chosen = _rank(pool, strategy)
                windows.append(dict(meta, shop_id=shop, evidence_channel=channel, date=cohort['date'],
                    cohort_id=cohort['cohort_id'], decision_at=recorded.isoformat(), due_date=cohort['due_date'],
                    source_run_id=cohort['run_id'], snapshot_sha256=cohort['snapshot_sha256'], rule_version=cohort['rule_version'],
                    strategy=strategy, algorithm_name=name, pool_count=len(pool),
                    pool_ids=sorted(signal['observation_id'] for signal, _ in pool),
                    selected_ids=[signal['observation_id'] for signal, _ in chosen],
                    **_measure(cohort, chosen, channel, now)))
            if pool:
                prior[shop, channel].append((cohort, pool))
    metrics, summaries = [], []
    for shop in sorted(shops):
        versions = sorted({cohort['rule_version'] for cohort, _, _ in prepared if cohort['shop_id'] == shop})
        for channel in CHANNELS:
            scoped = [row for row in windows if row['shop_id'] == shop and row['evidence_channel'] == channel]
            for strategy, name in ALGORITHMS.items():
                chosen = [row for row in scoped if row['strategy'] == strategy and row['pool_count']]
                metrics.append(dict(meta, shop_id=shop, evidence_channel=channel, strategy=strategy,
                                    algorithm_name=name, source_rule_versions=versions, **_aggregate(chosen)))
            base = [row for row in scoped if row['strategy'] == 'rule' and row['pool_count']]
            stats = _aggregate(base)
            count = counters[shop]
            readiness = ('no_frozen_candidates' if not count['frozen_watch_count'] else 'insufficient_pool' if not base
                         else 'waiting_outcomes' if not stats['mature_selected'] else 'insufficient_history' if not stats['known'] else 'shadow_evaluation')
            reason = {'no_frozen_candidates': '尚无本店当日冻结候选', 'insufficient_pool': '已留存候选，但共同池的速率、近期基准或身份图片证据不足',
                      'waiting_outcomes': '等待到期日结束及完整后续结果', 'insufficient_history': '成熟结果尚不可评价，未知保留分母',
                      'shadow_evaluation': '仅描述影子策略对照，未验证上线效果'}[readiness]
            summaries.append(dict(meta, shop_id=shop, evidence_channel=channel, source_rule_versions=versions,
                frozen_watch_count=count['frozen_watch_count'], channel_watch_count=count['channel_watch_' + channel],
                source_watch_count_total=count['frozen_watch_count'],
                unclassified_watch_count=count['frozen_watch_count']-sum(count['channel_watch_' + value] for value in CHANNELS),
                recorded_dates=count['recorded_decision_days'], history_days=count['recorded_decision_days'],
                eligible_decision_days=len(base), eligible_pool_cards=sum(row['pool_count'] for row in base),
                excluded_missing_rate=count['excluded_missing_rate'], excluded_missing_recent=count['excluded_missing_recent'],
                excluded_identity_or_image=count['excluded_identity_or_image'], excluded_nonpositive_or_unknown=count['excluded_nonpositive_or_unknown'],
                excluded_missing_feature_evidence=count['excluded_missing_feature_evidence'],
                excluded_overlapping_episode=count['excluded_overlapping_episode'], excluded_ambiguous_episode=count['excluded_ambiguous_episode'],
                superseded_same_day_cohorts=count['superseded_same_day_cohorts'],
                waiting_selected=stats['waiting'], unknown_selected=stats['unknown'], known_selected=stats['known'],
                metric_window_count=len(base), readiness=readiness, reason=reason))
    source = deepcopy(queries.get('observations', {}).get('source', {}))
    source.update(classification='derived', provider='Frozen local decision ledger and reviewed time evidence',
                  grain='one shop, frozen decision date, evidence channel and fixed shadow strategy',
                  files=list(dict.fromkeys([*source.get('files', []), str(Path(project) / 'state/trend_decisions/decisions.json'), str(Path(__file__).resolve())])),
                  caveats=LIMITATIONS, filters=LIMITATIONS[:2],
                  derivation='pdd_monitor.trend_backtest.build_backtest_queries(queries, project, ledger, now)',
                  metricDefinitions=[{'label': '共同冻结池的影子对照', 'definition': '；'.join(LIMITATIONS)}])
    output = {}
    for key, rows, label in [('trend_backtest_summary', summaries, '回测候选覆盖'), ('trend_backtest_metrics', metrics, '同池策略对照'),
                             ('trend_backtest_windows', windows, '逐日冻结窗口'), ('trend_backtest_selection', selections, '只看过去的影子参数')]:
        output[key] = {'rows': rows, 'source': dict(source, label=label),
                       'methods': [{'language': 'python', 'code': 'build_backtest_queries(queries, project, ledger=ledger, now=now)'}]}
    return output
