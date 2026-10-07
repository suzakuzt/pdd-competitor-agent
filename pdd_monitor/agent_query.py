"""Bounded read-only tools over the dashboard's reviewed snapshot (standard library).

No SQL, network calls, browser operations, or persistence. Each result is an
independent original observation; search never merges cards or sums sales.
"""
from __future__ import annotations

from .sales_metrics import SALES_METRIC_LABELS, same_sales_metric

from copy import deepcopy
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
import re
from urllib.parse import urlsplit


MAX_INTEGER = 2**53 - 1
LIMITATIONS = [
    '结果是一条条原始卡片观察，不是唯一商品或SKU；同标题、图片不合并，销量不相加。',
    '已拼/已抢件按同一指标筛选比较，原标签独立保留；总售等独立参考；缺失和模糊销量未知，不补零。',
    '销量范围与数值排序仅使用精确已拼/已抢件；其他口径和未知值不混排数值。',
    '前台展示数不是核实订单；展示价不是采购成本、同规格到手价或利润。',
    '部分采集不能代表全店；首次观察候选不是实际上架或已确认新品。',
]
RUN_FIELDS = (
    "sort_order",'run_id','shop_id','shop_name','status','end_boundary_observed',
    'observed_from','observed_to','snapshot_sha256','source_url','scope',
    'collection_stop_reason','collection_timestamp_precision')
CARD_FIELDS = ('observation_id','run_id','view_order','title','goods_id','goods_url',
    'identity_status','identity_evidence','price_raw','sales_raw','sales_label',
    'sales_value','sales_unit','sales_precision','observed_at','observed_at_precision',
    'last_dom_read_at','asset_sha256','image_url','image_status','image_content_status',
    'previous_attempt_blocked','previous_attempt_reason')
SOURCE_FIELDS = ('label','url','source_kind','verification','basis','date_checked',
    'purpose','verified_entry','evidence_url')
ARRIVAL_FIELDS = ('arrival_item_id','tracking_id','run_id','observation_id','view_order',
    'title','goods_id','identity_status','discovery_kind','discovery_label',
    'first_observed_at','first_observed_at_precision','first_observation_window',
    'first_seen_time_basis','possible_since','possible_until','possible_since_reason',
    'last_complete_absence_run_id','previous_absence_observation_window','time_range_label',
    'possible_change_reasons','verification_gaps','newness','latest_run_id',
    'latest_reference_basis','first_time_order_status','latest_time_order_status',
    'first_observation_id_candidates','latest_observation_ids')
ARRIVAL_SUMMARY_FIELDS = ('shop_id','tracking_id','started_at','timezone','state',
    'baseline_run_ids','baseline_observation_count','baseline_latest_observed_to',
    'post_baseline_run_count','post_baseline_observation_count','post_baseline_run_ids',
    'item_count','first_observed_id_candidate_count','new_card_clue_count',
    'post_observation_classification_counts','limitations')
TREND_FIELDS = ('signal_id','model_version','signal_code','label','level','action','reasons',
    'shop_id','run_id','observation_id','anchor_run_id','anchor_observation_id','date',
    'included_in_watch','rank','selected_for_review','review_rank','yipin_value',
    'latest_delta','rate24','rate_status','interval_hours','interval_hours_lower','interval_hours_upper',
    'avg_recent','avg_base','ratio','positive_recent_days','consecutive_days','normalized_delta_days',
    'recent_delta_days','baseline_delta_days','recent_interval_hours','baseline_interval_hours',
    'ewma_recent','ewma_base','ewma_momentum','ewma_method','rate_average_method',
    'basis','identity_confirmed','same_sku_verified','forecast_eligible','forecast_status',
    'readiness_stage','evidence_observation_ids','evidence_run_ids','evidence_dates')
TREND_SUMMARY_FIELDS = ('shop_id','shop_name','date','reference_run_id','model_version',
    'total_cards','watch_card_count','full_snapshot_days','priority_count','watch_count',
    'selected_review_count','data_gap_count','model_ready','model_status')
TREND_LEVELS = {'priority':0,'watch':1,'observe':2,'data_gap':3,'excluded':4}
TREND_LIMITATIONS = [
    '趋势使用当前店完整参考轮及可比的相邻日展示变化，不按累计已拼量代替增长。',
    '同标题同原图配对仅为展示线索，商品身份待核对；单日增长不代表持续趋势。',
    '仅展示已保存且完整性已验证的图片；缺图观察仍保留在快照中，不进入本清单。',
    '规则排序不是预测概率；未验证跟品命中率，成本未知时不能判断利润。',
]


def _finite(value):
    return type(value) in (int,float) and math.isfinite(value)


def _integer(value, name, minimum=0, maximum=MAX_INTEGER):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f'{name} must be an integer from {minimum} to {maximum}; bool is not accepted')
    return value


def _text(value, name):
    if not isinstance(value, str) or not value.strip() or len(value) > 256:
        raise ValueError(f'{name} must be a nonempty string up to 256 characters')
    return value


def _pick(row, fields):
    result = {key: deepcopy(row[key]) for key in fields if key in row}
    # Fail closed on malformed non-JSON numbers in a supplied in-memory snapshot.
    json.dumps(result, allow_nan=False)
    return result


def _time(value):
    if not isinstance(value, str):
        raise ValueError('Reviewed run requires an offset-aware observation window')
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError as exc:
        raise ValueError('Invalid reviewed observation window') from exc
    if parsed.tzinfo is None:
        raise ValueError('Reviewed observation window requires a timezone')
    return parsed.timestamp()


def _http_url(value):
    if not isinstance(value, str):
        return None
    try:
        parsed = urlsplit(value)
        return value if parsed.scheme in ('http','https') and parsed.netloc and not parsed.username and not parsed.password else None
    except ValueError:
        return None


def _exact_yipin(row):
    return (row.get('sales_precision') == 'exact_display' and row.get('sales_label') in SALES_METRIC_LABELS
            and row.get('sales_unit') == '件' and type(row.get('sales_value')) is int
            and 0 <= row['sales_value'] <= MAX_INTEGER)


class AgentQuery:
    """Immutable copied indexes; instantiate again after a newly built snapshot.

    search boundaries are inclusive (sales_min=11 means strictly >10). The
    caller must pass an explicit known shop_id to every card-bearing tool.
    """
    def __init__(self, snapshot, *, source_sha256=None):
        if not isinstance(snapshot, dict) or not isinstance(snapshot.get('queries'), dict):
            raise ValueError('Expected reviewed dashboard snapshot with queries')
        self._snapshot = deepcopy({'id':snapshot.get('id'),'generated_at':snapshot.get('generatedAt'),
                          'sha256':source_sha256})
        self._queries = snapshot['queries']
        self._runs, self._cards, self._shops, self._assets = {}, {}, {}, {}
        self._artists, self._arrivals, self._arrival_summaries = {}, {}, {}
        self._trends, self._trend_summaries = {}, {}
        for raw in self._rows('runs'):
            run_id = _text(raw.get('run_id'), 'run_id')
            shop_id = _text(raw.get('shop_id'), 'shop_id')
            if run_id in self._runs:
                raise ValueError('Duplicate reviewed run_id')
            start, end = _time(raw.get('observed_from')), _time(raw.get('observed_to'))
            if end < start:
                raise ValueError('Reversed reviewed run window')
            run = _pick(raw, RUN_FIELDS)
            run['_end_epoch'] = end
            self._runs[run_id] = run
            self._shops.setdefault(shop_id, {'shop_id':shop_id,'shop_name':raw.get('shop_name') or shop_id})
        for raw in self._rows('observations'):
            oid = _integer(raw.get('observation_id'), 'observation_id', 1)
            if oid in self._cards or raw.get('run_id') not in self._runs:
                raise ValueError('Duplicate observation_id or missing reviewed run')
            _integer(raw.get('view_order'), 'view_order', 1)
            card = _pick(raw, CARD_FIELDS)
            card['shop_id'] = self._runs[card['run_id']]['shop_id']
            if raw.get('shop_id', card['shop_id']) != card['shop_id']:
                raise ValueError('Reviewed observation shop/run conflict')
            original = raw.get('original') if isinstance(raw.get('original'), dict) else {}
            card['raw_text'] = original.get('cardText') or original.get('rawText') or raw.get('title')
            self._cards[oid] = card
        for raw in self._rows('image_assets', optional=True):
            sha = raw.get('sha256')
            if isinstance(sha, str) and re.fullmatch(r'[a-f0-9]{64}', sha):
                self._assets[sha] = _pick(raw, ('sha256','mime','byte_count','integrity_status'))
        profiles = {}
        for profile in self._rows('competitor_shops', optional=True):
            shop_id = profile.get('shop_id')
            if shop_id not in self._shops or shop_id in profiles:
                raise ValueError('Invalid or duplicate reviewed shop profile')
            for field in ('reference_run_id','latest_run_id'):
                self._check_run(profile.get(field), shop_id)
            profiles[shop_id] = profile
        for shop_id, shop in self._shops.items():
            runs = sorted((r for r in self._runs.values() if r['shop_id']==shop_id), key=lambda r:(-r['_end_epoch'],r['run_id']))
            latest = runs[0]
            reference = next((r for r in runs if r.get('status')=='complete' and r.get('end_boundary_observed') is True), latest)
            profile = profiles.get(shop_id)
            # Source profile and source runs must agree; do not silently pick another store/run.
            if profile and (profile['reference_run_id'] != reference['run_id'] or profile['latest_run_id'] != latest['run_id']):
                raise ValueError('Reviewed profile reference/latest run is inconsistent')
            shop.update(reference_run_id=reference['run_id'], latest_run_id=latest['run_id'],
                reference_selection='latest_complete_with_boundary' if reference.get('status')=='complete' and reference.get('end_boundary_observed') is True else 'latest_partial_fallback')
        for artist in self._rows('artist_watchlist', optional=True):
            shop_id = self._shop(artist.get('shop_id'))['shop_id']
            entry = _pick(artist, ('artist_id','artist_name','entity_kind','identity_note','source_status','research_checked_at'))
            entry['sources'] = []
            for raw in artist.get('sources', []):
                source = _pick(raw, SOURCE_FIELDS)
                if _http_url(source.get('url')):
                    if 'evidence_url' in source:
                        source['evidence_url'] = _http_url(source['evidence_url'])
                    entry['sources'].append(source)
            for run_key, ids_key in (('run_id','observation_ids'),('latest_run_id','latest_observation_ids')):
                ids = artist.get(ids_key, [])
                if not isinstance(ids, list):
                    raise ValueError('Invalid artist observation references')
                for oid in ids:
                    card = self._check_card(oid, shop_id)
                    if card['run_id'] != artist.get(run_key):
                        raise ValueError('Artist reference crosses reviewed run')
                    self._artists.setdefault(oid, {})[entry.get('artist_id')] = deepcopy(entry)
        for summary in self._rows('new_arrival_summary', optional=True):
            shop_id = self._shop(summary.get('shop_id'))['shop_id']
            if shop_id in self._arrival_summaries:
                raise ValueError('Duplicate arrival summary')
            for run_id in summary.get('baseline_run_ids', []) + summary.get('post_baseline_run_ids', []):
                self._check_run(run_id, shop_id)
            self._arrival_summaries[shop_id] = _pick(summary, ARRIVAL_SUMMARY_FIELDS)
        seen_arrivals = set()
        for raw in self._rows('new_arrival_items', optional=True):
            shop_id = self._shop(raw.get('shop_id'))['shop_id']
            card = self._check_card(raw.get('observation_id'), shop_id)
            if card['run_id'] != raw.get('run_id'):
                raise ValueError('Arrival anchor crosses reviewed run')
            arrival_id = _text(raw.get('arrival_item_id'), 'arrival_item_id')
            if arrival_id in seen_arrivals:
                raise ValueError('Duplicate arrival_item_id')
            seen_arrivals.add(arrival_id)
            item = _pick(raw, ARRIVAL_FIELDS)
            item['shop_id'] = shop_id
            for field in ('latest_run_id','last_complete_absence_run_id'):
                if item.get(field) is not None:
                    self._check_run(item[field], shop_id)
            for field in ('latest_observation_ids','first_observation_id_candidates'):
                if not isinstance(item.get(field, []), list):
                    raise ValueError('Invalid arrival observation references')
                for oid in item.get(field, []):
                    self._check_card(oid, shop_id)
            for field in ('historical_references','subsequent_references'):
                item[field] = []
                for raw_ref in raw.get(field, []):
                    referenced = self._check_card(raw_ref.get('observation_id'), shop_id)
                    if referenced['run_id'] != raw_ref.get('run_id'):
                        raise ValueError('Arrival reference crosses reviewed run')
                    item[field].append(_pick(raw_ref, ('run_id','observation_id','match_basis','temporal_order')))
            self._arrivals.setdefault(shop_id, []).append(item)
        for raw in self._rows('trend_signals', optional=True):
            self._index_trend(raw)
        for raw in self._rows('trend_signal_summary', optional=True):
            shop_id = self._shop(raw.get('shop_id'))['shop_id']
            if (shop_id in self._trend_summaries or raw.get('reference_run_id') != self._shops[shop_id]['reference_run_id']):
                raise ValueError('Trend summary does not match current shop reference')
            self._trend_summaries[shop_id] = _pick(raw, TREND_SUMMARY_FIELDS)
        # Do not retain the original snapshot containing full image payloads.
        del self._queries

    def _index_trend(self, raw):
        shop = self._shop(raw.get('shop_id'))
        card = self._check_card(raw.get('observation_id'), shop['shop_id'])
        if (raw.get('run_id') != card['run_id'] or raw.get('anchor_run_id') != card['run_id']
                or raw.get('anchor_observation_id') != card['observation_id']
                or card['run_id'] != shop['reference_run_id']):
            raise ValueError('Trend anchor does not match current shop reference')
        rows = self._trends.setdefault(shop['shop_id'], [])
        if any(row['observation_id'] == card['observation_id'] or row['rank'] == raw.get('rank') for row in rows):
            raise ValueError('Duplicate trend anchor or rank')
        _integer(raw.get('rank'), 'trend rank', 1)
        if (raw.get('level') not in TREND_LEVELS or type(raw.get('included_in_watch')) is not bool
                or type(raw.get('selected_for_review')) is not bool
                or raw.get('basis') not in ('confirmed_goods_id','provisional_title_image','single_card_only')):
            raise ValueError('Invalid reviewed trend classification')
        exact_value = card.get('sales_value') if _exact_yipin(card) else None
        if (raw.get('yipin_value') != exact_value or raw['included_in_watch'] != (exact_value is not None and exact_value > 0)):
            raise ValueError('Trend watch eligibility differs from original card')
        item = _pick(raw, TREND_FIELDS)
        for key in ('latest_delta','rate24','interval_hours','interval_hours_lower','interval_hours_upper',
                    'avg_recent','avg_base','ratio','ewma_recent','ewma_base','ewma_momentum'):
            if item.get(key) is not None and not _finite(item[key]):
                raise ValueError('Invalid reviewed trend number')
        for field in ('evidence_observation_ids','evidence_run_ids','evidence_dates','reasons'):
            if type(item.get(field)) is not list:
                raise ValueError('Invalid reviewed trend evidence')
        evidence_runs = item['evidence_run_ids']
        evidence_ids = item['evidence_observation_ids']
        if (any(type(oid) is not int for oid in evidence_ids) or len(set(evidence_ids)) != len(evidence_ids)
                or len(item['evidence_dates']) != len(evidence_ids)
                or any(type(run_id) is not str for run_id in evidence_runs)
                or len(set(evidence_runs)) != len(evidence_runs)):
            raise ValueError('Duplicate or malformed trend evidence')
        for run_id in evidence_runs:
            self._check_run(run_id, shop['shop_id'])
        referenced_runs = set()
        for oid in evidence_ids:
            evidence = self._check_card(oid, shop['shop_id'])
            referenced_runs.add(evidence['run_id'])
            if (evidence['run_id'] not in evidence_runs or
                    self._runs[evidence['run_id']]['_end_epoch'] > self._runs[card['run_id']]['_end_epoch']):
                raise ValueError('Trend evidence crosses its reference cutoff')
        if referenced_runs != set(evidence_runs):
            raise ValueError('Trend evidence run/card references differ')
        if item.get('latest_delta') is not None and item['latest_delta'] > 0:
            if (len(evidence_ids) < 2 or len(referenced_runs) < 2 or evidence_ids[-1] != card['observation_id']
                    or item['basis'] == 'single_card_only' or exact_value is None or item['latest_delta'] > exact_value):
                raise ValueError('Trend growth lacks comparable original evidence')
        rows.append(item)

    @classmethod
    def from_path(cls, path):
        data = Path(path).read_bytes()
        if len(data) > 256 * 1024 * 1024:
            raise ValueError('Reviewed snapshot exceeds 256 MiB')
        def invalid_constant(value):
            raise ValueError(f'Nonfinite JSON constant: {value}')
        return cls(json.loads(data.decode('utf-8-sig'), parse_constant=invalid_constant), source_sha256=hashlib.sha256(data).hexdigest())

    def _rows(self, name, optional=False):
        query = self._queries.get(name)
        if query is None and optional:
            return []
        if not isinstance(query, dict) or not isinstance(query.get('rows'), list) or not all(isinstance(row, dict) for row in query['rows']):
            raise ValueError(f'Invalid reviewed query: {name}')
        return query['rows']

    def _shop(self, shop_id):
        _text(shop_id, 'shop_id')
        if shop_id not in self._shops:
            raise ValueError('Unknown shop_id; choose an observed shop from context')
        return self._shops[shop_id]

    def _check_run(self, run_id, shop_id):
        if not isinstance(run_id, str) or run_id not in self._runs or self._runs[run_id]['shop_id'] != shop_id:
            raise ValueError('Run does not belong to the requested shop')
        return self._runs[run_id]

    def _check_card(self, observation_id, shop_id):
        _integer(observation_id, 'observation_id', 1)
        if observation_id not in self._cards or self._cards[observation_id]['shop_id'] != shop_id:
            raise ValueError('Observation is unavailable in the requested shop')
        return self._cards[observation_id]

    def _run(self, run_id):
        run = self._runs[run_id]
        result = {key:deepcopy(value) for key,value in run.items() if not key.startswith('_')}
        result['card_count'] = sum(row['run_id']==run_id for row in self._cards.values())
        return result

    def _image_state(self, row):
        asset = self._assets.get(row.get('asset_sha256'))
        if row.get('image_content_status')=='verified_local' and asset and asset.get('integrity_status')=='verified_local':
            return 'cached'
        if row.get('previous_attempt_blocked') or re.search(r'blocked|failed|invalid', str(row.get('image_status') or ''), re.I) or row.get('image_content_status')=='unavailable_or_invalid':
            return 'failed_or_blocked'
        return 'pending'

    def _card(self, row, detail=False):
        result = deepcopy(row)
        if not detail:
            result.pop('raw_text', None)
        run = self._runs[row['run_id']]
        result.update(evidence_id=f'observation:{row["observation_id"]}',
            observation_window={'from':run['observed_from'],'to':run['observed_to']},
            run_status=run.get('status'),end_boundary_observed=run.get('end_boundary_observed') is True,
            snapshot_sha256=run.get('snapshot_sha256'),eligible_gt10=_exact_yipin(row) and row['sales_value']>10,
            image_state=self._image_state(row),artist_sources=list(deepcopy(self._artists.get(row['observation_id'], {})).values()))
        result['thumbnail_ref'] = {'query_id':'image_assets','asset_sha256':row['asset_sha256'],'observation_id':row['observation_id']} if result['image_state']=='cached' else None
        result['goods_url'] = _http_url(result.get('goods_url'))
        result['image_url'] = _http_url(result.get('image_url'))
        result['limitations'] = LIMITATIONS[:]
        return result

    def _envelope(self, tool, shop_id=None):
        return {'status':'ok','tool':tool,'shop_id':shop_id,'snapshot':deepcopy(self._snapshot),'limitations':LIMITATIONS[:]}

    def context(self, shop_id=None):
        selected = [self._shop(shop_id)] if shop_id is not None else list(self._shops.values())
        shops = []
        for shop in selected:
            item = deepcopy(shop)
            item['reference'] = self._run(shop['reference_run_id'])
            item['latest'] = self._run(shop['latest_run_id'])
            item['historical_run_count'] = sum(run['shop_id']==shop['shop_id'] for run in self._runs.values())
            item['historical_observation_count'] = sum(row['shop_id']==shop['shop_id'] for row in self._cards.values())
            item['arrival_state'] = self._arrival_summaries.get(shop['shop_id'], {}).get('state','not_configured')
            shops.append(item)
        return {**self._envelope('context',shop_id),'shops':shops,'available_scopes':['reference','latest','all'],
                'sales_range_basis':'含上下界的精确已拼/已抢件；严格大于10请用sales_min=11','max_page_size':20}

    def search(self, shop_id, terms=None, *, scope='reference', sales_min=None, sales_max=None,
               sort='sales_desc', image_status='all', artists_only=False, limit=20, offset=0):
        shop = self._shop(shop_id)
        _integer(limit,'limit',1,20); _integer(offset,'offset',0,1_000_000)
        if type(scope) is not str or scope not in ('reference','latest','all'):
            raise ValueError('scope must be reference, latest, or all')
        if type(sort) is not str or sort not in ('sales_desc','position'):
            raise ValueError('sort must be sales_desc or position')
        if type(image_status) is not str or image_status not in ('all','cached','pending','failed_or_blocked'):
            raise ValueError('Invalid image_status')
        if type(artists_only) is not bool:
            raise ValueError('artists_only must be a boolean')
        terms = [] if terms is None else terms
        if type(terms) is not list or len(terms)>8 or any(type(term) is not str or not term.strip() or len(term)>120 for term in terms):
            raise ValueError('terms must be a list of at most 8 nonempty strings, each up to 120 characters')
        terms = [term.strip() for term in terms]
        for value,name in ((sales_min,'sales_min'),(sales_max,'sales_max')):
            if value is not None:
                _integer(value,name)
        if sales_min is not None and sales_max is not None and sales_min>sales_max:
            raise ValueError('sales_min must not exceed sales_max')
        selected_runs = {r['run_id'] for r in self._runs.values() if r['shop_id']==shop_id} if scope=='all' else {shop[scope+'_run_id']}
        rows = [row for row in self._cards.values() if row['shop_id']==shop_id and row['run_id'] in selected_runs]
        population_count = len(rows)
        def matches(row):
            if artists_only and not any(artist.get('entity_kind')=='artist' for artist in self._artists.get(row['observation_id'], {}).values()):
                return False
            text = ' '.join(str(row.get(key) or '') for key in ('title','goods_id','view_order')).casefold()
            if terms and not any(term.casefold() in text for term in terms):
                return False
            if (sales_min is not None or sales_max is not None) and (not _exact_yipin(row)
                    or sales_min is not None and row['sales_value']<sales_min
                    or sales_max is not None and row['sales_value']>sales_max):
                return False
            return image_status=='all' or self._image_state(row)==image_status
        rows = [row for row in rows if matches(row)]
        def position(row):
            return (-self._runs[row['run_id']]['_end_epoch'],row['run_id'],row['view_order'],row['observation_id'])
        rows.sort(key=(lambda row:(0,-row['sales_value'],*position(row)) if _exact_yipin(row) else (1,0,*position(row))) if sort=='sales_desc' else position)
        total = len(rows)
        return {**self._envelope('search',shop_id),'scope':scope,
            'filters':{'terms':terms,'terms_mode':'any_contains','sales_min':sales_min,'sales_max':sales_max,'image_status':image_status,'artists_only':artists_only,'sort':sort},
            'artist_filter_basis':'仅按已审artist_watchlist中entity_kind=artist的原卡关联；不凭标题猜人，不代表完整追星圈人物识别',
            'sort_basis':'精确已拼/已抢件按数值降序；其他标签及未知保持位置顺序放尾；不合计跨轮销量' if sort=='sales_desc' else '各轮按原位置；多轮时最新观察窗口在前',
            'runs':[self._run(run_id) for run_id in sorted(selected_runs,key=lambda rid:(-self._runs[rid]['_end_epoch'],rid))],
            'population_count':population_count,'total':total,'limit':limit,'offset':offset,
            'has_more':offset+limit<total,'next_offset':offset+limit if offset+limit<total else None,
            'rows':[self._card(row) for row in rows[offset:offset+limit]]}

    def card(self, shop_id, observation_id):
        self._shop(shop_id)
        row = self._check_card(observation_id,shop_id)
        return {**self._envelope('card',shop_id),'card':self._card(row,detail=True),'run':self._run(row['run_id'])}

    def trends(self, shop_id, *, limit=5, offset=0):
        shop = self._shop(shop_id)
        _integer(limit,'limit',1,20); _integer(offset,'offset',0,1_000_000)
        run = self._run(shop['reference_run_id'])
        signals = self._trends.get(shop_id, [])
        complete = run.get('status') == 'complete' and run.get('end_boundary_observed') is True
        usable = [row for row in signals if complete and row['included_in_watch']
                  and row['level'] in ('priority','watch','observe')
                  and _finite(row.get('latest_delta')) and row['latest_delta'] > 0]
        pictured = [row for row in usable if self._image_state(self._cards[row['observation_id']]) == 'cached']
        # Rank is generated by the reviewed trend rules (level, rate/delta, ID),
        # not by cumulative sales, image order, or a model-generated score.
        pictured.sort(key=lambda row: row['rank'])
        rows = [dict(deepcopy(row), anchor_card=self._card(self._cards[row['observation_id']]))
                for row in pictured[offset:offset+limit]]
        state = 'available' if pictured else 'no_pictured_growth' if usable else 'no_comparable_growth' if signals and complete else 'not_generated' if complete else 'incomplete_reference'
        return {**self._envelope('trends',shop_id),'scope':'reference','state':state,
            'summary':deepcopy(self._trend_summaries.get(shop_id, {})), 'runs':[run],
            'population_count':len(signals),'growth_candidate_count':len(usable),
            'missing_image_count':len(usable)-len(pictured),'total':len(pictured),'limit':limit,'offset':offset,
            'has_more':offset+limit<len(pictured),'next_offset':offset+limit if offset+limit<len(pictured) else None,
            'sort_basis':'已审趋势规则级别→区间24h速率（未知则原展示差值）→原差值→观察ID；累计已拼量不参与排序',
            'limitations':LIMITATIONS[:] + TREND_LIMITATIONS[:], 'rows':rows}

    def new_arrivals(self, shop_id, *, limit=20, offset=0):
        self._shop(shop_id); _integer(limit,'limit',1,20); _integer(offset,'offset',0,1_000_000)
        summary = deepcopy(self._arrival_summaries.get(shop_id, {'shop_id':shop_id,'state':'not_configured'}))
        rows = sorted(self._arrivals.get(shop_id, []), key=lambda row:(-self._runs[row['run_id']]['_end_epoch'],row['arrival_item_id']))
        selected = [dict(deepcopy(row),anchor_card=self._card(self._cards[row['observation_id']])) for row in rows[offset:offset+limit]]
        return {**self._envelope('new_arrivals',shop_id),'summary':summary,'total':len(rows),'limit':limit,'offset':offset,
            'has_more':offset+limit<len(rows),'next_offset':offset+limit if offset+limit<len(rows) else None,
            'interpretation':{'no_post_baseline_run':'固定起点后尚无采集，等待下一轮，不能判断无新品',
                'no_first_candidates':'已有起点后观察，但证据未形成新的首次候选，不代表已确认没有新品',
                'not_configured':'本店固定起点尚未配置','candidates_available':'仅为首次观察候选或新卡线索，不是已确认新品'}.get(summary.get('state'),'以原始新发现队列状态和局限为准'),
            'rows':selected}
