"""Synthetic-only fixed baseline and novelty evidence tests."""
from copy import deepcopy
from datetime import datetime, timedelta
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from pdd_monitor.new_arrivals import make_tracking_config, load_tracking_config, build_new_arrivals

START = "2026-10-04T08:00:00Z"
SHOP = "SYNTHETIC_SHOP"


def run(key, at, *, complete=True, minutes=10, shop=SHOP):
    start = datetime.fromisoformat(at.replace("Z", "+00:00"))
    end = start + timedelta(minutes=minutes)
    return {"run_id": key, "shop_id": shop, "observed_from": start.isoformat(), "observed_to": end.isoformat(),
            "observed_from_epoch": start.timestamp(), "observed_to_epoch": end.timestamp(),
            "status": "complete" if complete else "partial", "end_boundary_observed": complete,
            "snapshot_sha256": hashlib.sha256(key.encode()).hexdigest()}


def card(key, source, *, goods=None, title="SYNTHETIC TITLE", image="https://example.invalid/old.png", **extra):
    return {"observation_id": key, "run_id": source["run_id"], "view_order": key, "title": title, "image_url": image,
            "goods_id": goods, "identity_status": "unique_goods_id" if goods else "unknown_no_goods_id",
            "sales_raw": None, "sales_value": None, "sales_precision": "missing",
            "observed_at": source["observed_from"], "observed_at_epoch": source["observed_from_epoch"],
            "observed_at_precision": "card_read", **extra}


class NewArrivalsEvidenceTests(unittest.TestCase):
    def test_partial_origin_then_first_complete_scan_only_creates_qualified_first_seen_clues(self):
        base = run("partial-origin", "2026-10-03T08:00:00Z", complete=False)
        partial = run("expanded-partial", START, complete=False)
        full = run("first-complete", "2026-10-04T12:00:00Z")
        old = card(101, base, title="OLD-A")
        config = make_tracking_config([base], [old], shop_id=SHOP, started_at=START)
        frozen = deepcopy(config)
        rows = [old, card(102, partial, title="OLD-A"), card(103, partial, title="PREVIOUSLY-UNSEEN-B"),
                card(104, full, title="OLD-A"), card(105, full, title="PREVIOUSLY-UNSEEN-B"),
                card(106, full, title="PREVIOUSLY-UNSEEN-C"), card(107, full, title="PREVIOUSLY-UNSEEN-C")]
        result = build_new_arrivals([base, partial, full], rows, config)
        self.assertEqual(config, frozen)
        summary = result['new_arrival_summary'][0]
        self.assertEqual(summary['baseline_coverage_status'], 'partial_only')
        self.assertFalse(summary['baseline_has_complete_run'])
        self.assertEqual(summary['baseline_observation_count'], 1)
        self.assertEqual(summary['baseline_run_coverage'][0]['status'], 'partial')
        self.assertEqual(summary['possible_coverage_expansion_item_count'], 3)
        self.assertEqual({i['observation_id'] for i in result['new_arrival_items']}, {103,106,107})
        for item in result['new_arrival_items']:
            self.assertEqual(item['newness'], 'unknown'); self.assertIsNone(item['possible_since'])
            self.assertTrue(item['coverage_expansion_possible']); self.assertIn('可能为旧存量', item['discovery_label'])
            self.assertTrue(any('未覆盖全店' in value for value in item['verification_gaps']))

    def test_v5_first_ready_read_drives_first_seen_without_rewriting_latest_observation(self):
        row = card(4, self.new, goods="999", title="NEW", observed_at="2026-10-04T08:09:00Z",
                   observed_at_epoch=datetime.fromisoformat("2026-10-04T08:09:00+00:00").timestamp(),
                   observed_at_precision='batch_read',
                   original={'firstObservedAt':'2026-10-04T08:01:00Z','firstObservedAtPrecision':'batch_read',
                             'firstRenderedAt':'2026-10-04T08:00:30Z'})
        before=deepcopy(row)
        item=self.build([row])['new_arrival_items'][0]
        self.assertEqual(row,before); self.assertEqual(item['first_observed_at'],'2026-10-04T08:01:00Z')
        self.assertEqual(item['possible_until'],'2026-10-04T08:01:00Z')
        self.assertEqual(item['first_time_evidence_source'],'original_first_observed_at')
        self.assertEqual(item['first_observed_at_precision'],'batch_read')
        self.assertFalse(item['first_recorded_time_invalid'])

    def test_v5_invalid_first_time_does_not_override_known_latest_read(self):
        for first, precision in [('2026-10-04T07:59:00Z','batch_read'),('2026-10-04T08:09:01Z','batch_read'),
                                 ('2026-10-04T08:11:00Z','batch_read'),('2026-10-04T08:01:00','batch_read'),
                                 ('bad','batch_read'),('2026-10-04T08:01:00Z','legacy_unspecified')]:
            with self.subTest(first=first,precision=precision):
                row=card(4,self.new,goods='999',title='NEW',observed_at='2026-10-04T08:09:00Z',
                         observed_at_epoch=datetime.fromisoformat('2026-10-04T08:09:00+00:00').timestamp(),
                         original={'firstObservedAt':first,'firstObservedAtPrecision':precision})
                item=self.build([row])['new_arrival_items'][0]
                self.assertEqual(item['first_observed_at'],'2026-10-04T08:09:00Z')
                self.assertEqual(item['first_time_evidence_source'],'observed_at')
                self.assertTrue(item['first_recorded_time_invalid'])

    def test_v5_row_json_first_time_and_legacy_fallback(self):
        row=card(4,self.new,goods='999',title='NEW',observed_at='2026-10-04T08:09:00Z',
                 observed_at_epoch=datetime.fromisoformat('2026-10-04T08:09:00+00:00').timestamp(),
                 row_json=json.dumps({'firstObservedAt':'2026-10-04T16:01:00+08:00','firstObservedAtPrecision':'card_read'}))
        item=self.build([row])['new_arrival_items'][0]
        self.assertEqual(item['first_observed_at'],'2026-10-04T16:01:00+08:00')
        del row['row_json']
        legacy=self.build([row])['new_arrival_items'][0]
        self.assertEqual(legacy['first_observed_at'],row['observed_at'])
        self.assertFalse(legacy['first_recorded_time_invalid'])

    def setUp(self):
        self.old = run("base", "2026-10-03T08:00:00Z")
        self.partial = run("base_partial", "2026-10-03T12:00:00Z", complete=False)
        self.new = run("new", START)
        self.later = run("later", "2026-10-04T12:00:00Z")
        self.baseline_rows = [card(1, self.old), card(2, self.old, goods="111"), card(3, self.partial)]
        self.config = make_tracking_config([self.old, self.partial], self.baseline_rows, shop_id=SHOP, started_at=START)

    def build(self, new_rows, *, extra_runs=(), extra_rows=(), config=None):
        return build_new_arrivals([self.old, self.partial, self.new, *extra_runs], self.baseline_rows + list(extra_rows) + list(new_rows), config or self.config)

    def test_frozen_baseline_contains_all_complete_partial_and_missing_sales_cards(self):
        result = build_new_arrivals([self.partial, self.old], self.baseline_rows, self.config)
        self.assertEqual(result["new_arrival_items"], [])
        summary = result["new_arrival_summary"][0]
        self.assertEqual(summary["baseline_observation_count"], 3)
        self.assertEqual(summary["state"], "no_post_baseline_run")
        self.assertEqual(summary["post_baseline_run_count"], 0)
        self.assertEqual(set(self.config["baseline_run_ids"]), {"base", "base_partial"})

    def test_load_is_readonly_missing_invalid_never_initialize(self):
        with tempfile.TemporaryDirectory(prefix="SYNTHETIC_ARRIVAL_CONFIG_") as folder:
            path = Path(folder) / "tracking_config.json"
            with self.assertRaises(FileNotFoundError):
                load_tracking_config(path)
            self.assertFalse(path.exists())
            path.write_text(json.dumps(self.config), encoding="utf-8")
            before = path.read_bytes()
            self.assertEqual(load_tracking_config(path), self.config)
            self.assertEqual(path.read_bytes(), before)
            changed = deepcopy(self.config)
            changed["started_at"] = "2026-10-05T08:00:00Z"
            path.write_text(json.dumps(changed), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "fingerprint"):
                load_tracking_config(path)

    def test_baseline_missing_sha_time_or_card_count_drift_fails_explicitly(self):
        for field, value in (("snapshot_sha256", "0" * 64), ("observed_to", "2026-10-03T08:11:00+00:00"), ("shop_id", "other")):
            runs = [{**self.old, field: value}, self.partial]
            with self.subTest(field=field), self.assertRaises(ValueError):
                build_new_arrivals(runs, self.baseline_rows, self.config)
        with self.assertRaises(ValueError):
            build_new_arrivals([self.old, self.partial], self.baseline_rows[:-1], self.config)
        with self.assertRaises(ValueError):
            build_new_arrivals([self.old], self.baseline_rows[:2], self.config)

    def test_known_reliable_identity_is_existing_even_if_title_image_changed(self):
        result = self.build([card(4, self.new, goods="111", title="CHANGED TITLE", image="https://example.invalid/new.png")])
        self.assertEqual(result["new_arrival_items"], [])
        self.assertEqual(result["new_arrival_summary"][0]["post_observation_classification_counts"]["existing_reliable_identity"], 1)

    def test_unseen_reliable_id_is_candidate_not_listing_with_unknown_lower(self):
        item = self.build([card(4, self.new, goods="999", title="NEW", image="https://example.invalid/new.png")])["new_arrival_items"][0]
        self.assertEqual(item["discovery_kind"], "first_observed_id_candidate")
        self.assertIsNone(item["possible_since"])
        self.assertEqual(item["possible_since_reason"], "no_prior_complete_identity_coverage")
        self.assertEqual(item["newness"], "unknown")
        self.assertEqual(item["possible_until"], self.new["observed_from"])

    def test_lower_bound_requires_prior_full_identity_absence_and_is_sampling_reference(self):
        rows = [card(1, self.old, goods="111")]
        config = make_tracking_config([self.old], rows, shop_id=SHOP, started_at=START)
        result = build_new_arrivals([self.old, self.new], rows + [card(4, self.new, goods="999")], config)
        item = result["new_arrival_items"][0]
        self.assertEqual(item["possible_since"], self.old["observed_to"])
        self.assertEqual(item["last_complete_absence_run_id"], "base")
        self.assertEqual(item["previous_absence_observation_window"], {"from": self.old["observed_from"], "to": self.old["observed_to"]})
        self.assertIn("采样参考，非上架时间", item["time_range_label"])

    def test_legacy_window_unknown_never_claim_exact_first_read(self):
        for precision in ("legacy_unspecified", "unknown", "run_window"):
            with self.subTest(precision=precision):
                item = self.build([card(4, self.new, goods="999", title="NEW", observed_at_precision=precision)])["new_arrival_items"][0]
                self.assertEqual(item["first_seen_time_basis"], "observation_window")
                self.assertEqual(item["possible_until"], self.new["observed_to"])
        for precision in ("card_read", "batch_read"):
            item = self.build([card(4, self.new, goods="999", title="NEW", observed_at_precision=precision)])["new_arrival_items"][0]
            self.assertEqual(item["first_seen_time_basis"], "recorded_read_time")
            self.assertEqual(item["possible_until"], self.new["observed_from"])

    def test_missing_or_invalid_point_uses_real_window_without_fabricated_time(self):
        for value in (None, "bad", "2026-10-05T12:00:00Z"):
            item = self.build([card(4, self.new, goods="999", title="NEW", observed_at=value, observed_at_epoch=None)])["new_arrival_items"][0]
            self.assertIsNone(item["first_observed_at"])
            self.assertEqual(item["possible_until"], self.new["observed_to"])
            self.assertEqual(item["first_seen_time_basis"], "observation_window")

    def test_same_run_duplicate_weak_cards_stay_independent_and_later_do_not_repeat_first(self):
        first = [card(4, self.new, title="NEW"), card(5, self.new, title="NEW")]
        later = [card(6, self.later, title="NEW"), card(7, self.later, title="NEW")]
        result = self.build(first, extra_runs=[self.later], extra_rows=later)
        items = result["new_arrival_items"]
        self.assertEqual([item["observation_id"] for item in items], [4, 5])
        self.assertEqual(len({item["arrival_item_id"] for item in items}), 2)
        self.assertTrue(all(item["latest_observation_ids"] == [6, 7] for item in items))
        self.assertTrue(all(item["goods_id"] is None and item["possible_since"] is None for item in items))
        self.assertTrue(all("sales_total" not in item for item in items))

    def test_reliable_first_anchor_persists_with_latest_individual_sales_reference(self):
        first = card(4, self.new, goods="999", title="NEW", sales_raw="已拼1件", sales_value=1)
        later = card(5, self.later, goods="999", title="NEW", sales_raw="已拼12件", sales_value=12)
        before = self.build([first])["new_arrival_items"][0]
        after = self.build([first], extra_runs=[self.later], extra_rows=[later])["new_arrival_items"][0]
        self.assertEqual(after["arrival_item_id"], before["arrival_item_id"])
        self.assertEqual(after["first_observed_at"], before["first_observed_at"])
        self.assertEqual(after["latest_observation_ids"], [5])
        self.assertEqual(after["subsequent_references"], [{"run_id": "later", "observation_id": 5, "match_basis": "same_unique_goods_id", "temporal_order": "later_run"}])

    def test_duplicate_or_conflicting_ids_never_get_reliable_identity_or_lower_bound(self):
        result = self.build([card(4, self.new, goods="999", title="NEW"), card(5, self.new, goods="999", title="NEW")])
        self.assertEqual(len(result["new_arrival_items"]), 2)
        self.assertTrue(all(item["discovery_kind"] == "new_card_clue" and item["possible_since"] is None for item in result["new_arrival_items"]))
        result = self.build([card(4, self.new, goods="999", title="NEW", identity_status="conflict_goods_url")])
        self.assertEqual(result["new_arrival_items"][0]["discovery_kind"], "new_card_clue")

    def test_single_side_title_or_image_changes_have_historical_warning_references(self):
        for change, code in (({"title": "CHANGED TITLE"}, "possible_title_change"), ({"image": "https://example.invalid/changed.png"}, "possible_image_change")):
            item = self.build([card(4, self.new, **change)])["new_arrival_items"][0]
            self.assertIn(code, item["possible_change_reasons"])
            self.assertTrue(item["historical_references"])
            self.assertIsNone(item["possible_since"])

    def test_first_reliable_id_with_old_weak_combo_marks_possible_identity_enrichment(self):
        item = self.build([card(4, self.new, goods="999")])["new_arrival_items"][0]
        self.assertEqual(item["discovery_kind"], "first_observed_id_candidate")
        self.assertIn("possible_old_card_identity_enrichment", item["possible_change_reasons"])
        self.assertEqual(item["possible_since_reason"], "possible_old_card_identity_enrichment")
        self.assertIsNone(item["possible_since"])

    def test_missing_title_or_original_image_is_insufficient_weak_signal_not_new(self):
        for data in ({"title": ""}, {"image": None}):
            result = self.build([card(4, self.new, **data)])
            self.assertEqual(result["new_arrival_items"], [])
            self.assertEqual(result["new_arrival_summary"][0]["post_observation_classification_counts"]["insufficient_identity_signal"], 1)

    def test_late_imported_old_history_can_retract_candidate_without_resetting_config(self):
        first = card(4, self.new, title="NEW")
        before = self.build([first])
        late_run = run("late_old", "2026-10-02T08:00:00Z")
        late_card = card(5, late_run, title="NEW")
        after = self.build([first], extra_runs=[late_run], extra_rows=[late_card])
        self.assertEqual(len(before["new_arrival_items"]), 1)
        self.assertEqual(after["new_arrival_items"], [])
        self.assertEqual(after["new_arrival_summary"][0]["tracking_id"], self.config["tracking_id"])
        self.assertIn("late_old", after["new_arrival_summary"][0]["excluded_pre_tracking_run_ids"])

    def test_straddling_tracking_start_is_old_context_not_a_post_start_run(self):
        crossing = run("straddle", "2026-10-04T07:59:00Z", minutes=20)
        result = self.build([card(4, self.new, title="NEW")], extra_runs=[crossing], extra_rows=[card(5, crossing, title="NEW")])
        self.assertEqual(result["new_arrival_items"], [])
        self.assertEqual(result["new_arrival_summary"][0]["post_baseline_run_count"], 1)
        self.assertIn("straddle", result["new_arrival_summary"][0]["excluded_pre_tracking_run_ids"])

    def test_exact_tracking_boundary_offset_equivalence_and_other_shop_isolation(self):
        equivalent = run("new", "2026-10-04T16:00:00+08:00")
        other = run("other", "2026-10-03T09:00:00Z", shop="OTHER_SHOP")
        result = build_new_arrivals([self.old, self.partial, equivalent, other], self.baseline_rows + [card(4, equivalent, goods="999", title="NEW"), card(5, other, goods="999", title="NEW")], self.config)
        self.assertEqual(len(result["new_arrival_items"]), 1)
        self.assertEqual(result["new_arrival_summary"][0]["post_baseline_run_count"], 1)
        self.assertTrue(all(row["run_id"] != "other" for row in result["new_arrival_items"][0]["historical_references"]))
        with self.assertRaisesRegex(ValueError, "offset"):
            make_tracking_config([self.old], self.baseline_rows[:2], shop_id=SHOP, started_at="2026-10-04T08:00:00")

    def test_input_immutability_determinism_and_no_implicit_config_reset(self):
        runs, rows = [self.old, self.partial, self.new], self.baseline_rows + [card(4, self.new, title="NEW")]
        before = deepcopy((runs, rows, self.config))
        first = build_new_arrivals(runs, rows, self.config)
        self.assertEqual(first, build_new_arrivals(runs[::-1], rows[::-1], self.config))
        self.assertEqual((runs, rows, self.config), before)
        json.dumps(first, allow_nan=False)

    def test_empty_complete_run_cannot_supply_full_identity_absence(self):
        empty = run("empty", "2026-10-03T18:00:00Z")
        item = self.build([card(4, self.new, goods="999", title="NEW")], extra_runs=[empty])["new_arrival_items"][0]
        self.assertIsNone(item["possible_since"])
        self.assertEqual(item["possible_since_reason"], "no_prior_complete_identity_coverage")

    def test_overlapping_windows_do_not_reverse_first_and_latest_precise_times(self):
        early = run("overlapA", "2026-10-04T10:00:00Z", minutes=30)
        later = run("overlapB", "2026-10-04T10:05:00Z", minutes=55)
        a = card(4, early, goods="999", title="NEW", observed_at="2026-10-04T10:29:00Z", observed_at_epoch=None)
        b = card(5, later, goods="999", title="NEW", observed_at="2026-10-04T10:06:00Z", observed_at_epoch=None)
        result = self.build([], extra_runs=[early, later], extra_rows=[a, b])
        item = result["new_arrival_items"][0]
        self.assertEqual(item["first_time_order_status"], "overlapping_runs_unknown")
        self.assertIsNone(item["first_observed_at"])
        self.assertIsNone(item["possible_since"])
        self.assertEqual(item["first_observation_window"], {"from": early["observed_from"], "to": later["observed_to"]})
        self.assertEqual(item["latest_time_order_status"], "overlapping_runs_unknown")
        self.assertEqual(item["latest_observation_ids"], [4, 5])
        self.assertIsNone(item["latest_run_id"])


if __name__ == "__main__":
    unittest.main()
