"""Small, whole-message grammar for deterministic local read-only queries.

This module never reads records, credentials, files or the network. Unknown or
ambiguous requests return None for the caller's normal planner. New products
mean the competitor's complete reviewed 上新 list, not a today-only filter and
not first-seen changes. Every accepted plan uses the shared nine-field schema.
"""
from __future__ import annotations

import re

from .agent_codex import MAX_INTEGER, validate_plan


_NUMBER = r'(?:[0-9]{1,16}|[零〇一二两三四五六七八九十百千]{1,12})'
_PREFIX = re.compile(
    r'^(?:请|麻烦)?(?:帮我|给我)?'
    r'(?:查一下|查下|查查|看一下|看看|查询|查看|查找|列出|显示|找|看)?'
    r'(?:这家店铺|这个店铺|当前店铺|这家店|本店|店铺)?(?:的)?')
_NEW_LIST = r'(?:新品|上新)(?:列表|商品)?(?:里面|中|里|的)?'
_RANKING = r'(?:热销|销量最高|销量最多|已拼最高|已拼最多|已抢最高|已抢最多)'
_TAIL = rf'(?:的)?(?:排名)?(?:前)?(?P<limit>{_NUMBER})(?:个|件|张|款|条)?(?:商品|原卡|记录)?'
_SEARCH = re.compile(rf'(?:{_NEW_LIST}(?:{_RANKING})?|{_RANKING}(?:商品)?)(?:{_TAIL})?')
_THRESHOLD = re.compile(
    rf'(?:{_NEW_LIST})?(?:已拼|已抢)(?:件数)?'
    rf'(?P<op>大于等于|小于等于|不少于|不超过|至少|超过|大于|小于|少于|等于|>=|<=|≥|≤|>|<|=)'
    rf'(?P<bound>{_NUMBER})(?:件)?(?:的)?(?:{_RANKING})?(?:{_TAIL})?')
_THRESHOLD_SUFFIX = re.compile(
    rf'(?:{_NEW_LIST})?(?:已拼|已抢)(?P<bound>{_NUMBER})(?:件)?(?P<op>以上|以下)'
    rf'(?:的)?(?:{_RANKING})?(?:{_TAIL})?')
_FIRST_SEEN = re.compile(
    rf'(?:最近)?(?:新增记录|首次发现(?:的)?(?:记录|商品)?|新发现了什么)'
    rf'(?:{_TAIL})?')
_TRENDS = re.compile(
    rf'(?:{_NEW_LIST})?(?:哪些(?:款|商品|产品)(?:有(?:增长)?趋势|在增长|增长了)|'
    rf'有(?:增长)?趋势的(?:商品|产品)|(?:商品)?(?:增长)?趋势(?:列表|商品|产品)?)'
    rf'(?:{_TAIL})?')
_DETAIL = r'(?:详情|详细信息|参考来源|来源|原图)'
_CARD_PREVIOUS = re.compile(
    rf'(?:上一答|上个回答|上一条回答|刚才回答)(?:中|里|的)?第(?P<position>{_NUMBER})'
    rf'(?:个|张|条)(?:商品|原卡|卡片)?(?:的)?(?:{_DETAIL})?')
_CARD_DETAIL = re.compile(
    rf'第(?P<position>{_NUMBER})(?:个|张|条)(?:商品|原卡|卡片)?(?:的)?{_DETAIL}')
_DIGITS = dict(zip('零一二三四五六七八九', range(10)))
_UNITS = {'十': 10, '百': 100, '千': 1000}


def _to_chinese(number):
    if number == 0:
        return '零'
    result, pending_zero = '', False
    for unit, name in ((1000, '千'), (100, '百'), (10, '十'), (1, '')):
        digit, number = divmod(number, unit)
        if digit:
            if pending_zero:
                result += '零'
            result += '零一二三四五六七八九'[digit] + name
            pending_zero = False
        elif result and number:
            pending_zero = True
    return result[1:] if result.startswith('一十') else result


def _integer(text):
    if text is None:
        return None
    if re.fullmatch(r'[0-9]{1,16}', text):
        value = int(text)
        return value if value <= MAX_INTEGER else None
    normalized = text.replace('两', '二').replace('〇', '零')
    total, digit = 0, 0
    for char in normalized:
        if char in _DIGITS:
            digit = _DIGITS[char]
        elif char in _UNITS:
            total += (digit or 1) * _UNITS[char]
            digit = 0
        else:
            return None
    value = total + digit
    # Reject ambiguous shorthand such as 一百二 and malformed 一一 or 十十.
    canonical = normalized[1:] if normalized.startswith('一十') else normalized
    return value if 0 <= value <= 9999 and _to_chinese(value) == canonical else None


def _plan(action='search', *, limit=10, observation_id=None, sales_min=None, sales_max=None):
    return validate_plan({'action': action, 'terms': [], 'scope': 'reference',
                          'sales_min': sales_min, 'sales_max': sales_max,
                          'limit': limit, 'observation_id': observation_id,
                          'answer': '', 'artists_only': False})


def _limit(match):
    text = match.groupdict().get('limit')
    value = 10 if text is None else _integer(text)
    return min(value, 20) if value is not None and value > 0 else None


def _card_id(match, ids):
    position = _integer(match.group('position'))
    if type(ids) is not list or len(ids) > 20 or any(
            type(oid) is not int or not 1 <= oid <= MAX_INTEGER for oid in ids):
        return None
    if len(set(ids)) != len(ids) or position is None or not 1 <= position <= len(ids):
        return None
    return ids[position - 1]


def local_plan(message, last_observation_ids=None):
    """Return a validated QueryPlan for an exact known template, else None.

    No general keyword matching: modifiers, titles, alternative sales labels,
    requested date filters and compound instructions must match no template.
    The caller alone selects this shop, validates referenced IDs and executes.
    """
    if type(message) is not str or not message.strip() or len(message) > 2000:
        return None
    if any(ord(char) < 32 for char in message):
        return None
    text = message.strip().translate(str.maketrans('０１２３４５６７８９', '0123456789'))
    text = text.replace(' ', '').replace('\u3000', '')
    text = re.sub(r'[。！？?!]+$', '', text)
    text = _PREFIX.sub('', text, count=1)
    if not text:
        return None

    # The previous answer's list position is not the card's view_order or ID.
    for pattern in (_CARD_PREVIOUS, _CARD_DETAIL):
        match = pattern.fullmatch(text)
        if match:
            oid = _card_id(match, [] if last_observation_ids is None else last_observation_ids)
            return _plan('card', limit=1, observation_id=oid) if oid is not None else None

    match = _FIRST_SEEN.fullmatch(text)
    if match:
        limit = _limit(match)
        return _plan('new_arrivals', limit=limit) if limit is not None else None

    match = _TRENDS.fullmatch(text)
    if match:
        limit = 5 if match.groupdict().get('limit') is None else _limit(match)
        return _plan('trends', limit=limit) if limit is not None else None

    for pattern in (_THRESHOLD, _THRESHOLD_SUFFIX):
        match = pattern.fullmatch(text)
        if not match:
            continue
        bound, limit, operation = _integer(match.group('bound')), _limit(match), match.group('op')
        if bound is None or limit is None:
            return None
        lower = upper = None
        if operation in ('>', '大于', '超过'):
            lower = bound + 1
        elif operation in ('>=', '≥', '大于等于', '不少于', '至少', '以上'):
            lower = bound
        elif operation in ('<', '小于', '少于'):
            upper = bound - 1
        elif operation in ('<=', '≤', '小于等于', '不超过', '以下'):
            upper = bound
        elif operation in ('=', '等于'):
            lower = upper = bound
        if any(value is not None and not 0 <= value <= MAX_INTEGER for value in (lower, upper)):
            return None
        return _plan(limit=limit, sales_min=lower, sales_max=upper)

    match = _SEARCH.fullmatch(text)
    if match:
        limit = _limit(match)
        return _plan(limit=limit) if limit is not None else None
    return None
