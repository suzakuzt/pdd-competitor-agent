"""In-memory synthetic multi-shop model acceptance; no browser or DB writes."""
from copy import deepcopy
from datetime import datetime, timedelta
import json
import unittest

from pdd_monitor.competitor_model import build_competitor_model, parse_display_price


def run(key, shop="shopA", hour=0, complete=True):
    start = datetime(2026, 10, 4, hour, tzinfo=datetime.fromisoformat("2026-10-04T00:00:00+00:00").tzinfo)
    return {"run_id": key, "shop_id": shop, "shop_name": "SYNTHETIC " + shop,
            "observed_from": start.isoformat(), "observed_to": (start + timedelta(minutes=10)).isoformat(),
            "status": "complete" if complete else "partial", "end_boundary_observed": complete}


def card(key, source, value=11, **overrides):
    return {"observation_id": key, "run_id": source["run_id"], "view_order": key,
            "title": "SYNTHETIC 立牌", "sales_raw": f"已拼{value}件" if value is not None else None,
            "sales_label": "已拼" if value is not None else None, "sales_unit": "件" if value is not None else None,
            "sales_value": value, "sales_precision": "exact_display" if value is not None else "missing",
            "eligible_gt10": value is not None and value > 10, "price_raw": "券后¥2.90",
            "goods_id": None, "identity_status": "unknown_no_goods_id", "image_status": "pending",
            "image_content_status": "not_saved", "observed_at": source["observed_from"],
            "observed_at_precision": "card_read", "original": {"synthetic": True, "salesRaw": f"已拼{value}件" if value is not None else None},
            **overrides}


def pair_history(old_run, new_run, old, new, *, delta=None, mode="previous"):
    hours = (datetime.fromisoformat(new["observed_at"]) - datetime.fromisoformat(old["observed_at"])).total_seconds() / 3600
    return {"comparison_summaries": [{"comparison_id": "SYNTHETIC_C", "target_run_id": new_run["run_id"], "baseline_run_id": old_run["run_id"], "mode": mode, "baseline_status": "available"}],
            "comparison_items": [{"comparison_id": "SYNTHETIC_C", "comparison_item_id": "SYNTHETIC_PAIR", "target_run_id": new_run["run_id"],
                                  "old_observation_id": old["observation_id"], "new_observation_id": new["observation_id"],
                                  "status": "comparable", "match_basis": "same_unique_goods_id", "elapsed_hours_min": hours,
                                  "display_delta": new["sales_value"] - old["sales_value"] if delta is None else delta}]}


class CompetitorModelTests(unittest.TestCase):
    def setUp(self):
        self.old = run("old")
        self.latest = run("latest", hour=2, complete=False)

    def test_each_shop_selects_its_own_complete_reference_and_preserves_latest_partial(self):
        b = run("B_partial", shop="shopB", hour=3, complete=False)
        rows = [card(1, self.old, 11), card(2, self.latest, 99), card(3, b, 4)]
        result = build_competitor_model([b, self.latest, self.old], rows, [])
        shops = {row["shop_id"]: row for row in result["competitor_shops"]}
        self.assertEqual(shops["shopA"]["reference_run_id"], "old")
        self.assertEqual(shops["shopA"]["latest_run_id"], "latest")
        self.assertEqual(shops["shopA"]["card_count"], 1)
        self.assertEqual(shops["shopA"]["historical_run_count"], 2)
        self.assertEqual(shops["shopB"]["reference_selection"], "latest_partial_fallback")
        self.assertEqual(shops["shopB"]["reference_scope"], "partial_storefront")
        self.assertEqual([row["observation_id"] for row in result["competitor_products"]], [1, 3])

    def test_complete_without_boundary_is_partial_fallback_not_a_full_reference(self):
        incomplete = {**self.old, "end_boundary_observed": False}
        shop = build_competitor_model([incomplete], [card(1, incomplete)], [])["competitor_shops"][0]
        self.assertEqual(shop["reference_selection"], "latest_partial_fallback")
        self.assertEqual(shop["reference_scope"], "partial_storefront")

    def test_same_goods_id_title_and_image_in_other_shop_are_not_duplicates_or_groups(self):
        b = run("b", shop="shopB")
        rows = [card(1, self.old, goods_id="123", identity_status="unique_goods_id"), card(2, b, goods_id="123", identity_status="unique_goods_id")]
        result = build_competitor_model([self.old, b], rows, [])
        self.assertTrue(all(row["identity_unique_count"] == 1 and row["candidate_group_count"] == 0 for row in result["competitor_shops"]))
        self.assertEqual(len(result["competitor_products"]), 2)
        self.assertEqual(len({row["product_record_id"] for row in result["competitor_products"]}), 2)

    def test_all_reference_cards_and_nulls_retained_strict_sales_buckets(self):
        values = [11, 10, 9, 0, None]
        rows = [card(index + 1, self.old, value) for index, value in enumerate(values)]
        rows += [card(6, self.old, 100, sales_label="已抢", sales_raw="已抢100件"),
                 card(7, self.old, 100, sales_unit="单", sales_raw="已拼100单"),
                 card(8, self.old, None, sales_raw="已拼10+件", sales_label="已拼", sales_precision="non_exact_or_unparsed"),
                 card(9, self.old, 99, sales_raw=None), card(10, self.old, 11.5), card(11, self.old, True)]
        result = build_competitor_model([self.old], rows, [])
        products = result["competitor_products"]
        self.assertEqual(len(products), 11)
        self.assertEqual([row["observation_id"] for row in products if row["eligible_gt10"]], [1, 6])
        self.assertEqual([row["sales_bucket"] for row in products[:8]], ["yipin_gt10", "yipin_eq10", "yipin_1to9", "yipin_zero", "unknown", "yipin_gt10", "other_label", "unknown"])
        self.assertIsNone(products[4]["sales_value"])
        self.assertEqual(products[4]["original"], rows[4]["original"])
        self.assertEqual(result["competitor_shops"][0]["other_label_positive_card_count"], 1)

    def test_individual_sales_bands_use_inclusive_boundaries_no_summed_eligibility(self):
        values = [4, 4, 4, 10, 11, 30, 31, 100, 101]
        rows = [card(index + 1, self.old, value) for index, value in enumerate(values)]
        products = build_competitor_model([self.old], rows, [])["competitor_products"]
        self.assertEqual(sum(row["eligible_gt10"] for row in products), 5)
        self.assertEqual([row["sales_band"] for row in products[4:]], ["yipin_11_30", "yipin_11_30", "yipin_31_100", "yipin_31_100", "yipin_gt100"])

    def test_price_parsing_is_integer_cents_and_rejects_ranges_multiple_or_estimated_values(self):
        for raw, fen, condition in (("券后¥2.90", 290, "coupon_after_display"), ("¥2.9", 290, "display_unspecified"), ("￥0", 0, "display_unspecified"), ("券后 ￥4.50", 450, "coupon_after_display")):
            value = parse_display_price(raw)
            self.assertEqual((value["fen"], value["condition"]), (fen, condition))
            self.assertIs(type(value["fen"]), int)
        for raw in ("¥2–5", "¥2起", "¥2 ¥3", "券后2.9", "¥1.234", "¥1e3", "¥-1", "¥90071992547409.92", 2.9):
            with self.subTest(raw=raw):
                self.assertEqual(parse_display_price(raw)["status"], "unparsed")
                self.assertIsNone(parse_display_price(raw)["fen"])
        self.assertEqual(parse_display_price(None)["status"], "missing")

    def test_price_dimensions_keep_coupon_condition_separate_and_cover_each_card_once(self):
        prices = ["券后¥2.90", "¥2.90", "¥3", "券后¥5.00", "¥10", "¥2-5", None]
        rows = [card(index + 1, self.old, price_raw=value) for index, value in enumerate(prices)]
        result = build_competitor_model([self.old], rows, [])
        dims = [row for row in result["competitor_dimensions"] if row["dimension_key"] == "display_price_band"]
        low = [row for row in dims if row["dimension_value"] == "under_300"]
        self.assertEqual({row["qualifiers"]["price_condition"] for row in low}, {"coupon_after_display", "display_unspecified"})
        self.assertEqual(sorted(value for row in dims for value in row["observation_ids"]), list(range(1, 8)))
        self.assertEqual(sum(row["card_count"] for row in dims), 7)
        self.assertEqual(result["competitor_shops"][0]["price_unparsed_count"], 2)

    def test_keyword_category_is_explicit_priority_and_all_matches_remain_inspectable(self):
        titles = ["SYNTHETIC 立牌钥匙扣吧唧小卡", "SYNTHETIC 挂饰", "SYNTHETIC 徽章", "SYNTHETIC 明信片", "SYNTHETIC 没有匹配"]
        result = build_competitor_model([self.old], [card(i + 1, self.old, title=value) for i, value in enumerate(titles)], [])
        self.assertEqual([row["category_primary_clue"] for row in result["competitor_products"]], ["立牌", "挂件", "徽章", "卡片", "其他"])
        self.assertEqual(result["competitor_products"][0]["category_matched_clues"], ["立牌", "挂件", "徽章", "卡片"])
        self.assertEqual(result["competitor_products"][0]["category_rule_matches"]["挂件"], ["钥匙扣"])
        dims = [row for row in result["competitor_dimensions"] if row["dimension_key"] == "category_clue"]
        self.assertEqual(sum(row["card_count"] for row in dims), 5)
        self.assertTrue(all(not row["counts_overlap"] for row in dims))

    def test_price_structure_strategy_keeps_conditions_modes_denominators_and_unknown(self):
        prices = ["券后¥2.90", "券后¥2.90", "券后¥3.90", "¥2.90", "¥3.90", "¥2-5"]
        result = build_competitor_model([self.old], [card(i + 1, self.old, price_raw=value) for i, value in enumerate(prices)], [])
        strategy = next(row for row in result["competitor_strategy"] if row["rule_id"] == "DISPLAY_PRICE_STRUCTURE")
        modes = strategy["thresholds"]["modal_display_prices"]
        coupon = next(row for row in modes if row["price_condition"] == "coupon_after_display")
        self.assertEqual((coupon["display_price_fen"], coupon["card_count"], coupon["parsed_condition_card_count"], coupon["reference_card_count"]), (290, 2, 3, 6))
        self.assertEqual(len([row for row in modes if row["price_condition"] == "display_unspecified"]), 2)
        self.assertEqual(strategy["observation_ids"], [1, 2, 4, 5])
        unknown = build_competitor_model([self.old], [card(1, self.old, price_raw="¥2-5")], [])
        strategy = next(row for row in unknown["competitor_strategy"] if row["rule_id"] == "DISPLAY_PRICE_STRUCTURE")
        self.assertEqual(strategy["thresholds"]["modal_display_prices"], [])
        self.assertEqual(strategy["observation_ids"], [])
        self.assertIn("未知", strategy["facts"][0])

    def test_title_structure_strategy_is_auditable_and_other_is_not_an_industry_claim(self):
        rows = [card(1, self.old, title="SYNTHETIC 无规则词A"), card(2, self.old, title="SYNTHETIC 无规则词B"), card(3, self.old, title="SYNTHETIC 立牌徽章")]
        result = build_competitor_model([self.old], rows, [])
        strategy = next(row for row in result["competitor_strategy"] if row["rule_id"] == "TITLE_FORM_STRUCTURE")
        self.assertEqual(strategy["observation_ids"], [1, 2])
        self.assertEqual(strategy["thresholds"]["leading_category_clues"][0]["category_clue"], "其他")
        self.assertEqual(strategy["thresholds"]["leading_category_clues"][0]["reference_card_count"], 3)
        self.assertIn("规则未命中", strategy["interpretation_candidates"][0])
        self.assertEqual(result["competitor_products"][2]["category_matched_clues"], ["立牌", "徽章"])

    def test_image_failure_is_subset_of_unavailable_and_content_does_not_merge_cards(self):
        rows = [card(1, self.old, image_content_status="verified_local", asset_sha256="same"),
                card(2, self.old, image_content_status="verified_local", asset_sha256="same"),
                card(3, self.old, image_status="blocked", previous_attempt_blocked=True), card(4, self.old)]
        profile = build_competitor_model([self.old], rows, [])["competitor_shops"][0]
        self.assertEqual((profile["card_count"], profile["image_cached_count"], profile["image_unavailable_count"], profile["image_failed_count"]), (4, 2, 2, 1))

    def test_duplicate_and_conflicting_identity_counts_are_per_run(self):
        rows = [card(1, self.old, goods_id="123", identity_status="unique_goods_id"), card(2, self.old, goods_id="123", identity_status="unique_goods_id"),
                card(3, self.old, goods_id="0", identity_status="unique_goods_id"), card(4, self.old, goods_id="999", identity_status="conflict_goods_url")]
        profile = build_competitor_model([self.old], rows, [])["competitor_shops"][0]
        self.assertEqual((profile["identity_unique_count"], profile["identity_unknown_count"], profile["identity_conflict_count"]), (0, 1, 3))

    def test_candidate_membership_uses_whole_group_and_no_sales_sum(self):
        rows = [card(1, self.old, 10), card(2, self.old, 389), card(3, self.old, 11), card(4, self.old, 4), card(5, self.old, 4), card(6, self.old, 4), card(7, self.old, None)]
        groups = [{"group_id": 1, "group_code": "SYNTHETIC_A", "run_id": "old", "member_observation_ids": [1, 2, 3]},
                  {"group_id": 2, "group_code": "SYNTHETIC_B", "run_id": "old", "member_observation_ids": [4, 5, 6]}]
        result = build_competitor_model([self.old], rows, groups)
        profile = result["competitor_shops"][0]
        self.assertEqual((profile["candidate_group_count"], profile["candidate_member_count"], profile["multi_eligible_group_count"], profile["multi_eligible_card_count"]), (2, 6, 1, 2))
        strategy = next(row for row in result["competitor_strategy"] if row["rule_id"] == "TITLE_MULTI_INDEPENDENT_GT10")
        self.assertEqual(strategy["observation_ids"], [1, 2, 3])
        self.assertFalse(strategy["thresholds"]["sales_sum_used"])
        self.assertEqual(result["competitor_products"][0]["peer_eligible_count"], 2)

    def test_group_membership_cannot_cross_store_or_run(self):
        b = run("b", shop="shopB")
        rows = [card(1, self.old), card(2, b)]
        with self.assertRaisesRegex(ValueError, "membership"):
            build_competitor_model([self.old, b], rows, [{"group_id": 1, "run_id": "old", "member_observation_ids": [1, 2]}])

    def test_history_uses_only_existing_latest_previous_valid_pairs_not_other_modes(self):
        old = card(1, self.old, 10, goods_id="123", identity_status="unique_goods_id")
        new = card(2, self.latest, 11, goods_id="123", identity_status="unique_goods_id")
        history = pair_history(self.old, self.latest, old, new)
        history["comparison_items"].append({**history["comparison_items"][0], "comparison_id": "OTHER_MODE", "comparison_item_id": "OTHER"})
        result = build_competitor_model([self.old, self.latest], [old, new], [], history)
        profile = result["competitor_shops"][0]
        self.assertEqual(profile["growth_comparable_count"], 1)
        strategy = next(row for row in result["competitor_strategy"] if row["scope"] == "latest_previous_comparison" and row["basis_comparison_item_ids"])
        self.assertEqual(strategy["observation_ids"], [2])
        self.assertEqual(strategy["supporting_observation_ids"], [1, 2])
        self.assertEqual(strategy["basis_comparison_item_ids"], ["SYNTHETIC_PAIR"])
        self.assertEqual(result["competitor_products"][0]["observation_id"], 1)

    def test_history_rejects_cross_shop_baseline_bad_label_delta_time_or_duplicate_identity(self):
        old = card(1, self.old, 10, goods_id="123", identity_status="unique_goods_id")
        new = card(2, self.latest, 11, goods_id="123", identity_status="unique_goods_id")
        base_history = pair_history(self.old, self.latest, old, new)
        for override, pair_override in (({"sales_label": "总售"}, {}), ({}, {"display_delta": 999}), ({"observed_at": "bad"}, {}), ({"identity_status": "conflict_goods_url"}, {}), ({}, {"elapsed_hours_min": 99})):
            history = deepcopy(base_history)
            history["comparison_items"][0].update(pair_override)
            result = build_competitor_model([self.old, self.latest], [old, {**new, **override}], [], history)
            self.assertEqual(result["competitor_shops"][0]["growth_comparable_count"], 0)
            self.assertEqual(result["competitor_shops"][0]["growth_rejected_pair_count"], 1)
        b = {**self.old, "shop_id": "other_shop"}
        with self.assertRaisesRegex(ValueError, "different shop"):
            build_competitor_model([b, self.latest], [old, new], [], base_history)

    def test_arrival_scope_is_per_shop_and_does_not_convert_missing_configuration_to_zero(self):
        b = run("b", shop="shopB")
        rows = [card(1, self.old), card(2, self.latest), card(3, b)]
        arrivals = {"new_arrival_summary": [{"shop_id": "shopA", "state": "candidates_available", "tracking_id": "SYNTHETIC_T"}],
                    "new_arrival_items": [{"run_id": "old", "observation_id": 1}, {"run_id": "latest", "observation_id": 2}]}
        result = build_competitor_model([self.old, self.latest, b], rows, [], arrivals=arrivals)
        shops = {row["shop_id"]: row for row in result["competitor_shops"]}
        self.assertEqual(shops["shopA"]["new_arrival_anchor_count"], 2)
        self.assertIsNone(shops["shopB"]["new_arrival_anchor_count"])
        strategy = next(row for row in result["competitor_strategy"] if row["rule_id"] == "FIXED_BASELINE_FOLLOWUP")
        self.assertEqual(strategy["observation_ids"], [2])
        self.assertEqual(strategy["supporting_observation_ids"], [1, 2])

    def test_every_dimension_and_strategy_drilldown_is_scoped_to_its_declared_run(self):
        b = run("b", shop="shopB")
        rows = [card(1, self.old, 11), card(2, self.old, None), card(3, b, 4)]
        result = build_competitor_model([self.old, b], rows, [])
        lookup = {row["observation_id"]: row for row in rows}
        for query in ("competitor_dimensions", "competitor_strategy"):
            for row in result[query]:
                self.assertTrue(all(lookup[value]["run_id"] == row["run_id"] for value in row["observation_ids"]))
        for strategy in result["competitor_strategy"]:
            self.assertTrue(strategy["facts"] and strategy["interpretation_candidates"] and strategy["followup_actions"] and strategy["unknowns"])
            self.assertNotIn("score", strategy)
            self.assertNotIn("roi", strategy)
            self.assertTrue(all(isinstance(value, str) for value in strategy["facts"]))

    def test_empty_reference_has_unknown_ratios_and_no_implicit_denominator(self):
        result = build_competitor_model([self.old], [], [])
        profile = result["competitor_shops"][0]
        self.assertEqual(profile["card_count"], 0)
        self.assertIsNone(profile["image_coverage_ratio"])
        self.assertIsNone(profile["identity_coverage_ratio"])
        self.assertIsNone(profile["growth_comparable_count"])

    def test_inputs_are_immutable_and_output_is_deterministic(self):
        rows = [card(2, self.old, 10), card(1, self.old, 11)]
        before = deepcopy(rows)
        first = build_competitor_model([self.old], rows, [])
        self.assertEqual(rows, before)
        self.assertEqual(first, build_competitor_model([self.old], rows[::-1], []))
        first["competitor_products"][0]["original"]["synthetic"] = False
        self.assertTrue(rows[1]["original"]["synthetic"])
        json.dumps(first, allow_nan=False)


if __name__ == "__main__":
    unittest.main()
