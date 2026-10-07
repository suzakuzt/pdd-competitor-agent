"""Reviewed public-source directory joined to title clues; read-only, no network."""
from __future__ import annotations

from .sales_metrics import SALES_METRIC_LABELS, same_sales_metric

from collections import defaultdict
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import quote, urlsplit

CATALOG_PATH = Path('state/artist_research/catalog.json')
QUERY_IDS = ('artist_watchlist', 'artist_research_summary', 'artist_source_channels')


def _safe_url(value):
    if not isinstance(value, str) or any(ch.isspace() or ord(ch) < 32 or ord(ch) == 127 for ch in value):
        raise ValueError('Public source URL contains whitespace or control characters')
    parts = urlsplit(value)
    if parts.scheme != 'https' or not parts.hostname or parts.username or parts.password:
        raise ValueError('Public source URL must be HTTPS without credentials')
    return value


def load_catalog(project):
    path = Path(project) / CATALOG_PATH
    if not path.exists():
        return {'version': 'unconfigured', 'entities': [], 'event_rules': [], 'sources': [], 'channels': []}, {}
    raw = path.read_bytes()
    catalog = json.loads(raw)
    if catalog.get('schema_version') != 1 or not isinstance(catalog.get('entities'), list):
        raise ValueError('Unsupported artist research catalog')
    ids = [row['entity_id'] for row in catalog['entities']]
    if len(ids) != len(set(ids)):
        raise ValueError('Duplicate artist catalog identity')
    for entity in catalog['entities']:
        if entity.get('entity_kind') not in ('artist','esports','unresolved') or not entity.get('name'):
            raise ValueError('Invalid artist catalog entity')
        terms = entity.get('match_terms')
        if not isinstance(terms,list) or not terms or not all(isinstance(term,str) and term.strip() for term in terms):
            raise ValueError('Artist match terms must be non-empty strings')
    for row in catalog.get('sources', []) + catalog.get('channels', []):
        _safe_url(row['url'])
    return catalog, {str(path.resolve()): hashlib.sha256(raw).hexdigest()}


def title_entities(title, catalog):
    """Longest containing name wins; explicit context protects esports handles."""
    title = str(title or '')
    result, occupied, candidates = [], [], []
    artists = [e for e in catalog['entities'] if e['entity_kind'] == 'artist']
    for entity in artists:
        for term in entity['match_terms']:
            candidates.extend((m.start(),m.end(),entity['entity_id']) for m in re.finditer(re.escape(term), title, re.I))
    for a,b,eid in sorted(candidates, key=lambda value: (-(value[1]-value[0]),value[0],value[2])):
        if not any(a >= c and b <= d for c,d in occupied):
            if eid not in result:
                result.append(eid)
            occupied.append((a,b))
    for entity in catalog['entities']:
        if entity['entity_kind'] == 'artist':
            continue
        context = entity.get('required_context')
        if context and context.casefold() not in title.casefold():
            continue
        if any(term.casefold() in title.casefold() for term in entity['match_terms']):
            result.append(entity['entity_id'])
    return result


def event_clues(title, catalog):
    title = str(title or '')
    for phrase in catalog.get('excluded_generic_event_phrases', []):
        title = title.replace(phrase, '')
    return [rule for rule in catalog['event_rules'] if any(term.casefold() in title.casefold() for term in rule['terms'])]


def eligible(row):
    return (row.get('sales_label') in SALES_METRIC_LABELS and row.get('sales_unit') == '件'
            and row.get('sales_precision') == 'exact_display'
            and type(row.get('sales_value')) is int and row['sales_value'] > 10
            and bool(str(row.get('sales_raw') or '').strip()))


def _clue_counts(rows, catalog):
    hits = defaultdict(list)
    for row in rows:
        for clue in event_clues(row['title'], catalog):
            hits[clue['clue_id']].append(row['observation_id'])
    return [{'clue_id': rule['clue_id'], 'label': rule['label'], 'count': len(hits[rule['clue_id']]),
             'observation_ids': hits[rule['clue_id']]} for rule in catalog['event_rules'] if hits[rule['clue_id']]]


def build_artist_research(catalog, shops, runs, observations):
    output = {key: [] for key in QUERY_IDS}
    by_run = defaultdict(list)
    run_index = {run['run_id']: run for run in runs}
    for row in observations:
        by_run[row['run_id']].append(row)
    entities = {e['entity_id']: e for e in catalog['entities']}
    matches = {row['observation_id']: title_entities(row['title'], catalog) for row in observations}
    checked = catalog.get('checked_date')
    for shop in shops:
        ref, latest = shop['reference_run_id'], shop['latest_run_id']
        if any(run_index[key]['shop_id'] != shop['shop_id'] for key in (ref, latest)):
            raise ValueError('Artist reference/latest shop mismatch')
        ref_rows, latest_rows = by_run[ref], by_run[latest]
        active_ids = {eid for row in ref_rows + latest_rows for eid in matches[row['observation_id']]}
        artist_rows = [row for row in ref_rows if any(entities[eid]['entity_kind'] == 'artist' for eid in matches[row['observation_id']])]
        shop_watch = []
        for entity_id in sorted(active_ids):
            entity = entities[entity_id]
            subset = [r for r in ref_rows if entity_id in matches[r['observation_id']]]
            latest_subset = [r for r in latest_rows if entity_id in matches[r['observation_id']]]
            sources = [deepcopy(source) for source in catalog.get('sources', []) if source['artist'] == entity['name']]
            trusted = any(source.get('verified_entry') is True for source in sources)
            search_terms = entity.get('search_name') or entity['name']
            item = {
                'artist_id': shop['shop_id'] + ':' + entity_id, 'entity_id': entity_id,
                'artist_name': entity['name'], 'entity_kind': entity['entity_kind'],
                'identity_note': entity.get('identity_note', '原标题名称线索，具体款式人物仍以原卡核对。'),
                'shop_id': shop['shop_id'], 'run_id': ref, 'reference_card_count': len(subset),
                'eligible_card_count': sum(eligible(r) for r in subset),
                'observation_ids': [r['observation_id'] for r in subset],
                'eligible_observation_ids': [r['observation_id'] for r in subset if eligible(r)],
                'latest_run_id': latest, 'latest_card_count': len(latest_subset),
                'latest_observation_ids': [r['observation_id'] for r in latest_subset],
                'latest_is_partial': not (run_index[latest]['status'] == 'complete' and run_index[latest].get('end_boundary_observed')),
                'title_event_clues': _clue_counts(subset, catalog),
                'latest_title_event_clues': _clue_counts(latest_subset, catalog),
                'title_search_text': '\n'.join(dict.fromkeys(r['title'] for r in subset + latest_subset)),
                'title_examples': [{'observation_id': r['observation_id'], 'title': r['title']} for r in subset[:5]],
                'sources': sources, 'source_status': '有核实入口' if trusted else '入口待核验',
                'research_checked_at': checked,
                'search_links': [{'label': '检索公开活动', 'url': 'https://www.baidu.com/s?wd=' + quote(search_terms + ' 官方 工作室 公开活动'),
                                  'purpose': '检索快捷入口，不是已核实活动或本人主页'}],
            }
            shop_watch.append(item)
        shop_watch.sort(key=lambda row: (-row['eligible_card_count'], -row['reference_card_count'], row['artist_name']))
        output['artist_watchlist'].extend(shop_watch)
        clue_counts = _clue_counts(artist_rows, catalog)
        multi_titles = sum(len({r['title'] for r in ref_rows if item['entity_id'] in matches[r['observation_id']]}) > 1 for item in shop_watch if item['entity_kind'] == 'artist')
        artist_names = [item for item in shop_watch if item['entity_kind'] == 'artist']
        summary = {
            'shop_id': shop['shop_id'], 'run_id': ref, 'reference_card_count': len(ref_rows),
            'artist_count': len(artist_names), 'reference_artist_count': sum(item['reference_card_count'] > 0 for item in artist_names),
            'artist_card_count': len(artist_rows), 'artist_eligible_card_count': sum(eligible(r) for r in artist_rows),
            'unmatched_card_count': sum(not matches[r['observation_id']] for r in ref_rows),
            'verified_source_artist_count': sum(item['source_status'] == '有核实入口' for item in artist_names),
            'event_clues': clue_counts, 'research_checked_at': checked, 'catalog_version': catalog.get('version'),
            'coverage_note': '按已审阅称呼词表匹配；未命中包括游戏、虚拟题材和未知人名，不等于没有明星。多人卡可多处出现，人物卡数不可相加。入口核查不代表当天活动已采全或图片已授权。',
            'hypotheses': [
                {'title': '围绕公开题材持续找选题', 'fact': f'参考轮{len(ref_rows)}原卡中，{len(artist_rows)}卡标题命中艺人称呼；活动词按原文分列。',
                 'inference': '社媒新图、生日、演出和品牌词提供了可反复跟进的题材入口；不能证明卖家使用了哪套采图工具。',
                 'action': '先看本人/工作室公告，再跟原文提到的品牌、主办方和杂志。'},
                {'title': '同一人物可覆盖多个题材', 'fact': f'参考轮有{multi_titles}个艺人称呼关联不止一个原标题；每张卡仍独立。',
                 'inference': '可以按人物组织素材与活动台账，再按事件筛选；不同标题不证明不同SKU或更高收益。',
                 'action': '从已有达标原卡较多的人物开始核验，比较题材与供货条件；不把卡片展示数相加。'},
                {'title': '原始发布与可商用素材分别记录', 'fact': '商品缓存图片没有原作者/授权凭证字段，本次未确认竞品素材供应链。',
                 'inference': '公开可查看的图并不自动附带商品化许可。',
                 'action': '记录原帖、作者、公开活动时间及商品化许可；确认可用后少量测试，分别记曝光、点击、支付、退货及成本。'}
            ]
        }
        output['artist_research_summary'].append(summary)
    output['artist_source_channels'] = deepcopy(catalog.get('channels', []))
    return output
