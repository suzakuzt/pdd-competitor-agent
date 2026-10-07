"""Synthetic frozen decisions; never read or write production data."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import unittest

from pdd_monitor.trend_backtest import build_backtest_queries, ALGORITHMS
from pdd_monitor.trend_decisions import PROTOCOL, update_ledger
from tests.test_trend_decisions import fixtures as decision_fixture, fresh, START


START_DAY = datetime(2026, 10, 1, 4, tzinfo=timezone.utc)


def make_fixture(days=(0,), n=6, *, channel='confirmed'):
    runs, observations, daily = [], [], []
    for day in range(-3, max(days) + 8):
        stamp = (START_DAY + timedelta(days=day)).isoformat()
        rid = 'r' + str(day)
        runs.append(dict(run_id=rid, shop_id='A', observed_from=stamp, observed_to=stamp,
                         imported_at=stamp, snapshot_sha256='sha' + str(day), status='complete', end_boundary_observed=True))
        daily.append(dict(shop_id='A', run_id=rid, date=stamp[:10]))
        for index in range(1, n + 1):
            observations.append(dict(observation_id=(day + 10) * 100 + index, run_id=rid, view_order=index,
                title=f'SYNTHETIC ONLY {index}', goods_id=str(index) if channel == 'confirmed' else None,
                identity_status='unique_goods_id' if channel == 'confirmed' else 'unknown_no_goods_id',
                sales_raw=f'已拼{100 + index}件', sales_value=100 + index, sales_label='已拼', sales_unit='件',
                sales_precision='exact_display', image_url=f'https://example.invalid/{index}.png',
                asset_sha256=f'{index:064x}', image_content_status='verified_local', image_content_kind='original_url',
                observed_at=stamp, observed_at_precision='card_read'))
    by_run = {r['run_id']: [o for o in observations if o['run_id'] == r['run_id']] for r in runs}
    cohorts = []
    for day in days:
        rid = 'r' + str(day); rows = deepcopy(by_run[rid]); signals, evaluations = [], {}
        for index, card in enumerate(rows, 1):
            oid = card['observation_id']
            rate = 100 if index == 1 else 1 if index == 6 else 60
            recent = 1 if index == 1 else 100 if index == 6 else 60
            signals.append(dict(shop_id='A', run_id=rid, observation_id=oid, title=card['title'],
                included_in_watch=True, level='watch', label='SYNTHETIC', rank=index,
                selected_for_review=index <= 5, latest_delta=rate, rate24=rate, interval_hours=24,
                rate_status='normalized_exact_interval', avg_recent=recent, normalized_delta_days=3,
                basis='confirmed_goods_id' if channel == 'confirmed' else 'provisional_title_image',
                model_version='synthetic_rules', evidence_observation_ids=[(i + 10) * 100 + index for i in range(day - 3, day + 1)],
                evidence_run_ids=['r' + str(i) for i in range(day - 3, day + 1)],
                evidence_dates=[(START_DAY + timedelta(days=i)).date().isoformat() for i in range(day - 3, day + 1)]))
            available = (START_DAY + timedelta(days=day + 8)).isoformat()
            evaluations[str(oid)] = dict(evaluation_state='evaluated', outcome=index != 1,
                basis='confirmed_goods_id' if channel == 'confirmed' else 'provisional_title_image',
                evaluated_at=available, outcome_available_at=available,
                future_run_ids=['r' + str(i) for i in range(day + 1, day + 8)],
                future_observation_ids=[(i + 10) * 100 + index for i in range(day, day + 8)])
        cohorts.append(dict(cohort_id='c' + str(day), shop_id='A', run_id=rid,
            date=(START_DAY + timedelta(days=day)).date().isoformat(),
            due_date=(START_DAY + timedelta(days=day + 7)).date().isoformat(),
            recorded_at=(START_DAY + timedelta(days=day, minutes=5)).isoformat(),
            rule_version='synthetic_rules', protocol=deepcopy(PROTOCOL), snapshot_sha256='sha' + str(day),
            candidates=rows, signals=signals, selected_ids=[r['observation_id'] for r in rows[:5]], evaluations=evaluations))
    q = {key: {'rows': value, 'source': {'label': 'SYNTHETIC ONLY', 'classification': 'primary'}} for key, value in
         [('runs', runs), ('observations', observations), ('warehouse_days', daily)]}
    return q, {'version': 1, 'revision': 1, 'cohorts': cohorts}, START_DAY + timedelta(days=max(days) + 9)


def calculate(q, ledger, now):
    return build_backtest_queries(q, 'SYNTHETIC_NO_IO', ledger=ledger, now=now)


def rows(data, name, channel='confirmed'):
    return [r for r in data['trend_backtest_' + name]['rows'] if r['evidence_channel'] == channel]


class BacktestTests(unittest.TestCase):
    def test_same_pool_and_nonselected_original_cards_are_compared_without_mutation(self):
        q, ledger, now = make_fixture(); before = deepcopy((q, ledger))
        data = calculate(q, ledger, now); windows = rows(data, 'windows')
        self.assertEqual(len(windows), 6)
        self.assertEqual(len({tuple(r['pool_ids']) for r in windows}), 1)
        self.assertTrue(all(r['pool_count'] == 6 for r in windows))
        cumulative = next(r for r in windows if r['strategy'] == 'cumulative')
        self.assertIn(1006, cumulative['selected_ids'])
        self.assertNotIn(1006, ledger['cohorts'][0]['selected_ids'])
        self.assertEqual((q, ledger), before)
        self.assertTrue(all(pack['source']['classification'] == 'derived' for pack in data.values()))
        self.assertEqual(q['observations']['source']['classification'], 'primary')

    def test_unknown_keeps_denominator_and_bounds_not_false_or_full_precision(self):
        q, ledger, now = make_fixture(); evaluation = ledger['cohorts'][0]['evaluations']['1002']
        evaluation.update(evaluation_state='insufficient_data', outcome=None, outcome_label='缺采')
        data = calculate(q, ledger, now)
        rule = next(r for r in rows(data, 'metrics') if r['strategy'] == 'rule')
        self.assertEqual((rule['mature_selected'], rule['known'], rule['unknown'], rule['hit']), (5, 4, 1, 3))
        self.assertEqual(rule['coverage'], .8)
        self.assertEqual(rule['known_hit_rate'], .75)
        self.assertEqual((rule['lower_bound'], rule['upper_bound']), (.6, .8))
        self.assertIsNone(rule['full_precision']); self.assertIsNone(rule['macro_full_precision'])

    def test_waiting_is_separate_and_real_short_history_has_null_rates(self):
        q, ledger, _ = make_fixture(); ledger['cohorts'][0]['evaluations'] = {}
        for signal in ledger['cohorts'][0]['signals']:
            signal.update(rate24=None, rate_status='precision_unknown', normalized_delta_days=0, avg_recent=None)
        data = calculate(q, ledger, START_DAY + timedelta(days=1))
        summary = rows(data, 'summary')[0]
        self.assertEqual(summary['frozen_watch_count'], 6)
        self.assertEqual(summary['excluded_missing_rate'], 6)
        self.assertEqual(summary['readiness'], 'insufficient_pool')
        self.assertTrue(all(r['full_precision'] is None and r['coverage'] is None for r in rows(data, 'metrics')))

    def test_waiting_current_window_does_not_dilute_mature_precision(self):
        q, ledger, _ = make_fixture((0, 8)); ledger['cohorts'][1]['evaluations'] = {}
        data = calculate(q, ledger, START_DAY + timedelta(days=9))
        rule = next(r for r in rows(data, 'metrics') if r['strategy'] == 'rule')
        self.assertEqual((rule['waiting'], rule['mature_selected']), (5, 5))
        self.assertEqual(rule['full_precision'], .8)
        self.assertEqual(rule['macro_full_precision'], .8)

    def test_old_results_without_availability_are_unknown_and_never_train(self):
        q, ledger, now = make_fixture((0, 8))
        for value in ledger['cohorts'][0]['evaluations'].values(): value.pop('outcome_available_at')
        data = calculate(q, ledger, now)
        old = next(r for r in rows(data, 'windows') if r['cohort_id'] == 'c0' and r['strategy'] == 'rule')
        self.assertEqual(old['unknown'], 5); self.assertIsNone(old['full_precision'])
        selection = next(r for r in rows(data, 'selection') if r['cohort_id'] == 'c8')
        self.assertEqual(selection['training_windows'], 0)

    def test_late_import_or_evaluation_cannot_leak_into_past_parameter_choice(self):
        q, ledger, now = make_fixture((0, 8))
        late = START_DAY + timedelta(days=9)
        for value in ledger['cohorts'][0]['evaluations'].values(): value['outcome_available_at'] = late.isoformat()
        data = calculate(q, ledger, now)
        selection = next(r for r in rows(data, 'selection') if r['cohort_id'] == 'c8')
        self.assertEqual(selection['training_windows'], 0)
        self.assertIsNone(selection['candidate_parameter'])
        for value in ledger['cohorts'][0]['evaluations'].values(): value['outcome_available_at'] = (START_DAY + timedelta(days=8)).isoformat()
        selection = next(r for r in rows(calculate(q, ledger, now), 'selection') if r['cohort_id'] == 'c8')
        self.assertEqual(selection['candidate_parameter'], .75)
        self.assertIsNone(selection['chosen_parameter'])

    def test_parameter_selection_uses_only_prior_windows_and_remains_shadow(self):
        q, ledger, now = make_fixture((0, 8, 16, 24, 32))
        result = calculate(q, ledger, now)
        selected = rows(result, 'selection')[-1]
        self.assertEqual(selected['training_windows'], 4)
        self.assertEqual(selected['chosen_parameter'], .75)
        self.assertTrue(selected['shadow_only']); self.assertFalse(selected['forecast_validated'])
        before = deepcopy(selected)
        for value in ledger['cohorts'][-1]['evaluations'].values(): value['outcome'] = not value['outcome']
        self.assertEqual(rows(calculate(q, ledger, now), 'selection')[-1], before)

    def test_one_decision_per_shop_date_and_seven_day_episode_exclusion(self):
        q, ledger, now = make_fixture((0, 1, 7))
        duplicate = deepcopy(ledger['cohorts'][0]); duplicate['cohort_id'] = 'c0_later'
        duplicate['recorded_at'] = (START_DAY + timedelta(hours=1)).isoformat(); ledger['cohorts'].append(duplicate)
        data = calculate(q, ledger, now)
        windows = [r for r in rows(data, 'windows') if r['strategy'] == 'rule']
        self.assertEqual([r['pool_count'] for r in windows], [6, 0, 6])
        self.assertEqual(rows(data, 'summary')[0]['superseded_same_day_cohorts'], 1)
        self.assertEqual(rows(data, 'summary')[0]['excluded_overlapping_episode'], 6)

    def test_provisional_title_original_image_episode_and_channels(self):
        q, ledger, now = make_fixture((0, 1), channel='provisional')
        data = calculate(q, ledger, now)
        self.assertEqual(rows(data, 'summary', 'provisional')[0]['excluded_overlapping_episode'], 6)
        self.assertTrue(all(r['known'] == 0 for r in rows(data, 'metrics', 'confirmed')))
        self.assertTrue(any(r['known'] > 0 for r in rows(data, 'metrics', 'provisional')))

    def test_confirmed_outcome_cannot_silently_accept_provisional_identity(self):
        q, ledger, now = make_fixture()
        ledger['cohorts'][0]['evaluations']['1001']['basis'] = 'provisional_title_image'
        rule = next(r for r in rows(calculate(q, ledger, now), 'metrics') if r['strategy'] == 'rule')
        self.assertEqual(rule['unknown'], 1); self.assertIsNone(rule['full_precision'])

    def test_missing_rate_recent_or_original_image_never_falls_back(self):
        q, ledger, now = make_fixture()
        ledger['cohorts'][0]['signals'][0]['rate24'] = None
        ledger['cohorts'][0]['signals'][1]['avg_recent'] = None
        ledger['cohorts'][0]['candidates'][2]['asset_sha256'] = None
        data = calculate(q, ledger, now)
        self.assertTrue(all(r['pool_ids'] == [1004, 1005, 1006] for r in rows(data, 'windows')))
        summary = rows(data, 'summary')[0]
        self.assertEqual((summary['excluded_missing_rate'], summary['excluded_missing_recent'], summary['excluded_identity_or_image']), (1, 1, 1))

    def test_missing_feature_proof_is_excluded_without_fabricating_recent_baseline(self):
        q, ledger, now = make_fixture()
        for key in ('evidence_observation_ids', 'evidence_run_ids', 'evidence_dates'):
            ledger['cohorts'][0]['signals'][0][key] = []
        data = calculate(q, ledger, now)
        self.assertEqual(rows(data, 'summary')[0]['excluded_missing_feature_evidence'], 1)
        self.assertTrue(all(1001 not in r['pool_ids'] for r in rows(data, 'windows')))

    def test_future_decision_feature_evidence_or_source_import_rejected(self):
        for kind in ('decision', 'feature_date', 'feature_id', 'import'):
            q, ledger, now = make_fixture()
            if kind == 'decision': ledger['cohorts'][0]['recorded_at'] = (now + timedelta(days=1)).isoformat()
            elif kind == 'feature_date': ledger['cohorts'][0]['signals'][0]['evidence_dates'][-1] = '2026-10-02'
            elif kind == 'feature_id': ledger['cohorts'][0]['signals'][0]['evidence_observation_ids'][-1] = 1101
            else: next(r for r in q['runs']['rows'] if r['run_id'] == 'r0')['imported_at'] = (START_DAY + timedelta(days=1)).isoformat()
            with self.assertRaises(ValueError, msg=kind): calculate(q, ledger, now)

    def test_ineligible_window_time_card_does_not_block_other_candidates(self):
        q, ledger, now = make_fixture()
        ledger['cohorts'][0]['signals'][0].update(rate24=None, rate_status='precision_unknown')
        observation = next(r for r in q['observations']['rows'] if r['observation_id'] == 701)
        observation.update(observed_at=None, observed_at_precision='unknown')
        data = calculate(q, ledger, now)
        self.assertTrue(all(r['pool_ids'] == [1002, 1003, 1004, 1005, 1006] for r in rows(data, 'windows')))

    def test_macro_results_weight_decision_days_not_candidate_count(self):
        q, ledger, now = make_fixture((0, 8))
        for index in range(1, 7):
            ledger['cohorts'][0]['evaluations'][str(1000 + index)]['outcome'] = True
        for signal in ledger['cohorts'][1]['signals'][2:]:
            signal['avg_recent'] = None
        for value in ledger['cohorts'][1]['evaluations'].values():
            value['outcome'] = False
        rule = next(r for r in rows(calculate(q, ledger, now), 'metrics') if r['strategy'] == 'rule')
        self.assertEqual(rule['known_hit_rate'], 5 / 7)
        self.assertEqual(rule['macro_full_precision'], .5)

    def test_future_or_incomplete_outcome_provenance_is_rejected(self):
        for kind in ('available', 'runs', 'observations', 'early_close', 'future_import'):
            q, ledger, now = make_fixture(); value = ledger['cohorts'][0]['evaluations']['1001']
            if kind == 'available': value['outcome_available_at'] = (now + timedelta(days=1)).isoformat()
            elif kind == 'runs': value['future_run_ids'] = []
            elif kind == 'observations': value['future_observation_ids'][-1] = 1601
            elif kind == 'early_close': value['outcome_available_at'] = (START_DAY + timedelta(days=7)).isoformat()
            else: next(r for r in q['runs']['rows'] if r['run_id'] == 'r7')['imported_at'] = (now + timedelta(days=1)).isoformat()
            with self.assertRaises(ValueError, msg=kind): calculate(q, ledger, now)

    def test_mixed_protocols_and_duplicate_source_ids_fail_closed(self):
        for kind in ('protocol', 'run', 'observation', 'daily', 'shop'):
            q, ledger, now = make_fixture()
            if kind == 'protocol': ledger['cohorts'][0]['protocol']['min_rate_ratio'] = 9
            elif kind == 'run': q['runs']['rows'].append(deepcopy(q['runs']['rows'][0]))
            elif kind == 'observation': q['observations']['rows'].append(deepcopy(q['observations']['rows'][0]))
            elif kind == 'daily': q['warehouse_days']['rows'].append(deepcopy(q['warehouse_days']['rows'][0]))
            else: q['warehouse_days']['rows'][0]['shop_id'] = 'B'
            with self.assertRaises(ValueError, msg=kind): calculate(q, ledger, now)


class DecisionAvailabilityTests(unittest.TestCase):
    def test_due_day_waits_and_only_next_day_closes_with_availability(self):
        q = decision_fixture(); state = update_ledger(fresh(), q, now=START)
        waiting = update_ledger(state, q, now=START + timedelta(days=7, hours=11))
        self.assertEqual(waiting['cohorts'][0]['evaluations']['1']['evaluation_state'], 'waiting')
        done = update_ledger(waiting, q, now=START + timedelta(days=8))
        outcome = done['cohorts'][0]['evaluations']['1']
        self.assertTrue(outcome['outcome'])
        self.assertEqual(outcome['outcome_available_at'], (START + timedelta(days=8)).isoformat())
        self.assertEqual(done, update_ledger(done, q, now=START + timedelta(days=9)))

    def test_anchor_missing_or_future_import_cannot_freeze(self):
        for stamp in (None, (START + timedelta(minutes=1)).isoformat()):
            q = decision_fixture(); q['runs']['rows'][0]['imported_at'] = stamp
            self.assertEqual(update_ledger(fresh(), q, now=START)['cohorts'], [])

    def test_future_import_or_missing_import_does_not_close_outcome(self):
        for stamp in (None, (START + timedelta(days=10)).isoformat()):
            q = decision_fixture(); state = update_ledger(fresh(), q, now=START)
            q['runs']['rows'][-1]['imported_at'] = stamp
            done = update_ledger(state, q, now=START + timedelta(days=8))
            self.assertIsNone(done['cohorts'][0]['evaluations']['1']['outcome'])

    def test_old_evaluated_result_stays_unchanged_without_backfilled_time(self):
        q = decision_fixture(); state = update_ledger(fresh(), q, now=START)
        old = {'evaluation_state': 'evaluated', 'outcome': True, 'basis': 'confirmed_goods_id'}
        state['cohorts'][0]['evaluations']['1'] = deepcopy(old)
        done = update_ledger(state, q, now=START + timedelta(days=8))
        self.assertEqual(done['cohorts'][0]['evaluations']['1'], old)


if __name__ == '__main__': unittest.main()
