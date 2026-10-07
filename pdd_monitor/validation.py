"""Read-only, source-backed validation for the two SQLite databases.

This checks storage and stated decision rules. It cannot verify the shop's true
sales, listing dates, SKU equivalence, or the contents of unavailable images.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import stat

if __package__:
    from .image_variants import validate_image_variant
else:
    # backup_store loads this file independently for the explicitly chosen project.
    # Execute only its regular sibling, without sys.path/module-cache changes or pyc writes.
    _variant_path = Path(__file__).absolute().parent / "image_variants.py"
    _variant_stat = _variant_path.lstat()
    if (_variant_path.is_symlink() or not stat.S_ISREG(_variant_stat.st_mode)
            or getattr(_variant_stat, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)):
        raise ImportError("Image variant validator must be an ordinary sibling file")
    _variant_namespace = {"__name__": "_pdd_standalone_image_variants", "__file__": str(_variant_path)}
    exec(compile(_variant_path.read_bytes(), str(_variant_path), "exec"), _variant_namespace)
    validate_image_variant = _variant_namespace["validate_image_variant"]


_EXACT_SALES = re.compile(r"^(已拼|已抢|总售|已售|售出)\s*([0-9]{1,18})\s*(件|单|人)$")


def _validate_store(data_dir, run_id=None, *, scopes=False, scope_run_id=None):
    """Return ok/checks/errors/warnings/metrics, without creating or changing files.

Physical integrity, all image hashes and cross-database references are checked
globally. Raw-card and candidate checks apply to the requested run, or all runs.
"""
    scoped = None
    report = {"ok": False, "checks": [], "errors": [], "warnings": [], "metrics": {}}

    def check(name, condition, detail):
        passed = bool(condition)
        report["checks"].append({"name": name, "ok": passed, "detail": detail})
        if not passed:
            report["errors"].append(f"{name}: {detail}")

    data_dir = Path(data_dir)
    main_path, image_path = data_dir / "monitor.sqlite3", data_dir / "images.sqlite3"
    missing = [path.name for path in (main_path, image_path) if not path.is_file()]
    check("database_files", not missing, "both database files exist" if not missing
          else "missing existing database file(s): " + ", ".join(missing))
    if missing:
        return (deepcopy(report), report) if scopes else report

    connection = None
    try:
        connection = sqlite3.connect(main_path.resolve().as_uri() + "?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        connection.execute("PRAGMA busy_timeout=5000")
        connection.execute("ATTACH DATABASE ? AS images", (image_path.resolve().as_uri() + "?mode=ro",))
        connection.execute("BEGIN")
        for schema in ("main", "images"):
            integrity = [row[0] for row in connection.execute(f"PRAGMA {schema}.integrity_check")]
            foreign_keys = [tuple(row) for row in connection.execute(f"PRAGMA {schema}.foreign_key_check")]
            check(f"{schema}.integrity_check", integrity == ["ok"], integrity)
            check(f"{schema}.foreign_key_check", not foreign_keys, foreign_keys)

        def rows(sql):
            return [dict(row) for row in connection.execute(sql)]

        all_runs = rows("SELECT * FROM runs")
        run_hashes = {r['run_id']:r['snapshot_sha256'] for r in all_runs}
        selected_runs = [r for r in all_runs if run_id is None or r["run_id"] == run_id]
        check("requested_run", run_id is None or bool(selected_runs),
              "all runs" if run_id is None else str(run_id))
        selected_ids = {r["run_id"] for r in selected_runs}
        observations = rows("SELECT * FROM observations")
        by_id = {row["observation_id"]: row for row in observations}
        by_run = defaultdict(list)
        for row in observations:
            by_run[row["run_id"]].append(row)
        links = rows("SELECT * FROM images.source_links")
        assets = rows("SELECT * FROM images.assets")
        asset_by_sha = {row["sha256"]: row for row in assets}
        tasks = rows("SELECT * FROM image_tasks")
        tasks_by_observation = {row["observation_id"]: row for row in tasks}
        links_by_observation = {row["observation_id"]: row for row in links}

        bad_blobs = []
        for asset in assets:
            data = asset["data"]
            if not isinstance(data, bytes):
                bad_blobs.append(f"{asset['sha256']}: payload is not a BLOB")
            elif len(data) != asset["byte_count"] or hashlib.sha256(data).hexdigest() != asset["sha256"]:
                bad_blobs.append(f"{asset['sha256']}: byte count or SHA-256 mismatch")
        check("image_blob_size_sha256", not bad_blobs, bad_blobs or f"verified {len(assets)} unique BLOBs")

        reference_errors = []
        for link in links:
            observation = by_id.get(link["observation_id"])
            if observation is None or observation["run_id"] != link["run_id"]:
                reference_errors.append(f"link {link['link_id']}: missing or wrong-run card")
                continue
            if link["source_url"] != observation["image_url"]:
                reference_errors.append(f"link {link['link_id']}: source URL does not match raw card")
            if link["asset_sha256"] not in asset_by_sha:
                reference_errors.append(f"link {link['link_id']}: missing asset")
            task = tasks_by_observation.get(link["observation_id"])
            if not task or task["run_id"] != link["run_id"] or task["asset_sha256"] != link["asset_sha256"] or task["status"] != "saved":
                reference_errors.append(f"link {link['link_id']}: inconsistent image task")
            try:
                manifest_item = json.loads(link["manifest_json"])
                expected = (observation["view_order"], link["source_url"], link["asset_sha256"], link["archive_path"])
                actual = (manifest_item.get("viewOrder"), manifest_item.get("originalImageUrl"),
                          str(manifest_item.get("sha256", "")).lower(), manifest_item.get("archivePath"))
                if actual != expected:
                    reference_errors.append(f"link {link['link_id']}: stored manifest binding mismatch")
                validate_image_variant(manifest_item, original_url=observation['image_url'], title=observation['title'],
                                       snapshot_sha=run_hashes.get(link['run_id']))
            except (TypeError, ValueError):
                reference_errors.append(f"link {link['link_id']}: invalid manifest JSON")
        for task in tasks:
            observation = by_id.get(task["observation_id"])
            if not observation or observation["run_id"] != task["run_id"] or observation["image_url"] != task["source_url"]:
                reference_errors.append(f"task {task['task_id']}: inconsistent card/source reference")
            if task["automatic_retry_allowed"] != 0:
                reference_errors.append(f"task {task['task_id']}: unapproved automatic retry")
            if task["status"] == "saved" and task["observation_id"] not in links_by_observation:
                reference_errors.append(f"task {task['task_id']}: saved without an actual BLOB link")
            if task["status"] != "saved" and task["asset_sha256"] is not None:
                reference_errors.append(f"task {task['task_id']}: non-saved task has a BLOB reference")
        if set(tasks_by_observation) != set(by_id) or len(tasks) != len(observations):
            reference_errors.append("each observation must retain exactly one image task")
        if set(asset_by_sha) != {link["asset_sha256"] for link in links}:
            reference_errors.append("orphan or missing image BLOB")
        check("cross_database_image_references", not reference_errors, reference_errors[:20] or f"verified {len(links)} card-image links")

        groups = rows("SELECT * FROM candidate_groups")
        members = rows("SELECT * FROM group_members")
        members_by_group = defaultdict(list)
        for member in members:
            members_by_group[member["group_id"]].append(member)

        for run in selected_runs:
            label = run["run_id"]
            run_rows = by_run[label]
            raw_errors = []
            try:
                payload_bytes = run["snapshot_bytes"]
                if not isinstance(payload_bytes, bytes) or hashlib.sha256(payload_bytes).hexdigest() != run["snapshot_sha256"]:
                    raw_errors.append("original snapshot BLOB SHA-256 mismatch")
                raw_snapshot = json.loads(payload_bytes)
                if not isinstance(raw_snapshot, dict):
                    raise ValueError("source snapshot must be a JSON object")
                if json.loads(run["snapshot_json"]) != raw_snapshot:
                    raw_errors.append("snapshot_json differs from original bytes")
                raw_cards = raw_snapshot["rows"]
                if not isinstance(raw_cards, list) or any(not isinstance(card, dict) for card in raw_cards):
                    raise ValueError("source rows must be a list of card objects")
                if len(raw_cards) != len(run_rows):
                    raw_errors.append(f"raw {len(raw_cards)} cards versus {len(run_rows)} stored observations")
                orders = [card.get("viewOrder", index) for index, card in enumerate(raw_cards, 1)]
                if len(set(orders)) != len(orders) or set(orders) != {r["view_order"] for r in run_rows}:
                    raw_errors.append("raw/stored viewOrder mapping is not one-to-one")
                record_keys = [str(card["recordKey"]).strip() for card in raw_cards if str(card.get("recordKey") or "").strip()]
                if len(set(record_keys)) != len(record_keys):
                    raw_errors.append("duplicate recordKey in source snapshot")
                raw_by_order = {card.get("viewOrder", index): card for index, card in enumerate(raw_cards, 1)}
                for row in run_rows:
                    raw = raw_by_order.get(row["view_order"])
                    if raw is None:
                        continue
                    if json.loads(row["row_json"]) != raw:
                        raw_errors.append(f"card {row['view_order']}: row_json differs from original card")
                    for column, key in (("title", "title"), ("record_key", "recordKey"),
                                        ("sales_raw", "salesRaw"), ("price_raw", "priceRaw"),
                                        ("image_url", "imageUrl")):
                        value = raw.get(key)
                        expected_text = str(value).strip() or None if value is not None else None
                        if column == "title":
                            expected_text = expected_text or ""
                        if row[column] != expected_text:
                            raw_errors.append(f"card {row['view_order']}: {column} differs from raw")
                # A card is never collapsed merely because its goods ID is absent.
                no_id_raw = sum(not str(card.get("goodsId") or "").strip() for card in raw_cards)
                no_id_rows = sum(not str(json.loads(row["row_json"]).get("goodsId") or "").strip() for row in run_rows)
                if no_id_raw != no_id_rows:
                    raw_errors.append("cards with missing original goods IDs were lost or merged")
            except (ValueError, TypeError, KeyError, UnicodeError) as error:
                raw_errors.append(f"invalid stored raw JSON: {error}")
            check(f"raw_card_reconciliation:{label}", not raw_errors, raw_errors[:20] or f"{len(run_rows)} raw cards preserved separately")

            sales_errors = []
            for row in run_rows:
                raw = row["sales_raw"]
                raw_text = str(raw).strip() if raw is not None else ""
                exact = _EXACT_SALES.fullmatch(raw_text)
                eligible = bool(exact and exact.group(1) == "已拼" and exact.group(3) == "件" and int(exact.group(2)) > 10)
                if bool(row["eligible_gt10"]) != eligible:
                    sales_errors.append(f"card {row['view_order']}: incorrect strict >10 decision")
                if not raw_text and (row["sales_value"] is not None or row["sales_precision"] != "missing"):
                    sales_errors.append(f"card {row['view_order']}: missing sales converted to a value")
                if exact and (row["sales_label"] != exact.group(1) or row["sales_value"] != int(exact.group(2))
                              or row["sales_unit"] != exact.group(3) or row["sales_precision"] != "exact_display"):
                    sales_errors.append(f"card {row['view_order']}: parsed exact sales disagree with raw")
                if not exact and raw_text and row["sales_precision"] == "exact_display":
                    sales_errors.append(f"card {row['view_order']}: ambiguous display treated as exact")
                if row["newness"] != "unknown":
                    sales_errors.append(f"card {row['view_order']}: newness asserted without listing-date evidence")
            check(f"sales_threshold_and_unknowns:{label}", not sales_errors, sales_errors[:20] or "strict per-card threshold; labels and missing values preserved")

            group_errors = []
            run_groups = [group for group in groups if group["run_id"] == label]
            def candidate_title(row):
                return row["normalized_title"] or f"[untitled card {row['view_order']}]"

            candidate_titles = {candidate_title(row) for row in run_rows if row["eligible_gt10"]}
            if candidate_titles != {group["normalized_title"] for group in run_groups}:
                group_errors.append("candidate groups do not exactly cover titles with individual eligible cards")
            for group in run_groups:
                group_members = members_by_group[group["group_id"]]
                expected_rows = {row["observation_id"]: row for row in run_rows if candidate_title(row) == group["normalized_title"]}
                if {m["observation_id"] for m in group_members} != set(expected_rows):
                    group_errors.append(f"group {group['group_code']}: incomplete/wrong title membership")
                expected_triggers = sum(row["eligible_gt10"] for row in expected_rows.values())
                if not expected_triggers or group["member_count"] != len(expected_rows) or group["trigger_count"] != expected_triggers:
                    group_errors.append(f"group {group['group_code']}: wrong member/trigger counts")
                if group["relationship"] != "suspected_similar" or group["same_design_confirmed"] != 0:
                    group_errors.append(f"group {group['group_code']}: unverified design equivalence asserted")
                if any(m["run_id"] != label or m["is_trigger"] != expected_rows.get(m["observation_id"], {}).get("eligible_gt10") for m in group_members):
                    group_errors.append(f"group {group['group_code']}: wrong run/trigger flag")
            check(f"candidate_membership_and_counts:{label}", not group_errors, group_errors[:20] or f"verified {len(run_groups)} unconfirmed title candidate groups")

        _summarize(report, all_runs, selected_runs, observations, links, tasks, groups, assets, run_id)
        if scopes:
            # Both reports describe this same read transaction. Global integrity,
            # every image byte and all references have already been checked once.
            chosen = [run for run in all_runs if scope_run_id is None or run["run_id"] == scope_run_id]
            local_names = ("raw_card_reconciliation:", "sales_threshold_and_unknowns:", "candidate_membership_and_counts:")
            chosen_labels = {run["run_id"] for run in chosen}
            scoped = {"ok": False, "checks": [], "errors": [], "warnings": [], "metrics": {}}
            for original in report["checks"]:
                if original["name"].startswith(local_names) and original["name"].split(":", 1)[1] not in chosen_labels:
                    continue
                item = deepcopy(original)
                if item["name"] == "requested_run":
                    item.update(ok=scope_run_id is None or bool(chosen), detail="all runs" if scope_run_id is None else str(scope_run_id))
                scoped["checks"].append(item)
                if not item["ok"]:
                    scoped["errors"].append(f"{item['name']}: {item['detail']}")
            _summarize(scoped, all_runs, chosen, observations, links, tasks, groups, assets, scope_run_id)
            scoped["ok"] = not scoped["errors"]
        report["ok"] = not report["errors"]
    except (sqlite3.Error, OSError, ValueError, TypeError, KeyError, AttributeError) as error:
        check("validation_execution", False, f"{type(error).__name__}: {error}")
    finally:
        if connection is not None:
            connection.close()
    if scopes:
        # An exception in another run can stop the global loop before this
        # scope is checked. Preserve the original scoped diagnostics on this
        # exceptional path; ordinary validation still reads/hashes once.
        if scoped is None:
            scoped = _validate_store(data_dir, scope_run_id)
        return scoped, report
    return report


def _summarize(report, all_runs, selected_runs, observations, links, tasks, groups, assets, run_id):
    selected_ids = {run["run_id"] for run in selected_runs}
    selected_rows = [row for row in observations if row["run_id"] in selected_ids]
    selected_links = [link for link in links if link["run_id"] in selected_ids]
    selected_tasks = [task for task in tasks if task["run_id"] in selected_ids]
    report["metrics"] = {
        "run_count": len(all_runs), "validated_run_count": len(selected_runs),
        "card_count": len(selected_rows),
        "eligible_gt10_count": sum(row["eligible_gt10"] for row in selected_rows),
        "exactly_10_count": sum(row["sales_label"] == "已拼" and row["sales_precision"] == "exact_display"
                                  and row["sales_unit"] == "件" and row["sales_value"] == 10 for row in selected_rows),
        "sales_missing_count": sum(row["sales_precision"] == "missing" for row in selected_rows),
        "goods_id_missing_count": sum(row["goods_id"] is None for row in selected_rows),
        "candidate_group_count": sum(group["run_id"] in selected_ids for group in groups),
        "image_saved_card_count": len(selected_links),
        "image_unique_asset_count": len({link["asset_sha256"] for link in selected_links}),
        "all_image_unique_asset_count": len(assets),
        "all_image_blob_bytes": sum(asset["byte_count"] for asset in assets),
        "image_task_status_counts": dict(Counter(task["status"] for task in selected_tasks)),
    }
    if not selected_runs and run_id is None:
        report["warnings"].append("No observation runs exist; structural checks do not demonstrate a successful data import.")
    if report["metrics"]["goods_id_missing_count"]:
        report["warnings"].append(f"{report['metrics']['goods_id_missing_count']} cards lack goods IDs; cross-run product matching is unknown for these cards.")
    if report["metrics"]["sales_missing_count"]:
        report["warnings"].append(f"{report['metrics']['sales_missing_count']} cards have missing sales display; they are not zero sales.")
    if len(selected_links) < len(selected_rows):
        report["warnings"].append(f"{len(selected_rows) - len(selected_links)} card images have no stored BLOB; no network retries were attempted by validation.")
    if any(run["status"] != "complete" or not run["end_boundary_observed"] for run in selected_runs):
        report["warnings"].append("Partial or unbounded observation rounds are retained; absence does not establish delisting.")
    report["warnings"].append("Automated checks cover stored evidence and rules only; actual sales, actual listing dates, same design and same SKU remain unverified.")


def validate_store(data_dir, run_id=None):
    """Validate the original single scope with the existing report contract."""
    return _validate_store(data_dir, run_id)


def validate_store_scopes(data_dir, run_id):
    """Share a read/hash pass; retry the scoped report only on execution error."""
    return _validate_store(data_dir, scopes=True, scope_run_id=run_id)
