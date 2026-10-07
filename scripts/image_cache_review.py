"""Narrow, evidence-bound reuse of already verified original image bytes.

This never authorizes an image download or retry. Historical restrictions remain
in the source database, including the restriction that was manually reviewed.
"""
from datetime import datetime
import json
import re
from urllib.parse import parse_qs, urlsplit

_SHA = re.compile(r'[0-9a-f]{64}')
_REASONS = frozenset({
    'download_timeout_followed_by_chrome_error_page',
    'safety_rejection_of_chrome_error_protocol_after_prior_failure',
})
_PREFIX = 'Inherited exact-URL restriction; no retry: '
_REF_FIELDS = ('run_id', 'observation_id', 'view_order', 'source_url', 'previous_attempt_reason')


def _check(condition):
    if not condition:
        raise ValueError('Invalid or unbound historical image restriction review')


def _shop_identity(raw, required_key=None):
    _check(isinstance(raw, str))
    value = urlsplit(raw)
    _check(value.scheme == 'https' and value.hostname == 'mobile.yangkeduo.com'
           and value.path == '/mall_page.html' and not value.username and not value.password
           and value.port in (None, 443))
    query = parse_qs(value.query)
    for key in (required_key,) if required_key else ('mall_id', 'mall_sn'):
        if key in query:
            _check(len(query[key]) == 1 and bool(query[key][0]))
            return key, query[key][0]
    _check(False)


def navigation_only_reason(reason):
    if not isinstance(reason, str) or not reason or len(reason) > 65536:
        return False
    # Only the packager's exact inheritance wrapper and the two reviewed
    # event classes are accepted; arbitrary text containing a keyword is not.
    value = reason.replace(_PREFIX, '')
    parts = value.split('; ')
    return bool(parts) and all(part in _REASONS for part in parts)


def block_reference(ref):
    _check(isinstance(ref, dict))
    result = {key: ref.get(key) for key in _REF_FIELDS}
    _check(isinstance(result['run_id'], str) and re.fullmatch(r'run_[0-9a-f]{24}', result['run_id']) is not None)
    _check(all(type(result[key]) is int and result[key] > 0 for key in ('observation_id', 'view_order')))
    _check(isinstance(result['source_url'], str) and navigation_only_reason(result['previous_attempt_reason']))
    return result


def _ref_key(ref):
    return json.dumps(block_reference(ref), sort_keys=True, ensure_ascii=False, separators=(',', ':'))


def validate_restriction_review(item, *, original_url, title, snapshot_sha, view_order, shop_source_url):
    review = item.get('historical_restriction_review')
    if review is None:
        return None
    _check(isinstance(review, dict) and type(review.get('schema_version')) is int and review.get('schema_version') == 1
           and review.get('decision') == 'verified_local_cache_reuse_only'
           and review.get('scope') == 'chrome_error_navigation_only'
           and item.get('acquisitionMethod') == 'browser_loaded_image_body')
    url = urlsplit(original_url)
    _check(url.scheme == 'https' and (url.hostname == 'pddpic.com' or (url.hostname or '').endswith('.pddpic.com'))
           and not url.username and not url.password and url.port in (None, 443))
    _check(review.get('original_image_url') == original_url == review.get('loaded_image_url')
           and review.get('title') == title and review.get('view_order') == view_order
           and type(review.get('view_order')) is int
           and review.get('shop_source_url') == shop_source_url)
    shop_identity = _shop_identity(shop_source_url)
    _check(_shop_identity(review.get('observed_page_url'), shop_identity[0]) == shop_identity)
    for key in ('asset_sha256', 'snapshot_sha256', 'inventory_sha256', 'review_sha256', 'receipt_sha256'):
        _check(isinstance(review.get(key), str) and _SHA.fullmatch(review[key]) is not None)
    _check(review['asset_sha256'] == item.get('sha256') and review['snapshot_sha256'] == snapshot_sha)
    _check(review.get('image_loaded') is True and type(review.get('matched_card_count')) is int
           and review['matched_card_count'] == 1 and review.get('forbidden_protocol_accessed') is False
           and review.get('independent_download') is False)
    authorization = review.get('authorization_reference')
    _check(isinstance(authorization, str) and 1 <= len(authorization.strip()) <= 1024)
    try:
        at = datetime.fromisoformat(review.get('reviewed_at', '').replace('Z', '+00:00'))
        _check(at.tzinfo is not None)
        at.timestamp()
    except (TypeError, ValueError, AttributeError, OverflowError) as exc:
        raise ValueError('Invalid historical image restriction review time') from exc
    refs = review.get('historical_block_refs')
    _check(isinstance(refs, list) and 0 < len(refs) <= 10000)
    keys = [_ref_key(ref) for ref in refs]
    _check(len(set(keys)) == len(keys) and all(ref['source_url'] == original_url for ref in refs))
    return review


def cache_reuse_policy(candidates, blocked):
    """One hash plus covered navigation-only history allows LOCAL bytes only."""
    reusable, active_blocked, reviewed = set(), set(blocked), {}
    for url, references in candidates.items():
        hashes = {ref['asset_sha256'] for ref in references}
        if len(hashes) != 1:
            continue
        restrictions = blocked.get(url, [])
        if not restrictions:
            reusable.add(url)
            continue
        try:
            required = {_ref_key(ref) for ref in restrictions}
        except ValueError:
            continue
        reviews = [ref['historical_restriction_review'] for ref in references
                   if ref.get('historical_restriction_review')]
        covered = set()
        for review in reviews:
            # Reviews reaching this function were strictly validated by read_cache.
            if review['asset_sha256'] in hashes and review['original_image_url'] == url:
                covered.update(_ref_key(ref) for ref in review['historical_block_refs'])
        if required and required <= covered:
            reusable.add(url)
            active_blocked.discard(url)
            reviewed[url] = reviews
    return {'skip_image_urls': sorted(reusable), 'blocked_image_urls': sorted(active_blocked),
            'reviewed_cache_evidence': reviewed}
