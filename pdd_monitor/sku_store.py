"""Immutable product-spec captures; independent image BLOBs, never rewrite cards."""
import base64
from contextlib import closing, contextmanager, nullcontext
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import sys
import time
import uuid
from urllib.parse import urlsplit, parse_qs, parse_qsl

from .sales_metrics import SALES_METRIC_LABELS


def sku_eligible(row):
    """Only the two authorized exact item-sales labels enter SKU collection."""
    return (row.get('sales_label') in SALES_METRIC_LABELS
            and row.get('sales_precision') == 'exact_display'
            and row.get('sales_unit') == '件'
            and type(row.get('sales_value')) is int and row['sales_value'] > 0)


def validate_card_binding(capture, row):
    """Require the exact source card, never an arbitrary title-only match."""
    if capture.get('identity_basis') == 'current_unique_card_modal':
        evidence = _validate_list_modal_identity(capture)
        if (type(row.get('current_unique_card_candidate_count')) is not int
                or row['current_unique_card_candidate_count'] != 1
                or any(evidence[key] != row.get(key) for key in (
                    'shop_source_url', 'shop_name', 'title', 'image_url',
                    'view_order', 'observation_id'))):
            raise ValueError('SKU list modal differs from its verified shop or original card')
        if 'search_evidence' in capture:
            search = capture['search_evidence']
            if not sku_eligible(row) or search['live_sales_value'] < row['sales_value']:
                raise ValueError('SKU search sales conflict with the eligible original card')
        return
    if row.get('goods_id'):
        if str(row['goods_id']) != capture.get('goods_id'):
            raise ValueError('SKU product differs from recorded card ID')
        return
    evidence = capture.get('source_card_evidence') or {}
    if (row.get('current_unique_card_candidate_count', 1) != 1
            or not row.get('image_url')
            or evidence.get('title') != row.get('title')
            or evidence.get('image_url') != row.get('image_url')
            or evidence.get('view_order') != row.get('view_order')):
        raise ValueError('SKU capture lacks unique original card evidence')


def _search_page_url(value, path):
    """An exact public route, never a credentials-bearing or ambiguous URL."""
    if (not isinstance(value, str) or not value or value != value.strip()
            or re.search(r'[\x00-\x20\x7f]|%(?![0-9a-fA-F]{2})', value)):
        raise ValueError('SKU search needs an unambiguous public page URL')
    source = urlsplit(value)
    if (source.scheme != 'https' or source.netloc != 'mobile.yangkeduo.com'
            or source.path != path or source.fragment):
        raise ValueError('SKU search page has an invalid origin or route')
    return source, parse_qs(source.query, keep_blank_values=True, errors='strict')


def _search_store_identity(value):
    _, query = _search_page_url(value, '/mall_page.html')
    identities = [key for key in ('mall_id', 'mall_sn') if key in query]
    if (not identities or any(len(query[key]) != 1 or not query[key][0].strip()
            or query[key][0] != query[key][0].strip() for key in identities)
            or ('mall_id' in query and not re.fullmatch(r'[1-9]\d{0,24}', query['mall_id'][0]))):
        raise ValueError('SKU search entry needs its typed canonical storefront')
    kind = 'mall_id' if 'mall_id' in query else 'mall_sn'
    return kind, query[kind][0]


def _same_search_card_image(original, matched):
    if not isinstance(matched, str) or not matched:
        return False
    if original == matched:
        return True
    try:
        urls = [urlsplit(value) for value in (original, matched)]
        if any(url.scheme != 'https' or url.netloc not in ('img.pddpic.com', 'img-2.pddpic.com')
                or not url.path or url.fragment for url in urls):
            return False
        if any(re.search(r'[\x00-\x20\x7f]|%(?![0-9a-fA-F]{2})', value)
               for value in (original, matched)):
            return False
        def image_query(url):
            pairs = parse_qsl(url.query, keep_blank_values=True, errors='strict')
            # Observed native search omits this one presentation format prefix.
            # Keep all quality/thumbnail directives, other keys and their order.
            return [(('imageMogr2/' + key[len('imageMogr2/format/webp/'):])
                     if not value and key.startswith('imageMogr2/format/webp/') else key, value)
                    for key, value in pairs]
        # This proves the same original path, not identical response bytes.
        # The actual matched URL is retained separately in search_evidence.
        return (urls[0].path == urls[1].path
                and image_query(urls[0]) == image_query(urls[1]))
    except (TypeError, ValueError):
        return False


def _validate_search_evidence(value, card):
    """Bind native search to this original storefront; do not alias its mall ID."""
    from .store import parse_sales
    search = value['search_evidence']
    required = {'method', 'source_store_url', 'query', 'search_page_url', 'page_id',
                'shop_name_verified', 'native_search_opened', 'native_search_submitted',
                'same_tab_navigation_verified', 'matching_title', 'original_image_url',
                'matched_image_url', 'matched_card_count', 'live_sales_raw', 'live_sales_value',
                'live_sales_label', 'live_sales_unit', 'modal_closed', 'return_verified',
                'return_store_url'}
    if (not isinstance(search, dict) or not required <= search.keys()
            or search['method'] != 'native_store_search'
            or type(search['page_id']) is not int or search['page_id'] < 1
            or any(search[key] is not True for key in ('shop_name_verified',
                'native_search_opened', 'native_search_submitted', 'same_tab_navigation_verified'))
            or not isinstance(search['query'], str) or not 1 <= len(search['query']) <= 120
            or search['query'] != search['query'].strip()
            or re.search(r'[\x00-\x1f\x7f]', search['query'])
            or search['matching_title'] != card['title']
            or search['original_image_url'] != card['image_url']
            or not _same_search_card_image(card['image_url'], search['matched_image_url'])
            or type(search['matched_card_count']) is not int or search['matched_card_count'] != 1):
        raise ValueError('SKU search lacks its native transition and unique original-card evidence')
    identity = _search_store_identity(card['shop_source_url'])
    if _search_store_identity(search['source_store_url']) != identity:
        raise ValueError('SKU native search started from another typed storefront')
    _, query = _search_page_url(search['search_page_url'], '/mall_search_result.html')
    if (len(query.get('mall_id', [])) != 1
            or not re.fullmatch(r'[1-9]\d{0,24}', query['mall_id'][0])
            or query.get('search_key') != [search['query']]):
        raise ValueError('SKU search result needs its observed mall ID and exact submitted query')
    # An original numeric ID can be compared directly. For an opaque mall_sn,
    # only the native same-tab transition above connects this search page.
    if identity[0] == 'mall_id' and query['mall_id'][0] != identity[1]:
        raise ValueError('SKU search result belongs to another numeric storefront')
    if ('mall_sn' in query and (identity[0] != 'mall_sn' or query['mall_sn'] != [identity[1]])):
        raise ValueError('SKU search result has conflicting storefront identifiers')
    raw = search['live_sales_raw']
    sales = parse_sales(raw) if isinstance(raw, str) else {}
    if (sales.get('precision') != 'exact_display' or sales.get('label') not in SALES_METRIC_LABELS
            or sales.get('unit') != '件' or not isinstance(raw, str)
            or type(search['live_sales_value']) is not int or search['live_sales_value'] <= 0
            or any(search['live_sales_' + key] != sales.get(key) for key in ('value', 'label', 'unit'))):
        raise ValueError('SKU search sales need the card own exact positive displayed count')
    if type(search['modal_closed']) is not bool or type(search['return_verified']) is not bool:
        raise ValueError('SKU search cleanup needs explicit verified states')
    returned = search['return_store_url']
    if returned is not None and _search_store_identity(returned) != identity:
        raise ValueError('SKU search returned to another typed storefront')
    if search['return_verified']:
        if not search['modal_closed'] or returned is None:
            raise ValueError('SKU search return cannot precede verified modal closure')
    else:
        reason = 'sku_search_return_failed' if search['modal_closed'] else 'sku_modal_cleanup_failed'
        if (value.get('status') != 'partial' or value.get('all_combinations_visited') is not False
                or value.get('stop_reason') != reason):
            raise ValueError('SKU incomplete search cleanup can only retain an explicit partial capture')
    return search


def _validate_list_modal_identity(value):
    """A rendered list popup has card evidence, never an inferred public ID."""
    if ('goods_id' not in value or 'goods_url' not in value
            or value['goods_id'] is not None or value['goods_url'] is not None
            or value.get('capture_source') != 'shop_card_modal'):
        raise ValueError('SKU list modal must not borrow a public product ID or URL')
    evidence = value.get('list_modal_evidence')
    card = value.get('source_card_evidence')
    required = {'shop_source_url', 'shop_name', 'title', 'image_url', 'view_order',
                'observation_id', 'matched_card_count', 'opened_from_verified_plus',
                'modal_continuity_verified'}
    if (not isinstance(evidence, dict) or not required <= evidence.keys()
            or not isinstance(card, dict)
            or any(not isinstance(evidence[key], str) or not evidence[key].strip()
                   for key in ('shop_source_url', 'shop_name', 'title', 'image_url'))
            or any(type(evidence[key]) is not int or evidence[key] < 1
                   for key in ('view_order', 'observation_id'))
            or type(evidence['matched_card_count']) is not int or evidence['matched_card_count'] != 1
            or evidence['opened_from_verified_plus'] is not True
            or evidence['modal_continuity_verified'] is not True
            or type(card.get('view_order')) is not int
            or any(card.get(key) != evidence[key]
                   for key in ('shop_source_url', 'title', 'image_url', 'view_order'))
            or ('observation_id' in value and (type(value['observation_id']) is not int
                or value['observation_id'] != evidence['observation_id']))):
        raise ValueError('SKU list modal lacks unique card and popup continuity evidence')
    source = urlsplit(evidence['shop_source_url'])
    query = parse_qs(source.query, keep_blank_values=True)
    identities = [key for key in ('mall_id', 'mall_sn') if key in query]
    if (source.scheme != 'https' or source.netloc != 'mobile.yangkeduo.com'
            or source.path != '/mall_page.html' or source.fragment or not identities
            or any(len(query[key]) != 1 or not query[key][0].strip()
                   or query[key][0] != query[key][0].strip() for key in identities)
            or ('mall_id' in query and not re.fullmatch(r'[1-9]\d{0,24}', query['mall_id'][0]))):
        raise ValueError('SKU list modal needs its verified canonical shop source')
    if 'search_evidence' in value:
        _validate_search_evidence(value, evidence)
    return evidence


def price_value(raw):
    """One explicit yuan amount only. Ranges/coupons with multiple amounts unknown."""
    if not isinstance(raw, str):
        return None
    matches = re.findall(r'[¥￥]\s*(\d+(?:\.\d{1,2})?)(?![\d.])', raw)
    if len(matches) != 1 or re.search(r'\d\s*[-~—至]\s*[¥￥]?\s*\d|起|最低', raw):
        return None
    return matches[0]


def validate_capture(value):
    required = {'goods_id', 'goods_url', 'observed_at', 'identity_basis', 'status', 'variants'}
    if not isinstance(value, dict) or not required <= set(value):
        raise ValueError('SKU capture lacks evidence')
    if value['identity_basis'] == 'current_unique_card_modal':
        _validate_list_modal_identity(value)
    else:
        if (value.get('capture_source') == 'shop_card_modal' or 'list_modal_evidence' in value
                or 'search_evidence' in value
                or not isinstance(value['goods_url'], str)):
            raise ValueError('SKU public-link evidence cannot be replaced by list-modal evidence')
        url = urlsplit(value['goods_url'])
        ids = parse_qs(url.query).get('goods_id', [])
        if (url.scheme != 'https' or url.netloc != 'mobile.yangkeduo.com'
                or url.path not in ('/goods.html', '/goods1.html') or url.fragment
                or ids != [value['goods_id']] or not isinstance(value['goods_id'], str)
                or not re.fullmatch(r'[1-9]\d{0,24}', value['goods_id'])):
            raise ValueError('SKU capture requires visible public product URL')
    try:
        observed = datetime.fromisoformat(value['observed_at'].replace('Z', '+00:00'))
    except (ValueError, TypeError, AttributeError) as error:
        raise ValueError('SKU capture needs an actual timestamp') from error
    if observed.tzinfo is None:
        raise ValueError('SKU capture timestamp needs timezone')
    if value['status'] not in ('complete', 'partial') or value['identity_basis'] not in ('recorded_public_goods_link', 'current_unique_card_candidate', 'current_unique_card_modal'):
        raise ValueError('SKU capture scope/status invalid')
    rows = value['variants']
    if not isinstance(rows, list) or not 1 <= len(rows) <= 300:
        raise ValueError('Bounded nonempty SKU list required')
    keys = set()
    for row in rows:
        specs = row.get('specs')
        if not isinstance(specs, list) or not specs or any(not isinstance(s, dict) or not s.get('name') or not s.get('value') for s in specs):
            raise ValueError('Selected spec combination required')
        key = json.dumps(specs, sort_keys=True, ensure_ascii=False)
        if key in keys:
            raise ValueError('Duplicate SKU combination')
        keys.add(key)
        if row.get('available') not in (True, False, None):
            raise ValueError('Availability unknown must stay unknown')
        row['current_price_raw'] = row.get('current_price_raw', row.get('price_raw'))
        row['current_price_yuan'] = price_value(row['current_price_raw']) if row.get('selection_verified') is True else None
        row['price_yuan'] = row['current_price_yuan']
        # A product headline, historical price or crossed-out text elsewhere is
        # never an original price for this selected SKU.
        original_evidence = row.get('original_price_evidence')
        row['original_price_yuan'] = (price_value(row.get('original_price_raw'))
            if row.get('selection_verified') is True and original_evidence in (
                'explicit_original_price_label', 'struck_through_price',
                'explicit_before_coupon_price') else None)
        if row.get('sku_id') is not None and not re.fullmatch(r'[1-9]\d{0,24}', str(row['sku_id'])):
            raise ValueError('Do not invent SKU IDs')
    if value['status'] == 'complete' and (not value.get('all_combinations_visited') or not value.get('option_catalog_verified')):
        raise ValueError('All-SKU completion requires verified rendered option coverage')
    expected = value.get('expected_combinations')
    if expected is not None and (type(expected) is not int or expected < len(rows)
            or (value['status'] == 'complete' and expected != len(rows))):
        raise ValueError('SKU visited count conflicts with its option catalog')
    return value


def _write_capture(directory, capture, assets):
    directory.mkdir(parents=True, exist_ok=False)
    with closing(sqlite3.connect(directory / 'images.sqlite3')) as db:
        db.execute('CREATE TABLE assets(sha256 TEXT PRIMARY KEY, mime TEXT NOT NULL, byte_count INTEGER NOT NULL, data BLOB NOT NULL)')
        used_urls = {row.get('image_url') for row in capture['variants']}
        for url, asset in assets.items():
            if url not in used_urls:
                continue
            data = base64.b64decode(asset['base64'], validate=True)
            mime = asset['mime']
            if mime not in ('image/png', 'image/jpeg', 'image/webp', 'image/gif') or not 0 < len(data) <= 10 * 1024 * 1024:
                continue
            from .store import _image_mime
            if _image_mime(data) != mime:
                raise ValueError('SKU image MIME conflicts with its bytes')
            digest = hashlib.sha256(data).hexdigest()
            db.execute('INSERT OR IGNORE INTO assets VALUES(?,?,?,?)', (digest, mime, len(data), data))
            for row in capture['variants']:
                if row.get('image_url') == url:
                    row['image_sha256'] = digest
        db.commit()
        if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise ValueError('SKU BLOB database failed verification')
    (directory / 'capture.json').write_text(json.dumps(capture, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')


@dataclass(frozen=True)
class SkuBatchBackup:
    project: Path
    workspace: Path
    backup: Path
    source_inventory: dict | None = None
    scope: str = 'full_paired'


class SkuSourceChanged(ValueError):
    """The prepared full backup no longer covers the publication baseline."""


class SkuSaveCancelled(ValueError):
    """Cancellation was observed before immutable publication."""


def _verified_inventory(manifest):
    inventory = manifest.get('source_before')
    if (not isinstance(inventory, dict)
            or not {'data/monitor.sqlite3', 'data/images.sqlite3', 'schema.sql'} <= inventory.keys()
            or inventory != manifest.get('source_after') or inventory != manifest.get('files')
            or any(not isinstance(name, str) or not isinstance(entry, dict)
                   or type(entry.get('bytes')) is not int or entry['bytes'] < 0
                   or not isinstance(entry.get('sha256'), str)
                   or not re.fullmatch(r'[0-9a-f]{64}', entry['sha256'])
                   for name, entry in inventory.items())):
        raise ValueError('Paired backup lacks its complete verified source inventory')
    return deepcopy(inventory)


def prepare_batch_backup(project, workspace):
    """One paired backup/restore drill for one append-only SKU batch."""
    project, workspace = Path(project).resolve(), Path(workspace).resolve()
    workspace.mkdir(parents=True, exist_ok=False)
    backup = workspace / 'paired_backup'
    from .backup_policy import incremental_backup_enabled
    command = [sys.executable, '-B', '-X', 'utf8', str(project / 'scripts/backup_store.py'),
               '--project', str(project), '--output', str(backup)]
    if incremental_backup_enabled(project):
        command.append('--incremental')
    result = subprocess.run(command, capture_output=True, encoding='utf-8', timeout=900)
    if result.returncode:
        raise ValueError('Original paired-backup/restore verification failed')
    manifest = json.loads((backup / 'manifest.json').read_text(encoding='utf-8'))
    if (manifest.get('status') != 'complete' or manifest.get('source_project') != str(project)
            or manifest.get('backup_validation_ok') is not True
            or manifest.get('restore_validation_ok') is not True
            or manifest.get('source_unchanged') is not True):
        raise ValueError('Paired backup success manifest is not verified')
    return SkuBatchBackup(project, workspace, backup, _verified_inventory(manifest))


@contextmanager
def _parallel_publication_guard(project, backup):
    """Check all backed-up bytes once under the original paired write locks."""
    if backup.scope == 'sku_append_only':
        from .sku_append import append_publication_guard
        with append_publication_guard(project, backup):
            yield
        return
    if backup.scope != 'full_paired':
        raise SkuSourceChanged('Unknown SKU publication protection scope')
    from scripts.backup_store import _inventory, _paired_locks
    if backup.source_inventory is None:
        raise SkuSourceChanged('Prepared SKU backup has no proven full source baseline')
    with _paired_locks(project):
        if _inventory(project) != backup.source_inventory:
            raise SkuSourceChanged('Data, schema or sources changed after SKU backup; review before retrying')
        yield


def _check_save_cancelled(cancel_file):
    if cancel_file is not None and Path(cancel_file).exists():
        raise SkuSaveCancelled('SKU cancellation received before publication')


def _publication_card(project, job, capture):
    """Resolve the immutable original card again at the final save boundary."""
    from .store import shop_identity_evidence
    for key, expected in (('shop_id', job['shop_id']),
                          ('observation_id', job['observation_id']),
                          ('capture_id', job['id'])):
        if key in capture and (type(capture[key]) is not type(expected) or capture[key] != expected):
            raise ValueError('SKU capture conflicts with its publication scope')
    database = project / 'data/monitor.sqlite3'
    with closing(sqlite3.connect(database.as_uri() + '?mode=ro', uri=True)) as db:
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA query_only=ON')
        found = db.execute('''SELECT o.*,r.shop_id,r.source_url shop_source_url,s.shop_name
            FROM observations o JOIN runs r ON r.run_id=o.run_id
            JOIN shops s ON s.shop_id=r.shop_id WHERE o.observation_id=? AND r.shop_id=?''',
            (job['observation_id'], job['shop_id'])).fetchone()
        if found is None:
            raise ValueError('SKU original card belongs to another shop or is missing')
        row = dict(found)
        row['current_unique_card_candidate_count'] = db.execute(
            'SELECT COUNT(*) FROM observations WHERE run_id=? AND title=? AND image_url IS ?',
            (row['run_id'], row['title'], row['image_url'])).fetchone()[0]
    if ('source_run_id' in job and job['source_run_id'] != row['run_id']) or not sku_eligible(row):
        raise ValueError('SKU publication does not belong to its eligible source run')
    expected = shop_identity_evidence({'sourceUrl': row['shop_source_url']})
    if expected['shop_id'] != job['shop_id']:
        raise ValueError('SKU original shop source conflicts with its database scope')
    for evidence in (capture.get('list_modal_evidence'), capture.get('source_card_evidence')):
        if isinstance(evidence, dict) and evidence.get('shop_source_url') is not None:
            actual = shop_identity_evidence({'sourceUrl': evidence['shop_source_url']})
            if any(actual[key] != expected[key] for key in ('shop_id', 'identity_kind', 'stable_identifier')):
                raise ValueError('SKU source evidence belongs to another shop')
    # Share parameters may change while the same typed stable storefront remains.
    # The original row and stable identity above remain authoritative.
    if capture.get('identity_basis') == 'current_unique_card_modal':
        row['shop_source_url'] = capture['list_modal_evidence']['shop_source_url']
    validate_card_binding(capture, row)
    return row


def save_capture(project, job, value, assets, workspace, *, batch_backup=None,
                 parallel_prepared=False, cancel_file=None):
    """Rehearse and append an immutable capture; never write either business DB."""
    project, workspace = Path(project).resolve(), Path(workspace).resolve()
    _check_save_cancelled(cancel_file)
    if parallel_prepared and batch_backup is None:
        raise ValueError('Parallel SKU publication requires its completed backup')
    if (not re.fullmatch(r'collect_[0-9a-f]{32}(?:_o[1-9]\d*)?', job['id'])
            or not re.fullmatch(r'shop_[0-9a-f]{24}', job['shop_id'])
            or type(job['observation_id']) is not int or job['observation_id'] < 1):
        raise ValueError('Invalid SKU publication scope')
    capture = validate_capture(deepcopy(value))
    _publication_card(project, job, capture)
    if (capture['identity_basis'] == 'current_unique_card_modal'
            and capture['list_modal_evidence']['observation_id'] != job['observation_id']):
        raise ValueError('SKU list modal publication belongs to another original card')
    scope_note = {
        'current_unique_card_modal': '本店上新原卡标题和主图唯一匹配，规格来自该卡加号弹层；未取得公开商品ID',
        'current_unique_card_candidate': '当下唯一标题及主图候选，未确认与旧原卡同商品',
        'recorded_public_goods_link': '已记录公开商品链接与实际商品ID一致',
    }[capture['identity_basis']]
    if 'search_evidence' in capture:
        scope_note = '从本店原入口进行店内搜索，原标题、主图与实时销量核对唯一原卡，规格来自该卡加号弹层；未取得公开商品ID'
    capture.update(shop_id=job['shop_id'], observation_id=job['observation_id'], capture_id=job['id'],
                   row_scope_note=scope_note)
    if batch_backup is not None and batch_backup.scope == 'sku_append_only':
        capture['publication_mode'] = 'indexed_append_only'
    if batch_backup is None:
        batch_backup = prepare_batch_backup(project, workspace)
        reused = False
    else:
        if (not isinstance(batch_backup, SkuBatchBackup) or batch_backup.project != project
                or not workspace.is_relative_to(batch_backup.workspace)
                or workspace == batch_backup.workspace):
            raise ValueError('SKU batch backup belongs to another publication')
        workspace.mkdir(parents=True, exist_ok=False)
        reused = not parallel_prepared
    stage = workspace / 'sku_rehearsal'
    _write_capture(stage, capture, assets)
    # Read the actual staged immutable bytes and verify every stored image hash.
    checked = _read_capture(stage)
    if checked['capture_id'] != job['id'] or len(checked['variants']) != len(capture['variants']):
        raise ValueError('SKU rehearsal differs from capture')
    baseline_started = time.perf_counter()
    if batch_backup.scope == 'sku_append_only':
        protection_scope = json.loads((batch_backup.backup / 'manifest.json').read_text(encoding='utf-8'))['scope']
        if any(protection_scope.get(key) != job[key] for key in ('id', 'shop_id', 'observation_id')):
            raise ValueError('SKU append protection belongs to another original card')
        if not parallel_prepared:
            raise ValueError('SKU append protection requires a checked publication baseline')
    guard = _parallel_publication_guard(project, batch_backup) if parallel_prepared else nullcontext()
    with guard:
        baseline_seconds = time.perf_counter() - baseline_started if parallel_prepared else 0.0
        _check_save_cancelled(cancel_file)
        # Re-resolve the original card while the parallel baseline locks are held.
        if parallel_prepared:
            _publication_card(project, job, capture)
        destination = project / 'sources/sku_captures' / job['id']
        destination.parent.mkdir(parents=True, exist_ok=True)
        staging = destination.with_name('.' + job['id'] + '.' + uuid.uuid4().hex)
        shutil.copytree(stage, staging)
        _read_capture(staging)
        if batch_backup.scope == 'sku_append_only':
            for path in (staging / 'capture.json', staging / 'images.sqlite3'):
                with path.open('r+b') as stream:
                    stream.flush()
                    os.fsync(stream.fileno())
        _check_save_cancelled(cancel_file)
        staging.rename(destination)
        if batch_backup.scope == 'sku_append_only':
            _check_save_cancelled(cancel_file)
        _publish_latest(project, capture)
    return {'capture_id': job['id'], 'variant_count': len(capture['variants']), 'status': capture['status'],
            'backup_verified': True, 'backup_scope': batch_backup.scope,
            'main_database_writes': False, 'backup_reused_for_batch': reused, 'rehearsal_verified': True,
            'parallel_backup_prepared': parallel_prepared,
            'source_baseline_rechecked': parallel_prepared,
            'source_baseline_check_seconds': round(baseline_seconds, 3),
            'image_count': sum(bool(row.get('image_sha256')) for row in capture['variants'])}


def _publish_latest(project, capture):
    """Small derived pointer; all original capture directories stay immutable."""
    path = project / 'sources/sku_captures/index' / capture['shop_id'] / f"{capture['observation_id']}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    pointer = {key: capture[key] for key in ('capture_id', 'shop_id', 'observation_id', 'observed_at')}
    if path.is_file():
        old = json.loads(path.read_text(encoding='utf-8'))
        if datetime.fromisoformat(old['observed_at'].replace('Z', '+00:00')) > datetime.fromisoformat(pointer['observed_at'].replace('Z', '+00:00')):
            return
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        with temporary.open('x', encoding='utf-8') as stream:
            json.dump(pointer, stream, ensure_ascii=False, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _read_capture(directory):
    directory = Path(directory)
    value = validate_capture(json.loads((directory / 'capture.json').read_text(encoding='utf-8')))
    with closing(sqlite3.connect((directory / 'images.sqlite3').as_uri() + '?mode=ro', uri=True)) as db:
        for row in value['variants']:
            row['image_data_url'] = None
            if not row.get('image_sha256'):
                continue
            asset = db.execute('SELECT mime,byte_count,data FROM assets WHERE sha256=?', (row['image_sha256'],)).fetchone()
            if not asset or len(asset[2]) != asset[1] or hashlib.sha256(asset[2]).hexdigest() != row['image_sha256']:
                raise ValueError('SKU image hash mismatch')
            row['image_data_url'] = 'data:' + asset[0] + ';base64,' + base64.b64encode(asset[2]).decode('ascii')
    return value


def read_latest(project, shop_id, observation_id):
    root = Path(project) / 'sources/sku_captures'
    candidates = []
    if not root.is_dir():
        return None
    pointer_path = root / 'index' / shop_id / f'{observation_id}.json'
    if pointer_path.is_file():
        pointer = json.loads(pointer_path.read_text(encoding='utf-8'))
        capture_id = pointer.get('capture_id')
        if (pointer.get('shop_id') != shop_id or pointer.get('observation_id') != observation_id
                or not isinstance(capture_id, str)
                or not re.fullmatch(r'collect_[0-9a-f]{32}(?:_o[1-9]\d*)?', capture_id)):
            raise ValueError('SKU latest pointer has invalid scope')
        value = _read_capture(root / capture_id)
        if any(value.get(key) != pointer.get(key) for key in ('capture_id', 'shop_id', 'observation_id', 'observed_at')):
            raise ValueError('SKU latest pointer differs from capture')
        return value
    for directory in root.glob('collect_*'):
        path = directory / 'capture.json'
        if not path.is_file():
            continue
        value = json.loads(path.read_text(encoding='utf-8'))
        # A failed first pointer update must not promote an append-only orphan.
        # Old captures keep their existing fallback semantics.
        if value.get('publication_mode') == 'indexed_append_only':
            continue
        if value.get('shop_id') == shop_id and value.get('observation_id') == observation_id:
            candidates.append((datetime.fromisoformat(value['observed_at'].replace('Z', '+00:00')), directory))
    return _read_capture(max(candidates)[1]) if candidates else None
