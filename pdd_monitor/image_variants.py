"""Dependency-free provenance checks shared by imports and standalone validation."""
from __future__ import annotations

from datetime import datetime
import re
from urllib.parse import parse_qs, urlsplit


def validate_image_variant(item: dict, *, original_url: str, title: str, snapshot_sha: str | None = None) -> dict | None:
    """Validate an explicitly labelled display encoding; it is not original-URL bytes."""
    provenance = item.get('variant_provenance')
    acquired = item.get('image_acquisition_url')
    if provenance is None:
        if acquired is not None and acquired != original_url:
            raise ValueError('Alternate image acquisition URL requires variant provenance')
        return None
    if not isinstance(provenance, dict) or item.get('acquisitionMethod') != 'authorized_store_search_variant':
        raise ValueError('Invalid image variant method')
    if not isinstance(acquired, str) or acquired == original_url:
        raise ValueError('Image variant must name its different actual acquisition URL')
    original, actual = urlsplit(original_url), urlsplit(acquired)
    for value in (original, actual):
        if value.scheme != 'https' or value.hostname not in ('img.pddpic.com', 'img-2.pddpic.com') or value.username or value.password or value.port not in (None,443):
            raise ValueError('Image variant requires observed normal PDD image CDN URLs')
    if not original.path or original.path != actual.path or provenance.get('exact_pathname') != original.path:
        raise ValueError('Image variant CDN pathname differs from original card')
    if (provenance.get('method') != 'exact_cdn_path_store_search'
            or provenance.get('original_image_url') != original_url
            or provenance.get('image_acquisition_url') != acquired
            or provenance.get('original_title') != title or provenance.get('search_title') != title):
        raise ValueError('Image variant original card or public search title binding mismatch')
    for field in ('snapshot_sha256','inventory_sha256','receipt_sha256','asset_sha256','review_sha256','navigation_evidence_sha256'):
        value = provenance.get(field)
        if not isinstance(value,str) or not re.fullmatch(r'[0-9a-f]{64}',value):
            raise ValueError('Image variant evidence needs complete SHA256 bindings')
    if provenance['asset_sha256'] != str(item.get('sha256','')).lower() or (snapshot_sha is not None and provenance['snapshot_sha256'] != snapshot_sha):
        raise ValueError('Image variant asset or snapshot SHA mismatch')
    search = urlsplit(provenance.get('search_page_url',''))
    if (search.scheme != 'https' or search.hostname != 'mobile.yangkeduo.com' or search.path != '/mall_search_result.html'
            or search.username or search.password or search.port not in (None,443)
            or len(parse_qs(search.query).get('mall_id',[])) != 1):
        raise ValueError('Image variant needs its normal observed in-store search page')
    if type(provenance.get('asset_index')) is not int or provenance['asset_index'] < 0:
        raise ValueError('Image variant needs an exact receipt asset index')
    historical = provenance.get('historical_observation_ids')
    if not isinstance(historical,list) or not historical or any(type(x) is not int or x<=0 for x in historical):
        raise ValueError('Image variant needs historical review references')
    value = provenance.get('acquired_at')
    try:
        if not isinstance(value, str) or not value.strip():
            raise ValueError('Missing acquisition time')
        parsed = datetime.fromisoformat(value.strip().replace('Z', '+00:00'))
        if parsed.tzinfo is None:
            raise ValueError('Timezone is required')
        parsed.timestamp()
    except (ValueError, OverflowError) as exc:
        raise ValueError('Invalid timezone-aware source observation time: variant.acquired_at') from exc
    return provenance
