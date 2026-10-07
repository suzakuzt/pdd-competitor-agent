"""Storefront identity and cross-store isolation; synthetic temporary stores only."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from pdd_monitor import store
from pdd_monitor.history import build_history
from pdd_monitor.validation import validate_store


class ShopIdentityAcceptance(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="pdd_multishop_synthetic_")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.data = self.root / "data"
        self.sequence = 0

    def snapshot(self, *, source="https://example.invalid/mall?mall_id=900001", hour=1, sales=5, **extra):
        self.sequence += 1
        payload = {"synthetic": True, "shopName": "SYNTHETIC SAME DISPLAY NAME", "sourceUrl": source,
                   "observedFrom": f"2026-10-04T{hour:02d}:00:00Z", "observedTo": f"2026-10-04T{hour:02d}:10:00Z",
                   "status": "complete", "endBoundaryObserved": True, "sort": "上新",
                   "rows": [{"viewOrder": 1, "title": "SYNTHETIC SAME CARD TITLE", "cardText": "SYNTHETIC ONLY",
                             "goodsId": "123", "goodsUrl": "https://example.invalid/goods?goods_id=123",
                             "salesRaw": f"已拼{sales}件", "priceRaw": "¥5", "imageUrl": "https://example.invalid/same.png",
                             "observedAt": f"2026-10-04T{hour:02d}:01:00Z", "observedAtPrecision": "card_read"}]}
        payload.update(extra)
        path = self.root / f"synthetic-{self.sequence}.json"
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return path

    def rows(self, sql, args=()):
        connection = store._connect_readonly(self.data)
        try:
            return [dict(row) for row in connection.execute(sql, args)]
        finally:
            connection.close()

    def hashes(self):
        return {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in self.data.glob("*.sqlite3")}

    def test_valid_numeric_and_opaque_identifiers_keep_legacy_hashes(self):
        for source, kind, value in (("https://example.invalid/mall?mall_id=900001", "mall_id", "900001"),
                                    ("https://example.invalid/mall?mall_sn=opaque-A", "mall_sn", "opaque-A")):
            evidence = store.shop_identity_evidence({"sourceUrl": source})
            self.assertEqual(evidence["shop_id"], "shop_" + hashlib.sha256(("id:" + value).encode()).hexdigest()[:24])
            self.assertEqual(evidence["identity_kind"], kind)
            self.assertEqual(evidence["stable_identifier"], value)

    def test_same_names_different_mall_ids_remain_independent_same_window(self):
        first = store.import_snapshot(self.data, self.snapshot())
        second = store.import_snapshot(self.data, self.snapshot(source="https://example.invalid/mall?mall_id=900002", sales=15))
        self.assertEqual(len(self.rows("SELECT * FROM shops")), 2)
        self.assertNotEqual(first["summary"]["shop_id"], second["summary"]["shop_id"])
        self.assertEqual(first["summary"]["eligible_gt10_count"], 0)
        self.assertEqual(second["summary"]["eligible_gt10_count"], 1)
        self.assertTrue(validate_store(self.data)["ok"])

    def test_missing_stable_id_rejected_before_database_creation(self):
        snapshot = self.snapshot(source="https://example.invalid/distinct-store-no-id")
        with self.assertRaisesRegex(store.EvidenceValidationError, "shopName cannot identify"):
            store.import_snapshot(self.data, snapshot)
        self.assertFalse(self.data.exists())

    def test_explicit_url_conflicts_or_ambiguous_ids_reject_without_changes(self):
        store.import_snapshot(self.data, self.snapshot())
        before = self.hashes()
        cases = [dict(source="https://example.invalid/mall?mall_id=900002", mallId="900001"),
                 dict(source="https://example.invalid/mall?mall_id=900001&mall_id=900002"),
                 dict(source="https://example.invalid/mall?mall_id=&mall_id=900001"),
                 dict(source="https://example.invalid/mall?mall_id=900001", shopId="900002", mallId="900001"),
                 dict(source="https://example.invalid/mall?mall_sn=A&mall_sn=B"),
                 dict(source="https://example.invalid/mall?mall_id=900001", mallId="opaque-not-numeric")]
        for case in cases:
            with self.subTest(case=case), self.assertRaises(store.EvidenceValidationError):
                store.import_snapshot(self.data, self.snapshot(hour=2, **case))
            self.assertEqual(before, self.hashes())

    def test_same_duplicate_query_id_and_opaque_alias_do_not_make_false_conflict(self):
        evidence = store.shop_identity_evidence({"shopId": "900001", "mallId": 900001,
                                               "sourceUrl": "https://example.invalid/mall?mall_id=900001&mall_id=900001&mall_sn=opaque-A"})
        self.assertEqual(evidence["identity_kind"], "mall_id")
        self.assertEqual(evidence["source_mall_sn"], "opaque-A")
        self.assertEqual(evidence["stable_identifier"], "900001")

    def test_numeric_and_opaque_same_text_hash_collision_never_merges(self):
        store.import_snapshot(self.data, self.snapshot())
        before = self.hashes()
        with self.assertRaisesRegex(store.EvidenceValidationError, "namespaces"):
            store.import_snapshot(self.data, self.snapshot(source="https://example.invalid/mall?mall_sn=900001", hour=2))
        self.assertEqual(before, self.hashes())
        self.assertEqual(len(self.rows("SELECT * FROM shops")), 1)
        self.assertEqual(len(self.rows("SELECT * FROM runs")), 1)

    def test_same_id_rename_stays_one_store_and_raw_name_is_preserved(self):
        first = store.import_snapshot(self.data, self.snapshot())
        second = store.import_snapshot(self.data, self.snapshot(hour=2, shopName="SYNTHETIC RENAMED STORE"))
        self.assertEqual(first["summary"]["shop_id"], second["summary"]["shop_id"])
        original = json.loads(self.rows("SELECT snapshot_json FROM runs WHERE run_id=?", (second["run_id"],))[0]["snapshot_json"])
        self.assertEqual(original["shopName"], "SYNTHETIC RENAMED STORE")
        self.assertEqual(len(self.rows("SELECT * FROM shops")), 1)

    def test_old_name_only_snapshot_replay_is_immutable_but_new_one_rejected(self):
        snapshot = self.snapshot(source="https://example.invalid/legacy-no-id")
        old_id = "shop_" + hashlib.sha256(b"name:SYNTHETIC SAME DISPLAY NAME").hexdigest()[:24]
        # Arrange the old algorithm's exact stored outcome in a temporary DB.
        with patch.object(store, "shop_identity_evidence", return_value={"shop_id": old_id, "identity_kind": "legacy_name", "stable_identifier": "SYNTHETIC SAME DISPLAY NAME"}):
            first = store.import_snapshot(self.data, snapshot)
        before = self.hashes()
        repeat = store.import_snapshot(self.data, snapshot)
        self.assertTrue(repeat["duplicate"])
        self.assertEqual(repeat["run_id"], first["run_id"])
        self.assertEqual(before, self.hashes())
        with self.assertRaises(store.EvidenceValidationError):
            store.import_snapshot(self.data, self.snapshot(source="https://example.invalid/different-legacy-no-id", hour=2))
        self.assertEqual(before, self.hashes())

    def test_valid_replay_keeps_both_database_bytes(self):
        snapshot = self.snapshot()
        first = store.import_snapshot(self.data, snapshot)
        before = self.hashes()
        repeated = store.import_snapshot(self.data, snapshot)
        self.assertEqual(first["run_id"], repeated["run_id"])
        self.assertTrue(repeated["duplicate"])
        self.assertEqual(before, self.hashes())

    def test_compare_and_history_cannot_pair_shared_goods_ids_across_stores(self):
        a1 = store.import_snapshot(self.data, self.snapshot(hour=1, sales=5))["run_id"]
        b1 = store.import_snapshot(self.data, self.snapshot(source="https://example.invalid/mall?mall_id=900002", hour=2, sales=15))["run_id"]
        a2 = store.import_snapshot(self.data, self.snapshot(hour=3, sales=6))["run_id"]
        with self.assertRaisesRegex(ValueError, "different shops"):
            store.compare_runs(self.data, a1, b1)
        runs = self.rows("SELECT run_id,shop_id,observed_from,observed_to,status,end_boundary_observed,snapshot_sha256 FROM runs")
        history = build_history(runs, self.rows("SELECT * FROM observations"))
        run_map = {run["run_id"]: run for run in runs}
        for comparison in history["comparison_summaries"]:
            baseline = comparison["baseline_run_id"]
            if baseline:
                self.assertEqual(run_map[baseline]["shop_id"], run_map[comparison["target_run_id"]]["shop_id"])
        latest_a = next(row for row in history["comparison_summaries"] if row["target_run_id"] == a2 and row["mode"] == "previous")
        self.assertEqual(latest_a["baseline_run_id"], a1)


if __name__ == "__main__":
    unittest.main()
