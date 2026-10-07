-- Two durable SQLite files; images is attached before this script runs.
-- Cross-database references are verified by the importer and validator because
-- SQLite cannot express foreign keys across attached databases.
CREATE TABLE IF NOT EXISTS shops (
    shop_id TEXT PRIMARY KEY,
    shop_name TEXT NOT NULL,
    source_url TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    shop_id TEXT NOT NULL REFERENCES shops(shop_id),
    observed_from TEXT NOT NULL,
    observed_to TEXT NOT NULL,
    observed_from_epoch REAL NOT NULL,
    observed_to_epoch REAL NOT NULL,
    source_url TEXT,
    sort_order TEXT,
    status TEXT NOT NULL,
    end_boundary_observed INTEGER NOT NULL CHECK(end_boundary_observed IN (0,1)),
    snapshot_sha256 TEXT NOT NULL UNIQUE,
    snapshot_json TEXT NOT NULL,
    snapshot_bytes BLOB NOT NULL,
    image_manifest_json TEXT,
    image_queue_json TEXT,
    detail_note_json TEXT,
    imported_at TEXT NOT NULL,
    UNIQUE(shop_id, observed_from_epoch, observed_to_epoch),
    CHECK(observed_to_epoch >= observed_from_epoch)
);
CREATE TABLE IF NOT EXISTS observations (
    observation_id INTEGER PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    view_order INTEGER NOT NULL,
    record_key TEXT,
    goods_id TEXT,
    goods_url TEXT,
    identity_status TEXT NOT NULL,
    identity_evidence TEXT,
    title TEXT NOT NULL,
    normalized_title TEXT NOT NULL,
    price_raw TEXT,
    sales_raw TEXT,
    sales_label TEXT,
    sales_value INTEGER,
    sales_unit TEXT,
    sales_precision TEXT NOT NULL,
    eligible_gt10 INTEGER NOT NULL CHECK(eligible_gt10 IN (0,1)),
    newness TEXT NOT NULL DEFAULT 'unknown' CHECK(newness = 'unknown'),
    observed_at TEXT,
    observed_at_epoch REAL,
    last_dom_read_at TEXT,
    image_url TEXT,
    detail_note_json TEXT,
    row_json TEXT NOT NULL,
    UNIQUE(run_id, view_order),
    UNIQUE(run_id, observation_id),
    CHECK(eligible_gt10 = 0 OR (sales_label = '已拼' AND sales_precision = 'exact_display' AND sales_unit = '件' AND sales_value > 10))
);
CREATE INDEX IF NOT EXISTS observations_goods_idx ON observations(goods_id, run_id);
CREATE INDEX IF NOT EXISTS observations_eligible_idx ON observations(run_id, eligible_gt10);
CREATE TABLE IF NOT EXISTS candidate_groups (
    group_id INTEGER PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    group_code TEXT NOT NULL,
    normalized_title TEXT NOT NULL,
    relationship TEXT NOT NULL DEFAULT 'suspected_similar' CHECK(relationship = 'suspected_similar'),
    same_design_confirmed INTEGER NOT NULL DEFAULT 0 CHECK(same_design_confirmed = 0),
    visual_review_status TEXT NOT NULL DEFAULT 'not_reviewed',
    member_count INTEGER NOT NULL,
    trigger_count INTEGER NOT NULL,
    first_view_order INTEGER NOT NULL,
    evidence_json TEXT NOT NULL,
    UNIQUE(run_id, group_code),
    UNIQUE(run_id, normalized_title),
    UNIQUE(run_id, group_id)
);
CREATE TABLE IF NOT EXISTS group_members (
    group_id INTEGER NOT NULL,
    observation_id INTEGER NOT NULL,
    run_id TEXT NOT NULL,
    is_trigger INTEGER NOT NULL CHECK(is_trigger IN (0,1)),
    PRIMARY KEY(group_id, observation_id),
    FOREIGN KEY(run_id, group_id) REFERENCES candidate_groups(run_id, group_id),
    FOREIGN KEY(run_id, observation_id) REFERENCES observations(run_id, observation_id)
);
CREATE TABLE IF NOT EXISTS image_tasks (
    task_id INTEGER PRIMARY KEY,
    run_id TEXT NOT NULL,
    observation_id INTEGER NOT NULL UNIQUE,
    source_url TEXT,
    status TEXT NOT NULL,
    asset_sha256 TEXT,
    reported_download_success INTEGER NOT NULL DEFAULT 0 CHECK(reported_download_success IN (0,1)),
    stale_local_path INTEGER NOT NULL DEFAULT 0 CHECK(stale_local_path IN (0,1)),
    previous_attempt_blocked INTEGER NOT NULL DEFAULT 0 CHECK(previous_attempt_blocked IN (0,1)),
    previous_attempt_reason TEXT,
    automatic_retry_allowed INTEGER NOT NULL DEFAULT 0 CHECK(automatic_retry_allowed = 0),
    action_required TEXT NOT NULL,
    source_state_json TEXT,
    FOREIGN KEY(run_id, observation_id) REFERENCES observations(run_id, observation_id)
);
CREATE INDEX IF NOT EXISTS image_tasks_run_idx ON image_tasks(run_id, status);
CREATE TABLE IF NOT EXISTS validation_runs (
    validation_id INTEGER PRIMARY KEY,
    run_id TEXT REFERENCES runs(run_id),
    checked_at TEXT NOT NULL,
    status TEXT NOT NULL,
    report_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS images.assets (
    sha256 TEXT PRIMARY KEY,
    mime TEXT NOT NULL,
    byte_count INTEGER NOT NULL CHECK(byte_count > 0),
    data BLOB NOT NULL,
    created_at TEXT NOT NULL,
    CHECK(length(data) = byte_count)
);
CREATE TABLE IF NOT EXISTS images.source_links (
    link_id INTEGER PRIMARY KEY,
    run_id TEXT NOT NULL,
    observation_id INTEGER NOT NULL UNIQUE,
    source_url TEXT NOT NULL,
    asset_sha256 TEXT NOT NULL REFERENCES assets(sha256),
    archive_path TEXT NOT NULL,
    manifest_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS images.source_links_run_idx ON source_links(run_id);
