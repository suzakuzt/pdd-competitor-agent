"""Read-only replay of v5 rendered-DOM capture evidence (no network or DB writes).

Hashes bind the files inspected now, not a signature of the website or proof
against consistent replacement of all source files. Legacy snapshots are explicitly
unverified, never retrospectively promoted to a verified complete capture.
"""
from __future__ import annotations

from datetime import datetime
import hashlib
import json
import math
from pathlib import Path, PurePosixPath
import re
from urllib.parse import urlsplit

MAX_FILE_BYTES = 32 * 1024 * 1024
MAX_EVIDENCE_BYTES = 1024 * 1024 * 1024
MAX_BATCHES = 10000
MAX_SLOTS = 100000
IDENTITY_FIELDS = ('identityTitle', 'imageUrl', 'goodsUrl')
RELEVANT = ('ready', 'pending')


class _Invalid(ValueError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


def _need(condition, code, message):
    if not condition:
        raise _Invalid(code, message)


def _integer(value, minimum=0):
    return type(value) is int and value >= minimum


def _finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def _same_json(actual, expected):
    # Python's True == 1 must not make malformed evidence look identical.
    return json.dumps(actual, sort_keys=True, allow_nan=False, separators=(',', ':')) == json.dumps(expected, sort_keys=True, allow_nan=False, separators=(',', ':'))


def _time(value):
    _need(isinstance(value, str), 'invalid_time', '观察时间必须保留带时区原文')
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        _need(parsed.tzinfo is not None, 'invalid_time', '观察时间缺少时区')
        return parsed.timestamp()
    except (ValueError, OverflowError) as error:
        raise _Invalid('invalid_time', '观察时间无效') from error


def _object_pairs(pairs):
    result = {}
    for key, value in pairs:
        _need(key not in result, 'duplicate_json_key', 'JSON含重复字段')
        result[key] = value
    return result


def _read(path):
    _need(path.is_file() and not path.is_symlink(), 'missing_source', '来源文件缺失或为符号链接：' + path.name)
    _need(path.stat().st_size <= MAX_FILE_BYTES, 'source_too_large', '来源文件超过大小限制')
    raw = path.read_bytes()
    _need(len(raw) <= MAX_FILE_BYTES, 'source_too_large', '来源文件超过大小限制')
    try:
        data = json.loads(raw.decode('utf-8-sig'), object_pairs_hook=_object_pairs,
                          parse_constant=lambda v: (_ for _ in ()).throw(ValueError(v)))
    except (ValueError, UnicodeError) as error:
        if isinstance(error, _Invalid):
            raise
        raise _Invalid('invalid_json', '来源JSON损坏或含非有限数值：' + path.name) from error
    _need(isinstance(data, dict), 'invalid_json', '来源必须为JSON对象')
    return data, {'file': path.name, 'sha256': hashlib.sha256(raw).hexdigest(), 'byte_count': len(raw)}


def requires_capture_integrity(snapshot):
    """Recognize v5 even when its version alone was removed or downgraded."""
    if not isinstance(snapshot, dict):
        return False
    evidence = snapshot.get('collectionEvidence')
    evidence = evidence if isinstance(evidence, dict) else {}
    version = evidence.get('driverVersion')
    if (_finite(version) and version >= 5) or (isinstance(version, str) and version.isdigit() and int(version) >= 5):
        return True
    text = ' '.join(str(evidence.get(k, '')) for k in ('mode', 'completionRule', 'rowTimeMeaning')).lower()
    if any(marker in text for marker in ('offviewport', 'all rendered nonhidden', 'rendered_nonhidden', 'all current nonhidden')):
        return True
    if any(k in evidence for k in ('pendingSlots', 'pendingSlotCount', 'checkpointManifest', 'captureSession')):
        return True
    rows = snapshot.get('rows', [])
    return isinstance(rows, list) and any(isinstance(row, dict) and (
        'latestBatch' in row or ('slot' in row and 'ready' in row and 'firstRenderedAt' in row)) for row in rows)


def _origin(url):
    _need(isinstance(url, str), 'invalid_shop_url', '缺少店铺来源地址')
    try:
        parsed = urlsplit(url)
        _need(parsed.scheme in ('http', 'https') and parsed.hostname and not parsed.username and not parsed.password,
              'invalid_shop_url', '店铺来源地址无效')
        return (parsed.scheme, parsed.hostname, parsed.port)
    except ValueError as error:
        raise _Invalid('invalid_shop_url', '店铺来源地址无效') from error


def _identity(url):
    from .store import shop_identity_evidence
    _origin(url)
    try:
        identity = shop_identity_evidence({'sourceUrl': url})
    except ValueError as error:
        raise _Invalid('invalid_shop_identity', '缺少唯一可核对的店铺来源身份') from error
    return identity


def _same_identity(actual, expected):
    return all(actual.get(k) == expected.get(k) for k in ('identity_kind', 'stable_identifier'))


def _directory(snapshot_path, reference):
    _need(isinstance(reference, str) and 0 < len(reference) <= 2048, 'unsafe_checkpoint_path', '缺少批次目录声明')
    normalized = reference.replace('\\', '/')
    parts = PurePosixPath(normalized).parts
    _need(not normalized.startswith('/') and ':' not in normalized and '..' not in parts and
          not any(ord(c) < 32 for c in normalized), 'unsafe_checkpoint_path', '批次目录必须为不含回退的相对路径')
    parent = snapshot_path.parent.resolve()
    # A relocated recovery bundle may retain its project-relative declaration.
    # Never follow it up/outside the bundle: only these adjacent layouts are read.
    candidates = []
    for path in (parent / 'batches', parent):
        if not path.exists():
            continue
        _need(not path.is_symlink() and path.resolve().is_relative_to(parent), 'unsafe_checkpoint_path', '批次目录超出来源边界')
        if path.is_dir() and any(p.name.lower().startswith('batch_') for p in path.iterdir()):
            candidates.append(path)
    _need(len(candidates) == 1, 'ambiguous_or_missing_batches', '相邻批次目录缺失或有多个冲突版本')
    return candidates[0]


def _position(position):
    _need(isinstance(position, dict) and all(_finite(position.get(k)) for k in ('top', 'left', 'bottom', 'viewportTop', 'viewportBottom'))
          and position['bottom'] > position['top'], 'invalid_position', '原卡位置证据无效')


def _number_text(number):
    return str(int(number)) if number == int(number) else str(number)


def _replay(batch, previous, slots, rows, position_anchors, expected, origin, maximum_scroll):
    """Validate an accepted batch and derive independent row/time state."""
    _need(batch.get('driverVersion') == 5 and type(batch.get('driverVersion')) is int, 'unsupported_driver', '批次驱动版本不受支持')
    actual = _identity(batch.get('pageUrl'))
    _need(_same_identity(actual, expected) and _origin(batch['pageUrl']) == origin,
          'cross_shop_batch', '批次来源不是本次店铺')
    for key in ('observedShopIdentity', 'expectedShopIdentity'):
        _need(isinstance(batch.get(key), dict) and _same_identity(batch[key], expected), 'cross_shop_batch', '批次身份凭证不一致')
    _need(all(batch.get(k) is True for k in ('shopVerified', 'shopIdentityVerified', 'listPresent', 'columnLayoutVerified')),
          'unverified_batch', '店铺、列表或列结构未通过核对')
    _need(all(_finite(batch.get(k)) for k in ('scrollTop', 'scrollHeight', 'viewportHeight', 'viewportWidth')) and
          batch['scrollTop'] >= 0 and batch['viewportHeight'] > 0 and batch['viewportWidth'] > 0,
          'invalid_viewport', '视口或滚动位置无效')
    scroll = batch.get('scrollEvidence')
    if previous is None:
        _need(batch['scrollTop'] <= 2 and scroll is None, 'not_started_at_top', '首批未从列表顶部开始')
    else:
        _need(all(batch[k] == previous[k] for k in ('viewportWidth', 'viewportHeight')),
              'viewport_changed', '采集中视口尺寸发生变化')
        _need(batch['scrollTop'] >= previous['scrollTop'] - 2, 'reverse_scroll', '采集中出现反向滚动')
        _need(batch['scrollTop'] <= previous['scrollTop'] + 2 or isinstance(scroll, dict),
              'unrecorded_scroll', '移动缺少滚动证据')
        if scroll is not None:
            _need(isinstance(scroll, dict), 'invalid_scroll', '滚动证据无效')
            limit = max(previous['scrollTop'], min(previous['readCoverageBottom'] or previous['scrollTop'],
                                                  previous['scrollHeight'] - previous['viewportHeight']))
            requested = max(0, min(previous['viewportHeight'] * maximum_scroll, limit - previous['scrollTop']))
            _need(scroll.get('from') == previous['scrollTop'] and scroll.get('readCoverageBottom') == previous['readCoverageBottom'] and
                  scroll.get('maxAllowedScrollTop') == limit and scroll.get('requestedPixels') == requested and
                  _finite(scroll.get('requestedViewportUnits')) and abs(scroll['requestedViewportUnits'] - requested / previous['viewportHeight']) < 1e-9 and
                  scroll.get('kind') == ('normal_scroll' if requested > 0 else 'stationary_read') and batch['scrollTop'] <= limit + 2,
                  'scroll_coverage_gap', '滚动超出已读取覆盖或记录与前批不一致')
    boundaries = batch.get('boundaries')
    recommendations = batch.get('recommendations')
    _need(isinstance(boundaries, list) and isinstance(recommendations, list), 'invalid_boundary', '缺少结束边界及推荐分界证据')
    for point in boundaries + recommendations:
        _need(isinstance(point, dict) and all(_finite(point.get(k)) for k in ('top', 'bottom', 'documentTop')) and
              type(point.get('rendered')) is bool and type(point.get('visible')) is bool and point['bottom'] > point['top'] and
              abs(point['documentTop'] - point['top'] - batch['scrollTop']) <= 2,
              'invalid_boundary', '边界位置证据无效')
        visible = point['rendered'] and point['bottom'] > 0 and point['top'] < batch['viewportHeight']
        _need(point['visible'] == visible, 'boundary_not_visible', '结束边界可见标记与实际视口位置不符')
    visible_boundary = any(p['rendered'] and p['visible'] for p in boundaries)
    _need(type(batch.get('endBoundaryObserved')) is bool and batch['endBoundaryObserved'] == visible_boundary,
          'boundary_not_visible', '本店结束边界未在当前视口真实可见')
    cutoff = min((p['documentTop'] for p in boundaries + recommendations if p['rendered']), default=math.inf)
    _need(all(p['documentTop'] <= min((r['documentTop'] for r in recommendations if r['rendered']), default=math.inf)
              for p in boundaries), 'boundary_after_recommendations', '结束边界位于其他店铺推荐之后')
    counts, manifest, cards = batch.get('columnSlotCounts'), batch.get('slotManifest'), batch.get('cards')
    _need(isinstance(counts, list) and 0 < len(counts) <= 20 and all(_integer(n) for n in counts) and sum(counts) <= MAX_SLOTS and
          isinstance(manifest, list) and isinstance(cards, list), 'invalid_manifest', '列槽位或卡片清单无效')
    _need(_integer(batch.get('loadedCardCount')) and sum(counts) == batch['loadedCardCount'] == len(manifest),
          'slot_count_mismatch', '槽位数量与DOM清单不一致')
    if previous:
        _need(len(counts) == len(previous['columnSlotCounts']) and all(n >= old for n, old in zip(counts, previous['columnSlotCounts'])),
              'column_replaced', '列槽位减少，不能证明连续覆盖')
    manifest_map, card_map, positions = {}, {}, set()
    for entry in manifest:
        _need(isinstance(entry, dict), 'invalid_manifest', '槽位项无效')
        column, index = entry.get('domColumn'), entry.get('domRow')
        _need(_integer(column) and column < len(counts) and _integer(index) and index < counts[column] and
              entry.get('slot') == f'{column}:{index}' and entry['slot'] not in manifest_map and
              entry.get('state') in (*RELEVANT, 'excluded_hidden', 'excluded_after_boundary'),
              'duplicate_or_missing_slot', '槽位重复、断档或列编号无效')
        if entry['state'] == 'excluded_after_boundary':
            _position(entry.get('position'))
            _need(entry['position']['top'] >= cutoff, 'false_exclusion', '本店边界前卡片被错误排除')
        manifest_map[entry['slot']] = entry
    for card in cards:
        _need(isinstance(card, dict) and isinstance(card.get('slot'), str), 'invalid_card', '原卡缺少槽位')
        entry = manifest_map.get(card['slot'])
        _need(entry is not None and entry['state'] in RELEVANT and card['slot'] not in card_map and
              type(card.get('domColumn')) is int and type(card.get('domRow')) is int and
              card.get('domColumn') == entry['domColumn'] and card.get('domRow') == entry['domRow'],
              'card_manifest_mismatch', '原卡与槽位清单不一致或重复')
        _position(card.get('position'))
        p = card['position']
        _need(abs(p['top'] - p['viewportTop'] - batch['scrollTop']) <= 2 and
              abs(p['bottom'] - p['viewportBottom'] - batch['scrollTop']) <= 2 and p['top'] < cutoff,
              'invalid_position', '原卡文档位置与视口或本店边界不一致')
        _need(isinstance(entry.get('position'), dict) and all(entry['position'].get(k) == p[k] for k in p),
              'manifest_position_conflict', '槽位位置与卡片位置不一致')
        key = (p['top'], p['left'])
        _need(key not in positions, 'duplicate_position', '不同槽位占用相同文档位置')
        positions.add(key)
        ready = entry['state'] == 'ready'
        _need(type(card.get('ready')) is bool and card['ready'] == ready and isinstance(card.get('pendingReasons'), list),
              'invalid_readiness', '卡片就绪状态与槽位不一致')
        if ready:
            _need(isinstance(card.get('title'), str) and card['title'].strip() and card.get('identityTitle') == card['title'] and
                  isinstance(card.get('rawText'), str) and not card['pendingReasons'], 'unready_card', '就绪卡片缺少标题或原文')
            _origin(card.get('imageUrl'))
        else:
            _need(card['pendingReasons'], 'missing_pending_reason', '未就绪卡片缺少原因')
        card_map[card['slot']] = card
    required = {slot for slot, entry in manifest_map.items() if entry['state'] in RELEVANT}
    _need(required == set(card_map), 'missing_card', '应读取槽位有遗漏')
    bottom = max((c['position']['bottom'] for c in cards), default=None)
    _need(batch.get('readCoverageBottom') == bottom, 'coverage_bottom_mismatch', '读取覆盖下界与实际卡片不符')
    for slot, prior in slots.items():
        current, card = manifest_map.get(slot), card_map.get(slot)
        _need(current is not None, 'previous_slot_missing', '前批槽位在本批消失')
        if prior['state'] in RELEVANT:
            _need(card is not None and current['state'] in RELEVANT, 'previous_card_missing', '已读取卡片被隐藏或移出列表')
            anchor = position_anchors.get(slot)
            _need(anchor is not None and all(abs(anchor[k] - card['position'][k]) <= 1 for k in ('top', 'left')),
                  'layout_changed', '同一槽位偏离首次读取位置超过1个CSS像素')
            _need(all(prior['identity'][k] is None or prior['identity'][k] == card.get(k) for k in IDENTITY_FIELDS),
                  'slot_content_conflict', '同一槽位标题、原图或链接发生冲突')
            _need(prior['state'] != 'ready' or card['ready'], 'ready_became_pending', '已就绪卡片退回未就绪')
    next_slots, next_rows, next_anchors = dict(slots), dict(rows), dict(position_anchors)
    added = 0
    for slot, entry in manifest_map.items():
        if entry['state'] not in RELEVANT:
            next_slots[slot] = dict(entry)
            continue
        card = card_map[slot]
        next_anchors.setdefault(slot, {key: card['position'][key] for key in ('top', 'left')})
        first_rendered = slots.get(slot, {}).get('firstRenderedAt') or batch['at']
        next_slots[slot] = {**entry, 'identity': {k: card.get(k) for k in IDENTITY_FIELDS}, 'firstRenderedAt': first_rendered}
        if not card['ready']:
            continue
        old_row = rows.get(slot)
        added += old_row is None
        next_rows[slot] = {**card, 'recordKey': 'position_' + _number_text(card['position']['top']) + '_' + _number_text(card['position']['left']),
                           'firstRenderedAt': first_rendered, 'firstObservedAt': old_row['firstObservedAt'] if old_row else batch['at'],
                           'observedAt': batch['at'], 'observedAtPrecision': 'batch_read', 'firstObservedAtPrecision': 'batch_read',
                           'observationTimeMeaning': 'latest actual rendered DOM batch read; not listing time', 'latestBatch': batch['batch']}
    pending = sum(entry['state'] == 'pending' for entry in next_slots.values())
    complete = visible_boundary and bool(required) and all(manifest_map[slot]['state'] == 'ready' for slot in required)
    new_relevant = sum(slot not in slots or slots[slot]['state'] not in RELEVANT for slot in required)
    no_progress = 0 if added or new_relevant else (previous['noProgressReads'] if previous else 0)
    if scroll is not None:
        reached_frontier = batch['scrollTop'] + batch['viewportHeight'] >= previous['readCoverageBottom'] - 2
        stuck = abs(batch['scrollTop'] - previous['scrollTop']) <= 2
        waiting_at_boundary = visible_boundary and not complete
        if not added and not new_relevant and (reached_frontier or stuck or waiting_at_boundary):
            no_progress += 1
        _need(no_progress < 3 or complete, 'unreported_stop', '连续三次前沿、停滞或边界读取无进展；停止标记不能被删除后继续')
    _need(type(batch.get('noProgressReads')) is int and batch['noProgressReads'] == no_progress,
          'no_progress_mismatch', '连续无进展次数与实际批次不符')
    _need(all(_integer(batch.get(k)) for k in ('added', 'seen', 'pending')) and
          (batch['added'], batch['seen'], batch['pending']) == (added, len(next_rows), pending), 'batch_counts_mismatch', '批次累计/新增/未就绪数量不一致')
    _need(type(batch.get('complete')) is bool and batch['complete'] == complete, 'false_complete', '批次完整声明不符合可见结束边界和就绪覆盖')
    return next_slots, next_rows, next_anchors


def verify_capture(snapshot_path, expected_shop_id=None):
    """Return a bounded JSON receipt; ``valid`` is false for malformed evidence.

    Legacy: status=legacy_unverified, valid=true, gate_required=false,
    verified_status=null. v5: passed/failed, verified_status=complete/partial/null.
    This function never changes source files or opens a database.
    """
    receipt = {'schema_version': 1, 'status': 'failed', 'valid': False, 'gate_required': True,
               'declared_status': None, 'verified_status': None, 'shop_id': None, 'checks': [], 'errors': [],
               'warnings': [], 'source_hashes': [], 'batch_count': 0, 'verified_batch_count': 0, 'card_count': 0,
               'summary': '完整性证据未通过；不得按完整轮导入',
               'limitations': ['仅校验留存来源的一致性，不是网站签名或防全套替换证明',
                               '完整采集不代表商品身份、真实订单或上架时间已核实']}
    try:
        path = Path(snapshot_path).expanduser().absolute()
        snapshot, fingerprint = _read(path)
        receipt['snapshot_sha256'] = fingerprint['sha256']
        receipt['source_hashes'].append({'role': 'snapshot', **fingerprint})
        receipt['declared_status'] = snapshot.get('status')
        gate = requires_capture_integrity(snapshot)
        receipt['gate_required'] = gate
        if expected_shop_id is not None:
            _need(isinstance(expected_shop_id, str) and re.fullmatch(r'shop_[0-9a-f]{24}', expected_shop_id),
                  'invalid_expected_shop', '目标店铺编号格式无效')
        if not gate:
            if expected_shop_id:
                _need(_identity(snapshot.get('sourceUrl'))['shop_id'] == expected_shop_id, 'wrong_shop', '快照不属于目标店铺')
            receipt.update(status='legacy_unverified', valid=True, summary='旧格式快照兼容保留；未提供v5独立完整性证据')
            receipt['warnings'].append('旧格式不按本校验器认定完整，不改写既有历史状态')
            return receipt
        evidence = snapshot.get('collectionEvidence')
        _need(isinstance(evidence, dict) and type(evidence.get('driverVersion')) is int and evidence['driverVersion'] == 5,
              'unsupported_driver', '检测到v5证据，但版本缺失、被改写或尚不支持')
        _need(('importReady' not in evidence or evidence['importReady'] is True) and
              ('checkpointIntegrityStatus' not in evidence or evidence['checkpointIntegrityStatus'] == 'verified'),
              'manual_review_required', '采集会话尚未确认可入库或批次证据需要人工复核')
        if 'captureSession' in evidence:
            _need(evidence.get('importReady') is True and evidence.get('checkpointIntegrityStatus') == 'verified' and
                  'checkpointManifest' in evidence,
                  'session_not_sealed', '新版采集会话缺少可入库确认、已验证状态或完整批次SHA清单')
        _need(snapshot.get('status') in ('complete', 'partial'), 'invalid_status', '采集状态必须明确完整或部分')
        expected = _identity(snapshot.get('sourceUrl'))
        from .store import shop_identity_evidence
        try:
            explicit_identity = shop_identity_evidence(snapshot)
        except ValueError as error:
            raise _Invalid('invalid_shop_identity', '快照显式店铺标识与来源冲突') from error
        _need(_same_identity(explicit_identity, expected), 'invalid_shop_identity', '快照显式店铺标识与来源冲突')
        receipt['shop_id'] = expected['shop_id']
        _need(expected_shop_id is None or expected_shop_id == expected['shop_id'], 'wrong_shop', '快照不属于目标店铺')
        declared_identity = snapshot.get('shopIdentityEvidence')
        _need(isinstance(declared_identity, dict) and _same_identity(declared_identity, expected), 'invalid_shop_identity', '快照店铺身份声明与来源不一致')
        _need(isinstance(snapshot.get('shopName'), str) and snapshot['shopName'].strip(), 'missing_shop_name', '缺少本店名称')
        directory = _directory(path, evidence.get('checkpointDirectory'))
        paths = sorted(p for p in directory.iterdir() if p.name.lower().startswith('batch_'))
        n = evidence.get('batchCount')
        _need(_integer(n, 1) and n <= MAX_BATCHES and len(paths) == n, 'batch_count_mismatch', '批次数量不足、多余或声明无效')
        _need([p.name for p in paths] == [f'batch_{i:04d}.json' for i in range(1, n + 1)],
              'batch_sequence_gap', '批次文件重复、断档或命名无效')
        declared_manifest = evidence.get('checkpointManifest')
        if 'checkpointManifest' in evidence:
            _need(isinstance(declared_manifest, list) and len(declared_manifest) == n,
                  'checkpoint_manifest_mismatch', '封存SHA清单数量与批次不一致')
            for index, item in enumerate(declared_manifest, 1):
                _need(isinstance(item, dict) and item.get('file') == f'batch_{index:04d}.json' and
                      isinstance(item.get('sha256'), str) and re.fullmatch(r'[0-9a-f]{64}', item['sha256']) and
                      _integer(item.get('bytes'), 1) and type(item.get('verified')) is bool and not item.get('parseError'),
                      'checkpoint_manifest_mismatch', '封存SHA清单字段无效、重复、断档或记录了损坏批次')
        if 'captureSession' in evidence:
            session = evidence['captureSession']
            _need(isinstance(session, dict) and type(session.get('schemaVersion')) is int and session['schemaVersion'] == 1 and
                  isinstance(session.get('sessionId'), str) and bool(session['sessionId'].strip()) and
                  (session.get('parentSessionId') is None or isinstance(session['parentSessionId'], str)) and
                  _integer(session.get('freshRetryIndex')) and type(session.get('recoveredFromDurableCheckpoints')) is bool and
                  session.get('freshWindowOnly') is True and type(session.get('automaticRetriesPerformed')) is int and session['automaticRetriesPerformed'] == 0,
                  'invalid_capture_session', '封存会话字段无效或试图合并不同采集窗口')
        maximum_scroll = evidence.get('maxScrollViewports')
        _need(_finite(maximum_scroll) and 0 < maximum_scroll <= 100, 'invalid_scroll_limit', '缺少有效的正常滚动范围；每次移动仍限已读取覆盖')
        slots, rows, position_anchors, verified, checks = {}, {}, {}, [], []
        previous_time, terminal_failure, total_bytes = None, False, fingerprint['byte_count']
        for index, batch_path in enumerate(paths, 1):
            _need(batch_path.resolve().parent == directory.resolve(), 'unsafe_checkpoint_path', '批次文件超出来源目录')
            batch, fingerprint = _read(batch_path)
            total_bytes += fingerprint['byte_count']
            _need(total_bytes <= MAX_EVIDENCE_BYTES, 'evidence_too_large', '整轮证据超过读取限制')
            receipt['source_hashes'].append({'role': 'batch', 'batch': index, **fingerprint})
            if declared_manifest is not None:
                item = declared_manifest[index - 1]
                _need(item['sha256'] == fingerprint['sha256'] and item['bytes'] == fingerprint['byte_count'] and
                      item['verified'] == (not bool(batch.get('stop'))),
                      'checkpoint_manifest_mismatch', '封存批次SHA、长度或已核验状态不匹配')
            _need(type(batch.get('batch')) is int and batch['batch'] == index and not terminal_failure,
                  'batch_sequence_gap', '批次编号不连续或终止后仍有批次')
            moment = _time(batch.get('at'))
            _need(previous_time is None or moment >= previous_time, 'unordered_time', '批次时间倒退')
            previous_time = moment
            checks.append({'at': batch['at'], 'pageUrl': batch.get('pageUrl'), 'verified': batch.get('shopIdentityVerified'), 'stop': batch.get('stop')})
            if batch.get('stop'):
                _need(index == n and snapshot['status'] == 'partial' and isinstance(batch['stop'], str) and
                      batch.get('complete') is False and batch.get('added') == 0 and batch.get('seen') == len(rows),
                      'invalid_failed_batch', '失败批次被用于完整轮或继续累加卡片')
                terminal_failure = True
                receipt['warnings'].append('末次失败批次仅作为停止证据；原卡取此前已核验前缀')
                continue
            _need(not verified or verified[-1]['complete'] is False, 'batch_after_complete', '已结束后又追加批次')
            slots, rows, position_anchors = _replay(batch, verified[-1] if verified else None, slots, rows, position_anchors, expected, _origin(snapshot['sourceUrl']), maximum_scroll)
            verified.append(batch)
        _need(verified and rows, 'empty_capture', '没有已核验就绪原卡；应保留失败日志')
        _need(type(evidence.get('verifiedBatchCount')) is int and evidence['verifiedBatchCount'] == len(verified),
              'verified_batch_count_mismatch', '已核验批次数量不一致')
        _need(_same_json(evidence.get('shopIdentityChecks'), checks) and evidence.get('lastAttemptAt') == batch['at'],
              'attempt_manifest_mismatch', '快照身份/末次尝试凭证与批次不对应')
        _need(snapshot.get('observedFrom') == verified[0]['at'] and snapshot.get('observedTo') == verified[-1]['at'],
              'window_mismatch', '快照观察窗口不是实际已核验批次窗口')
        expected_scrolls = [{'at': b['at'], 'count': b['seen'], 'pending': b['pending'], 'scrollTop': b['scrollTop'], 'end': b['endBoundaryObserved']} for b in verified]
        _need(_same_json(snapshot.get('scrolls'), expected_scrolls), 'scroll_manifest_mismatch', '快照滚动摘要与批次不对应')
        pending = [entry for entry in slots.values() if entry['state'] == 'pending']
        _need(type(evidence.get('pendingSlotCount')) is int and evidence['pendingSlotCount'] == len(pending) and
              _same_json(evidence.get('pendingSlots'), pending), 'pending_mismatch', '快照未就绪槽位数量或内容不一致')
        expected_rows = sorted(rows.values(), key=lambda r: (r['position']['top'], r['position']['left']))
        expected_rows = [{**row, 'viewOrder': i} for i, row in enumerate(expected_rows, 1)]
        _need(_same_json(snapshot.get('rows'), expected_rows), 'snapshot_rows_mismatch', '快照原卡、位置或首次/最后读取时间与批次不一致')
        _need(snapshot.get('endBoundaryObserved') is verified[-1]['endBoundaryObserved'], 'last_batch_mismatch', '快照结束标记与末次已核验批次不符')
        reason = evidence.get('stopReason')
        if snapshot['status'] == 'complete':
            _need(verified[-1]['complete'] is True and not terminal_failure and not pending and reason is None,
                  'false_complete', '完整声明缺少真实可见结束、就绪覆盖或仍存在停止原因')
        else:
            _need(isinstance(reason, str) and bool(reason.strip()) and len(reason) <= 2048 and not any(ord(c) < 32 for c in reason),
                  'missing_partial_reason', '部分采集必须保留明确停止原因')
        digest_input = json.dumps(receipt['source_hashes'], sort_keys=True, separators=(',', ':')).encode('utf-8')
        receipt.update(status='passed', valid=True, verified_status=snapshot['status'], batch_count=n,
                       verified_batch_count=len(verified), card_count=len(rows), pending_slot_count=len(pending),
                       stop_reason=reason, evidence_sha256=hashlib.sha256(digest_input).hexdigest(),
                       checkpoint_layout='adjacent_batches' if directory.name == 'batches' else 'same_directory',
                       observed_from=snapshot['observedFrom'], observed_to=snapshot['observedTo'],
                       summary='完整采集证据通过' if snapshot['status'] == 'complete' else '部分采集证据通过；保留停止原因，不作为完整覆盖')
        receipt['checks'] = [{'code': code, 'passed': True} for code in (
            'bounded_adjacent_sources', 'continuous_batch_sequence', 'same_shop', 'column_slot_coverage',
            'scroll_coverage', 'snapshot_batch_content_and_times', 'visible_end_or_explicit_partial', 'source_hash_binding')]
    except (_Invalid, OSError, TypeError, KeyError, OverflowError, RecursionError) as error:
        receipt['errors'].append({'code': getattr(error, 'code', 'malformed_evidence'), 'message': str(error)[:500]})
    return receipt
