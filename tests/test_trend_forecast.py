"""Forecast boundaries use synthetic in-memory sources only."""
from copy import deepcopy
import unittest
from unittest.mock import patch

from pdd_monitor.trend_forecast import build_trend_forecasts, build_forecast_queries
from pdd_monitor.trend_dashboard import build_trend_queries
from pdd_monitor.trend_signals import build_trend_signals
from pdd_monitor.warehouse_display_history import build_display_history
from pdd_monitor.warehouse_focus import build_warehouse_focus
from tests.test_daily_warehouse import card, run


def fixtures(values, *, days=None, hours=None, shop='A', goods='123', precision='card_read',
             local=True, partial_days=(), labels=None, offset=0):
    days, hours = days or list(range(1, len(values) + 1)), hours or [1] * len(values)
    runs = [run(f'{shop}{day}', day, hour, day not in partial_days, shop=shop) for day, hour in zip(days, hours)]
    rows = [card(index + offset, source, value, goods=goods, image_url='https://example.invalid/SYNTHETIC.png',
                 image_content_status='verified_local' if local else 'missing', asset_sha256='a' * 64 if local else None,
                 observed_at_precision=precision, sales_label=(labels[index - 1] if labels else '已拼'))
            for index, (source, value) in enumerate(zip(runs, values), 1)]
    return rebuild(runs, rows)


def rebuild(runs, rows):
    focus = build_warehouse_focus(runs, rows)
    history = build_display_history(runs, rows, focus)
    return focus['warehouse_focus_items'], history, focus['warehouse_focus_summary'], runs, rows


def result(*args, **kwargs):
    return build_trend_forecasts(*fixtures(*args, **kwargs))['trend_forecasts'][0]


class TrendForecastTests(unittest.TestCase):
    def test_three_intervals_ewma_has_exact_source_evidence_and_scenario_bounds(self):
        evidence = fixtures([10, 12, 16, 24])
        before = deepcopy(evidence)
        data = build_trend_forecasts(*evidence)
        row = data['trend_forecasts'][0]
        expected = .4 * 8 + .6 * (.4 * 4 + .6 * 2)
        self.assertEqual(row['forecast_status'], 'estimated')
        self.assertAlmostEqual(row['ewma_rate24'], expected)
        self.assertAlmostEqual(row['forecast_increment_1d'], expected)
        self.assertAlmostEqual(row['forecast_increment_7d'], expected * 7)
        self.assertEqual((row['scenario_low_1d'], row['scenario_high_1d']), (2, 8))
        self.assertEqual((row['scenario_low_7d'], row['scenario_high_7d']), (14, 56))
        self.assertEqual(row['evidence_observation_ids'], [1, 2, 3, 4])
        self.assertEqual(row['evidence_run_ids'], ['A1', 'A2', 'A3', 'A4'])
        self.assertEqual(row['evidence_channel'], 'confirmed')
        self.assertEqual(row['normalized_interval_count'], 3)
        self.assertEqual(row['minimum_intervals'], 2)
        self.assertEqual(row['used_interval_count'], 3)
        self.assertEqual(row['remaining_intervals'], 0)
        self.assertEqual(len(row['evidence_intervals']), 3)
        self.assertEqual(row['evidence_intervals'][-1]['display_delta'], 8)
        self.assertEqual(row['evidence_time_window']['to'], row['forecast_from'])
        self.assertFalse(row['forecast_validated'])
        self.assertFalse(row['same_sku_verified'])
        self.assertEqual(row['scenario_range_kind'], 'historical_speed_scenarios')
        self.assertNotIn('probability', row)
        self.assertNotIn('confidence_interval', row)
        self.assertEqual(data['trend_forecast_summary'][0]['confirmed_estimated_count'], 1)
        self.assertEqual(evidence, before)

    def test_variable_intervals_use_elapsed_time_normalization_and_adjusted_alpha(self):
        row = result([10, 40, 49, 73], hours=[1, 7, 1, 1])
        expected = 24
        for rate, hours in [(12, 18), (24, 24)]:
            alpha = 1 - .6 ** (hours / 24)
            expected = alpha * rate + (1 - alpha) * expected
        self.assertAlmostEqual(row['ewma_rate24'], expected)
        self.assertEqual([edge['interval_hours'] for edge in row['evidence_intervals']], [30, 18, 24])
        self.assertEqual([edge['rate24'] for edge in row['evidence_intervals']], [24, 12, 24])

    def test_only_latest_three_intervals_enter_estimate_but_progress_keeps_suffix(self):
        row = result([10, 110, 210, 212, 216, 224])
        self.assertEqual(row['normalized_interval_count'], 5)
        self.assertEqual(row['evidence_observation_ids'], [3, 4, 5, 6])
        self.assertEqual(row['recent_min_rate24'], 2)
        self.assertEqual(row['recent_max_rate24'], 8)

    def test_first_date_and_one_interval_report_true_progress_without_fake_zero_forecast(self):
        for values, count in [([7], 0), ([7, 9], 1)]:
            row = result(values)
            self.assertEqual(row['forecast_status'], 'insufficient_data')
            self.assertEqual(row['normalized_interval_count'], count)
            self.assertEqual(row['remaining_intervals'], 2 - count)
            self.assertEqual(row['yipin_value'], values[-1])
            self.assertIsNone(row['forecast_increment_7d'])
            self.assertIsNone(row['scenario_low_1d'])

    def test_two_intervals_enable_explicit_preliminary_estimate_with_actual_source_window(self):
        row = result([7, 9, 13])
        self.assertEqual(row['forecast_status'], 'estimated')
        self.assertEqual(row['algorithm_version'], 'recent2plus_elapsed_ewma_scenarios_v1')
        self.assertEqual(row['algorithm_parameters']['minimum_intervals'], 2)
        self.assertEqual(row['algorithm_parameters']['recent_intervals'], 3)
        self.assertEqual(row['minimum_intervals'], 2)
        self.assertEqual(row['normalized_interval_count'], 2)
        self.assertEqual(row['used_interval_count'], 2)
        self.assertEqual(row['remaining_intervals'], 0)
        self.assertEqual(row['evidence_observation_ids'], [1, 2, 3])
        self.assertEqual(len(row['evidence_intervals']), 2)
        self.assertAlmostEqual(row['ewma_rate24'], .4 * 4 + .6 * 2)
        self.assertEqual((row['scenario_low_7d'], row['scenario_high_7d']), (14, 28))
        self.assertIn('初步估算', row['reason_label'])
        self.assertIn('最近2段', row['assumption'])
        self.assertFalse(row['forecast_validated'])

    def test_provisional_chain_is_separate_and_not_claimed_identity_confirmed(self):
        inputs = fixtures([10, 12, 16, 24], goods=None)
        data = build_trend_forecasts(*inputs)
        row = data['trend_forecasts'][0]
        self.assertEqual(row['forecast_status'], 'estimated')
        self.assertEqual(row['evidence_channel'], 'provisional')
        self.assertFalse(row['identity_confirmed'])
        self.assertEqual(data['trend_forecast_summary'][0]['provisional_estimated_count'], 1)
        self.assertEqual(data['trend_forecast_summary'][0]['confirmed_estimated_count'], 0)

    def test_zero_known_speed_is_real_zero_not_missing_imputation(self):
        row = result([10, 10, 10, 10])
        self.assertEqual(row['forecast_status'], 'estimated')
        self.assertEqual(row['forecast_increment_7d'], 0)
        self.assertEqual(row['scenario_high_7d'], 0)

    def test_nonpositive_or_unverified_image_is_excluded(self):
        for values, options, reason in [([0], {}, 'not_positive_exact_sales'),
                                         ([10, 11, 12, 13], {'local': False}, 'image_unverified'),
                                         ([10, 11, 12, 13], {'labels': ['总售'] * 4}, 'not_positive_exact_sales')]:
            row = result(values, **options)
            self.assertEqual(row['forecast_status'], 'excluded')
            self.assertEqual(row['reason'], reason)
            self.assertIsNone(row['forecast_increment_7d'])

    def test_sales_alias_changes_still_use_actual_positive_card_values(self):
        row = result([10, 11, 13, 16], labels=['已拼', '已抢', '已拼', '已抢'])
        self.assertEqual(row['forecast_status'], 'estimated')
        self.assertEqual(row['yipin_value'], 16)

    def test_missing_day_and_partial_day_cannot_be_bridged(self):
        for options in ({'days': [1, 2, 3, 5]}, {'partial_days': (3,)}):
            row = result([10, 11, 12, 13], **options)
            self.assertEqual(row['forecast_status'], 'insufficient_data')
            self.assertEqual(row['normalized_interval_count'], 0)
            self.assertIsNone(row['forecast_increment_1d'])

    def test_partial_current_without_complete_reference_stays_unestimated(self):
        row = result([10], partial_days=(1,))
        self.assertEqual(row['forecast_status'], 'insufficient_data')
        self.assertEqual(row['reason'], 'incomplete_snapshot')

    def test_downward_value_stops_tail_and_does_not_estimate_negative_orders(self):
        row = result([10, 11, 12, 9])
        self.assertEqual(row['reason'], 'negative_display_anomaly')
        self.assertEqual(row['normalized_interval_count'], 0)
        self.assertIsNone(row['forecast_increment_7d'])
        recovered = result([10, 11, 12, 9, 11, 13, 15])
        self.assertEqual(recovered['forecast_status'], 'estimated')
        self.assertEqual(recovered['evidence_observation_ids'], [4, 5, 6, 7])

    def test_legacy_precision_and_short_interval_remain_unknown_not_normalized(self):
        row = result([10, 11, 12, 13], precision='legacy_unspecified')
        self.assertEqual(row['reason'], 'precision_unknown')
        self.assertEqual(row['normalized_interval_count'], 0)
        inputs = fixtures([10, 11, 12, 13], hours=[1, 1, 15, 1])
        runs, observations = inputs[3:]
        runs[-1].update(observed_from='2026-10-03T17:00:00Z', observed_to='2026-10-03T17:10:00Z')
        observations[-1]['observed_at'] = runs[-1]['observed_to']
        short = build_trend_forecasts(*rebuild(runs, observations))['trend_forecasts'][0]
        self.assertEqual(short['reason'], 'irregular_interval')
        self.assertEqual(short['normalized_interval_count'], 0)

    def test_missing_or_changed_identity_not_inherited_by_position(self):
        inputs = fixtures([10, 11, 12, 13], goods=None)
        runs, observations = inputs[3:]
        observations[-1]['image_url'] += '?different=1'
        observations[-1]['asset_sha256'] = 'b' * 64
        row = build_trend_forecasts(*rebuild(runs, observations))['trend_forecasts'][0]
        self.assertEqual(row['forecast_status'], 'insufficient_data')
        self.assertEqual(row['normalized_interval_count'], 0)

    def test_future_cross_shop_and_wrong_run_evidence_rejected(self):
        for field, value in [('date', '2026-10-05'), ('shop_id', 'B'), ('run_id', 'MISSING'),
                              ('observation_id', 4), ('time_lower_epoch', 0), ('cumulative_yipin', 999)]:
            with self.subTest(field=field):
                inputs = list(fixtures([10, 11, 12, 13]))
                inputs[1][0][field] = value
                with self.assertRaises(ValueError):
                    build_trend_forecasts(*inputs)

    def test_stale_current_snapshot_rejected_even_if_its_rows_are_internally_valid(self):
        items, history, summaries, runs, observations = fixtures([10, 11, 12, 13])
        later = run('later', 5)
        runs.append(later)
        observations.append(card(5, later, 14))
        with self.assertRaisesRegex(ValueError, 'current canonical'):
            build_trend_forecasts(items, history, summaries, runs, observations)

    def test_newer_partial_run_does_not_supersede_latest_complete(self):
        inputs = fixtures([10, 11, 12, 13])
        runs, observations = inputs[3:]
        later = run('partial', 5, complete=False)
        runs.append(later)
        observations.append(card(5, later, 20))
        row = build_trend_forecasts(*rebuild(runs, observations))['trend_forecasts'][0]
        self.assertEqual(row['run_id'], 'A4')
        self.assertEqual(row['forecast_status'], 'estimated')
        self.assertNotIn(5, row['evidence_observation_ids'])

    def test_wrong_anchor_or_forged_identity_channel_rejected(self):
        for target, field, value in [(0, 'sales_value', 99), (0, 'asset_sha256', 'b' * 64),
                                      (1, 'match_basis', 'provisional_title_image')]:
            inputs = list(fixtures([10, 11, 12, 13]))
            inputs[target][0][field] = value
            with self.assertRaises(ValueError):
                build_trend_forecasts(*inputs)

    def test_forecasts_never_mix_shops_or_same_goods_id(self):
        a, b = fixtures([10, 11, 12, 13]), fixtures([10, 20, 30, 40], shop='B', offset=100)
        combined = [a[index] + b[index] for index in range(5)]
        data = build_trend_forecasts(*combined)
        rows = {row['shop_id']: row for row in data['trend_forecasts']}
        self.assertEqual(rows['A']['forecast_increment_7d'], 7)
        self.assertEqual(rows['B']['forecast_increment_7d'], 70)
        self.assertEqual(rows['A']['evidence_observation_ids'], [1, 2, 3, 4])
        for summary in data['trend_forecast_summary']:
            self.assertEqual(summary['total_card_count'], 1)
            self.assertEqual(summary['estimated_count'], 1)

    def test_export_has_methods_limitations_and_preserves_existing_signal_protocol(self):
        values = fixtures([10, 11, 12, 13])
        names = ('warehouse_focus_items', 'warehouse_display_history', 'warehouse_focus_summary', 'runs', 'observations')
        queries = {name: {'rows': rows, 'source': {'label': 'SYNTHETIC', 'files': [], 'caveats': [], 'sql': 'SYNTHETIC'}}
                   for name, rows in zip(names, values)}
        before = deepcopy(queries)
        signals_before = build_trend_signals(*values[:3])
        exported = build_forecast_queries(queries)
        self.assertEqual(set(exported), {'trend_forecasts', 'trend_forecast_summary'})
        self.assertEqual(queries, before)
        self.assertEqual(signals_before, build_trend_signals(*values[:3]))
        self.assertTrue(all(row['forecast_eligible'] is False for row in signals_before['trend_signals']))
        for name, query in exported.items():
            self.assertEqual(query['source']['classification'], 'derived')
            self.assertTrue(query['methods'])
            self.assertTrue(any('不是置信区间' in line for line in query['source']['caveats']))
            self.assertTrue(all(row['forecast_validated'] is False for row in query['rows']))
        with patch('pdd_monitor.trend_dashboard.build_decision_queries', return_value={}) as decisions, \
             patch('pdd_monitor.trend_dashboard.build_backtest_queries', return_value={}) as backtests:
            actual = build_trend_queries(queries, 'SYNTHETIC_NO_IO')
        self.assertEqual(actual['trend_forecasts'], exported['trend_forecasts'])
        self.assertEqual(actual['trend_signals']['rows'], signals_before['trend_signals'])
        self.assertEqual(decisions.call_count, 1)
        self.assertEqual(backtests.call_count, 1)


if __name__ == '__main__':
    unittest.main()
