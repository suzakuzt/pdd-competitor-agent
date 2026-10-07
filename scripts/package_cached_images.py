"""Package exact-URL, verified local cached images for a separately sealed snapshot.

No network, database write, snapshot construction, or image-policy retry is done.
Outputs are new files; acceptance is published last after source checks.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import importlib
import importlib.util
import json
from pathlib import Path
import shutil
import sqlite3
import sys
import tempfile
import zipfile
import uuid
if __package__:
    from .image_cache_review import cache_reuse_policy, validate_restriction_review
else:
    from image_cache_review import cache_reuse_policy, validate_restriction_review

sys.dont_write_bytecode = True
EXTENSIONS = {"image/png": "png", "image/jpeg": "jpg", "image/gif": "gif", "image/webp": "webp"}
OUTPUT_NAMES = ("images.zip", "image_queue.json", "missing_urls.json", "cache_acceptance.json")


def sha_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for part in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(part)
    return digest.hexdigest()


def json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")


def load_store(project):
    """Load the chosen project's complete package without sharing module caches.

    store.import_snapshot uses relative imports for the independent capture gate.
    A unique package namespace preserves those imports and prevents an already
    imported pdd_monitor from silently selecting code from another project.
    """
    package_dir = Path(project).resolve(strict=True) / "pdd_monitor"
    name = "_pdd_image_store_" + uuid.uuid4().hex
    spec = importlib.util.spec_from_file_location(
        name, package_dir / "__init__.py",
        submodule_search_locations=[str(package_dir)],
    )
    package = importlib.util.module_from_spec(spec)
    sys.modules[name] = package
    try:
        spec.loader.exec_module(package)
        return importlib.import_module(".store", name)
    except BaseException:
        for key in tuple(sys.modules):
            if key == name or key.startswith(name + "."):
                sys.modules.pop(key, None)
        raise


def check_sidecars(paths):
    for path in paths:
        for suffix in ("-wal", "-shm", "-journal"):
            if Path(str(path) + suffix).exists():
                raise ValueError(f"SQLite sidecar present; stop for consistency review: {path.name}{suffix}")


def read_cache(data_dir, store):
    """Freeze both DELETE-mode files using read locks and verify pre/post bytes."""
    paths = [data_dir / name for name in ("monitor.sqlite3", "images.sqlite3")]
    if not all(path.is_file() for path in paths):
        raise ValueError("Both existing monitor.sqlite3 and images.sqlite3 are required")
    check_sidecars(paths)
    before = {path.name: sha_file(path) for path in paths}
    candidates, blocked, assets = defaultdict(list), defaultdict(list), {}
    with closing(sqlite3.connect(paths[0].as_uri() + "?mode=ro", uri=True)) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("ATTACH DATABASE ? AS images", (paths[1].as_uri() + "?mode=ro",))
        connection.execute("PRAGMA query_only=ON")
        for schema in ("main", "images"):
            if connection.execute(f"PRAGMA {schema}.journal_mode").fetchone()[0].lower() != "delete":
                raise ValueError("Only DELETE journal mode is supported")
        connection.execute("BEGIN")
        connection.execute("SELECT COUNT(*) FROM observations").fetchone()
        connection.execute("SELECT COUNT(*) FROM images.assets").fetchone()
        # A writer between the pre-lock hash and acquisition of both locks is rejected.
        if before != {path.name: sha_file(path) for path in paths}:
            raise ValueError("Business database changed while acquiring read locks")
        for item in connection.execute("SELECT sha256,mime,byte_count,data FROM images.assets"):
            data = bytes(item["data"])
            if (not 0 < len(data) <= store.MAX_IMAGE_BYTES or len(data) != item["byte_count"]
                    or hashlib.sha256(data).hexdigest() != item["sha256"]
                    or store._image_mime(data) != item["mime"]):
                raise ValueError("Existing BLOB size, SHA256 or MIME failed verification")
            assets[item["sha256"]] = {"data": data, "mime": item["mime"], "byte_count": len(data)}
        rows = connection.execute("""
            SELECT l.run_id,l.observation_id,l.source_url,l.asset_sha256,l.archive_path,l.manifest_json,
                   o.run_id AS observation_run,o.view_order,o.image_url,o.title,r.snapshot_sha256,r.source_url AS shop_source_url,
                   t.run_id AS task_run,t.source_url AS task_url,t.asset_sha256 AS task_sha,t.status
            FROM images.source_links l
            LEFT JOIN observations o ON o.observation_id=l.observation_id
            LEFT JOIN runs r ON r.run_id=l.run_id
            LEFT JOIN image_tasks t ON t.observation_id=l.observation_id
        """).fetchall()
        for item in rows:
            if (item["run_id"] != item["observation_run"] or item["run_id"] != item["task_run"]
                    or not item["source_url"] or item["source_url"] != item["image_url"]
                    or item["source_url"] != item["task_url"] or item["asset_sha256"] != item["task_sha"]
                    or item["status"] != "saved" or item["asset_sha256"] not in assets):
                raise ValueError("Existing source link contradicts observation, task or BLOB")
            manifest_item = json.loads(item['manifest_json'])
            variant = store.validate_image_variant(manifest_item, original_url=item['image_url'], title=item['title'], snapshot_sha=item['snapshot_sha256'])
            # A display variant has different acquisition bytes. Never promote
            # it into the exact-original-URL cache, even on a later day.
            if variant is not None:
                if manifest_item.get('historical_restriction_review') is not None:
                    raise ValueError('Display variants cannot approve original-URL cache reuse')
                continue
            review = validate_restriction_review(manifest_item, original_url=item['image_url'],
                title=item['title'], snapshot_sha=item['snapshot_sha256'],
                view_order=item['view_order'], shop_source_url=item['shop_source_url'])
            candidates[item["source_url"]].append({
                "run_id": item["run_id"], "observation_id": item["observation_id"],
                "view_order": item["view_order"], "source_url": item["source_url"],
                "asset_sha256": item["asset_sha256"], "archive_path": item["archive_path"],
                **({'historical_restriction_review': review} if review else {})})
        missing_links = connection.execute("""
            SELECT COUNT(*) FROM image_tasks t LEFT JOIN images.source_links l
            ON l.observation_id=t.observation_id AND l.run_id=t.run_id
            WHERE (t.status='saved' OR t.asset_sha256 IS NOT NULL) AND l.observation_id IS NULL
        """).fetchone()[0]
        if missing_links:
            raise ValueError("Saved task lacks a matching source link")
        for item in connection.execute("""
            SELECT t.run_id,t.observation_id,t.source_url,t.status,t.previous_attempt_reason,
                   o.run_id AS observation_run,o.view_order,o.image_url
            FROM image_tasks t LEFT JOIN observations o ON o.observation_id=t.observation_id
            WHERE t.previous_attempt_blocked=1 OR t.status='blocked_previous_attempt'
        """):
            if item["run_id"] != item["observation_run"] or not item["source_url"] or item["source_url"] != item["image_url"]:
                raise ValueError("Blocked task cannot be bound to its original observation URL")
            blocked[item["source_url"]].append({
                "run_id": item["run_id"], "observation_id": item["observation_id"],
                "view_order": item["view_order"], "source_url": item["source_url"],
                "previous_attempt_reason": item["previous_attempt_reason"], "status": item["status"]})
        after = {path.name: sha_file(path) for path in paths}
        check_sidecars(paths)
        if before != after:
            raise ValueError("Business database bytes changed while reading")
        connection.rollback()
    return candidates, blocked, assets, before


def package_cached_images(project, snapshot_path, output_dir, *, data_dir=None, sealed=False):
    project, snapshot_path, output_dir = map(lambda path: Path(path).resolve(), (project, snapshot_path, output_dir))
    data_dir = Path(data_dir).resolve() if data_dir is not None else project / "data"
    if not sealed:
        raise ValueError("Only a separately sealed final snapshot is accepted; pass --sealed after sealing")
    if output_dir == project or project in output_dir.parents:
        raise ValueError("Outputs must be outside the production project")
    if output_dir == data_dir or data_dir in output_dir.parents:
        raise ValueError("Outputs must be outside the source database directory")
    if any((output_dir / name).exists() for name in OUTPUT_NAMES):
        raise FileExistsError("An output already exists; choose a fresh directory without overwriting evidence")
    store = load_store(project)
    snapshot, raw, _ = store._read_json(snapshot_path)
    if snapshot.get("status") not in ("complete", "partial"):
        raise ValueError("Snapshot must explicitly declare complete or partial; a batch is not a sealed snapshot")
    if snapshot["status"] == "partial" and snapshot.get("endBoundaryObserved") is not False:
        raise ValueError("Partial snapshot must explicitly set endBoundaryObserved=false")
    store.shop_identity_evidence(snapshot)
    rows, _, _ = store._prepare_snapshot(snapshot)
    for row in rows:
        if row["image_url"] and row["raw"].get("imageUrl") != row["image_url"]:
            raise ValueError("Image URL would require normalization; exact original URL is required")
    snapshot_sha = hashlib.sha256(raw).hexdigest()
    candidates, blocked, assets, source_hashes = read_cache(data_dir, store)
    cache_policy = cache_reuse_policy(candidates, blocked)
    active_blocked = set(cache_policy['blocked_image_urls'])
    manifest_items, queue_items, blocked_items, decisions, used_assets = [], [], [], [], set()
    by_url = defaultdict(list)
    for row in rows:
        by_url[row["image_url"]].append(row["view_order"])
    for url, views in by_url.items():
        old_refs, blocked_refs = candidates.get(url, []), blocked.get(url, [])
        hashes = sorted({item["asset_sha256"] for item in old_refs})
        if not url:
            status, reason = "missing_source_url", "New card has no source image URL"
        elif blocked_refs and url in active_blocked:
            status = "blocked_previous_attempt"
            reason = "Inherited exact-URL restriction; no retry: " + "; ".join(dict.fromkeys(
                item["previous_attempt_reason"] or "Historical restriction reason was not supplied" for item in blocked_refs))
            blocked_items.extend({"viewOrder": view, "imageUrl": url, "previousAttemptBlocked": True,
                                  "previousAttemptReason": reason, "inheritedSourceEvidence": blocked_refs,
                                  "automaticRetryAllowed": False} for view in views)
        elif len(hashes) == 1:
            status, reason = "reused_verified_cache", "Exact original URL matches verified immutable cache provenance"
            digest = hashes[0]
            asset = assets[digest]
            used_assets.add(digest)
            manifest_items.extend({"viewOrder": view, "originalImageUrl": url,
                                   "archivePath": f"images/{digest}.{EXTENSIONS[asset['mime']]}",
                                   "sha256": digest, "mime": asset["mime"], "byteCount": asset["byte_count"],
                                   "reuseSourceEvidence": old_refs, "acquisitionMethod": "local_exact_url_cache_reuse",
                                   **({'reviewedRestrictionEvidence': cache_policy['reviewed_cache_evidence'][url]}
                                      if url in cache_policy['reviewed_cache_evidence'] else {})}
                                  for view in views)
        elif len(hashes) > 1:
            status, reason = "unknown_source_conflict", "Exact URL has multiple historical image hashes; contents unknown until manual review"
        else:
            status, reason = "pending_image_stage", "No verified cached BLOB matches this exact original URL"
        if url:
            queue_items.extend({"imageUrl": url, "rowViewOrders": [view], "status": status,
                                "reason": reason, "automaticRetryAllowed": False,
                                "actionRequired": "requires_review" if status in ("blocked_previous_attempt", "unknown_source_conflict") else "none" if status == "reused_verified_cache" else "await_authorized_image_stage",
                                "reportedDownloadSuccess": False} for view in views)
        decisions.append({"image_url": url, "view_orders": views, "status": status, "reason": reason,
                          "source_references": old_refs, "blocked_source_references": blocked_refs})
    by_view = {row["view_order"]: row for row in rows}
    available = {item["viewOrder"]: item for item in manifest_items}
    for item in manifest_items:
        item["title"] = by_view[item["viewOrder"]]["raw"].get("title")
    card_decisions = sorted([{"viewOrder": view, "title": by_view[view]["raw"].get("title"),
                     "originalImageUrl": item["image_url"], "status": item["status"],
                     "sha256": available.get(view, {}).get("sha256"),
                     "archivePath": available.get(view, {}).get("archivePath"), "reason": item["reason"],
                     "actionRequired": "requires_review" if item["status"] in ("blocked_previous_attempt", "unknown_source_conflict") else "none" if item["status"] == "reused_verified_cache" else "needs_source_url" if not item["image_url"] else "await_authorized_image_stage"}
                     for item in decisions for view in item["view_orders"]], key=lambda item: item["viewOrder"])
    manifest = {"schemaVersion": 1, "method": "verified_exact_original_url_cache_reuse",
                "snapshotSha256": snapshot_sha, "availableCardImageCount": len(manifest_items),
                "snapshotCardCount": len(rows), "cardDecisions": card_decisions,
                "items": sorted(manifest_items, key=lambda item: item["viewOrder"])}
    queue = {"schemaVersion": 1, "snapshotSha256": snapshot_sha,
             "observationWindow": {"fromUtc": snapshot["observedFrom"], "toUtc": snapshot["observedTo"]},
             "automaticRetryAllowed": False, "networkRequestsMade": 0,
             "items": queue_items, "blockedPreviousAttempts": blocked_items}
    missing_items = [{"imageUrl": item["image_url"], "rowViewOrders": item["view_orders"],
                      "status": item["status"], "reason": item["reason"],
                      "actionRequired": "requires_review" if item["status"] in ("blocked_previous_attempt", "unknown_source_conflict") else "await_authorized_image_stage",
                      "eligibleForAuthorizedAcquisition": item["status"] == "pending_image_stage",
                      "automaticRetryAllowed": False}
                     for item in decisions if item["image_url"] and item["status"] != "reused_verified_cache"]
    missing = {"schemaVersion": 1, "snapshotSha256": snapshot_sha, "networkRequestsMade": 0,
               "uniqueUnpackagedUrlCount": len(missing_items), "items": missing_items,
               "cardsWithoutUrl": [item["viewOrder"] for item in card_decisions if not item["originalImageUrl"]],
               "note": "URLs group image work only; every original card remains separate in manifest.cardDecisions. No download or retry is performed."}
    output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="reuse_stage_", dir=output_dir) as stage_name:
        stage = Path(stage_name)
        with zipfile.ZipFile(stage / OUTPUT_NAMES[0], "x", compression=zipfile.ZIP_STORED) as archive:
            archive.writestr("manifest.json", json_bytes(manifest))
            for digest in sorted(used_assets):
                asset = assets[digest]
                archive.writestr(f"images/{digest}.{EXTENSIONS[asset['mime']]}", asset["data"])
        (stage / OUTPUT_NAMES[1]).write_bytes(json_bytes(queue))
        (stage / OUTPUT_NAMES[2]).write_bytes(json_bytes(missing))
        loaded, _ = store._load_archive(stage / OUTPUT_NAMES[0], rows)
        states, _ = store._load_queue(stage / OUTPUT_NAMES[1], snapshot, rows)
        if len(loaded) != len(manifest_items) or any(not states[item["viewOrder"]]["previousAttemptBlocked"] for item in blocked_items):
            raise ValueError("Importer validation does not reproduce reuse or blocked bindings")
        after_hashes = {name: sha_file(data_dir / name) for name in source_hashes}
        if source_hashes != after_hashes or sha_file(snapshot_path) != snapshot_sha:
            raise ValueError("Snapshot or database changed during packaging; outputs were not published")
        counts = {status: sum(len(item["view_orders"]) for item in decisions if item["status"] == status)
                  for status in ("reused_verified_cache", "blocked_previous_attempt", "unknown_source_conflict", "pending_image_stage", "missing_source_url")}
        acceptance = {"schema_version": 1, "status": "prepared_not_imported", "created_at": datetime.now(timezone.utc).isoformat(),
                      "project": str(project), "data_dir": str(data_dir), "snapshot": str(snapshot_path), "snapshot_sha256": snapshot_sha,
                      "snapshot_status": snapshot["status"], "snapshot_card_count": len(rows), "card_counts": counts,
                      "reused_unique_blobs": len(used_assets), "verified_existing_blobs": len(assets),
                      "verified_existing_source_links": sum(map(len, candidates.values())),
                      "historical_blocked_card_count": sum(map(len, blocked.values())),
                      "historical_blocked_urls": list(blocked), "new_unique_source_url_count": len([url for url in by_url if url]),
                      "database_sha256_before": source_hashes, "database_sha256_after": after_hashes,
                      "source_unchanged": True, "snapshot_unchanged": True, "network_requests_made": 0,
                      "database_writes_made": 0, "importer_archive_and_queue_validated": True,
                      "output_files": [{"name": name, "sha256": sha_file(stage / name), "bytes": (stage / name).stat().st_size}
                                       for name in OUTPUT_NAMES[:3]], "decisions": decisions,
                      "scope_note": "Image bytes only; cards, identities, titles and sales are not merged. No import or restore acceptance claimed."}
        (stage / OUTPUT_NAMES[3]).write_bytes(json_bytes(acceptance))
        # Exclusive creation protects existing output; acceptance is written last.
        for name in OUTPUT_NAMES:
            with (stage / name).open("rb") as source, (output_dir / name).open("xb") as target:
                shutil.copyfileobj(source, target)
    return acceptance


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True, type=Path)
    parser.add_argument("--snapshot", required=True, type=Path)
    parser.add_argument("--data-dir", type=Path, help="Read an existing matched pair here instead of PROJECT/data")
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--sealed", action="store_true", help="Confirm input is separately sealed, not a live batch")
    args = parser.parse_args()
    result = package_cached_images(args.project, args.snapshot, args.output_dir, data_dir=args.data_dir, sealed=args.sealed)
    print(json.dumps({key: result[key] for key in ("status", "snapshot_card_count", "card_counts", "reused_unique_blobs", "source_unchanged", "importer_archive_and_queue_validated")}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
