"""Current business alias changes derived data only; frozen contracts survive."""
from copy import deepcopy
from datetime import timedelta
import json
from pathlib import Path
import tempfile
import unittest

from pdd_monitor.daily_warehouse import build_daily_warehouse
from pdd_monitor.history import build_history
from pdd_monitor.warehouse_focus import build_warehouse_focus
from pdd_monitor.warehouse_display_history import build_display_history
from pdd_monitor.trend_signals import build_trend_signals, MODEL_VERSION
from pdd_monitor.trend_decisions import (LEGACY_PROTOCOL, PROTOCOL, evaluate_cohort,
    update_ledger, read_ledger, ledger_path, build_decision_queries)
from pdd_monitor.trend_backtest import build_backtest_queries
from tests.test_daily_warehouse import run, card
from tests.test_trend_decisions import fixtures, fresh, START


class SalesMetricAliasTests(unittest.TestCase):
    def test_cross_label_daily_curves_and_trends_preserve_original_fields(self):
        for first, second in [('已拼', '已抢'), ('已抢', '已拼'), ('已抢', '已抢')]:
            with self.subTest(first=first, second=second):
                runs = [run('old', 4), run('new', 5)]
                rows = [card(1, runs[0], 9, sales_label=first, sales_raw=first+'9件'),
                        card(2, runs[1], 19, sales_label=second, sales_raw=second+'19件')]
                before = deepcopy((runs, rows))
                focus = build_warehouse_focus(runs, rows)
                self.assertEqual(focus['warehouse_focus_items'][0]['yesterday_delta'], 10)
                self.assertEqual(focus['warehouse_focus_items'][0]['category'], 'yipin_gt10')
                daily = build_daily_warehouse(runs, rows)
                self.assertEqual([p['cumulative_yipin'] for p in daily['warehouse_points']], [9, 19])
                self.assertEqual(daily['warehouse_points'][1]['daily_delta'], 10)
                history = build_display_history(runs, rows, focus)
                self.assertEqual([p['cumulative_yipin'] for p in history], [9, 19])
                trend = build_trend_signals(focus['warehouse_focus_items'], history,
                                           focus['warehouse_focus_summary'])['trend_signals'][0]
                self.assertTrue(trend['included_in_watch'])
                self.assertEqual(trend['latest_delta'], 10)
                self.assertEqual(trend['model_version'], MODEL_VERSION)
                self.assertEqual((runs, rows), before)

    def test_alias_thresholds_still_exclude_fuzzy_missing_and_wrong_unit(self):
        source = run('r', 5)
        rows = [card(i, source, value, goods=None, sales_label='已抢', sales_raw=f'已抢{value}件')
                for i, value in enumerate([0, 1, 10, 11], 1)]
        rows.extend([card(5, source, None, goods=None, sales_label='已抢', sales_raw='已抢10+件', sales_precision='non_exact_or_unparsed'),
                     card(6, source, 99, goods=None, sales_label='总售', sales_raw='总售99件'),
                     card(7, source, 99, goods=None, sales_label='已抢', sales_unit='单', sales_raw='已抢99单')])
        result = build_warehouse_focus([source], rows)['warehouse_focus_items']
        self.assertEqual([r['yipin_value'] for r in result], [0, 1, 10, 11, None, None, None])
        self.assertEqual([r['category'] for r in result[:5]],
                         ['yipin_zero', 'yipin_1to10', 'yipin_1to10', 'yipin_gt10', 'unknown'])

    def test_original_protocol_evaluation_stays_yipin_only(self):
        q = fixtures()
        current = update_ledger(fresh(), q, now=START)['cohorts'][0]
        legacy = deepcopy(current)
        legacy['protocol'] = deepcopy(LEGACY_PROTOCOL)
        for row in q['observations']['rows'][1:]:
            row['sales_label'] = '已抢'
            row['sales_raw'] = f"已抢{row['sales_value']}件"
        now = START + timedelta(days=8)
        self.assertTrue(evaluate_cohort(current, q, now=now)['1']['outcome'])
        self.assertIsNone(evaluate_cohort(legacy, q, now=now)['1']['outcome'])

    def test_mixed_ledger_keeps_old_records_out_of_new_protocol_rates(self):
        q = fixtures()
        current = update_ledger(fresh(), q, now=START)['cohorts'][0]
        old = deepcopy(current)
        old.update(cohort_id='legacy-id', protocol=deepcopy(LEGACY_PROTOCOL))
        old['evaluations']['1'] = dict(evaluation_state='evaluated', outcome=True,
                                      basis='confirmed_goods_id', outcome_label='old result')
        ledger = dict(version=1, revision=1, cohorts=[old, current])
        with tempfile.TemporaryDirectory() as tmp:
            path = ledger_path(tmp)
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps(ledger), encoding='utf-8')
            self.assertEqual(read_ledger(tmp), ledger)
            report = build_decision_queries(q, tmp, ledger=ledger)
            summary = report['trend_decision_summary']['rows'][0]
            self.assertEqual(summary['legacy_protocol_record_count'], 1)
            self.assertEqual(summary['waiting_count'], 1)
            self.assertIsNone(summary['confirmed_hit_rate'])
            self.assertEqual(summary['protocol_version'], PROTOCOL['version'])
            self.assertEqual(len(report['trend_decision_records']['rows']), 2)
            old_only = dict(ledger, cohorts=[old])
            backtest = build_backtest_queries(q, tmp, ledger=old_only, now=START+timedelta(days=8))
            self.assertEqual(backtest['trend_backtest_windows']['rows'], [])
        self.assertEqual(ledger['cohorts'][0], old)


if __name__ == '__main__':
    unittest.main()
