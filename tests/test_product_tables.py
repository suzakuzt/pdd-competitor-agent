import json
import math
from copy import deepcopy
import unittest

from pdd_monitor.product_tables import build_product_table_queries


def run(rid, shop='a', day=1, complete=True):
    return {'run_id': rid, 'shop_id': shop, 'shop_name': '合成店' + shop,
            'observed_from': f'2026-01-{day:02}T01:00:00Z', 'observed_to': f'2026-01-{day:02}T01:10:00Z',
            'status': 'complete' if complete else 'partial', 'end_boundary_observed': complete}


def card(oid, rid, value=11, **kwargs):
    row = {'observation_id': oid, 'run_id': rid, 'view_order': oid, 'title': '合成卡', 'normalized_title': '合成卡',
           'goods_id': None, 'goods_url': None, 'identity_status': 'unknown_no_goods_id',
           'sales_raw': f'已拼{value}件', 'sales_value': value, 'sales_label': '已拼', 'sales_unit': '件',
           'sales_precision': 'exact_display', 'price_raw': '¥4.50', 'image_url': f'https://example.invalid/{oid}.png',
           'image_status': 'pending', 'asset_sha256': None, 'image_content_status': 'not_saved',
           'observed_at': None, 'observed_at_precision': 'unknown'}
    row.update(kwargs)
    return row


def tracking(runs, cards, baseline='r1', shop='a'):
    old = next(row for row in runs if row['run_id'] == baseline)
    return {'shop_id': shop, 'tracking_id': 'tracking_' + shop, 'state': 'candidates_available',
            'started_at': old['observed_to'], 'baseline_run_ids': [baseline],
            'baseline_observation_count': sum(row['run_id'] == baseline for row in cards),
            'baseline_coverage_status': 'complete_reference_available' if old['status'] == 'complete' else 'partial_only',
            'baseline_coverage_note': '合成起点覆盖说明'}


def arrival(row, runrow, expansion=False, **kwargs):
    value = {'arrival_item_id': 'arrival_' + str(row['observation_id']), 'shop_id': runrow['shop_id'],
        'tracking_id': 'tracking_' + runrow['shop_id'], 'run_id': row['run_id'], 'observation_id': row['observation_id'],
        'discovery_kind': 'new_card_clue', 'coverage_expansion_possible': expansion, 'newness': 'unknown',
        'first_observed_at': row.get('observed_at'), 'first_observed_at_precision': row.get('observed_at_precision'),
        'first_time_order_status': 'observed_run_order', 'first_observation_id_candidates': [row['observation_id']],
        'first_observation_window': {'from': runrow['observed_from'], 'to': runrow['observed_to']},
        'latest_observation_ids': [row['observation_id']], 'historical_references': [], 'subsequent_references': []}
    value.update(kwargs)
    return value


def queries(runs, cards, summaries=None, arrivals=None):
    return {key: {'rows': rows, 'source': {'files': ['SYNTHETIC'], 'tables': [key]}}
            for key, rows in [('runs', runs), ('observations', cards), ('new_arrival_summary', summaries or []),
                              ('new_arrival_items', arrivals or [])]}


def result(q):
    value = build_product_table_queries(q)
    return value['product_table_shops']['rows'], value['product_table_items']['rows']


class ProductTableTests(unittest.TestCase):
    def test_latest_complete_per_shop_and_latest_partial_stay_separate(self):
        rs = [run('r1'), run('r2', day=2, complete=False), run('b1', 'b')]
        rows = [card(1, 'r1', 12), card(2, 'r2', 999), card(3, 'b1', 13)]
        shops, items = result(queries(rs, rows))
        a = next(row for row in shops if row['shop_id'] == 'a')
        self.assertEqual((a['reference_run_id'], a['latest_run_id'], a['hot_count']), ('r1', 'r2', 1))
        self.assertEqual({row['observation_id'] for row in items}, {1, 3})
        self.assertEqual(a['history_observation_count'], 2)

    def test_no_complete_reference_does_not_promote_partial_heat(self):
        shops, items = result(queries([run('r1', complete=False)], [card(1, 'r1', 900)]))
        self.assertEqual(items, [])
        self.assertEqual(shops[0]['reference_state'], 'no_complete_reference')
        self.assertIsNone(shops[0]['reference_run_id'])
        self.assertEqual(shops[0]['latest_card_count'], 1)

    def test_strict_gt10_label_unit_integer_and_null_boundaries(self):
        rows = [card(1, 'r1', 11), card(2, 'r1', 10), card(3, 'r1', 9), card(4, 'r1', None),
                card(5, 'r1', 99, sales_label='已抢'), card(6, 'r1', 99, sales_unit='人'),
                card(7, 'r1', 99, sales_precision='fuzzy_display'), card(8, 'r1', True),
                card(9, 'r1', math.nan), card(10, 'r1', 11.0), card(11, 'r1', 99, sales_raw=None)]
        shops, items = result(queries([run('r1')], rows))
        self.assertEqual([row['observation_id'] for row in items], [5, 1])
        self.assertEqual(shops[0]['hot_count'], 2)

    def test_no_baseline_is_unknown_not_first_launch(self):
        shops, items = result(queries([run('r1')], [card(1, 'r1')]))
        self.assertEqual(shops[0]['baseline_state'], 'no_baseline')
        self.assertEqual(shops[0]['new_watch_count'], 0)
        self.assertEqual(items[0]['age_label'], '新旧未知')
        self.assertEqual(items[0]['newness'], 'unknown')

    def test_coverage_lane_retained_and_overlap_not_added(self):
        rs = [run('r1', complete=False), run('r2', day=2)]
        rows = [card(1, 'r1'), card(2, 'r2', 30)]
        shops, items = result(queries(rs, rows, [tracking(rs, rows)], [arrival(rows[1], rs[1], True)]))
        self.assertEqual({row['lane'] for row in items}, {'coverage_review', 'hot'})
        self.assertEqual(shops[0]['new_watch_count'], 0)
        self.assertEqual(shops[0]['coverage_review_count'], 1)
        self.assertEqual(shops[0]['hot_count'], 1)
        self.assertEqual(shops[0]['distinct_display_observation_count'], 1)
        self.assertEqual(shops[0]['overlap_observation_count'], 1)
        self.assertTrue(all('旧存量' in row['newness_label'] for row in items))

    def test_reliable_id_preserves_history_but_displays_reference_sales(self):
        rs = [run('r1'), run('r2', day=2, complete=False), run('b1', 'b')]
        rows = [card(1, 'r1', 12, goods_id='55', identity_status='unique_goods_id'),
                card(2, 'r2', 99, goods_id='55', identity_status='unique_goods_id'),
                card(3, 'b1', 13, goods_id='55', identity_status='unique_goods_id')]
        shops, items = result(queries(rs, rows))
        a = next(row for row in items if row['shop_id'] == 'a')
        b = next(row for row in items if row['shop_id'] == 'b')
        self.assertEqual(a['source_observation_ids'], [1, 2])
        self.assertEqual(a['sales_value'], 12)
        self.assertEqual(b['source_observation_ids'], [3])
        self.assertEqual(a['merge_basis'], 'same_unique_goods_id')

    def test_duplicate_goods_id_or_url_conflict_never_strongly_merges(self):
        rs = [run('r1'), run('r2', day=2)]
        for url in ['https://mobile.yangkeduo.com/goods.html?goods_id=66',
                    'https://mobile.yangkeduo.com/goods.html?goods_id=55&goods_id=55',
                    'https://mobile.yangkeduo.com/goods.html?goods_id=']:
            with self.subTest(url=url):
                rows = [card(1, 'r1', goods_id='55', identity_status='unique_goods_id'),
                        card(2, 'r2', goods_id='55', identity_status='unique_goods_id', goods_url=url)]
                _, items = result(queries(rs, rows))
                self.assertEqual(items[0]['source_observation_ids'], [2])
                self.assertFalse(items[0]['identity_reliable'])
        rows = [card(1, 'r1', goods_id='55', identity_status='unique_goods_id'),
                card(2, 'r2', goods_id='55', identity_status='unique_goods_id'),
                card(3, 'r2', goods_id='55', identity_status='unique_goods_id')]
        _, items = result(queries(rs, rows))
        self.assertEqual([row['source_observation_ids'] for row in items], [[2], [3]])

    def test_missing_id_same_title_image_only_display_fold_not_merge(self):
        rows = [card(1, 'r1', 12, image_url='https://example.invalid/same.png'),
                card(2, 'r1', 20, image_url='https://example.invalid/same.png'),
                card(3, 'r1', 30, image_url='https://example.invalid/other.png'),
                card(4, 'r1', 40, image_url=None)]
        shops, items = result(queries([run('r1')], rows))
        self.assertEqual(len(items), 4)
        self.assertTrue(all(len(row['source_observation_ids']) == 1 for row in items))
        self.assertEqual(shops[0]['hot_display_group_count'], 3)
        self.assertEqual([row['sales_value'] for row in items], [40, 30, 20, 12])

    def test_same_url_conflicting_verified_images_cannot_fold_even_through_unknown_image(self):
        rows = [card(1, 'r1', image_url='https://example.invalid/same.png', asset_sha256='a' * 64, image_content_status='verified_local'),
                card(2, 'r1', image_url='https://example.invalid/same.png'),
                card(3, 'r1', image_url='https://example.invalid/same.png', asset_sha256='b' * 64, image_content_status='verified_local')]
        shops, items = result(queries([run('r1')], rows))
        self.assertEqual(shops[0]['hot_display_group_count'], 2)
        self.assertEqual(len(items), 3)

    def test_weak_subsequent_card_is_reference_not_merged_or_replaced(self):
        rs = [run('r1'), run('r2', day=2), run('r3', day=3)]
        rows = [card(1, 'r1', 1), card(2, 'r2', 1, image_url='https://example.invalid/same.png'),
                card(3, 'r3', 99, image_url='https://example.invalid/same.png')]
        candidate = arrival(rows[1], rs[1], subsequent_references=[{'observation_id': 3, 'run_id': 'r3', 'match_basis': 'exact_title_original_image_url_clue_only'}], latest_observation_ids=[3])
        _, items = result(queries(rs, rows, [tracking(rs, rows)], [candidate]))
        first = next(row for row in items if row['lane'] == 'new_watch')
        self.assertEqual(first['observation_id'], 2)
        self.assertEqual(first['source_observation_ids'], [2])
        self.assertEqual(first['related_observation_ids'], [2, 3])
        self.assertEqual(first['sales_value'], 1)

    def test_cross_shop_arrival_or_baseline_evidence_aborts(self):
        rs = [run('r1'), run('r2', day=2), run('b1', 'b', day=2)]
        rows = [card(1, 'r1'), card(2, 'r2'), card(3, 'b1')]
        base = tracking(rs, rows)
        bad = arrival(rows[1], rs[1], shop_id='b')
        with self.assertRaisesRegex(ValueError, 'arrival crosses shops'):
            result(queries(rs, rows, [base], [bad]))
        bad = arrival(rows[1], rs[1], historical_references=[{'observation_id': 3, 'run_id': 'b1'}])
        with self.assertRaisesRegex(ValueError, 'reference crosses'):
            result(queries(rs, rows, [base], [bad]))
        base['baseline_run_ids'] = ['b1']
        with self.assertRaisesRegex(ValueError, 'baseline crosses'):
            result(queries(rs, rows, [base]))

    def test_arrival_without_baseline_or_first_point_window_tampering_aborts(self):
        rs = [run('r1'), run('r2', day=2)]
        rows = [card(1, 'r1'), card(2, 'r2')]
        item = arrival(rows[1], rs[1])
        with self.assertRaisesRegex(ValueError, 'matching fixed baseline'):
            result(queries(rs, rows, [], [item]))
        for changes in [{'first_observed_at': '2026-01-03T01:00:00Z'},
                        {'first_observed_at': '2026-01-02T01:01:00'},
                        {'first_observation_window': {'from': rs[0]['observed_from'], 'to': rs[1]['observed_to']}},
                        {'first_time_order_status': 'overlapping_runs_unknown'}]:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                result(queries(rs, rows, [tracking(rs, rows)], [{**item, **changes}]))

    def test_valid_overlapping_arrival_keeps_unknown_times_and_all_clue_refs(self):
        rs = [run('r1'), run('r2', day=2), run('r3', day=2)]
        rs[2].update(observed_from='2026-01-02T01:05:00Z', observed_to='2026-01-02T01:15:00Z')
        rows = [card(1, 'r1'), card(2, 'r2', image_url='https://example.invalid/same.png'),
                card(3, 'r3', image_url='https://example.invalid/same.png')]
        item = arrival(rows[1], rs[1], first_time_order_status='overlapping_runs_unknown',
            first_observation_id_candidates=[2, 3], first_observation_window={'from': rs[1]['observed_from'], 'to': rs[2]['observed_to']},
            subsequent_references=[{'observation_id': 3, 'run_id': 'r3', 'match_basis': 'exact_title_original_image_url_clue_only'}],
            latest_observation_ids=[2, 3])
        _, items = result(queries(rs, rows, [tracking(rs, rows)], [item]))
        first = next(row for row in items if row['lane'] == 'new_watch')
        self.assertEqual(first['time_order_status'], 'overlapping_runs_unknown')
        self.assertIsNone(first['first_seen_at'])
        self.assertEqual(first['source_observation_ids'], [2])
        self.assertEqual(first['related_observation_ids'], [2, 3])

    def test_unknown_last_read_is_not_generated_timestamp(self):
        rs, rows = [run('r1')], [card(1, 'r1')]
        shops, _ = result(queries(rs, rows))
        self.assertIsNone(shops[0]['last_read_at'])
        rows[0]['last_dom_read_at'] = '2026-01-01T01:06:00Z'
        shops, _ = result(queries(rs, rows))
        self.assertEqual(shops[0]['last_read_at'], rows[0]['last_dom_read_at'])
        rows[0]['last_dom_read_at'] = '2026-01-05T01:06:00Z'
        shops, _ = result(queries(rs, rows))
        self.assertIsNone(shops[0]['last_read_at'])

    def test_card_count_mismatch_and_canonical_duplicate_observation_rejected(self):
        r = run('r1'); r['card_count'] = 9
        with self.assertRaisesRegex(ValueError, 'card count'):
            result(queries([r], [card(1, 'r1')]))
        with self.assertRaisesRegex(ValueError, 'duplicate observation_id'):
            result(queries([run('r1')], [card(1, 'r1'), card('1', 'r1', view_order=2)]))

    def test_no_input_mutation_deterministic_and_source_ids_inspectable(self):
        q = queries([run('r1')], [card(1, 'r1')]); before = deepcopy(q)
        first, second = build_product_table_queries(q), build_product_table_queries(q)
        self.assertEqual(q, before)
        self.assertEqual(first, second)
        self.assertEqual(first['product_table_items']['source']['sourceQueryRowIds']['observations'], [1])
        json.dumps(first, allow_nan=False)
        self.assertEqual(first['product_table_shops']['rows'][0]['check_state'], 'passed')
        self.assertTrue(all(row['passed'] for row in first['product_table_shops']['rows'][0]['check_results']))


if __name__ == '__main__':
    unittest.main()
