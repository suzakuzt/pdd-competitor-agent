"""Synthetic, in-memory evidence only; no production store access."""
from copy import deepcopy
import unittest

from pdd_monitor.trend_signals import build_trend_signals
from pdd_monitor.warehouse_display_history import build_display_history
from pdd_monitor.warehouse_focus import build_warehouse_focus
from tests.test_daily_warehouse import card, run


def evidence(values, *, days=None, hours=None, goods='123', shop='A', local=True, precision='card_read'):
    days = days or list(range(1, len(values) + 1))
    hours = hours or [1] * len(values)
    runs = [run(f'{shop}{day}', day, hour, shop=shop) for day, hour in zip(days, hours)]
    rows = [card(index, source, value, goods=goods, image_url='https://img.pddpic.com/test.png',
                 image_content_status='verified_local' if local else 'missing',
                 asset_sha256='a' * 64 if local else None, observed_at_precision=precision)
            for index, (source, value) in enumerate(zip(runs, values), 1)]
    focus = build_warehouse_focus(runs, rows)
    history = build_display_history(runs, rows, focus)
    return focus['warehouse_focus_items'], history, focus['warehouse_focus_summary']


def result(*args, **kwargs):
    return build_trend_signals(*evidence(*args, **kwargs))['trend_signals'][0]


class TrendSignalsTests(unittest.TestCase):
    def test_single_date_is_observation_and_not_prediction(self):
        row = result([7])
        self.assertEqual(row['signal_code'], 'insufficient_history')
        self.assertTrue(row['included_in_watch'])
        self.assertEqual(row['consecutive_days'], 1)
        self.assertFalse(row['forecast_eligible'])
        self.assertFalse(row['model_ready'])
        self.assertFalse(row['selected_for_review'])
        self.assertEqual(row['outcome_window_days'], 7)

    def test_two_dates_even_huge_growth_only_single_day_clue(self):
        row = result([1, 999])
        self.assertEqual(row['signal_code'], 'single_day_growth')
        self.assertEqual(row['level'], 'watch')
        self.assertEqual(row['latest_delta'], 998)
        self.assertIsNone(row['avg_recent'])
        self.assertFalse(row['alert_candidate'])
        self.assertTrue(row['selected_for_review'])

    def test_low_sales_and_high_sales_use_same_rules(self):
        for values in ([1, 2, 3, 4], [101, 102, 103, 104]):
            row = result(values)
            self.assertEqual(row['signal_code'], 'sustained_growth')
            self.assertEqual(row['avg_recent'], 1)
            self.assertIsNone(row['avg_base'])
            self.assertEqual(row['valid_delta_days'], 3)

    def test_excludes_zero_unknown_approximate_other_label_and_boolean(self):
        items, history, summaries = evidence([0])
        self.assertEqual(build_trend_signals(items, history, summaries)['trend_signals'][0]['signal_code'], 'excluded_zero')
        for fields in ({'sales_value': None}, {'sales_precision': 'approximate'}, {'sales_label': '总售'},
                       {'sales_value': True}, {'sales_raw': ''}):
            altered = deepcopy(items)
            altered[0].update(fields)
            row = build_trend_signals(altered, history, summaries)['trend_signals'][0]
            self.assertEqual(row['signal_code'], 'excluded_unknown_sales')
            self.assertFalse(row['included_in_watch'])

    def test_actual_thirty_hour_interval_not_one_day_speed(self):
        row = result([10, 40], hours=[1, 7])
        self.assertEqual(row['latest_delta'], 30)
        self.assertEqual(row['interval_hours'], 30)
        self.assertEqual(row['rate24'], 24)

    def test_short_cross_midnight_interval_cannot_become_explosive_speed(self):
        a, b = run('a', 1, 15), run('b', 1, 17)
        records = [card(1, a, 10), card(2, b, 20)]
        focus = build_warehouse_focus([a, b], records)
        history = build_display_history([a, b], records, focus)
        row = build_trend_signals(focus['warehouse_focus_items'], history, focus['warehouse_focus_summary'])['trend_signals'][0]
        self.assertEqual(row['latest_delta'], 10)
        self.assertEqual(row['interval_hours'], 2)
        self.assertIsNone(row['rate24'])
        self.assertEqual(row['rate_status'], 'irregular_interval')
        self.assertEqual(row['signal_code'], 'single_day_growth')

    def test_normalization_interval_bounds_and_long_interval(self):
        for hours, valid in [(5.99, False), (6, True), (48, True), (48.01, False)]:
            items, history, summaries = evidence([10, 40])
            epoch = history[0]['time_lower_epoch'] + hours * 3600
            history[1].update(time_lower_epoch=epoch, time_upper_epoch=epoch)
            row = build_trend_signals(items, history, summaries)['trend_signals'][0]
            self.assertEqual(row['latest_delta'], 30)
            self.assertEqual(row['rate24'] is not None, valid)
            self.assertEqual(row['normalized_delta_days'], int(valid))

    def test_weighted_speed_not_mean_of_interval_rates(self):
        row = result([10, 40, 49, 73], hours=[1, 7, 1, 1])
        self.assertEqual(row['recent_interval_hours'], 72)
        self.assertEqual(row['avg_recent'], 21)
        self.assertNotEqual(row['avg_recent'], (24 + 12 + 24) / 3)
        self.assertEqual(row['rate_average_method'], 'total_delta_over_total_hours')

    def test_eight_dates_have_disjoint_baseline_and_recent_normalized_windows(self):
        row = result([10, 11, 12, 13, 14, 19, 24, 29])
        self.assertEqual(row['signal_code'], 'growth_acceleration')
        self.assertEqual(row['avg_base'], 1)
        self.assertEqual(row['avg_recent'], 5)
        self.assertEqual(row['ratio'], 5)
        self.assertEqual(row['baseline_interval_hours'], 96)
        self.assertEqual(row['recent_interval_hours'], 72)
        self.assertEqual(row['level'], 'priority')
        self.assertTrue(row['alert_candidate'])
        self.assertFalse(row['forecast_eligible'])
        self.assertIsNone(row.get('probability'))

    def test_elapsed_time_adjusted_ewma(self):
        row = result([10, 11, 12, 13, 14, 20, 23, 28], hours=[1, 1, 1, 1, 1, 7, 1, 1])
        base = 1.0
        for delta, hours in [(6, 30), (3, 18), (5, 24)]:
            alpha = 1 - .6 ** (hours / 24)
            base = alpha * (delta * 24 / hours) + (1 - alpha) * base
        self.assertAlmostEqual(row['ewma_recent'], base)
        self.assertEqual(row['ewma_base'], 1)
        self.assertEqual(row['ewma_method'], 'elapsed_time_adjusted_24h_alpha')

    def test_zero_baseline_no_infinite_ratio(self):
        row = result([5, 5, 5, 5, 5, 10, 15, 20])
        self.assertEqual(row['signal_code'], 'zero_baseline_growth')
        self.assertEqual(row['avg_base'], 0)
        self.assertIsNone(row['ratio'])

    def test_one_last_spike_does_not_become_sustained_acceleration(self):
        row = result([5, 6, 7, 8, 9, 9, 9, 109])
        self.assertEqual(row['signal_code'], 'recent_spike')
        self.assertEqual(row['level'], 'watch')

    def test_slowdown_and_stable_are_not_priority(self):
        row = result([10, 20, 30, 40, 50, 51, 52, 53])
        self.assertEqual(row['signal_code'], 'slowing_growth')
        self.assertEqual(result([5] * 8)['signal_code'], 'stable_display')

    def test_legacy_timestamp_keeps_raw_delta_but_prevents_acceleration(self):
        row = result([10, 11, 12, 13, 14, 19, 24, 29], precision='legacy_unspecified')
        self.assertEqual(row['signal_code'], 'sustained_growth')
        self.assertEqual(row['latest_delta'], 5)
        self.assertEqual(row['valid_delta_days'], 7)
        self.assertEqual(row['normalized_delta_days'], 0)
        self.assertIsNone(row['rate24'])
        self.assertIsNone(row['interval_hours'])
        self.assertEqual(row['interval_hours_lower'], 24)
        self.assertEqual(row['rate_status'], 'precision_unknown')
        self.assertIsNone(row['avg_base'])

    def test_window_timestamp_bounds_do_not_enable_point_speed(self):
        items, history, summaries = evidence([10, 40])
        history[0]['time_lower_epoch'] -= 1800
        row = build_trend_signals(items, history, summaries)['trend_signals'][0]
        self.assertEqual(row['latest_delta'], 30)
        self.assertIsNone(row['rate24'])
        self.assertEqual(row['rate_status'], 'window_only')
        self.assertEqual(row['interval_hours_lower'], 24)
        self.assertEqual(row['interval_hours_upper'], 24.5)
        self.assertAlmostEqual(row['rate24_lower'], 30 / 24.5 * 24)

    def test_last_normalized_suffix_does_not_bridge_unknown_precision(self):
        items, history, summaries = evidence([10, 11, 12, 13, 14, 19, 24, 29])
        history[3]['observed_at_precision'] = 'legacy_unspecified'
        row = build_trend_signals(items, history, summaries)['trend_signals'][0]
        self.assertEqual(row['normalized_delta_days'], 3)
        self.assertEqual(row['avg_recent'], 5)
        self.assertIsNone(row['avg_base'])
        self.assertEqual(row['signal_code'], 'sustained_growth')

    def test_missing_day_breaks_continuous_evidence(self):
        row = result([10, 11, 12, 13, 14, 19, 24, 29], days=[1, 2, 3, 4, 5, 6, 7, 9])
        self.assertEqual(row['signal_code'], 'history_gap')
        self.assertEqual(row['valid_delta_days'], 0)
        self.assertIsNone(row['latest_delta'])

    def test_negative_display_change_resets_tail_without_negative_orders(self):
        row = result([30, 31, 32, 20])
        self.assertEqual(row['signal_code'], 'negative_display_anomaly')
        self.assertEqual(row['raw_display_change'], -12)
        self.assertIsNone(row['latest_delta'])
        self.assertIsNone(row['rate24'])
        self.assertEqual(row['consecutive_days'], 1)
        self.assertEqual(row['basis'], 'confirmed_goods_id')
        recovered = result([30, 31, 32, 20, 25, 30, 35])
        self.assertEqual(recovered['signal_code'], 'sustained_growth')
        self.assertEqual(recovered['consecutive_days'], 4)
        self.assertIsNone(recovered['avg_base'])

    def test_identity_gap_with_falling_values_is_not_known_negative_match(self):
        items, history, summaries = evidence([30, 20])
        history[-1].update(delta_status='identity_unverified', delta_match_basis=None, break_before=True)
        row = build_trend_signals(items, history, summaries)['trend_signals'][0]
        self.assertEqual(row['signal_code'], 'history_gap')
        self.assertEqual(row['basis'], 'single_card_only')
        self.assertIsNone(row['raw_display_change'])

    def test_provisional_history_stays_provisional_and_does_not_merge_cards(self):
        items, history, summaries = evidence([1, 2, 3, 4], goods=None)
        original = deepcopy((items, history, summaries))
        row = build_trend_signals(items, history, summaries)['trend_signals'][0]
        self.assertEqual(row['basis'], 'provisional_title_image')
        self.assertFalse(row['identity_confirmed'])
        self.assertFalse(row['same_sku_verified'])
        self.assertEqual(row['observation_id'], 4)
        self.assertEqual(row['track_id'], items[0]['track_id'])
        self.assertEqual((items, history, summaries), original)

    def test_partial_or_ambiguous_day_breaks_chain(self):
        for changed in ({'point_status': 'ambiguous_identity', 'observation_id': None}, {'is_complete': False}):
            items, history, summaries = evidence([10, 11, 12, 13, 14, 19, 24, 29])
            history[-2].update(changed)
            row = build_trend_signals(items, history, summaries)['trend_signals'][0]
            self.assertEqual(row['signal_code'], 'history_gap')
            self.assertEqual(row['consecutive_days'], 1)

    def test_current_partial_snapshot_has_no_trend(self):
        source = run('partial', 1, complete=False)
        records = [card(1, source, 8)]
        focus = build_warehouse_focus([source], records)
        history = build_display_history([source], records, focus)
        data = build_trend_signals(focus['warehouse_focus_items'], history, focus['warehouse_focus_summary'])
        self.assertEqual(data['trend_signals'][0]['signal_code'], 'incomplete_snapshot')

    def test_bad_time_order_and_delta_mismatch_do_not_compare(self):
        for mode in ('overlap', 'wrong_delta'):
            items, history, summaries = evidence([1, 2])
            if mode == 'overlap':
                history[1]['time_lower_epoch'] = history[0]['time_upper_epoch']
            else:
                history[1]['daily_delta'] = 999
            row = build_trend_signals(items, history, summaries)['trend_signals'][0]
            self.assertEqual(row['signal_code'], 'history_gap')
            self.assertIsNone(row['latest_delta'])

    def test_cross_shop_same_observation_ids_do_not_mix(self):
        ai, ah, asm = evidence([1, 2], shop='A')
        bi, bh, bsm = evidence([99], shop='B')
        data = build_trend_signals(ai + bi, ah + bh, asm + bsm)
        self.assertEqual([r['signal_code'] for r in data['trend_signals']], ['single_day_growth', 'insufficient_history'])
        a, b = data['trend_signal_summary']
        self.assertEqual((a['total_cards'], a['full_snapshot_days'], a['cards_with_2_dates']), (1, 2, 1))
        self.assertEqual((b['total_cards'], b['full_snapshot_days'], b['cards_with_2_dates']), (1, 1, 0))
        self.assertEqual((a['date'], b['date']), ('2026-10-02', '2026-10-01'))

    def test_duplicates_future_dates_and_wrong_summary_fail_closed(self):
        items, history, summaries = evidence([1, 2])
        for bad_items, bad_history, bad_summary in (
            (items + items, history, summaries),
            (items, history + history[:1], summaries),
            (items, [dict(history[0], date='2026-10-03')], summaries),
            (items, history, [dict(summaries[0], date='2026-10-01')]),
            (items, [dict(history[0], anchor_observation_id=999)], summaries),
        ):
            with self.assertRaises(ValueError):
                build_trend_signals(bad_items, bad_history, bad_summary)

    def test_rank_and_top_five_use_same_backend_review_queue(self):
        a, b = run('a', 1), run('b', 2)
        records = []
        for index, growth in enumerate([10, 20, 30, 30, 40, 50, 60, 0], 1):
            for source, number, sales in [(a, index, 10), (b, index + 10, 10 + growth)]:
                records.append(card(number, source, sales, goods=str(index),
                                    image_content_status='verified_local', asset_sha256='a' * 64 if index != 7 else None))
        focus = build_warehouse_focus([a, b], records)
        history = build_display_history([a, b], records, focus)
        data = build_trend_signals(focus['warehouse_focus_items'], history, focus['warehouse_focus_summary'])
        selected = [r for r in data['trend_signals'] if r['selected_for_review']]
        self.assertEqual([r['observation_id'] for r in selected], [16, 15, 13, 14, 12])
        self.assertEqual([r['review_rank'] for r in selected], [1, 2, 3, 4, 5])
        self.assertEqual(data['trend_signal_summary'][0]['selected_review_count'], 5)
        self.assertEqual([r['rank'] for r in data['trend_signals']], list(range(1, 9)))


if __name__ == '__main__':
    unittest.main()
