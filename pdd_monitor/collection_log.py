"""Independent collection-attempt journal. Does not schedule or collect a site.

SQLite is authoritative; per-attempt JSON files are atomic, regenerable views.
The original monitor/images databases are only read to verify completion evidence.
An expired logical lease is never automatically stolen. Explicit recovery records
abandonment; a matching owner token may still finish its current expired lease.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import date, datetime, time, timedelta, timezone
import hashlib
import json
import os
import re
from pathlib import Path
import secrets
import sqlite3
import tempfile
import uuid

TZ = timezone(timedelta(hours=8), "Asia/Shanghai")
SLOTS = ("08:00", "20:00")
START_WINDOW_MINUTES = 30
DEFAULT_STATE_DIR = Path(__file__).resolve().parents[1] / "state" / "collection_attempts"
SCHEMA_VERSION = 2
SHOP_ID = re.compile(r'shop_[0-9a-f]{24}')
SCHEDULER_SCHEMA = """
CREATE TABLE IF NOT EXISTS scheduler_events (
 event_id INTEGER PRIMARY KEY, recorded_at TEXT NOT NULL, effective_at TEXT NOT NULL,
 scheduler_state TEXT NOT NULL CHECK(scheduler_state IN ('pending','enabled','disabled','unknown')),
 evidence_path TEXT, evidence_sha256 TEXT, note TEXT NOT NULL, origin TEXT NOT NULL
)
"""
SCHEMA = """
CREATE TABLE IF NOT EXISTS attempts (
 attempt_id TEXT PRIMARY KEY, trigger_kind TEXT NOT NULL,
 slot_key TEXT UNIQUE, slot_date TEXT, slot_time TEXT, scheduled_at TEXT,
 status TEXT NOT NULL CHECK(status IN ('running','complete','partial','failed','missed')),
 owner_label TEXT NOT NULL, started_at TEXT, finished_at TEXT, recorded_at TEXT NOT NULL,
 started_late_seconds REAL, run_id TEXT UNIQUE, snapshot_path TEXT, snapshot_sha256 TEXT,
 reason TEXT, evidence_json TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS active_lock (
 lock_id INTEGER PRIMARY KEY CHECK(lock_id=1), attempt_id TEXT NOT NULL UNIQUE REFERENCES attempts(attempt_id),
 owner_token TEXT NOT NULL, lease_expires_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
 event_id INTEGER PRIMARY KEY, attempt_id TEXT NOT NULL REFERENCES attempts(attempt_id),
 recorded_at TEXT NOT NULL, event_type TEXT NOT NULL, payload_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS configuration (
 config_id INTEGER PRIMARY KEY CHECK(config_id=1), scheduler_state TEXT NOT NULL,
 recorded_at TEXT NOT NULL, enabled_at TEXT, evidence_path TEXT, note TEXT NOT NULL
);
"""


class CollectionLogError(ValueError):
    pass


def _now():
    return datetime.now(timezone.utc)


def _iso(moment):
    return moment.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _parse(value):
    moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if moment.tzinfo is None:
        raise CollectionLogError("Timezone-aware evidence is required")
    return moment


def _json(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def _path(state_dir):
    return Path(state_dir).resolve() / "journal.sqlite3"


def _connect(state_dir, write=False):
    path = _path(state_dir)
    if not write and not path.is_file():
        return None
    if write:
        path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(path, timeout=10, isolation_level=None)
    else:
        connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=10, isolation_level=None)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA busy_timeout=10000")
    version = connection.execute("PRAGMA user_version").fetchone()[0]
    if version not in (0, 1, SCHEMA_VERSION):
        connection.close()
        raise CollectionLogError("Unsupported collection journal version")
    if write:
        connection.execute("PRAGMA journal_mode=DELETE")
        connection.execute("PRAGMA synchronous=FULL")
        if version < SCHEMA_VERSION:
            # Serialize first initialization too. A concurrent initializer may
            # have completed between the original version read and this lock.
            connection.execute("BEGIN IMMEDIATE")
            try:
                locked_version = connection.execute("PRAGMA user_version").fetchone()[0]
                if locked_version == 0:
                    if connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchone():
                        raise CollectionLogError("Refusing to initialize an unrelated existing SQLite file")
                    for statement in SCHEMA.split(";"):
                        if statement.strip():
                            connection.execute(statement)
                if locked_version < SCHEMA_VERSION:
                    connection.execute(SCHEDULER_SCHEMA)
                    if locked_version == 1:
                        previous = connection.execute("SELECT * FROM configuration WHERE config_id=1").fetchone()
                        if previous:
                            connection.execute("INSERT INTO scheduler_events(recorded_at,effective_at,scheduler_state,evidence_path,note,origin) VALUES (?,?,?,?,?,?)",
                                               (previous["recorded_at"], previous["enabled_at"] or previous["recorded_at"], previous["scheduler_state"],
                                                previous["evidence_path"], previous["note"], "legacy_v1_current_state_only"))
                    connection.execute("PRAGMA user_version=2")
                connection.commit()
            except BaseException:
                connection.rollback()
                connection.close()
                raise
    else:
        connection.execute("PRAGMA query_only=ON")
        if version not in (1, SCHEMA_VERSION):
            connection.close()
            raise CollectionLogError("Uninitialized journal; no read-time repair was attempted")
    return connection


@contextmanager
def _transaction(state_dir):
    connection = _connect(state_dir, write=True)
    try:
        connection.execute("BEGIN IMMEDIATE")
        yield connection
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()


def _event(connection, attempt_id, kind, payload, moment):
    connection.execute("INSERT INTO events(attempt_id,recorded_at,event_type,payload_json) VALUES (?,?,?,?)",
                       (attempt_id, _iso(moment), kind, _json(payload)))


def _public(row, lock=None, moment=None):
    result = dict(row)
    result["evidence"] = json.loads(result.pop("evidence_json"))
    declared_shop = result["evidence"].get("shop_id") if isinstance(result["evidence"], dict) else None
    result["shop_id"] = declared_shop if isinstance(declared_shop, str) and SHOP_ID.fullmatch(declared_shop) else None
    result["timezone"] = "Asia/Shanghai"
    result["lease_expires_at"] = lock["lease_expires_at"] if lock else None
    result["lock_expired"] = bool(lock and _parse(lock["lease_expires_at"]) < (moment or _now()))
    result["recovery_required"] = result["status"] == "running" and result["lock_expired"]
    return result


def _atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, prefix=path.name + ".", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def _publish_attempt(state_dir, attempt_id):
    connection = _connect(state_dir)
    try:
        # Keep the shared read transaction through atomic publication. A later
        # finish/renew cannot commit while an older JSON view is being saved.
        connection.execute("BEGIN")
        row = connection.execute("SELECT * FROM attempts WHERE attempt_id=?", (attempt_id,)).fetchone()
        lock = connection.execute("SELECT * FROM active_lock WHERE attempt_id=?", (attempt_id,)).fetchone()
        result = _public(row, lock)
        result["events"] = [{"recorded_at": event["recorded_at"], "event_type": event["event_type"], "payload": json.loads(event["payload_json"])}
                            for event in connection.execute("SELECT * FROM events WHERE attempt_id=? ORDER BY event_id", (attempt_id,))]
        path = _path(state_dir).parent / (attempt_id + ".json")
        try:
            _atomic_json(path, result)
            result["attempt_json"] = str(path)
        except OSError as error:
            # The DB transaction already committed. Return the true durable state
            # and owner token even if a regenerable JSON view cannot save.
            result["json_export_error"] = str(error)
        return result
    finally:
        connection.close()


def _slot(slot_date, slot_time):
    if not isinstance(slot_date, str):
        raise CollectionLogError("Scheduled attempts require --slot-date YYYY-MM-DD")
    if slot_time not in SLOTS:
        raise CollectionLogError("Only Asia/Shanghai 08:00 and 20:00 slots are defined")
    day = date.fromisoformat(slot_date)
    scheduled = datetime.combine(day, time.fromisoformat(slot_time), tzinfo=TZ)
    return day.isoformat() + "/" + slot_time, scheduled


def _lease_minutes(value):
    if not isinstance(value, int) or not 1 <= value <= 180:
        raise CollectionLogError("Lease must be 1–180 minutes; renew during long collection")
    return value


def begin_attempt(state_dir=DEFAULT_STATE_DIR, *, trigger_kind="scheduled", slot_date=None, slot_time=None, owner_label="local-collector", lease_minutes=60, shop_id=None):
    if shop_id is not None and (not isinstance(shop_id, str) or not SHOP_ID.fullmatch(shop_id)):
        raise CollectionLogError("shop_id must be shop_ followed by 24 lowercase hexadecimal characters")
    moment = _now()
    lease_minutes = _lease_minutes(lease_minutes)
    if trigger_kind not in ("scheduled", "manual") or not str(owner_label).strip():
        raise CollectionLogError("Begin requires scheduled/manual and a nonempty owner label")
    key, scheduled = (None, None)
    if trigger_kind == "scheduled":
        key, scheduled = _slot(slot_date, slot_time)
        if moment < scheduled:
            return {"acquired": False, "outcome": "not_due", "scheduled_at": _iso(scheduled)}
    elif slot_date is not None or slot_time is not None:
        raise CollectionLogError("Manual attempts must not claim a scheduled slot")
    token = secrets.token_urlsafe(32)
    attempt_id = "attempt_" + uuid.uuid4().hex
    with _transaction(state_dir) as connection:
        if key:
            existing = connection.execute("SELECT * FROM attempts WHERE slot_key=?", (key,)).fetchone()
            if existing:
                return {"acquired": False, "outcome": "duplicate_slot", "attempt": _public(existing)}
        missed = bool(scheduled and moment > scheduled + timedelta(minutes=START_WINDOW_MINUTES))
        lock = connection.execute("SELECT * FROM active_lock WHERE lock_id=1").fetchone()
        if lock and not missed:
            return {"acquired": False, "outcome": "collection_locked", "active_attempt_id": lock["attempt_id"],
                    "lease_expires_at": lock["lease_expires_at"], "recovery_required": _parse(lock["lease_expires_at"]) < moment}
        status = "missed" if missed else "running"
        reason = "start_window_elapsed_no_historical_collection_fabricated" if missed else None
        evidence = {"shop_id": shop_id} if shop_id is not None else {}
        connection.execute("INSERT INTO attempts(attempt_id,trigger_kind,slot_key,slot_date,slot_time,scheduled_at,status,owner_label,started_at,finished_at,recorded_at,started_late_seconds,reason,evidence_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                           (attempt_id, trigger_kind, key, slot_date, slot_time, _iso(scheduled) if scheduled else None, status, owner_label,
                            None if missed else _iso(moment), _iso(moment) if missed else None, _iso(moment),
                            (moment - scheduled).total_seconds() if scheduled else None, reason, _json(evidence)))
        if not missed:
            connection.execute("INSERT INTO active_lock(lock_id,attempt_id,owner_token,lease_expires_at) VALUES (1,?,?,?)",
                               (attempt_id, token, _iso(moment + timedelta(minutes=lease_minutes))))
        _event(connection, attempt_id, "missed" if missed else "begin", {"owner_label": owner_label, "scheduled_at": _iso(scheduled) if scheduled else None, **evidence}, moment)
    result = {"acquired": not missed, "outcome": status, "attempt": _publish_attempt(state_dir, attempt_id)}
    if not missed:
        result["owner_token"] = token
    return result


def renew_attempt(state_dir, attempt_id, owner_token, lease_minutes=60):
    moment = _now()
    with _transaction(state_dir) as connection:
        lock = connection.execute("SELECT * FROM active_lock WHERE attempt_id=?", (attempt_id,)).fetchone()
        if not lock or not secrets.compare_digest(lock["owner_token"], owner_token):
            raise CollectionLogError("Owner token does not match the current lock")
        expires = _iso(moment + timedelta(minutes=_lease_minutes(lease_minutes)))
        connection.execute("UPDATE active_lock SET lease_expires_at=? WHERE attempt_id=?", (expires, attempt_id))
        _event(connection, attempt_id, "renew", {"lease_expires_at": expires, "owner_token_verified": True}, moment)
    return _publish_attempt(state_dir, attempt_id)


def _run_evidence(data_dir, run_id, snapshot_path):
    from .store import _connect_readonly
    from .validation import validate_store
    path = Path(snapshot_path).resolve()
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    connection = _connect_readonly(data_dir)
    try:
        row = connection.execute("SELECT run_id,shop_id,status,end_boundary_observed,snapshot_sha256,snapshot_bytes,observed_from,observed_to FROM runs WHERE run_id=?", (run_id,)).fetchone()
        if not row or bytes(row["snapshot_bytes"]) != raw or row["snapshot_sha256"] != digest:
            raise CollectionLogError("Run ID and original snapshot bytes/SHA do not agree")
        result = {key: row[key] for key in ("run_id", "shop_id", "status", "end_boundary_observed", "observed_from", "observed_to")}
    finally:
        connection.close()
    validation = validate_store(data_dir, run_id)
    if not validation["ok"]:
        raise CollectionLogError("Stored evidence failed validation; do not record a successful collection")
    result.update(snapshot_path=str(path), snapshot_sha256=digest, validation=validation)
    return result


def finish_attempt(state_dir, attempt_id, owner_token, *, status, run_id=None, snapshot_path=None, data_dir=None, reason=None, checkpoint_path=None):
    if status not in ("complete", "partial", "failed"):
        raise CollectionLogError("Finish status must be complete, partial or failed; no 'unchanged' shortcut exists")
    if status in ("partial", "failed") and not str(reason or "").strip():
        raise CollectionLogError("Partial/failed attempts require their actual reason")
    if status in ("complete", "partial") and not (run_id and snapshot_path and data_dir):
        raise CollectionLogError("Complete/partial requires run_id, snapshot and data_dir evidence")
    if any((run_id, snapshot_path, data_dir)) and not all((run_id, snapshot_path, data_dir)):
        raise CollectionLogError("Supply run_id, snapshot and data_dir together")
    evidence = _run_evidence(data_dir, run_id, snapshot_path) if run_id else {}
    if checkpoint_path:
        path = Path(checkpoint_path).resolve()
        if not path.exists():
            raise CollectionLogError("Checkpoint evidence does not exist")
        evidence["checkpoint_path"] = str(path)
    if status == "complete" and (evidence["status"] != "complete" or not evidence["end_boundary_observed"]):
        raise CollectionLogError("A complete attempt needs a complete run with observed end boundary")
    if status == "partial" and evidence["status"] == "complete" and evidence["end_boundary_observed"]:
        raise CollectionLogError("Partial status contradicts the supplied complete run")
    moment = _now()
    with _transaction(state_dir) as connection:
        attempt = connection.execute("SELECT * FROM attempts WHERE attempt_id=?", (attempt_id,)).fetchone()
        if not attempt:
            raise CollectionLogError("Unknown attempt")
        prior_evidence = json.loads(attempt["evidence_json"])
        declared_shop = prior_evidence.get("shop_id") if isinstance(prior_evidence, dict) else None
        if declared_shop is not None and (not isinstance(declared_shop, str) or not SHOP_ID.fullmatch(declared_shop)):
            raise CollectionLogError("Stored declared shop identity is invalid; no finish was recorded")
        if run_id and declared_shop is not None and evidence.get("shop_id") != declared_shop:
            raise CollectionLogError("Run shop_id conflicts with the attempt's declared shop_id")
        if declared_shop is not None:
            evidence["shop_id"] = declared_shop
        if attempt["status"] != "running":
            same = attempt["status"] == status and attempt["run_id"] == run_id and attempt["snapshot_sha256"] == evidence.get("snapshot_sha256") and attempt["reason"] == reason
            if not same:
                raise CollectionLogError("Terminal attempt is immutable; supplied finish conflicts")
            return {"duplicate": True, "attempt": _public(attempt)}
        lock = connection.execute("SELECT * FROM active_lock WHERE attempt_id=?", (attempt_id,)).fetchone()
        if not lock or not secrets.compare_digest(lock["owner_token"], owner_token):
            raise CollectionLogError("Owner token does not match the current lock")
        if run_id and (_parse(evidence["observed_from"]) < _parse(attempt["started_at"]) or _parse(evidence["observed_to"]) > moment):
            raise CollectionLogError("Observation window predates begin or ends in the future; old snapshots cannot satisfy a new attempt")
        connection.execute("UPDATE attempts SET status=?,finished_at=?,run_id=?,snapshot_path=?,snapshot_sha256=?,reason=?,evidence_json=? WHERE attempt_id=?",
                           (status, _iso(moment), run_id, evidence.get("snapshot_path"), evidence.get("snapshot_sha256"), reason, _json(evidence), attempt_id))
        connection.execute("DELETE FROM active_lock WHERE attempt_id=? AND owner_token=?", (attempt_id, owner_token))
        _event(connection, attempt_id, "finish", {"status": status, "run_id": run_id, "reason": reason, "owner_token_verified": True}, moment)
    return {"duplicate": False, "attempt": _publish_attempt(state_dir, attempt_id)}


def recover_attempt(state_dir, attempt_id, *, reason, confirm_abandon=False, owner_token=None):
    if not confirm_abandon or not str(reason or "").strip():
        raise CollectionLogError("Explicit --confirm-abandon and a recovery reason are required")
    moment = _now()
    with _transaction(state_dir) as connection:
        lock = connection.execute("SELECT * FROM active_lock WHERE attempt_id=?", (attempt_id,)).fetchone()
        if not lock:
            raise CollectionLogError("No matching active lock; no recovery performed")
        owner_verified = bool(owner_token and secrets.compare_digest(lock["owner_token"], owner_token))
        expired = _parse(lock["lease_expires_at"]) < moment
        if not expired and not owner_verified:
            raise CollectionLogError("Unexpired owner must supply its token; the active lock was not removed")
        note = "explicit_recovery_abandonment: " + reason
        connection.execute("UPDATE attempts SET status='failed',finished_at=?,reason=? WHERE attempt_id=? AND status='running'", (_iso(moment), note, attempt_id))
        connection.execute("DELETE FROM active_lock WHERE attempt_id=?", (attempt_id,))
        _event(connection, attempt_id, "explicit_recovery", {"reason": reason, "expired": expired, "owner_token_verified": owner_verified}, moment)
    return _publish_attempt(state_dir, attempt_id)


def record_manual_run(state_dir, *, data_dir, run_id, snapshot_path, note):
    if not str(note or "").strip():
        raise CollectionLogError("Historical manual evidence needs a truthful origin note")
    evidence = _run_evidence(data_dir, run_id, snapshot_path)
    moment = _now()
    if _parse(evidence["observed_to"]) > moment:
        raise CollectionLogError("Historical observation window cannot end in the future")
    attempt_id = "attempt_" + uuid.uuid4().hex
    with _transaction(state_dir) as connection:
        existing = connection.execute("SELECT * FROM attempts WHERE run_id=?", (run_id,)).fetchone()
        if existing:
            return {"duplicate": True, "attempt": _public(existing)}
        status = "complete" if evidence["status"] == "complete" and evidence["end_boundary_observed"] else "partial"
        connection.execute("INSERT INTO attempts(attempt_id,trigger_kind,status,owner_label,started_at,finished_at,recorded_at,run_id,snapshot_path,snapshot_sha256,reason,evidence_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                           (attempt_id, "manual_history", status, "historical_manual_evidence", evidence["observed_from"], evidence["observed_to"], _iso(moment),
                            run_id, evidence["snapshot_path"], evidence["snapshot_sha256"], note, _json(evidence)))
        _event(connection, attempt_id, "record_manual_history", {"note": note, "never_assigned_to_slot": True}, moment)
    return {"duplicate": False, "attempt": _publish_attempt(state_dir, attempt_id)}


def configure_scheduler(state_dir, *, scheduler_state, note, evidence_path=None):
    if scheduler_state not in ("pending", "enabled", "disabled", "unknown") or not str(note).strip():
        raise CollectionLogError("Specify pending/enabled/disabled/unknown and an evidence note")
    if scheduler_state == "enabled" and (not evidence_path or not Path(evidence_path).is_file()):
        raise CollectionLogError("Enabled requires an existing external scheduler connection evidence file; this module does not activate a scheduler")
    moment = _now()
    evidence_sha256 = hashlib.sha256(Path(evidence_path).read_bytes()).hexdigest() if evidence_path else None
    with _transaction(state_dir) as connection:
        previous = connection.execute("SELECT * FROM configuration WHERE config_id=1").fetchone()
        last_event = connection.execute("SELECT * FROM scheduler_events ORDER BY event_id DESC LIMIT 1").fetchone()
        if last_event and moment < _parse(last_event["effective_at"]):
            raise CollectionLogError("Configuration clock moved backwards; no historical state was overwritten")
        enabled_at = (previous["enabled_at"] if previous and previous["scheduler_state"] == "enabled" else _iso(moment)) if scheduler_state == "enabled" else None
        connection.execute("INSERT INTO configuration(config_id,scheduler_state,recorded_at,enabled_at,evidence_path,note) VALUES (1,?,?,?,?,?) ON CONFLICT(config_id) DO UPDATE SET scheduler_state=excluded.scheduler_state,recorded_at=excluded.recorded_at,enabled_at=excluded.enabled_at,evidence_path=excluded.evidence_path,note=excluded.note",
                           (scheduler_state, _iso(moment), enabled_at, str(Path(evidence_path).resolve()) if evidence_path else None, note))
        connection.execute("INSERT INTO scheduler_events(recorded_at,effective_at,scheduler_state,evidence_path,evidence_sha256,note,origin) VALUES (?,?,?,?,?,?,?)",
                           (_iso(moment), _iso(moment), scheduler_state, str(Path(evidence_path).resolve()) if evidence_path else None,
                            evidence_sha256, note, "configuration_change"))
    return get_status(state_dir)


def _scheduler_history(connection, configuration):
    if connection.execute("PRAGMA user_version").fetchone()[0] >= 2:
        return [dict(row) for row in connection.execute("SELECT * FROM scheduler_events ORDER BY event_id")]
    if not configuration.get("recorded_at"):
        return []
    return [{"event_id": None, "recorded_at": configuration["recorded_at"],
             "effective_at": configuration.get("enabled_at") or configuration["recorded_at"],
             "scheduler_state": configuration["scheduler_state"], "evidence_path": configuration.get("evidence_path"),
             "evidence_sha256": None, "note": configuration["note"], "origin": "legacy_v1_current_state_only"}]


def _enabled_intervals(history):
    intervals, active = [], None
    for event in history:
        if event["scheduler_state"] == "enabled" and active is None:
            active = {"enabled_at": event["effective_at"], "disabled_at": None, "start_event_id": event["event_id"],
                      "end_event_id": None, "origin": event["origin"]}
        elif event["scheduler_state"] != "enabled" and active is not None:
            active.update(disabled_at=event["effective_at"], end_event_id=event["event_id"])
            intervals.append(active)
            active = None
    if active is not None:
        intervals.append(active)
    return intervals


def get_status(state_dir=DEFAULT_STATE_DIR, slot_date=None):
    moment = _now()
    day = slot_date or moment.astimezone(TZ).date().isoformat()
    date.fromisoformat(day)
    connection = _connect(state_dir)
    configuration = {"scheduler_state": "unknown", "enabled_at": None, "note": "No verified scheduler connection is recorded"}
    attempts, lock, history = [], None, []
    if connection:
        try:
            connection.execute("BEGIN")
            row = connection.execute("SELECT * FROM configuration WHERE config_id=1").fetchone()
            if row:
                configuration = dict(row)
            history = _scheduler_history(connection, configuration)
            lock = connection.execute("SELECT * FROM active_lock WHERE lock_id=1").fetchone()
            attempts = [_public(row, lock if lock and lock["attempt_id"] == row["attempt_id"] else None, moment)
                        for row in connection.execute("SELECT * FROM attempts ORDER BY recorded_at DESC,attempt_id")]
        finally:
            connection.close()
    slots = []
    intervals = _enabled_intervals(history)
    if connection is not None:
        by_slot = {attempt["slot_key"]: attempt for attempt in attempts if attempt["slot_key"]}
        for slot_time in SLOTS:
            key, scheduled = _slot(day, slot_time)
            attempt = by_slot.get(key)
            enabled_for_slot = any(_parse(interval["enabled_at"]) <= scheduled and
                                   (interval["disabled_at"] is None or scheduled < _parse(interval["disabled_at"])) for interval in intervals)
            state_at_slot = "unknown"
            for event in history:
                if _parse(event["effective_at"]) <= scheduled:
                    state_at_slot = event["scheduler_state"]
            missed = bool(enabled_for_slot and moment > scheduled + timedelta(minutes=START_WINDOW_MINUTES))
            slots.append({"slot_key": key, "slot_date": day, "slot_time": slot_time, "scheduled_at": _iso(scheduled),
                          "status": attempt["status"] if attempt else ("missed" if missed else "planned"),
                          "attempt_id": attempt["attempt_id"] if attempt else None, "run_id": attempt["run_id"] if attempt else None,
                          "reason": attempt["reason"] if attempt else ("no_start_record_within_window" if missed else (None if enabled_for_slot else "scheduler_connection_not_verified_for_this_slot")),
                          "due": bool(not attempt and enabled_for_slot and configuration["scheduler_state"] == "enabled" and scheduled <= moment <= scheduled + timedelta(minutes=START_WINDOW_MINUTES)),
                          "scheduler_state": configuration["scheduler_state"], "scheduler_state_at_slot": state_at_slot,
                          "enabled_for_slot": enabled_for_slot, "timezone": "Asia/Shanghai"})
    return {"journal_exists": connection is not None, "generated_at": _iso(moment), "timezone": "Asia/Shanghai", "slot_times": list(SLOTS),
            "start_window_minutes": START_WINDOW_MINUTES, "scheduler_state": configuration["scheduler_state"], "scheduler": configuration,
            "scheduler_created_by_this_module": False, "date": day, "slots": slots, "attempts": attempts,
            "scheduler_history": history, "enabled_intervals": intervals,
            "first_enabled_at": intervals[0]["enabled_at"] if intervals else None,
            "history_limitations": ["v1 retained only its then-current configuration; earlier overwritten transitions cannot be reconstructed"] if any(event["origin"] == "legacy_v1_current_state_only" for event in history) else [],
            "active_lock": {"attempt_id": lock["attempt_id"], "lease_expires_at": lock["lease_expires_at"], "expired": _parse(lock["lease_expires_at"]) < moment} if lock else None}


def _journal_summary(connection):
    """Validate and hash all authoritative rows, including the retained lock."""
    version = connection.execute("PRAGMA user_version").fetchone()[0]
    if version not in (1, SCHEMA_VERSION):
        raise CollectionLogError("Unsupported journal backup version")
    if [row[0] for row in connection.execute("PRAGMA integrity_check")] != ["ok"]:
        raise CollectionLogError("Journal integrity_check failed")
    if connection.execute("PRAGMA foreign_key_check").fetchall():
        raise CollectionLogError("Journal foreign_key_check failed")
    tables = [("attempts", "attempt_id"), ("active_lock", "lock_id"), ("events", "event_id"), ("configuration", "config_id")]
    if version >= 2:
        tables.append(("scheduler_events", "event_id"))
    payload = {"version": version, "schema": [dict(row) for row in connection.execute("SELECT type,name,tbl_name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name")]}
    counts = {}
    for table, order in tables:  # Both identifiers come from this fixed allowlist.
        rows = [dict(row) for row in connection.execute(f"SELECT * FROM {table} ORDER BY {order}")]
        payload[table] = rows
        counts[table] = len(rows)
    running = {row["attempt_id"] for row in payload["attempts"] if row["status"] == "running"}
    locked = {row["attempt_id"] for row in payload["active_lock"]}
    if running != locked:
        raise CollectionLogError("Journal running-attempt and owner-lock records disagree")
    return {"schema_version": version, "logical_sha256": hashlib.sha256(_json(payload).encode("utf-8")).hexdigest(), "counts": counts,
            "integrity_check": "ok", "foreign_key_check": "ok", "active_locks_preserved": len(locked)}


def backup_journal(state_dir, output):
    """Read-only consistent backup, verified through a second restored copy.

    The output must be absent or empty. Only this journal and regenerated JSON
    views are saved. Active owner locks are preserved, never reset by restoration.
    A completed manifest is published last; failure never declares success.
    """
    source_path = _path(state_dir)
    if not source_path.is_file():
        raise CollectionLogError("No existing journal to back up; source was not created")
    output = Path(output).resolve()
    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        raise CollectionLogError("Backup output must be absent or empty; existing data is never overwritten")
    connection = _connect(state_dir)
    try:
        if connection.execute("PRAGMA journal_mode").fetchone()[0].lower() != "delete":
            raise CollectionLogError("Journal backup requires its configured DELETE mode; WAL is not converted")
        connection.execute("BEGIN")
        source_summary = _journal_summary(connection)  # Acquires a shared snapshot lock.
        before = hashlib.sha256(source_path.read_bytes()).hexdigest()
        output.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=".journal_backup_", dir=output) as temporary:
            stage = Path(temporary)
            backup_path = stage / "journal.sqlite3"
            destination = sqlite3.connect(backup_path)
            try:
                connection.backup(destination)
            finally:
                destination.close()
            copied = _connect(stage)
            try:
                copied.execute("BEGIN")
                copied_summary = _journal_summary(copied)
                if copied_summary != source_summary:
                    raise CollectionLogError("Backup rows do not match the source snapshot")
                # Reopen a separate restored DB, proving the backup is usable as
                # journal state. Never point a restore at a user's active state.
                with tempfile.TemporaryDirectory(prefix="restore_check_", dir=stage) as restore_dir:
                    restored_path = Path(restore_dir) / "journal.sqlite3"
                    target = sqlite3.connect(restored_path)
                    try:
                        copied.backup(target)
                    finally:
                        target.close()
                    restored = _connect(restore_dir)
                    try:
                        restored_summary = _journal_summary(restored)
                    finally:
                        restored.close()
                    if restored_summary != source_summary:
                        raise CollectionLogError("Restored journal did not preserve the complete source state")
                    restored_status = get_status(restore_dir)
                    if len(restored_status["attempts"]) != source_summary["counts"]["attempts"]:
                        raise CollectionLogError("Restored journal cannot reproduce attempt status")
            finally:
                copied.close()
            status = get_status(stage)
            _atomic_json(stage / "status.json", status)
            for attempt in status["attempts"]:
                projection = _publish_attempt(stage, attempt["attempt_id"])
                if projection.get("json_export_error"):
                    raise CollectionLogError("Backup attempt JSON projection failed: " + projection["json_export_error"])
            after = hashlib.sha256(source_path.read_bytes()).hexdigest()
            if before != after:
                raise CollectionLogError("Source journal bytes changed while backing up")
            files = {path.name: {"sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "byte_count": path.stat().st_size}
                     for path in stage.iterdir() if path.is_file()}
            manifest = {"ok": True, "created_at": _iso(_now()), "source_path": str(source_path),
                        "source_sha256_before": before, "source_sha256_after": after, "source_unchanged": True,
                        "source_open_mode": "ro/query_only", "source_summary": source_summary,
                        "restore_verification": {"ok": True, **restored_summary}, "files": files,
                        "lock_policy": "Active locks and tokens remain inside journal.sqlite3; explicit owner verification/recovery is still required after restore"}
            # Publish only our own staged filenames; manifest follows all files.
            for name in files:
                if (output / name).exists():
                    raise CollectionLogError("Backup target changed during creation; refusing overwrite")
                (stage / name).rename(output / name)
            _atomic_json(output / "manifest.json", manifest)
            return manifest
    finally:
        connection.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", type=Path, default=DEFAULT_STATE_DIR)
    sub = parser.add_subparsers(dest="command", required=True)
    status = sub.add_parser("status"); status.add_argument("--date")
    begin = sub.add_parser("begin")
    begin_kind = begin.add_mutually_exclusive_group()
    begin_kind.add_argument("--kind", choices=("scheduled", "manual"), default="scheduled")
    begin_kind.add_argument("--manual", action="store_true")
    begin.add_argument("--slot-date"); begin.add_argument("--slot", choices=SLOTS)
    begin.add_argument("--owner-label", required=True); begin.add_argument("--lease-minutes", type=int, default=60)
    begin.add_argument("--shop-id", help="Verified stable shop_<24 lowercase hex>; optional for legacy/global attempts")
    renew = sub.add_parser("renew"); renew.add_argument("--attempt-id", required=True); renew.add_argument("--owner-token", required=True); renew.add_argument("--lease-minutes", type=int, default=60)
    finish = sub.add_parser("finish")
    finish.add_argument("--attempt-id", required=True); finish.add_argument("--owner-token", required=True)
    finish.add_argument("--status", choices=("complete", "partial", "failed"), required=True)
    finish.add_argument("--run-id"); finish.add_argument("--snapshot", type=Path); finish.add_argument("--data-dir", type=Path); finish.add_argument("--reason"); finish.add_argument("--checkpoint", type=Path)
    recover = sub.add_parser("recover")
    recover.add_argument("--attempt-id", required=True); recover.add_argument("--owner-token"); recover.add_argument("--reason", required=True); recover.add_argument("--confirm-abandon", action="store_true")
    manual = sub.add_parser("record-manual")
    manual.add_argument("--data-dir", type=Path, required=True); manual.add_argument("--run-id", required=True); manual.add_argument("--snapshot", type=Path, required=True); manual.add_argument("--note", required=True)
    config = sub.add_parser("configure")
    config.add_argument("--scheduler-state", choices=("pending", "enabled", "disabled", "unknown"), required=True); config.add_argument("--note", required=True); config.add_argument("--evidence", type=Path)
    backup = sub.add_parser("backup"); backup.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "status":
            result = get_status(args.state_dir, args.date)
        elif args.command == "begin":
            result = begin_attempt(args.state_dir, trigger_kind="manual" if args.manual else args.kind, slot_date=args.slot_date, slot_time=args.slot, owner_label=args.owner_label, lease_minutes=args.lease_minutes, shop_id=args.shop_id)
        elif args.command == "renew":
            result = renew_attempt(args.state_dir, args.attempt_id, args.owner_token, args.lease_minutes)
        elif args.command == "finish":
            result = finish_attempt(args.state_dir, args.attempt_id, args.owner_token, status=args.status, run_id=args.run_id, snapshot_path=args.snapshot, data_dir=args.data_dir, reason=args.reason, checkpoint_path=args.checkpoint)
        elif args.command == "recover":
            result = recover_attempt(args.state_dir, args.attempt_id, owner_token=args.owner_token, reason=args.reason, confirm_abandon=args.confirm_abandon)
        elif args.command == "record-manual":
            result = record_manual_run(args.state_dir, data_dir=args.data_dir, run_id=args.run_id, snapshot_path=args.snapshot, note=args.note)
        elif args.command == "backup":
            result = backup_journal(args.state_dir, args.output)
        else:
            result = configure_scheduler(args.state_dir, scheduler_state=args.scheduler_state, note=args.note, evidence_path=args.evidence)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, OSError, sqlite3.Error) as error:
        print(json.dumps({"ok": False, "error": type(error).__name__, "message": str(error)}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
