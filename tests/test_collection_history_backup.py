"""Long-lived scheduler intervals and independent journal recovery acceptance."""
from datetime import datetime
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from pdd_monitor import collection_log as journal
from pdd_monitor.collection_dashboard import build_collection_queries


class CollectionHistoryBackupAcceptance(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="pdd_journal_history_synthetic_")
        self.addCleanup(self.temporary.cleanup)
        self.project = Path(self.temporary.name)
        self.state = self.project / "state" / "collection_attempts"
        self.evidence = self.project / "SYNTHETIC_scheduler_evidence.json"
        self.evidence.write_text('{"synthetic":true}', encoding="utf-8")
        self.clock = patch.object(journal, "_now", return_value=datetime.fromisoformat("2026-09-30T22:00:00+00:00"))
        self.now = self.clock.start()
        self.addCleanup(self.clock.stop)

    def at(self, value):
        self.now.return_value = datetime.fromisoformat(value.replace("Z", "+00:00"))

    def config(self, state):
        return journal.configure_scheduler(self.state, scheduler_state=state, note="SYNTHETIC transition " + state,
                                           evidence_path=self.evidence if state == "enabled" else None)

    def digest(self):
        return hashlib.sha256((self.state / "journal.sqlite3").read_bytes()).hexdigest()

    def v1(self, state="enabled"):
        self.state.mkdir(parents=True)
        connection = sqlite3.connect(self.state / "journal.sqlite3")
        try:
            connection.executescript(journal.SCHEMA)
            connection.execute("PRAGMA user_version=1")
            connection.execute("INSERT INTO configuration VALUES (1,?,?,?,?,?)", (state, "2026-09-30T22:00:00.000Z", "2026-09-30T22:00:00.000Z" if state == "enabled" else None, str(self.evidence), "SYNTHETIC existing v1 current state"))
            connection.commit()
        finally:
            connection.close()

    def test_disabled_reenabled_preserves_prior_missed_and_omits_disabled_slots(self):
        self.config("enabled")
        self.at("2026-10-01T01:00:00Z")
        self.config("disabled")
        self.at("2026-10-03T02:00:00Z")
        self.config("enabled")
        self.at("2026-10-04T01:00:00Z")
        before = self.digest()
        status = journal.get_status(self.state)
        self.assertEqual(status["first_enabled_at"], "2026-09-30T22:00:00.000Z")
        self.assertEqual(len(status["enabled_intervals"]), 2)
        self.assertEqual(len(status["scheduler_history"]), 3)
        self.assertEqual(status["enabled_intervals"][0]["disabled_at"], "2026-10-01T01:00:00.000Z")
        queries, metadata = build_collection_queries(self.project)
        slots = queries["collection_schedule"]["rows"]
        self.assertEqual(len(slots), 8, "must begin from first historical enablement, not latest re-enable")
        self.assertEqual([row["status"] for row in slots], ["missed", "planned", "planned", "planned", "planned", "missed", "missed", "planned"])
        self.assertTrue(slots[0]["enabled_for_slot"])
        self.assertEqual(slots[2]["scheduler_state_at_slot"], "disabled")
        self.assertEqual(len(metadata["scheduler_history"]), 3)
        self.assertEqual(queries["collection_attempts"]["source"]["classification"], "observed")
        self.assertEqual(queries["collection_schedule"]["source"]["classification"], "derived")
        self.assertIn("scheduler_events", queries["collection_schedule"]["source"]["tables"])
        self.assertEqual(before, self.digest())

    def test_disabled_current_state_still_exposes_previous_missed(self):
        self.config("enabled")
        self.at("2026-10-01T01:00:00Z")
        self.config("disabled")
        self.at("2026-10-03T01:00:00Z")
        queries, metadata = build_collection_queries(self.project)
        self.assertEqual(metadata["scheduler_state"], "disabled")
        self.assertEqual(len(queries["collection_schedule"]["rows"]), 6)
        self.assertEqual(queries["collection_schedule"]["rows"][0]["status"], "missed")
        self.assertTrue(all(row["status"] == "planned" for row in queries["collection_schedule"]["rows"][1:]))
        self.assertFalse(any(row["due"] for row in queries["collection_schedule"]["rows"]))

    def test_configuration_events_append_without_splitting_continuous_enablement(self):
        self.config("enabled")
        self.at("2026-10-01T00:00:00Z")
        status = self.config("enabled")
        self.assertEqual(len(status["scheduler_history"]), 2)
        self.assertEqual(len(status["enabled_intervals"]), 1)
        self.assertEqual(status["scheduler_history"][0]["evidence_sha256"], hashlib.sha256(self.evidence.read_bytes()).hexdigest())
        self.assertTrue(status["slots"][0]["due"])
        status = self.config("disabled")  # End of interval is exclusive.
        self.assertFalse(status["slots"][0]["enabled_for_slot"])
        before = self.digest()
        self.at("2026-09-30T23:59:00Z")
        with self.assertRaisesRegex(journal.CollectionLogError, "clock moved backwards"):
            self.config("pending")
        self.assertEqual(before, self.digest())

    def test_v1_status_readonly_then_atomic_write_migration_preserves_known_interval(self):
        self.v1()
        self.at("2026-10-01T01:00:00Z")
        before = self.digest()
        status = journal.get_status(self.state)
        self.assertTrue(status["history_limitations"])
        self.assertEqual(status["slots"][0]["status"], "missed")
        self.assertEqual(before, self.digest(), "read-only status must never migrate")
        self.config("disabled")
        status = journal.get_status(self.state)
        self.assertEqual(status["slots"][0]["status"], "missed")
        self.assertEqual(len(status["scheduler_history"]), 2)
        self.assertEqual(status["scheduler_history"][0]["origin"], "legacy_v1_current_state_only")
        connection = sqlite3.connect(self.state / "journal.sqlite3")
        try:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 2)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM scheduler_events").fetchone()[0], 2)
        finally:
            connection.close()

    def test_disabled_v1_cannot_invent_lost_earlier_enabled_intervals(self):
        self.v1("disabled")
        self.at("2026-10-03T01:00:00Z")
        status = self.config("enabled")
        self.assertTrue(status["history_limitations"])
        self.assertEqual(status["first_enabled_at"], "2026-10-03T01:00:00.000Z")
        self.assertTrue(all(row["status"] == "planned" for row in journal.get_status(self.state, "2026-10-01")["slots"]))

    def test_backup_preserves_all_rows_source_bytes_and_live_lock_for_restore(self):
        self.config("enabled")
        first = journal.begin_attempt(self.state, trigger_kind="manual", owner_label="SYNTHETIC first")
        journal.finish_attempt(self.state, first["attempt"]["attempt_id"], first["owner_token"], status="failed", reason="SYNTHETIC zero-card failure")
        active = journal.begin_attempt(self.state, trigger_kind="manual", owner_label="SYNTHETIC active", lease_minutes=1)
        original_files = {path.name: path.read_bytes() for path in self.state.iterdir()}
        output = self.project / "backup"
        manifest = journal.backup_journal(self.state, output)
        self.assertTrue(manifest["source_unchanged"])
        self.assertTrue(manifest["restore_verification"]["ok"])
        self.assertEqual(manifest["source_summary"]["active_locks_preserved"], 1)
        self.assertEqual(manifest["source_summary"]["logical_sha256"], manifest["restore_verification"]["logical_sha256"])
        self.assertEqual(original_files, {path.name: path.read_bytes() for path in self.state.iterdir()})
        self.assertEqual(len(list(output.glob("attempt_*.json"))), 2)
        self.assertNotIn(active["owner_token"], (output / "status.json").read_text(encoding="utf-8"))
        self.assertNotIn(active["owner_token"], (output / "manifest.json").read_text(encoding="utf-8"))
        for name, record in manifest["files"].items():
            self.assertEqual(record["sha256"], hashlib.sha256((output / name).read_bytes()).hexdigest())
        restored = self.project / "restored_state"
        restored.mkdir()
        shutil.copyfile(output / "journal.sqlite3", restored / "journal.sqlite3")
        blocked = journal.begin_attempt(restored, trigger_kind="manual", owner_label="SYNTHETIC recovery")
        self.assertEqual(blocked["outcome"], "collection_locked")
        self.at("2026-09-30T22:02:00Z")
        recovered = journal.recover_attempt(restored, active["attempt"]["attempt_id"], confirm_abandon=True, reason="explicit copy-only recovery rehearsal")
        self.assertEqual(recovered["status"], "failed")
        self.assertIsNotNone(journal.get_status(self.state)["active_lock"], "restored-copy recovery must not change source lock")
        self.assertEqual(original_files, {path.name: path.read_bytes() for path in self.state.iterdir()})

    def test_backup_v1_does_not_migrate_source_or_copy(self):
        self.v1()
        before = self.digest()
        result = journal.backup_journal(self.state, self.project / "v1_backup")
        self.assertEqual(result["source_summary"]["schema_version"], 1)
        self.assertEqual(result["restore_verification"]["schema_version"], 1)
        self.assertEqual(before, self.digest())

    def test_backup_refuses_existing_output_and_missing_source(self):
        with self.assertRaises(journal.CollectionLogError):
            journal.backup_journal(self.state, self.project / "absent_backup")
        self.assertFalse(self.state.exists())
        self.assertFalse((self.project / "absent_backup").exists())
        self.config("pending")
        output = self.project / "protected_output"
        output.mkdir()
        (output / "sentinel.txt").write_text("must survive", encoding="utf-8")
        before = self.digest()
        with self.assertRaises(journal.CollectionLogError):
            journal.backup_journal(self.state, output)
        self.assertEqual((output / "sentinel.txt").read_text(encoding="utf-8"), "must survive")
        self.assertEqual(before, self.digest())
        self.assertFalse((output / "manifest.json").exists())

    def test_backup_failure_never_publishes_success_manifest(self):
        self.config("pending")
        before = self.digest()
        real_summary = journal._journal_summary
        calls = 0
        def corrupt_verification(connection):
            nonlocal calls
            calls += 1
            result = real_summary(connection)
            if calls == 2:
                result["logical_sha256"] = "SYNTHETIC mismatch"
            return result
        output = self.project / "failed_backup"
        with patch.object(journal, "_journal_summary", side_effect=corrupt_verification):
            with self.assertRaisesRegex(journal.CollectionLogError, "do not match"):
                journal.backup_journal(self.state, output)
        self.assertFalse((output / "manifest.json").exists())
        self.assertEqual(list(output.iterdir()), [])
        self.assertEqual(before, self.digest())


if __name__ == "__main__":
    unittest.main()
