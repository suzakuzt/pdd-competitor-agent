"""Public SSR work-heat evidence, separate from artists' popularity and shop data."""
from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from urllib.parse import urlsplit, urljoin
from urllib.request import Request, HTTPRedirectHandler, build_opener
import uuid

SOURCE_URL = 'https://www.iqiyi.com/ranks1/-1/0'
SOURCE_ID = 'iqiyi_public_ssr'
LIST_ID = 'iqiyi_hot_all_ssr_first_batch_v1'
METRIC_ID = 'iqiyi_work_realtime_heat'
QUERY_IDS = ('artist_heat_people', 'artist_heat_history', 'artist_heat_summary', 'artist_heat_sources')
MAX_BYTES = 5 * 1024 * 1024
TIMEOUT = 25
BEIJING = timezone(timedelta(hours=8))
TIME_ASSUMPTION = '页面未注明年份/时区；MM-DD HH:mm按Asia/Shanghai和采集时间最近年份候选解释，非来源完整时间戳'
CAVEAT = '作品实时热度不是艺人个人热度；不相加、不归因个人，不代表新出道、销量或商用授权。仅保存公开SSR首批可见卡；未出现不等于退榜或零热度。'


def _now():
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')


def _dt(value):
    if not isinstance(value, str):
        raise ValueError('Timestamp must be a timezone-aware ISO string')
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        raise ValueError('Timestamp must include timezone')
    return parsed.astimezone(timezone.utc)


def _iso(value):
    return value.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z')


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _safe_url(value, *, fixed_host=False):
    if not isinstance(value, str) or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in value):
        raise ValueError('Unsafe source URL')
    p = urlsplit(value)
    if p.scheme != 'https' or not p.hostname or p.username or p.password or p.port not in (None, 443):
        raise ValueError('Source URL must be HTTPS without credentials')
    if fixed_host and p.hostname != 'www.iqiyi.com':
        raise ValueError('Source host must remain www.iqiyi.com')
    return value


class _Node:
    def __init__(self, tag, attrs=()):
        self.tag, self.attrs, self.children = tag, dict(attrs), []

    @property
    def classes(self):
        return self.attrs.get('class', '').split()

    def text(self):
        if self.tag in ('script', 'style', 'template'):
            return ''
        return ''.join(c if isinstance(c, str) else c.text() for c in self.children)

    def walk(self):
        if self.tag in ('script', 'style', 'template'):
            return
        yield self
        for child in self.children:
            if isinstance(child, _Node):
                yield from child.walk()


class _SSRParser(HTMLParser):
    VOID = {'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'param', 'source', 'track', 'wbr'}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = _Node('root')
        self.stack = [self.root]

    def handle_starttag(self, tag, attrs):
        node = _Node(tag, attrs)
        self.stack[-1].children.append(node)
        if tag not in self.VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.stack[-1].children.append(_Node(tag, attrs))

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                break

    def handle_data(self, data):
        self.stack[-1].children.append(data)


def _class(node, name):
    return [n for n in node.walk() if name in n.classes]


def _source_time(raw, captured_at):
    captured = _dt(captured_at)
    match = re.fullmatch(r'(\d{2})-(\d{2})\s+(\d{2}):(\d{2})', raw or '')
    if not match:
        return None, 'unparsed'
    month, day, hour, minute = map(int, match.groups())
    candidates = []
    for year in range(captured.astimezone(BEIJING).year - 1, captured.astimezone(BEIJING).year + 2):
        try:
            candidates.append(datetime(year, month, day, hour, minute, tzinfo=BEIJING).astimezone(timezone.utc))
        except ValueError:
            pass
    if not candidates:
        return None, 'invalid_date'
    candidate = min(candidates, key=lambda t: abs(t - captured))
    lag = captured - candidate
    status = 'trusted_with_timezone_assumption'
    if lag < -timedelta(minutes=5):
        status = 'future_more_than_5_minutes'
    elif lag > timedelta(hours=48):
        status = 'older_than_48_hours'
    return _iso(candidate), status


def parse_iqiyi_html(html_bytes, captured_at):
    """Parse visible SSR card markup only; scripts/NUXT payloads are never read."""
    _dt(captured_at)
    if not isinstance(html_bytes, bytes) or not 0 < len(html_bytes) <= MAX_BYTES:
        raise ValueError('HTML must be non-empty bytes at most 5 MiB')
    parser = _SSRParser()
    parser.feed(html_bytes.decode('utf-8-sig', errors='strict'))
    metas = [' '.join(n.text().split()) for n in _class(parser.root, 'erji__meta__txt')]
    metas = [m for m in metas if '按实时热度排行' in m]
    selected = [n for n in parser.root.walk() if n.tag == 'a' and 'selected' in n.classes
                and n.attrs.get('href') == '/ranks1/-1/0']
    board_selected = any('rtab__slider__link' in n.classes and n.text().strip() == '总榜' for n in selected)
    metric_selected = any('rtab__sub__link' in n.classes and n.text().strip() == '热播榜' for n in selected)
    if len(metas) != 1 or not board_selected or not metric_selected:
        raise ValueError('Expected public hot-all SSR board and one update label')
    match = re.search(r'最近更新\s*(\d{2}-\d{2}\s+\d{2}:\d{2})', metas[0])
    raw_time = match.group(1) if match else metas[0]
    timestamp, time_status = _source_time(raw_time, captured_at)
    cards = [n for n in parser.root.walk() if n.tag == 'a' and 'rvi__box' in n.classes
             and n.attrs.get('data-block-v2') == 'detailrank.0']
    if not cards or len(cards) > 500:
        raise ValueError('No supported SSR cards or unreasonable card count')
    works, ranks, urls = [], set(), set()
    for card in cards:
        title_nodes, type_nodes = _class(card, 'rvi__tit1'), _class(card, 'rvi__type1')
        labels, values = _class(card, 'rvi__index__txt'), _class(card, 'rvi__index__num')
        if len(title_nodes) != 1 or len(type_nodes) != 1 or len(labels) != 1 or len(values) != 1:
            raise ValueError('SSR work fields are missing or ambiguous')
        title = title_nodes[0].attrs.get('title', '').strip()
        raw_type = type_nodes[0].attrs.get('title') or ' '.join(type_nodes[0].text().split())
        label, raw_value = labels[0].text().strip(), values[0].text().strip()
        rank_raw = card.attrs.get('data-rseat-v2', '')
        if not title or not re.fullmatch(r'[1-9]\d*', rank_raw) or label != '实时热度':
            raise ValueError('Invalid title, rank or changed metric')
        rank = int(rank_raw)
        url = _safe_url(urljoin(SOURCE_URL, card.attrs.get('href', '')), fixed_host=True)
        if not re.fullmatch(r'/v_[A-Za-z0-9]+\.html', urlsplit(url).path):
            raise ValueError('Unsupported work URL')
        if urlsplit(url).query or urlsplit(url).fragment or rank in ranks or url in urls:
            raise ValueError('Duplicate or ambiguous work/rank')
        ranks.add(rank)
        urls.add(url)
        pieces = [p.strip() for p in raw_type.split('/')]
        content_type = pieces[0] if pieces else None
        cast_names = []
        if content_type in ('电影', '电视剧', '综艺') and len(pieces) == 4:
            cast_names = list(dict.fromkeys(pieces[-1].split()))
            if any(not n or len(n) > 40 or re.search(r'[\d<>]', n) for n in cast_names):
                cast_names = []
        value = int(raw_value) if re.fullmatch(r'\d+', raw_value) else None
        works.append({'work_url': url, 'title': title, 'rank': rank,
                      'value': value, 'value_raw': raw_value,
                      'value_precision': 'exact_display' if value is not None else 'unparsed_or_missing',
                      'metric_label': label, 'content_type_raw': content_type, 'type_raw': raw_type,
                      'cast_names': cast_names, 'cast_basis': 'source_card_metadata_last_segment' if cast_names else 'not_available'})
    works.sort(key=lambda r: r['rank'])
    if [w['rank'] for w in works] != list(range(1, len(works) + 1)):
        raise ValueError('SSR first batch must have contiguous original ranks starting at 1')
    payload = {'schema_version': 1, 'source_id': SOURCE_ID, 'source_url': SOURCE_URL,
               'list_id': LIST_ID, 'metric_id': METRIC_ID, 'metric_scope': 'work',
               'coverage': 'ssr_visible_first_batch', 'full_board_complete': False,
               'source_updated_raw': raw_time, 'source_timestamp': timestamp,
               'timezone_assumption': TIME_ASSUMPTION, 'time_status': time_status,
               'source_meta_raw': metas[0], 'works': works}
    return payload


def _content(snapshot):
    # Capture-dependent validation status is evidence, not a new source period.
    return {k: deepcopy(snapshot[k]) for k in ('schema_version', 'source_id', 'source_url', 'list_id',
            'metric_id', 'metric_scope', 'coverage', 'full_board_complete', 'source_updated_raw',
            'source_timestamp', 'source_meta_raw', 'works')}


def _write_new(path, raw):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.writing-', delete=False) as stream:
            temp = Path(stream.name)
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temp, path)  # Exclusive atomic publish, never replace an existing file.
    finally:
        if temp and temp.exists():
            temp.unlink()


def _load_snapshots(project):
    project = Path(project).resolve()
    folder = project / 'state/artist_heat/snapshots'
    snapshots, files, periods = [], {}, {}
    if not folder.exists():
        return snapshots, files
    for path in sorted(folder.glob('*.json')):
        raw = path.read_bytes()
        data = json.loads(raw)
        if (data.get('schema_version') != 1 or data.get('source_id') != SOURCE_ID
                or data.get('source_url') != SOURCE_URL or data.get('list_id') != LIST_ID
                or data.get('metric_id') != METRIC_ID):
            raise ValueError('Unsupported heat snapshot')
        digest = _sha(_canonical(_content(data)))
        if data.get('content_sha256') != digest or data.get('snapshot_id') != 'iqiyi_' + digest[:24]:
            raise ValueError('Heat snapshot content checksum mismatch')
        source_sha = data.get('html_sha256', '')
        if not re.fullmatch('[0-9a-f]{64}', source_sha):
            raise ValueError('Invalid source evidence hash')
        expected_relative = f'sources/artist_heat/{source_sha}.html'
        if data.get('html_file') != expected_relative:
            raise ValueError('Invalid source evidence path')
        source_path = project / expected_relative
        html = source_path.read_bytes()
        if _sha(html) != source_sha:
            raise ValueError('Heat raw HTML checksum mismatch')
        reparsed = parse_iqiyi_html(html, data['captured_at'])
        if _content(reparsed) != _content(data) or reparsed['time_status'] != data.get('time_status'):
            raise ValueError('Heat snapshot does not match saved public SSR evidence')
        period = (data['source_updated_raw'], data['source_timestamp'])
        if period in periods:
            raise ValueError('Duplicate/conflicting saved heat period')
        periods[period] = digest
        snapshots.append(data)
        files[str(path)] = _sha(raw)
        files[str(source_path)] = source_sha
    snapshots.sort(key=lambda s: (_dt(s['captured_at']), s['snapshot_id']))
    return snapshots, files


def _attempt(project, receipt):
    value = dict(receipt, attempt_id='heat_attempt_' + uuid.uuid4().hex, attempted_at=_now(),
                 request_attempts=receipt.get('request_attempts', 0), automatic_retry=False)
    path = Path(project) / 'state/artist_heat/attempts' / (value['attempt_id'] + '.json')
    _write_new(path, _canonical(value))
    return value


def save_html_snapshot(project, html_bytes, captured_at):
    """Archive an already obtained response; no network. Replay is idempotent."""
    project = Path(project).resolve()
    parsed = parse_iqiyi_html(html_bytes, captured_at)
    digest, html_sha = _sha(_canonical(_content(parsed))), _sha(html_bytes)
    snapshot_id = 'iqiyi_' + digest[:24]
    state = project / 'state/artist_heat'
    state.mkdir(parents=True, exist_ok=True)
    lock = state / '.import.lock'
    try:
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise ValueError('Heat import already locked; inspect owner before explicit recovery') from exc
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
            json.dump({'pid': os.getpid(), 'started_at': _now()}, stream)
        existing, _ = _load_snapshots(project)
        for old in existing:
            if old['content_sha256'] == digest:
                return {'status': 'duplicate', 'snapshot_id': old['snapshot_id'],
                        'snapshot_path': str(project / 'state/artist_heat/snapshots' / (old['snapshot_id'] + '.json')),
                        'content_sha256': digest, 'html_sha256': old['html_sha256'],
                        'captured_at': old['captured_at'], 'source_timestamp': old['source_timestamp'],
                        'work_count': len(old['works']), 'history_preserved': True}
            if (old['source_updated_raw'], old['source_timestamp']) == (parsed['source_updated_raw'], parsed['source_timestamp']):
                raise ValueError('Conflicting work content at the same source update time')
        relative_html = f'sources/artist_heat/{html_sha}.html'
        html_path = project / relative_html
        if html_path.exists():
            if html_path.read_bytes() != html_bytes:
                raise ValueError('Existing HTML evidence hash conflict')
        else:
            _write_new(html_path, html_bytes)
        saved = dict(parsed, snapshot_id=snapshot_id, content_sha256=digest,
                     html_sha256=html_sha, html_file=relative_html, captured_at=_iso(_dt(captured_at)),
                     saved_at=_now(), capture_method='public_http_ssr_html_no_script_execution')
        target = state / 'snapshots' / (snapshot_id + '.json')
        _write_new(target, _canonical(saved))
        return {'status': 'saved', 'snapshot_id': snapshot_id, 'snapshot_path': str(target),
                'content_sha256': digest, 'html_sha256': html_sha, 'captured_at': saved['captured_at'],
                'source_timestamp': saved['source_timestamp'], 'work_count': len(saved['works']),
                'history_preserved': True}
    finally:
        lock.unlink()


class _SameHostRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _safe_url(newurl, fixed_host=True)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _http_fetch(url, *, timeout, max_bytes):
    request = Request(url, headers={'User-Agent': 'PDDCompetitorAgent/1.0 public source reader',
                                    'Accept': 'text/html', 'Accept-Encoding': 'identity'})
    with build_opener(_SameHostRedirect()).open(request, timeout=timeout) as response:
        _safe_url(response.geturl(), fixed_host=True)
        length = response.headers.get('Content-Length')
        if length and int(length) > max_bytes:
            raise ValueError('Source HTML exceeds 5 MiB')
        body = response.read(max_bytes + 1)
        return {'body': body, 'url': response.geturl(), 'status': response.status}


def fetch_iqiyi_snapshot(project, fetcher=None):
    """One fixed public request, no cookies or automatic retry. Failures keep history."""
    try:
        result = (fetcher or _http_fetch)(SOURCE_URL, timeout=TIMEOUT, max_bytes=MAX_BYTES)
        if isinstance(result, bytes):
            body = result
        else:
            _safe_url(result['url'], fixed_host=True)
            if result.get('status') != 200:
                raise ValueError('Source did not return HTTP 200')
            body = result['body']
        receipt = save_html_snapshot(project, body, _now())
        receipt['request_attempts'] = 1
        return _attempt(project, receipt)
    except Exception as exc:
        return _attempt(project, {'status': 'failed', 'error_type': type(exc).__name__,
                                 'error': str(exc)[:500], 'history_preserved': True, 'request_attempts': 1})


def _profile_data(project):
    path = Path(project).resolve() / 'state/artist_heat/profiles.json'
    if not path.exists():
        return {}, {}
    raw = path.read_bytes()
    data = json.loads(raw)
    if data.get('schema_version') != 1 or not isinstance(data.get('profiles'), list):
        raise ValueError('Unsupported artist heat profiles')
    profiles = {}
    for row in data['profiles']:
        name = row.get('name')
        if not isinstance(name, str) or not name.strip() or name in profiles:
            raise ValueError('Invalid or duplicate profile name')
        if row.get('official_platform_profile_url'):
            _safe_url(row['official_platform_profile_url'])
        profiles[name] = row
    return profiles, {str(path): _sha(raw)}


def _latest_attempt(project):
    folder = Path(project) / 'state/artist_heat/attempts'
    attempts, files = [], {}
    if folder.exists():
        for path in sorted(folder.glob('*.json')):
            raw = path.read_bytes()
            row = json.loads(raw)
            _dt(row['attempted_at'])
            attempts.append(row)
            files[str(path.resolve())] = _sha(raw)
    return max(attempts, key=lambda r: _dt(r['attempted_at'])) if attempts else None, files


def _change(current, previous):
    result = {'change_status': 'first_observation', 'change_label': '作品首次观察', 'delta': None,
              'previous_value': None, 'previous_snapshot_id': None, 'previous_source_timestamp': None,
              'comparison_reason': 'no_previous_same_work_observation'}
    if previous is None:
        if current['time_status'] != 'trusted_with_timezone_assumption':
            result.update(change_status='unknown', change_label='作品变化未知', comparison_reason='untrusted_source_time')
        elif current['value'] is None:
            result.update(change_status='unknown', change_label='作品变化未知', comparison_reason='missing_or_imprecise_value')
        return result
    result.update(previous_value=previous['value'], previous_snapshot_id=previous['snapshot_id'],
                  previous_source_timestamp=previous['source_timestamp'])
    reason = None
    if any(x['time_status'] != 'trusted_with_timezone_assumption' for x in (current, previous)):
        reason = 'untrusted_source_time'
    elif _dt(current['source_timestamp']) <= _dt(previous['source_timestamp']):
        reason = 'source_time_not_strictly_increasing'
    elif current['value'] is None or previous['value'] is None:
        reason = 'missing_or_imprecise_value'
    if reason:
        result.update(change_status='unknown', change_label='作品变化未知', comparison_reason=reason)
    else:
        delta = current['value'] - previous['value']
        state, label = ('up', '作品热度上升') if delta > 0 else ('down', '作品热度下降') if delta < 0 else ('flat', '作品热度持平')
        result.update(change_status=state, change_label=label, delta=delta,
                      comparison_reason='same_source_list_work_metric_strictly_ordered')
    return result


def build_heat_queries(project, catalog=None, now=None):
    """Read-only global queries. A missing state folder does not create any files."""
    project = Path(project).resolve()
    as_of = _dt(now or _now())
    snapshots, files = _load_snapshots(project)
    catalog_path = project / 'state/artist_research/catalog.json'
    if catalog_path.exists():
        catalog_raw = catalog_path.read_bytes()
        on_disk_catalog = json.loads(catalog_raw)
        if catalog is None:
            catalog = on_disk_catalog
        if catalog == on_disk_catalog:
            files[str(catalog_path)] = _sha(catalog_raw)
    profiles, profile_files = _profile_data(project)
    files.update(profile_files)
    attempt, attempt_files = _latest_attempt(project)
    files.update(attempt_files)
    history, previous = [], {}
    # Capture order is explicit: a late/cache response cannot be silently rewritten as current growth.
    for snapshot in snapshots:
        for work in snapshot['works']:
            row = dict(work, **{k: snapshot[k] for k in ('snapshot_id', 'source_id', 'list_id', 'metric_id',
                       'metric_scope', 'source_timestamp', 'source_updated_raw', 'time_status',
                       'timezone_assumption', 'captured_at', 'coverage', 'full_board_complete')})
            row['snapshot_sha256'] = snapshot['content_sha256']
            row['html_sha256'] = snapshot['html_sha256']
            row['history_id'] = snapshot['snapshot_id'] + ':' + _sha(work['work_url'].encode())[:16]
            key = tuple(row[k] for k in ('source_id', 'list_id', 'metric_id', 'work_url'))
            row.update(_change(row, previous.get(key)))
            history.append(row)
            previous[key] = row
    latest = snapshots[-1] if snapshots else None
    current_rows = [r for r in history if latest and r['snapshot_id'] == latest['snapshot_id']]
    directory = defaultdict(list)
    for entity in (catalog or {}).get('entities', []):
        if entity.get('entity_kind') == 'artist':
            directory[entity['name']].append(entity['entity_id'])
    by_person = defaultdict(list)
    first_seen = {}
    for row in history:
        for name in row['cast_names']:
            first_seen.setdefault(name, row['captured_at'])
    for row in current_rows:
        for name in row['cast_names']:
            by_person[name].append(row)
    people = []
    for name, works in by_person.items():
        ids = directory.get(name, [])
        profile = profiles.get(name, {})
        examples = profile.get('other_work_examples')
        if isinstance(examples, list):
            examples = '、'.join(map(str, examples))
        identity = profile.get('identity_short')
        brief = '爱奇艺作品卡公开列出的演职员或参与者；未核实完整个人资料'
        if identity:
            brief = str(identity) + (f'；可从《{examples}》认识' if examples else '')
        people.append({'person_id': 'iqiyi_cast_' + _sha(name.encode('utf-8'))[:24], 'name': name,
                       'directory_entity_id': ids[0] if len(ids) == 1 else None,
                       'in_artist_directory': len(ids) == 1, 'identity_status': 'source_cast_name_only',
                       'identity_brief': brief, 'person_url': profile.get('official_platform_profile_url'),
                       'profile_basis': profile.get('identity_source_status'),
                       'profile_checked_date': profile.get('checked_date'),
                       'where_from': '爱奇艺热播总榜公开SSR首批作品卡演职员列',
                       'first_observed_at': first_seen[name], 'last_observed_at': latest['captured_at'],
                       'latest_snapshot_id': latest['snapshot_id'], 'associated_works': deepcopy(works),
                       'newcomer_meaning': '目录外认识候选，不表示新出道或本人热度上涨',
                       'personal_heat': None, 'personal_heat_change': None, 'global_scope': True})
    people.sort(key=lambda p: (min(w['rank'] for w in p['associated_works']), p['name']))
    captured = latest['captured_at'] if latest else None
    timestamp = latest['source_timestamp'] if latest else None
    fresh, fresh_note = 'unknown', '尚无成功抓取；不能判断新鲜度。'
    if latest:
        age = as_of - _dt(captured)
        if age < -timedelta(minutes=5):
            fresh_note = '保存的抓取时间晚于当前时间，新鲜度未知。'
        elif age > timedelta(hours=24):
            fresh, fresh_note = 'stale', '距上次成功抓取超过24小时，展示历史记录；24小时是本地提醒阈值，不是平台标准。'
        else:
            fresh, fresh_note = 'recent', '距上次成功抓取不超过24小时；这是本地提醒阈值，不代表平台实时性，仍须查看榜单原时间。'
    summary = {'source_id': SOURCE_ID, 'status': 'ready' if latest else 'not_configured',
               'snapshot_count': len(snapshots), 'latest_snapshot_id': latest['snapshot_id'] if latest else None,
               'latest_source_timestamp': timestamp, 'latest_source_updated_raw': latest['source_updated_raw'] if latest else None,
               'latest_captured_at': captured, 'work_count': len(current_rows), 'person_count': len(people),
               'directory_person_count': sum(p['in_artist_directory'] for p in people),
               'new_to_directory_count': sum(not p['in_artist_directory'] for p in people),
               'comparison_available_count': sum(r['change_status'] in ('up', 'down', 'flat') for r in current_rows),
               'first_observation_count': sum(r['change_status'] == 'first_observation' for r in current_rows),
               'unknown_comparison_count': sum(r['change_status'] == 'unknown' for r in current_rows),
               'coverage': 'ssr_visible_first_batch', 'full_board_complete': False, 'global_scope': True,
               'last_attempt_status': attempt['status'] if attempt else ('saved_offline' if latest else None),
               'last_attempt_at': attempt['attempted_at'] if attempt else None,
               'last_error': attempt.get('error') if attempt else None, 'freshness_status': fresh,
               'freshness_note': fresh_note, 'as_of': _iso(as_of),
               'time_status': latest['time_status'] if latest else None, 'caveat': CAVEAT}
    sources = [{'source_id': SOURCE_ID, 'name': '爱奇艺热播总榜·作品实时热度', 'url': SOURCE_URL,
                'owner': '爱奇艺', 'status': summary['last_attempt_status'] or 'not_configured',
                'metric_id': METRIC_ID, 'metric_label': '实时热度', 'metric_scope': 'work',
                'metric_definition': '页面公布的作品实时热度原值；完整计算公式、统计窗口和去重规则未核实。',
                'scope': '热播总榜公开SSR首批可见作品卡；不是完整全榜',
                'latest_source_timestamp': timestamp, 'latest_captured_at': captured,
                'last_attempt_at': summary['last_attempt_at'], 'last_error': summary['last_error'],
                'freshness_status': fresh, 'freshness_note': fresh_note,
                'timezone_assumption': TIME_ASSUMPTION, 'global_scope': True,
                'caveat': CAVEAT}]
    source = {'provider': '爱奇艺公开SSR页面', 'url': SOURCE_URL, 'files': files,
              'tables': [], 'filters': ['公共作品榜与艺人认识线索；不按店铺或商品轮次过滤'],
              'executedAt': _now(), 'grain': 'one work per source update; people are source cast-name references',
              'caveats': [CAVEAT, TIME_ASSUMPTION],
              'metricDefinitions': [{'id': METRIC_ID, 'label': '作品实时热度',
                 'description': '只比较同来源、榜、作品URL、指标且可信源时间严格增加的精确值；首次观察无增量，缺值不补零。'}]}
    methods = ['HTMLParser只读取公开SSR卡，不执行脚本或解析NUXT数据',
               '作品精确原值逐期保存；演员只关联作品，无个人总热度',
               'MM-DD HH:mm按最近年份与北京时间解释；过旧/未来时间保留但不比较',
               '已有47人目录仅按artist类的完整名称匹配；目录匹配不是所选店铺存在证明']
    return {key: {'rows': rows, 'source': deepcopy(source), 'methods': methods[:]} for key, rows in zip(
            QUERY_IDS, (people, history, [summary], sources))}
