"""Prospective, local decision cohorts and seven-day outcomes; no model/network.

Freeze the complete candidate universe at analysis time. Missing future cards
remain unknown, including cards no longer present in the latest catalogue.
Business databases are read-only; publication records live in project state.
"""
from collections import defaultdict
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import tempfile

from .history import _complete, _time_evidence, SHANGHAI
from .warehouse_focus import _day, _pairs, _yipin

VERSION = 1
LEGACY_PROTOCOL = {'version': 'seven_day_growth_v1', 'horizon_days': 7,
            'baseline_intervals': 3, 'min_future_delta': 10,
            'min_rate_ratio': 1.5, 'min_positive_intervals': 4,
            'min_interval_hours': 6, 'max_interval_hours': 48,
            'rate_method': 'sum_delta_divided_by_sum_hours_times_24',
            'description': '未来7段连续可比展示增量合计至少10、24小时速度至少近3段的1.5倍、至少4段正增长；零基期不算倍率。'}
PROTOCOL = {**LEGACY_PROTOCOL, 'version': 'seven_day_growth_v2_sales_alias',
            'sales_metric': 'exact_yipin_or_yiqiang_items'}
SUPPORTED_PROTOCOLS = (LEGACY_PROTOCOL, PROTOCOL)
CARD_FIELDS = ('observation_id', 'run_id', 'view_order', 'title', 'goods_id',
               'identity_status', 'sales_raw', 'sales_value', 'sales_label',
               'sales_precision', 'sales_unit', 'price_raw', 'image_url',
               'asset_sha256', 'image_content_status', 'image_content_kind',
               'image_variant_provenance', 'observed_at', 'observed_at_precision',
               'observed_at_epoch')
LEVELS = {'priority': 0, 'watch': 1, 'observe': 2}


def _finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def ranked_signals(rows):
    """A transparent ordering, never a calibrated probability or score."""
    usable = [row for row in rows if row.get('included_in_watch') and
              row.get('level') in LEVELS and _finite(row.get('latest_delta'))
              and row['latest_delta'] > 0]
    if any('selected_for_review' in row for row in rows):
        return sorted((row for row in usable if row.get('selected_for_review') is True),
                      key=lambda row: (row.get('rank', 1000000), row['observation_id']))
    return sorted(usable, key=lambda row: (LEVELS[row['level']],
        -float(row.get('rate24') if _finite(row.get('rate24')) else row['latest_delta']),
        -float(row.get('latest_delta', 0)), row['observation_id']))


def ledger_path(project):
    return Path(project) / 'state/trend_decisions/decisions.json'


def read_ledger(project):
    path = ledger_path(project)
    if not path.exists():
        return {'version': VERSION, 'revision': 0, 'cohorts': []}
    raw = path.read_bytes()
    if len(raw) > 100_000_000:
        raise ValueError('Trend decision history exceeds supported size; archive explicitly before continuing')
    value = json.loads(raw)
    if (type(value) is not dict or value.get('version') != VERSION or
        type(value.get('revision')) is not int or value['revision'] < 0 or
        type(value.get('cohorts')) is not list):
        raise ValueError('Trend decision history invalid; existing history was not replaced')
    ids = set()
    for cohort in value['cohorts']:
        if (type(cohort) is not dict or not isinstance(cohort.get('cohort_id'), str)
            or cohort['cohort_id'] in ids or type(cohort.get('candidates')) is not list
            or type(cohort.get('selected_ids')) is not list
            or type(cohort.get('signals')) is not list or type(cohort.get('evaluations')) is not dict
            or cohort.get('protocol') not in SUPPORTED_PROTOCOLS):
            raise ValueError('Trend decision cohort invalid; existing history was not replaced')
        ids.add(cohort['cohort_id'])
    return value


def _point_seconds(row, run):
    evidence = _time_evidence(row, run)
    if not (evidence['valid'] and evidence['precision'] in ('card_read', 'batch_read')
            and evidence['lower'] == evidence['upper']):
        return None
    return evidence['lower']


def _unknown(reason, state='insufficient_data', **facts):
    return {'evaluation_state': state, 'outcome': None, 'outcome_label': reason,
            'future_delta': None, 'future_rate24': None, 'basis': 'unknown', **facts}


def _timestamp(value):
    if not isinstance(value, str):
        return None
    try:
        stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return stamp if stamp.tzinfo is not None else None
    except ValueError:
        return None


def _validated_history(queries):
    runs = {}
    for run in queries.get('runs', {}).get('rows', []):
        if run['run_id'] in runs:
            raise ValueError('Duplicate run in decision evidence')
        runs[run['run_id']] = run
    by_run, observations = defaultdict(list), set()
    for row in queries.get('observations', {}).get('rows', []):
        if row['observation_id'] in observations or row['run_id'] not in runs:
            raise ValueError('Duplicate or orphan observation in decision evidence')
        observations.add(row['observation_id'])
        by_run[row['run_id']].append(row)
    daily = {}
    for row in queries.get('warehouse_days', {}).get('rows', []):
        key = row['shop_id'], row['date']
        run = runs.get(row['run_id'])
        if (key in daily or run is None or run['shop_id'] != row['shop_id']
                or _day(run).isoformat() != row['date']):
            raise ValueError('Daily decision evidence has duplicate or mismatched shop/date/run')
        daily[key] = run
    return runs, by_run, daily


def evaluate_cohort(cohort, queries, *, now):
    """Re-evaluate using the frozen pool, not the surviving latest catalogue."""
    runs, by_run, daily = _validated_history(queries)
    anchor_run = runs.get(cohort['run_id'])
    due = date.fromisoformat(cohort['due_date'])
    if (not anchor_run or anchor_run['shop_id'] != cohort['shop_id'] or
        anchor_run.get('snapshot_sha256') != cohort.get('snapshot_sha256')):
        return {str(row['observation_id']): _unknown('原始推荐轮待核验') for row in cohort['signals']}
    if now.astimezone(SHANGHAI).date() <= due:
        return {str(row['observation_id']): _unknown('等待7日验证', 'waiting') for row in cohort['signals']}
    has_due = daily.get((cohort['shop_id'], due.isoformat()))
    if not _complete(has_due):
        state = 'waiting' if now.astimezone(SHANGHAI).date() <= due else 'insufficient_data'
        return {str(row['observation_id']): _unknown('等待7日后的完整采集', state)
                for row in cohort['signals']}
    anchor_date = date.fromisoformat(cohort['date'])
    future_runs = [daily.get((cohort['shop_id'], (anchor_date + timedelta(days=i)).isoformat()))
                   for i in range(1, 8)]
    if any(not _complete(run) for run in future_runs):
        return {str(row['observation_id']): _unknown('未来7日存在缺采或部分采集') for row in cohort['signals']}
    if any(_timestamp(run.get('imported_at')) is None for run in future_runs):
        return {str(row['observation_id']): _unknown('缺少导入可用时间，不能封存验证结果') for row in cohort['signals']}
    if any(_timestamp(run['observed_to']) > now or _timestamp(run['imported_at']) > now for run in future_runs):
        return {str(row['observation_id']): _unknown('等待实际完成采集', 'waiting') for row in cohort['signals']}
    anchor_pool = cohort['candidates']
    anchored = [_pairs(anchor_pool, by_run[run['run_id']]) for run in future_runs]
    adjacent = []
    previous_pool = anchor_pool
    for run in future_runs:
        adjacent.append(_pairs(by_run[run['run_id']], previous_pool))
        previous_pool = by_run[run['run_id']]
    frozen = {row['observation_id']: row for row in anchor_pool}
    result = {}
    for signal in cohort['signals']:
        oid = signal['observation_id']
        old = frozen[oid]
        old_run = anchor_run
        deltas, hours, ids, bases = [], [], [oid], []
        reason = None
        for index, run in enumerate(future_runs):
            new, anchor_basis, _ = anchored[index][oid]
            if new is None:
                reason = '未来商品未匹配或有身份冲突'; break
            direct_old, direct_basis, _ = adjacent[index][new['observation_id']]
            if direct_old is None or direct_old['observation_id'] != old['observation_id']:
                reason = '相邻日期身份不连续'; break
            before, after = _protocol_sales(old, cohort['protocol']), _protocol_sales(new, cohort['protocol'])
            if before is None or after is None or after < before:
                reason = '未来销量缺失、口径不符或出现下降异常'; break
            start, end = _point_seconds(old, old_run), _point_seconds(new, run)
            if start is None or end is None or end <= start:
                reason = '采集时间精度不足，不能比较增长速度'; break
            interval = (end - start) / 3600
            if not cohort['protocol']['min_interval_hours'] <= interval <= cohort['protocol']['max_interval_hours']:
                reason = '采集间隔异常，不能形成日级验证'; break
            deltas.append(after - before); hours.append(interval)
            ids.append(new['observation_id']); bases.extend([anchor_basis, direct_basis])
            old, old_run = new, run
        if reason:
            result[str(oid)] = _unknown(reason); continue
        delta = sum(deltas)
        rate = delta / sum(hours) * 24
        baseline = signal.get('avg_recent')
        basis = ('confirmed_goods_id' if signal.get('basis') == 'confirmed_goods_id'
                 and all(item == 'confirmed_goods_id' for item in bases) else 'provisional_title_image')
        facts = {'future_delta': delta, 'future_rate24': round(rate, 6),
                 'positive_intervals': sum(value > 0 for value in deltas),
                 'last_three_delta': sum(deltas[-3:]), 'basis': basis,
                 'future_observation_ids': ids, 'future_run_ids': [r['run_id'] for r in future_runs]}
        if signal.get('normalized_delta_days', 0) < 3 or not _finite(baseline):
            result[str(oid)] = _unknown('推荐时不足3段有效基准，只记录后续变化', **facts); continue
        if baseline <= 0:
            result[str(oid)] = _unknown('零基期起量单列，不计算延续倍率', **facts); continue
        protocol = cohort['protocol']
        hit = delta >= protocol['min_future_delta'] and rate >= baseline * protocol['min_rate_ratio'] and facts['positive_intervals'] >= protocol['min_positive_intervals']
        result[str(oid)] = {'evaluation_state': 'evaluated', 'outcome': hit,
            'outcome_label': ('7日达到起量门槛' if hit else '7日未达到起量门槛') +
                             ('（同图展示线索）' if basis != 'confirmed_goods_id' else ''), **facts}
    return result


def _protocol_sales(row, protocol):
    """Old frozen cohorts retain their original exact-已拼 sales definition."""
    if protocol == LEGACY_PROTOCOL and row.get('sales_label') != '已拼':
        return None
    if protocol not in SUPPORTED_PROTOCOLS:
        raise ValueError('Unsupported frozen sales protocol')
    return _yipin(row)


def update_ledger(ledger, queries, *, now):
    if now.tzinfo is None:
        raise ValueError('Decision time must include timezone')
    next_value = deepcopy(ledger)
    cohorts = next_value['cohorts']
    today = now.astimezone(SHANGHAI).date().isoformat()
    runs, originals, _ = _validated_history(queries)
    grouped = defaultdict(list)
    for row in queries.get('trend_signals', {}).get('rows', []):
        grouped[(row['shop_id'], row['run_id'])].append(row)
    for (shop, run_id), signals in sorted(grouped.items()):
        run = runs.get(run_id)
        if not _complete(run) or run['shop_id'] != shop or _day(run).isoformat() != today:
            continue  # No retrospective recommendation of stale data.
        if datetime.fromisoformat(run['observed_to'].replace('Z', '+00:00')) > now:
            continue
        imported = _timestamp(run.get('imported_at'))
        if imported is None or imported > now:
            continue  # A future or unknown import was not usable at this decision time.
        rule = signals[0].get('model_version') or signals[0].get('rule_version')
        if not isinstance(rule, str) or not rule:
            raise ValueError('Trend signal requires its actual rule/model version')
        cid = 'decision_' + hashlib.sha256(f'{shop}|{run_id}|{rule}|{PROTOCOL["version"]}'.encode()).hexdigest()[:24]
        if any(c['cohort_id'] == cid for c in cohorts):
            continue
        pool = [{key: deepcopy(row.get(key)) for key in CARD_FIELDS} for row in originals[run_id]]
        # Freeze all candidate signals (including non-selected watched cards) for
        # later baseline comparisons and false-negative measurement.
        candidates = [deepcopy(r) for r in signals if r.get('included_in_watch')]
        chosen = [r['observation_id'] for r in ranked_signals(candidates)[:5]]
        cohorts.append({'cohort_id': cid, 'shop_id': shop, 'run_id': run_id,
            'date': today, 'due_date': (date.fromisoformat(today) + timedelta(days=7)).isoformat(),
            'recorded_at': now.isoformat(), 'rule_version': rule, 'protocol': deepcopy(PROTOCOL),
            'snapshot_sha256': run.get('snapshot_sha256'), 'candidates': pool,
            'signals': candidates, 'selected_ids': chosen, 'evaluations': {}})
    for cohort in cohorts:
        evaluations = evaluate_cohort(cohort, queries, now=now)
        # Mature results are frozen; subsequent corrections require a new
        # explicitly versioned evaluation protocol, never silent rewriting.
        for oid, result in evaluations.items():
            old = cohort['evaluations'].get(oid)
            if not old or old.get('evaluation_state') != 'evaluated':
                comparable_old = {key: value for key, value in (old or {}).items()
                                  if key not in ('evaluated_at', 'outcome_available_at', 'availability_policy')}
                if comparable_old == result:
                    continue
                if result['evaluation_state'] != 'waiting':
                    result.update(evaluated_at=now.isoformat(), outcome_available_at=now.isoformat(),
                                  availability_policy='after_due_day_and_import_v1')
                cohort['evaluations'][oid] = result
    if next_value != ledger:
        next_value['revision'] = ledger['revision'] + 1
    return next_value


def _atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile('w', encoding='utf-8', dir=path.parent,
                                         prefix=path.name + '.', suffix='.tmp', delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(value, stream, ensure_ascii=False, allow_nan=False, indent=2)
            stream.write('\n'); stream.flush(); os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def record_decisions(project, queries, *, now=None):
    """Explicit standard-publication hook, idempotent for the same source run."""
    path = ledger_path(project)
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = path.with_suffix('.lock')
    try:
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        raise ValueError('Trend decision publisher is busy; preserve its lock and retry after it finishes') from exc
    try:
        os.close(descriptor)
        old = read_ledger(project)
        new = update_ledger(old, queries, now=now or datetime.now(timezone.utc))
        if new != old:
            if path.exists():
                digest = hashlib.sha256(path.read_bytes()).hexdigest()[:16]
                backup = path.parent / 'history' / f'revision_{old["revision"]}_{digest}.json'
                if not backup.exists():
                    _atomic_json(backup, old)
            _atomic_json(path, new)
        return new
    finally:
        lock.unlink(missing_ok=True)


def build_decision_queries(queries, project, *, ledger=None):
    ledger = read_ledger(project) if ledger is None else ledger
    records, summary = [], defaultdict(lambda: {'waiting_count': 0, 'unknown_count': 0,
        'evaluated_confirmed_count': 0, 'hit_confirmed_count': 0,
        'evaluated_clue_count': 0, 'hit_clue_count': 0, 'legacy_protocol_record_count': 0})
    for cohort in ledger['cohorts']:
        for signal in cohort['signals']:
            oid = signal['observation_id']
            if oid not in cohort['selected_ids']:
                continue
            outcome = cohort['evaluations'].get(str(oid), _unknown('等待7日验证', 'waiting'))
            row = {key: cohort[key] for key in ('shop_id', 'run_id', 'date', 'due_date', 'cohort_id', 'recorded_at', 'rule_version')}
            row.update(observation_id=oid, title=signal.get('title'), record_state='recorded',
                       protocol_version=cohort['protocol']['version'],
                       baseline_rate24=signal.get('avg_recent'), signal_label=signal.get('label'),
                       **outcome)
            records.append(row)
            stats = summary[row['shop_id']]
            if cohort['protocol'] == LEGACY_PROTOCOL:
                stats['legacy_protocol_record_count'] += 1
                continue
            if row['evaluation_state'] == 'waiting': stats['waiting_count'] += 1
            elif row['outcome'] is None: stats['unknown_count'] += 1
            elif row['basis'] == 'confirmed_goods_id':
                stats['evaluated_confirmed_count'] += 1; stats['hit_confirmed_count'] += int(row['outcome'])
            else:
                stats['evaluated_clue_count'] += 1; stats['hit_clue_count'] += int(row['outcome'])
    summaries = [{'shop_id': shop, 'protocol_version': PROTOCOL['version'], **stats,
        'confirmed_hit_rate': stats['hit_confirmed_count'] / stats['evaluated_confirmed_count'] if stats['evaluated_confirmed_count'] else None,
        'clue_hit_rate': stats['hit_clue_count'] / stats['evaluated_clue_count'] if stats['evaluated_clue_count'] else None,
        'ledger_revision': ledger['revision'], 'forecast_validated': False}
        for shop, stats in sorted(summary.items())]
    result = {}
    for key, rows, label in [('trend_decision_records', records, '推荐时冻结的7日跟踪记录'),
                              ('trend_decision_summary', summaries, '按店区分已确认与同图线索的7日验证')]:
        source = deepcopy(queries.get('observations', {}).get('source', {}))
        source.update(label=label, classification='derived', provider='Local decision ledger + reviewed observations',
            files=[*source.get('files', []), str(ledger_path(project)), str(Path(__file__).resolve())],
            grain='one prospective selected original card' if key.endswith('records') else 'one shop',
            caveats=['初始门槛未经历史效果验证，不是爆款概率；未成熟、缺采、基准不足均不记命中或失败。',
                     '旧已拼协议记录原样保留并按原协议验证；汇总命中率仅纳入当前已拼/已抢协议，两个口径不混算。',
                     '同图展示线索与可靠商品身份结果独立；不预测跟品盈利。',
                     '仅本机页面展示分析提醒，没有接通无人值守网页采集或外部推送。'],
            metricDefinitions=[{'label': '7日持续起量初始门槛', 'definition': PROTOCOL['description']}],
            derivation='Prospective versioned cohort ledger; freeze full candidate universe; no retrospective recommendation.')
        result[key] = {'rows': rows, 'source': source, 'methods': [{'language': 'python', 'code': 'build_decision_queries(queries, project)'}]}
    return result
