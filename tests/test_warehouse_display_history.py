from copy import deepcopy
import unittest

from pdd_monitor.daily_warehouse import build_daily_warehouse
from pdd_monitor.warehouse_display_history import build_display_history
from tests.test_daily_warehouse import run, card


class DisplayHistoryTests(unittest.TestCase):
    def history(self, runs, rows, anchor=None):
        before = deepcopy((runs, rows))
        result = build_display_history(runs, rows)
        self.assertEqual((runs, rows), before)
        return [point for point in result if anchor is None or point['anchor_observation_id'] == anchor]

    def test_low_and_high_sales_both_keep_all_three_daily_points(self):
        runs = [run('a', 3), run('b', 4), run('c', 5)]
        rows = [card(1, runs[0], 0, goods=None, image_url='low', title='LOW'),
                card(2, runs[0], 20, goods=None, image_url='high', title='HIGH'),
                card(3, runs[1], 3, goods=None, image_url='low', title='LOW'),
                card(4, runs[1], 30, goods=None, image_url='high', title='HIGH'),
                card(5, runs[2], 8, goods=None, image_url='low', title='LOW'),
                card(6, runs[2], 44, goods=None, image_url='high', title='HIGH')]
        low = self.history(runs, rows, 5)
        high = self.history(runs, rows, 6)
        self.assertEqual([p['cumulative_yipin'] for p in low], [0, 3, 8])
        self.assertEqual([p['daily_delta'] for p in low], [None, 3, 5])
        self.assertEqual([p['daily_delta'] for p in high], [None, 10, 14])
        self.assertEqual(low[0]['anchor_category'], 'yipin_1to10')
        self.assertEqual(high[0]['anchor_category'], 'yipin_gt10')
        self.assertEqual(low[0]['match_basis'], 'provisional_title_image')
        self.assertEqual(low[-1]['match_basis'], 'current_card')
        self.assertEqual(low[-1]['baseline_observation_id'], 3)
        # The separate reliable-ID store remains strictly unmerged.
        actual = build_daily_warehouse(runs, rows)
        self.assertEqual(len(actual['warehouse_tracks']), 6)
        self.assertTrue(all(p['daily_delta'] is None for p in actual['warehouse_points']))

    def test_reliable_id_history_survives_public_title_and_image_changes(self):
        a, b = run('a', 4), run('b', 5)
        points = self.history([a, b], [card(1, a, 10, title='OLD', image_url='old'),
                                       card(2, b, 20, title='NEW', image_url='new')], 2)
        self.assertEqual(points[0]['match_basis'], 'confirmed_goods_id')
        self.assertEqual(points[-1]['delta_match_basis'], 'confirmed_goods_id')
        self.assertEqual(points[-1]['daily_delta'], 10)
        self.assertEqual(points[0]['title'], 'OLD')

    def test_duplicate_old_title_image_is_explicit_null_not_arbitrary_pair(self):
        a, b = run('a', 4), run('b', 5)
        points = self.history([a, b], [card(1, a, 1, goods=None, image_url='x'), card(2, a, 7, goods=None, image_url='x'),
                                       card(3, b, 9, goods=None, image_url='x')], 3)
        self.assertEqual(points[0]['point_status'], 'ambiguous_identity')
        self.assertIsNone(points[0]['observation_id'])
        self.assertIsNone(points[0]['cumulative_yipin'])
        self.assertIsNone(points[-1]['daily_delta'])
        self.assertTrue(points[-1]['break_before'])

    def test_duplicate_current_title_image_cannot_share_one_old_card(self):
        a, b = run('a', 4), run('b', 5)
        rows = [card(1, a, 1, goods=None, image_url='x'), card(2, b, 7, goods=None, image_url='x'), card(3, b, 9, goods=None, image_url='x')]
        points = self.history([a, b], rows)
        self.assertEqual(len(points), 4)
        self.assertEqual(sum(p['point_status'] == 'ambiguous_identity' for p in points), 2)
        self.assertTrue(all(p['daily_delta'] is None for p in points))

    def test_missing_day_is_not_filled_or_used_as_daily_increment(self):
        a, b = run('a', 3), run('b', 5)
        points = self.history([a, b], [card(1, a, 10), card(2, b, 50)], 2)
        self.assertEqual([p['date'] for p in points], ['2026-10-03', '2026-10-05'])
        self.assertEqual(points[1]['delta_status'], 'missing_previous_day')
        self.assertTrue(points[1]['break_before'])
        self.assertIsNone(points[1]['daily_delta'])

    def test_partial_day_is_a_null_gap_and_later_partial_does_not_replace_full(self):
        a, partial, b, later = run('a', 3), run('p', 4, complete=False), run('b', 5), run('later', 5, 2, False)
        points = self.history([a, partial, b, later], [card(1, a, 10), card(2, partial, 25), card(3, b, 40), card(4, later, 99)], 3)
        self.assertEqual([p['run_id'] for p in points], ['a', 'p', 'b'])
        self.assertEqual(points[1]['point_status'], 'incomplete_day')
        self.assertIsNone(points[1]['observation_id'])
        self.assertIsNone(points[1]['cumulative_yipin'])
        self.assertTrue(points[2]['break_before'])
        self.assertEqual(points[2]['cumulative_yipin'], 40)

    def test_same_day_latest_complete_is_one_point_and_first_date_not_limited_to_30_days(self):
        older = run('old', 1)
        older.update(observed_from='2026-07-01T01:00:00Z', observed_to='2026-07-01T01:10:00Z')
        a, b = run('a', 5), run('b', 5, 2)
        points = self.history([older, a, b], [card(1, older, 2), card(2, a, 11), card(3, b, 12)], 3)
        self.assertEqual([p['date'] for p in points], ['2026-07-01', '2026-10-05'])
        self.assertEqual([p['cumulative_yipin'] for p in points], [2, 12])

    def test_other_store_matching_id_title_and_image_is_excluded(self):
        a, b = run('a', 4, shop='B'), run('b', 5)
        points = self.history([a, b], [card(1, a, 2, image_url='x'), card(2, b, 8, image_url='x')], 2)
        self.assertEqual(len(points), 1)
        self.assertEqual(points[0]['shop_id'], 'A')
        self.assertIsNone(points[0]['daily_delta'])

    def test_missing_sales_other_labels_and_negative_changes_keep_source_without_fake_increment(self):
        a, b = run('a', 4), run('b', 5)
        for extra, expected in (({'sales_label': '总售'}, 'sales_not_comparable'),
                                ({'sales_precision': 'approximate'}, 'sales_not_comparable'),
                                ({}, 'negative_anomaly')):
            points = self.history([a, b], [card(1, a, 20), card(2, b, 10, **extra)], 2)
            self.assertEqual(points[-1]['delta_status'], expected)
            self.assertIsNone(points[-1]['daily_delta'])
        self.assertEqual(points[-1]['cumulative_yipin'], 10)

    def test_invalid_observation_time_is_a_null_point(self):
        a, b = run('a', 4), run('b', 5)
        points = self.history([a, b], [card(1, a, 2, observed_at=b['observed_to']), card(2, b, 8)], 2)
        self.assertEqual(points[0]['point_status'], 'time_unverified')
        self.assertIsNone(points[0]['cumulative_yipin'])
        self.assertIsNone(points[0]['display_price_yuan'])
        self.assertTrue(points[1]['break_before'])

    def test_prices_keep_conditions_and_unparseable_ranges(self):
        a, b, c = run('a', 3), run('b', 4), run('c', 5)
        points = self.history([a, b, c], [card(1, a, price_raw='券后¥4.5'), card(2, b, price_raw='¥5'), card(3, c, price_raw='¥3-6')], 3)
        self.assertEqual([p['display_price_yuan'] for p in points], [4.5, 5, None])
        self.assertEqual(points[0]['price_condition'], 'coupon_after_display')
        self.assertEqual(points[1]['price_condition'], 'display_unspecified')

    def test_variant_sha_is_not_original_byte_matching_evidence(self):
        a, b = run('a', 4), run('b', 5)
        rows = [card(1, a, 2, goods=None, image_url='old', asset_sha256='a' * 64, image_content_status='verified_local'),
                card(2, b, 8, goods=None, image_url='new', asset_sha256='a' * 64, image_content_status='verified_local',
                     image_content_kind='store_search_variant', image_variant_provenance={'method': 'exact_cdn_path_store_search'})]
        points = self.history([a, b], rows, 2)
        self.assertEqual(points[0]['point_status'], 'unmatched')
        rows[1].pop('image_variant_provenance')
        rows[1]['image_content_kind'] = 'original_url_content'
        self.assertEqual(self.history([a, b], rows, 2)[0]['point_status'], 'matched')

    def test_anchor_matches_do_not_silently_create_transitive_daily_identity(self):
        a, b, c = run('a', 3), run('b', 4), run('c', 5)
        rows = [card(1, a, 1, goods=None, image_url='x', asset_sha256='a' * 64, image_content_status='verified_local'),
                card(2, b, 3, goods=None, image_url='y', asset_sha256='b' * 64, image_content_status='verified_local'),
                card(3, c, 8, goods=None, image_url='x', asset_sha256='b' * 64, image_content_status='verified_local')]
        points = self.history([a, b, c], rows, 3)
        self.assertEqual([p['cumulative_yipin'] for p in points], [1, 3, 8])
        self.assertEqual(points[1]['delta_status'], 'identity_unverified')
        self.assertTrue(points[1]['break_before'])
        self.assertIsNone(points[1]['daily_delta'])
        self.assertEqual(points[2]['daily_delta'], 5)

    def test_only_partial_store_has_explicit_null_history(self):
        a = run('a', 5, complete=False)
        points = self.history([a], [card(1, a, 8)])
        self.assertEqual(points[0]['point_status'], 'target_incomplete')
        self.assertIsNone(points[0]['observation_id'])
        self.assertIsNone(points[0]['cumulative_yipin'])
