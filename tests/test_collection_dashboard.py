"""Read-only attempt-dashboard adapter tests, no production data or scheduler."""
from datetime import datetime
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from pdd_monitor import collection_log as journal
from pdd_monitor.collection_dashboard import build_collection_queries
from pdd_monitor.store import import_snapshot


class CollectionDashboardAcceptance(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="pdd_collection_dashboard_synthetic_")
        self.addCleanup(self.temporary.cleanup)
        self.project = Path(self.temporary.name)
        self.state = self.project / "state" / "collection_attempts"
        self.clock = patch.object(journal, "_now", return_value=datetime.fromisoformat("2026-10-04T01:00:00+00:00"))
        self.now = self.clock.start()
        self.addCleanup(self.clock.stop)

    def test_absent_journal_export_does_not_create_files(self):
        queries, metadata = build_collection_queries(self.project)
        self.assertFalse((self.project / "state").exists())
        self.assertEqual(queries["collection_attempts"]["rows"], [])
        self.assertEqual(queries["collection_schedule"]["rows"], [])
        self.assertFalse(metadata["journal_exists"])
        self.assertEqual(metadata["scheduler_state"], "unknown")
        self.assertIsNone(metadata["source_journal_sha256"])
        self.assertEqual(metadata["actual_scheduled_successes"], 0)

    def test_manual_complete_retains_evidence_without_filling_scheduled_success(self):
        data = self.project / "data"
        snapshot = self.project / "synthetic_snapshot.json"
        snapshot.write_text(json.dumps({
            "synthetic": True, "shopName": "SYNTHETIC_TEST_ONLY",
            "sourceUrl": "https://example.invalid/shop?mall_id=9000000001",
            "sort": "上新", "status": "complete", "endBoundaryObserved": True,
            "observedFrom": "2026-10-04T00:00:00Z", "observedTo": "2026-10-04T00:05:00Z",
            "limitations": ["SYNTHETIC ONLY"],
            "rows": [{"viewOrder": 1, "title": "SYNTHETIC_TEST_ONLY card", "cardText": "SYNTHETIC_TEST_ONLY card", "goodsId": None, "goodsUrl": None,
                      "imageUrl": None, "salesRaw": None, "priceRaw": None, "observedAt": "2026-10-04T00:01:00Z"}],
        }), encoding="utf-8")
        run_id = import_snapshot(data, snapshot)["run_id"]
        journal.record_manual_run(self.state, data_dir=data, run_id=run_id, snapshot_path=snapshot, note="Earlier manual evidence, not daily task")
        path = self.state / "journal.sqlite3"
        before = hashlib.sha256(path.read_bytes()).hexdigest()
        queries, metadata = build_collection_queries(self.project)
        attempts = queries["collection_attempts"]["rows"]
        slots = queries["collection_schedule"]["rows"]
        self.assertEqual(len(attempts), 1)
        self.assertEqual(attempts[0]["status"], "complete")
        self.assertEqual(attempts[0]["trigger_kind"], "manual_history")
        self.assertEqual(attempts[0]["run_id"], run_id)
        self.assertEqual(metadata["actual_scheduled_successes"], 0)
        self.assertEqual([slot["status"] for slot in slots], ["planned", "planned"])
        self.assertTrue(all(slot["attempt_id"] is None for slot in slots))
        self.assertEqual(before, hashlib.sha256(path.read_bytes()).hexdigest())
        self.assertEqual(metadata["source_journal_sha256"], before)

    def test_enabled_cross_day_empty_slots_are_all_retained_as_missed(self):
        evidence = self.project / "SYNTHETIC_scheduler_evidence.json"
        evidence.write_text('{"synthetic":true}', encoding="utf-8")
        self.now.return_value = datetime.fromisoformat("2026-09-30T22:00:00+00:00")
        journal.configure_scheduler(self.state, scheduler_state="enabled", note="SYNTHETIC ONLY", evidence_path=evidence)
        self.now.return_value = datetime.fromisoformat("2026-10-04T01:00:00+00:00")
        before = (self.state / "journal.sqlite3").read_bytes()
        queries, metadata = build_collection_queries(self.project)
        slots = queries["collection_schedule"]["rows"]
        self.assertEqual(len(slots), 8)
        self.assertEqual([slot["slot_key"] for slot in slots], [f"2026-10-{day:02d}/{slot}" for day in range(1, 5) for slot in ("08:00", "20:00")])
        self.assertEqual([slot["status"] for slot in slots], ["missed"] * 7 + ["planned"])
        self.assertTrue(all(slot["attempt_id"] is None and slot["run_id"] is None for slot in slots))
        self.assertEqual(queries["collection_attempts"]["rows"], [])
        self.assertEqual(metadata["actual_scheduled_successes"], 0)
        self.assertEqual(before, (self.state / "journal.sqlite3").read_bytes())


if __name__ == "__main__":
    unittest.main()
