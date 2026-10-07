"""Pure, repeatable history derived from immutable run and card evidence.

No I/O, writes, network, title matching, or inference of listing/delisting.  All
unpaired cards survive independently, including every duplicate-ID card.
"""
from __future__ import annotations

from .sales_metrics import SALES_METRIC_LABELS, same_sales_metric

from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import re

SHANGHAI = timezone(timedelta(hours=8), "Asia/Shanghai")
MODES = ("previous", "yesterday_slot")
HISTORY_LIMITATIONS = [
    "展示数差值不是已核实订单；已拼/已抢件按同一指标比较，其他标签和单位不混算，缺失值不补零。",
    "首次观察候选不等于实际上架；未在本轮看到不能推断下架。",
    "只有两轮内均唯一且无冲突的稳定商品ID才能配对；标题或图片相同不确认身份。",
    "部分轮次只支持已匹配卡片的有限对照，不计算全店卡片或销量总增减。",
    "昨天同档仅指北京时间昨日相同AM/PM；没有对应轮次时返回缺基线，不借用其他日期或档位。",
    "逐卡时间缺失或为轮次窗口时，间隔只给上下界，不伪装为精确时长。",
]


def _epoch(value):
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.timestamp() if parsed.tzinfo is not None else None
    except (ValueError, OverflowError):
        return None


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _run_time(run):
    start, end = _epoch(run.get("observed_from")), _epoch(run.get("observed_to"))
    if start is None or end is None or end < start:
        raise ValueError(f"Invalid observation window for run {run.get('run_id')}")
    for key, parsed in (("observed_from_epoch", start), ("observed_to_epoch", end)):
        if key in run and (not _number(run[key]) or abs(run[key] - parsed) > .001):
            raise ValueError(f"Conflicting run timestamp: {run.get('run_id')} {key}")
    return start, end


def _complete(run):
    return bool(run and run.get("status") == "complete" and run.get("end_boundary_observed"))


def _goods_id(row):
    value = row.get("goods_id")
    return value if isinstance(value, str) and re.fullmatch(r"[1-9][0-9]*", value) else None


def _exact(row):
    value = row.get("sales_value")
    return row.get("sales_precision") == "exact_display" and type(value) is int and value >= 0


def _eligible(row):
    return _exact(row) and row.get("sales_label") in SALES_METRIC_LABELS and row.get("sales_unit") == "件" and row["sales_value"] > 10


def _identity_counts(rows):
    ids = Counter(_goods_id(row) for row in rows if _goods_id(row))
    counts = Counter()
    for row in rows:
        goods = _goods_id(row)
        if (goods and ids[goods] > 1) or str(row.get("identity_status", "")).startswith("conflict_"):
            counts["conflict"] += 1
        elif goods and row.get("identity_status") == "unique_goods_id":
            counts["unique"] += 1
        else:
            counts["unknown"] += 1
    return counts


def _time_evidence(row, run):
    if row is None:
        return {"observed_at": None, "precision": None, "basis": None, "lower": None, "upper": None, "valid": False}
    raw = row.get("original")
    if not isinstance(raw, dict):
        try:
            raw = json.loads(row.get("row_json") or "{}")
        except (TypeError, ValueError):
            raw = {}
    precision = row.get("observed_at_precision") or raw.get("observedAtPrecision") or ("legacy_unspecified" if row.get("observed_at") else "unknown")
    value = row.get("observed_at")
    point = _epoch(value)
    start, end = _run_time(run)
    valid = not value or point is not None
    if point is not None and not start <= point <= end:
        valid = False
    recorded_epoch = row.get("observed_at_epoch")
    if recorded_epoch is not None and (not _number(recorded_epoch) or point is None or abs(point - recorded_epoch) > .001):
        valid = False
    point_precision = precision in ("card_read", "batch_read", "legacy_unspecified")
    uses_point = point is not None and point_precision
    return {"observed_at": value, "precision": precision,
            "basis": "recorded_read_time" if uses_point else "observation_window",
            "lower": point if uses_point else start, "upper": point if uses_point else end, "valid": valid}


def _item(comparison_id, target, baseline, old, new):
    anchor = new if new is not None else old
    old_time = _time_evidence(old, baseline) if baseline else _time_evidence(None, target)
    new_time = _time_evidence(new, target)
    key = f"{comparison_id}|{old.get('observation_id') if old else '-'}|{new.get('observation_id') if new else '-'}"
    result = {
        "comparison_item_id": "item_" + hashlib.sha256(key.encode()).hexdigest()[:24],
        "comparison_id": comparison_id, "target_run_id": target["run_id"],
        "baseline_run_id": baseline["run_id"] if baseline else None,
        "goods_id": anchor.get("goods_id"), "title": anchor.get("title"),
        "status": "unknown", "reason": None, "event": "unknown", "match_basis": None,
        "display_delta": None, "crossed_gt10": None, "elapsed_hours": None,
        "elapsed_hours_min": None, "elapsed_hours_max": None,
        "first_observed_candidate": False, "first_observed_evidence": None, "newness": "unknown",
        "old_observation_window": {"from": baseline["observed_from"], "to": baseline["observed_to"]} if baseline else None,
        "new_observation_window": {"from": target["observed_from"], "to": target["observed_to"]},
        "verification_gaps": [],
        "identity_clue_status": "no_clue", "identity_clue_basis": None,
        "identity_clue_observation_ids": [], "identity_clue_candidates": [],
        "earlier_observation_ids": [],
        "price_raw_changed": None, "title_changed": None, "image_url_changed": None,
    }
    for prefix, row, time in (("old", old, old_time), ("new", new, new_time)):
        for out, key in (("observation_id", "observation_id"), ("view_order", "view_order"),
                         ("title", "title"), ("value", "sales_value"), ("label", "sales_label"),
                         ("price_raw", "price_raw"), ("image_url", "image_url"),
                         ("unit", "sales_unit"), ("sales_raw", "sales_raw"), ("sales_precision", "sales_precision"),
                         ("identity_status", "identity_status")):
            result[f"{prefix}_{out}"] = row.get(key) if row else None
        result[f"{prefix}_identity_reason"] = (None if not row or _goods_id(row) and row.get("identity_status") == "unique_goods_id" else
                                               "missing_goods_id" if row.get("goods_id") in (None, "", "0", 0) else
                                               "invalid_goods_id" if not _goods_id(row) else "identity_conflict")
        result[f"{prefix}_observed_at"] = time["observed_at"]
        result[f"{prefix}_observed_at_precision"] = time["precision"]
        result[f"{prefix}_time_basis"] = time["basis"]
    return result


def _compare(target, baseline, new_rows, old_rows, mode, selection, earlier_runs, rows_by_run):
    comparison_id = f"{target['run_id']}:{mode}:{baseline['run_id'] if baseline else 'missing'}"
    old_by_id, new_by_id = defaultdict(list), defaultdict(list)
    for rows, mapping in ((old_rows, old_by_id), (new_rows, new_by_id)):
        for row in rows:
            if _goods_id(row):
                mapping[_goods_id(row)].append(row)
    old_ids = _identity_counts(old_rows)
    base_identity_complete = bool(baseline) and old_ids["unique"] == len(old_rows)
    history_ids = defaultdict(list)
    history_coverage_complete = bool(earlier_runs)
    for earlier in earlier_runs:
        earlier_rows = rows_by_run[earlier["run_id"]]
        history_coverage_complete &= _complete(earlier) and _identity_counts(earlier_rows)["unique"] == len(earlier_rows)
        for row in earlier_rows:
            if _goods_id(row):
                history_ids[_goods_id(row)].append(row["observation_id"])
    items, used_old = [], set()

    def unpaired(row, side):
        item = _item(comparison_id, target, baseline, row if side == "old" else None, row if side == "new" else None)
        goods = _goods_id(row)
        if baseline is None:
            item["reason"] = "missing_baseline"
        elif not goods:
            item["reason"] = "missing_goods_id" if row.get("goods_id") in (None, "", "0", 0) else "invalid_goods_id"
        elif len(old_by_id[goods]) > 1 or len(new_by_id[goods]) > 1:
            item.update(status="anomaly", reason="duplicate_goods_id")
        elif row.get("identity_status") != "unique_goods_id":
            item.update(status="anomaly", reason="identity_conflict")
        elif any(match.get("identity_status") != "unique_goods_id" for match in old_by_id[goods] + new_by_id[goods]):
            item.update(status="anomaly", reason="identity_conflict")
        elif side == "old":
            item.update(reason="not_observed_in_target", event="unmatched")
            item["verification_gaps"].append("未在本轮看到不能证明下架")
        else:
            if history_ids.get(goods):
                item.update(event="unmatched", reason="seen_in_earlier_run")
                item["earlier_observation_ids"] = history_ids[goods]
                item["verification_gaps"].append("更早轮次已观察到此商品ID，所选基线未出现，不称首次观察")
                return item
            item.update(event="first_observed_candidate", first_observed_candidate=True,
                        first_observed_evidence="pending_verification")
            if not _complete(baseline):
                item["reason"] = "baseline_partial"
            elif not base_identity_complete:
                item["reason"] = "baseline_identity_incomplete"
            elif not history_coverage_complete:
                item["reason"] = "history_coverage_incomplete"
            else:
                item.update(status="candidate", reason="no_prior_observation",
                            first_observed_evidence="not_seen_in_recorded_history_complete_identity")
            item["verification_gaps"].append("仅在现有历史中未记录此ID的首次观察候选，不是实际上架时间")
        if baseline and not _complete(baseline):
            item["verification_gaps"].append("基线为部分轮次")
        if not _complete(target):
            item["verification_gaps"].append("目标为部分轮次")
        if baseline and not base_identity_complete:
            item["verification_gaps"].append("基线商品身份覆盖不完整")
        return item

    for new in new_rows:
        goods = _goods_id(new)
        matches = old_by_id.get(goods, [])
        if (baseline is None or not goods or len(matches) != 1 or len(new_by_id[goods]) != 1
                or new.get("identity_status") != "unique_goods_id" or matches[0].get("identity_status") != "unique_goods_id"):
            item = unpaired(new, "new")
            # A corresponding conflict must not turn a valid-looking new card
            # into a first-observed candidate or silently consume the old card.
            if matches and len(matches) == len(new_by_id[goods]) == 1 and (new.get("identity_status") != "unique_goods_id" or matches[0].get("identity_status") != "unique_goods_id"):
                item.update(status="anomaly", reason="identity_conflict", event="unknown", first_observed_candidate=False, first_observed_evidence=None)
            items.append(item)
            continue
        old = matches[0]
        used_old.add(old["observation_id"])
        item = _item(comparison_id, target, baseline, old, new)
        item["match_basis"] = "same_unique_goods_id"
        for field in ("price_raw", "title", "image_url"):
            left, right = old.get(field), new.get(field)
            if isinstance(left, str) and left.strip() and isinstance(right, str) and right.strip():
                item[f"{field}_changed"] = left != right
        old_time, new_time = _time_evidence(old, baseline), _time_evidence(new, target)
        old_start, old_end = _run_time(baseline)
        new_start, _ = _run_time(target)
        if not old_time["valid"] or not new_time["valid"]:
            item["reason"] = "invalid_observation_time"
        elif old_end > new_start or old_start >= new_start or new_time["lower"] <= old_time["upper"]:
            item["reason"] = "observation_time_not_ordered"
        elif not _exact(old) or not _exact(new):
            item["reason"] = "sales_not_exact"
        elif not old.get("sales_label") or not same_sales_metric(old, new):
            item["reason"] = "sales_label_changed"
        elif not old.get("sales_unit") or old.get("sales_unit") != new.get("sales_unit"):
            item["reason"] = "sales_unit_changed"
        else:
            elapsed_min = (new_time["lower"] - old_time["upper"]) / 3600
            elapsed_max = (new_time["upper"] - old_time["lower"]) / 3600
            item.update(display_delta=new["sales_value"] - old["sales_value"],
                        elapsed_hours_min=elapsed_min, elapsed_hours_max=elapsed_max,
                        elapsed_hours=elapsed_min if old_time["basis"] == new_time["basis"] == "recorded_read_time" else None)
            if item["display_delta"] < 0:
                item.update(status="anomaly", reason="negative_display_delta", event="negative_display_delta")
            else:
                crossed = old["sales_value"] <= 10 < new["sales_value"] if old["sales_label"] in SALES_METRIC_LABELS and old["sales_unit"] == "件" else None
                event = "crossed_gt10" if crossed else ("increased" if item["display_delta"] > 0 else "unchanged")
                item.update(status="comparable", event=event, crossed_gt10=crossed)
        if not _complete(baseline) or not _complete(target):
            item["verification_gaps"].append("仅此匹配卡片有效，部分轮次不能用于全店总量变化")
        items.append(item)
    for old in old_rows:
        if old["observation_id"] not in used_old:
            items.append(unpaired(old, "old"))
    # Optional locating clues, never matching keys. Each original observation
    # remains its own item. Multiplicity stays explicit and no side is chosen.
    clues = {"old": defaultdict(list), "new": defaultdict(list)}
    for side, rows in (("old", old_rows), ("new", new_rows)):
        for row in rows:
            if row.get("title") and row.get("image_url"):
                clues[side][(row["title"], row["image_url"])].append(row)
    all_rows_by_id = {row["observation_id"]: row for row in old_rows + new_rows}
    for item in items:
        if item["old_observation_id"] is not None and item["new_observation_id"] is not None:
            continue
        side = "new" if item["new_observation_id"] is not None else "old"
        anchor = all_rows_by_id[item[f"{side}_observation_id"]]
        key = (anchor.get("title"), anchor.get("image_url"))
        others = clues["old" if side == "new" else "new"].get(key, [])
        # Known differing IDs are not identity candidates even if images match.
        others = [row for row in others if not (_goods_id(anchor) and _goods_id(row) and _goods_id(anchor) != _goods_id(row))]
        if not others:
            continue
        other_side = "old" if side == "new" else "new"
        item["identity_clue_status"] = "unique_title_image_candidate" if len(clues[other_side][key]) == len(clues[side][key]) == 1 else "ambiguous_title_image"
        item["identity_clue_basis"] = "exact_title_and_original_image_url"
        item["identity_clue_observation_ids"] = [row["observation_id"] for row in others]
        item["identity_clue_candidates"] = [{key: row.get(key) for key in ("observation_id", "title", "sales_raw", "sales_value", "sales_label", "sales_unit", "observed_at")} for row in others]
        item["verification_gaps"].append("标题与原图URL相同仅供定位，身份待核验，不能计算差值")
    statuses = Counter(item["status"] for item in items)
    summary = {
        "comparison_id": comparison_id, "target_run_id": target["run_id"],
        "baseline_run_id": baseline["run_id"] if baseline else None, "mode": mode,
        "baseline_status": "available" if baseline else "missing_baseline", "baseline_selection": selection,
        "target_is_complete": _complete(target), "baseline_is_complete": _complete(baseline) if baseline else None,
        "baseline_identity_complete": base_identity_complete if baseline else None,
        "earlier_history_coverage_complete": bool(history_coverage_complete),
        "target_card_count": len(new_rows), "baseline_card_count": len(old_rows) if baseline else None,
        "item_count": len(items), "comparable_count": statuses["comparable"], "unknown_count": statuses["unknown"],
        "anomaly_count": statuses["anomaly"], "candidate_count": statuses["candidate"],
        "first_observed_candidate_count": sum(item["first_observed_candidate"] for item in items),
        "first_observed_pending_count": sum(item["first_observed_evidence"] == "pending_verification" for item in items),
        "crossed_gt10_count": sum(item["crossed_gt10"] is True for item in items),
        "increased_count": sum(item["event"] == "increased" for item in items),
        "positive_delta_count": sum(item["status"] == "comparable" and item["display_delta"] > 0 for item in items),
        "unchanged_count": sum(item["status"] == "comparable" and item["display_delta"] == 0 for item in items),
        "negative_delta_count": sum(item["reason"] == "negative_display_delta" for item in items),
        "reason_counts": dict(Counter(item["reason"] for item in items if item["reason"])),
        "scope": "paired_card_evidence_only", "counting_rule": "increased_count excludes crossed_gt10; positive_delta_count includes both", "limitations": HISTORY_LIMITATIONS,
    }
    # Every input card is represented exactly once per comparison, even when a
    # duplicate or conflict forbids joining it to the other side.
    assert sorted(item["new_observation_id"] for item in items if item["new_observation_id"] is not None) == sorted(row["observation_id"] for row in new_rows)
    assert sorted(item["old_observation_id"] for item in items if item["old_observation_id"] is not None) == sorted(row["observation_id"] for row in old_rows)
    return summary, items


def build_history(runs, observations):
    """Return three row arrays for a reviewed snapshot without mutating inputs.

    AM/PM uses observation *end* in UTC+08, divided exactly at noon. Yesterday
    chooses the latest complete same-slot run, otherwise the latest partial;
    previous means the latest earlier endpoint from the same shop, not a day.
    """
    by_run = defaultdict(list)
    run_ids = {run["run_id"] for run in runs}
    if len(run_ids) != len(runs) or len({row["observation_id"] for row in observations}) != len(observations):
        raise ValueError("Duplicate run or observation identity")
    for row in observations:
        if row["run_id"] not in run_ids:
            raise ValueError("Observation references unknown run")
        by_run[row["run_id"]].append(row)
    for rows in by_run.values():
        rows.sort(key=lambda row: (row.get("view_order", 0), row["observation_id"]))
    ordered = sorted(runs, key=lambda run: (*reversed(_run_time(run)), run["run_id"]), reverse=True)
    histories, summaries, all_items = [], [], []
    for target in ordered:
        start, end = _run_time(target)
        local_start, local_end = (datetime.fromtimestamp(epoch, SHANGHAI) for epoch in (start, end))
        local_date = local_end.date()
        slot = "AM" if local_end.hour < 12 else "PM"
        rows = by_run[target["run_id"]]
        identities = _identity_counts(rows)
        history = {key: target.get(key) for key in ("run_id", "shop_id", "observed_from", "observed_to", "status", "end_boundary_observed", "snapshot_sha256")}
        history.update(local_date=local_date.isoformat(), time_slot=slot,
                       local_observed_from=local_start.isoformat(), local_observed_to=local_end.isoformat(),
                       is_complete=_complete(target), card_count=len(rows), eligible_gt10_count=sum(_eligible(row) for row in rows),
                       identity_unique_count=identities["unique"], identity_unknown_count=identities["unknown"], identity_conflict_count=identities["conflict"])
        previous = [run for run in ordered if run.get("shop_id") == target.get("shop_id") and _run_time(run)[1] < end]
        yesterday = []
        for run in previous:
            when = datetime.fromtimestamp(_run_time(run)[1], SHANGHAI)
            if when.date() == local_date - timedelta(days=1) and ("AM" if when.hour < 12 else "PM") == slot:
                yesterday.append(run)
        for mode in MODES:
            if mode == "previous":
                baseline = previous[0] if previous else None
                selection = "previous_observation" if baseline else "missing_previous"
            else:
                complete = [run for run in yesterday if _complete(run)]
                baseline = (complete or yesterday or [None])[0]
                selection = ("latest_complete_yesterday_slot" if complete else "latest_partial_yesterday_slot") if baseline else "missing_yesterday_slot"
            summary, items = _compare(target, baseline, rows, by_run[baseline["run_id"]] if baseline else [], mode, selection, previous, by_run)
            history[f"{mode}_comparison_id"] = summary["comparison_id"]
            summaries.append(summary)
            all_items.extend(items)
        histories.append(history)
    return {"run_history": histories, "comparison_summaries": summaries, "comparison_items": all_items}
