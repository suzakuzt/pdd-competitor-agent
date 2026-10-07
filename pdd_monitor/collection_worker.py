"""One local browser attempt; accepts only durable, validated local job IDs."""
import argparse
from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import time
import uuid
import zipfile
from urllib.parse import parse_qs, urlparse

from . import capture_control
from .collection_service import atomic, stamp, public_probe_url, browser_mode


_JSON_READ_RETRY_DELAYS = (0.05, 0.1, 0.2)


def _read_json_text(path):
    path = Path(path)
    if os.name != 'nt':
        return path.read_text(encoding='utf-8')
    # FileIO can map a Windows sharing violation to errno 13 while dropping
    # winerror. Preserve the native error so only real 32/33 conflicts retry.
    import ctypes
    from ctypes import wintypes
    import msvcrt
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateFileW.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                  wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE)
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel.CloseHandle.restype = wintypes.BOOL
    # Shared read/write/delete lets the writer atomically replace the path;
    # an already-open handle still reads one committed file's complete bytes.
    handle = kernel.CreateFileW(str(path), 0x80000000, 1 | 2 | 4, None, 3, 0x80, None)
    if handle == ctypes.c_void_p(-1).value:
        error = ctypes.WinError(ctypes.get_last_error())
        error.filename = str(path)
        raise error
    try:
        descriptor = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
    except Exception:
        kernel.CloseHandle(handle)
        raise
    with os.fdopen(descriptor, 'rb') as stream:
        return stream.read().decode('utf-8')


def load(path):
    # Windows can briefly deny a read while an atomic receipt rename completes.
    # Retry sharing/lock violations only, never permissions or invalid content.
    for attempt in range(len(_JSON_READ_RETRY_DELAYS) + 1):
        try:
            raw = _read_json_text(path)
            break
        except OSError as error:
            if getattr(error, 'winerror', None) not in (32, 33) or attempt == len(_JSON_READ_RETRY_DELAYS):
                raise
            time.sleep(_JSON_READ_RETRY_DELAYS[attempt])
    return json.loads(raw)


def _diagnostic_path(project, value):
    """Return bounded project-relative paths, without reading any file contents."""
    if not isinstance(value, (str, Path)) or not str(value) or str(value).startswith('<'):
        return None
    try:
        root = Path(os.path.abspath(project))
        relative = Path(os.path.abspath(root / value)).relative_to(root)
    except (OSError, ValueError, TypeError):
        return None
    if (len(relative.as_posix()) > 512 or any(
            any(marker in part.casefold() for marker in ('owner', 'token', 'credential', 'secret', 'cookie', 'api_key'))
            or any(ord(char) < 32 for char in part) for part in relative.parts)):
        return None
    return relative.as_posix()


def _error_details(project, error, context):
    # Do not serialize exception messages, source lines, arguments, or locals:
    # any of them may contain private request data or credentials.
    frames = []
    trace = error.__traceback__
    code_root = Path(os.path.abspath(__file__)).parents[1]
    while trace is not None:
        filename = _diagnostic_path(code_root, trace.tb_frame.f_code.co_filename)
        if filename and filename.startswith(('pdd_monitor/', 'scripts/', 'tests/')):
            frames.append({'file': filename, 'line': trace.tb_lineno})
        trace = trace.tb_next
    return {'stage': context['stage'],
            'errno': error.errno if isinstance(error, OSError) and type(error.errno) is int else None,
            'winerror': getattr(error, 'winerror', None) if type(getattr(error, 'winerror', None)) is int else None,
            'filename': _diagnostic_path(project, getattr(error, 'filename', None)),
            'frames': frames[-8:]}


def storefront_entry(project, shop_id, source_url):
    """Use a previously verified public share entry; keep the stable capture identity."""
    entries = []
    for path in (project / 'state/competitors/intakes').glob('*.json'):
        record = load(path)
        if record.get('shop_id') != shop_id or record.get('resolved_storefront_url') != source_url:
            continue
        original = record.get('original_input_url', '')
        url = urlparse(original)
        query = parse_qs(url.query)
        if url.scheme == 'https' and url.netloc == 'mobile.yangkeduo.com' and url.path == '/mall_page.html' and set(query) == {'ps'} and len(query['ps']) == 1:
            entries.append(original)
    return entries[0] if len(set(entries)) == 1 else source_url


def image_package(project, snapshot_path, captured, output):
    """Reuse approved cache, add only exact URL bodies the rendered page loaded."""
    sys.path.insert(0, str(project / 'scripts'))
    from package_cached_images import package_cached_images
    package_cached_images(project, snapshot_path, output / 'cache', sealed=True)
    from pdd_monitor.store import _image_mime, _load_archive, _load_queue, _prepare_snapshot
    archive = output / 'images.zip'
    queue = load(output / 'cache/image_queue.json')
    with zipfile.ZipFile(output / 'cache/images.zip') as old:
        manifest = json.loads(old.read('manifest.json'))
        entries = {name: old.read(name) for name in old.namelist() if name != 'manifest.json'}
    by_view = {row['viewOrder']: row for row in load(snapshot_path)['rows']}
    bodies = captured or {}
    for item in queue['items']:
        if item.get('status') != 'pending_image_stage' or item.get('previousAttemptBlocked'):
            continue
        asset = bodies.get(item['imageUrl'])
        if not asset:
            continue
        import base64
        data = base64.b64decode(asset['base64'], validate=True)
        extension = {'image/png': 'png', 'image/jpeg': 'jpg', 'image/gif': 'gif', 'image/webp': 'webp'}.get(asset['mime'])
        if not extension or not 0 < len(data) <= 10 * 1024 * 1024:
            continue
        if _image_mime(data) != asset['mime']:
            raise ValueError('Loaded image MIME conflicts with its bytes')
        digest = hashlib.sha256(data).hexdigest()
        name = f'images/{digest}.{extension}'
        entries[name] = data
        for view in item['rowViewOrders']:
            manifest['items'].append({'viewOrder': view, 'originalImageUrl': item['imageUrl'], 'archivePath': name, 'sha256': digest, 'mime': asset['mime'], 'byteCount': len(data), 'title': by_view[view]['title'], 'acquisitionMethod': 'browser_loaded_image_body'})
            decision = next(d for d in manifest['cardDecisions'] if d['viewOrder'] == view)
            decision.update(status='browser_loaded_image_body', sha256=digest, archivePath=name, actionRequired='none', reason='Exact URL body loaded by the authorized Chrome runtime')
        item.update(status='browser_loaded_image_body', reportedDownloadSuccess=True, actionRequired='none', reason='本轮可见商品主图已由浏览器加载并校验内容；未另发下载请求。')
    manifest['method'] = 'verified_cache_and_browser_loaded_images'
    manifest['availableCardImageCount'] = len(manifest['items'])
    manifest['items'].sort(key=lambda row: row['viewOrder'])
    with zipfile.ZipFile(archive, 'x', compression=zipfile.ZIP_STORED) as target:
        target.writestr('manifest.json', json.dumps(manifest, ensure_ascii=False))
        for name, content in entries.items():
            target.writestr(name, content)
    atomic(output / 'image_queue.json', queue)
    snapshot = load(snapshot_path)
    rows, _, _ = _prepare_snapshot(snapshot)
    loaded, _ = _load_archive(archive, rows)
    states, _ = _load_queue(output / 'image_queue.json', snapshot, rows)
    if len(loaded) != len(manifest['items']):
        raise ValueError('Importer did not reproduce Chrome image bindings')
    loaded_views = {item['view_order'] for item in loaded}
    missing_reasons = {}
    for row in rows:
        view = row['view_order']
        if view not in loaded_views:
            reason = states.get(view, {}).get('status') or ('missing_source_url' if not row['image_url'] else 'pending_image_stage')
            missing_reasons[reason] = missing_reasons.get(reason, 0) + 1
    summary = {'total': len(rows), 'saved': len(loaded), 'missing': len(rows) - len(loaded),
               'status': 'complete' if len(loaded) == len(rows) else 'partial',
               'missing_by_reason': missing_reasons}
    atomic(output / 'image_summary.json', summary)
    return archive, output / 'image_queue.json', summary


def verify_image_release(receipt, summary):
    """Coverage counts must be reproduced by the independently checked import."""
    from .collection_service import released_image_summary
    verified = released_image_summary(receipt, summary)
    if verified is None:
        raise ValueError('Committed card/image coverage differs from validated image package')
    return verified


def browser_image_policy(project):
    """Optimization only; release still independently verifies cache evidence."""
    data = project / 'data'
    if not all((data / name).is_file() for name in ('monitor.sqlite3', 'images.sqlite3')):
        return {'skip_image_urls': [], 'blocked_image_urls': []}
    sys.path.insert(0, str(project / 'scripts'))
    from package_cached_images import read_cache, load_store, cache_reuse_policy
    candidates, blocked, _, _ = read_cache(data, load_store(project))
    policy = cache_reuse_policy(candidates, blocked)
    return {key: policy[key] for key in ('skip_image_urls', 'blocked_image_urls')}


def sku_observations(project, shop_id, run_id):
    """Read only the just-published complete run; no old or other-shop cards."""
    from .sku_store import sku_eligible
    with closing(sqlite3.connect((project / 'data/monitor.sqlite3').as_uri() + '?mode=ro', uri=True)) as db:
        db.row_factory = sqlite3.Row
        run = db.execute('SELECT shop_id,status FROM runs WHERE run_id=?', (run_id,)).fetchone()
        if run is None or run['shop_id'] != shop_id or run['status'] != 'complete':
            raise ValueError('SKU batch must follow this shop complete release')
        rows = [dict(row) for row in db.execute('SELECT * FROM observations WHERE run_id=? ORDER BY view_order,observation_id', (run_id,))]
    from collections import Counter
    candidates = Counter((row.get('title'), row.get('image_url')) for row in rows)
    return [{**row, 'current_unique_card_candidate_count': candidates[(row.get('title'), row.get('image_url'))]}
            for row in rows if sku_eligible(row)]


def latest_complete_sku_run(project, shop_id):
    """Pin the latest fully observed shop run; never silently choose a partial run."""
    with closing(sqlite3.connect((Path(project) / 'data/monitor.sqlite3').as_uri() + '?mode=ro', uri=True)) as db:
        row = db.execute('''SELECT run_id FROM runs WHERE shop_id=? AND status='complete'
            AND end_boundary_observed=1 ORDER BY observed_to_epoch DESC,observed_from_epoch DESC,run_id DESC LIMIT 1''',
            (shop_id,)).fetchone()
    if row is None:
        raise ValueError('本店尚无已完整采集的商品清单，请先完成一次全店采集。')
    return row[0]


_SKU_CAPTURE_NOT_LOADED = object()


def with_verified_product_ref(project, shop_id, row, *, previous=_SKU_CAPTURE_NOT_LOADED):
    """A navigation hint from this exact stored card; original identity fields stay intact."""
    from .sku_store import read_latest, validate_capture, validate_card_binding
    if previous is _SKU_CAPTURE_NOT_LOADED:
        previous = read_latest(project, shop_id, row['observation_id'])
    output = dict(row)
    if previous is None:
        if 'verified_product_ref' in row:
            raise ValueError('SKU navigation reference has no matching saved evidence')
        return output
    if (previous.get('shop_id') != shop_id or previous.get('observation_id') != row['observation_id']
            or not isinstance(previous.get('capture_id'), str)
            or not re.fullmatch(r'collect_[0-9a-f]{32}(?:_o[1-9]\d*)?', previous['capture_id'])):
        raise ValueError('Saved SKU navigation reference belongs to another shop or card')
    checked = validate_capture(deepcopy(previous))
    validate_card_binding(checked, row)
    if checked['identity_basis'] == 'current_unique_card_modal':
        if 'verified_product_ref' in row:
            raise ValueError('SKU list modal has no verified public navigation reference')
        return output
    public = urlparse(checked['goods_url'])
    reference = {'goods_id': checked['goods_id'],
                 'goods_url': f'{public.scheme}://{public.netloc}{public.path}?goods_id={checked["goods_id"]}',
                 'capture_id': checked['capture_id']}
    if 'verified_product_ref' in row and row['verified_product_ref'] != reference:
        raise ValueError('SKU navigation reference conflicts with saved evidence')
    output['verified_product_ref'] = reference
    return output


def pending_sku_observations(project, shop_id, observations):
    """Resume only this original-card scope; skip complete, verified image/price evidence."""
    from .sku_store import read_latest
    pending, completed = [], []
    for row in observations:
        previous = read_latest(project, shop_id, row['observation_id'])
        enriched = with_verified_product_ref(project, shop_id, row, previous=previous)
        ready = False
        if previous is not None:
            variants = previous['variants']
            ready = (previous['status'] == 'complete'
                     and previous.get('all_combinations_visited') is True
                     and previous.get('option_catalog_verified') is True
                     and previous.get('expected_combinations', len(variants)) == len(variants)
                     and bool(variants) and all(item.get('selection_verified') is True
                         and item.get('current_price_yuan') is not None
                         and item.get('image_sha256') and item.get('image_data_url') for item in variants))
        if ready:
            completed.append({'observation_id': row['observation_id'], 'capture_id': previous['capture_id']})
        else:
            pending.append(enriched)
    return pending, completed


def _close_shop_browser(process, directory):
    """Release only our Node helper; attached user Chrome is never terminated."""
    if process is None or process.poll() is not None:
        return
    try:
        # Legacy helpers may still await this stop signal. Preserve any existing
        # receipt; never send a new SKU request through the old shop handshake.
        if not (directory / 'sku_skip.json').exists():
            atomic(directory / 'sku_skip.json', {'reason': 'worker_finished_or_stopped', 'at': stamp()})
        process.wait(timeout=15)
    except (subprocess.TimeoutExpired, OSError):
        # This is our exact Popen handle, never a PID search or Chrome process.
        try:
            process.terminate()
            process.wait(timeout=5)
        except (subprocess.TimeoutExpired, OSError):
            try:
                process.kill()
                process.wait(timeout=5)
            except (subprocess.TimeoutExpired, OSError):
                pass


def _start_shop_browser(project, directory, env, *, timeout=7500):
    """Wait for the shop receipt while its one authorized Chrome session stays open."""
    if any((directory / name).exists() for name in ('browser_result.json', 'sku_request.json', 'sku_skip.json')):
        raise ValueError('A fresh job must not adopt an existing browser receipt')
    command = [str(project / 'runtime/node/bin/node.exe'), str(project / 'scripts/local_collection.mjs'), str(directory / 'request.json')]
    process = subprocess.Popen(command, cwd=project, env=env, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    deadline = time.monotonic() + timeout
    try:
        while True:
            if (directory / 'browser_result.json').is_file():
                # Atomic rename is the commit marker; validate basic shape here,
                # then independently validate the capture before any release.
                result = load(directory / 'browser_result.json')
                if not isinstance(result, dict) or not isinstance(result.get('status'), str):
                    raise ValueError('Browser returned an invalid shop receipt')
                process.poll()
                return process
            if process.poll() is not None:
                raise FileNotFoundError('Browser exited without its shop receipt')
            if time.monotonic() >= deadline:
                atomic(directory / 'cancel.json', {'reason': 'shop_capture_timeout', 'at': stamp()})
                raise subprocess.TimeoutExpired(command, timeout)
            time.sleep(0.2)
    except Exception:
        _close_shop_browser(process, directory)
        raise


def _batch_item_payload(directory, item):
    if not item.get('capture_file'):
        return item
    relative = item['capture_file']
    if not isinstance(relative, str) or Path(relative).is_absolute():
        raise ValueError('SKU item evidence path must be relative')
    path = (directory / relative).resolve()
    if not path.is_relative_to(directory.resolve()) or not path.is_file():
        raise ValueError('SKU item evidence escaped its batch')
    payload = load(path)
    if payload.get('observation_id', item['observation_id']) != item['observation_id']:
        raise ValueError('SKU item evidence belongs to another card')
    return {**item, **payload, 'observation_id': item['observation_id']}


def _persist_sku_batch(project, job, observations, browser_result, directory, workspace):
    """Publish verified products independently, even when later products stop."""
    from .sku_store import prepare_batch_backup, save_capture, validate_card_binding
    expected = {row['observation_id']: row for row in observations}
    batch = browser_result.get('sku_batch') or {}
    summary = {'status': 'partial', 'total': len(expected), 'completed': 0, 'partial': 0,
               'failed': 0, 'remaining': len(expected), 'variant_count': 0, 'items': []}
    seen, backup = set(), None
    items = batch.get('items', [])
    if not isinstance(items, list):
        raise ValueError('SKU batch result must list its card evidence')
    item_ids = [item.get('observation_id') if isinstance(item, dict) else None for item in items]
    if (any(type(key) is not int or key not in expected for key in item_ids)
            or len(set(item_ids)) != len(item_ids)):
        raise ValueError('SKU batch includes duplicate or out-of-scope cards')
    for raw_item in items:
        observation_id = raw_item.get('observation_id') if isinstance(raw_item, dict) else None
        if type(observation_id) is not int or observation_id not in expected or observation_id in seen:
            raise ValueError('SKU batch includes duplicate or out-of-scope cards')
        seen.add(observation_id)
        status = raw_item.get('status')
        entry = {'observation_id': observation_id, 'status': status}
        if status not in ('complete', 'partial'):
            entry.update(reason=raw_item.get('reason'), message=raw_item.get('message'))
            if status not in ('pending', 'not_started', 'cancelled'):
                summary['failed'] += 1
            summary['items'].append(entry)
            continue
        try:
            item = _batch_item_payload(directory, raw_item)
            capture = item['sku']
            row = expected[observation_id]
            validate_card_binding(capture, row)
            if capture.get('status') != status:
                raise ValueError('SKU completion status conflicts with capture')
            if backup is None:
                try:
                    backup = prepare_batch_backup(project, workspace)
                except Exception as error:
                    entry.update(status='failed', reason='sku_backup_failed', error_type=type(error).__name__,
                                 message='SKU 保存前的备份校验失败，已停止发布，原有数据保留。')
                    summary['failed'] += 1
                    summary['items'].append(entry)
                    batch = {**batch, 'status': 'manual_review', 'reason': 'sku_backup_failed'}
                    break
            capture_job = {**job, 'id': job['id'] + '_o' + str(observation_id), 'observation_id': observation_id}
            receipt = save_capture(project, capture_job, capture,
                item.get('images', browser_result.get('images', {})), workspace / str(observation_id), batch_backup=backup)
            entry.update(status=receipt['status'], receipt=receipt)
            summary['completed' if receipt['status'] == 'complete' else 'partial'] += 1
            summary['variant_count'] += receipt['variant_count']
        except Exception as error:
            entry.update(status='failed', reason='sku_evidence_or_save_failed', error_type=type(error).__name__,
                         message='本商品规格证据未通过校验或保存，已有记录保留。')
            summary['failed'] += 1
        summary['items'].append(entry)
        # A crash after one immutable publication still leaves its exact receipt.
        summary['remaining'] = summary['total'] - summary['completed'] - summary['partial'] - summary['failed']
        atomic(directory / 'publication_progress.json', summary)
    summary['remaining'] = summary['total'] - summary['completed'] - summary['partial'] - summary['failed']
    stop_status = batch.get('status', browser_result.get('status'))
    summary['status'] = ('complete' if summary['completed'] == summary['total'] else
                         stop_status if stop_status in ('needs_login', 'needs_browser', 'needs_url', 'cancelled', 'manual_review', 'failed') else 'partial')
    summary['reason'] = batch.get('reason', browser_result.get('reason'))
    if isinstance(batch.get('message'), str) and batch['message'].strip():
        summary['stop_message'] = batch['message'].strip()
    summary['message'] = (f"SKU 完成 {summary['completed']}/{summary['total']} 个商品，"
                          f"部分 {summary['partial']}，未成功 {summary['failed']}，待处理 {summary['remaining']}。")
    atomic(directory / 'publication_progress.json', summary)
    return summary


def collect_shop_skus(project, job, shop, entry_url, release_receipt, parent_directory, env, *, browser_process=None, resume_completed=False):
    """Explicit SKU batch only; never continue a shop browser's legacy handshake."""
    if job.get('kind') != 'sku_batch' or browser_process is not None:
        raise ValueError('SKU batch requires its own explicit task; shop follow-up is disabled')
    directory = parent_directory / 'sku_batch'
    directory.mkdir(parents=True, exist_ok=False)
    observations = [{**row, 'shop_source_url': shop['source_url'], 'shop_name': shop['shop_name']}
                    for row in sku_observations(project, job['shop_id'], release_receipt['run_id'])]
    scope_total = len(observations)
    skipped = []
    if resume_completed:
        observations, skipped = pending_sku_observations(project, job['shop_id'], observations)
    else:
        observations = [with_verified_product_ref(project, job['shop_id'], row) for row in observations]
    scope = {'source_run_id': release_receipt['run_id'], 'scope_total': scope_total,
             'already_complete': len(skipped), 'skipped_observation_ids': [item['observation_id'] for item in skipped]}
    atomic(parent_directory / 'sku_queue.json', {**scope,
        'observation_ids': [row['observation_id'] for row in observations], 'skipped_receipts': skipped})
    empty = {'status': 'complete', 'total': 0, 'completed': 0, 'partial': 0, 'failed': 0,
             'remaining': 0, 'variant_count': 0, 'items': [], 'website_collection_performed': False, **scope,
             'message': f'已有 {len(skipped)} 个商品 SKU 完整，本次没有待补采商品。' if skipped else '本轮没有需要采集 SKU 的已出单商品。'}
    if not observations:
        atomic(parent_directory / 'sku_receipt.json', empty)
        return empty
    if (parent_directory / 'cancel.json').exists():
        cancelled = {**empty, 'status': 'cancelled', 'reason': 'operator_cancelled',
                     'total': len(observations), 'remaining': len(observations),
                     'message': '已取消 SKU 采集，已有数据保留。'}
        atomic(parent_directory / 'sku_receipt.json', cancelled)
        return cancelled
    request = {'kind': 'sku_batch', 'directory': str(directory), 'project': str(project),
               'shop': {key: shop[key] for key in ('shop_name', 'source_url')}, 'entry_url': entry_url,
               'observations': observations, 'cancel_file': str(parent_directory / 'cancel.json'),
               'progress_file': str(parent_directory / 'progress.json'), 'reuse_shop': False,
               'sku_only': resume_completed, **scope}
    atomic(directory / 'request.json', request)
    atomic(parent_directory / 'progress.json', {'phase': '正在采集本次明确选择的商品 SKU',
           'stage': 'sku', 'sku_total': len(observations), 'sku_completed': 0,
           'sku_remaining': len(observations), **scope, 'at': stamp()})
    failure = None
    try:
        completed = subprocess.run([str(project / 'runtime/node/bin/node.exe'),
            str(project / 'scripts/local_collection.mjs'), str(directory / 'request.json')],
            cwd=project, env=env, timeout=28800, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        returncode = completed.returncode
        if returncode and failure is None:
            failure = {'status': 'manual_review', 'reason': 'sku_browser_process_failed'}
    except (subprocess.TimeoutExpired, OSError) as error:
        failure = {'status': 'manual_review', 'reason': 'sku_browser_process_interrupted',
                   'error_type': type(error).__name__}
    result_path = directory / 'browser_result.json'
    if result_path.is_file():
        result = load(result_path)
    else:
        failure = failure or {'status': 'manual_review', 'reason': 'sku_browser_receipt_missing'}
        result = {'sku_batch': {'items': []}}
    if failure:
        result.update(failure)
        result['sku_batch'] = {**result.get('sku_batch', {}), **failure}
    browser_items = (result.get('sku_batch') or {}).get('items') or []
    atomic(parent_directory / 'progress.json', {'phase': 'SKU 已读取，正在备份并校验保存规格',
           'stage': 'sku_validation', 'sku_total': len(observations),
           'sku_completed': sum(item.get('status') == 'complete' for item in browser_items),
           'sku_partial': sum(item.get('status') == 'partial' for item in browser_items),
           'sku_failed': sum(item.get('status') not in ('complete', 'partial', 'pending', 'not_started', 'cancelled') for item in browser_items),
           'sku_remaining': max(0, len(observations) - len(browser_items)), 'at': stamp()})
    workspace = Path(project.anchor) / 'PDDLR' / uuid.uuid4().hex[:10]
    summary = _persist_sku_batch(project, job, observations, result, directory, workspace)
    summary.update(scope, website_collection_performed=result.get('website_collection_performed') is True)
    if resume_completed and skipped:
        summary['message'] = f'已跳过 {len(skipped)} 个完整商品。' + summary['message']
    atomic(parent_directory / 'sku_receipt.json', summary)
    return summary


def _sku_workspace(project):
    return Path(project.anchor) / 'PDDLR' / uuid.uuid4().hex[:10]


def _single_sku_pipeline(project, job, request, directory, env, workspace, context):
    """Read one requested product; protect its append-only publication in parallel."""
    from .sku_append import prepare_append_protection
    from .sku_store import (save_capture, validate_card_binding,
                            SkuSourceChanged, SkuSaveCancelled)
    _finish_stage(context)
    context['_record_stage_timings'] = False
    timings = context['stages_seconds'] = {}
    context['parallel_backup_prepared'] = False
    cancel_file = directory / 'cancel.json'
    if cancel_file.exists():
        return {'status': 'cancelled', 'reason': 'cancelled',
                'message': '本次 SKU 采集已取消，原记录保留。', 'website_collection_performed': False}
    started = time.perf_counter()

    def prepare():
        backup_started = time.perf_counter()
        try:
            return prepare_append_protection(project, workspace, job)
        finally:
            timings['backup_prepare'] = round(time.perf_counter() - backup_started, 3)

    atomic(directory / 'progress.json', {'phase': '读取所选商品规格（同时核验保存范围）',
           'stage': 'sku_read_and_backup', 'backup_status': 'preparing', 'at': stamp()})
    result, browser_error, backup_error, backup = None, None, None, None
    _set_stage(context, 'sku_read_and_backup')
    # Executor shutdown waits for all backup I/O even if reading its result or
    # writing progress fails. subprocess.run likewise reaps its Node on timeout.
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix='pdd-sku-backup') as pool:
        future = pool.submit(prepare)
        browser_started = time.perf_counter()
        try:
            completed = subprocess.run([str(project / 'runtime/node/bin/node.exe'),
                str(project / 'scripts/local_collection.mjs'), str(directory / 'request.json')],
                cwd=project, env=env, timeout=7500,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            result = load(directory / 'browser_result.json')
            if not isinstance(result, dict) or not isinstance(result.get('status'), str):
                raise ValueError('Browser returned an invalid SKU receipt')
            if completed.returncode and result.get('status') in ('complete', 'partial'):
                result = {**result, 'status': 'manual_review', 'reason': 'browser_process_failed',
                          'message': '浏览器进程异常退出；本轮未发布，保留封存证据待复核。'}
        except Exception as error:
            browser_error = error
        finally:
            timings['browser_read'] = round(time.perf_counter() - browser_started, 3)
        readable = isinstance(result, dict) and result.get('status') in ('complete', 'partial')
        atomic(directory / 'progress.json', {
            'phase': '规格已读取，核验并保存' if readable else '规格读取已停止，等待保存核验结束',
            'stage': 'sku_backup_wait', 'backup_status': 'awaiting_result',
            'variants': len((result.get('sku') or {}).get('variants', [])) if readable else 0,
            'at': stamp()})
        _set_stage(context, 'sku_backup_wait')
        waited = time.perf_counter()
        try:
            backup = future.result()
            context['parallel_backup_prepared'] = True
        except Exception as error:
            backup_error = error
        finally:
            timings['backup_wait'] = round(time.perf_counter() - waited, 3)
    timings['parallel_total'] = round(time.perf_counter() - started, 3)
    if backup_error is not None:
        context['backup_error_type'] = type(backup_error).__name__
    if browser_error is not None:
        _set_stage(context, 'sku_browser_capture')
        raise browser_error
    if not readable:
        return result
    if cancel_file.exists():
        return {'status': 'cancelled', 'reason': 'cancelled',
                'message': '本次 SKU 采集已取消；两项准备均已结束，本轮未发布，原记录保留。',
                'website_collection_performed': result.get('website_collection_performed') is True}
    if backup_error is not None:
        return {'status': 'manual_review', 'reason': 'sku_backup_failed',
                'error_type': type(backup_error).__name__,
                'message': 'SKU 保存前的备份校验失败，已停止发布，原有数据和本轮规格证据保留。',
                'website_collection_performed': result.get('website_collection_performed') is True}
    _set_stage(context, 'sku_validation_and_save')
    validate_card_binding(result['sku'], request['observation'])
    atomic(directory / 'progress.json', {'phase': '保存范围已核验，正在保存所选商品规格',
           'stage': 'sku_validation', 'backup_status': 'verified',
           'variants': len(result['sku'].get('variants', [])),
           'total': result['sku'].get('expected_combinations'), 'at': stamp()})
    save_started = time.perf_counter()
    try:
        receipt = save_capture(project, job, result['sku'], result.get('images', {}),
            workspace / 'item', batch_backup=backup, parallel_prepared=True, cancel_file=cancel_file)
    except SkuSourceChanged:
        return {'status': 'manual_review', 'reason': 'sku_backup_source_changed',
                'message': '备份后原始数据或来源文件已变化，本轮 SKU 尚未发布；已保留证据，请复核后重新发起。',
                'website_collection_performed': result.get('website_collection_performed') is True}
    except SkuSaveCancelled:
        return {'status': 'cancelled', 'reason': 'cancelled',
                'message': '本次 SKU 保存前收到取消，本轮未发布，原记录保留。',
                'website_collection_performed': result.get('website_collection_performed') is True}
    finally:
        timings['save'] = round(time.perf_counter() - save_started, 3)
    return {'status': receipt['status'],
            'message': f"已保存{receipt['variant_count']}个规格组合；每项价格、图片及读取依据分别保留。",
            'receipt': receipt, 'website_collection_performed': True}


def _finish_stage(context):
    started = context.pop('_stage_started', None)
    stage = context.get('stage')
    if started is not None and stage:
        timings = context.setdefault('stages_seconds', {})
        timings[stage] = round(timings.get(stage, 0.0) + max(0.0, time.perf_counter() - started), 3)


def _set_stage(context, stage):
    _finish_stage(context)
    context['stage'] = stage
    if context.get('_record_stage_timings', True):
        context['_stage_started'] = time.perf_counter()


def work(project, job_id):
    """Execute the fixed local pipeline; no model request is made by this worker."""
    started = time.perf_counter()
    context = {}
    _set_stage(context, 'initializing')
    try:
        result = _work(project, job_id, context=context)
    except Exception as error:
        if isinstance(error, subprocess.TimeoutExpired):
            result = {'status': 'manual_review', 'reason': 'browser_process_timeout',
                      'message': '浏览器采集进程超时；已停止本次进程，保留批次和锁，请复核后再启动。'}
        elif isinstance(error, FileNotFoundError):
            result = {'status': 'failed', 'reason': 'local_runtime_or_receipt_missing',
                      'message': '本机执行文件或采集收据缺失；未确认发布成功，请检查本次诊断信息。'}
        elif isinstance(error, PermissionError):
            result = {'status': 'failed', 'reason': 'local_file_access_failed',
                      'message': '本机文件访问失败；原数据和本轮已保存证据保留，请复核文件访问及封存结果后恢复。'}
        else:
            result = {'status': 'failed', 'reason': 'local_worker_failed_requires_review',
                      'message': '采集或发布未完成；保留原数据与本轮证据，请复核后重试。'}
        result.update(error_type=type(error).__name__, error_details=_error_details(project, error, context))
    _finish_stage(context)
    if re.fullmatch(r'capture_[0-9a-f]{32}', context.get('capture_job_id', '')):
        result.setdefault('capture_job_id', context['capture_job_id'])
    for key in ('stages_seconds', 'parallel_backup_prepared', 'backup_error_type'):
        if key in context:
            result[key] = context[key]
    return {**result, 'execution_mode': 'fixed_program', 'ai_requests': 0,
            'elapsed_seconds': round(max(0.0, time.perf_counter() - started), 3)}


def _work(project, job_id, *, context=None):
    context = context if context is not None else {}
    _set_stage(context, 'initializing')
    if not re.fullmatch(r'collect_[0-9a-f]{32}', job_id):
        raise ValueError('Invalid job ID')
    if browser_mode(project) == 'codex_iab':
        return {'status': 'manual_review', 'reason': 'external_browser_disabled', 'message': '已改用 Codex 右侧浏览器；外部 Chrome 采集器停用，原数据保留。', 'website_collection_performed': False}
    directory = project / 'state/local_collection' / job_id
    _set_stage(context, 'settings_read')
    state = load(project / 'state/local_collection/settings.json')
    job = next(j for j in state['jobs'] if j['id'] == job_id and j['status'] == 'running')
    if job.get('browser_mode') == 'codex_iab':
        return {'status': 'manual_review', 'reason': 'iab_host_only',
                'message': '此请求只由 Codex 右侧浏览器宿主执行；后台未启动外部浏览器。'}
    directory.mkdir(parents=True, exist_ok=True)
    _set_stage(context, 'scope_validation')
    request = {'kind': job['kind'], 'directory': str(directory), 'project': str(project)}
    if job['kind'] == 'probe':
        if job.get('verify_storefront') is True:
            from .collection_service import public_shop_entry_url
            request['entry_url'] = public_shop_entry_url(job['url'])
            request['verify_storefront'] = True
        else:
            request['entry_url'] = public_probe_url(job['url'])
    else:
        shop = capture_control._resolve_shop(project, job['shop_id'])
        request['shop'] = {k: shop[k] for k in ('shop_name', 'source_url')}
    if job['kind'] == 'shop':
        # A replacement is an entry only: the driver independently verifies the
        # rendered URL against the original shop identity before capturing.
        from .collection_service import public_shop_entry_url
        request['entry_url'] = public_shop_entry_url(job['entry_url']) if job.get('entry_url') else storefront_entry(project, job['shop_id'], shop['source_url'])
    capture_job = None
    if job['kind'] == 'shop':
        _set_stage(context, 'capture_begin')
        capture_job = capture_control.begin(project, shop_id=job['shop_id'])
        if not capture_job.get('acquired'):
            return {'status': 'manual_review', 'reason': 'capture_lock_held', 'message': '已有采集持有独占锁；保留原尝试，请先复核或完成它。'}
        context['capture_job_id'] = capture_job['job_id']
        request['capture'] = {k: capture_job[k] for k in ('session_directory', 'session_options', 'job_id')}
        request['capture']['session_options'].update(browserName='Google Chrome · 用户正常会话', browserTool='pdd_local_chrome_worker', maxScrollViewports=3)
    elif job['kind'] == 'sku':
        if type(job.get('observation_id')) is not int or job['observation_id'] < 1:
            raise ValueError('Invalid original SKU card scope')
        with closing(sqlite3.connect((project / 'data/monitor.sqlite3').as_uri() + '?mode=ro', uri=True)) as db:
            db.row_factory = sqlite3.Row
            row = db.execute('SELECT o.* FROM observations o JOIN runs r ON r.run_id=o.run_id WHERE o.observation_id=? AND r.shop_id=?', (job['observation_id'], job['shop_id'])).fetchone()
            if row is None:
                raise ValueError('Wrong SKU scope')
            if 'source_run_id' in job and job['source_run_id'] != row['run_id']:
                raise ValueError('SKU original card no longer belongs to its pinned run')
            request['observation'] = dict(row)
            request['observation']['current_unique_card_candidate_count'] = db.execute(
                'SELECT COUNT(*) FROM observations WHERE run_id=? AND title=? AND image_url IS ?',
                (row['run_id'], row['title'], row['image_url'])).fetchone()[0]
            from .sku_store import sku_eligible
            if not sku_eligible(request['observation']):
                raise ValueError('SKU collection requires positive exact item sales')
        request['observation'].update(shop_source_url=shop['source_url'], shop_name=shop['shop_name'])
        request['observation'] = with_verified_product_ref(project, job['shop_id'], request['observation'])
        request['entry_url'] = (state.get('entries', {}).get(job['shop_id']) or {}).get('entry_url') or storefront_entry(project, job['shop_id'], shop['source_url'])
        request['parallel_backup_preparation'] = True
    node = project / 'runtime/node/bin/node.exe'
    env = dict(os.environ)
    env.pop('DEEPSEEK_API_KEY', None)
    if job['kind'] == 'sku_batch':
        _set_stage(context, 'sku_batch')
        if job.get('source_run_id') != latest_complete_sku_run(project, job['shop_id']):
            return {'status': 'manual_review', 'reason': 'sku_source_run_changed',
                    'message': '商品清单已更新，请重新发起 SKU 补采以确认当前范围。',
                    'website_collection_performed': False}
        entry_url = job.get('entry_url') or (state.get('entries', {}).get(job['shop_id']) or {}).get('entry_url') or storefront_entry(project, job['shop_id'], shop['source_url'])
        summary = collect_shop_skus(project, job, shop, entry_url,
            {'run_id': job['source_run_id']}, directory, env, resume_completed=True)
        return {'status': summary['status'], 'message': summary['message'], 'sku_summary': summary,
                'reason': summary.get('reason'), 'source_run_id': job['source_run_id'],
                'website_collection_performed': summary['website_collection_performed'],
                'shop_collection_performed': False}
    shop_browser_process = None
    try:
        _set_stage(context, 'request_write')
        if job['kind'] == 'shop':
            request.update(browser_image_policy(project))
        atomic(directory / 'request.json', request)
        if job['kind'] == 'sku':
            return _single_sku_pipeline(project, job, request, directory, env,
                                        _sku_workspace(project), context)
        _set_stage(context, 'browser_capture_and_receipt_wait')
        if job['kind'] == 'shop':
            shop_browser_process = completed = _start_shop_browser(project, directory, env)
        else:
            completed = subprocess.run([str(node), str(project / 'scripts/local_collection.mjs'), str(directory / 'request.json')], cwd=project, env=env, timeout=7500, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        _set_stage(context, 'browser_receipt_read')
        result = load(directory / 'browser_result.json')
        if job['kind'] == 'probe':
            if request.get('verify_storefront') is True and completed.returncode and result.get('status') == 'complete':
                return {**result, 'status': 'manual_review', 'reason': 'browser_process_failed',
                        'message': '店铺核验进程异常退出；核验凭证保留，未确认完成。'}
            return result
        if completed.returncode or result['status'] not in ('complete', 'partial'):
            if capture_job:
                try:
                    capture_control.abandon(project, capture_job['job_id'], result.get('reason') or 'local_browser_stopped')
                except Exception as error:
                    # Preserve the observed login/layout cause even if cleanup needs review.
                    result = {**result, 'lock_release_requires_review': True,
                              'cleanup_error_type': type(error).__name__}
            if completed.returncode and result.get('status') in ('complete', 'partial'):
                return {**result, 'status': 'manual_review', 'reason': 'browser_process_failed',
                        'message': '浏览器进程异常退出；本轮未发布，保留封存证据待复核。'}
            return result
        # Keep release workspaces short: the existing Windows path budget gate remains enforced.
        workspace = Path(project.anchor) / 'PDDLR' / uuid.uuid4().hex[:10]
        _set_stage(context, 'session_read')
        session = load(Path(capture_job['session_directory']) / 'session.json')
        if not session.get('snapshot') or not session.get('importReady'):
            capture_control.abandon(project, capture_job['job_id'], 'local_capture_not_import_ready')
            return {'status': 'manual_review', 'reason': 'local_capture_not_import_ready', 'message': '本轮未通过封存门槛，批次保留，未写业务库。'}
        snapshot = Path(capture_job['session_directory']) / session['snapshot']['file']
        _set_stage(context, 'capture_validation')
        gate = capture_control.check(project, snapshot, job['shop_id'])
        _set_stage(context, 'capture_gate_write')
        atomic(directory / 'capture_gate.json', gate)
        if result['status'] != 'complete' or not gate.get('ready_for_rehearsal') or (directory / 'cancel.json').exists():
            if result['status'] != 'complete':
                capture_control.abandon(project, capture_job['job_id'], result.get('reason', 'partial_requires_review'))
            return {'status': 'manual_review', 'reason': 'capture_gate_requires_review', 'message': '本轮部分覆盖、卡数减少或取消，需复核；原完整参考保留，新批次尚未入库。', 'capture_job_id': capture_job['job_id'], 'gate': gate}
        _set_stage(context, 'capture_lock_renewal')
        capture_control.renew(project, capture_job['job_id'])
        _set_stage(context, 'image_validation')
        atomic(directory / 'progress.json', {'stage': 'image_validation',
               'phase': '正在核对图片缓存和本轮主图', 'cards': gate['captured_card_count'], 'at': stamp()})
        image_archive, image_queue, image_summary = image_package(project, snapshot, result.get('images'), workspace / 'images')
        image_progress = {'cards': gate['captured_card_count'],
                          'image_total': image_summary['total'], 'image_saved': image_summary['saved'],
                          'image_missing': image_summary['missing']}
        sys.path.insert(0, str(project / 'scripts'))
        from release_snapshot import prepare, apply
        release_workspace = workspace / 'release'
        _set_stage(context, 'release_preparation')
        atomic(directory / 'progress.json', {'stage': 'release_preparation',
               'phase': '正在备份并预演入库', 'at': stamp(), **image_progress})
        from .backup_policy import incremental_backup_enabled
        prepare(project, snapshot, session['snapshot']['sha256'], job['shop_id'], release_workspace,
                image_archive=image_archive, image_queue=image_queue,
                incremental_backup=incremental_backup_enabled(project))
        _set_stage(context, 'release_commit')
        atomic(directory / 'progress.json', {'stage': 'release_commit',
               'phase': '正在保存商品数据', 'at': stamp(), **image_progress})
        receipt = apply(release_workspace, capture_job['job_id'], execute=True)
        _set_stage(context, 'release_receipt_write')
        atomic(directory / 'release_receipt.json', receipt)
        _set_stage(context, 'image_release_verification')
        image_summary = verify_image_release(receipt, image_summary)
        message = f"已采集并验收入库{gate['captured_card_count']}张原卡；主图已保存{image_summary['saved']}/{image_summary['total']}张。"
        if image_summary['missing']:
            message += f"采集未完成，仍有{image_summary['missing']}张主图待补；历史数据保留。"
        published = {'status': image_summary['status'], 'message': message,
                     'image_summary': image_summary, 'capture_job_id': capture_job['job_id'], 'receipt': receipt,
                     'website_collection_performed': True, 'entry_verified': result.get('entry_verified') is True,
                     'progress': {'stage': 'release_commit', 'phase': '商品数据已保存', **image_progress}}
        if image_summary['missing']:
            published['reason'] = 'images_missing'
        if result.get('image_repair_stopped') is True or result.get('sku_followup_available') is False:
            published.update(image_repair_stopped=True, image_repair=result.get('image_repair') or {})
        published['sku_collection_mode'] = 'on_demand'
        # Shop publication is the end of this task. Historical SKU queues and
        # receipts remain evidence only; a new explicit SKU task owns any reads.
        _set_stage(context, 'result_write')
        atomic(directory / 'result.json', published)
        return published
    except Exception:
        # A sealed complete attempt may be partially published: keep its lock/receipts for review.
        if capture_job:
            try:
                capture_control.abandon(project, capture_job['job_id'], 'local_worker_failed_requires_review')
            except Exception:
                pass
        raise
    finally:
        _finish_stage(context)
        cleanup_started = time.perf_counter()
        try:
            _close_shop_browser(shop_browser_process, directory)
        finally:
            if job['kind'] == 'shop':
                context.setdefault('stages_seconds', {})['browser_cleanup'] = round(time.perf_counter() - cleanup_started, 3)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--project', required=True, type=Path)
    parser.add_argument('--job', required=True)
    args = parser.parse_args()
    project = args.project.resolve()
    if not re.fullmatch(r'collect_[0-9a-f]{32}', args.job):
        return 1
    try:
        result = work(project, args.job)
    except Exception as error:
        result = {'status': 'failed', 'message': '采集或发布未完成；保留原数据与本轮证据，请复核后重试。', 'error_type': type(error).__name__}
    atomic(project / 'state/local_collection' / args.job / 'result.json', result)
    return 0 if result['status'] in ('complete', 'partial', 'needs_login', 'needs_url', 'needs_browser', 'manual_review', 'cancelled') else 1


if __name__ == '__main__':
    raise SystemExit(main())
