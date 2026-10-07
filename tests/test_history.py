"""Synthetic, in-memory evidence tests; never writes production observations."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import unittest

from pdd_monitor.history import build_history


def run(key, at, *, complete=True, shop="synthetic", minutes=10):
    start = datetime.fromisoformat(at)
    end = start + timedelta(minutes=minutes)
    return {"run_id": key, "shop_id": shop, "observed_from": start.isoformat(), "observed_to": end.isoformat(),
            "observed_from_epoch": start.timestamp(), "observed_to_epoch": end.timestamp(),
            "status": "complete" if complete else "partial", "end_boundary_observed": complete}


def card(key, source, value=11, goods="123", **overrides):
    result = {"observation_id": key, "run_id": source["run_id"], "view_order": key,
              "goods_id": goods, "identity_status": "unique_goods_id" if goods else "unknown_no_goods_id",
              "title": "SYNTHETIC identical title", "image_url": "https://example.invalid/synthetic.png",
              "sales_raw": f"已拼{value}件" if value is not None else None,
              "sales_value": value, "sales_label": "已拼", "sales_unit": "件",
              "sales_precision": "exact_display" if value is not None else "missing",
              "observed_at": source["observed_from"], "observed_at_epoch": source["observed_from_epoch"],
              "observed_at_precision": "card_read"}
    result.update(overrides)
    return result


def comparison(result, target, mode="previous"):
    summary = next(row for row in result["comparison_summaries"] if row["target_run_id"] == target and row["mode"] == mode)
    return summary, [row for row in result["comparison_items"] if row["comparison_id"] == summary["comparison_id"]]


class HistoryEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.old = run("old", "2026-10-03T08:00:00+08:00")
        self.new = run("new", "2026-10-04T08:00:00+08:00")

    def build(self, old_rows, new_rows, old=None, new=None):
        return build_history([old or self.old, new or self.new], old_rows + new_rows)

    def test_exact_delta_crossing_retains_old_raw_and_label(self):
        result = self.build([card(1, self.old, 10)], [card(2, self.new, 11)])
        summary, items = comparison(result, "new")
        self.assertEqual(summary["comparable_count"], 1)
        self.assertEqual(summary["crossed_gt10_count"], 1)
        self.assertEqual(summary["increased_count"], 0)
        self.assertEqual(summary["positive_delta_count"], 1)
        self.assertEqual(items[0]["display_delta"], 1)
        self.assertEqual(items[0]["old_sales_raw"], "已拼10件")
        self.assertEqual(items[0]["elapsed_hours"], 24)
        self.assertEqual(items[0]["match_basis"], "same_unique_goods_id")

    def test_window_precision_and_missing_card_time_give_bounds_not_false_hours(self):
        old = card(1, self.old, 10, observed_at=None, observed_at_epoch=None, observed_at_precision="unknown")
        new = card(2, self.new, 12, observed_at_precision="run_window")
        _, items = comparison(self.build([old], [new]), "new")
        item = items[0]
        self.assertEqual(item["display_delta"], 2)
        self.assertIsNone(item["elapsed_hours"])
        self.assertAlmostEqual(item["elapsed_hours_min"], 24 - 1 / 6)
        self.assertAlmostEqual(item["elapsed_hours_max"], 24 + 1 / 6)
        self.assertIsNone(item["old_observed_at"])
        self.assertEqual(item["old_observed_at_precision"], "unknown")

    def test_legacy_timestamp_has_explicit_unspecified_precision(self):
        old, new = card(1, self.old), card(2, self.new)
        old.pop("observed_at_precision")
        new.pop("observed_at_precision")
        _, items = comparison(self.build([old], [new]), "new")
        self.assertEqual(items[0]["elapsed_hours"], 24)
        self.assertEqual(items[0]["old_observed_at_precision"], "legacy_unspecified")

    def test_time_overlap_bad_timestamps_and_outside_window_rejected(self):
        overlapping = run("new", "2026-10-03T08:05:00+08:00")
        _, items = comparison(self.build([card(1, self.old)], [card(2, overlapping)], new=overlapping), "new")
        self.assertEqual(items[0]["reason"], "observation_time_not_ordered")
        for value in ("bad", "2026-10-04T08:01:00", "2026-10-02T08:00:00+08:00"):
            with self.subTest(value=value):
                _, items = comparison(self.build([card(1, self.old)], [card(2, self.new, observed_at=value)]), "new")
                self.assertEqual(items[0]["reason"], "invalid_observation_time")
                self.assertIsNone(items[0]["display_delta"])

    def test_negative_difference_is_preserved_as_anomaly_not_growth(self):
        summary, items = comparison(self.build([card(1, self.old, 20)], [card(2, self.new, 11)]), "new")
        self.assertEqual((items[0]["display_delta"], items[0]["status"]), (-9, "anomaly"))
        self.assertEqual(summary["increased_count"], 0)
        self.assertEqual(summary["negative_delta_count"], 1)
        self.assertIsNone(items[0]["crossed_gt10"])

    def test_field_differences_are_raw_text_only_on_stable_identity_pairs(self):
        old = card(1, self.old, price_raw="券后¥4.50")
        new = card(2, self.new, price_raw="¥4.50", title="SYNTHETIC changed title", image_url="https://example.invalid/changed.png")
        _, items = comparison(self.build([old], [new]), "new")
        item = items[0]
        self.assertTrue(item["price_raw_changed"])
        self.assertTrue(item["title_changed"])
        self.assertTrue(item["image_url_changed"])
        self.assertEqual((item["old_price_raw"], item["new_price_raw"]), ("券后¥4.50", "¥4.50"))
        self.assertNotIn("price_delta", item)
        _, items = comparison(self.build([old], [{**new, "price_raw": None, "image_url": None}]), "new")
        self.assertIsNone(items[0]["price_raw_changed"])
        self.assertIsNone(items[0]["image_url_changed"])
        _, items = comparison(self.build([{**old, "goods_id": None}], [new]), "new")
        self.assertTrue(all(row["price_raw_changed"] is None for row in items))

    def test_null_fuzzy_fraction_boolean_and_labels_units_do_not_mix(self):
        variants = [({"sales_value": None}, "sales_not_exact"), ({"sales_value": 11.5}, "sales_not_exact"),
                    ({"sales_value": True}, "sales_not_exact"), ({"sales_value": float("nan")}, "sales_not_exact"),
                    ({"sales_precision": "non_exact_or_unparsed"}, "sales_not_exact"),
                    ({"sales_label": "总售"}, "sales_label_changed"), ({"sales_unit": "人"}, "sales_unit_changed")]
        for overrides, reason in variants:
            with self.subTest(overrides=overrides):
                _, items = comparison(self.build([card(1, self.old, 10)], [card(2, self.new, **overrides)]), "new")
                self.assertEqual(items[0]["reason"], reason)
                self.assertIsNone(items[0]["display_delta"])
        _, items = comparison(self.build([card(1, self.old, 0)], [card(2, self.new, 0)]), "new")
        self.assertEqual(items[0]["display_delta"], 0)
        self.assertEqual(items[0]["event"], "unchanged")

    def test_duplicate_ids_preserve_every_card_without_choosing_a_match(self):
        old, new = [card(1, self.old), card(2, self.old)], [card(3, self.new, 99), card(4, self.new, 25)]
        summary, items = comparison(self.build(old, new), "new")
        self.assertEqual(summary["item_count"], 4)
        self.assertTrue(all(row["reason"] == "duplicate_goods_id" for row in items))
        self.assertTrue(all(row["display_delta"] is None for row in items))
        self.assertEqual(sorted(row["old_observation_id"] for row in items if row["old_observation_id"]), [1, 2])
        self.assertEqual(sorted(row["new_observation_id"] for row in items if row["new_observation_id"]), [3, 4])

    def test_identity_conflict_preserves_both_sides_without_first_seen(self):
        for side in ("old", "new"):
            old, new = card(1, self.old), card(2, self.new)
            (old if side == "old" else new)["identity_status"] = "conflict_goods_url"
            _, items = comparison(self.build([old], [new]), "new")
            self.assertEqual(len(items), 2)
            self.assertTrue(all(row["reason"] == "identity_conflict" for row in items))
            self.assertFalse(any(row["first_observed_candidate"] for row in items))

    def test_missing_and_invalid_id_are_unknown_not_merged_by_title_image(self):
        for goods in (None, "0", "-1", "bad", 123):
            with self.subTest(goods=goods):
                _, items = comparison(self.build([card(1, self.old, goods=goods)], [card(2, self.new, goods=goods)]), "new")
                self.assertEqual(len(items), 2)
                self.assertTrue(all(row["status"] == "unknown" and row["display_delta"] is None for row in items))
                self.assertTrue(all(row["identity_clue_status"] == "unique_title_image_candidate" for row in items))

    def test_title_image_clue_is_symmetric_and_ambiguity_never_picks_one(self):
        old = [card(1, self.old, 10, goods=None), card(2, self.old, 20, goods=None)]
        new = [card(3, self.new, 40, goods=None)]
        _, items = comparison(self.build(old, new), "new")
        self.assertEqual(len(items), 3)
        self.assertTrue(all(row["identity_clue_status"] == "ambiguous_title_image" for row in items))
        new_item = next(row for row in items if row["new_observation_id"])
        self.assertEqual(new_item["identity_clue_observation_ids"], [1, 2])
        self.assertEqual([row["sales_raw"] for row in new_item["identity_clue_candidates"]], ["已拼10件", "已拼20件"])
        self.assertIsNone(new_item["display_delta"])

    def test_first_observation_candidate_requires_complete_identity_baseline_for_clear_evidence(self):
        for complete, missing, reason, status in ((True, False, "no_prior_observation", "candidate"),
                                                  (False, False, "baseline_partial", "unknown"),
                                                  (True, True, "baseline_identity_incomplete", "unknown")):
            old_run = {**self.old, "status": "complete" if complete else "partial", "end_boundary_observed": complete}
            old = card(1, old_run, goods=None if missing else "100")
            new = card(2, self.new, goods="200")
            _, items = comparison(self.build([old], [new], old=old_run), "new")
            item = next(row for row in items if row["new_observation_id"])
            self.assertEqual((item["reason"], item["status"]), (reason, status))
            self.assertTrue(item["first_observed_candidate"])
            self.assertEqual(item["newness"], "unknown")
            self.assertIsNone(item["crossed_gt10"])
            previous = next(row for row in items if row["old_observation_id"])
            if not missing:
                self.assertEqual(previous["reason"], "not_observed_in_target")

    def test_partial_matching_pair_can_compare_without_global_card_delta(self):
        partial = {**self.new, "status": "partial", "end_boundary_observed": False}
        summary, items = comparison(self.build([card(1, self.old, 10), card(2, self.old, goods="999")], [card(3, partial, 11)], new=partial), "new")
        self.assertEqual(summary["comparable_count"], 1)
        self.assertFalse(summary["target_is_complete"])
        self.assertNotIn("card_delta", summary)
        self.assertEqual(next(row for row in items if row["old_observation_id"] == 2)["reason"], "not_observed_in_target")

    def test_previously_seen_id_absent_from_selected_baseline_is_not_first_observed(self):
        ancient = run("ancient", "2026-10-02T08:00:00+08:00")
        result = build_history([ancient, self.old, self.new],
                               [card(1, ancient, goods="123"), card(2, self.old, goods="999"), card(3, self.new, goods="123")])
        for mode in ("previous", "yesterday_slot"):
            summary, items = comparison(result, "new", mode)
            item = next(row for row in items if row["new_observation_id"] == 3)
            self.assertEqual(item["reason"], "seen_in_earlier_run")
            self.assertEqual(item["earlier_observation_ids"], [1])
            self.assertFalse(item["first_observed_candidate"])
            self.assertEqual(summary["first_observed_candidate_count"], 0)

    def test_first_observed_candidate_is_pending_when_older_history_has_coverage_gaps(self):
        ancient = run("ancient", "2026-10-02T08:00:00+08:00", complete=False)
        result = build_history([ancient, self.old, self.new],
                               [card(1, ancient, goods=None), card(2, self.old, goods="999"), card(3, self.new, goods="123")])
        _, items = comparison(result, "new")
        item = next(row for row in items if row["new_observation_id"] == 3)
        self.assertEqual(item["reason"], "history_coverage_incomplete")
        self.assertEqual(item["first_observed_evidence"], "pending_verification")

    def test_first_run_and_absent_yesterday_do_not_borrow_another_day_or_slot(self):
        ancient = run("ancient", "2026-10-02T08:00:00+08:00")
        wrong_slot = run("wrong_slot", "2026-10-03T20:00:00+08:00")
        result = build_history([ancient, wrong_slot, self.new], [card(1, ancient), card(2, wrong_slot), card(3, self.new)])
        yesterday, items = comparison(result, "new", "yesterday_slot")
        self.assertEqual(yesterday["baseline_status"], "missing_baseline")
        self.assertIsNone(yesterday["baseline_run_id"])
        self.assertEqual(items[0]["reason"], "missing_baseline")
        self.assertFalse(items[0]["first_observed_candidate"])
        first, _ = comparison(result, "ancient")
        self.assertEqual(first["baseline_selection"], "missing_previous")
        previous, _ = comparison(result, "new")
        self.assertEqual(previous["baseline_run_id"], "wrong_slot")

    def test_yesterday_prefers_complete_with_boundary_else_explicit_partial(self):
        partial = run("later_partial", "2026-10-03T10:00:00+08:00", complete=False)
        result = build_history([self.old, partial, self.new], [card(1, self.old), card(2, partial), card(3, self.new)])
        summary, _ = comparison(result, "new", "yesterday_slot")
        self.assertEqual((summary["baseline_run_id"], summary["baseline_selection"]), ("old", "latest_complete_yesterday_slot"))
        result = build_history([partial, self.new], [card(2, partial), card(3, self.new)])
        summary, _ = comparison(result, "new", "yesterday_slot")
        self.assertEqual(summary["baseline_selection"], "latest_partial_yesterday_slot")
        self.assertFalse(summary["baseline_is_complete"])

    def test_shanghai_day_and_noon_slot_use_observation_end_and_shop_isolation(self):
        before = run("before", "2026-10-03T03:40:00+00:00")
        noon = run("noon", "2026-10-03T03:50:00+00:00")
        midnight = run("midnight", "2026-10-03T15:50:00+00:00")
        stranger = run("stranger", "2026-10-03T23:00:00+08:00", shop="other")
        result = build_history([before, noon, midnight, stranger], [])
        history = {row["run_id"]: row for row in result["run_history"]}
        self.assertEqual((history["before"]["local_date"], history["before"]["time_slot"]), ("2026-10-03", "AM"))
        self.assertEqual(history["noon"]["time_slot"], "PM")
        self.assertEqual((history["midnight"]["local_date"], history["midnight"]["time_slot"]), ("2026-10-04", "AM"))
        previous, _ = comparison(result, "midnight")
        self.assertEqual(previous["baseline_run_id"], "noon")

    def test_inputs_immutable_and_item_keys_deterministic(self):
        runs, rows = [self.old, self.new], [card(1, self.old), card(2, self.new)]
        before = deepcopy((runs, rows))
        result = build_history(runs, rows)
        self.assertEqual((runs, rows), before)
        self.assertEqual(result, build_history(runs[::-1], rows[::-1]))
        json.dumps(result, allow_nan=False)

    def test_invalid_source_identity_or_run_window_rejected(self):
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            build_history([self.old], [card(1, self.old), card(1, self.old)])
        with self.assertRaisesRegex(ValueError, "unknown run"):
            build_history([self.old], [card(1, self.new)])
        with self.assertRaisesRegex(ValueError, "timestamp"):
            build_history([{**self.old, "observed_to_epoch": 0}], [])


if __name__ == "__main__":
    unittest.main()
