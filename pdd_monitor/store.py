"""Offline SQLite storage of card-level storefront evidence.

Only Python's standard library is used. Nothing in this module opens a network
connection or retries an image request. Frontend sales labels remain distinct;
their values are displayed counts, never inferred unique buyers or orders.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import sqlite3
import stat
from typing import Any
from urllib.parse import parse_qs, urlsplit
import zipfile


SCHEMA_VERSION = 1
MAX_JSON_BYTES = 32 * 1024 * 1024
MAX_ARCHIVE_BYTES = 256 * 1024 * 1024
MAX_IMAGE_BYTES = 32 * 1024 * 1024
MAX_MANIFEST_BYTES = 4 * 1024 * 1024
MAX_ARCHIVE_ENTRIES = 10000
MAX_CARDS = 100000
MAX_COMPRESSION_RATIO = 250
SHIPPING_SUFFIX = re.compile(r"(?:【(?:\d+天内发货|\d+月\d+日发完)】)$")
EXACT_SALES = re.compile(r"^(已拼|已抢|总售|已售|售出)\s*([0-9]+)\s*(件|人|单)$")
SALES_LABEL = re.compile(r"^(已拼|已抢|总售|已售|售出)")
GOODS_ID = re.compile(r"^[1-9][0-9]*$")
OBSERVATION_PRECISIONS = frozenset({"card_read", "batch_read", "run_window", "unknown"})


class SnapshotConflictError(ValueError):
    """A different immutable snapshot already occupies this observation window."""


class EvidenceValidationError(ValueError):
    """Input evidence is malformed, unsafe, or contradicts its provenance."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def _text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, (dict, list, bool)):
        raise EvidenceValidationError("Expected a text or numeric scalar, not a container or boolean")
    result = str(value).strip()
    return result or None


def _epoch(value: Any, field: str, *, required: bool = True) -> float | None:
    value = _text(value)
    if not value:
        if required:
            raise EvidenceValidationError(f"Missing source observation time: {field}")
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError("Timezone is required")
        return parsed.timestamp()
    except (ValueError, OverflowError) as exc:
        raise EvidenceValidationError(f"Invalid timezone-aware source observation time: {field}") from exc


def _read_json(path: str | Path, maximum: int = MAX_JSON_BYTES) -> tuple[dict, bytes, str]:
    path = Path(path)
    if path.stat().st_size > maximum:
        raise EvidenceValidationError(f"JSON exceeds size limit: {path.name}")
    with path.open("rb") as handle:
        raw = handle.read(maximum + 1)
    if len(raw) > maximum:
        raise EvidenceValidationError("JSON exceeds size limit")
    try:
        text = raw.decode("utf-8-sig")
        value = json.loads(text, parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
    except (UnicodeError, ValueError) as exc:
        raise EvidenceValidationError(f"Invalid UTF-8 JSON: {path.name}") from exc
    if not isinstance(value, dict):
        raise EvidenceValidationError(f"Expected a JSON object: {path.name}")
    return value, raw, text


def normalize_title(title: str) -> str:
    """Remove only the documented trailing shipping promise, preserving attributes."""
    return SHIPPING_SUFFIX.sub("", title.strip()).strip()


def parse_sales(sales_raw: Any) -> dict:
    """Only unambiguous integral displays are exact; 10+, 1万, and blanks are not."""
    raw = _text(sales_raw)
    if raw is None:
        return {"label": None, "value": None, "unit": None, "precision": "missing", "eligible_gt10": False}
    exact = EXACT_SALES.fullmatch(raw)
    if exact and len(exact.group(2)) <= 18:
        label, number, unit = exact.groups()
        value = int(number)
        return {"label": label, "value": value, "unit": unit, "precision": "exact_display",
                "eligible_gt10": label == "已拼" and unit == "件" and value > 10}
    label = SALES_LABEL.match(raw)
    return {"label": label.group(1) if label else None, "value": None, "unit": None,
            "precision": "non_exact_or_unparsed", "eligible_gt10": False}


def connect(data_dir: str | Path) -> sqlite3.Connection:
    """Open both disk databases. The caller owns and must close the connection.

    DELETE journals and FULL synchronization retain SQLite's attached-database
    transaction semantics. WAL is deliberately not used across these two files.
    """
    directory = Path(data_dir).expanduser().resolve()
    directory.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(directory / "monitor.sqlite3"), timeout=30, isolation_level=None)
    try:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        connection.execute("PRAGMA main.journal_mode = DELETE")
        connection.execute("PRAGMA main.synchronous = FULL")
        connection.execute("ATTACH DATABASE ? AS images", (str(directory / "images.sqlite3"),))
        connection.execute("PRAGMA images.journal_mode = DELETE")
        connection.execute("PRAGMA images.synchronous = FULL")
        return connection
    except BaseException:
        connection.close()
        raise


def initialize(data_dir: str | Path) -> None:
    """Create the schema without altering existing evidence."""
    schema_path = Path(__file__).resolve().parent.parent / "schema.sql"
    with closing(connect(data_dir)) as connection:
        main_version = connection.execute("PRAGMA main.user_version").fetchone()[0]
        image_version = connection.execute("PRAGMA images.user_version").fetchone()[0]
        if main_version not in (0, SCHEMA_VERSION) or image_version not in (0, SCHEMA_VERSION):
            raise EvidenceValidationError("Unsupported database schema version; no migration was attempted")
        if main_version == SCHEMA_VERSION and image_version == SCHEMA_VERSION:
            return
        connection.executescript("BEGIN IMMEDIATE;\n" + schema_path.read_text(encoding="utf-8") +
                                 "\nPRAGMA main.user_version = 1;\nPRAGMA images.user_version = 1;\nCOMMIT;")


def _connect_readonly(data_dir: str | Path) -> sqlite3.Connection:
    directory = Path(data_dir).expanduser().resolve()
    main_path, image_path = directory / "monitor.sqlite3", directory / "images.sqlite3"
    if not main_path.is_file() or not image_path.is_file():
        raise ValueError("Both monitor.sqlite3 and images.sqlite3 must exist; import a snapshot or restore a matched backup")
    connection = sqlite3.connect(main_path.as_uri() + "?mode=ro", uri=True, timeout=30, isolation_level=None)
    try:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("ATTACH DATABASE ? AS images", (image_path.as_uri() + "?mode=ro",))
        connection.execute("PRAGMA query_only = ON")
        if (connection.execute("PRAGMA main.user_version").fetchone()[0] != SCHEMA_VERSION or
                connection.execute("PRAGMA images.user_version").fetchone()[0] != SCHEMA_VERSION):
            raise EvidenceValidationError("Unsupported or uninitialized database schema")
        return connection
    except BaseException:
        connection.close()
        raise


def shop_identity_evidence(snapshot: dict) -> dict:
    """Resolve stable storefront evidence, rejecting display-name identity.

    Numeric mall_id and opaque mall_sn are distinct evidence types. The historic
    id:<value> hash remains unchanged for replay compatibility; import separately
    refuses a cross-type hash collision instead of merging unrelated stores.
    """
    source = _text(snapshot.get("sourceUrl")) or ""
    try:
        query = parse_qs(urlsplit(source).query, keep_blank_values=True)
    except ValueError as error:
        raise EvidenceValidationError("Invalid storefront sourceUrl") from error
    def one_query_value(key):
        values = query.get(key, [])
        if not values:
            return None
        if any(not value or value != value.strip() for value in values) or len(set(values)) != 1:
            raise EvidenceValidationError(f"Ambiguous or empty storefront {key}")
        return values[0]
    url_id, url_sn = one_query_value("mall_id"), one_query_value("mall_sn")
    identifiers = [(field, _text(snapshot.get(field))) for field in ("shopId", "mallId") if snapshot.get(field) is not None]
    if url_id is not None:
        identifiers.append(("sourceUrl.mall_id", url_id))
    if any(not value or not GOODS_ID.fullmatch(value) for _, value in identifiers):
        raise EvidenceValidationError("Explicit shopId/mallId and URL mall_id must be positive integer identifiers")
    if len({value for _, value in identifiers}) > 1:
        raise EvidenceValidationError("Conflicting explicit and URL storefront mall_id identifiers")
    if identifiers:
        kind, identifier = "mall_id", identifiers[0][1]
        basis = [field for field, _ in identifiers]
    elif url_sn is not None:
        kind, identifier, basis = "mall_sn", url_sn, ["sourceUrl.mall_sn"]
    else:
        raise EvidenceValidationError("A stable storefront mall_id or mall_sn is required; shopName cannot identify a new store")
    return {"shop_id": "shop_" + hashlib.sha256(("id:" + identifier).encode("utf-8")).hexdigest()[:24],
            "identity_kind": kind, "stable_identifier": identifier, "identity_basis": basis,
            "source_url": source or None, "source_mall_id": url_id, "source_mall_sn": url_sn,
            "hash_scheme": "legacy_id_value_sha256_24", "typed_namespace_enforced_at_import": True}


def _shop_identity(snapshot: dict) -> str:
    return shop_identity_evidence(snapshot)["shop_id"]


def _check_existing_shop_identity(connection, evidence):
    """Do not let the legacy untyped hash join numeric IDs with opaque tokens."""
    for row in connection.execute("SELECT snapshot_json FROM runs WHERE shop_id=?", (evidence["shop_id"],)):
        try:
            existing = shop_identity_evidence(json.loads(row["snapshot_json"]))
        except (ValueError, TypeError) as error:
            raise EvidenceValidationError("Existing shop identity cannot be reverified; manual migration is required") from error
        if (existing["identity_kind"], existing["stable_identifier"]) != (evidence["identity_kind"], evidence["stable_identifier"]):
            raise EvidenceValidationError("Legacy shop hash collides across mall_id/mall_sn namespaces; stores were not merged")


def _prepare_snapshot(snapshot: dict) -> tuple[list[dict], float, float]:
    if _text(snapshot.get("status")) == "complete" and snapshot.get("endBoundaryObserved") is not True:
        raise EvidenceValidationError("A complete snapshot requires endBoundaryObserved=true")
    rows = snapshot.get("rows")
    if not isinstance(rows, list) or not rows or len(rows) > MAX_CARDS:
        raise EvidenceValidationError("rows must be a nonempty bounded list of individual card records")
    observed_from = _epoch(snapshot.get("observedFrom"), "observedFrom")
    observed_to = _epoch(snapshot.get("observedTo"), "observedTo")
    if observed_to < observed_from:
        raise EvidenceValidationError("Observation window ends before it starts")
    prepared = []
    views = set()
    record_keys = set()
    for index, row in enumerate(rows, 1):
        if not isinstance(row, dict):
            raise EvidenceValidationError(f"Row {index} is not an object")
        view = row.get("viewOrder", index)
        if isinstance(view, bool) or not isinstance(view, int) or view <= 0 or view in views:
            raise EvidenceValidationError("Each card requires a unique positive integer viewOrder")
        views.add(view)
        record_key = _text(row.get("recordKey"))
        if record_key:
            if record_key in record_keys:
                raise EvidenceValidationError("Duplicate nonempty recordKey makes card evidence ambiguous")
            record_keys.add(record_key)
        title = _text(row.get("title")) or ""
        raw_goods_id = _text(row.get("goodsId"))
        goods_id = raw_goods_id if raw_goods_id and GOODS_ID.fullmatch(raw_goods_id) else None
        goods_url = _text(row.get("goodsUrl"))
        identity_status = "unique_goods_id" if goods_id else "unknown_no_goods_id"
        if raw_goods_id not in (None, "0") and goods_id is None:
            identity_status = "unknown_invalid_goods_id"
        if goods_id and goods_url:
            try:
                url_ids = parse_qs(urlsplit(goods_url).query, keep_blank_values=True).get("goods_id", [])
            except ValueError:
                url_ids = [None]
            if url_ids and (len(set(url_ids)) != 1 or url_ids[0] != goods_id):
                identity_status = "conflict_goods_url"
        observed_at = _text(row.get("observedAt"))
        precision = row.get("observedAtPrecision")
        if precision is not None and (not isinstance(precision, str) or precision not in OBSERVATION_PRECISIONS):
            raise EvidenceValidationError(f"rows[{view}].observedAtPrecision is unsupported")
        if precision in ("card_read", "batch_read") and not observed_at:
            raise EvidenceValidationError(f"rows[{view}] declares a read time precision without observedAt")
        row_time = _epoch(observed_at, f"rows[{view}].observedAt", required=False)
        # Out-of-window times remain raw evidence but cannot establish growth.
        if row_time is not None and not observed_from <= row_time <= observed_to:
            row_time = None
        prepared.append({"raw": row, "view_order": view, "title": title,
                         "normalized_title": normalize_title(title), "goods_id": goods_id,
                         "goods_url": goods_url, "identity_status": identity_status,
                         "observed_at": observed_at, "observed_at_epoch": row_time,
                         "sales": parse_sales(row.get("salesRaw")), "image_url": _text(row.get("imageUrl"))})
    id_counts = Counter(row["goods_id"] for row in prepared if row["goods_id"])
    for row in prepared:
        if row["goods_id"] and id_counts[row["goods_id"]] > 1:
            row["identity_status"] = "conflict_duplicate_goods_id"
    return prepared, observed_from, observed_to


def _safe_zip_name(name: str) -> bool:
    if not name or "\\" in name or "\x00" in name or ":" in name:
        return False
    path = PurePosixPath(name)
    return not path.is_absolute() and ".." not in path.parts and "." not in path.parts


def _image_mime(data: bytes) -> str:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    raise EvidenceValidationError("Archive member is not a recognized raster image (PNG/JPEG/GIF/WebP)")


def validate_image_variant(item: dict, *, original_url: str, title: str, snapshot_sha: str | None = None) -> dict | None:
    """Preserve the store error contract for the shared image evidence check."""
    from .image_variants import validate_image_variant as validate_variant
    try:
        return validate_variant(item, original_url=original_url, title=title, snapshot_sha=snapshot_sha)
    except ValueError as exc:
        raise EvidenceValidationError(str(exc)) from exc


def _load_archive(path: str | Path | None, rows: list[dict], *, snapshot_sha: str | None = None) -> tuple[list[dict], str | None]:
    if path is None:
        return [], None
    path = Path(path)
    if path.stat().st_size > MAX_ARCHIVE_BYTES:
        raise EvidenceValidationError("Image archive exceeds compressed size limit")
    by_view = {row["view_order"]: row for row in rows}
    loaded = []
    try:
        with zipfile.ZipFile(path) as archive:
            infos = archive.infolist()
            if len(infos) > MAX_ARCHIVE_ENTRIES:
                raise EvidenceValidationError("Too many archive members")
            if len({info.filename for info in infos}) != len(infos):
                raise EvidenceValidationError("Duplicate archive member paths")
            if sum(info.file_size for info in infos) > MAX_ARCHIVE_BYTES:
                raise EvidenceValidationError("Image archive exceeds total uncompressed size limit")
            for info in infos:
                if not _safe_zip_name(info.filename) or stat.S_ISLNK(info.external_attr >> 16):
                    raise EvidenceValidationError("Unsafe archive member path or symbolic link")
                if info.flag_bits & 1:
                    raise EvidenceValidationError("Encrypted archive members are not supported")
                if info.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
                    raise EvidenceValidationError("Unsupported archive compression type")
                if info.file_size > MAX_IMAGE_BYTES:
                    raise EvidenceValidationError("Archive member exceeds size limit")
                if info.file_size / max(info.compress_size, 1) > MAX_COMPRESSION_RATIO:
                    raise EvidenceValidationError("Archive compression ratio exceeds safety limit")
            members = {info.filename: info for info in infos}
            manifest_info = members.get("manifest.json")
            if not manifest_info or manifest_info.file_size > MAX_MANIFEST_BYTES:
                raise EvidenceValidationError("Missing or oversized manifest.json")
            manifest_text = archive.read("manifest.json").decode("utf-8-sig")
            manifest = json.loads(manifest_text)
            items = manifest.get("items") if isinstance(manifest, dict) else None
            if not isinstance(items, list) or len(items) > MAX_CARDS:
                raise EvidenceValidationError("Manifest items must be a bounded list")
            if "availableCardImageCount" in manifest and manifest["availableCardImageCount"] != len(items):
                raise EvidenceValidationError("Manifest card count does not match its items")
            seen = set()
            for item in items:
                if not isinstance(item, dict):
                    raise EvidenceValidationError("Manifest item is not an object")
                view = item.get("viewOrder")
                if isinstance(view, bool) or not isinstance(view, int) or view not in by_view or view in seen:
                    raise EvidenceValidationError("Manifest viewOrder must identify one unambiguous snapshot card")
                seen.add(view)
                row = by_view[view]
                if item.get("originalImageUrl") != row["image_url"] or not row["image_url"]:
                    raise EvidenceValidationError(f"Manifest image source URL mismatch at viewOrder {view}")
                if "title" in item and item["title"] != row["raw"].get("title"):
                    raise EvidenceValidationError(f"Manifest title mismatch at viewOrder {view}")
                validate_image_variant(item, original_url=row['image_url'], title=row['raw'].get('title'),
                                       snapshot_sha=snapshot_sha or manifest.get('snapshotSha256'))
                member = item.get("archivePath")
                if not isinstance(member, str) or not _safe_zip_name(member) or member not in members or members[member].is_dir():
                    raise EvidenceValidationError("Invalid or missing manifest archivePath")
                claimed = item.get("sha256")
                if not isinstance(claimed, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", claimed):
                    raise EvidenceValidationError("Manifest SHA256 must contain 64 hexadecimal characters")
                data = archive.read(member)
                digest = hashlib.sha256(data).hexdigest()
                if digest != claimed.lower():
                    raise EvidenceValidationError(f"Manifest SHA256 mismatch at viewOrder {view}")
                loaded.append({"view_order": view, "data": data, "sha256": digest, "mime": _image_mime(data),
                               "source_url": row["image_url"], "archive_path": member, "manifest_json": _json(item)})
            return loaded, manifest_text
    except (zipfile.BadZipFile, UnicodeError, json.JSONDecodeError, KeyError, RuntimeError) as exc:
        raise EvidenceValidationError("Unreadable or invalid image archive") from exc


def _load_queue(path: str | Path | None, snapshot: dict, rows: list[dict]) -> tuple[dict[int, dict], str | None]:
    if path is None:
        return {}, None
    queue, _, queue_text = _read_json(path)
    window = queue.get("observationWindow")
    if window:
        if not isinstance(window, dict):
            raise EvidenceValidationError("Image queue observationWindow is not an object")
        for key, source_key in (("fromUtc", "observedFrom"), ("toUtc", "observedTo")):
            if key in window and _epoch(window[key], "queue." + key) != _epoch(snapshot[source_key], source_key):
                raise EvidenceValidationError("Image queue belongs to a different observation window")
    by_view = {row["view_order"]: row for row in rows}
    states = {}
    items = queue.get("items", [])
    blocked_items = queue.get("blockedPreviousAttempts", [])
    if not isinstance(items, list) or not isinstance(blocked_items, list):
        raise EvidenceValidationError("Invalid image queue item list")
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get("rowViewOrders", []), list):
            raise EvidenceValidationError("Invalid image queue item")
        for view in item.get("rowViewOrders", []):
            if view not in by_view or item.get("imageUrl") != by_view[view]["image_url"]:
                raise EvidenceValidationError("Image queue card or source URL mismatch")
            if view in states:
                raise EvidenceValidationError("Duplicate image queue card")
            states[view] = dict(item)
    for item in blocked_items:
        if not isinstance(item, dict):
            raise EvidenceValidationError("Invalid blocked image queue item")
        view = item.get("viewOrder")
        if view not in by_view or item.get("imageUrl") != by_view[view]["image_url"]:
            raise EvidenceValidationError("Blocked queue card or source URL mismatch")
        merged = dict(states.get(view, {}))
        merged.update(item)
        merged["previousAttemptBlocked"] = True
        states[view] = merged
    return states, queue_text


def _load_detail(path: str | Path | None, rows: list[dict]) -> tuple[int | None, str | None]:
    if path is None:
        return None, None
    detail, _, text = _read_json(path)
    view = detail.get("viewOrder")
    match = next((row for row in rows if row["view_order"] == view), None)
    if match is None or not match["goods_id"] or _text(detail.get("goodsId")) != match["goods_id"]:
        raise EvidenceValidationError("Detail note does not match a card with its own known goodsId")
    return view, text


def import_snapshot(data_dir: str | Path, snapshot_path: str | Path,
                    image_archive: str | Path | None = None,
                    image_queue_path: str | Path | None = None,
                    detail_note_path: str | Path | None = None) -> dict:
    """Import one immutable snapshot and optional image evidence atomically.

    Reimporting identical source bytes is a no-op. A different snapshot for the
    same shop and source observation window raises SnapshotConflictError.
    Image paths inside JSON are provenance only and are never opened.
    """
    snapshot, snapshot_bytes, snapshot_text = _read_json(snapshot_path)
    sha256 = hashlib.sha256(snapshot_bytes).hexdigest()
    rows, from_epoch, to_epoch = _prepare_snapshot(snapshot)
    # Old immutable snapshots, including legacy name-only ones, may be replayed.
    # This read-only fast path never assigns them to a newly inferred identity.
    directory = Path(data_dir)
    if (directory / "monitor.sqlite3").is_file() and (directory / "images.sqlite3").is_file():
        with closing(_connect_readonly(data_dir)) as prior:
            duplicate = prior.execute("SELECT run_id,snapshot_bytes FROM runs WHERE snapshot_sha256=?", (sha256,)).fetchone()
            if duplicate:
                if bytes(duplicate['snapshot_bytes']) != snapshot_bytes:
                    raise EvidenceValidationError('Existing immutable snapshot bytes conflict with their SHA256')
                # Replays never write auxiliary evidence; supplied inputs must not
                # silently conflict with the immutable archive/queue/detail.
                stored = prior.execute('SELECT image_manifest_json,image_queue_json,detail_note_json FROM runs WHERE run_id=?',
                                       (duplicate['run_id'],)).fetchone()
                supplied = ((_load_archive(image_archive, rows)[1], stored['image_manifest_json']),
                            (_load_queue(image_queue_path, snapshot, rows)[1], stored['image_queue_json']),
                            (_load_detail(detail_note_path, rows)[1], stored['detail_note_json']))
                for incoming, existing in supplied:
                    if incoming is not None and (existing is None or json.loads(incoming) != json.loads(existing)):
                        raise EvidenceValidationError('Duplicate snapshot has conflicting auxiliary evidence; use the explicit image attachment workflow')
                return {"run_id": duplicate["run_id"], "status": "duplicate", "duplicate": True,
                        "snapshot_sha256": sha256, "summary": _get_summary(prior, duplicate["run_id"]),
                        "capture_integrity": {"status": "existing_immutable_noop", "revalidated": False,
                                              "summary": "已入库相同原始字节；只读返回，不重新认定采集完整性"}}
    # New v5 evidence must pass before either DB is initialized or opened writable.
    # Existing byte-identical history remains a read-only no-op even after relocation.
    from .capture_integrity import requires_capture_integrity, verify_capture
    capture_integrity = None
    if requires_capture_integrity(snapshot):
        capture_integrity = verify_capture(snapshot_path)
        if not capture_integrity['valid'] or capture_integrity.get('snapshot_sha256') != sha256:
            problem = '; '.join(item['message'] for item in capture_integrity['errors']) or 'Snapshot changed during integrity verification'
            raise EvidenceValidationError('采集完整性校验未通过：' + problem)
    evidence = shop_identity_evidence(snapshot)
    initialize(data_dir)
    shop_id = evidence["shop_id"]
    run_id = "run_" + sha256[:24]
    with closing(connect(data_dir)) as connection:
        duplicate = connection.execute("SELECT run_id FROM runs WHERE snapshot_sha256 = ?", (sha256,)).fetchone()
        if duplicate:
            return {"run_id": duplicate["run_id"], "status": "duplicate", "duplicate": True,
                    "snapshot_sha256": sha256, "summary": _get_summary(connection, duplicate["run_id"])}
        assets, manifest_json = _load_archive(image_archive, rows, snapshot_sha=sha256)
        states, queue_json = _load_queue(image_queue_path, snapshot, rows)
        detail_view, detail_json = _load_detail(detail_note_path, rows)
        now = _now()
        connection.execute("BEGIN IMMEDIATE")
        try:
            # Repeat the checks after taking the writer lock for concurrent importers.
            duplicate = connection.execute("SELECT run_id FROM runs WHERE snapshot_sha256 = ?", (sha256,)).fetchone()
            if duplicate:
                connection.rollback()
                return {"run_id": duplicate["run_id"], "status": "duplicate", "duplicate": True,
                        "snapshot_sha256": sha256, "summary": _get_summary(connection, duplicate["run_id"])}
            _check_existing_shop_identity(connection, evidence)
            conflict = connection.execute(
                "SELECT run_id FROM runs WHERE shop_id = ? AND observed_from_epoch = ? AND observed_to_epoch = ?",
                (shop_id, from_epoch, to_epoch)).fetchone()
            if conflict:
                raise SnapshotConflictError(f"Observation window already contains different evidence: {conflict['run_id']}")
            connection.execute("INSERT OR IGNORE INTO shops(shop_id, shop_name, source_url, created_at) VALUES (?, ?, ?, ?)",
                               (shop_id, _text(snapshot.get("shopName")) or "unnamed_shop", _text(snapshot.get("sourceUrl")), now))
            connection.execute(
                "INSERT INTO runs(run_id,shop_id,observed_from,observed_to,observed_from_epoch,observed_to_epoch,source_url,sort_order,status,end_boundary_observed,snapshot_sha256,snapshot_json,snapshot_bytes,image_manifest_json,image_queue_json,detail_note_json,imported_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (run_id, shop_id, snapshot["observedFrom"], snapshot["observedTo"], from_epoch, to_epoch,
                 _text(snapshot.get("sourceUrl")), _text(snapshot.get("sort")), _text(snapshot.get("status")) or "unknown",
                 int(snapshot.get("endBoundaryObserved") is True), sha256, snapshot_text,
                 sqlite3.Binary(snapshot_bytes), manifest_json, queue_json, detail_json, now))
            observations = {}
            for row in rows:
                raw = row["raw"]
                sales = row["sales"]
                cursor = connection.execute(
                    "INSERT INTO observations(run_id,view_order,record_key,goods_id,goods_url,identity_status,identity_evidence,title,normalized_title,price_raw,sales_raw,sales_label,sales_value,sales_unit,sales_precision,eligible_gt10,newness,observed_at,observed_at_epoch,last_dom_read_at,image_url,detail_note_json,row_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (run_id, row["view_order"], _text(raw.get("recordKey")), row["goods_id"], row["goods_url"],
                     row["identity_status"], _text(raw.get("goodsIdEvidence")), row["title"], row["normalized_title"],
                     _text(raw.get("priceRaw")), _text(raw.get("salesRaw")), sales["label"], sales["value"], sales["unit"],
                     sales["precision"], int(sales["eligible_gt10"]), "unknown", row["observed_at"], row["observed_at_epoch"],
                     _text(raw.get("lastDomReadAt")), row["image_url"],
                     detail_json if row["view_order"] == detail_view else None, _json(raw)))
                observations[row["view_order"]] = cursor.lastrowid
            grouped = defaultdict(list)
            for row in sorted(rows, key=lambda item: item["view_order"]):
                # An empty title is not evidence linking distinct cards.
                key = row["normalized_title"] or f"[untitled card {row['view_order']}]"
                grouped[key].append(row)
            group_number = 0
            for title, members in grouped.items():
                triggers = [row for row in members if row["sales"]["eligible_gt10"]]
                if not triggers:
                    continue
                group_number += 1
                evidence = {"method": "same_normalized_title_within_one_run", "normalization_pattern": SHIPPING_SUFFIX.pattern,
                            "numeric_sales_aggregation_used": False, "image_contents_compared": False,
                            "raw_titles": list(dict.fromkeys(row["title"] for row in members))}
                cursor = connection.execute(
                    "INSERT INTO candidate_groups(run_id,group_code,normalized_title,relationship,same_design_confirmed,visual_review_status,member_count,trigger_count,first_view_order,evidence_json) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (run_id, f"G{group_number:03d}", title, "suspected_similar", 0, "not_reviewed", len(members), len(triggers),
                     members[0]["view_order"], _json(evidence)))
                group_id = cursor.lastrowid
                connection.executemany("INSERT INTO group_members(group_id,observation_id,run_id,is_trigger) VALUES (?,?,?,?)",
                                       [(group_id, observations[row["view_order"]], run_id, int(row["sales"]["eligible_gt10"])) for row in members])
            assets_by_view = {asset["view_order"]: asset for asset in assets}
            for asset in assets:
                existing = connection.execute("SELECT byte_count, data FROM images.assets WHERE sha256 = ?", (asset["sha256"],)).fetchone()
                if existing and (existing["byte_count"] != len(asset["data"]) or bytes(existing["data"]) != asset["data"]):
                    raise EvidenceValidationError("Existing image asset conflicts with its SHA256")
                connection.execute("INSERT OR IGNORE INTO images.assets(sha256,mime,byte_count,data,created_at) VALUES (?,?,?,?,?)",
                                   (asset["sha256"], asset["mime"], len(asset["data"]), sqlite3.Binary(asset["data"]), now))
                connection.execute("INSERT INTO images.source_links(run_id,observation_id,source_url,asset_sha256,archive_path,manifest_json,created_at) VALUES (?,?,?,?,?,?,?)",
                                   (run_id, observations[asset["view_order"]], asset["source_url"], asset["sha256"], asset["archive_path"], asset["manifest_json"], now))
            for row in rows:
                view = row["view_order"]
                state = states.get(view, {})
                asset = assets_by_view.get(view)
                blocked = bool(state.get("previousAttemptBlocked") or state.get("status") == "blocked_previous_attempt" or
                               view in state.get("previousAttemptBlockedViewOrders", []))
                prior_reasons = state.get("previousAttemptReasons", {})
                reason = state.get("previousAttemptReason") or (prior_reasons.get(str(view)) if isinstance(prior_reasons, dict) else None)
                reported = bool(row["raw"].get("localImagePath") or state.get("reportedDownloadSuccess"))
                stale = bool(row["raw"].get("localImagePath") and not asset)
                status = "saved" if asset else "blocked_previous_attempt" if blocked else "pending_image_stage" if row["image_url"] else "missing_source_url"
                action = "none" if asset else "requires_review" if blocked else "await_authorized_image_stage" if row["image_url"] else "needs_source_url"
                connection.execute(
                    "INSERT INTO image_tasks(run_id,observation_id,source_url,status,asset_sha256,reported_download_success,stale_local_path,previous_attempt_blocked,previous_attempt_reason,automatic_retry_allowed,action_required,source_state_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (run_id, observations[view], row["image_url"], status, asset["sha256"] if asset else None,
                     int(reported), int(stale), int(blocked), _text(reason), 0, action, _json(state) if state else None))
            # Foreign keys cannot span attached databases, so assert those links in
            # this same transaction before either file becomes committed evidence.
            orphan_count = connection.execute(
                "SELECT COUNT(*) FROM images.source_links l LEFT JOIN observations o ON o.observation_id=l.observation_id AND o.run_id=l.run_id LEFT JOIN image_tasks t ON t.observation_id=l.observation_id WHERE o.observation_id IS NULL OR t.asset_sha256 IS NULL OR t.asset_sha256<>l.asset_sha256 OR t.source_url<>l.source_url").fetchone()[0]
            if orphan_count:
                raise EvidenceValidationError("Cross-database image provenance check failed")
            if connection.execute("PRAGMA main.foreign_key_check").fetchone() or connection.execute("PRAGMA images.foreign_key_check").fetchone():
                raise EvidenceValidationError("Foreign key validation failed")
            connection.execute("INSERT INTO validation_runs(run_id,checked_at,status,report_json) VALUES (?,?,?,?)",
                               (run_id, now, "passed", _json({"scope": "import_transaction", "card_count": len(rows),
                                "archive_card_images": len(assets), "snapshot_sha256_verified": True,
                                "manifest_hashes_verified": True, "cross_database_links_verified": True,
                                "capture_integrity": capture_integrity})))
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        return {"run_id": run_id, "status": "imported", "duplicate": False,
                "snapshot_sha256": sha256, "summary": _get_summary(connection, run_id),
                "capture_integrity": capture_integrity}


def attach_images(data_dir: str | Path, run_id: str, image_archive: str | Path) -> dict:
    """Attach verified image bytes after the original card snapshot was imported.

    The source snapshot and observations remain immutable. Existing observation
    links may be repeated with identical bytes, but a different image hash for
    the same observation is a conflict. Historical failure reasons and the
    prohibition on automatic retries remain recorded even after a manual import.
    """
    with closing(_connect_readonly(data_dir)) as reader:
        run = _resolve_run(reader, run_id)
        raw_bytes = bytes(run["snapshot_bytes"])
        if hashlib.sha256(raw_bytes).hexdigest() != run["snapshot_sha256"]:
            raise EvidenceValidationError("Stored source snapshot SHA256 mismatch")
        snapshot = json.loads(raw_bytes.decode("utf-8-sig"))
        rows, _, _ = _prepare_snapshot(snapshot)
    assets, _ = _load_archive(image_archive, rows, snapshot_sha=run['snapshot_sha256'])
    added_links, added_assets, existing_links = 0, 0, 0
    with closing(connect(data_dir)) as connection:
        connection.execute("BEGIN IMMEDIATE")
        try:
            _resolve_run(connection, run_id)
            for asset in assets:
                observation = connection.execute(
                    "SELECT observation_id,image_url FROM observations WHERE run_id=? AND view_order=?",
                    (run_id, asset["view_order"])).fetchone()
                if observation is None or observation["image_url"] != asset["source_url"]:
                    raise EvidenceValidationError("Stored observation contradicts source snapshot image binding")
                observation_id = observation["observation_id"]
                task = connection.execute("SELECT * FROM image_tasks WHERE observation_id=? AND run_id=?",
                                          (observation_id, run_id)).fetchone()
                if task is None or task["source_url"] != asset["source_url"]:
                    raise EvidenceValidationError("Missing or inconsistent image task")
                link = connection.execute("SELECT * FROM images.source_links WHERE observation_id=?",
                                          (observation_id,)).fetchone()
                if link and (link["run_id"] != run_id or link["source_url"] != asset["source_url"] or
                             link["asset_sha256"] != asset["sha256"]):
                    raise EvidenceValidationError("Existing image link conflicts with the supplied image SHA256 or source")
                stored_asset = connection.execute("SELECT byte_count,data FROM images.assets WHERE sha256=?",
                                                  (asset["sha256"],)).fetchone()
                if stored_asset and (stored_asset["byte_count"] != len(asset["data"]) or bytes(stored_asset["data"]) != asset["data"]):
                    raise EvidenceValidationError("Existing image asset conflicts with its SHA256")
                if link:
                    if not stored_asset or task["asset_sha256"] != asset["sha256"] or task["status"] != "saved":
                        raise EvidenceValidationError("Existing image provenance is inconsistent")
                    existing_links += 1
                    continue
                if task["asset_sha256"] is not None or task["status"] == "saved":
                    raise EvidenceValidationError("Image task claims an asset without its required source link")
                now = _now()
                if not stored_asset:
                    connection.execute("INSERT INTO images.assets(sha256,mime,byte_count,data,created_at) VALUES (?,?,?,?,?)",
                                       (asset["sha256"], asset["mime"], len(asset["data"]), sqlite3.Binary(asset["data"]), now))
                    added_assets += 1
                connection.execute("INSERT INTO images.source_links(run_id,observation_id,source_url,asset_sha256,archive_path,manifest_json,created_at) VALUES (?,?,?,?,?,?,?)",
                                   (run_id, observation_id, asset["source_url"], asset["sha256"], asset["archive_path"], asset["manifest_json"], now))
                connection.execute("UPDATE image_tasks SET status=?,asset_sha256=?,stale_local_path=0,action_required=?,automatic_retry_allowed=0 WHERE observation_id=? AND run_id=?",
                                   ("saved", asset["sha256"], "none", observation_id, run_id))
                added_links += 1
            if connection.execute("PRAGMA main.foreign_key_check").fetchone() or connection.execute("PRAGMA images.foreign_key_check").fetchone():
                raise EvidenceValidationError("Image attachment foreign key validation failed")
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        return {"run_id": run_id, "status": "images_attached" if added_links else "duplicate",
                "duplicate": added_links == 0, "image_added_card_count": added_links,
                "image_added_asset_count": added_assets, "image_existing_card_count": existing_links,
                "summary": _get_summary(connection, run_id)}


def _resolve_run(connection: sqlite3.Connection, run_id: str | None) -> sqlite3.Row:
    if run_id is None:
        row = connection.execute("SELECT * FROM runs ORDER BY observed_to_epoch DESC, observed_from_epoch DESC, imported_at DESC LIMIT 1").fetchone()
    else:
        row = connection.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
    if row is None:
        raise ValueError("No imported run was found" if run_id is None else f"Unknown run_id: {run_id}")
    return row


def _get_summary(connection: sqlite3.Connection, run_id: str | None) -> dict:
    run = _resolve_run(connection, run_id)
    run_id = run["run_id"]
    counts = connection.execute(
        "SELECT COUNT(*) card_count, COALESCE(SUM(eligible_gt10),0) eligible_gt10_count, "
        "COALESCE(SUM(sales_label='已拼' AND sales_precision='exact_display' AND sales_unit='件' AND sales_value=10),0) exactly_10_count, "
        "COALESCE(SUM(sales_precision='missing'),0) sales_missing_count, "
        "COALESCE(SUM(sales_precision='non_exact_or_unparsed'),0) sales_non_exact_count, "
        "COALESCE(SUM(goods_id IS NOT NULL),0) goods_id_card_count, "
        "COALESCE(SUM(identity_status='unique_goods_id'),0) stable_identity_card_count, "
        "COALESCE(SUM(identity_status LIKE 'conflict_%'),0) identity_conflict_card_count "
        "FROM observations WHERE run_id=?", (run_id,)).fetchone()
    images = connection.execute(
        "SELECT COALESCE(SUM(status='saved'),0) image_saved_card_count, "
        "COALESCE(SUM(previous_attempt_blocked),0) image_blocked_count, "
        "COALESCE(SUM(status IN ('pending_image_stage','missing_source_url')),0) image_pending_count, "
        "COALESCE(SUM(stale_local_path),0) image_stale_local_path_count, "
        "COALESCE(SUM(reported_download_success),0) image_reported_success_count "
        "FROM image_tasks WHERE run_id=?", (run_id,)).fetchone()
    summary = dict(counts)
    summary.update(dict(images))
    summary.update({"run_id": run_id, "shop_id": run["shop_id"],
                    "shop_name": connection.execute("SELECT shop_name FROM shops WHERE shop_id=?", (run["shop_id"],)).fetchone()[0],
                    "observed_from": run["observed_from"], "observed_to": run["observed_to"],
                    "status": run["status"], "sort": run["sort_order"], "end_boundary_observed": bool(run["end_boundary_observed"]),
                    "snapshot_sha256": run["snapshot_sha256"], "imported_at": run["imported_at"],
                    "candidate_group_count": connection.execute("SELECT COUNT(*) FROM candidate_groups WHERE run_id=?", (run_id,)).fetchone()[0],
                    "candidate_member_card_count": connection.execute("SELECT COUNT(*) FROM group_members WHERE run_id=?", (run_id,)).fetchone()[0],
                    "image_unique_asset_count": connection.execute("SELECT COUNT(DISTINCT asset_sha256) FROM images.source_links WHERE run_id=?", (run_id,)).fetchone()[0],
                    "database_unique_asset_count": connection.execute("SELECT COUNT(*) FROM images.assets").fetchone()[0],
                    "database_image_bytes": connection.execute("SELECT COALESCE(SUM(byte_count),0) FROM images.assets").fetchone()[0],
                    "run_count": connection.execute("SELECT COUNT(*) FROM runs").fetchone()[0],
                    "sales_label_counts": {row[0]: row[1] for row in connection.execute("SELECT COALESCE(sales_label,'missing_or_unrecognized'),COUNT(*) FROM observations WHERE run_id=? GROUP BY sales_label", (run_id,))},
                    "newness": "unknown", "automatic_image_retries": False,
                    "limitations": ["Card records are not unique goods, SKUs, buyers, or orders.",
                                    "Eligibility requires one card with exact 已拼 >10件; cards are never summed.",
                                    "Title groups are suspected_similar; they do not establish one design or identity.",
                                    "Listing dates and newness are unknown; image dates and list position are not listing dates.",
                                    "No image network requests or automatic retries are performed."]})
    return summary


def get_summary(data_dir: str | Path, run_id: str | None = None) -> dict:
    with closing(_connect_readonly(data_dir)) as connection:
        return _get_summary(connection, run_id)


def get_candidates(data_dir: str | Path, run_id: str | None = None) -> list[dict]:
    """Return title candidate groups and every member card, preserving price text."""
    with closing(_connect_readonly(data_dir)) as connection:
        run = _resolve_run(connection, run_id)
        groups = connection.execute("SELECT * FROM candidate_groups WHERE run_id=? ORDER BY first_view_order,group_id", (run["run_id"],)).fetchall()
        result = []
        for group in groups:
            item = dict(group)
            item["same_design_confirmed"] = False
            item["evidence"] = json.loads(item.pop("evidence_json"))
            members = connection.execute(
                "SELECT o.*,m.is_trigger,t.status AS image_status,t.asset_sha256,t.previous_attempt_blocked,t.previous_attempt_reason,t.stale_local_path,t.action_required,t.automatic_retry_allowed FROM group_members m JOIN observations o ON m.observation_id=o.observation_id JOIN image_tasks t ON t.observation_id=o.observation_id WHERE m.group_id=? ORDER BY o.view_order", (group["group_id"],)).fetchall()
            item["rows"] = []
            for member in members:
                card = dict(member)
                raw = json.loads(card.pop("row_json"))
                card["observed_at_precision"] = raw.get("observedAtPrecision") or (
                    "legacy_unspecified" if card["observed_at"] else "unknown")
                detail = card.pop("detail_note_json")
                card["detail_note"] = json.loads(detail) if detail else None
                card["eligible_gt10"] = bool(card["eligible_gt10"])
                card["is_trigger"] = bool(card["is_trigger"])
                card["automatic_retry_allowed"] = False
                item["rows"].append(card)
            item["trigger_view_orders"] = [row["view_order"] for row in item["rows"] if row["is_trigger"]]
            item["row_view_orders"] = [row["view_order"] for row in item["rows"]]
            result.append(item)
        return result


def compare_runs(data_dir: str | Path, older_run_id: str, newer_run_id: str) -> dict:
    """Compare only unique, consistent goods IDs with exact same-label displays.

    Missing identities are emitted individually. First observations above 10 are
    not newly crossed thresholds. A decrease is an anomaly, never negative growth.
    """
    with closing(_connect_readonly(data_dir)) as connection:
        older = _resolve_run(connection, older_run_id)
        newer = _resolve_run(connection, newer_run_id)
        if older["shop_id"] != newer["shop_id"]:
            raise ValueError("Cannot compare observations from different shops")
        old_rows = [dict(row) for row in connection.execute("SELECT * FROM observations WHERE run_id=? ORDER BY view_order", (older_run_id,))]
        new_rows = [dict(row) for row in connection.execute("SELECT * FROM observations WHERE run_id=? ORDER BY view_order", (newer_run_id,))]
        old_by_id, new_by_id = defaultdict(list), defaultdict(list)
        for row in old_rows:
            if row["goods_id"]:
                old_by_id[row["goods_id"]].append(row)
        for row in new_rows:
            if row["goods_id"]:
                new_by_id[row["goods_id"]].append(row)
        items = []

        def time_evidence(row: dict | None, run: sqlite3.Row) -> dict:
            if row is None:
                return {"observed_at": None, "precision": None, "basis": None, "lower": None, "upper": None}
            precision = json.loads(row["row_json"]).get("observedAtPrecision") or (
                "legacy_unspecified" if row["observed_at"] else "unknown")
            point = row["observed_at_epoch"]
            if point is not None and precision not in ("run_window", "unknown"):
                return {"observed_at": row["observed_at"], "precision": precision,
                        "basis": "recorded_read_time", "lower": point, "upper": point}
            return {"observed_at": row["observed_at"], "precision": precision,
                    "basis": "observation_window", "lower": run["observed_from_epoch"],
                    "upper": run["observed_to_epoch"]}

        def entry(old: dict | None, new: dict | None, status: str, reason: str | None) -> dict:
            anchor = new or old
            old_time, new_time = time_evidence(old, older), time_evidence(new, newer)
            return {"goods_id": anchor["goods_id"], "title": anchor["title"],
                    "old_observation_id": old["observation_id"] if old else None,
                    "new_observation_id": new["observation_id"] if new else None,
                    "old_view_order": old["view_order"] if old else None, "new_view_order": new["view_order"] if new else None,
                    "old_value": old["sales_value"] if old else None, "new_value": new["sales_value"] if new else None,
                    "old_label": old["sales_label"] if old else None, "new_label": new["sales_label"] if new else None,
                    "old_sales_raw": old["sales_raw"] if old else None, "new_sales_raw": new["sales_raw"] if new else None,
                    "old_observed_at": old_time["observed_at"], "new_observed_at": new_time["observed_at"],
                    "old_observed_at_precision": old_time["precision"], "new_observed_at_precision": new_time["precision"],
                    "old_time_basis": old_time["basis"], "new_time_basis": new_time["basis"],
                    "old_observation_window": {"from": older["observed_from"], "to": older["observed_to"]},
                    "new_observation_window": {"from": newer["observed_from"], "to": newer["observed_to"]},
                    "status": status, "reason": reason, "display_delta": None, "crossed_gt10": None,
                    "elapsed_hours": None, "elapsed_hours_min": None, "elapsed_hours_max": None,
                    "newness": "unknown"}

        for row in old_rows:
            if not row["goods_id"]:
                items.append(entry(row, None, "unknown", "missing_goods_id"))
        for row in new_rows:
            if not row["goods_id"]:
                items.append(entry(None, row, "unknown", "missing_goods_id"))
        identities = list(dict.fromkeys([*old_by_id, *new_by_id]))
        for goods_id in identities:
            old_matches, new_matches = old_by_id[goods_id], new_by_id[goods_id]
            if len(old_matches) > 1 or len(new_matches) > 1:
                # Do not arbitrarily choose a card from a duplicate goods-ID set.
                item = entry(old_matches[0] if old_matches else None, new_matches[0] if new_matches else None,
                             "anomaly", "duplicate_goods_id")
                item.update({"old_observation_ids": [row["observation_id"] for row in old_matches],
                             "new_observation_ids": [row["observation_id"] for row in new_matches],
                             "old_observation_id": None, "new_observation_id": None,
                             "old_view_order": None, "new_view_order": None, "old_value": None, "new_value": None,
                             "old_label": None, "new_label": None, "old_sales_raw": None, "new_sales_raw": None,
                             "old_observed_at": None, "new_observed_at": None,
                             "old_observed_at_precision": None, "new_observed_at_precision": None,
                             "old_time_basis": None, "new_time_basis": None})
                items.append(item)
                continue
            old = old_matches[0] if old_matches else None
            new = new_matches[0] if new_matches else None
            item = entry(old, new, "unknown", None)
            if old is None:
                item["reason"] = "no_prior_observation"
            elif new is None:
                item["reason"] = "no_later_observation"
            elif old["identity_status"] != "unique_goods_id" or new["identity_status"] != "unique_goods_id":
                item.update(status="anomaly", reason="identity_conflict")
            elif older_run_id == newer_run_id:
                item["reason"] = "same_run"
            elif older["observed_to_epoch"] > newer["observed_from_epoch"] or older["observed_from_epoch"] >= newer["observed_from_epoch"]:
                item["reason"] = "observation_time_not_ordered"
            elif old["sales_precision"] != "exact_display" or new["sales_precision"] != "exact_display":
                item["reason"] = "sales_not_exact"
            elif old["sales_label"] != new["sales_label"]:
                item["reason"] = "sales_label_changed"
            elif old["sales_unit"] != new["sales_unit"]:
                item["reason"] = "sales_unit_changed"
            elif (old["observed_at"] and old["observed_at_epoch"] is None) or (new["observed_at"] and new["observed_at_epoch"] is None):
                item["reason"] = "observation_time_not_ordered"
            else:
                old_time, new_time = time_evidence(old, older), time_evidence(new, newer)
                elapsed_min = (new_time["lower"] - old_time["upper"]) / 3600
                elapsed_max = (new_time["upper"] - old_time["lower"]) / 3600
                delta = new["sales_value"] - old["sales_value"]
                if elapsed_min <= 0:
                    item["reason"] = "observation_time_not_ordered"
                elif delta < 0:
                    item.update(status="anomaly", reason="negative_display_delta")
                else:
                    uses_read_times = old_time["basis"] == new_time["basis"] == "recorded_read_time"
                    item.update(status="comparable", reason=None, display_delta=delta,
                                elapsed_hours=elapsed_min if uses_read_times else None,
                                elapsed_hours_min=elapsed_min, elapsed_hours_max=elapsed_max,
                                crossed_gt10=(old["sales_value"] <= 10 < new["sales_value"]) if old["sales_label"] == "已拼" and old["sales_unit"] == "件" else None)
            items.append(item)
        status_counts = Counter(item["status"] for item in items)
        reason_counts = Counter(item["reason"] for item in items if item["reason"])
        summary = {"item_count": len(items), "comparable_count": status_counts["comparable"],
                   "unknown_count": status_counts["unknown"], "anomaly_count": status_counts["anomaly"],
                   "crossed_gt10_count": sum(item["crossed_gt10"] is True for item in items),
                   "reason_counts": dict(reason_counts), "old_card_count": len(old_rows), "new_card_count": len(new_rows)}
        return {"older_run_id": older_run_id, "newer_run_id": newer_run_id, "summary": summary, "items": items,
                "limitations": ["Displayed-count differences are not verified orders or unique buyers.",
                                "No goods ID matching is inferred from titles or image similarity.",
                                "Window-based observation intervals are bounds, not exact per-card elapsed times; legacy timestamps have unspecified precision.",
                                "No negative changes, non-exact displays, conflicting identities, or label changes are treated as growth.",
                                "First observed above-threshold cards do not count as newly crossed; newness remains unknown."]}
