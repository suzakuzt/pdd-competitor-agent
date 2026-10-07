from copy import deepcopy
import unittest
from pdd_monitor.daily_warehouse import build_daily_warehouse
from tests.test_daily_warehouse import run, card


def arrival(row, **extra):
    return {'observation_id': row['observation_id'], 'arrival_item_id': 'synthetic_anchor',
            'coverage_expansion_possible': False, 'first_time_order_status': 'observed_run_order',
            'historical_references': [], 'possible_change_reasons': [], **extra}


class MonitoringCohortsTests(unittest.TestCase):
    def test_initial_catalogue_is_stock_without_claiming_listing_date(self):
        a = run('a', 4)
        row = card(1, a, goods=None)
        data = build_daily_warehouse([a], [row])
        rec = data['warehouse_records'][0]
        self.assertEqual(rec['monitor_origin'], 'reference_stock')
        self.assertIsNone(rec['listing_date'])
        self.assertEqual(rec['monitor_first_date'], '2026-10-04')

    def test_repeated_reliable_id_preserves_watch_start_and_all_daily_points(self):
        a, b, c = run('a', 3), run('b', 4), run('c', 5)
        rows = [card(1, a, goods='111'), card(2, b, goods='222'), card(3, c, goods='222')]
        before = deepcopy(rows)
        data = build_daily_warehouse([c, b, a], rows, [arrival(rows[1])])
        repeated = [r for r in data['warehouse_records'] if r['goods_id'] == '222']
        self.assertTrue(all(r['monitor_origin'] == 'first_observed_candidate' for r in repeated))
        self.assertTrue(all(r['monitor_first_observation_id'] == 2 for r in repeated))
        self.assertEqual(repeated[1]['monitor_first_date'], '2026-10-04')
        self.assertEqual(len({r['track_id'] for r in repeated}), 1)
        self.assertEqual(rows, before)

    def test_coverage_expansion_old_card_change_and_invalid_time_are_pending(self):
        a, b = run('a', 3), run('b', 4)
        for evidence in ({'coverage_expansion_possible': True}, {'possible_change_reasons': ['possible_image_change']},
                         {'first_time_order_status': 'overlapping_runs_unknown'}):
            rows = [card(1, a), card(2, b, goods=None)]
            data = build_daily_warehouse([a, b], rows, [arrival(rows[1], **evidence)])
            self.assertEqual(data['warehouse_records'][1]['monitor_origin'], 'pending')
        rows = [card(1, a), card(2, b, goods=None, observed_at=a['observed_to'])]
        data = build_daily_warehouse([a, b], rows, [arrival(rows[1])])
        self.assertIsNone(data['warehouse_records'][1]['monitor_first_date'])
        self.assertEqual(data['warehouse_records'][1]['monitor_origin'], 'pending')

    def test_same_title_or_image_never_inherits_watch_anchor(self):
        a, b, c = run('a', 3), run('b', 4), run('c', 5)
        rows = [card(1, a), card(2, b, goods=None, image_url='same'), card(3, c, goods=None, image_url='same')]
        data = build_daily_warehouse([a, b, c], rows, [arrival(rows[1])])
        self.assertEqual(data['warehouse_records'][1]['monitor_origin'], 'first_observed_candidate')
        self.assertEqual(data['warehouse_records'][2]['monitor_origin'], 'pending')
        self.assertNotEqual(data['warehouse_records'][1]['track_id'], data['warehouse_records'][2]['track_id'])

    def test_same_day_replacement_does_not_erase_independent_discovery(self):
        a, b, c = run('a', 3), run('b', 4), run('c', 4, 2)
        rows = [card(1, a), card(2, b, goods=None), card(3, c, goods=None)]
        data = build_daily_warehouse([a, b, c], rows, [arrival(rows[1])])
        self.assertEqual([r['observation_id'] for r in data['warehouse_records']], [1, 3])
        saved = next(r for r in data['warehouse_watch_records'] if r['observation_id'] == 2)
        self.assertTrue(saved['archived_anchor'])
        self.assertEqual(saved['sales_raw'], rows[1]['sales_raw'])
        self.assertFalse(any(p['observation_id'] == 2 for p in data['warehouse_points']))

    def test_first_ready_read_precedes_last_read_but_remains_inside_source_window(self):
        a = run('a', 4)
        row = card(1, a, original={'firstObservedAt': a['observed_from'], 'firstObservedAtPrecision': 'batch_read'})
        data = build_daily_warehouse([a], [row])
        self.assertEqual(data['warehouse_records'][0]['monitor_first_observed_at'], a['observed_from'])

    def test_id_from_another_shop_and_conflicting_ids_do_not_inherit_start(self):
        a, b = run('a', 3), run('b', 4, shop='B')
        rows = [card(1, a), card(2, b)]
        data = build_daily_warehouse([a, b], rows)
        self.assertEqual(data['warehouse_records'][1]['monitor_first_observation_id'], 2)
        rows += [card(3, b)]
        data = build_daily_warehouse([a, b], rows)
        self.assertEqual(data['warehouse_records'][1]['identity_basis'], 'independent_card')


if __name__ == '__main__':
    unittest.main()
