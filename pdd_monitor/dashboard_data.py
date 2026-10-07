"""Export an inspectable Data app snapshot from paired databases, read-only.

All observations are retained. A dashboard may select a single run and filter
rows, but must never treat the union of historical cards as unique products.
Only verified, already-stored raster BLOBs become data URLs; no network access.
"""

from __future__ import annotations

from .sales_metrics import SALES_METRIC_LABELS, same_sales_metric

import argparse
import base64
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import tempfile

from .store import _connect_readonly, _image_mime
from .history import build_history, HISTORY_LIMITATIONS
from .collection_dashboard import build_collection_queries
from .portfolio_tracking import build_portfolio_arrivals
from .competitor_registry import build_targets
from .competitor_model import build_competitor_model
from .artist_research import load_catalog, build_artist_research
from .artist_heat import build_heat_queries
from .opportunities import build_opportunity_queries
from .profit_lab import build_trial_queries
from .product_tables import build_product_table_queries
from .daily_warehouse import build_warehouse_queries


OBSERVATIONS_SQL = """SELECT o.*, t.status AS image_status, t.asset_sha256,
       t.previous_attempt_blocked, t.previous_attempt_reason, t.action_required,
       t.automatic_retry_allowed
FROM observations o
LEFT JOIN image_tasks t ON t.observation_id=o.observation_id AND t.run_id=o.run_id
ORDER BY o.run_id, o.view_order, o.observation_id"""
RUNS_SQL = """SELECT r.*, s.shop_name
FROM runs r JOIN shops s ON s.shop_id=r.shop_id
ORDER BY r.observed_to_epoch DESC, r.observed_from_epoch DESC, r.run_id DESC"""
GROUPS_SQL = "SELECT * FROM candidate_groups ORDER BY run_id, first_view_order, group_id"
MEMBERS_SQL = "SELECT group_id, observation_id, run_id, is_trigger FROM group_members ORDER BY group_id, observation_id"
ASSETS_SQL = "SELECT sha256, mime, byte_count, data, created_at FROM images.assets ORDER BY sha256"
LINKS_SQL = "SELECT observation_id, run_id, asset_sha256, source_url, manifest_json FROM images.source_links ORDER BY observation_id"
LABELS = ("已拼", "已抢", "总售", "已售", "售出")
IDENTITY_KEYS = ("id", "legacyPresentationTitle", "buildStatus")
LIMITATIONS = [
    "每个观察轮次中的一张原始卡片是一条记录；跨轮卡片不能相加当成唯一商品、SKU 或买家数。",
    "单卡精确已拼/已抢 >10 件才达主门槛；正好 10 不达标，模糊值与缺失值不补零。",
    "已抢与已拼件按同一指标展示；其他销量标签与单位独立参考；前台展示不是已核实成交。",
    "同标题候选组不确认同设计或同 SKU；展示价未经同规格、券、数量与运费核实。",
    "部分轮次不能替代完整基线；未出现的卡片不能推断已下架。",
    "跨轮仅计算唯一稳定ID、同销量指标和单位、精确展示及正确时间顺序的配对差值；首次观察候选不是实际上架，缺证据返回未知。",
    "数据生成时刻是查询时间，不是页面观察时间；逐卡时间精度按原始来源保留。",
    "图片只使用已有本地 BLOB，下载失败或限制保留，不自动重试。",
    "图片内容已校验仅表示本地文件完整；店内搜索同文件的公开展示变体另列实际获取URL和来源证明，不冒称原图URL对应字节。",
]


def _utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _hash(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _reference_selection(row):
    if row["sales_precision"] == "missing" or not row["sales_raw"]:
        return "missing"
    if row["sales_precision"] != "exact_display" or row["sales_value"] is None:
        return "non_exact_or_unparsed"
    if row["sales_value"] == 0:
        return "zero"
    if row["sales_label"] not in LABELS or row["sales_value"] < 0:
        return "other_excluded"
    return "positive"


def _reference_group(row):
    if _reference_selection(row) != "positive":
        return None
    if row["sales_label"] in SALES_METRIC_LABELS and row["sales_unit"] == "件":
        if row["sales_value"] > 10:
            return "yipin_gt10"
        return "yipin_eq10" if row["sales_value"] == 10 else "yipin_1to9"
    return "other_positive"


def _source(label, sql, tables, executed_at, files, definitions, grain, windows):
    return {
        "label": label, "provider": "SQLite", "classification": "observed",
        "sql": sql, "tables": tables, "files": files, "executedAt": executed_at,
        "timezone": "Asia/Shanghai", "grain": grain, "observationWindows": windows,
        "filters": ["保留全部已存轮次；前端默认选中最近完整且有结束边界的一轮。", "无正数预筛选，无 LIMIT 截断。"],
        "metricDefinitions": definitions, "caveats": LIMITATIONS,
        "evidenceFlow": [
            {"title": "只读查询", "detail": "两个已存在 SQLite 文件以 mode=ro、query_only 打开；同一 attached 事务读取。"},
            {"title": "可追溯原始证据", "detail": "轮次保留原始快照 SHA-256、观察窗口、完整/部分状态；卡片保留原始 JSON 与观察 ID。"},
            {"title": "复现", "detail": "python -m pdd_monitor.dashboard_data --data-dir data --output dashboard/src/data.json；需在包含原双库的项目目录执行。"},
        ],
    }


def _definition(label, definition, tables, **extra):
    return {"label": label, "definition": definition, "sourceLineage": [{"tables": tables}], **extra}


def build_dashboard_snapshot(data_dir, output, preserve_existing_identity=True, *, write_output=True,
                             image_delivery="inline"):
    """Write reviewed JSON atomically and return it; never change source databases.

Existing canonical app identity fields survive refresh by default. Invalid
existing JSON or invalid stored BLOB evidence causes a failure without replacing
the previous output. A source read is required on each call; no cached sales.
The publisher may use write_output=False to append its decision ledger in memory
before the one atomic publication, avoiding a full JSON write/read round trip.
"""
    if image_delivery not in ("inline", "local_http"):
        raise ValueError("Unknown dashboard image delivery mode")
    data_dir, output = Path(data_dir).resolve(), Path(output).resolve()
    if output.suffix.lower() != ".json":
        raise ValueError("Dashboard output must use a .json extension")
    if output.is_relative_to(data_dir) or output.is_relative_to(data_dir.parent / "sources"):
        raise ValueError("Dashboard output must be outside source data and sources directories")
    identity = {}
    if preserve_existing_identity and output.exists():
        existing = json.loads(output.read_text(encoding="utf-8-sig"))
        if not isinstance(existing, dict):
            raise ValueError("Existing dashboard snapshot must be a JSON object")
        identity = {key: existing[key] for key in IDENTITY_KEYS if key in existing}
        del existing
    connection = _connect_readonly(data_dir)
    database_paths = {name: data_dir / name for name in ("monitor.sqlite3", "images.sqlite3")}
    try:
        connection.execute("BEGIN")
        for schema in ("main", "images"):
            connection.execute(f"SELECT COUNT(*) FROM {schema}.sqlite_master").fetchone()
            if connection.execute(f"PRAGMA {schema}.journal_mode").fetchone()[0] != "delete":
                raise ValueError("Dashboard export requires the project's paired DELETE journal mode; no conversion attempted")
        database_before = {name: _hash(path) for name, path in database_paths.items()}
        runs = [dict(row) for row in connection.execute(RUNS_SQL)]
        observations = [dict(row) for row in connection.execute(OBSERVATIONS_SQL)]
        groups = [dict(row) for row in connection.execute(GROUPS_SQL)]
        members = [dict(row) for row in connection.execute(MEMBERS_SQL)]
        assets = [dict(row) for row in connection.execute(ASSETS_SQL)]
        links = {row["observation_id"]: dict(row) for row in connection.execute(LINKS_SQL)}
        query_finished_at = _utc_now()
        if not runs:
            raise ValueError("No observation runs exist; a blank database is not a reviewed dashboard")
        if len({row["observation_id"] for row in observations}) != len(observations):
            raise ValueError("Observation join changed the one-card grain")
        for asset in assets:
            content = asset.pop("data")
            if not isinstance(content, bytes) or len(content) != asset["byte_count"] or hashlib.sha256(content).hexdigest() != asset["sha256"]:
                raise ValueError(f"Stored image SHA-256/byte count validation failed: {asset['sha256']}")
            if _image_mime(content) != asset["mime"]:
                raise ValueError(f"Stored image MIME/signature validation failed: {asset['sha256']}")
            asset["data_url"] = ("/__pdd_image/" + asset["sha256"] if image_delivery == "local_http" else
                                 "data:" + asset["mime"] + ";base64," + base64.b64encode(content).decode("ascii"))
            asset["integrity_status"] = "verified_local"
        assets_by_sha = {asset["sha256"]: asset for asset in assets}
        runs_by_id = {run['run_id']: run for run in runs}
        rows_by_run = defaultdict(list)
        for row in observations:
            raw = json.loads(row["row_json"])
            row["original"] = raw
            row["observed_at_precision"] = raw.get("observedAtPrecision") or ("legacy_unspecified" if row["observed_at"] else "unknown")
            row["stored_eligible_gt10"] = bool(row["eligible_gt10"])
            row["reference_selection"] = _reference_selection(row)
            row["reference_group"] = _reference_group(row)
            if row["stored_eligible_gt10"] != (row["reference_group"] == "yipin_gt10" and row["sales_label"] == "已拼"):
                raise ValueError(f"Stored threshold differs from per-card sales fields: {row['observation_id']}")
            row["eligible_gt10"] = row["reference_group"] == "yipin_gt10"
            sha, link = row["asset_sha256"], links.get(row["observation_id"])
            saved = bool(sha and sha in assets_by_sha and link and link["run_id"] == row["run_id"]
                         and link["asset_sha256"] == sha and link["source_url"] == row["image_url"] and row["image_status"] == "saved")
            row["image_content_status"] = "verified_local" if saved else ("unavailable_or_invalid" if sha else "not_saved")
            row.update(image_content_kind=None, image_content_mime=None, image_acquisition_url=None, image_variant_provenance=None,
                       image_original_bytes_verified=False)
            if saved:
                from .store import validate_image_variant
                manifest = json.loads(link['manifest_json'])
                if not isinstance(manifest, dict):
                    raise ValueError('Stored image manifest must be an object')
                provenance = validate_image_variant(manifest, original_url=row['image_url'], title=row['title'],
                                                    snapshot_sha=runs_by_id[row['run_id']]['snapshot_sha256'])
                if provenance is not None and provenance.get('asset_sha256') != sha:
                    raise ValueError('Stored image variant SHA differs from attached image content')
                row.update(image_content_kind='store_search_variant' if provenance is not None else 'original_url_content',
                           image_content_mime=assets_by_sha[sha]['mime'],
                           image_acquisition_url=manifest['image_acquisition_url'] if provenance is not None else row['image_url'],
                           image_variant_provenance=provenance, image_original_bytes_verified=provenance is None)
            row["automatic_image_retry_allowed"] = False
            rows_by_run[row["run_id"]].append(row)
        complete = [run for run in runs if run["status"] == "complete" and run["end_boundary_observed"]]
        default = (complete or runs)[0]
        selection = "latest_complete_with_boundary" if complete else "latest_partial_fallback"
        for run in runs:
            raw_bytes = run.pop("snapshot_bytes")
            if not isinstance(raw_bytes, bytes) or hashlib.sha256(raw_bytes).hexdigest() != run["snapshot_sha256"]:
                raise ValueError(f"Stored snapshot SHA-256 validation failed: {run['run_id']}")
            original = json.loads(run.pop("snapshot_json"))
            if json.loads(raw_bytes) != original:
                raise ValueError(f"Stored snapshot text differs from original bytes: {run['run_id']}")
            run["snapshot_metadata"] = {key: value for key, value in original.items() if key != "rows"}
            run["end_boundary_observed"] = bool(run["end_boundary_observed"])
            run["scope"] = "complete_storefront" if run["status"] == "complete" and run["end_boundary_observed"] else "partial_storefront"
            run["default_selected"] = run["run_id"] == default["run_id"]
            run["card_count"] = len(rows_by_run[run["run_id"]])
            run["eligible_gt10_count"] = sum(row["eligible_gt10"] for row in rows_by_run[run["run_id"]])
            run["selection_counts"] = dict(Counter(row["reference_selection"] for row in rows_by_run[run["run_id"]]))
            run["collection_stop_reason"] = original.get("collectionEvidence", {}).get("stopReason")
            run["collection_timestamp_precision"] = original.get("collectionEvidence", {}).get("timestampPrecision")
            if run["card_count"] != len(original.get("rows", [])):
                raise ValueError(f"Stored card count differs from source snapshot: {run['run_id']}")
            # Raw image evidence remains in the DB; include originals in source
            # metadata only when needed, never repeat huge image manifests.
            for key in ("image_manifest_json", "image_queue_json", "detail_note_json"):
                run.pop(key, None)
        by_group = defaultdict(list)
        for member in members:
            by_group[member["group_id"]].append(member)
        for group in groups:
            group["members"] = by_group[group["group_id"]]
            group["member_observation_ids"] = [member["observation_id"] for member in group["members"]]
            group["same_design_confirmed"] = bool(group["same_design_confirmed"])
            if len(group["members"]) != group["member_count"]:
                raise ValueError(f"Candidate group member count mismatch: {group['group_id']}")
        database_after = {name: _hash(path) for name, path in database_paths.items()}
        if database_before != database_after:
            raise ValueError("Source databases changed during the reviewed read")
    finally:
        connection.close()
    windows = [{key: run[key] for key in ("run_id", "observed_from", "observed_to", "status", "end_boundary_observed", "snapshot_sha256")} for run in runs]
    files = [str(path) for path in database_paths.values()]
    queries = {
        "observations": {
            "rows": observations, "payloadColumns": ["row_json", "identity_evidence", "detail_note_json"],
            "methods": [{"language": "python", "code": "Preserve every SQL row. Decode row_json as original. observed_at_precision = original.get('observedAtPrecision') or ('legacy_unspecified' if observed_at else 'unknown'). Classify reference_selection per exactness/zero/positive; reference_group distinguishes yipin_gt10, yipin_eq10, yipin_1to9 and other_positive. Do not group cards or sum sales."}],
            "source": _source("逐轮独立商品卡片观察", OBSERVATIONS_SQL + ";\n" + LINKS_SQL, ["observations", "image_tasks", "images.source_links"], query_finished_at, files, [
                _definition("观察卡片", "一轮内每张原始卡片对应一条 observation；历史轮次可能重复看到同商品，不能累加为唯一商品数。", ["observations"]),
                _definition("主清单达标", "单张卡片 sales_label为已拼/已抢、sales_unit=件、sales_precision=exact_display 且 sales_value>10；等于10、模糊或缺失均不达标。", ["observations"], variable="eligible_gt10"),
                _definition("精确正数参考", "sales_precision=exact_display、sales_value>0 且标签为已拼/已抢/总售/已售/售出之一的独立卡片；各标签和单位分别展示，不能相加为已拼销量。", ["observations"]),
                _definition("临界观察", "单卡精确已拼=10件，只作临界参考，不进入 >10 主清单。", ["observations"]),
                _definition("未知展示", "缺失或 non_exact_or_unparsed 的销量不视为零，不纳入精确正数；原始卡片仍保留供检查。", ["observations"]),
                _definition("销量原文与未知", "sales_raw 原文独立保存。已拼/已抢原文与原标签保留并按同一指标展示；总售、已售、售出与单位分别保留；缺失仍为 null，未自动补零。", ["observations"]),
                _definition("展示价", "price_raw 是原卡片展示价，不表示已核实同规格到手价。", ["observations"]),
                _definition("商品图片来源", "image_url保留原卡URL。image_content_kind区分original_url_content和store_search_variant；后者为店内搜索同文件图片，image_acquisition_url为实际获取URL，image_variant_provenance保留来源证明。verified_local仅表示附带图片BLOB校验通过，不表示变体等于原URL字节。", ["observations", "image_tasks", "images.source_links", "images.assets"]),
            ], "one raw card per run; observation_id", windows),
        },
        "runs": {
            "rows": runs,
            "methods": [{"language": "python", "code": "Verify snapshot_bytes SHA-256 against snapshot_sha256, decode snapshot_json and verify it matches original bytes. Keep metadata except rows in snapshot_metadata. Count observations independently by run_id. Sort runs by the SQL order; select the first complete run with end_boundary_observed, else the first run with an explicit partial-fallback flag. Never sum runs into product counts."}],
            "source": _source("已存观察轮次与覆盖范围", RUNS_SQL, ["runs", "shops"], query_finished_at, files, [
                _definition("默认轮次", "按 observed_to_epoch、observed_from_epoch、run_id 降序选择最近 status=complete 且 end_boundary_observed=true 的轮次；没有完整轮时明确回退最近部分轮。", ["runs"]),
                _definition("观察窗口", "observed_from/to 是来源页面观察起止；generatedAt/executedAt 是当前导出查询时间，两者不可互换。", ["runs"]),
                _definition("每轮卡片数", "card_count 是该 run_id 的独立观察行数，各轮分别计数；部分轮次不能据此判断全店规模或下架。", ["runs", "observations"]),
            ], "one observation run; run_id", windows),
        },
        "candidate_groups": {
            "rows": groups, "payloadColumns": ["evidence_json"],
            "source": _source("同标题多链接候选", GROUPS_SQL + ";\n" + MEMBERS_SQL + ";", ["candidate_groups", "group_members"], query_finished_at, files, [
                _definition("候选组", "保留既有候选组及各独立观察成员；同标题只表示疑似相似，不确认同设计或同 SKU，不求和凑销量门槛。", ["candidate_groups", "group_members"]),
            ], "one existing title candidate group per run; group_id", windows),
            "methods": [{"language": "python", "code": "Attach group_members rows by group_id as members and their observation_id values as member_observation_ids. Preserve is_trigger per member; do not sum sales."}],
        },
        "image_assets": {
            "rows": assets, "payloadColumns": ["data_url"],
            "source": _source("已有本地图像内容", ASSETS_SQL + ";\n" + LINKS_SQL + ";", ["images.assets", "images.source_links"], query_finished_at, files, [
                _definition("已校验图片内容", "对已有 BLOB 校验 byte_count、SHA-256 及 PNG/JPEG/GIF/WebP 文件签名与 MIME 一致后" + ("由本机按内容 SHA 按需读取；请求时再次校验。" if image_delivery == "local_http" else "编码 data URL；") + "无下载，无自动重试。", ["images.assets"]),
                _definition("图片去重", "sha256 是内容身份；多个卡片可引用同一内容，图片去重不合并卡片或销量。", ["images.assets", "images.source_links"]),
            ], "one stored image BLOB per sha256", windows),
            "methods": [{"language": "python", "code": "assert len(data) == byte_count; assert hashlib.sha256(data).hexdigest() == sha256; assert _image_mime(data) == mime; " + ("data_url = '/__pdd_image/' + sha256  # local HTTP reader revalidates stored BLOBs" if image_delivery == "local_http" else "data_url = 'data:' + mime + ';base64,' + base64.b64encode(data).decode('ascii')")}],
        },
    }
    # This metric's physical lineage also includes observations.
    queries["runs"]["source"]["tables"].append("observations")
    history = build_history(runs, observations)
    history_definitions = [
        _definition("历史时段", "按观察结束时间转换Asia/Shanghai；12点前AM、12点起PM。上一轮为同店更早结束的最近轮；昨日同档只取昨日相同档，优先完整含边界，否则明确部分样本，无对应轮则missing_baseline。", ["runs"]),
        _definition("跨轮展示数变化", "两轮内goods_id均合法、唯一、无冲突；同销量指标和单位、精确非负整数、观察窗口及逐卡时间正确有序才计算展示差值。负差标异常；窗口精度仅给时长上下界，缺值不补零。", ["runs", "observations"]),
        _definition("逐卡保留", "每种比较中所有新旧观察各出现一次；缺ID和重复ID不合并，首见不称上架，未见不称下架。标题和原图URL完全相同仅提供独立身份待核验线索，多对多不择一。", ["observations"]),
    ]
    for query_id, label, grain in (
        ("run_history", "逐轮历史与早晚覆盖", "one historical observation run; run_id"),
        ("comparison_summaries", "指定基线的比较覆盖与未知原因", "one target run and baseline mode; comparison_id"),
        ("comparison_items", "每张原始卡片的比较证据", "one verified identity pair or one unmatched original card per comparison; comparison_item_id"),
    ):
        source = _source(label, RUNS_SQL + ";\n" + OBSERVATIONS_SQL + ";", ["runs", "shops", "observations"], query_finished_at,
                         files + [str(Path(__file__).with_name("history.py").resolve())], history_definitions, grain, windows)
        source["filters"] = ["保留全部已存轮次；更新与变化默认最近观察轮，商品池默认最近完整轮。", "每次比较保留新旧全部卡片，筛选范围另列，无 LIMIT 截断。"]
        source.update(classification="derived", caveats=LIMITATIONS + HISTORY_LIMITATIONS,
                      derivation="pdd_monitor.history.build_history(runs, observations); immutable source rows, no writes or network")
        queries[query_id] = {"rows": history[query_id], "source": source,
                             "methods": [{"language": "python", "code": "from pdd_monitor.history import build_history\nhistory = build_history(runs, observations)\nrows = history[" + repr(query_id) + "]"}]}
    queries["comparison_items"]["payloadColumns"] = ["identity_clue_candidates"]
    collection_queries, collection_scheduler = build_collection_queries(data_dir.resolve().parent)
    queries.update(collection_queries)
    arrivals, arrival_tracking = build_portfolio_arrivals(data_dir.resolve().parent, runs, observations)
    arrival_files = files + [str(Path(__file__).with_name("new_arrivals.py").resolve()),
                             str(Path(__file__).with_name("portfolio_tracking.py").resolve()),
                             *arrival_tracking["config_files_sha256"]]
    arrival_definitions = [
        _definition("固定存量起点", "每家店分别显式保存固定run ID、原快照SHA、观察窗口与开始时刻；原店起点保留，新店未配置时不继承别店起点。重建面板不移动起点。", ["runs", "observations"]),
        _definition("起点后首次观察", "可靠唯一goods_id首次出现仅是首次观察ID候选；缺ID时新标题与原图URL组合只作独立卡片线索。相同标题/图片不确认商品身份，同轮多卡保留，不累加销量。", ["observations"]),
        _definition("大概出现时间", "标注首次实际读取时刻或该轮观察窗口；完整且身份全覆盖的前次未见轮可提供采样之间的参考范围，不是实际上架时间。缺证据不给区间下限。", ["runs", "observations"]),
        _definition("后续跟进", "首次发现锚点保持不变。可靠唯一ID的后续卡与同标题原图的外观线索分别保留，后续多张卡不择一、不合并为商品或销量。", ["observations"]),
    ]
    for query_id, label, grain in (
        ("new_arrival_items", "固定起点后的首次发现及后续观察", "one first-observed card anchor after the fixed tracking start; not unique products"),
        ("new_arrival_summary", "新品关注起点与实际采集覆盖", "one shop with an explicit fixed tracking configuration or a not_configured status"),
    ):
        source = _source(label, RUNS_SQL + ";\n" + OBSERVATIONS_SQL + ";", ["runs", "shops", "observations"],
                         query_finished_at, arrival_files, arrival_definitions, grain, windows)
        source.update(classification="derived", filters=["固定起点存量默认排除；起点后首次卡片锚点持续保留，旧原卡仍在历史查询。"],
                      derivation="pdd_monitor.portfolio_tracking.build_portfolio_arrivals(project, runs, observations); read-only per shop")
        source["caveats"] = [*source["caveats"], "首次看到不等于新品上架；首次卡片线索不确认唯一商品。", "没有起点后采集时是等待新数据，不能解释为无新增。"]
        queries[query_id] = {"rows": arrivals[query_id], "source": source,
                             "methods": [{"language": "python", "code": "result, metadata = build_portfolio_arrivals(project, runs, observations)\nrows = result[" + repr(query_id) + "]"}]}
    model = build_competitor_model(runs, observations, groups, history, arrivals)
    model_definitions = [
        _definition("每店参考轮", "每店最新完整且含结束边界的一轮；没有完整轮则显式回退最新部分轮。最新观察窗口另外列出，不称旧完整轮为当前全量。", ["shops", "runs"]),
        _definition("多维商品结构", "每店参考轮全部原卡；销量标签单位分开，严格已拼>10；单值原展示价分金额与券条件，标题关键词只是可追溯规则线索。分子、分母与observation_ids均保留。", ["runs", "observations", "candidate_groups", "group_members"]),
        _definition("策略线索", "可观察事实、候选解释、建议核验动作分开；规则版本与证据原卡可查。不推定经营意图、真实订单、利润或同SKU。不同店不按不同观察时间的样本直接排名。", ["runs", "observations"]),
    ]
    for query_id, label, grain in (
        ("competitor_shops", "各店观察范围与统一指标", "one observed shop, one reference run plus separately identified latest run"),
        ("competitor_products", "各店参考轮完整原卡及分析维度", "one original observation in each shop reference run"),
        ("competitor_dimensions", "各店可下钻的维度分布", "one shop, reference run, dimension and mutually defined category"),
        ("competitor_strategy", "证据支持的策略候选与跟进动作", "one rule-based observation/hypothesis per shop with independent source cards"),
    ):
        source = _source(label, RUNS_SQL + ";\n" + OBSERVATIONS_SQL + ";\n" + GROUPS_SQL + ";\n" + MEMBERS_SQL,
                         ["shops", "runs", "observations", "candidate_groups", "group_members"], query_finished_at,
                         arrival_files + [str(Path(__file__).with_name("competitor_model.py").resolve())], model_definitions, grain, windows)
        source.update(classification="derived", filters=["按shop_id隔离；每店独立选择参考轮，全部原卡保留，不按同标题/图片/跨店ID合并。"],
                      derivation="pdd_monitor.competitor_model.build_competitor_model(runs, observations, groups, history, arrivals)")
        source["caveats"] = [*source["caveats"], "跨店窗口和完整性不同，只并列样本，不虚构同日经营排名。", "策略是规则支持的待验证解释，不是对手真实经营意图。"]
        queries[query_id] = {"rows": model[query_id], "source": source,
                             "methods": [{"language": "python", "code": "model = build_competitor_model(runs, observations, groups, history, arrivals)\nrows = model[" + repr(query_id) + "]"}]}
    artist_catalog, artist_fingerprints = load_catalog(data_dir.resolve().parent)
    artist_data = build_artist_research(artist_catalog, model['competitor_shops'], runs, observations)
    for query_id, label, grain in (
        ('artist_watchlist', '明星称呼、标题题材与公开来源入口', 'one shop and reviewed title entity across reference and latest run'),
        ('artist_research_summary', '艺人题材覆盖与来源研究边界', 'one shop reference run; distinct original cards'),
        ('artist_source_channels', '公开信息渠道与使用范围', 'one reviewed public channel; independent of store'),
    ):
        source = _source(label, RUNS_SQL + ';\n' + OBSERVATIONS_SQL, ['shops','runs','observations'],
                         query_finished_at, [*files,*artist_fingerprints],
                         [_definition('艺人标题线索', '按已审阅称呼词表逐卡匹配；长姓名优先，电竞需明确上下文。多人卡分别提及，不相加销量。参考轮与最新部分轮分开，标题日期不是活动或上架日期。', ['observations']),
                          _definition('公开入口', '人工检索核对公开本人、工作室、机构或渠道；索引可能滞后，查验日不是最后更新日。公开信息不证明竞品实际取图或已获得商品化许可。', [*artist_fingerprints])], grain, windows)
        source.update(classification='derived', provider='Local SQLite + reviewed public web directory',
                      filters=['每店参考轮与最新轮的已审阅人物称呼；词表未覆盖名称保留未知。'],
                      derivation='pdd_monitor.artist_research.build_artist_research(catalog, competitor_shops, runs, observations)')
        source['caveats'] = [*source['caveats'], '本页是公开信息入口目录，不是当前完整活动日历；没有新增网站采集、自动关注或定时任务。',
                             '来源核实与素材商品化许可独立；本次未核验图片许可。']
        source['links'] = [{'label':row['label'], 'url':row['url']} for row in (artist_catalog.get('channels',[]) if query_id=='artist_source_channels' else artist_catalog.get('sources',[]))]
        source['evidenceFlow'] = [{'title':'入口核查', 'detail':'检索与身份依据、原站链接和检查日期保存在目录每个来源行；原始研究文件归档于reports/artist_research_20261004。'},
                                  {'title':'逐卡派生', 'detail':'按本店参考轮/最新轮独立匹配目录中的名称与题材词；卡片ID用于下钻，不生成实际活动日期或总销量。'}]
        if query_id == 'artist_source_channels':
            source.update(provider='Reviewed public web directory', files=list(artist_fingerprints), tables=[],
                          filters=['公共入口目录，不按店铺或采集轮次筛选。'],
                          metricDefinitions=[_definition('公共信息入口', '人工核对的工作活动发布渠道；是来源目录，不是活动日历或本店采集结果。', list(artist_fingerprints))],
                          caveats=['公开渠道可能需要登录，索引内容可能滞后；当前活动须逐条打开复核。','公开图片与商品化许可分别核实。'],
                          evidenceFlow=[source['evidenceFlow'][0]])
            source.pop('sql', None)
            source.pop('observationWindows', None)
        queries[query_id] = {'rows':artist_data[query_id], 'source':source,
                             'methods':[{'language':'python','code':'catalog, fingerprints = load_catalog(project)\nresult = build_artist_research(catalog, shops, runs, observations)\nrows = result[' + repr(query_id) + ']'}]}
    queries.update(build_heat_queries(data_dir.resolve().parent, catalog=artist_catalog))
    targets, registry_fingerprints = build_targets(data_dir.resolve().parent, runs, queries['collection_attempts']['rows'])
    queries["competitor_targets"] = {"rows": targets, "source": {
        "label": "已登记竞品与实际数据接入状态", "provider": "Local JSON + SQLite", "classification": "derived",
        "files": [*registry_fingerprints, *files, *queries['collection_attempts']['source'].get('files', []),
                  str(Path(__file__).with_name('shop_tracking.py').resolve())], "tables": ["shops", "runs"], "sql": RUNS_SQL,
        "executedAt": query_finished_at, "timezone": "Asia/Shanghai", "grain": "one registered target or unregistered observed shop",
        "filters": ["名称或未解析分享链接不自动按名字绑定真实店铺；登记不代表采集成功。"],
        "metricDefinitions": [_definition("竞品接入状态", "持久登记文件加已观察轮次；实际存在相同稳定shop_id的run才为observed，其余pending_capture/needs_identity。", ["shops", "runs"]),
                              _definition("逐店跟踪设置", "每店独立保存enabled/paused及版本号；缺文件为unconfigured。暂停保留商品、历史与原固定起点，开启不会触发网站采集。", [*registry_fingerprints]),
                              _definition("下一步流程", "按本店身份、设置、最新轮、完整边界与可归属本店的真实采集日志推导；无shop_id或本店run_id的日志不强行归属。部分轮始终提示补扫。", ["runs", "collection_attempts"])],
        "derivation": "pdd_monitor.competitor_registry.build_targets(project, runs, collection_attempts); per-shop settings and evidence-derived workflow",
        "caveats": ["登记和开启跟踪都不等于已采集；仅状态保存成功也不能冒称面板构建成功。", "逐店自动采集连接待完成；当前全局早晚槽位不能证明每家店已分别到点运行。", "新设置不重置固定新品起点、不删除历史；部分轮不视为完整扫描。"]}}
    queries.update(build_opportunity_queries(queries, project=data_dir.resolve().parent))
    queries.update(build_trial_queries(data_dir.resolve().parent))
    queries.update(build_product_table_queries(queries))
    queries.update(build_warehouse_queries(queries))
    from .trend_dashboard import build_trend_queries
    queries.update(build_trend_queries(queries, data_dir.resolve().parent))
    snapshot = {
        "surface": "dashboard", "title": "拼多多多店竞品分析", "generatedAt": _utc_now(), "status": "reviewed",
        "filters": [{"id": "run_id", "label": "观察轮次", "field": "run_id", "defaultValue": default["run_id"]}],
        "metadata": {"schema_version": 1, "timezone": "Asia/Shanghai", "grain": "one original card per observation run",
                     "default_run_id": default["run_id"], "default_run_selection": selection,
                     "default_run_is_partial": not bool(complete), "latest_run_id": runs[0]["run_id"],
                     "source_database_sha256": database_before, "source_database_sha256_after": database_after,
                     "database_writes_performed": False, "network_requests_performed": False,
                     "all_observation_row_count": len(observations), "run_count": len(runs),
                     "cross_run_growth_status": "derived_evidence", "history_schema_version": 1,
                     "history_comparison_modes": ["previous", "yesterday_slot"], "collection_scheduler": collection_scheduler,
                     "new_arrival_tracking": arrival_tracking,
                     "competitor_model_version": "pdd_competitor_v1", "profit_model_version": "pdd_profit_lab_v1", "default_shop_id": default["shop_id"],
                     "shop_count": len(model["competitor_shops"]), "competitor_registry_sha256": registry_fingerprints,
                     "artist_research_catalog_sha256": artist_fingerprints, "artist_research_version": artist_catalog.get('version'),
                     "scheduled_scope": "legacy_global_slot; not proof of per-shop scheduled collection",
                     "limitations": LIMITATIONS + HISTORY_LIMITATIONS},
        "queries": queries,
        **identity,
    }
    if not write_output:
        return snapshot
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n", suffix=".tmp", prefix=output.name + ".", dir=output.parent, delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(snapshot, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(output)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()
    return snapshot


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", default="data", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    snapshot = build_dashboard_snapshot(args.data_dir, args.output)
    print(json.dumps({"status": "saved", "output": str(args.output.resolve()),
                      "default_run_id": snapshot["metadata"]["default_run_id"],
                      "query_row_counts": {key: len(query["rows"]) for key, query in snapshot["queries"].items()},
                      "database_writes_performed": False, "network_requests_performed": False}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
