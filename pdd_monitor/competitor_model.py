"""Standard, source-preserving competitor analysis, independently per shop.

The API performs no I/O and never selects a global reference run. Displayed
counts, price conditions and raw cards retain their original grains and units.
"""
from __future__ import annotations

from .sales_metrics import SALES_METRIC_LABELS, same_sales_metric

from collections import Counter, defaultdict
from copy import deepcopy
from datetime import datetime
import hashlib
import math
import re

MODEL_VERSION = 1
SALES_LABELS = ("已拼", "已抢", "总售", "已售", "售出")
CATEGORY_RULES = (
    ("立牌", ("立牌",)), ("挂件", ("挂件", "挂饰", "钥匙扣")),
    ("徽章", ("徽章", "吧唧")), ("卡片", ("卡片", "小卡", "透卡", "拍立得", "明信片")),
)
LIMITATIONS = [
    "每店仅一个参考轮参与横截面统计；较新的部分轮另列，不与完整参考轮相加。",
    "卡片数不是唯一商品、SKU或买家数；各销量标签与单位分别保存，展示数不是已核实成交。",
    "原展示金额只接受单个金额；券后和未注明券的展示分开，不确认同规格到手价或采购成本。",
    "品类来自公开标题关键词规则；主线索按立牌、挂件、徽章、卡片顺序选首个，全部命中另保留，不确认SKU。",
    "同标题候选组保留全部独立卡片，不累加销量，不确认同设计、同SKU或经营意图。",
    "建议仅为人工核验顺序；成本、供货、库存、预算、利润、实际上架时间和渠道信息未知。",
]


def _epoch(value):
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError("offset required")
        return parsed.timestamp()
    except (TypeError, AttributeError, ValueError, OverflowError) as error:
        raise ValueError("Invalid offset-aware observation timestamp") from error


def _window(run):
    start, end = _epoch(run.get("observed_from")), _epoch(run.get("observed_to"))
    if end < start:
        raise ValueError("Reversed observation window")
    return start, end


def _complete(run):
    return run.get("status") == "complete" and bool(run.get("end_boundary_observed"))


def _rows(container, name):
    if not container:
        return []
    if "queries" in container:
        container = container["queries"]
    value = container.get(name, [])
    return value.get("rows", []) if isinstance(value, dict) else value


def _exact(row):
    return (row.get("sales_precision") == "exact_display" and type(row.get("sales_value")) is int
            and row["sales_value"] >= 0 and bool(row.get("sales_label")) and bool(row.get("sales_unit"))
            and isinstance(row.get("sales_raw"), str) and bool(row["sales_raw"].strip()))


def _sales(row):
    if not _exact(row):
        return "unknown", "unknown"
    if row["sales_label"] not in SALES_METRIC_LABELS or row["sales_unit"] != "件":
        return "other_label", "other_label"
    value = row["sales_value"]
    if value > 10:
        return "yipin_gt10", "yipin_11_30" if value <= 30 else "yipin_31_100" if value <= 100 else "yipin_gt100"
    return ("yipin_eq10", "yipin_eq10") if value == 10 else ("yipin_1to9", "yipin_1to9") if value > 0 else ("yipin_zero", "yipin_zero")


def _goods(row):
    value = row.get("goods_id")
    return value if isinstance(value, str) and re.fullmatch(r"[1-9][0-9]*", value) else None


def _identity(row, counts):
    goods = _goods(row)
    if (goods and counts[goods] > 1) or str(row.get("identity_status", "")).startswith("conflict_"):
        return "conflict"
    return "unique" if goods and row.get("identity_status") == "unique_goods_id" else "unknown"


def parse_display_price(raw):
    """Parse only a literal single yuan amount, using integer cents throughout."""
    if raw is None or isinstance(raw, str) and not raw.strip():
        return {"status": "missing", "fen": None, "condition": None, "band": "unknown"}
    match = re.fullmatch(r"(券后\s*)?[¥￥]\s*([0-9]+)(?:\.([0-9]{1,2}))?", raw.strip()) if isinstance(raw, str) else None
    if not match or len(match.group(2)) > 14:
        return {"status": "unparsed", "fen": None, "condition": None, "band": "unknown"}
    fen = int(match.group(2)) * 100 + int((match.group(3) or "").ljust(2, "0"))
    if fen > 9007199254740991:
        return {"status": "unparsed", "fen": None, "condition": None, "band": "unknown"}
    return {"status": "single_display_amount", "fen": fen,
            "condition": "coupon_after_display" if match.group(1) else "display_unspecified",
            "band": "under_300" if fen < 300 else "300_to_499" if fen < 500 else "500_to_999" if fen < 1000 else "1000_plus"}


def _image(row):
    if row.get("image_content_status") == "verified_local":
        return "cached"
    status = str(row.get("image_status", ""))
    if row.get("previous_attempt_blocked") or re.search("blocked|failed|invalid", status, re.I) or row.get("image_content_status") == "unavailable_or_invalid":
        return "failed_or_blocked"
    return "not_cached"


def _id(prefix, *values):
    return prefix + "_" + hashlib.sha256("|".join(map(str, values)).encode()).hexdigest()[:24]


def _time_bounds(row, run):
    lower, upper = _window(run)
    value = row.get("observed_at")
    if not value:
        return lower, upper
    try:
        point = _epoch(value)
    except ValueError:
        return None
    if not lower <= point <= upper or row.get("observed_at_epoch") is not None and row["observed_at_epoch"] != point:
        return None
    precision = row.get("observed_at_precision") or "legacy_unspecified"
    return (point, point) if precision in ("card_read", "batch_read", "legacy_unspecified") else (lower, upper)


def _dimension(shop, run_id, key, value, label, rows, denominator, definition, qualifiers=None):
    qualifiers = qualifiers or {}
    return {"dimension_id": _id("dimension", shop, run_id, key, value, repr(sorted(qualifiers.items()))),
            "shop_id": shop, "run_id": run_id, "dimension_key": key, "dimension_value": value, "label": label,
            "card_count": len(rows), "eligible_card_count": sum(row["eligible_gt10"] for row in rows),
            "observation_ids": [row["observation_id"] for row in rows], "denominator_card_count": denominator,
            "counts_overlap": False, "definition": definition, "qualifiers": qualifiers}


def _history_evidence(shop, latest, history, by_run, by_observation, identities):
    summaries = [row for row in _rows(history, "comparison_summaries") if row.get("target_run_id") == latest["run_id"] and row.get("mode") == "previous"]
    if len(summaries) > 1:
        raise ValueError("Duplicate previous-comparison summary for one target run")
    if not summaries:
        return None, [], [], 0
    summary = summaries[0]
    baseline = by_run.get(summary.get("baseline_run_id"))
    if baseline and baseline["shop_id"] != shop:
        raise ValueError("Comparison baseline belongs to a different shop")
    selected = [row for row in _rows(history, "comparison_items") if row.get("comparison_id") == summary["comparison_id"]]
    valid, rejected, pair_keys = [], 0, set()
    for item in selected:
        if item.get("status") != "comparable":
            continue
        old, new = by_observation.get(item.get("old_observation_id")), by_observation.get(item.get("new_observation_id"))
        goods = _goods(old) if old else None
        valid_scope = bool(baseline and old and new and old["run_id"] == baseline["run_id"] and new["run_id"] == latest["run_id"]
                           and by_run[old["run_id"]]["shop_id"] == by_run[new["run_id"]]["shop_id"] == shop)
        interval = item.get("elapsed_hours_min")
        old_time = _time_bounds(old, baseline) if valid_scope else None
        new_time = _time_bounds(new, latest) if valid_scope else None
        valid_pair = (valid_scope and goods and goods == _goods(new) and identities[old["observation_id"]] == identities[new["observation_id"]] == "unique"
                      and item.get("match_basis") == "same_unique_goods_id" and _exact(old) and _exact(new)
                      and same_sales_metric(old, new) and old["sales_unit"] == new["sales_unit"]
                      and _window(baseline)[1] <= _window(latest)[0] and _window(baseline)[0] < _window(latest)[0]
                      and type(interval) in (int, float) and math.isfinite(interval) and interval > 0
                      and old_time and new_time and new_time[0] > old_time[1]
                      and abs(interval - (new_time[0] - old_time[1]) / 3600) < 1e-9
                      and type(item.get("display_delta")) is int and item["display_delta"] == new["sales_value"] - old["sales_value"] >= 0)
        key = (old["observation_id"], new["observation_id"]) if valid_scope else None
        if not valid_pair or key in pair_keys:
            rejected += 1
            continue
        pair_keys.add(key)
        valid.append(item)
    return summary, selected, valid, rejected


def build_competitor_model(runs, observations, groups, history=None, arrivals=None):
    """Return four source-backed row arrays. No writes and no inferred events."""
    by_run = {run["run_id"]: run for run in runs}
    by_observation = {row["observation_id"]: row for row in observations}
    if len(by_run) != len(runs) or len(by_observation) != len(observations):
        raise ValueError("Run IDs and observation IDs must be unique")
    shops, rows_by_run = defaultdict(list), defaultdict(list)
    for run in runs:
        if not run.get("shop_id"):
            raise ValueError("Every run needs a shop_id")
        _window(run)
        shops[run["shop_id"]].append(run)
    for row in observations:
        if row["run_id"] not in by_run:
            raise ValueError("Observation refers to an unknown run")
        if row.get("shop_id") is not None and row["shop_id"] != by_run[row["run_id"]]["shop_id"]:
            raise ValueError("Observation shop disagrees with its run")
        rows_by_run[row["run_id"]].append(row)
    identities = {}
    for rows in rows_by_run.values():
        rows.sort(key=lambda row: (row.get("view_order", 0), row["observation_id"]))
        counts = Counter(_goods(row) for row in rows if _goods(row))
        identities.update({row["observation_id"]: _identity(row, counts) for row in rows})
    groups_by_run, group_keys = defaultdict(list), set()
    for group in groups:
        run = by_run.get(group["run_id"])
        if not run or group.get("shop_id", run["shop_id"]) != run["shop_id"]:
            raise ValueError("Candidate group has inconsistent run/shop scope")
        key = (group["run_id"], group["group_id"])
        if key in group_keys:
            raise ValueError("Duplicate candidate group identity")
        group_keys.add(key)
        ids = group.get("member_observation_ids")
        if ids is None:
            ids = [member["observation_id"] for member in group.get("members", [])]
        if len(ids) != len(set(ids)) or any(value not in by_observation or by_observation[value]["run_id"] != group["run_id"] for value in ids):
            raise ValueError("Candidate group membership crosses a run or duplicates a card")
        groups_by_run[group["run_id"]].append((group, ids))
    output = {key: [] for key in ("competitor_shops", "competitor_products", "competitor_dimensions", "competitor_strategy")}
    for shop, shop_runs in sorted(shops.items()):
        ordered = sorted(shop_runs, key=lambda run: (*reversed(_window(run)), run["run_id"]), reverse=True)
        complete = [run for run in ordered if _complete(run)]
        reference, latest = (complete or ordered)[0], ordered[0]
        reference_rows = rows_by_run[reference["run_id"]]
        products = []
        peer = defaultdict(list)
        valid_groups = []
        for group, ids in groups_by_run[reference["run_id"]]:
            eligible_ids = [value for value in ids if _sales(by_observation[value])[0] == "yipin_gt10"]
            info = {"group_id": group["group_id"], "group_code": group.get("group_code"), "member_observation_ids": ids,
                    "eligible_observation_ids": eligible_ids, "eligible_count": len(eligible_ids)}
            valid_groups.append(info)
            for value in ids:
                peer[value].append(info)
        for row in reference_rows:
            product = deepcopy(row)
            bucket, band = _sales(row)
            price = parse_display_price(row.get("price_raw"))
            category_matches = {name: [term for term in terms if term in (row.get("title") or "")] for name, terms in CATEGORY_RULES}
            matched = [name for name, terms in category_matches.items() if terms]
            memberships = peer.get(row["observation_id"], [])
            product.update(shop_id=shop, product_record_id=_id("card", shop, row["run_id"], row["observation_id"]),
                           eligible_gt10=bucket == "yipin_gt10", sales_bucket=bucket, sales_band=band,
                           display_price_status=price["status"], display_price_fen=price["fen"], display_price_condition=price["condition"], display_price_band=price["band"],
                           category_primary_clue=matched[0] if matched else "其他", category_matched_clues=matched,
                           category_rule_matches={name: terms for name, terms in category_matches.items() if terms},
                           identity_coverage=identities[row["observation_id"]], image_coverage=_image(row),
                           candidate_group_ids=[group["group_id"] for group in memberships], candidate_group_codes=[group["group_code"] for group in memberships],
                           peer_eligible_count=max((group["eligible_count"] for group in memberships), default=0))
            product["candidate_structure"] = "multiple_eligible_candidate_group" if product["peer_eligible_count"] >= 2 else "single_eligible_candidate_group" if memberships else "not_in_candidate_group"
            products.append(product)
        output["competitor_products"].extend(products)
        n = len(products)
        count = lambda predicate: sum(bool(predicate(row)) for row in products)
        label_units = defaultdict(list)
        for row in products:
            label_units[(row.get("sales_label"), row.get("sales_unit"))].append(row)
        label_counts = [{"label": label, "unit": unit, "card_count": len(rows),
                         "positive_card_count": sum(_exact(row) and row["sales_value"] > 0 and row["sales_label"] in SALES_LABELS for row in rows)}
                        for (label, unit), rows in sorted(label_units.items(), key=lambda pair: (str(pair[0][0]), str(pair[0][1])))]
        summary, history_items, valid_pairs, rejected_pairs = _history_evidence(shop, latest, history, by_run, by_observation, identities)
        growth_status = "not_available" if summary is None else "missing_baseline" if summary.get("baseline_status") == "missing_baseline" else "comparable_display_evidence" if valid_pairs else "no_confirmed_comparable_pairs"
        arrival_summaries = [value for value in _rows(arrivals, "new_arrival_summary") if value.get("shop_id") == shop]
        if len(arrival_summaries) > 1:
            raise ValueError("Multiple active arrival baselines for one shop")
        arrival_summary = arrival_summaries[0] if arrival_summaries else None
        arrival_configured = bool(arrival_summary and arrival_summary.get("tracking_id"))
        arrival_items = [value for value in _rows(arrivals, "new_arrival_items") if value.get("run_id") in by_run and by_run[value["run_id"]]["shop_id"] == shop]
        if any(value.get("observation_id") not in by_observation or by_observation[value["observation_id"]]["run_id"] != value["run_id"] for value in arrival_items):
            raise ValueError("Arrival anchor does not reference its original observation")
        profile = {"shop_id": shop, "shop_name": reference.get("shop_name") or latest.get("shop_name") or shop,
                   "model_version": MODEL_VERSION, "reference_run_id": reference["run_id"],
                   "reference_selection": "latest_complete_with_boundary" if complete else "latest_partial_fallback",
                   "reference_scope": "complete_storefront" if complete else "partial_storefront",
                   "reference_observed_from": reference["observed_from"], "reference_observed_to": reference["observed_to"],
                   "reference_status": reference["status"], "reference_end_boundary_observed": bool(reference.get("end_boundary_observed")),
                   "latest_run_id": latest["run_id"], "latest_observed_from": latest["observed_from"], "latest_observed_to": latest["observed_to"],
                   "latest_is_complete": _complete(latest), "latest_card_count": len(rows_by_run[latest["run_id"]]),
                   "historical_run_count": len(ordered), "card_count": n,
                   "eligible_card_count": count(lambda row: row["eligible_gt10"]),
                   "yipin_positive_card_count": count(lambda row: row["sales_bucket"] in ("yipin_gt10", "yipin_eq10", "yipin_1to9")),
                   "positive_card_count": count(lambda row: _exact(row) and row["sales_value"] > 0 and row["sales_label"] in SALES_LABELS),
                   "missing_sales_card_count": count(lambda row: row.get("sales_precision") == "missing" or row.get("sales_raw") in (None, "")),
                   "fuzzy_sales_card_count": count(lambda row: row["sales_bucket"] == "unknown" and row.get("sales_precision") != "missing" and row.get("sales_raw") not in (None, "")),
                   "other_label_positive_card_count": count(lambda row: row["sales_bucket"] == "other_label" and row["sales_value"] > 0 and row["sales_label"] in SALES_LABELS),
                   "image_cached_count": count(lambda row: row["image_coverage"] == "cached"),
                   "image_failed_count": count(lambda row: row["image_coverage"] == "failed_or_blocked"),
                   "image_unavailable_count": count(lambda row: row["image_coverage"] != "cached"),
                   "identity_unique_count": count(lambda row: row["identity_coverage"] == "unique"),
                   "identity_unknown_count": count(lambda row: row["identity_coverage"] == "unknown"),
                   "identity_conflict_count": count(lambda row: row["identity_coverage"] == "conflict"),
                   "candidate_group_count": len(valid_groups), "candidate_member_count": len(peer),
                   "multi_eligible_group_count": sum(group["eligible_count"] >= 2 for group in valid_groups),
                   "multi_eligible_card_count": count(lambda row: row["eligible_gt10"] and row["peer_eligible_count"] >= 2),
                   "price_parsed_count": count(lambda row: row["display_price_status"] == "single_display_amount"),
                   "price_unparsed_count": count(lambda row: row["display_price_status"] != "single_display_amount"),
                   "reference_observation_ids": [row["observation_id"] for row in products], "sales_label_unit_counts": label_counts,
                   "growth_status": growth_status, "growth_comparison_id": summary.get("comparison_id") if summary else None,
                   "growth_comparable_count": len(valid_pairs) if summary else None,
                   "growth_positive_pair_count": sum(item["display_delta"] > 0 for item in valid_pairs) if summary else None,
                   "growth_unknown_item_count": sum(item.get("status") == "unknown" for item in history_items) if summary else None,
                   "growth_rejected_pair_count": rejected_pairs,
                   "arrivals_state": arrival_summary.get("state") if arrival_summary else "not_configured",
                   "new_arrival_anchor_count": len(arrival_items) if arrival_configured else None,
                   "category_rules": [{"category": name, "keywords": list(terms), "priority": index + 1} for index, (name, terms) in enumerate(CATEGORY_RULES)],
                   "limitations": LIMITATIONS}
        profile["image_coverage_ratio"] = profile["image_cached_count"] / n if n else None
        profile["identity_coverage_ratio"] = profile["identity_unique_count"] / n if n else None
        output["competitor_shops"].append(profile)
        definitions = {
            "sales_bucket": "单卡精确已拼/已抢件分为>10/=10/1–9/0；其他标签或单位独立，非精确或缺失为未知。",
            "sales_band": "已拼>10的人工观察档11–30/31–100/>100；仅展示分层，不代表趋势或潜力。",
            "category_clue": "按标题明文词命中；主线索顺序立牌→挂件→徽章→卡片，未命中为其他；不确认SKU。",
            "image_coverage": "已有本地校验图/失败或限制/未缓存；失败是未缓存子集，不重复相加。",
            "identity_coverage": "合法且本轮唯一无冲突的ID/未知/冲突，不以标题补ID。",
            "candidate_structure": "已有候选组内逐卡达标数量结构，不合并卡片或求销量和。",
        }
        for key, field in (("sales_bucket", "sales_bucket"), ("sales_band", "sales_band"), ("category_clue", "category_primary_clue"),
                           ("image_coverage", "image_coverage"), ("identity_coverage", "identity_coverage"), ("candidate_structure", "candidate_structure")):
            buckets = defaultdict(list)
            for row in products:
                buckets[row[field]].append(row)
            for value, members in sorted(buckets.items()):
                output["competitor_dimensions"].append(_dimension(shop, reference["run_id"], key, value, value, members, n, definitions[key]))
        for (label, unit), members in sorted(label_units.items(), key=lambda pair: (str(pair[0][0]), str(pair[0][1]))):
            output["competitor_dimensions"].append(_dimension(shop, reference["run_id"], "sales_label_unit", f"{label}|{unit}", f"{label or '未知标签'} · {unit or '未知单位'}", members, n,
                "原始销量标签与单位的独立卡片数，不相加展示数字。", {"sales_label": label, "sales_unit": unit}))
        price_buckets = defaultdict(list)
        price_labels = {"under_300": "低于¥3.00", "300_to_499": "¥3.00–4.99", "500_to_999": "¥5.00–9.99", "1000_plus": "¥10.00及以上", "unknown": "展示金额未知或不可单值解析"}
        for row in products:
            price_buckets[(row["display_price_condition"] or "unknown", row["display_price_band"])].append(row)
        for (condition, band), members in sorted(price_buckets.items()):
            output["competitor_dimensions"].append(_dimension(shop, reference["run_id"], "display_price_band", band, price_labels[band], members, n,
                "仅单一原展示金额的整数分区间；券后与未注明券条件分开，非同规格到手价或采购成本。", {"price_condition": condition}))

        def strategy(rule_id, title, facts, candidates, actions, ids, thresholds, unknowns, *, run_id=None, scope="reference_run", comparison_ids=None):
            target_run_id = run_id or reference["run_id"]
            evidence_ids = list(dict.fromkeys(ids))
            output["competitor_strategy"].append({"strategy_id": _id("strategy", shop, run_id or reference["run_id"], rule_id),
                "shop_id": shop, "run_id": target_run_id, "rule_id": rule_id, "title": title,
                "facts": facts, "interpretation_candidates": candidates, "followup_actions": actions,
                "observation_ids": [value for value in evidence_ids if by_observation[value]["run_id"] == target_run_id],
                "supporting_observation_ids": evidence_ids, "thresholds": thresholds, "unknowns": unknowns, "scope": scope,
                "basis_comparison_item_ids": comparison_ids or [], "confidence": "rule_based_candidate"})
        eligible = [row for row in products if row["eligible_gt10"]]
        if eligible:
            strategy("SINGLE_CARD_GT10", "逐卡达标参考", [f"参考轮有{len(eligible)}张卡各自精确已拼>10件，其中{sum(row['image_coverage']=='cached' for row in eligible)}张有本地已校验图。"],
                     ["这些卡片可作为人工核验款式与条件的起始清单；前台展示不证明可盈利。"],
                     ["逐卡核对款式、规格、供货、实际到手条件，再依据自己的成本和预算决定是否试款。"],
                     [row["observation_id"] for row in eligible], {"sales_labels": list(SALES_METRIC_LABELS), "sales_unit": "件", "sales_precision": "exact_display", "sales_value_exclusive_min": 10},
                     ["真实成交与款式归因", "采购成本、起订量、运费、库存、可承受预算"])
        multi = [row for row in eligible if row["peer_eligible_count"] >= 2]
        if multi:
            group_members = sorted({value for group in valid_groups if group["eligible_count"] >= 2 for value in group["member_observation_ids"]})
            strategy("TITLE_MULTI_INDEPENDENT_GT10", "同标题多卡逐条对照", [f"{profile['multi_eligible_group_count']}个已有候选组中，有{len(multi)}张卡各自达标；展开保留{len(group_members)}张全部组员。"],
                     ["相同标题提供比较入口，可能包含不同款式或链接，不确认同SKU，也不解释卖家铺货意图。"],
                     ["对照全部组员的原价、主图和规格，达标与未达标卡分别记录。"], group_members,
                     {"same_title_independently_eligible_card_min": 2, "sales_sum_used": False}, ["同设计、同SKU关系", "每条链接的规格与价格条件"])
        amount_groups = defaultdict(lambda: defaultdict(list))
        for row in products:
            if row["display_price_status"] == "single_display_amount":
                amount_groups[row["display_price_condition"]][row["display_price_fen"]].append(row)
        modal_prices, price_facts, price_ids = [], [], []
        for condition, amounts in sorted(amount_groups.items()):
            denominator = sum(len(members) for members in amounts.values())
            maximum = max(len(members) for members in amounts.values())
            for fen, members in sorted(amounts.items()):
                if len(members) != maximum:
                    continue
                ids = [row["observation_id"] for row in members]
                modal_prices.append({"price_condition": condition, "display_price_fen": fen,
                                     "card_count": len(members), "parsed_condition_card_count": denominator,
                                     "reference_card_count": n, "observation_ids": ids})
                price_ids.extend(ids)
                condition_label = "券后展示" if condition == "coupon_after_display" else "未注明券条件的展示"
                price_facts.append(f"{condition_label}中，¥{fen // 100}.{fen % 100:02d}为出现最多的单值金额之一：{len(members)}/{denominator}张该条件可解析卡；全参考轮{n}卡。")
        if not price_facts:
            price_facts = [f"参考轮{n}卡没有可解析的单一原展示金额，主展示金额保留未知。"]
        strategy("DISPLAY_PRICE_STRUCTURE", "原展示金额结构", price_facts,
                 ["频次描述原卡的展示金额分布；券条件分开，不能由此认定同规格到手价、采购成本或利润空间。"],
                 ["先对照相同券条件、规格、数量与运费，再核对自己的供货条件；并列金额全部保留。"], price_ids,
                 {"modal_display_prices": modal_prices, "amount_unit": "integer_fen", "tie_policy": "retain_all_modes", "conditions_combined": False},
                 ["同规格实际到手价", "券门槛、运费与供货成本"])
        category_groups = defaultdict(list)
        for row in products:
            category_groups[row["category_primary_clue"]].append(row)
        largest = max((len(members) for members in category_groups.values()), default=0)
        leading_categories = [{"category_clue": name, "card_count": len(members), "reference_card_count": n,
                               "observation_ids": [row["observation_id"] for row in members]}
                              for name, members in sorted(category_groups.items()) if len(members) == largest]
        category_facts = [f"标题主线索“{entry['category_clue']}”有{entry['card_count']}/{n}卡，为规则计数最多的线索之一。" for entry in leading_categories]
        if not category_facts:
            category_facts = ["参考轮没有卡片，标题结构保留未知。"]
        strategy("TITLE_FORM_STRUCTURE", "标题品类线索结构", category_facts,
                 ["标题关键词只是可复查的形式线索；多个命中另存，“其他”表示规则未命中，不推断行业、真实SKU或卖家经营意图。"],
                 ["对照同主题不同款式的独立卡片，核对主图与规格；不能把标题大类当作同款确认。"],
                 [value for entry in leading_categories for value in entry["observation_ids"]],
                 {"leading_category_clues": leading_categories, "category_rules": profile["category_rules"], "primary_rule": "first_matching_category_in_priority_order"},
                 ["真实商品品类与SKU", "相同主题各款式的真实成交表现"])
        gaps = [row for row in products if row["identity_coverage"] != "unique" or row["image_coverage"] != "cached"]
        strategy("EVIDENCE_COVERAGE", "优先补足核验证据", [f"参考轮{n}卡中，唯一可靠ID {profile['identity_unique_count']}卡、本地校验图{profile['image_cached_count']}卡；最新轮为{'完整' if _complete(latest) else '部分'}观察。"],
                 ["身份不足限制跨轮变化判断；部分轮不能代表全店已无新增或下架。"],
                 ["优先补有价值候选的真实可见ID和规格；已有失败图片保留原因，不自动换路径重试。"],
                 [row["observation_id"] for row in gaps], {"identity_requirement": "unique_goods_id_per_run", "image_requirement": "verified_local"},
                 ["缺身份卡的可靠跨轮关联", "部分轮未覆盖区域", "失败图片的原始限制"])
        growth_groups = defaultdict(list)
        for item in valid_pairs:
            row = by_observation[item["new_observation_id"]]
            growth_groups[(row["sales_label"], row["sales_unit"])].append(item)
        for (label, unit), pairs in sorted(growth_groups.items()):
            ids = [value for item in pairs for value in (item["old_observation_id"], item["new_observation_id"])]
            strategy("VERIFIED_DISPLAY_PAIR_" + _id("label", label, unit), "可比展示数的后续核验", [f"最新轮对上一轮，同销量指标和单位{label}/{unit}有{len(pairs)}对已验证身份与时间的可比卡，其中{sum(item['display_delta']>0 for item in pairs)}对展示数为正差。"],
                     ["展示数变化可作为复核线索，不是已核实成交订单；各对仍独立保留。"],
                     ["打开前后原卡检查原文、时间精度和规格，再决定是否持续关注。"], ids,
                     {"required_match_basis": "same_unique_goods_id", "sales_label": label, "sales_unit": unit},
                     ["真实成交、订单取消与显示口径调整", "同规格到手价"], run_id=latest["run_id"], scope="latest_previous_comparison",
                     comparison_ids=[item["comparison_item_id"] for item in pairs])
        if not valid_pairs:
            growth_label = {"not_available": "尚无已有比较证据", "missing_baseline": "缺少上一轮基线", "no_confirmed_comparable_pairs": "没有合格可比配对"}[growth_status]
            strategy("GROWTH_EVIDENCE_PENDING", "增长判断保留未知", [f"最新轮对上一轮：{growth_label}；当前没有可确认的合格配对。"],
                     ["缺少可比证据不等于没有增长。"], ["保留每日独立快照，待同店稳定ID、标签单位和时间条件齐备后再比较。"], [],
                     {"confirmed_comparable_pairs": 0}, ["真实跨轮展示变化"], run_id=latest["run_id"], scope="latest_previous_comparison")
        if arrival_configured:
            ids = [item["observation_id"] for item in arrival_items]
            arrival_label = {"no_post_baseline_run": "起点后尚无采集，等待下一轮", "no_first_candidates": "已有起点后观察，当前未产生首次候选", "candidates_available": "已有起点后首次候选可跟进"}.get(arrival_summary.get("state"), "当前跟踪状态待核验")
            strategy("FIXED_BASELINE_FOLLOWUP", "固定起点后的候选跟进", [f"该店固定起点队列：{arrival_label}；保留{len(arrival_items)}条首次观察锚点。"],
                     ["首见锚点与弱线索不等于实际新品上架；固定起点已记录的原卡被排除，此前未采到的旧存量仍可能因补扫成为首见线索。",
                      arrival_summary.get("baseline_coverage_note") or "请核对固定起点的完整与部分范围，不能把扩大采集范围认作新增上架。"],
                     ["只跟进已有首次锚点及其后续原卡；无起点后记录时等待下一轮真实采集。"], ids,
                     {"tracking_id": arrival_summary.get("tracking_id")}, ["实际上架时间", "弱线索对应的商品身份"], run_id=latest["run_id"], scope="fixed_tracking_baseline")
    return output
