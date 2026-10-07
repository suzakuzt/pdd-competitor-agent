"""Local image packaging from immutable pageAssets receipts and exact-URL cache.

No URL is requested, rewritten, retried or transformed. Missing images do not
prevent an otherwise valid card snapshot from being imported separately.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import shutil
import stat
import tempfile
from urllib.parse import parse_qs, urlsplit
import zipfile

from package_cached_images import EXTENSIONS, check_sidecars, json_bytes, load_store, read_cache, sha_file

OUTPUT_NAMES = ('images.zip', 'image_queue.json', 'missing_urls.json', 'bundle_acceptance.json')
REVIEWABLE_LEGACY_REASONS = frozenset((
    'download_timeout_followed_by_chrome_error_page',
    'safety_rejection_of_chrome_error_protocol_after_prior_failure',
))


def read_legacy_review(path, snapshot, snapshot_sha, allowed_urls, blocked, store):
    """Verify an explicit coordinator attestation, never authorize a download.

    The file binds this new snapshot to an already successful normal-storefront
    pageAssets receipt. Browser provenance is attested by the coordinator, not
    independently established by this offline packager. Original restrictions
    remain immutable; all receipt and byte claims are checked below.
    """
    if path is None:
        return {}, None
    path = Path(path).resolve()
    raw_bytes = path.read_bytes()
    if not 0 < len(raw_bytes) <= 1024 * 1024:
        raise ValueError('Legacy review has invalid size')
    raw = _strict_json(raw_bytes)
    if (not isinstance(raw, dict) or raw.get('schemaVersion') != 1
            or raw.get('snapshotSha256') != snapshot_sha
            or raw.get('tool') != 'cua_repl.pageAssets'
            or raw.get('basis') != 'normal_storefront_page_asset'):
        raise ValueError('Legacy review must bind this snapshot and normal storefront pageAssets evidence')
    page_url = raw.get('pageUrl')
    if not isinstance(page_url, str):
        raise ValueError('Legacy review needs the actually observed normal storefront pageUrl')
    page, source = urlsplit(page_url), urlsplit(snapshot.get('sourceUrl', ''))
    if (page.scheme != 'https' or page.username or page.password
            or (page.hostname, page.port, page.path) != (source.hostname, source.port, source.path)):
        raise ValueError('Legacy review page must be the normal HTTPS storefront, never an error protocol')
    expected = store.shop_identity_evidence(snapshot)
    actual = store.shop_identity_evidence({'sourceUrl': page_url})
    if any(actual[key] != expected[key] for key in ('identity_kind', 'stable_identifier')):
        raise ValueError('Legacy review page belongs to another shop')
    reviewed_at = store._epoch(raw.get('reviewedAt'), 'review.reviewedAt')
    if not store._epoch(snapshot['observedTo'], 'snapshot.observedTo') <= reviewed_at <= datetime.now(timezone.utc).timestamp():
        raise ValueError('Legacy review time must follow this snapshot and cannot be in the future')
    items = raw.get('items')
    if not isinstance(items, list) or not items:
        raise ValueError('Legacy review needs explicit image evidence entries')
    reviewed = {}
    for item in items:
        if not isinstance(item, dict):
            raise ValueError('Legacy review entry must be an object')
        url = item.get('imageUrl')
        if not isinstance(url, str) or url not in allowed_urls or url not in blocked or url in reviewed:
            raise ValueError('Legacy review URL must uniquely identify an exact blocked URL in this snapshot')
        refs = blocked[url]
        if any(ref.get('previous_attempt_reason') not in REVIEWABLE_LEGACY_REASONS for ref in refs):
            raise ValueError('Legacy review cannot override other historical restrictions')
        if any(ref['run_id'] == 'run_' + snapshot_sha[:24] for ref in refs):
            raise ValueError('Legacy review is only for a new snapshot; old observations stay blocked')
        ids = item.get('historicalObservationIds')
        if (not isinstance(ids, list) or any(type(v) is not int for v in ids)
                or sorted(ids) != sorted(ref['observation_id'] for ref in refs)):
            raise ValueError('Legacy review must bind every historical blocked observation')
        if (any(not isinstance(item.get(key), str) or not re.fullmatch(r'[0-9a-f]{64}', item[key])
                for key in ('receiptSha256', 'assetSha256'))
                or type(item.get('assetIndex')) is not int or item['assetIndex'] < 0):
            raise ValueError('Legacy review needs exact receipt SHA, asset index and asset SHA')
        acquired_at = store._epoch(item.get('acquiredAt'), 'review.acquiredAt')
        if not store._epoch(snapshot['observedFrom'], 'snapshot.observedFrom') <= acquired_at <= reviewed_at:
            raise ValueError('Legacy review acquisition must belong to the current capture/review window')
        reviewed[url] = item
    digest = hashlib.sha256(raw_bytes).hexdigest()
    record = {'path': str(path), 'sha256': digest, 'raw_bytes': raw_bytes,
              'archive_copy': f'receipts/review_{digest[:16]}.json', 'page_url': page_url,
              'reviewed_at': raw['reviewedAt'], 'provenance': 'explicit_coordinator_attestation'}
    return reviewed, record


def validate_reviewed_assets(reviewed, successes, failures):
    for url, item in reviewed.items():
        if failures.get(url):
            raise ValueError('Legacy review cannot override any current pageAssets failure or denial')
        refs = successes.get(url, [])
        if not any(ref['receipt_sha256'] == item['receiptSha256'] and ref['asset_index'] == item['assetIndex']
                   and ref['sha256'] == item['assetSha256'] for ref in refs):
            raise ValueError('Legacy review does not match an original successful receipt and verified asset bytes')
        if any(ref['sha256'] != item['assetSha256'] for ref in refs):
            raise ValueError('Legacy review cannot select among conflicting current image bytes')


def read_variant_review(path, snapshot, snapshot_sha, by_url, blocked, store):
    """Bind observed store-search encodings without claiming original URL bytes."""
    if path is None:
        return {}, []
    evidence = []
    def read_evidence(filename, claimed=None, label='variant'):
        filename=Path(filename).resolve();data=filename.read_bytes()
        if not 0<len(data)<=16*1024*1024:raise ValueError('Variant evidence size is invalid')
        digest=hashlib.sha256(data).hexdigest()
        if claimed is not None and claimed!=digest:raise ValueError('Variant evidence SHA mismatch')
        value=_strict_json(data)
        if not isinstance(value,dict):raise ValueError('Variant evidence must be an object')
        record={'path':str(filename),'sha256':digest,'raw_bytes':data,'archive_copy':f'receipts/{label}_{digest[:16]}.json'}
        evidence.append(record);return value,record
    raw,review_record=read_evidence(path,label='variant_review')
    if (raw.get('schemaVersion')!=1 or raw.get('snapshotSha256')!=snapshot_sha or raw.get('tool')!='cua_repl.pageAssets'
            or raw.get('basis')!='normal_storefront_search_variant'):
        raise ValueError('Variant review must bind the new snapshot and normal in-store search')
    reviewed_at=store._epoch(raw.get('reviewedAt'),'variant.reviewedAt')
    if not store._epoch(snapshot['observedTo'],'observedTo')<=reviewed_at<=datetime.now(timezone.utc).timestamp():
        raise ValueError('Variant review time is outside the current completed capture')
    nav_ref=raw.get('navigationEvidence',{})
    navigation,nav_record=read_evidence(nav_ref.get('path',''),nav_ref.get('sha256'),label='navigation')
    if nav_ref.get('sha256')!=nav_record['sha256']:raise ValueError('Variant navigation needs an explicit SHA')
    origin=urlsplit(navigation.get('fromPageUrl',''));source=urlsplit(snapshot['sourceUrl'])
    expected=store.shop_identity_evidence(snapshot);actual=store.shop_identity_evidence({'sourceUrl':navigation.get('fromPageUrl')})
    if (navigation.get('tool')!='cua_repl' or navigation.get('sameStoreConfirmed') is not True
            or not isinstance(navigation.get('actions'),list) or not navigation['actions'] or not all(isinstance(x,str) and x for x in navigation['actions'])
            or origin.scheme!='https' or origin.hostname!='mobile.yangkeduo.com' or origin.path!='/mall_page.html'
            or origin.username or origin.password or origin.port not in (None,443)
            or any(actual[k]!=expected[k] for k in ('identity_kind','stable_identifier'))):
        raise ValueError('Variant navigation is not an explicit same-store normal-page attestation')
    page_id=navigation.get('storefrontPageId');mall_id=navigation.get('searchMallId')
    if (not isinstance(page_id,str) or page_id not in parse_qs(origin.query).get('page_id',[])
            or not isinstance(mall_id,str) or not re.fullmatch(r'[1-9]\d*',mall_id)):
        raise ValueError('Variant navigation needs actual storefront page and search mall identifiers')
    nav_at=store._epoch(navigation.get('observedAt'),'navigation.observedAt')
    if not store._epoch(snapshot['observedFrom'],'observedFrom')<=nav_at<=reviewed_at:
        raise ValueError('Variant navigation must belong to this capture/review window')
    items=raw.get('items')
    if not isinstance(items,list) or not 0<len(items)<=9:raise ValueError('Variant review supports only explicit current legacy-error cards')
    variants={};used_acquisition=set()
    for item in items:
        if not isinstance(item,dict):raise ValueError('Invalid variant entry')
        url=item.get('originalImageUrl');acquired=item.get('image_acquisition_url')
        if not isinstance(url,str) or url not in by_url or url not in blocked or url in variants or acquired in used_acquisition:
            raise ValueError('Variant needs a unique exact blocked original URL and acquisition URL')
        refs=blocked[url]
        if (any(r.get('previous_attempt_reason') not in REVIEWABLE_LEGACY_REASONS for r in refs)
                or any(r['run_id']=='run_'+snapshot_sha[:24] for r in refs)):
            raise ValueError('Variant review cannot override other restrictions or modify an old snapshot')
        if sorted(item.get('historicalObservationIds',[]))!=sorted(r['observation_id'] for r in refs):
            raise ValueError('Variant historical observation binding mismatch')
        if sorted(item.get('viewOrders',[]))!=sorted(r['view_order'] for r in by_url[url]):
            raise ValueError('Variant must bind every exact original-URL card in the new snapshot')
        if any(r['raw'].get('title')!=item.get('title') for r in by_url[url]) or item.get('searchTitle')!=item.get('title'):
            raise ValueError('Variant original and publicly observed search titles must match exactly')
        inventory,inventory_record=read_evidence(item.get('inventoryPath',''),item.get('inventorySha256'),label='search_inventory')
        if item.get('inventorySha256')!=inventory_record['sha256'] or inventory.get('pageUrl')!=item.get('searchPageUrl'):
            raise ValueError('Variant inventory page/SHA binding mismatch')
        search=urlsplit(item.get('searchPageUrl',''));query=parse_qs(search.query)
        if (query.get('mall_id')!=[mall_id] or query.get('refer_page_id')!=[page_id]
                or query.get('refer_page_name')!=['mall_page']):
            raise ValueError('Variant search navigation identifiers do not match the attested storefront')
        matches=[a for a in inventory.get('assets',[]) if a.get('kind')=='image' and a.get('url')==acquired
                 and any(s.get('kind')=='attribute' and s.get('property') in ('src','data-src') for s in a.get('sources',[]))]
        if not matches:raise ValueError('Variant acquisition URL was not observed on a public search image element')
        acquired_at=store._epoch(item.get('acquiredAt'),'variant.acquiredAt')
        if not nav_at<=acquired_at<=reviewed_at:raise ValueError('Variant asset time is outside the attested navigation/review window')
        provenance={'method':'exact_cdn_path_store_search','snapshot_sha256':snapshot_sha,'original_image_url':url,
            'image_acquisition_url':acquired,'exact_pathname':urlsplit(url).path,'original_title':item['title'],'search_title':item['searchTitle'],
            'search_page_url':item['searchPageUrl'],'inventory_sha256':inventory_record['sha256'],'receipt_sha256':item.get('receiptSha256'),
            'asset_sha256':item.get('assetSha256'),'asset_index':item.get('assetIndex'),'review_sha256':review_record['sha256'],
            'acquired_at':item['acquiredAt'],'historical_observation_ids':item['historicalObservationIds'],'navigation_evidence_sha256':nav_record['sha256']}
        store.validate_image_variant({'acquisitionMethod':'authorized_store_search_variant','image_acquisition_url':acquired,
            'variant_provenance':provenance,'sha256':item.get('assetSha256')},original_url=url,title=item['title'],snapshot_sha=snapshot_sha)
        variants[url]={'imageUrl':acquired,'receiptSha256':item['receiptSha256'],'assetIndex':item['assetIndex'],
                       'assetSha256':item['assetSha256'],'provenance':provenance}
        used_acquisition.add(acquired)
    # The same original inventory may support several cards; copy it once.
    return variants,list({r['archive_copy']:r for r in evidence}.values())


def _strict_json(raw):
    def pairs(values):
        result = {}
        for key, value in values:
            if key in result:
                raise ValueError('Repeated JSON key in original pageAssets receipt')
            result[key] = value
        return result
    def bad(value):
        raise ValueError('Nonfinite value in original receipt')
    return json.loads(raw.decode('utf-8-sig'), object_pairs_hook=pairs, parse_constant=bad)


def _ordinary(path):
    mode = path.lstat()
    return not path.is_symlink() and not (getattr(mode, 'st_file_attributes', 0) & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0))


def _asset_root(raw):
    value = raw.get('directoryPath')
    if not isinstance(value, str):
        raise ValueError('Receipt needs its explicit pageAssets directoryPath')
    root = Path(value)
    if not root.is_absolute() or '..' in root.parts:
        raise ValueError('pageAssets directory must be absolute and contain no traversal')
    if root.parent.name != 'assets' or root.parent.parent.name != 'browser-use' or not re.fullmatch(r'[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}', root.name):
        raise ValueError('Require an explicit browser-use/assets/UUID pageAssets directory')
    if not root.is_dir() or any(not _ordinary(p) for p in (root, root.parent, root.parent.parent)):
        raise ValueError('pageAssets root cannot be missing, symbolic or redirected')
    return root.resolve()


def _asset_bytes(asset, root, store):
    value = asset.get('path')
    if not isinstance(value, str):
        raise ValueError('Downloaded page asset must identify its local path')
    original = Path(value)
    if not original.is_absolute() or '..' in original.parts or not original.is_relative_to(root):
        raise ValueError('Asset path is outside its explicit pageAssets directory')
    for path in (original, *original.parents):
        if not _ordinary(path):
            raise ValueError('Symbolic or redirected asset paths are not accepted')
        if path == root:
            break
    path = original.resolve()
    if not path.is_relative_to(root) or not path.is_file() or not stat.S_ISREG(path.stat().st_mode):
        raise ValueError('Asset must be an ordinary file inside its pageAssets directory')
    if not 0 < path.stat().st_size <= store.MAX_IMAGE_BYTES:
        raise ValueError('Asset size is outside importer bounds')
    data = path.read_bytes(); digest = hashlib.sha256(data).hexdigest(); mime = store._image_mime(data)
    if asset.get('contentType', '').split(';', 1)[0].strip().lower() != mime:
        raise ValueError('pageAssets MIME does not match local image bytes')
    if 'sha256' in asset and asset['sha256'] != digest:
        raise ValueError('pageAssets declared SHA does not match local bytes')
    for key in ('byteCount', 'byte_count'):
        if key in asset and (type(asset[key]) is not int or asset[key] != len(data)):
            raise ValueError('pageAssets declared byte count does not match local bytes')
    return {'sha256': digest, 'mime': mime, 'byte_count': len(data), 'data': data}, str(path)


def _failure_reason(failure):
    for key in ('reason', 'message', 'error'):
        value = failure.get(key)
        if isinstance(value, str) and value.strip():
            return value
        if isinstance(value, dict):
            return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return 'Original pageAssets failure did not provide a reason; preserved for review'


def _failure_blocked(failure):
    error = failure.get('error') if isinstance(failure.get('error'), dict) else {}
    return failure.get('blocked') is True or failure.get('status') in ('blocked', 'denied') or (
        failure.get('code') or error.get('code')) in ('policy_blocked', 'blocked_by_policy', 'permission_denied', 'approval_denied')


def read_receipts(receipt_paths, allowed_urls, blocked, store, reviewed=None):
    """Keep original receipts untouched; validate success and failure independently."""
    successes, failures = defaultdict(list), defaultdict(list)
    blobs, source_hashes, receipts, ignored = {}, {}, [], []
    for path in dict.fromkeys(Path(p).resolve() for p in receipt_paths):
        raw_bytes = path.read_bytes()
        if not 0 < len(raw_bytes) <= 16 * 1024 * 1024:
            raise ValueError('Original receipt has invalid size')
        raw = _strict_json(raw_bytes)
        if not isinstance(raw, dict) or not isinstance(raw.get('assets'), list) or not isinstance(raw.get('failures', []), list):
            raise ValueError('Original receipt needs assets and optional failures lists')
        assets, failed, summary = raw['assets'], raw.get('failures', []), raw.get('summary', {})
        if not isinstance(summary, dict) or type(summary.get('downloadedCount')) is not int or summary['downloadedCount'] != len(assets) or type(summary.get('failedCount')) is not int or summary['failedCount'] != len(failed):
            raise ValueError('Original receipt summary contradicts saved assets/failures')
        if 'requestedCount' in summary and (type(summary['requestedCount']) is not int or summary['requestedCount'] != len(assets) + len(failed)):
            raise ValueError('Original requestedCount disagrees with success plus failure entries')
        root = _asset_root(raw)
        receipt_sha = hashlib.sha256(raw_bytes).hexdigest()
        record = {'path': str(path), 'sha256': receipt_sha, 'directory_path': str(root),
                  'downloaded_count': len(assets), 'failed_count': len(failed), 'raw_bytes': raw_bytes,
                  'archive_copy': f'receipts/{len(receipts)+1:04d}_{receipt_sha[:16]}.json'}
        receipts.append(record)
        for index, asset in enumerate(assets):
            if not isinstance(asset, dict) or asset.get('kind') != 'image' or not isinstance(asset.get('url'), str) or not asset['url']:
                raise ValueError('Saved pageAssets entry must be an image with an exact URL')
            url = asset['url']
            if url in blocked and url not in (reviewed or {}):
                raise ValueError('Historical blocked URL appears in new downloaded assets; cannot package it as an authorized retry')
            if url not in allowed_urls:
                ignored.append({'receipt_sha256': receipt_sha, 'asset_index': index, 'url': url, 'reason': 'not referenced by sealed snapshot; local bytes not read'})
                continue
            value, source_path = _asset_bytes(asset, root, store)
            prior = source_hashes.get(source_path)
            if prior is not None and prior != value['sha256']:
                raise ValueError('The same local asset path changed between receipts')
            source_hashes[source_path] = value['sha256']; blobs[value['sha256']] = value
            successes[url].append({'receipt_sha256': receipt_sha, 'asset_index': index, 'source_path': source_path,
                                   'sha256': value['sha256'], 'mime': value['mime'], 'byte_count': value['byte_count']})
        for index, failure in enumerate(failed):
            if not isinstance(failure, dict) or not isinstance(failure.get('url'), str) or not failure['url'] or failure.get('kind', 'image') != 'image':
                raise ValueError('pageAssets failure must identify its exact image URL')
            if any(failure.get(key) for key in ('path', 'local_file', 'sha256')):
                raise ValueError('Failure entry cannot claim downloaded local bytes')
            if failure['url'] not in allowed_urls:
                ignored.append({'receipt_sha256': receipt_sha, 'failure_index': index, 'url': failure['url'], 'reason': 'failure URL not referenced by snapshot'})
                continue
            failures[failure['url']].append({'receipt_sha256': receipt_sha, 'failure_index': index,
                'reason': _failure_reason(failure), 'blocked': _failure_blocked(failure), 'original_failure': failure})
    return successes, failures, blobs, source_hashes, receipts, ignored


def _output_directory(output_dir, project, data_dir, workspace=None):
    """Bound caller-selected output without embedding any machine or user path."""
    requested = Path(output_dir)
    workspace_path = None
    if workspace is not None:
        workspace_path = Path(workspace)
        if not workspace_path.is_absolute() or not workspace_path.is_dir() or not _ordinary(workspace_path):
            raise ValueError('Explicit workspace must be an existing ordinary absolute directory')
        workspace_path = workspace_path.resolve()
        if not requested.is_absolute():
            requested = workspace_path / requested
    elif not requested.is_absolute():
        raise ValueError('Without --workspace, output must be an explicit authorized absolute external path')
    if '..' in requested.parts:
        raise ValueError('Output path cannot contain parent traversal')
    output = requested.resolve()
    if workspace_path is not None and (output == workspace_path or not output.is_relative_to(workspace_path)):
        raise ValueError('Output must be a child of the explicit workspace')
    if output.is_relative_to(project) or output.is_relative_to(data_dir):
        raise ValueError('Output must be outside the source project and databases')
    for path in (requested, *requested.parents):
        if path.exists() and not _ordinary(path):
            raise ValueError('Output cannot use symbolic or redirected directories')
    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        raise FileExistsError('Choose a new or empty output directory; never overwrite evidence')
    return output, workspace_path


def prepare_mixed_bundle(project, snapshot_path, receipt_paths, output_dir, *, data_dir=None, sealed=False, workspace=None, review=None, variant_review=None):
    project, snapshot_path = map(lambda p: Path(p).resolve(), (project, snapshot_path))
    data_dir = Path(data_dir).resolve() if data_dir is not None else project / 'data'
    if not sealed:
        raise ValueError('Only a separately sealed snapshot is accepted; use --sealed')
    output, workspace_path = _output_directory(output_dir, project, data_dir, workspace)
    store = load_store(project)
    snapshot, raw, _ = store._read_json(snapshot_path)
    if snapshot.get('status') not in ('complete', 'partial') or type(snapshot.get('endBoundaryObserved')) is not bool:
        raise ValueError('Sealed snapshot must have explicit complete/partial status and Boolean boundary')
    if snapshot['status'] == 'complete' and snapshot['endBoundaryObserved'] is not True:
        raise ValueError('Complete requires a genuinely observed end boundary')
    identity = store.shop_identity_evidence(snapshot)
    rows, _, _ = store._prepare_snapshot(snapshot)
    snapshot_sha = hashlib.sha256(raw).hexdigest()
    by_url = defaultdict(list)
    for row in rows:
        if row['image_url'] and row['raw'].get('imageUrl') != row['image_url']:
            raise ValueError('Image URL must match exact original text without normalization')
        by_url[row['image_url']].append(row)
    cache, blocked, cached_assets, source_db = read_cache(data_dir, store)
    allowed_urls = set(by_url)-{None,''}
    reviewed, review_record = read_legacy_review(review, snapshot, snapshot_sha, allowed_urls, blocked, store)
    variants, variant_evidence = read_variant_review(variant_review, snapshot, snapshot_sha, by_url, blocked, store)
    if set(reviewed)&set(variants):raise ValueError('One original URL cannot have both exact and variant review')
    allowed_urls.update(item['imageUrl'] for item in variants.values())
    successes, failures, downloads, source_files, receipts, ignored = read_receipts(receipt_paths, allowed_urls, blocked, store, reviewed)
    validate_reviewed_assets(reviewed, successes, failures)
    validate_reviewed_assets({v['imageUrl']:v for v in variants.values()},successes,failures)
    if any(failures.get(url) for url in variants):raise ValueError('Variant review cannot override current original-URL failures or denials')
    decisions, manifest_items, queue_items, block_items, used_assets = [], [], [], [], {}
    for url, cards in by_url.items():
        cached_refs, downloaded_refs, fail_refs = cache.get(url, []), successes.get(url, []), failures.get(url, [])
        variant=variants.get(url)
        if variant:
            cached_refs=[];downloaded_refs=successes[variant['imageUrl']];fail_refs=failures.get(variant['imageUrl'],[])
        hashes = sorted({ref['asset_sha256'] for ref in cached_refs} | {ref['sha256'] for ref in downloaded_refs})
        historical = blocked.get(url, [])
        policy_failed = [ref for ref in fail_refs if ref['blocked']]
        asset = None
        if not url:
            status, reason, action = 'missing_source_url', 'Original card has no source image URL', 'needs_source_url'
        elif (historical and url not in reviewed and not variant) or policy_failed:
            status, action = 'blocked_previous_attempt', 'requires_review'
            reason = '; '.join(dict.fromkeys([ref.get('previous_attempt_reason') or 'Historical restriction' for ref in historical] + [ref['reason'] for ref in policy_failed]))
        elif len(hashes) > 1:
            status, reason, action = 'unknown_source_conflict', 'Exact original URL has differing verified bytes; do not select one without review', 'requires_review'
        elif len(hashes) == 1:
            digest = hashes[0]
            source = cached_assets.get(digest) or downloads[digest]
            asset = {'sha256': digest, **source}
            status = 'reused_verified_cache' if cached_refs else 'saved_authorized_page_assets'
            if variant:status='saved_authorized_store_search_variant'
            reason, action = 'Exact URL and locally verified SHA/MIME/size', 'none'
        elif fail_refs:
            status, reason, action = 'failed_page_assets', '; '.join(dict.fromkeys(ref['reason'] for ref in fail_refs)), 'requires_review'
        else:
            status, reason, action = 'pending_image_stage', 'No verified cached or already downloaded bytes for this exact URL', 'await_authorized_image_stage'
        views = [row['view_order'] for row in cards]
        decision = {'image_url': url, 'view_orders': views, 'status': status, 'reason': reason, 'actionRequired': action,
                    'automaticRetryAllowed': False, 'eligibleForAuthorizedAcquisition': status == 'pending_image_stage',
                    'candidate_sha256': hashes, 'cache_references': cached_refs, 'page_asset_references': downloaded_refs,
                    'failure_references': fail_refs, 'blocked_source_references': historical}
        if url in reviewed:
            decision['historicalReview'] = {'reviewSha256': review_record['sha256'], **reviewed[url]}
        if variant:
            decision.update(image_acquisition_url=variant['imageUrl'],variant_provenance=variant['provenance'])
        decisions.append(decision)
        for row in cards:
            view = row['view_order']
            if asset:
                digest=asset['sha256'];member=f'images/{digest}.{EXTENSIONS[asset["mime"]]}'
                used_assets[digest] = asset
                manifest_items.append({'viewOrder': view, 'title': row['raw'].get('title'), 'originalImageUrl': url,
                    'archivePath': member, 'sha256': digest, 'mime': asset['mime'], 'byteCount': asset['byte_count'],
                    'acquisitionMethod': 'local_exact_url_cache_reuse' if cached_refs else 'authorized_page_assets',
                    'acquisitionReceiptSha256': sorted({ref['receipt_sha256'] for ref in downloaded_refs}),
                    'reuseObservationIds': [ref['observation_id'] for ref in cached_refs]})
                if url in reviewed:
                    manifest_items[-1]['historicalReview'] = decision['historicalReview']
                if variant:
                    manifest_items[-1].update(acquisitionMethod='authorized_store_search_variant',image_acquisition_url=variant['imageUrl'],variant_provenance=variant['provenance'])
            if url:
                queue_items.append({'imageUrl': url, 'rowViewOrders': [view], 'status': status, 'reason': reason,
                                    'actionRequired': action, 'reportedDownloadSuccess': bool(asset and downloaded_refs), 'automaticRetryAllowed': False})
                if url in reviewed:
                    queue_items[-1]['historicalReview'] = decision['historicalReview']
                    queue_items[-1]['historicalBlockedObservations'] = historical
                if variant:
                    queue_items[-1].update(image_acquisition_url=variant['imageUrl'],variant_provenance=variant['provenance'],historicalBlockedObservations=historical)
            if status == 'blocked_previous_attempt':
                block_items.append({'viewOrder': view, 'imageUrl': url, 'previousAttemptBlocked': True,
                                    'previousAttemptReason': reason, 'automaticRetryAllowed': False})
    manifest = {'schemaVersion':1,'snapshotSha256':snapshot_sha,'snapshotCardCount':len(rows),
                'availableCardImageCount':len(manifest_items),'items':sorted(manifest_items,key=lambda row:row['viewOrder']),
                'method':'exact_original_url_cache_and_original_page_assets_receipts'}
    queue = {'schemaVersion':1,'snapshotSha256':snapshot_sha,
             'observationWindow':{'fromUtc':snapshot['observedFrom'],'toUtc':snapshot['observedTo']},
             'automaticRetryAllowed':False,'networkRequestsMade':0,'items':queue_items,'blockedPreviousAttempts':block_items}
    missing = {'schemaVersion':1,'snapshotSha256':snapshot_sha,'automaticRetryAllowed':False,'networkRequestsMade':0,
               'items':[d for d in decisions if d['status'] not in ('reused_verified_cache','saved_authorized_page_assets','saved_authorized_store_search_variant')],
               'note':'URL grouping is only for image work; every original card remains a separate manifest/queue reference. Missing images do not block card import.'}
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.mixed_bundle_',dir=output) as temp:
        stage=Path(temp);(stage/'receipts').mkdir()
        for receipt in receipts:(stage/receipt['archive_copy']).write_bytes(receipt['raw_bytes'])
        if review_record:
            (stage/review_record['archive_copy']).write_bytes(review_record['raw_bytes'])
        for record in variant_evidence:(stage/record['archive_copy']).write_bytes(record['raw_bytes'])
        with zipfile.ZipFile(stage/'images.zip','x',compression=zipfile.ZIP_STORED) as archive:
            archive.writestr('manifest.json',json_bytes(manifest))
            for digest, asset in sorted(used_assets.items()):archive.writestr(f'images/{digest}.{EXTENSIONS[asset["mime"]]}',asset['data'])
        (stage/'image_queue.json').write_bytes(json_bytes(queue));(stage/'missing_urls.json').write_bytes(json_bytes(missing))
        loaded,_=store._load_archive(stage/'images.zip',rows);states,_=store._load_queue(stage/'image_queue.json',snapshot,rows)
        if len(loaded)!=len(manifest_items) or len(states)!=sum(len(cards) for url,cards in by_url.items() if url):
            raise ValueError('Importer did not reproduce card-image bindings')
        if any(not states[item['viewOrder']]['previousAttemptBlocked'] for item in block_items):
            raise ValueError('Importer did not reproduce blocked evidence')
        after_db={name:sha_file(data_dir/name) for name in source_db};check_sidecars([data_dir/name for name in source_db])
        if after_db!=source_db or sha_file(snapshot_path)!=snapshot_sha:
            raise ValueError('Snapshot or source database changed during packaging')
        if any(sha_file(Path(name))!=digest for name,digest in source_files.items()) or any(sha_file(Path(r['path']))!=r['sha256'] for r in receipts):
            raise ValueError('Original pageAssets receipt or local bytes changed during packaging')
        if review_record and sha_file(Path(review_record['path'])) != review_record['sha256']:
            raise ValueError('Legacy review changed during packaging')
        if any(sha_file(Path(r['path']))!=r['sha256'] for r in variant_evidence):raise ValueError('Variant evidence changed during packaging')
        counts=Counter()
        for decision in decisions:counts[decision['status']]+=len(decision['view_orders'])
        receipt_records=[{key:value for key,value in record.items() if key!='raw_bytes'} for record in receipts]
        acceptance={'status':'prepared_not_imported','created_at':datetime.now(timezone.utc).isoformat(),
            'output_directory':str(output),'explicit_workspace':str(workspace_path) if workspace_path else None,
            'project':str(project),'data_dir':str(data_dir),'snapshot':str(snapshot_path),'snapshot_sha256':snapshot_sha,
            'shop_identity':identity,'snapshot_status':snapshot['status'],'end_boundary_observed':snapshot['endBoundaryObserved'],
            'card_count':len(rows),'card_images':len(manifest_items),'unique_image_blobs':len(used_assets),
            'missing_card_images':len(rows)-len(manifest_items),'card_counts':dict(counts),
            'historical_blocked_urls':list(blocked),'historical_blocked_card_count':sum(map(len,blocked.values())),
            'legacy_review': {key:value for key,value in review_record.items() if key!='raw_bytes'} if review_record else None,
            'reviewed_legacy_card_count':sum(len(by_url[url]) for url in reviewed),
            'variant_card_count':sum(len(by_url[url]) for url in variants),
            'variant_evidence':[{k:v for k,v in r.items() if k!='raw_bytes'} for r in variant_evidence],
            'original_receipts':receipt_records,'local_asset_files_sha256':source_files,'ignored_unreferenced_entries':ignored,
            'database_sha256_before':source_db,'database_sha256_after':after_db,'source_unchanged':True,'input_files_unchanged':True,
            'network_requests_made':0,'database_writes_made':0,'importer_archive_and_queue_validated':True,
            'requires_review_card_count':sum(len(d['view_orders']) for d in decisions if d['actionRequired']=='requires_review'),
            'decisions':decisions,'output_files':{name:{'sha256':sha_file(stage/name),'bytes':(stage/name).stat().st_size} for name in OUTPUT_NAMES[:3]},
            'scope_note':'Image-only packaging; no import claimed, no card/title/identity/sales merging, no downloads or retries.',
            'importer_action_note':'For nonblocked missing bytes, current importer uses pending_image_stage; exact conflict/failure review action remains in source_state_json and this acceptance. Automatic retry stays disabled.'}
        (stage/'bundle_acceptance.json').write_bytes(json_bytes(acceptance))
        (output/'receipts').mkdir()
        for receipt in receipts:
            with (stage/receipt['archive_copy']).open('rb') as source,(output/receipt['archive_copy']).open('xb') as target:shutil.copyfileobj(source,target)
        if review_record:
            with (stage/review_record['archive_copy']).open('rb') as source,(output/review_record['archive_copy']).open('xb') as target:shutil.copyfileobj(source,target)
        for record in variant_evidence:
            with (stage/record['archive_copy']).open('rb') as source,(output/record['archive_copy']).open('xb') as target:shutil.copyfileobj(source,target)
        for name in OUTPUT_NAMES:
            with (stage/name).open('rb') as source,(output/name).open('xb') as target:shutil.copyfileobj(source,target)
    return acceptance


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project',required=True,type=Path);parser.add_argument('--snapshot',required=True,type=Path)
    parser.add_argument('--receipt',action='append',default=[],type=Path,help='Repeat for original pageAssets bundle receipts; failures and partial URL batches are supported')
    parser.add_argument('--data-dir',type=Path);parser.add_argument('--output-dir',required=True,type=Path);parser.add_argument('--sealed',action='store_true')
    parser.add_argument('--workspace',type=Path,help='Optional existing absolute authorized workspace; output must be its child. Otherwise supply an explicit authorized absolute external --output-dir.')
    parser.add_argument('--review',type=Path,help='Explicit current normal-page success evidence for the two reviewed legacy browser-error reasons only; never authorizes a download or changes old tasks')
    parser.add_argument('--variant-review',type=Path,help='Explicit same-store public-search image encoding evidence; labelled as a display variant, never original-URL bytes')
    args=parser.parse_args()
    result=prepare_mixed_bundle(args.project,args.snapshot,args.receipt,args.output_dir,data_dir=args.data_dir,sealed=args.sealed,workspace=args.workspace,review=args.review,variant_review=args.variant_review)
    print(json.dumps({key:result[key] for key in ('status','card_count','card_images','unique_image_blobs','missing_card_images','card_counts','requires_review_card_count')},ensure_ascii=False,indent=2))
