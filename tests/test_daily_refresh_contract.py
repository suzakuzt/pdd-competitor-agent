"""In-memory regression of daily replacement across table, chart and signals.

These fixtures never read or write the production databases.
"""
from copy import deepcopy
import unittest

from pdd_monitor.daily_warehouse import build_daily_warehouse
from pdd_monitor.trend_signals import build_trend_signals
from pdd_monitor.warehouse_display_history import build_display_history
from pdd_monitor.warehouse_focus import build_warehouse_focus
from tests.test_daily_warehouse import card, run


def views(runs, rows):
    source = deepcopy((runs, rows))
    daily = build_daily_warehouse(runs, rows)
    focus = build_warehouse_focus(runs, rows)
    history = build_display_history(runs, rows, focus)
    trends = build_trend_signals(focus['warehouse_focus_items'], history,
                               focus['warehouse_focus_summary'])
    if source != (runs, rows):
        raise AssertionError('Derived daily refresh changed original evidence')
    return {**daily, **focus, 'history': history, **trends}


class DailyRefreshContractTests(unittest.TestCase):
    def test_afternoon_replaces_morning_across_table_chart_signals_not_yesterday(self):
        yesterday, morning, afternoon = run('yesterday', 5), run('morning', 6), run('afternoon', 6, 7)
        original = [card(1, yesterday, 20), card(2, morning, 25)]
        before = views([yesterday, morning], original)
        after = views([yesterday, morning, afternoon], [*original, card(3, afternoon, 42)])
        self.assertEqual([d['run_id'] for d in after['warehouse_days']], ['yesterday', 'afternoon'])
        for key in ('warehouse_days', 'warehouse_records', 'warehouse_points'):
            self.assertEqual([r for r in before[key] if r['date'] == '2026-10-05'],
                             [r for r in after[key] if r['date'] == '2026-10-05'])
        item = after['warehouse_focus_items'][0]
        self.assertEqual((item['observation_id'], item['yipin_value'], item['yesterday_delta']), (3, 42, 22))
        self.assertEqual([(p['run_id'], p['cumulative_yipin'], p['daily_delta']) for p in after['history']],
                         [('yesterday', 20, None), ('afternoon', 42, 22)])
        self.assertEqual(after['trend_signals'][0]['latest_delta'], 22)
        self.assertEqual(len(after['warehouse_points']), 2)

    def test_later_partial_failed_or_unsealed_run_never_replaces_complete(self):
        previous, complete = run('previous', 5), run('complete', 6)
        baseline_rows = [card(1, previous, 20), card(2, complete, 35)]
        expected = views([previous, complete], baseline_rows)
        for status, boundary in (('partial', False), ('failed', False), ('complete', False)):
            with self.subTest(status=status, boundary=boundary):
                later = run('unusable', 6, 7, complete=False)
                later.update(status=status, end_boundary_observed=boundary)
                # A failed job normally has no imported source run or cards.
                # Even this defensive malformed-input fixture cannot displace a complete run.
                extra = [] if status == 'failed' else [card(3, later, 999)]
                actual = views([previous, complete, later], [*baseline_rows, *extra])
                self.assertEqual(actual['warehouse_days'][-1]['run_id'], 'complete')
                for key in ('warehouse_records', 'warehouse_points', 'warehouse_focus_items', 'history', 'trend_signals'):
                    self.assertEqual(actual[key], expected[key])

    def test_beijing_midnight_uses_capture_end_not_start_or_utc_date(self):
        before_midnight = run('before-midnight', 5, 15)
        crossing = run('crossing', 5, 15)
        crossing.update(observed_from='2026-10-05T15:59:00Z', observed_to='2026-10-05T16:01:00Z')
        afternoon = run('afternoon', 6, 7)
        result = views([afternoon, before_midnight, crossing],
                       [card(1, before_midnight, 10), card(2, crossing, 12), card(3, afternoon, 19)])
        self.assertEqual([(d['date'], d['run_id']) for d in result['warehouse_days']],
                         [('2026-10-05', 'before-midnight'), ('2026-10-06', 'afternoon')])
        self.assertEqual(result['warehouse_days'][-1]['version_count'], 2)
        self.assertEqual([p['run_id'] for p in result['history']], ['before-midnight', 'afternoon'])
        self.assertEqual(result['warehouse_focus_items'][0]['yesterday_delta'], 9)

    def test_refresh_one_shop_keeps_other_shop_with_same_goods_id_unchanged(self):
        a_old, b_old = run('a-old', 5), run('b-old', 5, shop='B')
        a_new, b_new = run('a-new', 6), run('b-new', 6, shop='B')
        initial_runs = [a_old, b_old, a_new, b_new]
        initial_rows = [card(1, a_old, 10), card(2, b_old, 90), card(3, a_new, 20), card(4, b_new, 100)]
        before = views(initial_runs, initial_rows)
        refresh = run('a-refresh', 6, 7)
        after = views([*initial_runs, refresh], [*initial_rows, card(5, refresh, 40)])
        for key in ('warehouse_days', 'warehouse_records', 'warehouse_points', 'warehouse_focus_summary',
                    'warehouse_focus_items', 'history', 'trend_signals'):
            self.assertEqual([r for r in before[key] if r['shop_id'] == 'B'],
                             [r for r in after[key] if r['shop_id'] == 'B'])
        self.assertEqual({r['shop_id']: r['yesterday_delta'] for r in after['warehouse_focus_items']},
                         {'A': 30, 'B': 10})
