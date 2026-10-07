"""Behavioral acceptance tests. Synthetic data exists only in TemporaryDirectory.

Run from the project directory: python -m unittest discover -s tests -v
The real-data test reads the bundled baseline; it never contacts a shop or image URL.
"""

from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
import struct
import tempfile
import unittest
import zipfile
import zlib

from pdd_monitor.store import (
    attach_images, compare_runs, connect, get_candidates, get_summary, import_snapshot,
)
from pdd_monitor.validation import validate_store


PROJECT = Path(__file__).resolve().parents[1]
SOURCE_NAME = "pdd_phase1_snapshot_20261004.json"
TINY_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jh3sAAAAASUVORK5CYII="
)


def alternative_synthetic_png():
    """A valid one-pixel red PNG, distinct from TINY_PNG; no image library needed."""
    def chunk(kind, payload):
        return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(b"\x00\xff\x00\x00\xff")) + chunk(b"IEND", b""))


def find_source(name):
    for folder in (PROJECT / "sources", PROJECT.parents[1]):
        candidate = folder / name
        if candidate.is_file():
            return candidate
    raise AssertionError(f"Required real acceptance fixture is missing: {name}")


def card(index, sales=None, goods_id=None, title=None, image_url=None, record_key=None):
    """Fabricated card, clearly labeled and never imported into the delivery store."""
    title = title or f"SYNTHETIC_TEST_ONLY card {index}"
    return {
        "viewOrder": index, "domIndex": index - 1,
        "recordKey": record_key or f"synthetic-record-{index}",
        "title": title, "cardText": title + ("\n" + sales if sales else ""),
        "goodsId": goods_id,
        "goodsUrl": f"https://example.invalid/goods?goods_id={goods_id}" if goods_id else None,
        "imageUrl": image_url or f"https://example.invalid/synthetic/{index}.png",
        "priceRaw": "券后¥4.5", "salesRaw": sales,
        "observedAt": "2026-10-04T01:00:00Z",
        "lastDomReadAt": "2026-10-04T01:10:00Z",
    }


class StoreAcceptance(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="pdd_synthetic_acceptance_")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.data = self.root / "db"
        self.sequence = 0

    def snapshot(self, rows, *, status="complete", hour=1, **extra):
        self.sequence += 1
        rows = [dict(row, observedAt=f"2026-10-04T{hour:02d}:00:00Z",
                     lastDomReadAt=f"2026-10-04T{hour:02d}:10:00Z") for row in rows]
        payload = {
            "synthetic": True,
            "shopName": "SYNTHETIC_TEST_ONLY",
            "sourceUrl": "https://example.invalid/synthetic-shop?mall_id=9000000001",
            "sort": "上新",
            "observedFrom": f"2026-10-04T{hour:02d}:00:00Z",
            "observedTo": f"2026-10-04T{hour:02d}:10:00Z",
            "status": status, "endBoundaryObserved": status == "complete",
            "rows": rows,
            "limitations": ["SYNTHETIC TEST FIXTURE; not real observations"],
        }
        payload.update(extra)
        path = self.root / f"synthetic-{self.sequence}.json"
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return path

    def import_rows(self, rows, **kwargs):
        return import_snapshot(self.data, self.snapshot(rows, **kwargs))["run_id"]

    def query(self, sql, parameters=()):
        connection = connect(self.data)
        try:
            return [dict(row) for row in connection.execute(sql, parameters)]
        finally:
            connection.close()

    def counts(self):
        return {
            name: self.query(f"SELECT COUNT(*) AS n FROM {name}")[0]["n"]
            for name in ("runs", "observations", "candidate_groups", "group_members",
                         "image_tasks", "images.assets", "images.source_links")
        }

    def archive(self, rows, *, wrong_hash=False, wrong_url=False, payload=TINY_PNG):
        path = self.root / f"synthetic-images-{self.sequence}.zip"
        items = []
        with zipfile.ZipFile(path, "w") as archive:
            for index, row in enumerate(rows):
                member = f"images/synthetic_{row['viewOrder']}.png"
                archive.writestr(member, payload)
                items.append({
                    "viewOrder": row["viewOrder"], "title": row["title"],
                    "originalImageUrl": "https://example.invalid/wrong.png"
                        if wrong_url and index == len(rows) - 1 else row["imageUrl"],
                    "archivePath": member,
                    "sha256": "0" * 64 if wrong_hash and index == len(rows) - 1
                        else hashlib.sha256(payload).hexdigest(),
                })
            archive.writestr("manifest.json", json.dumps({
                "synthetic": True, "availableCardImageCount": len(items), "items": items,
            }))
        return path

    def test_real_baseline_counts_raw_preservation_and_image_manifest(self):
        snapshot = find_source(SOURCE_NAME)
        archive = find_source("pdd_phase1_available_images_20261004.zip")
        result = import_snapshot(
            self.data, snapshot, archive,
            find_source("pdd_phase1_image_queue_20261004.json"),
            find_source("pdd_phase1_detail_note_20261004.json"),
        )
        run_id = result["run_id"]
        summary = get_summary(self.data, run_id)
        for key, expected in {
            "card_count": 489, "eligible_gt10_count": 61, "exactly_10_count": 3,
            "sales_missing_count": 244, "candidate_group_count": 48,
            "image_saved_card_count": 66, "image_unique_asset_count": 65,
        }.items():
            self.assertEqual(summary[key], expected, key)
        raw = self.query("SELECT snapshot_json, snapshot_sha256 FROM runs WHERE run_id=?", (run_id,))[0]
        self.assertEqual(json.loads(raw["snapshot_json"]), json.loads(snapshot.read_text(encoding="utf-8")))
        self.assertEqual(raw["snapshot_sha256"], hashlib.sha256(snapshot.read_bytes()).hexdigest())
        self.assertEqual(self.query("SELECT COUNT(*) n FROM observations WHERE goods_id IS NOT NULL")[0]["n"], 1)
        self.assertEqual(len(get_candidates(self.data, run_id)), 48)
        with zipfile.ZipFile(archive) as files:
            manifest = json.loads(files.read("manifest.json"))
            for item in manifest["items"]:
                bound = self.query(
                    "SELECT a.data, a.byte_count, a.sha256, s.source_url "
                    "FROM observations o JOIN images.source_links s ON s.observation_id=o.observation_id "
                    "JOIN images.assets a ON a.sha256=s.asset_sha256 WHERE o.run_id=? AND o.view_order=?",
                    (run_id, item["viewOrder"]),
                )
                self.assertEqual(len(bound), 1)
                self.assertEqual(bound[0]["data"], files.read(item["archivePath"]))
                self.assertEqual(bound[0]["source_url"], item["originalImageUrl"])
        validation = validate_store(self.data, run_id)
        self.assertTrue(validation["ok"], validation)

    def test_repeated_identical_import_is_idempotent(self):
        rows = [card(1, "已拼11件"), card(2, None)]
        snapshot = self.snapshot(rows)
        archive = self.archive(rows)
        first = import_snapshot(self.data, snapshot, archive)
        original = self.counts()
        paths = [self.data / "monitor.sqlite3", self.data / "images.sqlite3"]
        hashes = [hashlib.sha256(path.read_bytes()).hexdigest() for path in paths]
        second = import_snapshot(self.data, snapshot, archive)
        self.assertEqual(first["run_id"], second["run_id"])
        self.assertEqual(original, self.counts())
        self.assertEqual(hashes, [hashlib.sha256(path.read_bytes()).hexdigest() for path in paths])
        self.assertEqual(original["images.assets"], 1, "Identical BLOBs must deduplicate by SHA")
        self.assertEqual(original["images.source_links"], 2, "Card image references must remain distinct")

    def test_strict_threshold_and_sales_labels(self):
        run_id = self.import_rows([
            card(1, "已拼10件"), card(2, "已拼10+件"), card(3, "已拼11件"),
            card(4, "已抢99件"), card(5, "总售20件"), card(6, None),
        ])
        rows = self.query("SELECT view_order, eligible_gt10 FROM observations ORDER BY view_order")
        self.assertEqual([r["view_order"] for r in rows if r["eligible_gt10"]], [3])
        self.assertEqual(get_summary(self.data, run_id)["candidate_group_count"], 1)
        self.assertTrue(validate_store(self.data, run_id)["ok"])

    def test_four_four_four_never_aggregates_to_threshold(self):
        run_id = self.import_rows([card(i, "已拼4件", title="SYNTHETIC 同标题候选") for i in (1, 2, 3)])
        self.assertEqual(get_summary(self.data, run_id)["eligible_gt10_count"], 0)
        self.assertEqual(get_candidates(self.data, run_id), [])

    def test_missing_goods_ids_preserve_distinct_cards(self):
        same = "https://example.invalid/synthetic/shared.png"
        run_id = self.import_rows([
            card(1, "已拼11件", title="SYNTHETIC 重复卡片", image_url=same),
            card(2, "已拼12件", title="SYNTHETIC 重复卡片", image_url=same),
        ])
        self.assertEqual(self.query("SELECT COUNT(*) n FROM observations")[0]["n"], 2)
        self.assertEqual(self.query("SELECT COUNT(DISTINCT observation_id) n FROM observations")[0]["n"], 2)
        self.assertTrue(all(r["goods_id"] is None for r in self.query("SELECT goods_id FROM observations")))
        groups = get_candidates(self.data, run_id)
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["member_count"], 2)
        self.assertEqual(groups[0]["relationship"], "suspected_similar")

    def test_partial_same_day_rounds_are_preserved(self):
        old = self.import_rows([card(1, "已拼9件", "900001")], hour=1)
        new = self.import_rows([card(1, "已拼12件", "900001")], hour=2, status="partial")
        self.assertNotEqual(old, new)
        self.assertEqual(self.query("SELECT COUNT(*) n FROM runs")[0]["n"], 2)
        self.assertEqual(get_summary(self.data, old)["eligible_gt10_count"], 0)
        self.assertEqual(get_summary(self.data, new)["eligible_gt10_count"], 1)
        self.assertEqual(self.query("SELECT status FROM runs WHERE run_id=?", (new,))[0]["status"], "partial")

    def test_conflicting_content_in_same_window_is_rejected(self):
        self.import_rows([card(1, "已拼9件", "900001")], hour=1)
        before = self.counts()
        with self.assertRaises((ValueError, RuntimeError)):
            self.import_rows([card(1, "已拼12件", "900001")], hour=1)
        self.assertEqual(before, self.counts())

    def pair(self, older, newer):
        old = self.import_rows(older, hour=1)
        new = self.import_rows(newer, hour=2)
        return compare_runs(self.data, old, new)

    def test_stable_goods_id_growth_and_first_observed_crossing(self):
        result = self.pair([card(1, "已拼9件", "900001")], [card(1, "已拼12件", "900001")])
        item = next(i for i in result["items"] if i["goods_id"] == "900001")
        self.assertEqual(item["display_delta"], 3)
        self.assertIs(item["crossed_gt10"], True)
        self.assertEqual((item["old_value"], item["new_value"]), (9, 12))
        self.assertEqual(item["old_observed_at_precision"], "legacy_unspecified")
        self.assertEqual(item["new_observed_at_precision"], "legacy_unspecified")
        self.assertEqual(item["elapsed_hours"], 1)

    def test_window_based_comparison_reports_interval_bounds_without_exact_elapsed_time(self):
        previous = [card(i, "已拼9件", str(900000 + i)) for i in range(1, 4)]
        current = [card(i, "已拼12件", str(900000 + i)) for i in range(1, 4)]
        runs = []
        for hour, rows in ((1, previous), (2, current)):
            path = self.snapshot(rows, hour=hour)
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["rows"][0]["observedAt"] = None
            payload["rows"][1]["observedAtPrecision"] = "run_window"
            payload["rows"][2]["observedAtPrecision"] = "unknown"
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            runs.append(import_snapshot(self.data, path)["run_id"])
        result = compare_runs(self.data, *runs)
        self.assertEqual(result["summary"]["crossed_gt10_count"], 3)
        for item, precision in zip(result["items"], ("unknown", "run_window", "unknown")):
            self.assertEqual(item["display_delta"], 3)
            self.assertIsNone(item["elapsed_hours"])
            self.assertAlmostEqual(item["elapsed_hours_min"], 50 / 60)
            self.assertAlmostEqual(item["elapsed_hours_max"], 70 / 60)
            self.assertEqual(item["old_time_basis"], "observation_window")
            self.assertEqual(item["new_time_basis"], "observation_window")
            self.assertEqual(item["old_observed_at_precision"], precision)
            self.assertEqual(item["new_observed_at_precision"], precision)
        self.assertIsNone(result["items"][0]["old_observed_at"])
        self.assertIsNone(result["items"][0]["new_observed_at"])

    def test_missing_previous_sales_does_not_claim_crossing(self):
        result = self.pair([card(1, None, "900001")], [card(1, "已拼12件", "900001")])
        item = next(i for i in result["items"] if i["goods_id"] == "900001")
        self.assertIsNone(item["display_delta"])
        self.assertFalse(item["crossed_gt10"])

    def test_negative_delta_is_an_anomaly(self):
        result = self.pair([card(1, "已拼12件", "900001")], [card(1, "已拼5件", "900001")])
        item = next(i for i in result["items"] if i["goods_id"] == "900001")
        self.assertFalse(item["crossed_gt10"])
        self.assertIn("negative", str(item).lower())
        self.assertNotEqual(item["status"], "comparable")

    def test_changed_sales_label_is_not_compared(self):
        result = self.pair([card(1, "已拼9件", "900001")], [card(1, "已抢12件", "900001")])
        item = next(i for i in result["items"] if i["goods_id"] == "900001")
        self.assertIsNone(item["display_delta"])
        self.assertFalse(item["crossed_gt10"])

    def test_repeated_goods_id_is_ambiguous_and_not_compared(self):
        result = self.pair(
            [card(1, "已拼9件", "900001"), card(2, "已拼10件", "900001")],
            [card(1, "已拼12件", "900001")],
        )
        for item in result["items"]:
            self.assertIsNone(item["display_delta"])
            self.assertFalse(item["crossed_gt10"])

    def test_same_title_different_ids_and_missing_ids_are_not_compared(self):
        result = self.pair(
            [card(1, "已拼9件", "900001", title="SYNTHETIC 相同标题"), card(2, "已拼9件")],
            [card(1, "已拼12件", "900002", title="SYNTHETIC 相同标题"), card(2, "已拼12件")],
        )
        self.assertFalse(any(i.get("display_delta") is not None for i in result["items"]))
        self.assertFalse(any(i.get("crossed_gt10") for i in result["items"]))

    def test_zero_goods_ids_are_unknown_placeholders(self):
        result = self.pair(
            [card(1, "已拼9件", 0), card(2, "已拼9件", "0")],
            [card(1, "已拼12件", 0), card(2, "已拼12件", "0")],
        )
        self.assertEqual(self.query("SELECT COUNT(*) n FROM observations WHERE goods_id IS NULL")[0]["n"], 4)
        self.assertFalse(any(i.get("display_delta") is not None for i in result["items"]))
        self.assertFalse(any(i.get("crossed_gt10") for i in result["items"]))

    def test_complete_without_explicit_end_boundary_is_rejected_atomically(self):
        self.import_rows([card(1, "已拼11件")])
        before = self.counts()
        for boundary in (False, None, "true", 1):
            with self.subTest(boundary=boundary):
                path = self.snapshot([card(1, "已拼12件")], hour=2,
                                     endBoundaryObserved=boundary)
                with self.assertRaisesRegex(ValueError, "endBoundaryObserved"):
                    import_snapshot(self.data, path)
                self.assertEqual(before, self.counts())
        path = self.snapshot([card(1, "已拼12件")], hour=2, status=" complete ",
                             endBoundaryObserved=False)
        with self.assertRaisesRegex(ValueError, "endBoundaryObserved"):
            import_snapshot(self.data, path)
        self.assertEqual(before, self.counts())

    def test_invalid_goods_ids_preserve_raw_cards_without_creating_identity(self):
        invalid = ["abc", "-1", "00", "01", "1.5", 1.0, "９００００１"]
        old = [card(i, "已拼9件", value) for i, value in enumerate(invalid, 1)]
        new = [card(i, "已拼12件", value) for i, value in enumerate(invalid, 1)]
        result = self.pair(old, new)
        stored = self.query("SELECT goods_id,identity_status,row_json FROM observations ORDER BY observation_id")
        self.assertEqual(len(stored), len(invalid) * 2)
        self.assertTrue(all(row["goods_id"] is None for row in stored))
        self.assertTrue(all(row["identity_status"] == "unknown_invalid_goods_id" for row in stored))
        self.assertEqual([json.loads(row["row_json"])["goodsId"] for row in stored], invalid * 2)
        self.assertFalse(any(item["display_delta"] is not None for item in result["items"]))
        self.assertTrue(validate_store(self.data)["ok"])

    def test_goods_url_mismatch_blank_and_malformed_identity_are_not_comparable(self):
        old = [card(i, "已拼9件", str(900000 + i)) for i in range(1, 4)]
        new = [card(i, "已拼12件", str(900000 + i)) for i in range(1, 4)]
        new[0]["goodsUrl"] = "https://example.invalid/goods?goods_id=999999"
        new[1]["goodsUrl"] = "https://example.invalid/goods?goods_id="
        new[2]["goodsUrl"] = "https://[invalid"
        result = self.pair(old, new)
        self.assertEqual(result["summary"]["reason_counts"], {"identity_conflict": 3})
        self.assertFalse(any(item["display_delta"] is not None for item in result["items"]))

    def test_candidates_expose_source_time_precision_without_fabricating_missing_time(self):
        rows = [card(i, "已拼11件", str(900000 + i)) for i in range(1, 6)]
        path = self.snapshot(rows)
        payload = json.loads(path.read_text(encoding="utf-8"))
        for row, precision in zip(payload["rows"], ("card_read", "batch_read", "run_window", "unknown")):
            row["observedAtPrecision"] = precision
            if precision in ("run_window", "unknown"):
                row["observedAt"] = None
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        run_id = import_snapshot(self.data, path)["run_id"]
        exported = [row for group in get_candidates(self.data, run_id) for row in group["rows"]]
        self.assertEqual([row["observed_at_precision"] for row in exported],
                         ["card_read", "batch_read", "run_window", "unknown", "legacy_unspecified"])
        self.assertIsNone(exported[2]["observed_at"])
        self.assertIsNone(exported[3]["observed_at"])
        stored = self.query("SELECT row_json FROM observations WHERE run_id=? ORDER BY view_order", (run_id,))
        self.assertEqual([json.loads(row["row_json"]) for row in stored], payload["rows"])

    def test_precision_cannot_claim_a_read_time_when_timestamp_is_missing(self):
        self.import_rows([card(1, "已拼11件")])
        before = self.counts()
        for precision in ("card_read", "batch_read", "made_up"):
            with self.subTest(precision=precision):
                path = self.snapshot([card(1, "已拼12件")], hour=2)
                payload = json.loads(path.read_text(encoding="utf-8"))
                payload["rows"][0].update(observedAt=None, observedAtPrecision=precision)
                path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
                with self.assertRaises(ValueError):
                    import_snapshot(self.data, path)
                self.assertEqual(before, self.counts())

    def test_partial_report_marks_identity_conflict_and_coverage(self):
        from pdd_monitor.reporting import create_report
        full = self.import_rows([card(1, "已拼11件", "900001")])
        conflicted = card(1, "已拼12件", "900001")
        conflicted["goodsUrl"] = "https://example.invalid/goods?goods_id=900002"
        malformed = card(2, "已拼12件", "900003")
        malformed["goodsUrl"] = "https://[invalid"
        partial = self.import_rows([conflicted, malformed], hour=2, status="partial")
        output = self.root / "partial-report.html"
        result = create_report(self.data, output)
        html = output.read_text(encoding="utf-8")
        self.assertEqual(result["run_id"], partial)
        self.assertIn("本轮覆盖不完整", html)
        self.assertIn("商品ID与链接冲突，待核验", html)
        self.assertIn("历史完整快照仍独立保留", html)
        self.assertNotIn('href="https://[invalid"', html)
        self.assertEqual(get_summary(self.data, full)["status"], "complete")
        self.assertEqual(get_summary(self.data, full)["eligible_gt10_count"], 1)

    def test_report_reads_without_changing_database_hashes_or_creating_missing_images(self):
        from pdd_monitor.reporting import create_report
        self.import_rows([card(1, "已拼11件", "900001")])
        paths = [self.data / "monitor.sqlite3", self.data / "images.sqlite3"]
        before = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}
        create_report(self.data, self.root / "readonly-report.html")
        self.assertEqual(before, {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in paths})
        missing_pair = self.root / "synthetic_missing_images"
        missing_pair.mkdir()
        (missing_pair / "monitor.sqlite3").write_bytes(paths[0].read_bytes())
        with self.assertRaisesRegex(ValueError, "Both monitor.sqlite3 and images.sqlite3"):
            create_report(missing_pair, self.root / "missing-pair-report.html")
        self.assertFalse((missing_pair / "images.sqlite3").exists())
        self.assertEqual(before["monitor.sqlite3"], hashlib.sha256((missing_pair / "monitor.sqlite3").read_bytes()).hexdigest())
        self.assertFalse((self.root / "missing-pair-report.html").exists())

    def assert_invalid_archive_does_not_change_store(self, **damage):
        self.import_rows([card(1, "已拼11件")])
        before = self.counts()
        rows = [card(1, "已拼11件"), card(2, "已拼12件")]
        snapshot = self.snapshot(rows, hour=2)
        archive = self.archive(rows, **damage)
        with self.assertRaises((ValueError, RuntimeError, zipfile.BadZipFile)):
            import_snapshot(self.data, snapshot, archive)
        self.assertEqual(before, self.counts(), "Failed import must not leave rows or BLOBs behind")
        self.assertTrue(validate_store(self.data)["ok"])

    def test_bad_image_hash_rolls_back_entire_import(self):
        self.assert_invalid_archive_does_not_change_store(wrong_hash=True)

    def test_wrong_image_url_mapping_rolls_back_entire_import(self):
        self.assert_invalid_archive_does_not_change_store(wrong_url=True)

    def test_later_image_attachment_preserves_snapshot_and_sales(self):
        cards = [card(1, "已拼11件"), card(2, None)]
        run_id = self.import_rows(cards)
        before_runs = self.query("SELECT * FROM runs")
        before_cards = self.query("SELECT * FROM observations")
        result = attach_images(self.data, run_id, self.archive(cards))
        self.assertEqual(result["image_added_card_count"], 2)
        self.assertEqual(result["image_added_asset_count"], 1)
        self.assertEqual(before_runs, self.query("SELECT * FROM runs"))
        self.assertEqual(before_cards, self.query("SELECT * FROM observations"))
        self.assertEqual(get_summary(self.data, run_id)["image_saved_card_count"], 2)
        self.assertTrue(validate_store(self.data, run_id)["ok"])

    def test_repeated_later_image_attachment_is_strongly_idempotent(self):
        cards = [card(1, "已拼11件"), card(2, None)]
        run_id = self.import_rows(cards)
        archive = self.archive(cards)
        attach_images(self.data, run_id, archive)
        counts = self.counts()
        paths = [self.data / "monitor.sqlite3", self.data / "images.sqlite3"]
        before = [hashlib.sha256(path.read_bytes()).hexdigest() for path in paths]
        result = attach_images(self.data, run_id, archive)
        self.assertTrue(result["duplicate"])
        self.assertEqual(result["image_added_card_count"], 0)
        self.assertEqual(counts, self.counts())
        self.assertEqual(before, [hashlib.sha256(path.read_bytes()).hexdigest() for path in paths])

    def test_conflicting_later_image_binding_rejects_entire_batch(self):
        cards = [card(1, "已拼11件"), card(2, None)]
        run_id = self.import_rows(cards)
        attach_images(self.data, run_id, self.archive(cards[:1]))
        counts = self.counts()
        paths = [self.data / "monitor.sqlite3", self.data / "images.sqlite3"]
        before = [hashlib.sha256(path.read_bytes()).hexdigest() for path in paths]
        conflicting_archive = self.archive(cards, payload=alternative_synthetic_png())
        with self.assertRaises((ValueError, RuntimeError)):
            attach_images(self.data, run_id, conflicting_archive)
        self.assertEqual(counts, self.counts())
        self.assertEqual(before, [hashlib.sha256(path.read_bytes()).hexdigest() for path in paths])
        self.assertTrue(validate_store(self.data, run_id)["ok"])

    def test_invalid_json_is_rejected_without_partial_data(self):
        self.import_rows([card(1, "已拼11件")])
        before = self.counts()
        broken = self.root / "synthetic-broken.json"
        broken.write_text('{"rows": [', encoding="utf-8")
        with self.assertRaises((ValueError, RuntimeError)):
            import_snapshot(self.data, broken)
        self.assertEqual(before, self.counts())

    def test_duplicate_record_key_is_rejected_without_partial_data(self):
        self.import_rows([card(1, "已拼11件")])
        before = self.counts()
        broken = self.snapshot([card(1, "已拼11件"), card(2, "已拼12件", record_key="synthetic-record-1")], hour=2)
        with self.assertRaises((ValueError, RuntimeError)):
            import_snapshot(self.data, broken)
        self.assertEqual(before, self.counts())

    def test_validator_detects_blob_corruption(self):
        rows = [card(1, "已拼11件")]
        snapshot = self.snapshot(rows)
        result = import_snapshot(self.data, snapshot, self.archive(rows))
        self.assertTrue(validate_store(self.data, result["run_id"])["ok"])
        connection = connect(self.data)
        try:
            connection.execute("UPDATE images.assets SET data=?", (b"x" * len(TINY_PNG),))
            connection.commit()
        finally:
            connection.close()
        report = validate_store(self.data, result["run_id"])
        self.assertFalse(report["ok"])
        self.assertTrue(report["errors"])

    def test_validator_is_read_only_and_never_creates_missing_databases(self):
        absent = self.root / "synthetic_missing_db"
        self.assertFalse(validate_store(absent)["ok"])
        self.assertFalse(absent.exists())
        run_id = self.import_rows([card(1, "已拼11件")])
        paths = [self.data / "monitor.sqlite3", self.data / "images.sqlite3"]
        before = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}
        self.assertTrue(validate_store(self.data, run_id)["ok"])
        self.assertEqual(before, {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in paths})

    def test_queries_do_not_change_database_file_hashes(self):
        older = self.import_rows([card(1, "已拼9件", "900001")], hour=1)
        newer = self.import_rows([card(1, "已拼12件", "900001")], hour=2)
        paths = [self.data / "monitor.sqlite3", self.data / "images.sqlite3"]
        before = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}
        get_summary(self.data, newer)
        get_candidates(self.data, newer)
        compare_runs(self.data, older, newer)
        self.assertEqual(before, {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in paths})

    def test_validator_detects_threshold_tampering(self):
        run_id = self.import_rows([card(1, "已拼11件")])
        connection = connect(self.data)
        try:
            connection.execute("UPDATE observations SET eligible_gt10=0")
            connection.commit()
        finally:
            connection.close()
        report = validate_store(self.data, run_id)
        self.assertFalse(report["ok"])
        self.assertTrue(report["errors"])


if __name__ == "__main__":
    unittest.main()
