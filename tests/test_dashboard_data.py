"""Dashboard evidence tests: synthetic data only, isolated temporary stores."""

from __future__ import annotations

import base64
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
import zipfile
from unittest.mock import patch

from pdd_monitor.dashboard_data import build_dashboard_snapshot
from pdd_monitor.store import import_snapshot
from pdd_monitor.new_arrivals import make_tracking_config


PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jh3sAAAAASUVORK5CYII=")


class DashboardSnapshotAcceptance(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="pdd_dashboard_synthetic_")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.data = self.root / "data"
        self.output = self.root / "dashboard" / "data.json"

    def test_in_memory_refresh_preserves_published_snapshot_and_complete_evidence(self):
        self.make_run(["已拼11件", "已抢3件"], hour=1, image=True)
        self.make_run(["已拼12件"], hour=2, image=True)
        self.output.parent.mkdir()
        original = b'{"id":"canonical-synthetic","buildStatus":"complete"}\n'
        self.output.write_bytes(original)
        before = {p.name: p.read_bytes() for p in self.data.glob('*.sqlite3')}
        timestamp = '2026-10-07T00:00:00.000Z'
        with patch('pdd_monitor.dashboard_data._utc_now', return_value=timestamp), \
                patch('pdd_monitor.collection_log._now', return_value=datetime(2026, 10, 7, tzinfo=timezone.utc)), \
                patch('pdd_monitor.artist_heat._now', return_value=timestamp), \
                patch('pdd_monitor.profit_lab._now', return_value=timestamp), \
                patch('pdd_monitor.trend_backtest.datetime', wraps=datetime) as backtest_clock:
            backtest_clock.now.return_value = datetime(2026, 10, 7, tzinfo=timezone.utc)
            in_memory = build_dashboard_snapshot(self.data, self.output, write_output=False)
            self.assertEqual(self.output.read_bytes(), original)
            self.assertFalse((self.root / 'state/trend_decisions/decisions.json').exists())
            saved = build_dashboard_snapshot(self.data, self.output)
        # Independent builds retain their actual per-query execution timestamps.
        # Compare all evidence and rows after normalizing only those clock fields.
        def without_execution_times(value):
            if isinstance(value, dict):
                return {key: without_execution_times(item) for key, item in value.items()
                        if key not in ('executedAt', 'executed_at')}
            if isinstance(value, list):
                return [without_execution_times(item) for item in value]
            return value
        self.assertEqual(without_execution_times(in_memory), without_execution_times(saved))
        self.assertEqual(saved, json.loads(self.output.read_text(encoding='utf-8')))
        self.assertEqual(len(saved['queries']['observations']['rows']), 3)
        self.assertEqual(len(saved['queries']['image_assets']['rows']), 1)
        self.assertEqual({p.name: p.read_bytes() for p in self.data.glob('*.sqlite3')}, before)

    def test_in_memory_refresh_does_not_create_output_directory(self):
        self.make_run(["已拼11件"])
        result = build_dashboard_snapshot(self.data, self.output, write_output=False)
        self.assertEqual(len(result['queries']['observations']['rows']), 1)
        self.assertFalse(self.output.parent.exists())

    def test_local_http_images_preserve_every_card_and_verified_asset(self):
        self.make_run(["已拼11件", "已抢3件"], image=True)
        before = {p.name: p.read_bytes() for p in self.data.glob('*.sqlite3')}
        inline = build_dashboard_snapshot(self.data, self.output, write_output=False)
        local = build_dashboard_snapshot(self.data, self.output, write_output=False,
                                         image_delivery='local_http')
        self.assertEqual(inline['queries']['observations']['rows'], local['queries']['observations']['rows'])
        for old, new in zip(inline['queries']['image_assets']['rows'], local['queries']['image_assets']['rows']):
            self.assertEqual(new['data_url'], '/__pdd_image/' + old['sha256'])
            self.assertEqual(new['integrity_status'], 'verified_local')
            self.assertEqual({k: v for k, v in old.items() if k != 'data_url'},
                             {k: v for k, v in new.items() if k != 'data_url'})
            self.assertTrue(old['data_url'].startswith('data:image/png;base64,'))
        self.assertEqual({p.name: p.read_bytes() for p in self.data.glob('*.sqlite3')}, before)
        self.assertFalse(self.output.exists())

    def test_local_http_mode_still_rejects_corrupted_images(self):
        self.make_run(["已拼11件"], image=True)
        connection = sqlite3.connect(self.data / 'images.sqlite3')
        try:
            connection.execute('UPDATE assets SET data=?', (b'X' * len(PNG),))
            connection.commit()
        finally:
            connection.close()
        with self.assertRaisesRegex(ValueError, 'SHA-256/byte count'):
            build_dashboard_snapshot(self.data, self.output, write_output=False, image_delivery='local_http')
        self.assertFalse(self.output.exists())

    def test_unknown_image_delivery_is_rejected_before_reading_anything(self):
        with self.assertRaisesRegex(ValueError, 'delivery mode'):
            build_dashboard_snapshot(self.data, self.output, write_output=False, image_delivery='external')

    def make_run(self, values, hour=1, status="complete", image=False, precision=None, image_variant=False):
        rows = []
        for index, value in enumerate(values, 1):
            row = {"viewOrder": index, "title": "SYNTHETIC SAME TITLE", "goodsId": None,
                   "recordKey": f"SYNTHETIC-{hour}-{index}", "salesRaw": value, "priceRaw": "¥4.5",
                   "observedAt": f"2026-10-04T{hour:02d}:00:00Z", "imageUrl": f"https://example.invalid/{index}.png"}
            if image_variant:
                row['imageUrl'] = f'https://img-2.pddpic.com/goods/SYNTHETIC/{index}.png?format=webp'
            if precision:
                row["observedAtPrecision"] = precision
            rows.append(row)
        payload = {"synthetic": True, "shopName": "SYNTHETIC_TEST_ONLY",
                   "sourceUrl": "https://example.invalid/mall?mall_id=9000000001", "sort": "上新",
                   "observedFrom": f"2026-10-04T{hour:02d}:00:00Z", "observedTo": f"2026-10-04T{hour:02d}:10:00Z",
                   "status": status, "endBoundaryObserved": status == "complete", "rows": rows}
        snapshot = self.root / f"synthetic-{hour}.json"
        snapshot.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        archive = None
        if image:
            archive = self.root / f"synthetic-images-{hour}.zip"
            with zipfile.ZipFile(archive, "w") as stream:
                items = []
                for row in rows:
                    path = f"images/{row['viewOrder']}.png"
                    stream.writestr(path, PNG)
                    item = {"viewOrder": row["viewOrder"], "originalImageUrl": row["imageUrl"],
                            "archivePath": path, "sha256": hashlib.sha256(PNG).hexdigest()}
                    if image_variant:
                        acquired = f'https://img.pddpic.com/goods/SYNTHETIC/{row["viewOrder"]}.png'
                        item.update(image_acquisition_url=acquired, acquisitionMethod='authorized_store_search_variant',
                                    variant_provenance={'method': 'exact_cdn_path_store_search',
                                        'snapshot_sha256': hashlib.sha256(snapshot.read_bytes()).hexdigest(),
                                        'original_image_url': row['imageUrl'], 'image_acquisition_url': acquired,
                                        'exact_pathname': f'/goods/SYNTHETIC/{row["viewOrder"]}.png',
                                        'original_title': row['title'], 'search_title': row['title'],
                                        'search_page_url': 'https://mobile.yangkeduo.com/mall_search_result.html?mall_id=9000000001',
                                        'inventory_sha256': 'a' * 64, 'receipt_sha256': 'b' * 64,
                                        'asset_sha256': hashlib.sha256(PNG).hexdigest(), 'asset_index': 0,
                                        'review_sha256': 'c' * 64, 'acquired_at': '2026-10-04T01:01:00Z',
                                        'historical_observation_ids': [1], 'navigation_evidence_sha256': 'd' * 64})
                    items.append(item)
                stream.writestr("manifest.json", json.dumps({"items": items}))
        return import_snapshot(self.data, snapshot, archive)["run_id"]

    def test_store_search_variant_exports_actual_origin_and_mime_without_relabeling_original_url(self):
        self.make_run(['已拼11件'], image=True, image_variant=True)
        before = self.hashes()
        result = self.export()
        row = result['queries']['observations']['rows'][0]
        self.assertEqual(row['image_content_status'], 'verified_local')
        self.assertEqual(row['image_content_kind'], 'store_search_variant')
        self.assertEqual(row['image_content_mime'], 'image/png')
        self.assertEqual(row['image_url'], 'https://img-2.pddpic.com/goods/SYNTHETIC/1.png?format=webp')
        self.assertEqual(row['image_acquisition_url'], 'https://img.pddpic.com/goods/SYNTHETIC/1.png')
        self.assertFalse(row['image_original_bytes_verified'])
        self.assertEqual(row['image_variant_provenance']['asset_sha256'], row['asset_sha256'])
        for name in ('warehouse_records', 'warehouse_points', 'warehouse_focus_items'):
            item = result['queries'][name]['rows'][0]
            self.assertEqual(item['image_content_kind'], 'store_search_variant')
            self.assertEqual(item['image_acquisition_url'], row['image_acquisition_url'])
        self.assertEqual(before, self.hashes())

    def test_original_image_export_and_missing_image_do_not_invent_variant_provenance(self):
        self.make_run(['已拼11件'], image=True)
        self.make_run(['已拼12件'], hour=2)
        result = self.export()
        rows = sorted(result['queries']['observations']['rows'], key=lambda row: row['observation_id'])
        self.assertEqual(rows[0]['image_content_kind'], 'original_url_content')
        self.assertEqual(rows[0]['image_acquisition_url'], rows[0]['image_url'])
        self.assertTrue(rows[0]['image_original_bytes_verified'])
        self.assertIsNone(rows[0]['image_variant_provenance'])
        self.assertIsNone(rows[1]['image_content_kind'])
        self.assertIsNone(rows[1]['image_content_mime'])
        self.assertIsNone(rows[1]['image_acquisition_url'])
        self.assertFalse(rows[1]['image_original_bytes_verified'])

    def test_corrupt_variant_origin_proof_rejects_export_without_replacing_previous_snapshot(self):
        self.make_run(['已拼11件'], image=True, image_variant=True)
        self.export()
        previous = self.output.read_bytes()
        connection = sqlite3.connect(self.data / 'images.sqlite3')
        try:
            manifest = json.loads(connection.execute('SELECT manifest_json FROM source_links').fetchone()[0])
            manifest['variant_provenance']['snapshot_sha256'] = 'f' * 64
            connection.execute('UPDATE source_links SET manifest_json=?', (json.dumps(manifest),))
            connection.commit()
        finally:
            connection.close()
        before = self.hashes()
        with self.assertRaisesRegex(ValueError, 'SHA mismatch'):
            self.export()
        self.assertEqual(before, self.hashes())
        self.assertEqual(previous, self.output.read_bytes())

    def hashes(self):
        return {name: hashlib.sha256((self.data / name).read_bytes()).hexdigest()
                for name in ("monitor.sqlite3", "images.sqlite3")}

    def export(self):
        return build_dashboard_snapshot(self.data, self.output)

    def test_fixed_arrival_anchor_survives_new_import_and_export(self):
        first_run = self.make_run(["已拼1件"])
        first = self.export()
        config = make_tracking_config(first["queries"]["runs"]["rows"], first["queries"]["observations"]["rows"],
                                      shop_id=first["queries"]["runs"]["rows"][0]["shop_id"],
                                      started_at="2026-10-04T01:11:00Z")
        config_path = self.root / "state" / "new_arrivals" / "tracking_config.json"
        config_path.parent.mkdir(parents=True)
        config_path.write_text(json.dumps(config), encoding="utf-8")
        config_bytes = config_path.read_bytes()
        self.make_run(["已拼12件", None], hour=2, status="partial")
        database_before = self.hashes()
        result = self.export()
        summary = result["queries"]["new_arrival_summary"]["rows"][0]
        self.assertEqual(summary["baseline_run_ids"], [first_run])
        self.assertEqual(summary["baseline_observation_count"], 1)
        self.assertEqual(summary["post_baseline_run_count"], 1)
        self.assertEqual(summary["item_count"], 1)
        self.assertEqual(result["queries"]["new_arrival_items"]["rows"][0]["view_order"], 2)
        self.assertEqual(config_bytes, config_path.read_bytes())
        self.assertEqual(database_before, self.hashes())
        self.assertTrue(result["metadata"]["new_arrival_tracking"]["configured"])
        self.assertEqual(result["metadata"]["new_arrival_tracking"]["config_sha256"], hashlib.sha256(config_bytes).hexdigest())

    def test_invalid_arrival_anchor_does_not_replace_reviewed_export(self):
        self.make_run(["已拼1件"])
        self.export()
        prior_output = self.output.read_bytes()
        config_path = self.root / "state" / "new_arrivals" / "tracking_config.json"
        config_path.parent.mkdir(parents=True)
        config_path.write_text('{"schema_version":1}', encoding="utf-8")
        with self.assertRaises(ValueError):
            self.export()
        self.assertEqual(prior_output, self.output.read_bytes())

    def test_all_cards_retained_strict_threshold_and_unknowns(self):
        self.make_run(["已拼11件", "已拼10件", "已拼9件", "已拼0件", "已拼10+件", None,
                       "已抢99件", "总售100件", "已拼20单", "已售5件", "售出3件"])
        before = self.hashes()
        snapshot = self.export()
        rows = snapshot["queries"]["observations"]["rows"]
        self.assertEqual(len(rows), 11)
        self.assertEqual([r["view_order"] for r in rows if r["eligible_gt10"]], [1, 7])
        self.assertFalse(rows[6]['stored_eligible_gt10'])
        self.assertEqual(rows[6]['sales_raw'], '已抢99件')
        self.assertEqual(rows[6]['sales_label'], '已抢')
        self.assertEqual([r["view_order"] for r in rows if r["reference_selection"] == "positive"], [1, 2, 3, 7, 8, 9, 10, 11])
        self.assertEqual(rows[1]["reference_group"], "yipin_eq10")
        self.assertEqual(rows[2]["reference_group"], "yipin_1to9")
        self.assertEqual(rows[3]["reference_selection"], "zero")
        self.assertIsNone(rows[3]["reference_group"])
        self.assertEqual(rows[4]["reference_selection"], "non_exact_or_unparsed")
        self.assertIsNone(rows[4]["sales_value"])
        self.assertEqual(rows[5]["reference_selection"], "missing")
        self.assertIsNone(rows[5]["sales_value"])
        self.assertTrue(all(r["goods_id"] is None for r in rows))
        self.assertTrue(all(r["original"]["recordKey"] == r["record_key"] for r in rows))
        self.assertEqual(rows[8]["reference_group"], "other_positive")
        self.assertEqual(before, self.hashes())

    def test_default_complete_run_survives_newer_partial_and_no_cross_run_count(self):
        complete_id = self.make_run(["已拼11件", None])
        partial_id = self.make_run(["已拼20件"], hour=2, status="partial", precision="batch_read")
        snapshot = self.export()
        self.assertEqual(snapshot["metadata"]["default_run_id"], complete_id)
        self.assertEqual(snapshot["metadata"]["latest_run_id"], partial_id)
        self.assertFalse(snapshot["metadata"]["default_run_is_partial"])
        runs = snapshot["queries"]["runs"]["rows"]
        self.assertEqual([r["card_count"] for r in runs], [1, 2])
        self.assertEqual([r["run_id"] for r in runs if r["default_selected"]], [complete_id])
        self.assertEqual(len(snapshot["queries"]["observations"]["rows"]), 3)
        self.assertEqual(snapshot["metadata"]["cross_run_growth_status"], "derived_evidence")
        self.assertEqual(len(snapshot["queries"]["run_history"]["rows"]), 2)
        self.assertEqual(len(snapshot["queries"]["comparison_summaries"]["rows"]), 4)
        self.assertEqual(snapshot["queries"]["comparison_items"]["source"]["classification"], "derived")
        self.assertNotIn("product_count", snapshot["metadata"])
        self.assertEqual(snapshot["queries"]["observations"]["source"]["executedAt"][:10], snapshot["generatedAt"][:10])
        self.assertNotEqual(snapshot["generatedAt"], runs[0]["observed_to"])

    def test_no_complete_run_has_explicit_latest_partial_fallback(self):
        self.make_run(["已拼2件"], status="partial")
        latest = self.make_run([None], hour=2, status="partial")
        snapshot = self.export()
        self.assertEqual(snapshot["metadata"]["default_run_id"], latest)
        self.assertEqual(snapshot["metadata"]["default_run_selection"], "latest_partial_fallback")
        self.assertTrue(snapshot["metadata"]["default_run_is_partial"])
        self.assertTrue(all(r["scope"] == "partial_storefront" for r in snapshot["queries"]["runs"]["rows"]))

    def test_images_deduplicate_content_without_merging_cards_and_no_image_payload_in_observations(self):
        self.make_run(["已拼11件", "已拼4件", "已拼4件"], image=True)
        before = self.hashes()
        snapshot = self.export()
        observations = snapshot["queries"]["observations"]["rows"]
        assets = snapshot["queries"]["image_assets"]["rows"]
        self.assertEqual(len(observations), 3)
        self.assertEqual(len(assets), 1)
        self.assertEqual(assets[0]["data_url"], "data:image/png;base64," + base64.b64encode(PNG).decode("ascii"))
        self.assertEqual(snapshot["queries"]["image_assets"]["payloadColumns"], ["data_url"])
        self.assertTrue(all(row["image_content_status"] == "verified_local" for row in observations))
        self.assertTrue(all("data_url" not in row for row in observations))
        groups = snapshot["queries"]["candidate_groups"]["rows"]
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["member_observation_ids"], [row["observation_id"] for row in observations])
        self.assertEqual(sum(row["is_trigger"] for row in groups[0]["members"]), 1)
        self.assertFalse(groups[0]["same_design_confirmed"])
        self.assertEqual(before, self.hashes())

    def test_image_sha_corruption_rejects_without_replacing_previous_snapshot(self):
        self.make_run(["已拼11件"], image=True)
        self.export()
        original_output = self.output.read_bytes()
        connection = sqlite3.connect(self.data / "images.sqlite3")
        try:
            content = PNG[:-1] + bytes([PNG[-1] ^ 1])
            connection.execute("UPDATE assets SET data=?", (content,))
            connection.commit()
        finally:
            connection.close()
        before = self.hashes()
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            self.export()
        self.assertEqual(self.output.read_bytes(), original_output)
        self.assertEqual(self.hashes(), before)

    def test_image_mime_must_match_signature(self):
        self.make_run(["已拼11件"], image=True)
        connection = sqlite3.connect(self.data / "images.sqlite3")
        try:
            connection.execute("UPDATE assets SET mime=?", ("image/jpeg",))
            connection.commit()
        finally:
            connection.close()
        with self.assertRaisesRegex(ValueError, "MIME"):
            self.export()
        self.assertFalse(self.output.exists())

    def test_refresh_preserves_existing_app_identity_and_updates_reviewed_rows(self):
        self.make_run(["已拼11件"])
        self.output.parent.mkdir()
        identity = {"id": "canonical-app-123", "legacyPresentationTitle": "Old display title", "buildStatus": "ready"}
        self.output.write_text(json.dumps({**identity, "queries": {"stale": {"rows": []}}}), encoding="utf-8")
        result = self.export()
        for key, value in identity.items():
            self.assertEqual(result[key], value)
        self.assertEqual(set(result["queries"]), {"observations", "runs", "candidate_groups", "image_assets", "run_history", "comparison_summaries", "comparison_items", "collection_attempts", "collection_schedule", "new_arrival_items", "new_arrival_summary", "competitor_shops", "competitor_products", "competitor_dimensions", "competitor_strategy", "competitor_targets", "artist_watchlist", "artist_research_summary", "artist_source_channels", "artist_heat_people", "artist_heat_history", "artist_heat_summary", "artist_heat_sources", "profit_opportunities", "profit_opportunity_summary", "profit_trials", "profit_trial_summary", "product_table_shops", "product_table_items", "warehouse_days", "warehouse_records", "warehouse_watch_records", "warehouse_tracks", "warehouse_points", "warehouse_focus_summary", "warehouse_focus_items", "warehouse_display_history", "trend_signals", "trend_signal_summary", "trend_decision_records", "trend_decision_summary", "trend_backtest_summary", "trend_backtest_metrics", "trend_backtest_windows", "trend_backtest_selection", "trend_forecasts", "trend_forecast_summary"})
        self.assertFalse((self.root / 'state/trend_decisions/decisions.json').exists())
        for name in ('trend_backtest_summary', 'trend_backtest_metrics',
                     'trend_backtest_windows', 'trend_backtest_selection', 'trend_forecasts', 'trend_forecast_summary'):
            self.assertEqual(result['queries'][name]['source']['classification'], 'derived')
        # Exporting a one-day store must never invent measured recommendation
        # accuracy or create a prospective decision as a read side effect.
        for row in result['queries']['trend_backtest_metrics']['rows']:
            self.assertIsNone(row['full_precision'])
        for row in result['queries']['trend_backtest_selection']['rows']:
            self.assertIsNone(row['chosen_parameter'])
        self.assertEqual(len(result['queries']['warehouse_days']['rows']),1)
        self.assertEqual(result['queries']['warehouse_records']['rows'][0]['observation_id'],result['queries']['observations']['rows'][0]['observation_id'])
        self.assertEqual(result['queries']['warehouse_points']['source']['classification'],'derived')
        self.assertEqual(result['queries']['warehouse_focus_summary']['rows'][0]['yipin_gt10_count'], 1)
        self.assertEqual(result['queries']['warehouse_focus_items']['source']['classification'], 'derived')
        self.assertEqual(result['queries']['warehouse_focus_items']['rows'][0]['observation_id'], result['queries']['observations']['rows'][0]['observation_id'])
        self.assertEqual(result["queries"]["product_table_shops"]["rows"][0]["hot_count"], 1)
        self.assertEqual(result["queries"]["product_table_shops"]["rows"][0]["baseline_state"], "no_baseline")
        self.assertEqual(result["queries"]["new_arrival_summary"]["rows"][0]["state"], "not_configured")
        self.assertFalse(result["metadata"]["new_arrival_tracking"]["configured"])
        self.assertFalse(result["metadata"]["collection_scheduler"]["journal_exists"])
        self.assertEqual(result["metadata"]["collection_scheduler"]["actual_scheduled_successes"], 0)
        self.assertEqual(json.loads(self.output.read_text(encoding="utf-8"))["id"], identity["id"])

    def test_time_precision_is_source_backed_not_invented(self):
        legacy = self.make_run(["已拼2件"])
        batch = self.make_run(["已拼3件"], hour=2, precision="batch_read")
        snapshot = self.export()
        rows = {r["run_id"]: r for r in snapshot["queries"]["observations"]["rows"]}
        self.assertEqual(rows[legacy]["observed_at_precision"], "legacy_unspecified")
        self.assertEqual(rows[batch]["observed_at_precision"], "batch_read")
        for row in rows.values():
            self.assertEqual(row["observed_at"], row["original"]["observedAt"])

    def test_missing_databases_do_not_create_empty_files(self):
        with self.assertRaises(ValueError):
            self.export()
        self.assertFalse(self.data.exists())
        self.assertFalse(self.output.exists())


if __name__ == "__main__":
    unittest.main()
