"""Collection state transitions use only synthetic evidence in temp directories."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stdout
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from pdd_monitor import collection_log as journal
from pdd_monitor.store import import_snapshot


def moment(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


class CollectionJournalAcceptance(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="pdd_collection_synthetic_")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.state = self.root / "state"
        self.data = self.root / "db"
        self.clock = patch.object(journal, "_now", return_value=moment("2026-10-04T00:00:00Z"))
        self.now = self.clock.start()
        self.addCleanup(self.clock.stop)

    def at(self, value):
        self.now.return_value = moment(value)

    def begin(self, **kwargs):
        return journal.begin_attempt(self.state, trigger_kind="manual", owner_label="SYNTHETIC_TEST_ONLY", **kwargs)

    def finish_failed(self, acquired, reason="SYNTHETIC browser tool failed before cards"):
        return journal.finish_attempt(self.state, acquired["attempt"]["attempt_id"], acquired["owner_token"], status="failed", reason=reason)

    def digest(self):
        return hashlib.sha256((self.state / "journal.sqlite3").read_bytes()).hexdigest()

    def source_hashes(self):
        return {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in self.data.glob("*.sqlite3")}

    def snapshot(self, *, status="complete", start="2026-10-04T00:01:00Z", end="2026-10-04T00:05:00Z"):
        path = self.root / ("snapshot-" + status + "-" + start.replace(":", "") + ".json")
        title = "SYNTHETIC_TEST_ONLY collection card"
        payload = {
            "synthetic": True, "shopName": "SYNTHETIC_TEST_ONLY",
            "sourceUrl": "https://example.invalid/shop?mall_id=9000000001",
            "sort": "上新", "observedFrom": start, "observedTo": end,
            "status": status, "endBoundaryObserved": status == "complete",
            "limitations": ["SYNTHETIC TEST FIXTURE, never production evidence"],
            "rows": [{"viewOrder": 1, "domIndex": 0, "recordKey": "synthetic-card-1",
                      "title": title, "cardText": title + "\n已拼11件",
                      "goodsId": None, "goodsUrl": None,
                      "imageUrl": "https://example.invalid/synthetic/1.png",
                      "priceRaw": "¥5", "salesRaw": "已拼11件",
                      "observedAt": start, "lastDomReadAt": end}],
        }
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        run_id = import_snapshot(self.data, path)["run_id"]
        return path, run_id

    def test_status_absent_is_read_only_and_does_not_invent_activation(self):
        result = journal.get_status(self.state)
        self.assertFalse(result["journal_exists"])
        self.assertEqual(result["scheduler_state"], "unknown")
        self.assertEqual(result["slots"], [])
        self.assertEqual(result["slot_times"], ["08:00", "20:00"])
        self.assertFalse(self.state.exists())

    def test_scheduled_clock_window_and_duplicate_are_truthful(self):
        kwargs = dict(slot_date="2026-10-04", slot_time="08:00", owner_label="SYNTHETIC")
        self.at("2026-10-03T23:59:59Z")
        early = journal.begin_attempt(self.state, **kwargs)
        self.assertEqual(early["outcome"], "not_due")
        self.assertFalse(self.state.exists())
        self.at("2026-10-04T00:30:00Z")
        begun = journal.begin_attempt(self.state, **kwargs)
        self.assertTrue(begun["acquired"])
        self.assertEqual(begun["attempt"]["started_late_seconds"], 1800)
        self.assertEqual(begun["attempt"]["scheduled_at"], "2026-10-04T00:00:00.000Z")
        self.finish_failed(begun)
        before = self.digest()
        duplicate = journal.begin_attempt(self.state, **kwargs)
        self.assertEqual(duplicate["outcome"], "duplicate_slot")
        self.assertEqual(duplicate["attempt"]["status"], "failed")
        self.assertEqual(before, self.digest())
        self.assertEqual(len(journal.get_status(self.state)["attempts"]), 1)

    def test_late_scheduled_run_records_missed_without_data_or_lock(self):
        self.at("2026-10-04T12:30:00.001Z")
        missed = journal.begin_attempt(self.state, slot_date="2026-10-04", slot_time="20:00")
        self.assertFalse(missed["acquired"])
        self.assertEqual(missed["outcome"], "missed")
        self.assertIsNone(missed["attempt"]["started_at"])
        self.assertIsNone(missed["attempt"]["run_id"])
        self.assertNotIn("owner_token", missed)
        self.assertIsNone(journal.get_status(self.state)["active_lock"])
        self.assertTrue(self.begin()["acquired"])

    def test_manual_attempt_protects_global_collection_and_zero_card_failure(self):
        first = self.begin()
        blocked = journal.begin_attempt(self.state, slot_date="2026-10-04", slot_time="08:00")
        self.assertEqual(blocked["outcome"], "collection_locked")
        self.assertEqual(self.begin()["outcome"], "collection_locked")
        result = self.finish_failed(first)
        self.assertEqual(result["attempt"]["status"], "failed")
        self.assertIsNone(result["attempt"]["run_id"])
        self.assertIsNone(result["attempt"]["slot_key"])
        projection = json.loads(Path(result["attempt"]["attempt_json"]).read_text(encoding="utf-8"))
        self.assertEqual(projection["status"], "failed")
        self.assertEqual([row["event_type"] for row in projection["events"]], ["begin", "finish"])
        self.assertNotIn(first["owner_token"], json.dumps(projection))
        self.assertTrue(journal.begin_attempt(self.state, slot_date="2026-10-04", slot_time="08:00")["acquired"])

    def test_concurrent_first_begin_has_only_one_owner(self):
        barrier = threading.Barrier(2)
        def start(_):
            barrier.wait(timeout=5)
            return self.begin()
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(start, range(2)))
        self.assertEqual(sum(result["acquired"] for result in results), 1)
        status = journal.get_status(self.state)
        self.assertEqual(len(status["attempts"]), 1)
        self.assertIsNotNone(status["active_lock"])

    def test_expired_lock_never_steals_and_requires_explicit_recovery(self):
        acquired = self.begin(lease_minutes=1)
        attempt_id = acquired["attempt"]["attempt_id"]
        with self.assertRaises(journal.CollectionLogError):
            journal.recover_attempt(self.state, attempt_id, reason="not owner", confirm_abandon=True)
        self.at("2026-10-04T00:02:00Z")
        blocked = self.begin()
        self.assertFalse(blocked["acquired"])
        self.assertTrue(blocked["recovery_required"])
        with self.assertRaises(journal.CollectionLogError):
            journal.recover_attempt(self.state, attempt_id, reason="expired but no confirmation")
        recovered = journal.recover_attempt(self.state, attempt_id, reason="explicit worker abandonment", confirm_abandon=True)
        self.assertEqual(recovered["status"], "failed")
        self.assertIn("explicit_recovery_abandonment", recovered["reason"])
        self.assertFalse(recovered["events"][-1]["payload"]["owner_token_verified"])
        self.assertTrue(self.begin()["acquired"])

    def test_owner_can_renew_or_finish_expired_lease_but_wrong_token_cannot(self):
        acquired = self.begin(lease_minutes=1)
        attempt_id = acquired["attempt"]["attempt_id"]
        with self.assertRaises(journal.CollectionLogError):
            journal.renew_attempt(self.state, attempt_id, "wrong")
        with self.assertRaises(journal.CollectionLogError):
            journal.finish_attempt(self.state, attempt_id, "wrong", status="failed", reason="wrong owner")
        self.at("2026-10-04T00:02:00Z")
        renewed = journal.renew_attempt(self.state, attempt_id, acquired["owner_token"], lease_minutes=1)
        self.assertFalse(renewed["lock_expired"])
        self.at("2026-10-04T00:04:00Z")
        self.assertEqual(self.finish_failed(acquired)["attempt"]["status"], "failed")

    def test_complete_binds_verified_run_and_keeps_original_databases_unchanged(self):
        acquired = self.begin()
        snapshot, run_id = self.snapshot()
        self.at("2026-10-04T00:06:00Z")
        original = self.source_hashes()
        kwargs = dict(status="complete", run_id=run_id, snapshot_path=snapshot, data_dir=self.data)
        result = journal.finish_attempt(self.state, acquired["attempt"]["attempt_id"], acquired["owner_token"], **kwargs)
        self.assertTrue(result["attempt"]["evidence"]["validation"]["ok"])
        self.assertEqual(result["attempt"]["run_id"], run_id)
        self.assertEqual(result["attempt"]["snapshot_sha256"], hashlib.sha256(snapshot.read_bytes()).hexdigest())
        before = self.digest()
        self.assertTrue(journal.finish_attempt(self.state, acquired["attempt"]["attempt_id"], acquired["owner_token"], **kwargs)["duplicate"])
        self.assertEqual(before, self.digest())
        self.assertEqual(original, self.source_hashes())
        self.assertIsNone(journal.get_status(self.state)["active_lock"])
        with self.assertRaises(journal.CollectionLogError):
            self.finish_failed(acquired)

    def test_partial_is_retained_and_cannot_be_promoted_to_complete(self):
        acquired = self.begin()
        snapshot, run_id = self.snapshot(status="partial")
        self.at("2026-10-04T00:06:00Z")
        kwargs = dict(run_id=run_id, snapshot_path=snapshot, data_dir=self.data)
        with self.assertRaises(journal.CollectionLogError):
            journal.finish_attempt(self.state, acquired["attempt"]["attempt_id"], acquired["owner_token"], status="complete", **kwargs)
        result = journal.finish_attempt(self.state, acquired["attempt"]["attempt_id"], acquired["owner_token"], status="partial", reason="tool interrupted before end boundary", **kwargs)
        self.assertEqual(result["attempt"]["status"], "partial")
        self.assertEqual(result["attempt"]["evidence"]["end_boundary_observed"], 0)

    def test_wrong_snapshot_or_old_window_does_not_satisfy_new_attempt(self):
        snapshot, run_id = self.snapshot()
        self.at("2026-10-04T00:10:00Z")
        acquired = self.begin()
        kwargs = dict(status="complete", run_id=run_id, snapshot_path=snapshot, data_dir=self.data)
        with self.assertRaisesRegex(journal.CollectionLogError, "predates begin"):
            journal.finish_attempt(self.state, acquired["attempt"]["attempt_id"], acquired["owner_token"], **kwargs)
        snapshot.write_bytes(snapshot.read_bytes() + b" ")
        with self.assertRaisesRegex(journal.CollectionLogError, "bytes/SHA"):
            journal.finish_attempt(self.state, acquired["attempt"]["attempt_id"], acquired["owner_token"], **kwargs)
        self.assertEqual(journal.get_status(self.state)["attempts"][0]["status"], "running")

    def test_manual_history_never_fills_scheduled_slots_or_activates_scheduler(self):
        snapshot, run_id = self.snapshot()
        partial, partial_id = self.snapshot(status="partial", start="2026-10-04T01:00:00Z", end="2026-10-04T01:05:00Z")
        self.at("2026-10-04T04:00:00Z")
        original = self.source_hashes()
        for path, identifier in ((snapshot, run_id), (partial, partial_id)):
            journal.record_manual_run(self.state, data_dir=self.data, run_id=identifier, snapshot_path=path, note="Earlier manual collection, never an 08:00 task")
        status = journal.get_status(self.state)
        self.assertEqual(status["scheduler_state"], "unknown")
        self.assertEqual({row["trigger_kind"] for row in status["attempts"]}, {"manual_history"})
        self.assertEqual({row["status"] for row in status["attempts"]}, {"complete", "partial"})
        self.assertTrue(all(row["slot_key"] is None for row in status["attempts"]))
        self.assertTrue(all(row["status"] == "planned" for row in status["slots"]))
        before = self.digest()
        self.assertTrue(journal.record_manual_run(self.state, data_dir=self.data, run_id=run_id, snapshot_path=snapshot, note="duplicate note")["duplicate"])
        self.assertEqual(before, self.digest())
        self.assertEqual(original, self.source_hashes())

    def test_readonly_status_and_pending_schedule_do_not_create_missed_history(self):
        journal.configure_scheduler(self.state, scheduler_state="pending", note="Awaiting verified connection")
        self.at("2026-10-04T15:00:00Z")
        before = self.digest()
        paths = sorted(path.name for path in self.state.iterdir())
        status = journal.get_status(self.state)
        self.assertEqual([row["status"] for row in status["slots"]], ["planned", "planned"])
        self.assertFalse(any(row["due"] for row in status["slots"]))
        self.assertEqual(before, self.digest())
        self.assertEqual(paths, sorted(path.name for path in self.state.iterdir()))

    def test_enabled_schedule_needs_evidence_and_only_later_slots_can_be_missed(self):
        with self.assertRaises(journal.CollectionLogError):
            journal.configure_scheduler(self.state, scheduler_state="enabled", note="unsupported assertion")
        self.assertFalse(self.state.exists())
        evidence = self.root / "SYNTHETIC_scheduler_evidence.json"
        evidence.write_text('{"synthetic":true}', encoding="utf-8")
        self.at("2026-10-04T02:00:00Z")
        journal.configure_scheduler(self.state, scheduler_state="enabled", note="SYNTHETIC connection evidence", evidence_path=evidence)
        status = journal.get_status(self.state)
        self.assertEqual(status["slots"][0]["status"], "planned")
        self.assertIn("not_verified", status["slots"][0]["reason"])
        self.at("2026-10-04T12:02:00Z")
        self.assertTrue(journal.get_status(self.state)["slots"][1]["due"])
        begun = journal.begin_attempt(self.state, slot_date="2026-10-04", slot_time="20:00")
        self.assertFalse(journal.get_status(self.state)["slots"][1]["due"])
        self.finish_failed(begun)
        self.at("2026-10-05T00:31:00Z")
        before = self.digest()
        status = journal.get_status(self.state)
        self.assertEqual(status["slots"][0]["status"], "missed")
        self.assertIsNone(status["slots"][0]["attempt_id"])
        self.assertEqual(before, self.digest(), "derived missed status must not write history")

    def test_json_projection_failure_does_not_lose_durable_owner(self):
        with patch.object(journal, "_atomic_json", side_effect=OSError("SYNTHETIC output denied")):
            result = self.begin()
        self.assertTrue(result["acquired"])
        self.assertIn("json_export_error", result["attempt"])
        self.assertIn("owner_token", result)
        self.assertEqual(journal.get_status(self.state)["active_lock"]["attempt_id"], result["attempt"]["attempt_id"])
        self.assertEqual(self.finish_failed(result)["attempt"]["status"], "failed")

    def test_cli_manual_alias_and_missing_schedule_arguments(self):
        output = io.StringIO()
        with redirect_stdout(output):
            code = journal.main(["--state-dir", str(self.state), "begin", "--manual", "--owner-label", "SYNTHETIC"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output.getvalue())["attempt"]["trigger_kind"], "manual")
        with redirect_stdout(io.StringIO()):
            self.assertEqual(journal.main(["--state-dir", str(self.root / "other"), "begin", "--slot", "08:00", "--owner-label", "SYNTHETIC"]), 2)
        self.assertFalse((self.root / "other").exists())


if __name__ == "__main__":
    unittest.main()
