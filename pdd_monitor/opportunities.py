"""Read-only, per-card follow-up priorities. These are evidence tiers, not profit scores."""
from __future__ import annotations

from pdd_monitor.sales_metrics import SALES_METRIC_LABELS, same_sales_metric

from collections import Counter, defaultdict
from copy import deepcopy
from datetime import datetime, timezone
import math
from pathlib import Path
import re
from urllib.parse import parse_qs, urlsplit

QUERY_IDS = ('profit_opportunities', 'profit_opportunity_summary')
UPSTREAM = ('runs', 'observations', 'competitor_shops', 'competitor_products',
            'comparison_items', 'comparison_summaries', 'new_arrival_items', 'new_arrival_summary', 'artist_watchlist')
TIERS = {
    'new_candidate': (1, '固定起点后候选·先核验'),
    'comparable_growth': (2, '已有可比正增长·核验原因'),
    'eligible_reference': (3, '单卡已拼>10·已有观察参考'),
    'watch': (4, '待观察·证据不足'),
}
LIMITATIONS = [
    '优先级是证据跟进顺序，不是盈利评分、购买建议或利润承诺。',
    '每行仍是一张原卡；参考轮与最新轮只组成工作队列，不相加为商品数或销量。',
    '固定起点后候选不等于实际上架，历史达标不等于今天新款；缺ID不合并卡片。',
    '补扫扩覆盖首见不触发新品优先；缺ID弱首见且未单卡精确已拼/已抢>10时继续待观察。首见证据仍保留。',
    '展示数按原标签/单位保留，不代表已核实订单；同标题、图片和商品ID不用于合并队列。',
    '缺少自有销售、成本、供货和素材许可证据，不能计算实际利润。',
]


def _rows(queries, key):
    value = queries.get(key, {})
    result = value.get('rows', []) if isinstance(value, dict) else value
    if not isinstance(result, list):
        raise ValueError(f'{key} rows must be a list')
    return result


def _id(value):
    if type(value) not in (int, str) or value == '' or (type(value) is int and value <= 0):
        raise ValueError('Expected a nonempty original row ID')
    return value


def _unique(rows, field):
    result = {}
    for row in rows:
        key = _id(row.get(field))
        if key in result:
            raise ValueError(f'Duplicate {field}')
        result[key] = row
    return result


def _time(value):
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if parsed.tzinfo is None:
            raise ValueError('Timezone required')
        return parsed.timestamp()
    except (ValueError, TypeError, AttributeError, OverflowError) as exc:
        raise ValueError('Invalid offset-aware observation time') from exc


def _window(run):
    a, b = _time(run.get('observed_from')), _time(run.get('observed_to'))
    if b < a:
        raise ValueError('Reversed observation window')
    return a, b


def _exact(row):
    return (row.get('sales_precision') == 'exact_display' and type(row.get('sales_value')) is int
            and row['sales_value'] >= 0 and bool(row.get('sales_label')) and bool(row.get('sales_unit'))
            and isinstance(row.get('sales_raw'), str) and bool(row['sales_raw'].strip()))


def _eligible(row):
    return _exact(row) and row['sales_label'] in SALES_METRIC_LABELS and row['sales_unit'] == '件' and row['sales_value'] > 10


def _goods(row):
    value = row.get('goods_id')
    return value if isinstance(value, str) and re.fullmatch(r'[1-9][0-9]*', value) else None


def _reliable(row, counts):
    goods = _goods(row)
    if not goods or row.get('identity_status') != 'unique_goods_id' or counts[(row['run_id'], goods)] != 1:
        return False
    # A visible URL disagreement cannot be hidden by a stale identity flag.
    try:
        url_ids = parse_qs(urlsplit(row.get('goods_url') or '').query).get('goods_id', [])
    except ValueError:
        return False
    return not url_ids or (len(url_ids) == 1 and url_ids[0] == goods)


def _bounds(row, run):
    a, b = _window(run)
    value = row.get('observed_at')
    if value is None:
        return None if row.get('observed_at_epoch') is not None else (a, b)
    point = _time(value)
    recorded = row.get('observed_at_epoch')
    if not a <= point <= b or recorded is not None and (type(recorded) not in (int, float)
            or not math.isfinite(recorded) or abs(point - recorded) > .001):
        return None
    precision = row.get('observed_at_precision') or 'legacy_unspecified'
    return (point, point) if precision in ('card_read', 'batch_read', 'legacy_unspecified') else (a, b)


def _growth(item, observations, runs, counts):
    """Validate existing paired evidence; do not manufacture a comparison."""
    if item.get('status') != 'comparable' or type(item.get('display_delta')) is not int or item['display_delta'] <= 0:
        return False
    old, new = observations.get(item.get('old_observation_id')), observations.get(item.get('new_observation_id'))
    if not old or not new:
        return False
    a, b = runs[old['run_id']], runs[new['run_id']]
    if (a['shop_id'] != b['shop_id'] or a['run_id'] != item.get('baseline_run_id')
            or b['run_id'] != item.get('target_run_id') or item.get('match_basis') != 'same_unique_goods_id'
            or not _reliable(old, counts) or not _reliable(new, counts) or _goods(old) != _goods(new)
            or not _exact(old) or not _exact(new) or not same_sales_metric(old, new)
            or old['sales_unit'] != new['sales_unit'] or new['sales_value'] - old['sales_value'] != item['display_delta']):
        return False
    for prefix, row in (('old', old), ('new', new)):
        for out, raw in (('value', 'sales_value'), ('label', 'sales_label'), ('unit', 'sales_unit')):
            if prefix + '_' + out in item and item[prefix + '_' + out] != row.get(raw):
                return False
    try:
        old_time, new_time = _bounds(old, a), _bounds(new, b)
        elapsed = item.get('elapsed_hours_min')
        return bool(old_time and new_time and _window(a)[1] <= _window(b)[0]
            and _window(a)[0] < _window(b)[0] and new_time[0] > old_time[1]
            and type(elapsed) in (int, float) and math.isfinite(elapsed) and elapsed > 0
            and abs(elapsed - (new_time[0] - old_time[1]) / 3600) < 1e-9)
    except ValueError:
        return False


def build_opportunity_queries(queries, project=None):
    """Derive two queries without reading/writing project, databases or network.

    Required inputs are runs, observations, competitor_shops and reference
    competitor_products. History/arrivals are optional evidence, never inferred.
    """
    if 'queries' in queries:
        queries = queries['queries']
    runs = _unique(_rows(queries, 'runs'), 'run_id')
    observations = _unique(_rows(queries, 'observations'), 'observation_id')
    shops = _unique(_rows(queries, 'competitor_shops'), 'shop_id')
    rows_by_run = defaultdict(list)
    for run in runs.values():
        if not run.get('shop_id'):
            raise ValueError('Run shop_id is required')
        _window(run)
    for row in observations.values():
        run = runs.get(row.get('run_id'))
        if not run or row.get('shop_id', run['shop_id']) != run['shop_id']:
            raise ValueError('Observation has unknown or conflicting run/shop')
        rows_by_run[row['run_id']].append(row)
    if set(r['shop_id'] for r in runs.values()) != set(shops):
        raise ValueError('Observed shops and competitor profiles must agree')
    for shop, profile in shops.items():
        for key in ('reference_run_id', 'latest_run_id'):
            if profile.get(key) not in runs or runs[profile[key]]['shop_id'] != shop:
                raise ValueError('Profile reference/latest run belongs to another shop')
        latest = max((r for r in runs.values() if r['shop_id'] == shop), key=lambda r: (*reversed(_window(r)), r['run_id']))
        if latest['run_id'] != profile['latest_run_id']:
            raise ValueError('Profile latest run is stale')
    counts = Counter((row['run_id'], _goods(row)) for row in observations.values() if _goods(row))
    selected, product_ids = {}, defaultdict(set)
    core = ('run_id','title','goods_id','goods_url','sales_raw','sales_value','sales_label','sales_unit',
            'sales_precision','price_raw','image_url','observed_at','asset_sha256')
    for product in _rows(queries, 'competitor_products'):
        row = observations.get(product.get('observation_id'))
        if not row:
            raise ValueError('Reference product lacks its original observation')
        shop = runs[row['run_id']]['shop_id']
        if product.get('shop_id') != shop or row['run_id'] != shops[shop]['reference_run_id']:
            raise ValueError('Reference product crosses its shop/reference run')
        if any(key in product and product[key] != row.get(key) for key in core):
            raise ValueError('Reference product disagrees with immutable original fields')
        selected[row['observation_id']] = row
        product_ids[shop].add(row['observation_id'])
    for shop, profile in shops.items():
        expected = {r['observation_id'] for r in rows_by_run[profile['reference_run_id']]}
        if product_ids[shop] != expected:
            raise ValueError('Reference products must preserve every reference-run card')
        for row in rows_by_run[profile['latest_run_id']]:
            selected[row['observation_id']] = row

    arrival_summaries = _unique(_rows(queries, 'new_arrival_summary'), 'shop_id')
    arrivals, rejected_arrivals = defaultdict(list), Counter()
    for item in _rows(queries, 'new_arrival_items'):
        anchor = observations.get(item.get('observation_id'))
        if not anchor or anchor['run_id'] != item.get('run_id'):
            raise ValueError('Arrival anchor is not its original observation')
        shop = runs[anchor['run_id']]['shop_id']
        if item.get('shop_id', shop) != shop:
            raise ValueError('Arrival anchor crosses shops')
        summary = arrival_summaries.get(shop, {})
        valid = bool(item.get('arrival_item_id') and item.get('tracking_id') == summary.get('tracking_id')
            and summary.get('tracking_id') and item.get('discovery_kind') in ('first_observed_id_candidate','new_card_clue')
            and anchor['run_id'] not in summary.get('baseline_run_ids', []))
        try:
            valid = valid and _window(runs[anchor['run_id']])[0] >= _time(summary.get('started_at'))
        except ValueError:
            valid = False
        if not valid:
            rejected_arrivals[shop] += 1
            continue
        refs = {anchor['observation_id']}
        refs.update(item.get('latest_observation_ids', []))
        refs.update(item.get('first_observation_id_candidates', []))
        refs.update(r.get('observation_id') for r in item.get('subsequent_references', []))
        for observation_id in refs:
            row = observations.get(observation_id)
            if not row or runs[row['run_id']]['shop_id'] != shop:
                raise ValueError('Arrival reference crosses shops or is missing')
            if row['run_id'] in summary.get('baseline_run_ids', []) or _window(runs[row['run_id']])[0] < _time(summary['started_at']):
                rejected_arrivals[shop] += 1
                continue
            if observation_id in selected:
                arrivals[observation_id].append(item)

    growth, rejected_growth = defaultdict(list), Counter()
    for item in _rows(queries, 'comparison_items'):
        target = observations.get(item.get('new_observation_id'))
        if not target or target['observation_id'] not in selected:
            continue
        if _growth(item, observations, runs, counts):
            growth[target['observation_id']].append(item)
        elif item.get('status') == 'comparable' and type(item.get('display_delta')) is int and item['display_delta'] > 0:
            rejected_growth[runs[target['run_id']]['shop_id']] += 1

    artists = defaultdict(dict)
    for artist in _rows(queries, 'artist_watchlist'):
        if artist.get('entity_kind') != 'artist':
            continue
        if not artist.get('artist_id') or not artist.get('artist_name'):
            raise ValueError('Artist directory row requires its original ID and name')
        for run_key, ids_key in (('run_id', 'observation_ids'), ('latest_run_id', 'latest_observation_ids')):
            for oid in artist.get(ids_key, []):
                row = observations.get(oid)
                if not row or row['run_id'] != artist.get(run_key) or runs[row['run_id']]['shop_id'] != artist.get('shop_id'):
                    raise ValueError('Artist directory reference crosses shop/run or lacks an original card')
                if oid in selected:
                    artists[oid][artist['artist_id']] = artist

    output = []
    for oid, row in selected.items():
        run, new_items, pairs = runs[row['run_id']], arrivals[oid], growth[oid]
        shop = run['shop_id']; profile = shops[shop]
        reference, latest = row['run_id'] == profile['reference_run_id'], row['run_id'] == profile['latest_run_id']
        role = 'reference_and_latest' if reference and latest else 'reference' if reference else 'latest'
        eligible = _eligible(row)
        expansion_possible = any(i.get('coverage_expansion_possible') for i in new_items)
        priority_arrivals = [i for i in new_items if not i.get('coverage_expansion_possible')
            and (i.get('discovery_kind') == 'first_observed_id_candidate' or eligible)]
        new_priority_eligible = bool(priority_arrivals) and not expansion_possible
        tier = 'new_candidate' if new_priority_eligible else 'comparable_growth' if pairs else 'eligible_reference' if eligible else 'watch'
        reasons, blockers = [], ['自有成交、退款、供货及履约成本尚未提供，不能判定利润', '素材商品化许可与可供规格尚待核验']
        if new_items:
            reasons.append('已有固定起点后的首次发现证据；只作候选，不是实际上架确认')
            if expansion_possible:
                blockers.append('固定起点只有部分采集，本次首见可能只是补齐此前未采到的旧存量，不能当作新增上架')
                reasons.append('补扫首见仅保留证据，不触发新品优先；按可比增长、单卡达标或待观察互斥归档')
            elif not new_priority_eligible:
                reasons.append('缺ID弱首见且未单卡精确已拼/已抢>10，不触发新品优先；保留线索继续观察')
            if any(i.get('discovery_kind') == 'new_card_clue' for i in new_items):
                blockers.append('新标题与原图组合只是弱线索，不确认唯一商品')
        if pairs:
            reasons.append(f'已有同店可靠唯一ID、同销量指标和单位的可比正增长：{row["sales_label"]}（{row["sales_unit"]}），原配对逐项保留')
        if eligible:
            reasons.append('本张原卡精确已拼>10件；只是该观察窗口的历史展示证据')
        elif not _exact(row):
            blockers.append('销量缺失或非精确整数，保持原文和未知，不补零')
        elif row['sales_label'] not in SALES_METRIC_LABELS or row['sales_unit'] != '件':
            blockers.append('该销量标签或单位不是已拼件，不进入已拼>10主清单')
        else:
            reasons.append('本张原卡未超过严格已拼>10件门槛，不能与同标题卡累加')
        if not _reliable(row, counts):
            blockers.append('缺少无冲突且本轮唯一的可靠商品ID，不能确认跨轮商品身份')
        if not row.get('asset_sha256') or row.get('image_content_status') != 'verified_local':
            blockers.append('本地图像缺失或未完成内容校验；保留卡片，不自动重试')
        if run.get('status') != 'complete' or not run.get('end_boundary_observed'):
            blockers.append('当前原卡来自部分观察，不能据此推断全店或下架')
        if role == 'reference':
            blockers.append('这是较早参考轮原卡，不是最新轮重新观察的证据')
        if not new_items and not pairs and not eligible and not reasons:
            reasons.append('现有证据仅支持继续观察和核验')
        evidence = {oid}
        evidence.update(i['observation_id'] for i in new_items)
        evidence.update(p['old_observation_id'] for p in pairs)
        value = {key: deepcopy(row.get(key)) for key in ('run_id','observation_id','view_order','title','goods_id','goods_url',
            'identity_status','sales_raw','sales_label','sales_unit','sales_value','sales_precision','price_raw',
            'asset_sha256','image_url','image_status','image_content_status','observed_at')}
        value.update(opportunity_id='observation:' + str(oid), shop_id=shop, priority_key=tier,
            priority_order=TIERS[tier][0], priority_label=TIERS[tier][1], reasons=reasons, blockers=blockers,
            next_action='先核验候选身份、关联题材与素材许可，再录入自身供货和测试成本' if tier=='new_candidate' else
                '查看前后原卡与时间范围，核验同规格，再准备单变量小量测试记录' if tier=='comparable_growth' else
                '把原卡作为历史需求线索，核对当前供货与可用素材，记录自有曝光、点击、支付、退款和成本' if tier=='eligible_reference' else
                '保留原卡，补充身份、销量和供货证据后再决定是否试品',
            source_role=role, is_new_candidate=bool(new_items), is_comparable_growth=bool(pairs), eligible_gt10=eligible,
            new_candidate_priority_eligible=new_priority_eligible, coverage_expansion_possible=expansion_possible,
            sales_display=row.get('sales_raw'), source_url=row.get('goods_url') or run.get('source_url'),
            source_url_role='goods' if row.get('goods_url') else 'shop',
            observed_at_precision=row.get('observed_at_precision') or ('legacy_unspecified' if row.get('observed_at') else 'unknown'),
            observation_window={'from':run['observed_from'],'to':run['observed_to']},
            run_status=run.get('status'), end_boundary_observed=bool(run.get('end_boundary_observed')),
            new_arrival_item_ids=sorted({i['arrival_item_id'] for i in new_items}),
            comparison_item_ids=sorted({p['comparison_item_id'] for p in pairs}),
            evidence_observation_ids=sorted(evidence, key=str),
            artist_names=sorted({a['artist_name'] for a in artists[oid].values()}),
            artist_watchlist_ids=sorted(artists[oid]),
            artist_public_links=[dict(deepcopy(source), artist_id=a['artist_id'], artist_name=a['artist_name'])
                for a in sorted(artists[oid].values(), key=lambda a:a['artist_id']) for source in a.get('sources', [])],
            artist_topic_note='仅关联已审目录中的标题人物线索与公开入口；不确认原卡人物、当前活动或商品化许可，不影响优先级。',
            growth_evidence=[{key:p.get(key) for key in ('comparison_item_id','comparison_id','old_observation_id',
                'new_observation_id','old_sales_raw','new_sales_raw','old_label','new_label','old_unit','new_unit',
                'display_delta','elapsed_hours_min','elapsed_hours_max')} for p in pairs],
            profit_status='unknown_no_own_sales_or_costs', rule_version=2)
        output.append(value)
    output.sort(key=lambda r:(r['shop_id'],r['priority_order'],
        -r['sales_value'] if r['priority_key']=='eligible_reference' else 0,
        -_window(runs[r['run_id']])[1],r.get('view_order') or 0,str(r['observation_id'])))

    summaries=[]
    for shop, profile in sorted(shops.items()):
        selected_rows=[r for r in output if r['shop_id']==shop]
        tiers=Counter(r['priority_key'] for r in selected_rows)
        tracking=arrival_summaries.get(shop,{})
        gaps=['优先级仅用于核验顺序；没有实际利润数据']
        if tracking.get('state')=='no_post_baseline_run':gaps.append('固定起点后尚无采集，等待下一轮；不能解释为无新品')
        elif not tracking.get('tracking_id'):gaps.append('尚未配置本店固定新品起点')
        if tracking.get('baseline_coverage_status')=='partial_only':gaps.append(tracking.get('baseline_coverage_note') or '固定起点未覆盖全店；首见可能来自补扫旧存量，不确认新增上架')
        if not profile.get('latest_is_complete'):gaps.append('最新轮为部分观察，参考轮与最新轮计数分开')
        if rejected_arrivals[shop] or rejected_growth[shop]:gaps.append('部分派生证据未通过范围或比较复核，未升级优先级')
        summaries.append({'shop_id':shop,'shop_name':profile.get('shop_name'),'reference_run_id':profile['reference_run_id'],
            'latest_run_id':profile['latest_run_id'],'opportunity_count':len(selected_rows),
            **{key+'_count':tiers[key] for key in TIERS},
            'arrival_evidence_count':sum(r['is_new_candidate'] for r in selected_rows),
            'coverage_expansion_evidence_count':sum(r['coverage_expansion_possible'] for r in selected_rows),
            'arrival_priority_suppressed_count':sum(r['is_new_candidate'] and not r['new_candidate_priority_eligible'] for r in selected_rows),
            'eligible_gt10_evidence_count':sum(r['eligible_gt10'] for r in selected_rows),
            'reference_card_count':len(product_ids[shop]),'latest_card_count':len(rows_by_run[profile['latest_run_id']]),
            'unknown_sales_count':sum(not _exact(observations[r['observation_id']]) for r in selected_rows),
            'missing_identity_count':sum(not _reliable(observations[r['observation_id']],counts) for r in selected_rows),
            'missing_image_count':sum(not r['asset_sha256'] or r['image_content_status']!='verified_local' for r in selected_rows),
            'rejected_growth_evidence_count':rejected_growth[shop],'rejected_arrival_evidence_count':rejected_arrivals[shop],
            'arrival_state':tracking.get('state','not_configured'),'coverage_gaps':gaps,'profit_status':'unknown_no_own_sales_or_costs',
            'counting_rule':'four mutually exclusive follow-up tiers; queue counts original observations, not unique products; arrival/coverage/eligible evidence counts overlap tiers and must not be added to them',
            'evidence_counting_note':'四档互斥，合计等于原卡队列；首见、扩覆盖和达标证据为可重叠标记，不另加到四档总量。补扫首见不触发新品优先。',
            'observation_ids':[r['observation_id'] for r in selected_rows]})
    files, tables = set(), set()
    for key in UPSTREAM:
        src=queries.get(key,{}).get('source',{}) if isinstance(queries.get(key,{}),dict) else {}
        files.update(src.get('files',[]));tables.update(src.get('tables',[]))
    files.add(str(Path(__file__).resolve()))
    source={'label':'原卡证据驱动的试品前机会队列','provider':'Local reviewed queries','classification':'derived',
        'upstreamQueryIds':list(UPSTREAM),'files':sorted(files),'tables':sorted(tables),
        'executedAt':datetime.now(timezone.utc).isoformat(),'grain':'one immutable observation card; observation_id',
        'filters':['Each shop reference products union latest-run original observations; deduplicate observation_id only.'],
        'caveats':LIMITATIONS[:],'derivation':'pdd_monitor.opportunities.build_opportunity_queries(queries, project=None); no I/O',
        'sourceQueryRowIds':{'observations':sorted({i for row in output for i in row['evidence_observation_ids']},key=str),
            'comparison_items':sorted({i for row in output for i in row['comparison_item_ids']}),
            'new_arrival_items':sorted({i for row in output for i in row['new_arrival_item_ids']}),
            'artist_watchlist':sorted({i for row in output for i in row['artist_watchlist_ids']})},
        'metricDefinitions':[{'id':'priority_order','label':'跟进优先级','description':'1非扩覆盖的起点后候选（弱身份还须单卡精确已拼/已抢>10）；2经复核既有可比正增长；3原卡精确已拼>10，按该同销量指标和单位精确值降序、同轮同值按原位置；4待观察。互斥，不是利润评分。首见证据可跨档保留，不能重复相加。'}]}
    methods=[{'language':'python','code':'# Immutable source cards; validate existing arrival/comparison evidence.\n# Select one exclusive evidence tier per observation. Never aggregate sales or infer profit.'}]
    return {QUERY_IDS[0]:{'rows':output,'source':source,'methods':methods},
            QUERY_IDS[1]:{'rows':summaries,'source':dict(deepcopy(source),grain='one shop; mutually exclusive observation counts'), 'methods':deepcopy(methods)}}
