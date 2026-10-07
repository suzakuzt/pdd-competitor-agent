"""User-entered order economics and append-only trial records; no business DB writes."""
from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from datetime import date, datetime, timezone, timedelta
from decimal import Decimal, InvalidOperation, localcontext, ROUND_CEILING, ROUND_FLOOR
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import uuid

PLAN_FIELDS = ('price', 'goods_cost', 'packaging_cost', 'shipping_cost', 'platform_fee_rate_pct',
               'platform_fee_fixed', 'refund_allowance', 'ad_cost_per_order', 'other_cost_per_order',
               'tax_cost_per_order', 'fixed_test_cost', 'max_loss_budget', 'target_orders', 'review_after_orders')
PLAN_COST_FIELDS = ('goods_cost', 'packaging_cost', 'shipping_cost', 'platform_fee_fixed',
                    'refund_allowance', 'ad_cost_per_order', 'other_cost_per_order', 'tax_cost_per_order')
ACTUAL_COST_FIELDS = ('goods_cost', 'packaging_cost', 'shipping_cost', 'platform_fees', 'ad_spend',
                      'refund_extra_cost', 'tax_cost', 'other_cost', 'fixed_cost')
ACTUAL_FIELDS = ('period_start', 'period_end', 'paid_orders', 'settled_orders', 'net_receipts',
                 *ACTUAL_COST_FIELDS, 'settlement_complete', 'evidence_reference')
CHECK_FIELDS = ('supply_confirmed', 'rights_confirmed', 'spec_confirmed')
TRIAL_FIELDS = ('trial_id', 'revision', 'name', 'shop_id', 'observation_id', 'channel', 'own_product_ref',
                'status', 'plan', 'actual', 'checks', 'notes', 'created_at', 'updated_at')
STATUSES = ('draft', 'testing', 'paused', 'closed')
QUERY_IDS = ('profit_trials', 'profit_trial_summary')
FIELD_DEFINITIONS = {
    'grain': '计划一笔订单；实际同一试品、渠道、商品和起始期间的累计订单队列。',
    'plan.price': '每笔订单预计收款单价，元；不是竞品售价自动建议。',
    'plan.platform_fee_rate_pct': '按该计划价格计费的百分数，例如0.6表示0.6%；无默认费率。',
    'plan.refund_allowance': '计划每单退款/售后损耗准备，仅用于估算，不同时扣入实际退款本金。',
    'plan.fixed_test_cost': '此试品队列可归集的一次性测试成本；不分摊给不相关队列。',
    'plan.max_loss_budget': '用户明确填写的此试品止损额度，不会自动加预算。',
    'actual.net_receipts': '累计净回款，已扣退款但未扣任何其他费用；平台如已扣费，须先还原，否则会重复扣费。',
    'actual.refund_extra_cost': '退款之外的额外退货运费、处置等成本，不重复包含已从净回款扣除的退款本金。',
    'actual.platform_fees': '此队列实际平台费用总额，须与未扣费用的净回款同口径。',
    'actual.fixed_cost': '此队列实际一次性可归集成本总额，不自动采用计划值。',
    'other_cost': '尚未计入其他成本项的可归集成本；一笔费用只记一次。',
    'actual.evidence_reference': '用户提供的结算报表名或证据说明；填写后仍不等于平台已核验。',
    'actual.period': '同一累计队列；YYYY-MM-DD按北京时间日期，完整时间戳按明确时区；已结清期间不能晚于评估时点。',
}


class RevisionConflict(ValueError):
    """Caller must reload the latest version rather than overwrite it."""


class IntegrityError(ValueError):
    """Saved revision, evidence hash or lineage does not match."""


def _now():
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')


def _bytes(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')


def _sha(value):
    return hashlib.sha256(value).hexdigest()


def _display(value):
    if value is None:
        return None
    if value == 0:
        return '0'
    text = format(value, 'f')
    return text.rstrip('0').rstrip('.') if '.' in text else text


def _decimal(value, field, *, signed=False):
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        raise ValueError(f'{field}: expected a decimal number or null')
    if isinstance(value, str) and (not value.strip() or len(value) > 80):
        raise ValueError(f'{field}: blank is not zero; use null for unknown')
    try:
        number = Decimal(str(value).strip())
    except InvalidOperation as exc:
        raise ValueError(f'{field}: invalid decimal') from exc
    if not number.is_finite() or (not signed and number < 0):
        raise ValueError(f'{field}: expected a finite non-negative number')
    # Bounded input precision protects exact arithmetic without rounding user costs.
    if number.adjusted() > 17 or number.as_tuple().exponent < -12:
        raise ValueError(f'{field}: at most 18 integer and 12 fractional digits')
    return number


def _integer(value, field):
    number = _decimal(value, field)
    if number is not None and number != number.to_integral_value():
        raise ValueError(f'{field}: order count must be an integer')
    return int(number) if number is not None else None


def _text(value, field, *, maximum=4000, required=False):
    if value is None:
        if required:
            raise ValueError(f'{field}: required')
        return None
    if not isinstance(value, str) or len(value) > maximum:
        raise ValueError(f'{field}: invalid text')
    result = value.strip()
    if required and not result:
        raise ValueError(f'{field}: required')
    return result or None


def _bool(value, field):
    if value is not None and type(value) is not bool:
        raise ValueError(f'{field}: expected true, false or null')
    return value


def _object(value, allowed, field):
    if value is None:
        return {}
    if not isinstance(value, dict) or set(value) - set(allowed):
        raise ValueError(f'{field}: unknown fields or invalid object')
    return value


def _plan(value):
    value = _object(value, PLAN_FIELDS, 'plan')
    result = {}
    for field in PLAN_FIELDS:
        result[field] = (_integer(value.get(field), f'plan.{field}') if field in ('target_orders', 'review_after_orders')
                         else _display(_decimal(value.get(field), f'plan.{field}')))
        if field in ('target_orders', 'review_after_orders') and result[field] is not None and result[field] < 1:
            raise ValueError(f'plan.{field}: must be a positive integer or null')
    rate = result['platform_fee_rate_pct']
    if rate is not None and Decimal(rate) > 100:
        raise ValueError('plan.platform_fee_rate_pct: must be between 0 and 100')
    return result


def _period(value, field):
    value = _text(value, field, maximum=80)
    if value is None:
        return None, None
    if re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        return value, ('date', date.fromisoformat(value))
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if parsed.tzinfo is None:
            raise ValueError('missing timezone')
    except ValueError as exc:
        raise ValueError(f'{field}: use YYYY-MM-DD or timezone-aware ISO timestamp') from exc
    return parsed.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z'), ('timestamp', parsed)


def _actual(value, now=None):
    value = _object(value, ACTUAL_FIELDS, 'actual')
    result = {field: _display(_decimal(value.get(field), f'actual.{field}', signed=(field == 'net_receipts')))
              for field in ('net_receipts', *ACTUAL_COST_FIELDS)}
    for field in ('paid_orders', 'settled_orders'):
        result[field] = _integer(value.get(field), f'actual.{field}')
    result['settlement_complete'] = _bool(value.get('settlement_complete'), 'actual.settlement_complete')
    result['evidence_reference'] = _text(value.get('evidence_reference'), 'actual.evidence_reference')
    result['period_start'], start = _period(value.get('period_start'), 'actual.period_start')
    result['period_end'], end = _period(value.get('period_end'), 'actual.period_end')
    if start and end and (start[0] != end[0] or end[1] < start[1]):
        raise ValueError('actual period must have one precision and start <= end')
    if result['settlement_complete'] is True and end:
        _, as_of = _period(now or _now(), 'now')
        if not as_of or as_of[0] != 'timestamp':
            raise ValueError('now must be a timezone-aware timestamp')
        cutoff = as_of[1].astimezone(timezone(timedelta(hours=8))).date() if end[0] == 'date' else as_of[1]
        if end[1] > cutoff:
            raise ValueError('A completed settlement cannot have a future period_end')
    paid, settled = result['paid_orders'], result['settled_orders']
    if paid is not None and settled is not None:
        if settled > paid:
            raise ValueError('settled_orders cannot exceed paid_orders')
        if result['settlement_complete'] is True and settled != paid:
            raise ValueError('settlement_complete requires every paid order to be settled')
    if paid == 0 and result['net_receipts'] is not None and Decimal(result['net_receipts']) != 0:
        raise ValueError('zero paid_orders cannot have non-zero order net_receipts')
    return result


def _uuid(value):
    if not isinstance(value, str):
        raise ValueError('trial_id must be a canonical UUID string')
    try:
        canonical = str(uuid.UUID(value))
    except (ValueError, AttributeError) as exc:
        raise ValueError('trial_id must be a canonical UUID string') from exc
    if canonical != value:
        raise ValueError('trial_id must be a canonical lowercase UUID string')
    return canonical


def _trial(payload, *, require_identity=False, now=None):
    payload = _object(payload, TRIAL_FIELDS, 'trial')
    status = payload.get('status', 'draft')
    if status not in STATUSES:
        raise ValueError('Invalid trial status')
    checks = _object(payload.get('checks'), CHECK_FIELDS, 'checks')
    result = {field: _text(payload.get(field), field, maximum=8000 if field == 'notes' else 240)
              for field in ('name', 'shop_id', 'channel', 'own_product_ref', 'notes')}
    observation_id = payload.get('observation_id')
    if observation_id is not None and (type(observation_id) is not int or observation_id <= 0):
        raise ValueError('observation_id must be a positive integer or null')
    result['observation_id'] = observation_id
    if require_identity:
        result['trial_id'] = _uuid(payload.get('trial_id'))
        result['name'] = _text(payload.get('name'), 'name', maximum=240, required=True)
    else:
        result['trial_id'] = _uuid(payload['trial_id']) if payload.get('trial_id') else None
    result.update(status=status, plan=_plan(payload.get('plan')), actual=_actual(payload.get('actual'), now=now),
                  checks={k: _bool(checks.get(k), f'checks.{k}') for k in CHECK_FIELDS})
    return result


def calculate_plan(plan):
    plan = _plan(plan)
    missing = [f'plan.{key}' for key in PLAN_FIELDS if plan[key] is None]
    operands = ('price', 'platform_fee_rate_pct', *PLAN_COST_FIELDS)
    contribution = before_ad = variable = platform_fee = after_fixed = None
    with localcontext() as context:
        context.prec = 80
        before_ad_operands = tuple(key for key in operands if key != 'ad_cost_per_order')
        if all(plan[key] is not None for key in before_ad_operands):
            number = {key: Decimal(plan[key]) for key in before_ad_operands}
            platform_fee = number['price'] * number['platform_fee_rate_pct'] / Decimal(100) + number['platform_fee_fixed']
            before_ad_cost = number['price'] * number['platform_fee_rate_pct'] / Decimal(100) + sum(
                (number[k] for k in PLAN_COST_FIELDS if k != 'ad_cost_per_order'), Decimal(0))
            before_ad = number['price'] - before_ad_cost
            if plan['ad_cost_per_order'] is not None:
                variable = before_ad_cost + Decimal(plan['ad_cost_per_order'])
                contribution = number['price'] - variable
            if plan['target_orders'] is not None and plan['target_orders'] > 0 and plan['fixed_test_cost'] is not None:
                after_fixed = (before_ad - Decimal(plan['fixed_test_cost']) / Decimal(plan['target_orders'])).quantize(
                    Decimal('0.01'), rounding=ROUND_FLOOR)
        surplus = (contribution * Decimal(plan['target_orders']) - Decimal(plan['fixed_test_cost'])
                   if contribution is not None and plan['target_orders'] is not None and plan['fixed_test_cost'] is not None else None)
        break_even = (int((Decimal(plan['fixed_test_cost']) / contribution).to_integral_value(rounding=ROUND_CEILING))
                      if contribution is not None and contribution > 0 and plan['fixed_test_cost'] is not None else None)
    return {'normalized_plan': plan, 'contribution_per_order': _display(contribution),
            'before_ad_contribution': _display(before_ad), 'max_ad_cost_per_order': _display(before_ad),
            'max_ad_after_fixed_per_order': _display(after_fixed),
            'variable_cost_per_order': _display(variable), 'platform_fee_per_order': _display(platform_fee),
            'planned_surplus': _display(surplus), 'break_even_orders': break_even,
            'missing_fields': missing, 'calculation_complete': not missing,
            'max_ad_cost_basis': '每单变动成本口径、未分摊固定测试成本；负数表示投放前已经亏损。',
            'max_ad_after_fixed_basis': '扣除固定测试成本/目标订单数后，按人民币分向下取整的每单投放上限；负数表示即使零投放也难按目标保本。',
            'break_even_basis': 'ceil(固定测试成本/正的每单贡献)；非正贡献不计算保本单数。',
            'guaranteed_profit': False, 'grain': 'one_order', 'currency': 'CNY'}


def _actual_result(actual):
    required = [field for field in ACTUAL_FIELDS if field != 'evidence_reference']
    missing = [f'actual.{field}' for field in required if actual[field] is None]
    cost_total = profit = None
    with localcontext() as context:
        context.prec = 80
        if all(actual[key] is not None for key in ACTUAL_COST_FIELDS):
            cost_total = sum((Decimal(actual[k]) for k in ACTUAL_COST_FIELDS), Decimal(0))
        if not missing:
            profit = Decimal(actual['net_receipts']) - cost_total
    return {'actual_profit': _display(profit), 'cost_total': _display(cost_total),
            'net_receipts': actual['net_receipts'], 'paid_orders': actual['paid_orders'],
            'settled_orders': actual['settled_orders'], 'settlement_complete': actual['settlement_complete'],
            'profit_is_final': bool(profit is not None and actual['settlement_complete'] is True),
            'profit_label': '用户记录的结清结余' if actual['settlement_complete'] is True else '用户记录的暂列结余',
            'missing_fields': missing, 'evidence_status': 'user_declared_reference' if actual['evidence_reference'] else 'not_provided',
            'platform_verified': False, 'currency': 'CNY'}


def _has_actual(actual):
    return any(actual[key] is not None for key in ('period_start', 'period_end', 'paid_orders', 'settled_orders', 'net_receipts', *ACTUAL_COST_FIELDS))


def evaluate_trial(trial, now=None):
    trial = _trial(trial, now=now)
    plan_result = calculate_plan(trial['plan'])
    actual_result = _actual_result(trial['actual'])
    missing = plan_result['missing_fields'] + [key for key in ('name', 'shop_id', 'observation_id', 'channel', 'own_product_ref') if not trial[key]]
    unconfirmed = [f'checks.{key}' for key in CHECK_FIELDS if trial['checks'][key] is not True]
    ready = not missing and not unconfirmed
    contribution = Decimal(plan_result['contribution_per_order']) if plan_result['contribution_per_order'] is not None else None
    profit = Decimal(actual_result['actual_profit']) if actual_result['actual_profit'] is not None else None
    budget = Decimal(trial['plan']['max_loss_budget']) if trial['plan']['max_loss_budget'] is not None else None
    planned_surplus = Decimal(plan_result['planned_surplus']) if plan_result['planned_surplus'] is not None else None
    reasons = []
    if trial['status'] == 'closed':
        code, label, reasons = 'keep_observing', '已关闭，保留复盘', ['记录已关闭；模型不会自动重新执行或增加预算。']
    elif trial['status'] == 'paused':
        code, label, reasons = 'pause_review', '暂停复核', ['试品已由用户暂停，须人工复核后决定。']
    elif contribution is not None and contribution < 0:
        code, label, reasons = 'pause_review', '暂停复核', ['按已填成本，计划每单贡献为负。']
    elif profit is not None and profit < 0 and budget is not None and -profit >= budget:
        code, label, reasons = 'pause_review', '暂停复核', ['用户记录的累计亏损已达到或超过所填止损预算。']
    elif planned_surplus is not None and planned_surplus < 0 and budget is not None and -planned_surplus >= budget:
        code, label, reasons = 'pause_review', '暂停复核', ['计入固定测试成本后，计划总亏损已达到或超过所填止损预算。']
    elif not ready:
        code, label, reasons = 'missing_info', '补充资料', ['计划、订单归属或供货/授权/规格核验尚不完整。']
    elif not _has_actual(trial['actual']):
        code, label, reasons = 'not_started', '待执行', ['计划资料齐备；尚未录入实际队列结果，不代表已下单。']
    elif actual_result['missing_fields']:
        code, label, reasons = 'missing_info', '补充资料', ['实际累计期间、订单数、净回款、成本或结算状态尚未填全。']
    elif not trial['actual']['evidence_reference']:
        code, label, reasons = 'missing_info', '补充资料', ['缺少结算依据说明，当前仅为用户自填数字，不给复测建议。']
    elif trial['actual']['paid_orders'] == 0:
        code, label, reasons = 'keep_observing', '继续观察', ['本队列尚无支付订单；已录入费用照实保留，不能判断试品盈利。']
    elif trial['actual']['settlement_complete'] is not True:
        code, label, reasons = 'keep_observing', '继续观察', ['本队列尚未全部结清，暂列结余不能作为复测依据。']
    elif trial['actual']['settled_orders'] < trial['plan']['review_after_orders']:
        code, label, reasons = 'keep_observing', '继续观察', ['已结算订单尚未达到用户填写的复核样本门槛。']
    elif profit is not None and profit > 0:
        code, label, reasons = 'consider_small_retest', '可考虑小幅复测', ['用户记录已结清、达到自定样本门槛且结余为正；核验项已确认。是否复测及预算仍由你决定。']
    else:
        code, label, reasons = 'keep_observing', '继续观察', ['当前结清结果没有正结余，暂不建议复测；不自动延长投放。']
    return {'plan_result': plan_result, 'actual_result': actual_result,
            'readiness': {'ready': ready, 'missing_fields': missing, 'unconfirmed_checks': unconfirmed},
            'recommendation': {'code': code, 'label': label, 'reasons': reasons},
            'user_input_only': True, 'automatic_action': False,
            'caveats': ['计算包括用户填入的全部可归集成本，不保证利润。',
                        '实际数据来自用户录入；证据说明不是平台核验。',
                        '净回款扣退款但未扣其他费用，避免平台费用和退款本金重复扣除。']}


def _assert_same_queue(old, new):
    if not _has_actual(old['actual']):
        return
    for field in ('shop_id', 'observation_id', 'channel', 'own_product_ref'):
        if old[field] != new[field]:
            raise ValueError('Existing actual results belong to one fixed trial queue; create a new trial for changed attribution')
    start = old['actual']['period_start']
    if start is not None and new['actual']['period_start'] != start:
        raise ValueError('Actual cumulative period_start is fixed; create a new trial for a different window')
    end = old['actual']['period_end']
    if end is not None:
        _, old_end = _period(end, 'old.period_end')
        _, new_end = _period(new['actual']['period_end'], 'new.period_end')
        if not new_end or old_end[0] != new_end[0] or new_end[1] < old_end[1]:
            raise ValueError('Cumulative period_end cannot move backwards or change precision')
    for field in ('paid_orders', 'settled_orders'):
        a, b = old['actual'][field], new['actual'][field]
        if a is not None and (b is None or b < a):
            raise ValueError('Known cumulative order counts cannot be erased or decreased')


def _revision_number(value):
    if type(value) is not int or value < 0:
        raise ValueError('expected_revision must be a non-negative integer')
    return value


def _read_history(folder):
    if not folder.exists():
        return [], {}
    records, files = [], {}
    for path in sorted(folder.glob('*.json')):
        raw = path.read_bytes()
        try:
            stored = json.loads(raw)
            record_sha = stored['record_sha256']
            unsigned = {k: v for k, v in stored.items() if k != 'record_sha256'}
            trial = stored['trial']
            normal = _trial(trial, require_identity=True, now=trial.get('updated_at'))
            revision = len(records) + 1
            if (stored.get('schema_version') != 1 or path.name != f'{revision:06d}.json'
                    or trial['revision'] != revision or trial['trial_id'] != folder.name
                    or _sha(_bytes(unsigned)) != record_sha
                    or _sha(_bytes(normal)) != stored['content_sha256']
                    or stored['previous_record_sha256'] != (records[-1]['record_sha256'] if records else None)):
                raise IntegrityError('Trial revision checksum, sequence or identity mismatch')
            _, created = _period(trial['created_at'], 'created_at')
            _, updated = _period(trial['updated_at'], 'updated_at')
            if not created or not updated or created[0] != 'timestamp' or updated[0] != 'timestamp' or updated[1] < created[1]:
                raise IntegrityError('Invalid trial audit timestamps')
            if records:
                for earlier in records:
                    _assert_same_queue(earlier['trial'], trial)
                if trial['created_at'] != records[0]['trial']['created_at']:
                    raise IntegrityError('Trial creation time changed')
        except (KeyError, TypeError, ValueError) as exc:
            if isinstance(exc, IntegrityError):
                raise
            raise IntegrityError(f'Invalid trial history: {path.name}') from exc
        records.append(stored)
        files[str(path.resolve())] = _sha(raw)
    return records, files


def load_trials(project):
    """Return (latest trial dictionaries, all immutable revision file SHA256s)."""
    root = Path(project).resolve() / 'state/profit_lab/trials'
    if not root.exists():
        return [], {}
    latest, files = [], {}
    for folder in sorted(root.iterdir()):
        if not folder.is_dir():
            raise IntegrityError('Unexpected file in trial collection')
        _uuid(folder.name)
        history, evidence = _read_history(folder)
        files.update(evidence)
        if history:
            latest.append(deepcopy(history[-1]['trial']))
    latest.sort(key=lambda row: (row['updated_at'], row['trial_id']), reverse=True)
    return latest, files


def _write_new(path, raw):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.writing-', delete=False) as stream:
            temp = Path(stream.name)
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temp, path)
    finally:
        if temp is not None and temp.exists():
            temp.unlink()


def save_trial(project, payload, expected_revision):
    expected = _revision_number(expected_revision)
    normal = _trial(payload, require_identity=True)
    if 'revision' in payload and _revision_number(payload['revision']) != expected:
        raise RevisionConflict('payload revision must equal expected_revision')
    project = Path(project).resolve()
    root = project / 'state/profit_lab'
    folder = root / 'trials' / normal['trial_id']
    locks = root / '.locks'
    locks.mkdir(parents=True, exist_ok=True)
    lock = locks / (normal['trial_id'] + '.lock')
    try:
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise RevisionConflict('Trial save is already locked; verify owner before explicit recovery') from exc
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
            json.dump({'pid': os.getpid(), 'started_at': _now()}, stream)
        history, _ = _read_history(folder)
        current = len(history)
        digest = _sha(_bytes(normal))
        if expected > current:
            raise RevisionConflict('Expected revision is newer than saved history')
        if history and digest == history[-1]['content_sha256']:
            old = history[-1]
            return {'status': 'duplicate', 'trial_id': normal['trial_id'], 'revision': current,
                    'record_sha256': old['record_sha256'], 'path': str(folder / f'{current:06d}.json'),
                    'trial': deepcopy(old['trial'])}
        if expected != current:
            raise RevisionConflict('Trial changed; reload the latest revision before saving')
        for earlier in history:
            _assert_same_queue(earlier['trial'], normal)
        now = _now()
        trial = dict(normal, revision=current + 1,
                     created_at=history[0]['trial']['created_at'] if history else now, updated_at=now)
        envelope = {'schema_version': 1, 'trial': trial, 'content_sha256': digest,
                    'previous_record_sha256': history[-1]['record_sha256'] if history else None}
        envelope['record_sha256'] = _sha(_bytes(envelope))
        target = folder / f'{current + 1:06d}.json'
        _write_new(target, _bytes(envelope))
        return {'status': 'saved', 'trial_id': normal['trial_id'], 'revision': current + 1,
                'record_sha256': envelope['record_sha256'], 'path': str(target), 'trial': deepcopy(trial)}
    finally:
        lock.unlink()


def build_trial_queries(project):
    trials, files = load_trials(project)
    rows = [dict(trial, analysis=evaluate_trial(trial), input_source='user_entered_trial_record') for trial in trials]
    grouped = defaultdict(list)
    for row in rows:
        grouped[row['shop_id']].append(row)
    summary = []
    for shop_id, items in sorted(grouped.items(), key=lambda item: item[0] or ''):
        summary.append({'shop_id': shop_id, 'trial_count': len(items),
                        **{status + '_count': sum(row['status'] == status for row in items) for status in STATUSES},
                        'ready_count': sum(row['analysis']['readiness']['ready'] for row in items),
                        'missing_info_count': sum(row['analysis']['recommendation']['code'] == 'missing_info' for row in items),
                        'pause_review_count': sum(row['analysis']['recommendation']['code'] == 'pause_review' for row in items),
                        'financial_totals_aggregated': False})
    source = {'provider': '用户填写的试品计划与累计结算记录', 'files': files, 'tables': [],
              'executedAt': _now(), 'grain': 'one latest revision per independent trial queue',
              'filters': ['按试品ID及其店铺/原卡锚点；不是平台销量或结算接口'],
              'fieldDefinitions': FIELD_DEFINITIONS,
              'metricDefinitions': [
                  {'id': 'contribution_per_order', 'label': '计划每单贡献',
                   'description': '价格×(1-平台百分费率)-全部已填每单变动成本；缺任一所需值则未知。'},
                  {'id': 'planned_surplus', 'label': '全口径计划结余',
                   'description': '计划每单贡献×用户目标订单数-固定测试成本；不保证实现。'},
                  {'id': 'actual_profit', 'label': '用户记录的累计结余',
                   'description': '已扣退款未扣其他费用的净回款-全部可归集实际成本；期间/订单/费用/结算状态缺失则未知。'}],
              'caveats': ['金额为元，内部Decimal精确运算；未知保留NULL，0必须明确填写。',
                          '证据说明与核验勾选是用户声明，不构成平台或法律授权核验。',
                          '不同试品队列可能重叠，不合计成店铺总利润，不自动加预算或下单。']}
    methods = ['计划按一笔订单计算；实际按固定试品队列累计记录',
               '只读取最新版本供展示，所有追加历史文件与SHA保留来源证据',
               '止损/复测仅为资料规则提示，执行及预算由用户决定']
    return {key: {'rows': value, 'source': deepcopy(source), 'methods': methods[:]}
            for key, value in zip(QUERY_IDS, (rows, summary))}
