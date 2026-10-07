"""Native store-search provenance; synthetic fixtures and temporary stores only."""
from copy import deepcopy
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import urlencode

from pdd_monitor.sku_store import validate_capture, validate_card_binding, save_capture, read_latest
from tests.test_sku_pipeline import (
    A, JOB, assets, capture, modal_capture, modal_observation,
    prepare_synthetic_backup, synthetic_db,
)


ORIGIN = 'https://mobile.yangkeduo.com'


def search_capture(row=None, *, status='complete'):
    row = row or modal_observation()
    result = modal_capture(row, status=status)
    result['search_evidence'] = {
        'method': 'native_store_search', 'source_store_url': row['shop_source_url'],
        'query': row['title'], 'search_page_url': ORIGIN + '/mall_search_result.html?'
            + urlencode({'mall_id': '1', 'search_key': row['title']}),
        'page_id': 7, 'shop_name_verified': True, 'native_search_opened': True,
        'native_search_submitted': True, 'same_tab_navigation_verified': True,
        'matching_title': row['title'], 'original_image_url': row['image_url'],
        'matched_image_url': row['image_url'], 'matched_card_count': 1,
        'live_sales_raw': '已拼21件', 'live_sales_value': 21,
        'live_sales_label': '已拼', 'live_sales_unit': '件',
        'modal_closed': True, 'return_verified': True, 'return_store_url': row['shop_source_url'],
    }
    return result


class NativeSearchEvidenceTests(unittest.TestCase):
    def reject_search(self, **changes):
        payload = search_capture()
        payload['search_evidence'].update(changes)
        with self.assertRaises(ValueError):
            validate_capture(payload)

    def test_search_keeps_canonical_sn_and_does_not_infer_a_public_goods_id(self):
        canonical = ORIGIN + '/mall_page.html?mall_sn=SYNTHETIC_OPAQUE'
        row = modal_observation(shop_source_url=canonical)
        payload = search_capture(row)
        payload['search_evidence']['source_store_url'] += '&refer_page_sn=1'
        payload['search_evidence']['search_page_url'] = ORIGIN + '/mall_search_result.html?' + urlencode(
            {'mall_id': '90001', 'search_key': row['title']})
        checked = validate_capture(payload)
        validate_card_binding(checked, row)
        self.assertEqual(checked['list_modal_evidence']['shop_source_url'], canonical)
        self.assertEqual(checked['source_card_evidence']['shop_source_url'], canonical)
        self.assertIsNone(checked['goods_id'])
        self.assertIsNone(checked['goods_url'])

    def test_old_modal_captures_stay_compatible_but_detail_captures_cannot_claim_search(self):
        validate_capture(modal_capture())
        payload = capture()
        payload['search_evidence'] = search_capture()['search_evidence']
        with self.assertRaises(ValueError):
            validate_capture(payload)

    def test_every_search_provenance_field_is_required(self):
        for key in search_capture()['search_evidence']:
            payload = search_capture()
            del payload['search_evidence'][key]
            with self.subTest(missing=key), self.assertRaises(ValueError):
                validate_capture(payload)
        for value in (None, [], True, 'native_store_search'):
            payload = search_capture()
            payload['search_evidence'] = value
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_capture(payload)

    def test_native_transition_flags_page_id_and_query_are_strict(self):
        for field in ('shop_name_verified', 'native_search_opened', 'native_search_submitted',
                      'same_tab_navigation_verified'):
            for value in (False, 1, 'true', None):
                with self.subTest(field=field, value=value):
                    self.reject_search(**{field: value})
        for value in (0, -1, True, '7', 7.0):
            with self.subTest(page_id=value):
                self.reject_search(page_id=value)
        for query in ('', ' ', ' leading', 'trailing ', 'x' * 121, 'a\nb', None):
            with self.subTest(query=query):
                self.reject_search(query=query)
        self.reject_search(method='direct_search_url')

    def test_store_entry_and_return_require_the_original_typed_identity(self):
        urls = [ORIGIN + '/mall_page.html?mall_id=2',
                ORIGIN + '/mall_page.html?mall_sn=1',
                ORIGIN + '/mall_page.html?mall_id=1&mall_id=1',
                ORIGIN + '/mall_search_result.html?mall_id=1',
                ORIGIN + '/mall_page.html?ps=UNKNOWN',
                ORIGIN + '/mall_page.html?mall_id=1#fragment',
                'https://user:pass@mobile.yangkeduo.com/mall_page.html?mall_id=1',
                'http://mobile.yangkeduo.com/mall_page.html?mall_id=1',
                'https://foreign.invalid/mall_page.html?mall_id=1']
        for field in ('source_store_url', 'return_store_url'):
            for url in urls:
                with self.subTest(field=field, url=url):
                    self.reject_search(**{field: url})

    def test_search_result_requires_exact_route_numeric_id_and_query_without_ambiguity(self):
        query = urlencode({'search_key': 'SYNTHETIC A'})
        urls = [ORIGIN + '/mall_search_result.html?mall_id=2&' + query,
                ORIGIN + '/mall_search_result.html?mall_id=1&mall_id=1&' + query,
                ORIGIN + '/mall_search_result.html?mall_id=0&' + query,
                ORIGIN + '/mall_search_result.html?mall_sn=1&' + query,
                ORIGIN + '/mall_search_result.html?mall_id=1&search_key=OTHER',
                ORIGIN + '/mall_search_result.html?mall_id=1&' + query + '&' + query,
                ORIGIN + '/mall_search_result.html?mall_id=1&mall_sn=OTHER&' + query,
                ORIGIN + '/mall_page.html?mall_id=1&' + query,
                ORIGIN + '/mall_search_result.html?mall_id=1&' + query + '#fragment',
                'https://foreign.invalid/mall_search_result.html?mall_id=1&' + query]
        for url in urls:
            with self.subTest(url=url):
                self.reject_search(search_page_url=url)

    def test_matching_card_needs_complete_exact_title_and_original_image(self):
        for changes in ({'matching_title': 'SYNTHETIC'}, {'matching_title': 'SYNTHETIC A '},
                        {'original_image_url': 'https://img.pddpic.com/OTHER.png'},
                        {'matched_card_count': 0}, {'matched_card_count': 2},
                        {'matched_card_count': True}):
            with self.subTest(changes=changes):
                self.reject_search(**changes)
        payload = search_capture()
        with self.assertRaises(ValueError):
            validate_card_binding(payload, modal_observation(current_unique_card_candidate_count=2))

    def test_image_aliases_preserve_path_and_exact_ordered_query_semantics(self):
        original = 'https://img.pddpic.com/SYNTHETIC.png?imageMogr2/thumbnail/500x&note=a+b'
        row = modal_observation(image_url=original)
        payload = search_capture(row)
        payload['search_evidence']['matched_image_url'] = (
            'https://img-2.pddpic.com/SYNTHETIC.png?imageMogr2%2Fthumbnail%2F500x&note=a%20b')
        validate_capture(payload)
        validate_card_binding(payload, row)
        for matched in ('https://img-2.pddpic.com/SYNTHETIC.png',
                        original.replace('500x', '200x'), original.replace('SYNTHETIC', 'OTHER'),
                        original.replace('img.pddpic.com', 'img-3.pddpic.com'),
                        original.replace('https:', 'http:'), original + '#fragment',
                        original + '&extra=1', original.replace('a+b', '%ZZ')):
            invalid = deepcopy(payload)
            invalid['search_evidence']['matched_image_url'] = matched
            with self.subTest(matched=matched), self.assertRaises(ValueError):
                validate_capture(invalid)

    def test_live_sales_are_independently_parsed_and_may_increase(self):
        payload = search_capture()
        validate_capture(payload)
        validate_card_binding(payload, modal_observation(sales_value=5, sales_label='已抢'))
        for changes in ({'live_sales_raw': '已抢21件'}, {'live_sales_raw': '已拼21+'},
                        {'live_sales_raw': '已拼1万件'}, {'live_sales_raw': '总售21件'},
                        {'live_sales_raw': '已拼21人'}, {'live_sales_raw': None},
                        {'live_sales_value': True}, {'live_sales_value': 20},
                        {'live_sales_value': 0}, {'live_sales_label': '已售'}, {'live_sales_unit': '人'}):
            with self.subTest(changes=changes):
                self.reject_search(**changes)
        payload['search_evidence'].update(live_sales_raw='已抢21件', live_sales_label='已抢')
        validate_capture(payload)
        validate_card_binding(payload, modal_observation(sales_label='已拼'))
        with self.assertRaises(ValueError):
            validate_card_binding(payload, modal_observation(sales_value=22))

    def test_observed_search_image_may_omit_only_webp_prefix_keeping_quality_and_thumbnail(self):
        original = ('https://img-2.pddpic.com/SYNTHETIC.png?'
                    'imageMogr2/format/webp/quality/90/thumbnail/500x9999%3E')
        matched = ('https://img.pddpic.com/SYNTHETIC.png?'
                   'imageMogr2/quality/90/thumbnail/500x9999%3E')
        row = modal_observation(image_url=original, sales_value=594)
        payload = search_capture(row)
        payload['search_evidence'].update(matched_image_url=matched,
            live_sales_raw='已抢598件', live_sales_label='已抢', live_sales_value=598)
        validate_capture(payload)
        validate_card_binding(payload, row)
        self.assertEqual(payload['list_modal_evidence']['image_url'], original)
        self.assertEqual(payload['search_evidence']['matched_image_url'], matched)
        self.assertEqual(row['sales_value'], 594)
        for wrong in (matched.replace('/quality/90', '/quality/80'),
                      matched.replace('/500x9999', '/200x9999'),
                      matched.replace('imageMogr2/', 'imageMogr2/format/png/'),
                      matched + '&extra=1', matched.replace('SYNTHETIC', 'OTHER')):
            value = deepcopy(payload)
            value['search_evidence']['matched_image_url'] = wrong
            with self.subTest(wrong=wrong), self.assertRaises(ValueError):
                validate_capture(value)

    def test_only_explicit_partial_cleanup_failures_can_retain_unclosed_search(self):
        for modal_closed, reason in ((False, 'sku_modal_cleanup_failed'), (True, 'sku_search_return_failed')):
            payload = search_capture(status='partial')
            payload.update(stop_reason=reason, stop_status='manual_review')
            payload['search_evidence'].update(modal_closed=modal_closed, return_verified=False, return_store_url=None)
            validate_capture(payload)
            for changes in ({'status': 'complete', 'all_combinations_visited': True},
                            {'all_combinations_visited': True}, {'stop_reason': 'sku_read_failed'}):
                with self.subTest(modal_closed=modal_closed, changes=changes), self.assertRaises(ValueError):
                    validate_capture({**deepcopy(payload), **changes})
        for changes in ({'modal_closed': False}, {'return_verified': False},
                        {'modal_closed': 1}, {'return_verified': 1}, {'return_store_url': None}):
            with self.subTest(changes=changes):
                self.reject_search(**changes)

    def test_search_roundtrip_preserves_original_scope_and_business_database_bytes(self):
        for partial in (False, True):
            with self.subTest(partial=partial), tempfile.TemporaryDirectory() as name:
                project = Path(name).resolve()
                synthetic_db(project)
                before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in (project / 'data').iterdir()}
                payload = search_capture(status='partial' if partial else 'complete')
                if partial:
                    payload['stop_reason'] = 'sku_search_return_failed'
                    payload['search_evidence'].update(return_verified=False, return_store_url=None)
                with patch('pdd_monitor.sku_store.prepare_batch_backup', side_effect=prepare_synthetic_backup):
                    receipt = save_capture(project, {'id': JOB, 'shop_id': A, 'observation_id': 1},
                                           payload, assets(), project / 'rehearsal')
                self.assertEqual(receipt['status'], payload['status'])
                self.assertEqual(receipt['image_count'], 1)
                saved = read_latest(project, A, 1)
                self.assertEqual(saved['search_evidence'], payload['search_evidence'])
                self.assertEqual(saved['observation_id'], 1)
                self.assertIn('店内搜索', saved['row_scope_note'])
                after = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in (project / 'data').iterdir()}
                self.assertEqual(before, after)


if __name__ == '__main__':
    unittest.main()
