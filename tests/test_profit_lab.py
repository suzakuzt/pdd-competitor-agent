from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
import uuid

from pdd_monitor.profit_lab import (PLAN_FIELDS, PLAN_COST_FIELDS, ACTUAL_COST_FIELDS,
    RevisionConflict, IntegrityError, calculate_plan, evaluate_trial, load_trials, save_trial, build_trial_queries)


def plan():
    return dict(price='100', goods_cost='30', packaging_cost='2', shipping_cost='5',
                platform_fee_rate_pct='3', platform_fee_fixed='1', refund_allowance='4',
                ad_cost_per_order='10', other_cost_per_order='1', tax_cost_per_order='1',
                fixed_test_cost='100', max_loss_budget='50', target_orders=10, review_after_orders=5)


def actual():
    return dict(period_start='2026-10-01', period_end='2026-10-04', paid_orders=10, settled_orders=10,
                net_receipts='800', goods_cost='300', packaging_cost='20', shipping_cost='50',
                platform_fees='30', ad_spend='100', refund_extra_cost='40', tax_cost='10',
                other_cost='10', fixed_cost='100', settlement_complete=True,
                evidence_reference='结算报表A，2026-10-01至2026-10-04，同一SKU队列')


def trial():
    return dict(trial_id=str(uuid.uuid4()), revision=0, name='样品A测试', shop_id='shop_a', observation_id=123,
                channel='用户填写渠道', own_product_ref='own_sku_a', status='draft', plan=plan(), actual={},
                checks=dict(supply_confirmed=True, rights_confirmed=True, spec_confirmed=True), notes=None)


def hashes(root):
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob('*') if p.is_file()}


class ProfitLabTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.project = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_empty_inputs_remain_unknown_not_assumed_zero(self):
        result = calculate_plan({})
        self.assertEqual(set(result['missing_fields']), {'plan.' + k for k in PLAN_FIELDS})
        for key in ('contribution_per_order', 'planned_surplus', 'break_even_orders', 'max_ad_cost_per_order', 'max_ad_after_fixed_per_order'):
            self.assertIsNone(result[key])
        analysis = evaluate_trial({})
        self.assertEqual(analysis['recommendation']['code'], 'missing_info')
        self.assertFalse(analysis['readiness']['ready'])
        self.assertIsNone(analysis['actual_result']['actual_profit'])

    def test_plan_all_costs_fees_fixed_and_round_up_break_even(self):
        result = calculate_plan(plan())
        self.assertEqual(result['platform_fee_per_order'], '4')
        self.assertEqual(result['variable_cost_per_order'], '57')
        self.assertEqual(result['contribution_per_order'], '43')
        self.assertEqual(result['before_ad_contribution'], '53')
        self.assertEqual(result['planned_surplus'], '330')
        self.assertEqual(result['break_even_orders'], 3)
        self.assertEqual(result['max_ad_after_fixed_per_order'], '43')
        self.assertFalse(result['guaranteed_profit'])

    def test_exact_decimal_math_and_explicit_zero(self):
        value = {k: '0' for k in PLAN_FIELDS}
        value.update(price='0.3', goods_cost='0.1', packaging_cost='0.2', target_orders=1, review_after_orders=1)
        result = calculate_plan(value)
        self.assertEqual(result['contribution_per_order'], '0')
        self.assertEqual(result['planned_surplus'], '0')
        self.assertIsNone(result['break_even_orders'])
        self.assertEqual(result['max_ad_after_fixed_per_order'], '0')
        self.assertEqual(result['missing_fields'], [])

    def test_missing_ad_can_show_before_ad_limit_but_no_fake_margin(self):
        value = plan(); value['ad_cost_per_order'] = None
        result = calculate_plan(value)
        self.assertEqual(result['max_ad_cost_per_order'], '53')
        self.assertEqual(result['max_ad_after_fixed_per_order'], '43')
        self.assertIsNone(result['contribution_per_order'])
        self.assertIsNone(result['planned_surplus'])

    def test_full_cost_ad_limit_floors_cents_and_can_be_negative(self):
        value = plan(); value.update(target_orders=3, fixed_test_cost='1')
        self.assertEqual(calculate_plan(value)['max_ad_after_fixed_per_order'], '52.66')
        value['fixed_test_cost'] = '200'
        self.assertEqual(calculate_plan(value)['max_ad_after_fixed_per_order'], '-13.67')

    def test_invalid_blank_boolean_nonfinite_negative_and_rate_inputs(self):
        for key, value in [('goods_cost', ''), ('goods_cost', True), ('goods_cost', 'NaN'),
                           ('goods_cost', 'Infinity'), ('goods_cost', '-1'),
                           ('platform_fee_rate_pct', '100.001'), ('target_orders', '1.1'), ('target_orders', False),
                           ('target_orders', 0), ('review_after_orders', 0)]:
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                data = plan(); data[key] = value; calculate_plan(data)
        with self.assertRaises(ValueError):
            calculate_plan({'shipping': '1'})

    def test_negative_unit_margin_stops_without_inventing_break_even(self):
        value = trial(); value['plan']['goods_cost'] = '100'
        analysis = evaluate_trial(value)
        self.assertEqual(analysis['recommendation']['code'], 'pause_review')
        self.assertIsNone(analysis['plan_result']['break_even_orders'])

    def test_fixed_cost_can_hit_planned_loss_budget_with_positive_margin(self):
        value = trial(); value['plan'].update(fixed_test_cost='500', max_loss_budget='70')
        analysis = evaluate_trial(value)
        self.assertEqual(analysis['plan_result']['contribution_per_order'], '43')
        self.assertEqual(analysis['plan_result']['planned_surplus'], '-70')
        self.assertEqual(analysis['recommendation']['code'], 'pause_review')

    def test_ready_needs_user_checks_and_attribution(self):
        value = trial()
        self.assertEqual(evaluate_trial(value)['recommendation']['code'], 'not_started')
        for field in ('supply_confirmed', 'rights_confirmed', 'spec_confirmed'):
            value = trial(); value['checks'][field] = None
            self.assertFalse(evaluate_trial(value)['readiness']['ready'])
        value = trial(); value['channel'] = None
        self.assertFalse(evaluate_trial(value)['readiness']['ready'])
        for bad in ('123', True, 0, -1):
            with self.assertRaises(ValueError):
                value = trial(); value['observation_id'] = bad; evaluate_trial(value)

    def test_actual_refund_cost_and_all_actual_costs_included_once(self):
        value = trial(); value['actual'] = actual()
        result = evaluate_trial(value)['actual_result']
        self.assertEqual(result['cost_total'], '660')
        self.assertEqual(result['actual_profit'], '140')
        self.assertTrue(result['profit_is_final'])
        self.assertFalse(result['platform_verified'])
        self.assertEqual(result['evidence_status'], 'user_declared_reference')

    def test_missing_any_actual_numeric_or_scope_field_leaves_profit_unknown(self):
        for missing in ('net_receipts', *ACTUAL_COST_FIELDS, 'paid_orders', 'settled_orders', 'period_start', 'period_end', 'settlement_complete'):
            with self.subTest(missing=missing):
                value = trial(); value['actual'] = actual(); value['actual'][missing] = None
                analysis = evaluate_trial(value)
                self.assertIsNone(analysis['actual_result']['actual_profit'])
                self.assertEqual(analysis['recommendation']['code'], 'missing_info')

    def test_unsettled_or_insufficient_sample_or_evidence_never_retests(self):
        value = trial(); value['actual'] = actual()
        value['actual']['settlement_complete'] = False
        value['actual']['settled_orders'] = 5
        analysis = evaluate_trial(value)
        self.assertEqual(analysis['actual_result']['actual_profit'], '140')
        self.assertFalse(analysis['actual_result']['profit_is_final'])
        self.assertEqual(analysis['recommendation']['code'], 'keep_observing')
        value['actual'] = actual(); value['plan']['review_after_orders'] = 11
        self.assertEqual(evaluate_trial(value)['recommendation']['code'], 'keep_observing')
        value['plan']['review_after_orders'] = 5; value['actual']['evidence_reference'] = None
        self.assertEqual(evaluate_trial(value)['recommendation']['code'], 'missing_info')
        self.assertEqual(evaluate_trial(value)['actual_result']['actual_profit'], '140')

    def test_settled_positive_with_checks_and_evidence_only_considers_retest(self):
        value = trial(); value['actual'] = actual(); value['status'] = 'testing'
        analysis = evaluate_trial(value)
        self.assertEqual(analysis['recommendation']['code'], 'consider_small_retest')
        self.assertFalse(analysis['automatic_action'])
        value['checks']['rights_confirmed'] = False
        self.assertEqual(evaluate_trial(value)['recommendation']['code'], 'missing_info')
        value['status'] = 'closed'
        self.assertEqual(evaluate_trial(value)['recommendation']['label'], '已关闭，保留复盘')

    def test_actual_loss_threshold_and_zero_budget_not_zero_profit_pause(self):
        value = trial(); value['actual'] = actual(); value['actual']['net_receipts'] = '610'
        self.assertEqual(evaluate_trial(value)['recommendation']['code'], 'pause_review')
        value['plan']['max_loss_budget'] = '0'; value['actual']['net_receipts'] = '660'
        self.assertNotEqual(evaluate_trial(value)['recommendation']['code'], 'pause_review')
        value['actual']['net_receipts'] = '659.99'
        self.assertEqual(evaluate_trial(value)['recommendation']['code'], 'pause_review')

    def test_zero_orders_explicit_zero_costs_do_not_prove_profit(self):
        value = trial(); value['actual'] = actual()
        value['actual'].update({key: '0' for key in ('net_receipts', *ACTUAL_COST_FIELDS)})
        value['actual'].update(paid_orders=0, settled_orders=0)
        result = evaluate_trial(value)
        self.assertEqual(result['actual_result']['actual_profit'], '0')
        self.assertEqual(result['recommendation']['code'], 'keep_observing')

    def test_actual_period_counts_and_settlement_consistency(self):
        for changes in ({'settled_orders': 11}, {'settled_orders': 9}, {'period_start': '2026-10-05'},
                        {'period_end': '2026-10-04T00:00:00Z'}, {'period_end': '2026-10-04T00:00:00'},
                        {'paid_orders': 0, 'settled_orders': 0}, {'settlement_complete': 'true'}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                value = trial(); value['actual'] = actual(); value['actual'].update(changes); evaluate_trial(value)

    def test_readonly_empty_query_and_no_direct_financial_aggregation(self):
        before = hashes(self.project)
        self.assertEqual(load_trials(self.project), ([], {}))
        q = build_trial_queries(self.project)
        self.assertEqual(q['profit_trials']['rows'], [])
        self.assertEqual(q['profit_trial_summary']['rows'], [])
        self.assertEqual(before, hashes(self.project))

    def test_first_save_replay_and_numeric_canonical_idempotence(self):
        value = trial(); receipt = save_trial(self.project, value, 0)
        before = hashes(self.project)
        value['plan']['price'] = '100.00'
        replay = save_trial(self.project, value, 0)
        self.assertEqual(replay['status'], 'duplicate')
        self.assertEqual(replay['revision'], 1)
        self.assertEqual(before, hashes(self.project))
        self.assertEqual(receipt['trial']['observation_id'], 123)
        self.assertEqual(receipt['trial']['plan']['price'], '100')

    def test_append_revision_keeps_original_and_rejects_stale_changed_payload(self):
        value = trial(); first = save_trial(self.project, value, 0)
        first_path = Path(first['path']); first_bytes = first_path.read_bytes()
        second = deepcopy(first['trial']); second['notes'] = '新增说明'
        second_receipt = save_trial(self.project, second, 1)
        self.assertEqual(second_receipt['revision'], 2)
        self.assertEqual(first_path.read_bytes(), first_bytes)
        value['notes'] = '过期页面的另一修改'
        with self.assertRaises(RevisionConflict):
            save_trial(self.project, value, 0)
        latest, files = load_trials(self.project)
        self.assertEqual(latest[0]['notes'], '新增说明')
        self.assertEqual(len(files), 2)

    def test_concurrent_writers_one_revision_wins(self):
        value = trial(); first = save_trial(self.project, value, 0)
        gate = threading.Barrier(2)
        def write(note):
            payload = deepcopy(first['trial']); payload['notes'] = note
            gate.wait()
            try:
                return save_trial(self.project, payload, 1)['status']
            except RevisionConflict:
                return 'conflict'
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(write, ['A', 'B']))
        self.assertEqual(sorted(results), ['conflict', 'saved'])
        latest, files = load_trials(self.project)
        self.assertEqual(latest[0]['revision'], 2)
        self.assertEqual(len(files), 2)

    def test_trial_uuid_path_revision_and_owner_lock_safety(self):
        value = trial()
        for bad in ('../outside', str(uuid.uuid4()).upper()):
            with self.assertRaises(ValueError):
                payload = deepcopy(value); payload['trial_id'] = bad; save_trial(self.project, payload, 0)
        with self.assertRaises(ValueError):
            save_trial(self.project, value, True)
        locks = self.project / 'state/profit_lab/.locks'; locks.mkdir(parents=True)
        path = locks / (value['trial_id'] + '.lock'); path.write_text('{"pid":999999}')
        before = hashes(self.project)
        with self.assertRaises(RevisionConflict):
            save_trial(self.project, value, 0)
        self.assertEqual(before, hashes(self.project))

    def test_fixed_actual_cohort_rejects_mixing_window_attribution_or_counts(self):
        value = trial(); value['actual'] = actual()
        first = save_trial(self.project, value, 0)
        cases = [lambda row: row.update(own_product_ref='different_sku'),
                 lambda row: row['actual'].update(period_start='2026-09-01'),
                 lambda row: row['actual'].update(period_end='2026-10-03'),
                 lambda row: row['actual'].update(paid_orders=9, settled_orders=9)]
        for mutate in cases:
            with self.subTest(mutate=mutate), self.assertRaises(ValueError):
                row = deepcopy(first['trial']); mutate(row); save_trial(self.project, row, 1)
        row = deepcopy(first['trial']); row['actual'].update(period_end='2026-10-05', paid_orders=11, settled_orders=11)
        with patch('pdd_monitor.profit_lab._now', return_value='2026-10-06T00:00:00Z'):
            self.assertEqual(save_trial(self.project, row, 1)['revision'], 2)

    def test_future_completed_period_rejected_with_explicit_as_of(self):
        value = trial(); value['actual'] = actual()
        with self.assertRaisesRegex(ValueError, 'future'):
            evaluate_trial(value, now='2026-10-03T12:00:00Z')
        self.assertEqual(evaluate_trial(value, now='2026-10-04T00:00:00Z')['recommendation']['code'], 'consider_small_retest')
        value['actual'].update(period_start='2026-10-01T00:00:00Z', period_end='2026-10-04T10:00:00Z')
        with self.assertRaisesRegex(ValueError, 'future'):
            evaluate_trial(value, now='2026-10-04T09:00:00Z')

    def test_erasing_actual_counts_and_period_does_not_remove_queue_guard(self):
        value = trial(); value['actual'] = actual()
        first = save_trial(self.project, value, 0)
        for field in ('paid_orders', 'settled_orders', 'period_start', 'period_end'):
            with self.subTest(field=field), self.assertRaises(ValueError):
                row = deepcopy(first['trial']); row['actual'][field] = None; save_trial(self.project, row, 1)
        with self.assertRaises(ValueError):
            row = deepcopy(first['trial']); row['actual'] = {}; save_trial(self.project, row, 1)

    def test_all_historical_actual_anchors_survive_costs_returning_to_unknown(self):
        value = trial(); value['actual'] = {'goods_cost': '10'}
        first = save_trial(self.project, value, 0)
        row = deepcopy(first['trial']); row['actual'] = {}
        second = save_trial(self.project, row, 1)
        row = deepcopy(second['trial']); row['channel'] = 'another channel'
        with self.assertRaises(ValueError):
            save_trial(self.project, row, 2)

    def test_tampered_or_missing_revision_fails_without_repair(self):
        first = save_trial(self.project, trial(), 0)
        row = deepcopy(first['trial']); row['notes'] = 'revision2'
        second = save_trial(self.project, row, 1)
        path = Path(first['path'])
        original = path.read_bytes(); tampered = json.loads(original); tampered['trial']['plan']['price'] = '999'
        path.write_text(json.dumps(tampered), encoding='utf-8')
        before = hashes(self.project)
        with self.assertRaises(IntegrityError):
            build_trial_queries(self.project)
        self.assertEqual(before, hashes(self.project))
        path.unlink()
        with self.assertRaises(IntegrityError):
            load_trials(self.project)

    def test_source_versions_and_shop_summaries_are_isolated_without_profit_sum(self):
        a = trial(); a['actual'] = actual(); save_trial(self.project, a, 0)
        b = trial(); b['shop_id'] = 'shop_b'; b['actual'] = actual(); save_trial(self.project, b, 0)
        before = hashes(self.project)
        q = build_trial_queries(self.project)
        self.assertEqual(len(q['profit_trials']['rows']), 2)
        summaries = {r['shop_id']: r for r in q['profit_trial_summary']['rows']}
        self.assertEqual(set(summaries), {'shop_a', 'shop_b'})
        self.assertTrue(all(row['trial_count'] == 1 and not row['financial_totals_aggregated'] for row in summaries.values()))
        self.assertTrue(all('actual_profit' not in row for row in summaries.values()))
        self.assertEqual(len(q['profit_trials']['source']['files']), 2)
        self.assertEqual(before, hashes(self.project))


if __name__ == '__main__':
    unittest.main()
