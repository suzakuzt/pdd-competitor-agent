"""Fixed-baseline first-observation clues. Pure derivation, no DB/network writes.

Configuration is created explicitly once, outside dashboard refresh. Every card
keeps its original observation ID; neither weak clues nor repeated IDs merge it.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re

LIMITATIONS = [
    "固定起点中的全部原始卡片均为存量，包含部分轮次和销量缺失卡；刷新不移动起点。",
    "首次观察候选和新卡片线索都不是已确认新品或实际上架时间。",
    "部分采集起点没有覆盖全店；后续扩大采集产生的首见线索可能只是此前未采到的旧存量。",
    "标题与原图URL组合只是定位线索；原图或标题变化可能来自旧卡改动。",
    "同轮重复卡片独立保留；后续弱线索多卡全部列出，不择一、不合并销量。",
    "时间区间是此前完整轮未记录至首次记录的采样参考；连续扫描不是瞬时全店快照。",
    "缺少此前完整且身份全覆盖的未记录证据时，下界为空，不能推定具体上架时段。",
]


def _time(value):
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError("timestamp must contain an offset")
        return parsed.timestamp()
    except (AttributeError, TypeError, ValueError, OverflowError) as error:
        raise ValueError(f"Invalid offset-aware timestamp: {value!r}") from error


def _run_times(run):
    start, end = _time(run.get("observed_from")), _time(run.get("observed_to"))
    if end < start:
        raise ValueError("Observation window is reversed")
    for field, expected in (("observed_from_epoch", start), ("observed_to_epoch", end)):
        if field in run and (type(run[field]) not in (int, float) or run[field] != expected):
            raise ValueError("Observation epoch conflicts with source timestamp")
    return start, end


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _tracking_id(config):
    return "tracking_" + hashlib.sha256(_canonical({key: value for key, value in config.items() if key != "tracking_id"}).encode()).hexdigest()[:24]


def _validate_config(config):
    if not isinstance(config, dict) or config.get("schema_version") != 1 or config.get("timezone") != "Asia/Shanghai":
        raise ValueError("Unsupported tracking configuration")
    if not isinstance(config.get("shop_id"), str) or not config["shop_id"]:
        raise ValueError("Tracking configuration needs one shop_id")
    _time(config.get("started_at"))
    baselines = config.get("baseline_runs")
    if not isinstance(baselines, list) or not baselines:
        raise ValueError("Tracking baseline must contain existing observation runs")
    ids = [row.get("run_id") for row in baselines]
    if len(set(ids)) != len(ids) or ids != config.get("baseline_run_ids"):
        raise ValueError("Baseline run IDs are inconsistent")
    for row in baselines:
        if not row.get("run_id") or not re.fullmatch(r"[0-9a-f]{64}", row.get("snapshot_sha256", "")):
            raise ValueError("Baseline run identity or SHA-256 is invalid")
        if _time(row["observed_from"]) > _time(row["observed_to"]) or _time(row["observed_to"]) > _time(config["started_at"]):
            raise ValueError("Baseline window cannot extend beyond tracking start")
        if type(row.get("card_count")) is not int or row["card_count"] < 0:
            raise ValueError("Invalid baseline card count")
    if sum(row["card_count"] for row in baselines) != config.get("baseline_observation_count"):
        raise ValueError("Baseline observation total is inconsistent")
    latest = max(baselines, key=lambda row: _time(row["observed_to"]))["observed_to"]
    if latest != config.get("baseline_latest_observed_to") or config.get("tracking_id") != _tracking_id(config):
        raise ValueError("Tracking configuration fingerprint changed; do not reset the baseline during refresh")
    return config


def make_tracking_config(runs, observations, *, shop_id, started_at):
    """Explicit one-time construction; caller saves with exclusive-create mode."""
    _time(started_at)
    if len({run["run_id"] for run in runs}) != len(runs) or len({row["observation_id"] for row in observations}) != len(observations):
        raise ValueError("Duplicate source run or observation ID")
    selected = sorted((run for run in runs if run["shop_id"] == shop_id), key=lambda run: (_run_times(run)[1], run["run_id"]))
    counts = Counter(row["run_id"] for row in observations)
    baseline = [{"run_id": run["run_id"], "snapshot_sha256": run["snapshot_sha256"],
                 "observed_from": run["observed_from"], "observed_to": run["observed_to"], "card_count": counts[run["run_id"]]} for run in selected]
    if not baseline:
        raise ValueError("No existing runs for the configured shop")
    result = {"schema_version": 1, "timezone": "Asia/Shanghai", "shop_id": shop_id, "started_at": started_at,
              "baseline_run_ids": [row["run_id"] for row in baseline], "baseline_runs": baseline,
              "baseline_observation_count": sum(row["card_count"] for row in baseline),
              "baseline_latest_observed_to": baseline[-1]["observed_to"],
              "baseline_source_kind": "all_existing_cards_including_complete_partial_and_missing_sales"}
    result["tracking_id"] = _tracking_id(result)
    return _validate_config(result)


def load_tracking_config(path):
    """Read only. Missing/invalid config is an error, never an auto-reset."""
    return _validate_config(json.loads(Path(path).read_text(encoding="utf-8-sig")))


def _goods(row):
    value = row.get("goods_id")
    return value if isinstance(value, str) and re.fullmatch(r"[1-9][0-9]*", value) else None


def _combo(row):
    title, image = row.get("title"), row.get("image_url")
    return (title, image) if isinstance(title, str) and title.strip() and isinstance(image, str) and image.strip() else None


def _first_time(row, run):
    raw = row.get("original")
    if not isinstance(raw, dict):
        try:
            raw = json.loads(row.get("row_json") or "{}")
        except (ValueError, TypeError):
            raw = {}
    raw = raw if isinstance(raw, dict) else {}
    value = row.get("observed_at")
    precision = row.get("observed_at_precision") or raw.get("observedAtPrecision") or ("legacy_unspecified" if value else "unknown")
    valid = False
    if value:
        try:
            point = _time(value)
            start, end = _run_times(run)
            valid = start <= point <= end and (row.get("observed_at_epoch") is None or row["observed_at_epoch"] == point)
        except ValueError:
            pass
    # v5 keeps the latest real read in observed_at and the earliest ready-card
    # read in immutable original.firstObservedAt. Never rewrite source rows.
    first_value = raw.get("firstObservedAt")
    first_precision = raw.get("firstObservedAtPrecision")
    first_valid = False
    if first_value is not None and valid and first_precision in ("card_read", "batch_read"):
        try:
            first_point = _time(first_value)
            first_valid = start <= first_point <= point <= end
        except ValueError:
            pass
    source = "original_first_observed_at" if first_valid else "observed_at" if valid else "observation_window"
    if first_valid:
        value, precision = first_value, first_precision
    point_basis = valid and precision in ("card_read", "batch_read")
    return {"first_observed_at": value if valid else None, "first_observed_at_precision": precision,
            "first_observation_window": {"from": run["observed_from"], "to": run["observed_to"]},
            "first_seen_time_basis": "recorded_read_time" if point_basis else "observation_window",
            "possible_until": value if point_basis else run["observed_to"],
            "recorded_time_invalid": bool(value and not valid),
            "first_time_evidence_source": source,
            "first_recorded_time_invalid": first_value is not None and not first_valid}


def build_new_arrivals(runs, observations, config):
    """Return persistent first-observation anchors and one fixed-scope summary.

    Nonbaseline runs starting at/after started_at are eligible. Earlier and
    straddling runs remain historical evidence, including late imports. All
    historical rows for this shop are reconsidered on refresh; new evidence can
    retract a derived candidate without changing the fixed configuration.
    """
    _validate_config(config)
    by_run = {run["run_id"]: run for run in runs}
    if len(by_run) != len(runs) or len({row["observation_id"] for row in observations}) != len(observations):
        raise ValueError("Duplicate source run or observation ID")
    rows_by_run = defaultdict(list)
    for row in observations:
        if row["run_id"] not in by_run:
            raise ValueError("Observation references an unknown run")
        rows_by_run[row["run_id"]].append(row)
    for baseline in config["baseline_runs"]:
        run = by_run.get(baseline["run_id"])
        if not run or run["shop_id"] != config["shop_id"]:
            raise ValueError("Fixed baseline run is missing or belongs to another shop")
        if any(run.get(field) != baseline[field] for field in ("snapshot_sha256", "observed_from", "observed_to")) or len(rows_by_run[run["run_id"]]) != baseline["card_count"]:
            raise ValueError("Fixed baseline SHA, window or card count changed")
    selected = sorted((run for run in runs if run["shop_id"] == config["shop_id"]), key=lambda run: (*_run_times(run), run["run_id"]))
    reliable = {}
    full_identity = {}
    for run in selected:
        rows = rows_by_run[run["run_id"]]
        rows.sort(key=lambda row: (row.get("view_order", 0), row["observation_id"]))
        counts = Counter(_goods(row) for row in rows if _goods(row))
        for row in rows:
            reliable[row["observation_id"]] = bool(_goods(row) and counts[_goods(row)] == 1 and row.get("identity_status") == "unique_goods_id")
        full_identity[run["run_id"]] = bool(rows and run.get("status") == "complete" and run.get("end_boundary_observed") and all(reliable[row["observation_id"]] for row in rows))
    started = _time(config["started_at"])
    baseline_ids = set(config["baseline_run_ids"])
    baseline_coverage = [{"run_id": run["run_id"], "status": run.get("status"),
                          "end_boundary_observed": bool(run.get("end_boundary_observed")),
                          "card_count": len(rows_by_run[run["run_id"]])}
                         for run in selected if run["run_id"] in baseline_ids]
    baseline_has_complete = any(row["status"] == "complete" and row["end_boundary_observed"] for row in baseline_coverage)
    baseline_coverage_status = "complete_reference_available" if baseline_has_complete else "partial_only"
    baseline_note = ("固定起点含完整参考轮及其已记录原卡；首见仍只是观察线索，不确认新品或实际上架。" if baseline_has_complete else
                     "固定起点只有部分采集，未覆盖全店；后续补扫首次看到的卡片可能是此前未采到的旧存量，不能当作新增上架。")
    prior_ids, prior_raw_ids, prior_combos, prior_titles, prior_images = (defaultdict(list) for _ in range(5))
    items, previous_runs, post_runs, excluded_runs = [], [], [], []
    classified = Counter()

    def refs(rows, basis):
        return [{"run_id": row["run_id"], "observation_id": row["observation_id"], "match_basis": basis} for row in rows]

    for run in selected:
        rows = rows_by_run[run["run_id"]]
        post = run["run_id"] not in baseline_ids and _run_times(run)[0] >= started
        if post:
            post_runs.append(run)
        elif run["run_id"] not in baseline_ids:
            excluded_runs.append(run["run_id"])
        for row in rows:
            goods, combo = _goods(row), _combo(row)
            is_reliable = reliable[row["observation_id"]]
            if not post:
                continue
            if is_reliable and prior_ids.get(goods):
                classified["existing_reliable_identity"] += 1
                continue
            if not is_reliable and (not combo or prior_combos.get(combo)):
                classified["existing_card_clue" if combo else "insufficient_identity_signal"] += 1
                continue
            kind = "first_observed_id_candidate" if is_reliable else "new_card_clue"
            classified[kind] += 1
            changes, references = [], []
            if combo and prior_combos.get(combo):
                references.extend(refs(prior_combos[combo], "exact_title_original_image_url_clue_only"))
                if any(not reliable[prior["observation_id"]] for prior in prior_combos[combo]):
                    changes.append("possible_old_card_identity_enrichment")
            if goods and prior_raw_ids.get(goods):
                references.extend(refs(prior_raw_ids[goods], "historical_unreliable_id"))
                changes.append("historical_unreliable_id_seen")
            title_matches = [prior for prior in prior_titles.get(row.get("title"), []) if prior.get("image_url") != row.get("image_url")]
            image_matches = [prior for prior in prior_images.get(row.get("image_url"), []) if prior.get("title") != row.get("title")]
            if title_matches:
                changes.append("possible_image_change")
                references.extend(refs(title_matches, "same_title_different_original_image"))
            if image_matches:
                changes.append("possible_title_change")
                references.extend(refs(image_matches, "same_original_image_different_title"))
            timing = _first_time(row, run)
            absence = [prior for prior in previous_runs if full_identity[prior["run_id"]]
                       and _run_times(prior)[1] < _run_times(run)[0]
                       and not any(_goods(prior_row) == goods for prior_row in rows_by_run[prior["run_id"]])]
            prior = max(absence, key=lambda value: _run_times(value)[1]) if absence else None
            reason = "prior_complete_identity_coverage_absence"
            if not is_reliable:
                reason = "identity_unverified"
            elif "possible_old_card_identity_enrichment" in changes:
                reason = "possible_old_card_identity_enrichment"
            elif "historical_unreliable_id_seen" in changes:
                reason = "historical_unreliable_id_seen"
            elif prior is None:
                reason = "no_prior_complete_identity_coverage"
            if reason != "prior_complete_identity_coverage_absence":
                prior = None
            expansion_possible = not baseline_has_complete and prior is None
            key = f"{config['tracking_id']}|{run['run_id']}|{row['observation_id']}"
            item = {"arrival_item_id": "arrival_" + hashlib.sha256(key.encode()).hexdigest()[:24],
                    "tracking_id": config["tracking_id"], "run_id": run["run_id"], "observation_id": row["observation_id"],
                    "view_order": row.get("view_order"), "title": row.get("title"), "goods_id": row.get("goods_id"),
                    "identity_status": row.get("identity_status"), "discovery_kind": kind,
                    "discovery_label": "ID首次观察候选" if is_reliable else "新卡片线索（身份待核验）",
                    **timing, "possible_since": prior["observed_to"] if prior else None,
                    "possible_since_reason": reason, "last_complete_absence_run_id": prior["run_id"] if prior else None,
                    "previous_absence_observation_window": {"from": prior["observed_from"], "to": prior["observed_to"]} if prior else None,
                    "time_range_label": "此前完整轮未记录→本次首次记录（采样参考，非上架时间）" if prior else "最迟于首次记录已观察，更早时间未知",
                    "possible_change_reasons": changes, "historical_references": list({(value["observation_id"], value["match_basis"]): value for value in references}.values()),
                    "verification_gaps": ["首次记录不是实际上架时间", "销量和价格仍以每张原卡为准"], "newness": "unknown",
                    "baseline_coverage_status": baseline_coverage_status, "coverage_expansion_possible": expansion_possible}
            if expansion_possible:
                item["discovery_label"] = ("ID首次观察候选" if is_reliable else "首次观察卡片线索") + "（起点部分，可能为旧存量）"
                item["verification_gaps"].append(baseline_note)
            if not is_reliable:
                item["verification_gaps"].append("标题与原图URL组合不确认商品身份，同轮重复卡独立保留")
            if not _combo(row):
                item["verification_gaps"].append("缺标题或原图URL，无法提供完整弱线索")
            if timing["recorded_time_invalid"]:
                item["verification_gaps"].append("原逐卡时间无效，首次时间按实际观察窗口表示")
            if timing["first_recorded_time_invalid"]:
                item["verification_gaps"].append("原首见时刻字段未通过精度、窗口或先后核对，未采用该字段")
            items.append(item)
        # Update only after the entire run so same-run repetitions stay distinct.
        for row in rows:
            goods, combo = _goods(row), _combo(row)
            if reliable[row["observation_id"]]:
                prior_ids[goods].append(row)
            if goods:
                prior_raw_ids[goods].append(row)
            if combo:
                prior_combos[combo].append(row)
            if row.get("title"):
                prior_titles[row["title"]].append(row)
            if row.get("image_url"):
                prior_images[row["image_url"]].append(row)
        previous_runs.append(run)
    by_observation = {row["observation_id"]: row for row in observations}
    def overlap_component(seed, candidates):
        """Unordered runs connected by overlapping observation windows."""
        component = {seed}
        eligible = {row["run_id"] for row in candidates}
        while True:
            new = {key for key in eligible if any(_run_times(by_run[key])[0] < _run_times(by_run[existing])[1]
                    and _run_times(by_run[existing])[0] < _run_times(by_run[key])[1] for existing in component)}
            if new <= component:
                return component
            component |= new
    for item in items:
        anchor = by_observation[item["observation_id"]]
        strong = item["discovery_kind"] == "first_observed_id_candidate"
        candidates = prior_ids[_goods(anchor)] if strong else prior_combos[_combo(anchor)]
        # Same-run sibling weak cards are each retained, never selected as one.
        later = [row for row in candidates if _run_times(by_run[row["run_id"]])[0] >= _run_times(by_run[anchor["run_id"]])[0]
                 and row["observation_id"] != anchor["observation_id"]]
        basis = "same_unique_goods_id" if strong else "exact_title_original_image_url_clue_only"
        item["subsequent_references"] = refs(later, basis)
        current = [anchor] + later
        first_component = overlap_component(anchor["run_id"], current)
        item["first_time_order_status"] = "observed_run_order"
        item["first_observation_id_candidates"] = [anchor["observation_id"]]
        if len(first_component) > 1:
            component_runs = [by_run[key] for key in first_component]
            start_run = min(component_runs, key=lambda value: _run_times(value)[0])
            end_run = max(component_runs, key=lambda value: _run_times(value)[1])
            item.update(first_time_order_status="overlapping_runs_unknown", first_observed_at=None,
                        first_observed_at_precision="unknown", first_seen_time_basis="observation_window",
                        first_observation_window={"from": start_run["observed_from"], "to": end_run["observed_to"]},
                        possible_since=None, possible_until=end_run["observed_to"], possible_since_reason="observation_windows_overlap",
                        last_complete_absence_run_id=None, previous_absence_observation_window=None,
                        time_range_label="重叠观察窗口内已记录，逐卡先后未确认；非上架时间",
                        first_observation_id_candidates=[row["observation_id"] for row in current if row["run_id"] in first_component])
            item["verification_gaps"].append("观察轮次窗口重叠；不以轮次起止替代逐卡先后，首见时间保守保留联合窗口")
        latest_run = max((by_run[row["run_id"]] for row in current), key=lambda run: (*reversed(_run_times(run)), run["run_id"]))
        latest_component = overlap_component(latest_run["run_id"], current)
        item["latest_run_id"] = latest_run["run_id"] if len(latest_component) == 1 else None
        item["latest_time_order_status"] = "observed_run_order" if len(latest_component) == 1 else "overlapping_runs_unknown"
        item["latest_observation_ids"] = [row["observation_id"] for row in current if row["run_id"] in latest_component]
        for reference in item["subsequent_references"]:
            reference["temporal_order"] = "unknown_overlap" if reference["run_id"] in first_component - {anchor["run_id"]} else "same_run_sibling" if reference["run_id"] == anchor["run_id"] else "later_run"
        item["latest_reference_basis"] = basis
    state = "no_post_baseline_run" if not post_runs else "candidates_available" if items else "no_first_candidates"
    summary = {"tracking_id": config["tracking_id"], "started_at": config["started_at"], "timezone": config["timezone"],
               "shop_id": config["shop_id"], "baseline_run_ids": list(config["baseline_run_ids"]),
               "baseline_observation_count": config["baseline_observation_count"], "baseline_latest_observed_to": config["baseline_latest_observed_to"],
               "baseline_coverage_status": baseline_coverage_status, "baseline_has_complete_run": baseline_has_complete,
               "baseline_run_coverage": baseline_coverage, "baseline_coverage_note": baseline_note,
               "post_baseline_run_count": len(post_runs), "post_baseline_run_ids": [run["run_id"] for run in post_runs],
               "post_baseline_observation_count": sum(len(rows_by_run[run["run_id"]]) for run in post_runs),
               "excluded_pre_tracking_run_ids": excluded_runs, "item_count": len(items),
               "first_observed_id_candidate_count": sum(item["discovery_kind"] == "first_observed_id_candidate" for item in items),
               "new_card_clue_count": sum(item["discovery_kind"] == "new_card_clue" for item in items),
               "possible_coverage_expansion_item_count": sum(item["coverage_expansion_possible"] for item in items),
               "post_observation_classification_counts": dict(classified), "state": state,
               "grain": "first-observation anchors, not unique products or SKU counts", "limitations": LIMITATIONS}
    return {"new_arrival_items": items, "new_arrival_summary": [summary]}
