from copy import deepcopy
import unittest
from unittest.mock import patch

from pdd_monitor.daily_warehouse import build_daily_warehouse
from pdd_monitor.history import _time_evidence
from pdd_monitor.warehouse_focus import build_warehouse_focus
from tests.test_daily_warehouse import run, card


class WarehouseFocusTests(unittest.TestCase):
    def test_comparison_periods_reuse_card_time_validation(self):
        older, yesterday, current = run('older', 3), run('yesterday', 4), run('current', 5)
        rows = [card(1, older, 10), card(2, yesterday, 20), card(3, current, 40)]
        with patch('pdd_monitor.warehouse_focus._time_evidence', wraps=_time_evidence) as validate_time:
            item = self.data([older, yesterday, current], rows)['warehouse_focus_items'][0]
        self.assertEqual((item['yesterday_delta'], item['day_before_yesterday_delta']), (20, 30))
        self.assertEqual(validate_time.call_count, 3)

    def test_card_time_cache_does_not_survive_a_build(self):
        old, current = run('old', 4), run('current', 5)
        rows = [card(1, old, 10), card(2, current, 40)]
        self.assertEqual(self.data([old, current], rows)['warehouse_focus_items'][0]['yesterday_status'], 'comparable')
        rows[1]['observed_at'] = 'invalid'
        item = self.data([old, current], rows)['warehouse_focus_items'][0]
        self.assertEqual(item['yesterday_status'], 'time_unverified')
        self.assertIsNone(item['yesterday_delta'])

    def data(self, runs, rows):
        before = deepcopy((runs, rows))
        result = build_warehouse_focus(runs, rows)
        self.assertEqual((runs, rows), before)
        return result

    def test_whole_catalogue_classes_are_exhaustive_no_monitor_age_filter(self):
        current = run('current', 5)
        rows = [card(1, current, 11, goods=None), card(2, current, 10, goods=None),
                card(3, current, 1, goods=None), card(4, current, 0, goods=None),
                card(5, current, 90, goods=None, sales_label='已抢'),
                card(6, current, 90, goods=None, sales_precision='approximate'),
                card(7, current, None, goods=None, sales_raw=None)]
        rows[0].update(monitor_origin='baseline_stock', monitor_first_date='2020-01-01')
        result = self.data([current], rows)
        summary = result['warehouse_focus_summary'][0]
        self.assertEqual([summary[k + '_count'] for k in ('yipin_gt10', 'yipin_1to10', 'yipin_zero', 'other_label', 'unknown')], [2, 2, 1, 0, 2])
        self.assertEqual(len(result['warehouse_focus_items']), 7)
        self.assertEqual(summary['card_count'], 7)

    def test_latest_complete_keeps_whole_store_after_newer_partial(self):
        old, current, partial = run('old', 4), run('current', 5), run('partial', 6, complete=False)
        result = self.data([partial, current, old], [card(1, old), card(2, current, 40), card(3, partial, 90)])
        summary = result['warehouse_focus_summary'][0]
        self.assertEqual((summary['run_id'], summary['latest_observed_run_id']), ('current', 'partial'))
        self.assertEqual(result['warehouse_focus_items'][0]['yesterday_delta'], 20)
        self.assertEqual(summary['yesterday_rapid_confirmed_count'], 1)

    def test_exact_yesterday_and_day_before_never_substitute_other_dates(self):
        old, current = run('old', 3), run('current', 5)
        result = self.data([old, current], [card(1, old, 10), card(2, current, 40)])
        item = result['warehouse_focus_items'][0]
        self.assertEqual(item['yesterday_status'], 'missing_baseline')
        self.assertIsNone(item['yesterday_delta'])
        self.assertEqual(item['day_before_yesterday_delta'], 30)
        self.assertEqual(item['day_before_yesterday_baseline_date'], '2026-10-03')

    def test_partial_date_is_not_full_baseline(self):
        old, current = run('old', 4, complete=False), run('current', 5)
        result = self.data([old, current], [card(1, old, 10), card(2, current, 40)])
        self.assertEqual(result['warehouse_focus_items'][0]['yesterday_status'], 'partial_baseline')
        self.assertIsNone(result['warehouse_focus_items'][0]['yesterday_delta'])

    def test_unique_title_and_exact_image_is_provisional_not_track_merge(self):
        old, current = run('old', 4), run('current', 5)
        rows = [card(1, old, 10, goods=None, image_url='https://images.example/exact.jpg'),
                card(2, current, 40, goods=None, image_url='https://images.example/exact.jpg')]
        result = self.data([old, current], rows)
        item = result['warehouse_focus_items'][0]
        self.assertEqual(item['yesterday_match_basis'], 'provisional_title_image')
        self.assertEqual(item['yesterday_delta'], 30)
        self.assertEqual(result['warehouse_focus_summary'][0]['yesterday_rapid_provisional_count'], 1)
        self.assertEqual(result['warehouse_focus_summary'][0]['yesterday_rapid_confirmed_count'], 0)
        warehouse = build_daily_warehouse([old, current], rows)
        self.assertEqual(len(warehouse['warehouse_tracks']), 2)
        self.assertTrue(all(p['daily_delta'] is None for p in warehouse['warehouse_points']))
        self.assertEqual(item['track_id'], warehouse['warehouse_records'][1]['track_id'])

    def test_no_title_only_image_only_or_transformed_url_guess(self):
        old, current = run('old', 4), run('current', 5)
        for extra in ({'image_url': None}, {'image_url': 'x?resize=20'}, {'title': 'Different', 'image_url': 'x'}):
            result = self.data([old, current], [card(1, old, 10, goods=None, image_url='x'), card(2, current, 40, goods=None, **extra)])
            self.assertEqual(result['warehouse_focus_items'][0]['yesterday_status'], 'unmatched')

    def test_verified_equal_image_bytes_can_supply_exact_image_clue(self):
        old, current = run('old', 4), run('current', 5)
        rows = [card(1, old, 10, goods=None, image_url='x', asset_sha256='a' * 64, image_content_status='verified_local'),
                card(2, current, 40, goods=None, image_url='y', asset_sha256='a' * 64, image_content_status='verified_local')]
        self.assertEqual(self.data([old, current], rows)['warehouse_focus_items'][0]['yesterday_match_basis'], 'provisional_title_image')
        rows[1]['image_content_status'] = 'not_saved'
        self.assertEqual(self.data([old, current], rows)['warehouse_focus_items'][0]['yesterday_status'], 'unmatched')

    def test_public_display_variant_does_not_supply_original_byte_equality(self):
        old, current = run('old', 4), run('current', 5)
        rows = [card(1, old, 10, goods=None, image_url='x', asset_sha256='a' * 64, image_content_status='verified_local'),
                card(2, current, 40, goods=None, image_url='y', asset_sha256='a' * 64, image_content_status='verified_local',
                     image_content_kind='store_search_variant', image_variant_provenance={'method': 'exact_cdn_path_store_search'},
                     image_acquisition_url='z')]
        item = self.data([old, current], rows)['warehouse_focus_items'][0]
        self.assertEqual(item['yesterday_status'], 'unmatched')
        self.assertEqual(item['image_acquisition_url'], 'z')
        self.assertEqual(item['image_content_kind'], 'store_search_variant')
        rows[1]['image_url'] = 'x'
        self.assertEqual(self.data([old, current], rows)['warehouse_focus_items'][0]['yesterday_match_basis'], 'provisional_title_image')

    def test_duplicate_clues_and_conflicting_goods_ids_never_pair(self):
        old, current = run('old', 4), run('current', 5)
        rows = [card(1, old, 10, goods=None, image_url='x'), card(2, old, 15, goods=None, image_url='x'), card(3, current, 40, goods=None, image_url='x')]
        self.assertEqual(self.data([old, current], rows)['warehouse_focus_items'][0]['yesterday_status'], 'ambiguous_identity')
        rows = [card(1, old, 10, goods='123', image_url='x'), card(2, current, 40, goods='456', image_url='x')]
        self.assertEqual(self.data([old, current], rows)['warehouse_focus_items'][0]['yesterday_status'], 'identity_conflict')

    def test_confirmed_pair_cannot_be_reused_as_other_cards_provisional_pair(self):
        old, current = run('old', 4), run('current', 5)
        rows = [card(1, old, 10, image_url='x'), card(2, current, 40, image_url='y', title='Changed title'),
                card(3, current, 50, goods=None, image_url='x')]
        items = self.data([old, current], rows)['warehouse_focus_items']
        self.assertEqual(items[0]['yesterday_match_basis'], 'confirmed_goods_id')
        self.assertEqual(items[1]['yesterday_status'], 'ambiguous_identity')

    def test_rapid_threshold_exact_boundary_zero_base_and_anomaly(self):
        old, current = run('old', 4), run('current', 5)
        for start, end, rapid, status, ratio in ((100, 130, True, 'comparable', .3), (100, 129, False, 'comparable', .29),
                                               (10, 19, False, 'comparable', .9), (0, 10, True, 'zero_baseline', None),
                                               (0, 9, False, 'zero_baseline', None), (50, 20, False, 'negative_anomaly', None)):
            item = self.data([old, current], [card(1, old, start), card(2, current, end)])['warehouse_focus_items'][0]
            self.assertEqual((item['yesterday_rapid_growth'], item['yesterday_status'], item['yesterday_growth_rate']), (rapid, status, ratio))
            if status == 'negative_anomaly':
                self.assertIsNone(item['yesterday_delta'])

    def test_sales_labels_missing_values_and_invalid_times_do_not_grow(self):
        old, current = run('old', 4), run('current', 5)
        for extra, status in (({'sales_label': '总售'}, 'sales_not_comparable'),
                              ({'sales_precision': 'approximate'}, 'sales_not_comparable'),
                              ({'sales_raw': None}, 'sales_not_comparable'),
                              ({'observed_at': old['observed_to']}, 'time_unverified')):
            item = self.data([old, current], [card(1, old, 10), card(2, current, 40, **extra)])['warehouse_focus_items'][0]
            self.assertEqual(item['yesterday_status'], status)
            self.assertFalse(item['yesterday_rapid_growth'])
            self.assertIsNone(item['yesterday_delta'])

    def test_same_goods_and_title_image_do_not_compare_across_shops(self):
        old, current = run('old', 4, shop='B'), run('current', 5)
        result = self.data([old, current], [card(1, old, 10, image_url='x'), card(2, current, 40, image_url='x')])
        self.assertEqual(len(result['warehouse_focus_summary']), 2)
        self.assertTrue(all(r['yesterday_status'] == 'missing_baseline' for r in result['warehouse_focus_items']))

    def test_same_day_uses_latest_full_and_beijing_date(self):
        old, later, current = run('old', 3, 16), run('later', 3, 17), run('current', 4, 16)
        result = self.data([old, later, current], [card(1, old, 10), card(2, later, 20), card(3, current, 40)])
        item = result['warehouse_focus_items'][0]
        self.assertEqual(item['date'], '2026-10-05')
        self.assertEqual(item['yesterday_baseline_run_id'], 'later')
        self.assertEqual(item['yesterday_delta'], 20)

    def test_only_partial_is_explicit_and_has_no_growth_claim(self):
        current = run('current', 5, complete=False)
        result = self.data([current], [card(1, current)])
        self.assertFalse(result['warehouse_focus_summary'][0]['is_complete'])
        self.assertEqual(result['warehouse_focus_items'][0]['yesterday_status'], 'target_incomplete')

    def test_image_ready_count_requires_verified_content(self):
        current = run('current', 5)
        result = self.data([current], [card(1, current, goods=None, image_url='x', image_content_status='verified_local'),
                                       card(2, current, goods=None, image_url='y', image_content_status='not_saved')])
        self.assertEqual(result['warehouse_focus_summary'][0]['image_ready_count'], 1)

    def test_orphan_and_duplicate_rows_are_rejected(self):
        current = run('current', 5)
        with self.assertRaises(ValueError):
            build_warehouse_focus([], [card(1, current)])
        with self.assertRaises(ValueError):
            build_warehouse_focus([current], [card(1, current), card(1, current)])
