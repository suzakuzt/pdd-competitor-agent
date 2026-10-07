from copy import deepcopy
import unittest

from pdd_monitor.daily_warehouse import build_daily_warehouse, build_warehouse_queries
from pdd_monitor.warehouse_discovery import build_discovery_evidence
from pdd_monitor.warehouse_focus import CATEGORIES, build_warehouse_focus
from tests.test_daily_warehouse import card, run


def row(number, source, name, goods=None, **extra):
    fields = {'image_url': 'https://images.example/' + name + '.jpg', 'title': name, **extra}
    return card(number, source, goods=goods, **fields)


class WarehouseDiscoveryTests(unittest.TestCase):
    def data(self, runs, rows):
        before = deepcopy((runs, rows))
        result = build_discovery_evidence(runs, rows)
        self.assertEqual((runs, rows), before)
        return result

    def test_initial_catalogue_is_not_new_even_after_same_day_recollection(self):
        a, b = run('first', 4), run('later', 4, 2)
        rows = [row(1, a, 'old'), row(2, b, 'old')]
        data = self.data([b, a], rows)
        self.assertEqual(data[1]['discovery_status'], 'initial_catalogue')
        self.assertEqual(data[2]['discovery_status'], 'initial_catalogue')
        self.assertEqual(data[2]['first_observation_id'], 1)
        summary = build_warehouse_focus([a, b], rows)['warehouse_focus_summary'][0]
        self.assertEqual(summary['initial_catalogue_count'], 1)
        self.assertEqual(summary['first_observed_count'], 0)

    def test_complete_baseline_new_original_card_and_same_day_anchor(self):
        a, b, c = run('old', 4), run('first_new', 5), run('recollected', 5, 2)
        rows = [row(1, a, 'old'), row(2, b, 'new'), row(3, c, 'new')]
        data = self.data([c, a, b], rows)
        for oid in (2, 3):
            self.assertEqual(data[oid]['discovery_status'], 'first_observed_after_complete')
            self.assertEqual(data[oid]['first_observation_id'], 2)
            self.assertEqual(data[oid]['first_observed_date'], '2026-10-05')
            self.assertEqual(data[oid]['discovery_baseline_run_id'], 'old')
            self.assertEqual(data[oid]['discovery_basis'], 'exact_title_original_image')
            self.assertIsNone(data[oid]['listing_date'])
        records = build_daily_warehouse([a, b, c], rows)['warehouse_records']
        self.assertEqual([r['observation_id'] for r in records], [1, 3])
        self.assertNotEqual(records[0]['track_id'], records[1]['track_id'])

    def test_partial_previous_coverage_is_only_candidate_and_initial_is_distinct(self):
        a, b = run('partial', 4, complete=False), run('complete', 5)
        data = self.data([a, b], [row(1, a, 'old'), row(2, b, 'new')])
        self.assertEqual(data[1]['discovery_status'], 'initial_catalogue')
        self.assertEqual(data[2]['discovery_status'], 'first_observed_candidate')
        self.assertEqual(data[2]['discovery_reason'], 'no_prior_complete_card_list')
        self.assertIsNone(data[2]['discovery_baseline_run_id'])

    def test_complete_with_missing_card_keys_does_not_prove_absence(self):
        a, b = run('old', 4), run('new', 5)
        rows = [row(1, a, 'unidentified', image_url=None), row(2, b, 'new')]
        data = self.data([a, b], rows)
        self.assertEqual(data[1]['discovery_status'], 'identity_unresolved')
        self.assertEqual(data[2]['discovery_status'], 'first_observed_candidate')
        self.assertEqual(data[2]['discovery_reason'], 'prior_complete_key_coverage_insufficient')

    def test_baseline_must_cover_current_matching_key_not_only_other_identity_type(self):
        a, b = run('old', 4), run('new', 5)
        rows = [row(1, a, 'old-id-only', goods='123', image_url=None), row(2, b, 'new-without-id')]
        data = self.data([a, b], rows)
        self.assertEqual(data[2]['discovery_status'], 'first_observed_candidate')
        self.assertEqual(data[2]['discovery_reason'], 'prior_complete_key_coverage_insufficient')
        rows = [row(1, a, 'old-no-id'), row(2, b, 'new-id-only', goods='456', image_url=None)]
        self.assertEqual(self.data([a, b], rows)[2]['discovery_status'], 'first_observed_candidate')

    def test_archive_date_uses_beijing_run_end_and_not_invented_listing_date(self):
        a, b = run('old', 4, 14), run('new', 4, 16)
        data = self.data([a, b], [row(1, a, 'old'), row(2, b, 'new')])
        self.assertEqual(data[2]['first_observed_date'], '2026-10-05')
        self.assertEqual(data[2]['discovery_baseline_date'], '2026-10-04')
        self.assertIsNone(data[2]['listing_date'])

    def test_title_or_original_image_change_never_confirmed_from_full_baseline(self):
        a, b = run('old', 4), run('new', 5)
        old = row(1, a, 'old', asset_sha256='a' * 64, image_content_status='verified_local')
        for extra in ({'title': 'changed', 'image_url': old['image_url']},
                      {'title': 'old', 'image_url': old['image_url'] + '?quality=90'}):
            with self.subTest(extra=extra):
                current = row(2, b, 'unused', asset_sha256='a' * 64, image_content_status='verified_local', **extra)
                data = self.data([a, b], [old, current])
                self.assertEqual(data[2]['discovery_status'], 'first_observed_candidate')
                self.assertEqual(data[2]['discovery_reason'], 'title_or_image_changed')
                self.assertEqual(data[2]['first_observation_id'], 2)

    def test_duplicate_original_cards_are_not_merged_or_counted_as_new(self):
        a, b = run('old', 4), run('new', 5)
        rows = [row(1, a, 'duplicate'), row(2, a, 'duplicate'), row(3, b, 'duplicate'),
                row(4, b, 'duplicate'), row(5, b, 'brand-new'), row(6, b, 'brand-new')]
        data = self.data([a, b], rows)
        for oid in (3, 4):
            self.assertEqual(data[oid]['discovery_status'], 'existing')
            self.assertIsNone(data[oid]['first_observation_id'])
        for oid in (5, 6):
            self.assertEqual(data[oid]['discovery_status'], 'identity_unresolved')
        focus = build_warehouse_focus([a, b], rows)
        self.assertEqual(len(focus['warehouse_focus_items']), 4)
        self.assertEqual(len({r['track_id'] for r in focus['warehouse_focus_items']}), 4)
        self.assertEqual(focus['warehouse_focus_summary'][0]['first_observed_count'], 0)

    def test_conflicting_known_ids_do_not_fall_back_to_exact_combination(self):
        a, b = run('old', 4), run('new', 5)
        for current in (row(2, b, 'same', goods='456'),
                        row(2, b, 'same', goods='123', identity_status='conflict_url')):
            data = self.data([a, b], [row(1, a, 'same', goods='123'), current])
            self.assertEqual(data[2]['discovery_status'], 'identity_unresolved')
            self.assertIsNone(data[2]['first_observation_id'])
        rows = [row(1, a, 'same', goods='123'), row(2, a, 'same', goods='456'), row(3, b, 'same')]
        self.assertEqual(self.data([a, b], rows)[3]['discovery_status'], 'identity_unresolved')

    def test_reliable_id_retains_first_observation_across_title_image_change_and_gap(self):
        a, b = run('old', 4), run('new', 7)
        data = self.data([a, b], [row(1, a, 'old', goods='123'), row(2, b, 'different', goods='123')])
        self.assertEqual(data[2]['discovery_status'], 'existing')
        self.assertEqual(data[2]['first_observation_id'], 1)
        self.assertEqual(data[2]['first_observed_date'], '2026-10-04')

    def test_weak_clue_does_not_inherit_old_different_image_through_id(self):
        a, b, c = run('old', 4), run('changed', 5), run('noid', 6)
        rows = [row(1, a, 'old', goods='123'), row(2, b, 'changed', goods='123'), row(3, c, 'changed')]
        data = self.data([a, b, c], rows)
        self.assertEqual(data[3]['discovery_status'], 'existing')
        self.assertEqual(data[3]['first_observation_id'], 2)
        self.assertEqual(data[3]['first_observed_date'], '2026-10-05')

    def test_partial_first_record_is_not_rediscovered_next_day(self):
        a, b, c = run('old', 4), run('partial', 5, complete=False), run('current', 6)
        rows = [row(1, a, 'old'), row(2, b, 'new'), row(3, c, 'new')]
        data = self.data([a, b, c], rows)
        self.assertEqual(data[2]['discovery_status'], 'first_observed_after_complete')
        self.assertEqual(data[3]['discovery_status'], 'existing')
        self.assertEqual(data[3]['first_observation_id'], 2)

    def test_overlapping_runs_cannot_claim_ordered_discovery(self):
        a, b, c = run('old', 4), run('one', 5), run('two', 5)
        rows = [row(1, a, 'old'), row(2, b, 'new'), row(3, c, 'new')]
        data = self.data([a, b, c], rows)
        for oid in (2, 3):
            self.assertEqual(data[oid]['discovery_status'], 'identity_unresolved')
            self.assertEqual(data[oid]['discovery_reason'], 'overlapping_observation_windows')

    def test_scope_categories_and_counts_use_selected_shop_date_run(self):
        a, b, extra, other = run('old', 4), run('current', 5), run('partial', 5, 2, False), run('other_shop', 5, shop='B')
        rows = [row(1, a, 'old'), row(2, b, 'old', sales=11), row(3, b, 'new', sales=1),
                row(4, b, 'zero', sales=0), row(5, b, 'other', sales_label='总售'),
                row(6, b, 'unknown', sales_precision='approximate'), row(7, extra, 'not-selected'),
                row(8, other, 'new', sales=1)]
        runs = [other, extra, b, a]
        daily, focus = build_daily_warehouse(runs, rows), build_warehouse_focus(runs, rows)
        summary = next(d for d in daily['warehouse_days'] if d['shop_id'] == 'A' and d['date'] == '2026-10-05')
        self.assertEqual(summary['run_id'], 'current')
        self.assertEqual(summary['first_observed_count'], 4)
        self.assertEqual([summary[k + '_count'] for k in CATEGORIES], [1, 1, 1, 1, 1])
        self.assertEqual(sum(summary[k + '_count'] for k in CATEGORIES), summary['card_count'])
        self.assertEqual(next(d for d in daily['warehouse_days'] if d['shop_id'] == 'B')['first_observed_count'], 0)
        items = {r['observation_id']: r for r in daily['warehouse_records']}
        self.assertNotIn(7, items)
        for item in focus['warehouse_focus_items']:
            for key in ('discovery_status', 'first_observed_date', 'first_observation_id', 'category'):
                self.assertEqual(item[key], items[item['observation_id']][key])

    def test_duplicate_observation_identity_fails_closed(self):
        a = run('one', 4)
        with self.assertRaisesRegex(ValueError, 'Duplicate observation'):
            build_discovery_evidence([a], [row(1, a, 'one'), row(1, a, 'two')])

    def test_queries_reuse_existing_names_and_include_derivation_lineage(self):
        a = run('one', 4)
        queries = {'runs': {'rows': [a], 'source': {'sql': 'SYNTHETIC RUNS'}},
                   'observations': {'rows': [row(1, a, 'one')], 'source': {'sql': 'SYNTHETIC OBSERVATIONS', 'files': []}}}
        result = build_warehouse_queries(queries)
        self.assertEqual(set(result), {'warehouse_days', 'warehouse_records', 'warehouse_watch_records',
            'warehouse_tracks', 'warehouse_points', 'warehouse_focus_summary', 'warehouse_focus_items', 'warehouse_display_history'})
        for name in ('warehouse_days', 'warehouse_records', 'warehouse_focus_summary', 'warehouse_focus_items'):
            self.assertTrue(any(p.endswith('warehouse_discovery.py') for p in result[name]['source']['files']))
            self.assertTrue(any('不是实际上架' in note for note in result[name]['source']['caveats']))


if __name__ == '__main__':
    unittest.main()
