from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest

from pdd_monitor.trend_decisions import (PROTOCOL, read_ledger, update_ledger,
    record_decisions, build_decision_queries, ranked_signals, ledger_path)


START = datetime(2026, 10, 1, 4, tzinfo=timezone.utc)


def fixtures(*, weak=False, delta=3, baseline=1):
    runs, observations, days = [], [], []
    for i in range(8):
        stamp = (START + timedelta(days=i)).isoformat()
        run = {'shop_id': 'A', 'run_id': f'A{i}', 'status': 'complete',
               'end_boundary_observed': True, 'observed_from': stamp,
               'observed_to': stamp, 'imported_at': stamp, 'snapshot_sha256': f'sha{i}'}
        runs.append(run); days.append({'shop_id': 'A', 'run_id': f'A{i}', 'date': stamp[:10]})
        observations.append({'observation_id': i + 1, 'run_id': f'A{i}', 'view_order': 1,
            'title': 'SYNTHETIC', 'goods_id': None if weak else '123',
            'identity_status': 'missing_goods_id' if weak else 'unique_goods_id',
            'sales_value': 10 + i * delta, 'sales_raw': f'已拼{10+i*delta}件',
            'sales_precision': 'exact_display', 'sales_label': '已拼', 'sales_unit': '件',
            'image_url': 'https://example.invalid/synthetic.png', 'observed_at': stamp,
            'observed_at_precision': 'batch_read'})
    signal = {'shop_id': 'A', 'run_id': 'A0', 'observation_id': 1,
              'included_in_watch': True, 'level': 'observe', 'label': '单日增长线索',
              'title': 'SYNTHETIC', 'latest_delta': 2, 'rate24': 2,
              'avg_recent': baseline, 'normalized_delta_days': 3,
              'rule_version': 'synthetic_v1',
              'basis': 'provisional_title_image' if weak else 'confirmed_goods_id'}
    return {key: {'rows': rows, 'source': {'label': 'SYNTHETIC ONLY'}} for key, rows in
            [('runs', runs), ('observations', observations), ('warehouse_days', days), ('trend_signals', [signal])]}


def fresh():
    return {'version': 1, 'revision': 0, 'cohorts': []}


class DecisionTests(unittest.TestCase):
    def test_prospective_freeze_is_idempotent_and_excludes_stale_shops(self):
        q = fixtures(); state = update_ledger(fresh(), q, now=START + timedelta(minutes=5))
        self.assertEqual(len(state['cohorts']), 1)
        self.assertEqual(state['cohorts'][0]['selected_ids'], [1])
        self.assertEqual(state['cohorts'][0]['evaluations']['1']['evaluation_state'], 'waiting')
        self.assertEqual(state, update_ledger(state, q, now=START + timedelta(minutes=10)))
        self.assertEqual(update_ledger(fresh(), q, now=START + timedelta(days=1))['cohorts'], [])
        q['trend_signals']['rows'][0]['latest_delta'] = 999
        self.assertEqual(state['cohorts'][0]['signals'][0]['latest_delta'], 2)

    def test_known_future_data_does_not_leak_into_early_outcomes(self):
        state = update_ledger(fresh(), fixtures(), now=START)
        self.assertIsNone(state['cohorts'][0]['evaluations']['1']['outcome'])

    def test_seven_day_success_and_mature_result_freeze(self):
        q = fixtures(); state = update_ledger(fresh(), q, now=START)
        done = update_ledger(state, q, now=START + timedelta(days=8, minutes=5))
        result = done['cohorts'][0]['evaluations']['1']
        self.assertTrue(result['outcome']); self.assertEqual(result['future_delta'], 21)
        self.assertEqual(result['future_rate24'], 3)
        q['observations']['rows'][-1]['sales_value'] = 1
        self.assertEqual(update_ledger(done, q, now=START + timedelta(days=8))['cohorts'][0]['evaluations']['1'], result)

    def test_clue_and_confirmed_hit_rates_never_mix(self):
        q = fixtures(weak=True); state = update_ledger(fresh(), q, now=START)
        state = update_ledger(state, q, now=START + timedelta(days=8, minutes=5))
        with tempfile.TemporaryDirectory() as tmp:
            stats = build_decision_queries(q, tmp, ledger=state)['trend_decision_summary']['rows'][0]
        self.assertEqual(stats['evaluated_clue_count'], 1)
        self.assertIsNone(stats['confirmed_hit_rate'])
        self.assertEqual(stats['clue_hit_rate'], 1)

    def test_baseline_short_or_zero_remains_unknown_not_failure(self):
        for baseline, count in [(0, 3), (1, 1)]:
            q = fixtures(baseline=baseline); q['trend_signals']['rows'][0]['normalized_delta_days'] = count
            state = update_ledger(fresh(), q, now=START)
            state = update_ledger(state, q, now=START + timedelta(days=8, minutes=5))
            outcome = state['cohorts'][0]['evaluations']['1']
            self.assertIsNone(outcome['outcome']); self.assertEqual(outcome['future_delta'], 21)

    def test_missing_or_delisted_card_keeps_original_cohort_unknown(self):
        q = fixtures(); state = update_ledger(fresh(), q, now=START)
        q['observations']['rows'] = q['observations']['rows'][:-1]
        q['trend_signals']['rows'] = []
        result = update_ledger(state, q, now=START + timedelta(days=8, minutes=5))['cohorts'][0]
        self.assertEqual(result['selected_ids'], [1]); self.assertIsNone(result['evaluations']['1']['outcome'])

    def test_gap_partial_ambiguous_negative_and_imprecise_future_are_unknown(self):
        for kind in ['gap', 'partial', 'duplicate', 'negative', 'imprecise']:
            q = fixtures(weak=True); state = update_ledger(fresh(), q, now=START)
            if kind == 'gap': q['warehouse_days']['rows'].pop(3)
            elif kind == 'partial': q['runs']['rows'][3]['status'] = 'partial'
            elif kind == 'duplicate':
                duplicate = deepcopy(q['observations']['rows'][3]); duplicate['observation_id'] = 100
                q['observations']['rows'].append(duplicate)
            elif kind == 'negative': q['observations']['rows'][3]['sales_value'] = 0
            else: q['observations']['rows'][3]['observed_at_precision'] = 'legacy_unspecified'
            result = update_ledger(state, q, now=START + timedelta(days=8, minutes=5))['cohorts'][0]['evaluations']['1']
            self.assertIsNone(result['outcome'], kind)

    def test_other_store_cannot_fill_missing_future_day(self):
        q = fixtures(); state = update_ledger(fresh(), q, now=START)
        q['warehouse_days']['rows'][-1]['shop_id'] = 'B'
        with self.assertRaisesRegex(ValueError, 'mismatched shop/date/run'):
            update_ledger(state, q, now=START + timedelta(days=8))

    def test_stagnation_is_failure_only_when_complete_comparable_data_exists(self):
        q = fixtures(delta=0); state = update_ledger(fresh(), q, now=START)
        result = update_ledger(state, q, now=START + timedelta(days=8, minutes=5))['cohorts'][0]['evaluations']['1']
        self.assertIs(result['outcome'], False)

    def test_full_reference_pool_includes_nonselected_and_zero_cards(self):
        q = fixtures(); extra = deepcopy(q['observations']['rows'][0])
        extra.update(observation_id=90, title='ZERO', goods_id='900', sales_value=0, sales_raw='已拼0件')
        q['observations']['rows'].append(extra)
        state = update_ledger(fresh(), q, now=START)
        self.assertEqual(len(state['cohorts'][0]['candidates']), 2)
        self.assertEqual(state['cohorts'][0]['selected_ids'], [1])

    def test_time_normalized_outcome_uses_total_exposure_not_mean_of_rates(self):
        q = fixtures(); state = update_ledger(fresh(), q, now=START)
        stamp = START + timedelta(days=7, hours=6)
        q['observations']['rows'][-1]['observed_at'] = stamp.isoformat()
        q['runs']['rows'][-1].update(observed_to=stamp.isoformat(), observed_from=stamp.isoformat())
        result = update_ledger(state, q, now=stamp + timedelta(days=1, minutes=5))['cohorts'][0]['evaluations']['1']
        self.assertAlmostEqual(result['future_rate24'], 21 / 174 * 24, places=6)

    def test_atomic_persistence_backup_corruption_and_busy_lock(self):
        with tempfile.TemporaryDirectory() as tmp:
            q = fixtures(); initial = record_decisions(tmp, q, now=START)
            path = ledger_path(tmp); first = path.read_bytes()
            self.assertEqual(read_ledger(tmp), initial)
            record_decisions(tmp, q, now=START + timedelta(minutes=2))
            self.assertEqual(path.read_bytes(), first)
            record_decisions(tmp, q, now=START + timedelta(days=8, minutes=1))
            self.assertEqual(len(list((path.parent / 'history').glob('*.json'))), 1)
            lock = path.with_suffix('.lock'); lock.write_text('synthetic owner')
            with self.assertRaisesRegex(ValueError, 'busy'): record_decisions(tmp, q, now=START)
            self.assertTrue(lock.exists()); lock.unlink()
            path.write_text('{broken')
            with self.assertRaises(ValueError): record_decisions(tmp, q, now=START)
            self.assertEqual(path.read_text(), '{broken')

    def test_top_five_order_prefers_sustained_evidence_over_raw_total(self):
        values = [dict(fixtures()['trend_signals']['rows'][0], observation_id=i,
                       level='priority' if i == 2 else 'observe', rate24=float(i)) for i in range(1, 8)]
        self.assertEqual([r['observation_id'] for r in ranked_signals(values)[:5]], [2, 7, 6, 5, 4])

    def test_ledger_reuses_the_exact_review_queue_and_actual_model_version(self):
        q = fixtures()
        q['trend_signals']['rows'][0].update(model_version='model_v2', selected_for_review=False, rank=1)
        state = update_ledger(fresh(), q, now=START)
        self.assertEqual(state['cohorts'][0]['selected_ids'], [])
        self.assertEqual(state['cohorts'][0]['rule_version'], 'model_v2')

    def test_changed_source_snapshot_cannot_rewrite_original_evidence(self):
        q = fixtures(); state = update_ledger(fresh(), q, now=START)
        q['runs']['rows'][0]['snapshot_sha256'] = 'changed'
        result = update_ledger(state, q, now=START + timedelta(days=8, minutes=5))['cohorts'][0]['evaluations']['1']
        self.assertIsNone(result['outcome'])
        self.assertIn('原始', result['outcome_label'])


if __name__ == '__main__': unittest.main()
