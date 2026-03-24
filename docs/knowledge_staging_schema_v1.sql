-- knowledge_staging.db schema v1
-- Purpose:
--   Unified staging layer for OpenClaw-authored knowledge before promotion
--   into JARVIS formal knowledge layers.
--
-- Usage:
--   sqlite3 db/common/knowledge_staging.db < docs/knowledge_staging_schema_v1.sql

PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;
PRAGMA foreign_keys = ON;
PRAGMA busy_timeout = 5000;

BEGIN;

CREATE TABLE IF NOT EXISTS schema_info (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

INSERT OR REPLACE INTO schema_info (key, value) VALUES
    ('schema_name', 'knowledge_staging'),
    ('schema_version', '1'),
    ('updated_at', strftime('%s', 'now'));


-- ============================================================
-- drawing_extractions
-- ============================================================
CREATE TABLE IF NOT EXISTS drawing_extractions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    source_id TEXT NOT NULL,
    source_type TEXT NOT NULL DEFAULT '',
    source_table TEXT NOT NULL DEFAULT '',
    source_record_id TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    owner TEXT NOT NULL DEFAULT '',
    version INTEGER NOT NULL DEFAULT 1,
    evidence_ref TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'draft',
    content_hash TEXT NOT NULL DEFAULT '',
    review_status TEXT NOT NULL DEFAULT 'unreviewed',
    reviewer TEXT NOT NULL DEFAULT '',
    reviewed_at REAL,
    review_comment TEXT NOT NULL DEFAULT '',
    is_deleted INTEGER NOT NULL DEFAULT 0,

    project_id TEXT NOT NULL DEFAULT '',
    project_name TEXT NOT NULL DEFAULT '',
    province TEXT NOT NULL DEFAULT '',
    specialty TEXT NOT NULL DEFAULT '',
    drawing_set_name TEXT NOT NULL DEFAULT '',
    section_name TEXT NOT NULL DEFAULT '',

    raw_text TEXT NOT NULL DEFAULT '',
    structured_json TEXT NOT NULL DEFAULT '{}',
    system_tags TEXT NOT NULL DEFAULT '[]',
    material_tags TEXT NOT NULL DEFAULT '[]',
    constraint_tags TEXT NOT NULL DEFAULT '[]',
    risk_tags TEXT NOT NULL DEFAULT '[]',

    summary TEXT NOT NULL DEFAULT '',
    extracted_by TEXT NOT NULL DEFAULT 'openclaw',
    confidence REAL NOT NULL DEFAULT 0.0
);

CREATE INDEX IF NOT EXISTS idx_de_source
    ON drawing_extractions(source_type, source_id, source_record_id);
CREATE INDEX IF NOT EXISTS idx_de_source_table
    ON drawing_extractions(source_table, source_record_id);
CREATE INDEX IF NOT EXISTS idx_de_project
    ON drawing_extractions(project_id, province, specialty);
CREATE INDEX IF NOT EXISTS idx_de_review
    ON drawing_extractions(review_status, status, is_deleted);
CREATE INDEX IF NOT EXISTS idx_de_content_hash
    ON drawing_extractions(content_hash);
CREATE UNIQUE INDEX IF NOT EXISTS uk_de_hash_source
    ON drawing_extractions(content_hash, source_type, source_id, source_record_id);


-- ============================================================
-- audit_errors
-- ============================================================
CREATE TABLE IF NOT EXISTS audit_errors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    source_id TEXT NOT NULL,
    source_type TEXT NOT NULL DEFAULT '',
    source_table TEXT NOT NULL DEFAULT '',
    source_record_id TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    owner TEXT NOT NULL DEFAULT '',
    version INTEGER NOT NULL DEFAULT 1,
    evidence_ref TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'draft',
    content_hash TEXT NOT NULL DEFAULT '',
    review_status TEXT NOT NULL DEFAULT 'unreviewed',
    reviewer TEXT NOT NULL DEFAULT '',
    reviewed_at REAL,
    review_comment TEXT NOT NULL DEFAULT '',
    is_deleted INTEGER NOT NULL DEFAULT 0,

    task_id TEXT NOT NULL DEFAULT '',
    result_id TEXT NOT NULL DEFAULT '',
    project_id TEXT NOT NULL DEFAULT '',
    province TEXT NOT NULL DEFAULT '',
    specialty TEXT NOT NULL DEFAULT '',

    bill_name TEXT NOT NULL DEFAULT '',
    bill_desc TEXT NOT NULL DEFAULT '',
    predicted_quota_code TEXT NOT NULL DEFAULT '',
    predicted_quota_name TEXT NOT NULL DEFAULT '',
    corrected_quota_code TEXT NOT NULL DEFAULT '',
    corrected_quota_name TEXT NOT NULL DEFAULT '',

    match_source TEXT NOT NULL DEFAULT '',   -- agent / rule / experience / search / hybrid
    error_type TEXT NOT NULL DEFAULT '',
    error_level TEXT NOT NULL DEFAULT '',    -- low / medium / high / critical
    root_cause TEXT NOT NULL DEFAULT '',
    root_cause_tags TEXT NOT NULL DEFAULT '[]',
    fix_suggestion TEXT NOT NULL DEFAULT '',
    decision_basis TEXT NOT NULL DEFAULT '',

    requires_manual_followup INTEGER NOT NULL DEFAULT 0,
    can_promote_rule INTEGER NOT NULL DEFAULT 0,
    can_promote_method INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_ae_type
    ON audit_errors(error_type, error_level, match_source);
CREATE INDEX IF NOT EXISTS idx_ae_task
    ON audit_errors(task_id, result_id);
CREATE INDEX IF NOT EXISTS idx_ae_review
    ON audit_errors(review_status, status, is_deleted);
CREATE INDEX IF NOT EXISTS idx_ae_promote
    ON audit_errors(can_promote_rule, can_promote_method);
CREATE INDEX IF NOT EXISTS idx_ae_source_table
    ON audit_errors(source_table, source_record_id);
CREATE INDEX IF NOT EXISTS idx_ae_content_hash
    ON audit_errors(content_hash);
CREATE UNIQUE INDEX IF NOT EXISTS uk_ae_hash_source
    ON audit_errors(content_hash, source_type, source_id, source_record_id);


-- ============================================================
-- pricing_case_summaries
-- ============================================================
CREATE TABLE IF NOT EXISTS pricing_case_summaries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    source_id TEXT NOT NULL,
    source_type TEXT NOT NULL DEFAULT '',
    source_table TEXT NOT NULL DEFAULT '',
    source_record_id TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    owner TEXT NOT NULL DEFAULT '',
    version INTEGER NOT NULL DEFAULT 1,
    evidence_ref TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'draft',
    content_hash TEXT NOT NULL DEFAULT '',
    review_status TEXT NOT NULL DEFAULT 'unreviewed',
    reviewer TEXT NOT NULL DEFAULT '',
    reviewed_at REAL,
    review_comment TEXT NOT NULL DEFAULT '',
    is_deleted INTEGER NOT NULL DEFAULT 0,

    case_id TEXT NOT NULL DEFAULT '',
    project_id TEXT NOT NULL DEFAULT '',
    project_name TEXT NOT NULL DEFAULT '',
    province TEXT NOT NULL DEFAULT '',
    specialty TEXT NOT NULL DEFAULT '',

    bill_name TEXT NOT NULL DEFAULT '',
    bill_desc TEXT NOT NULL DEFAULT '',
    final_quota_code TEXT NOT NULL DEFAULT '',
    final_quota_name TEXT NOT NULL DEFAULT '',
    quantity REAL,
    unit TEXT NOT NULL DEFAULT '',

    summary TEXT NOT NULL DEFAULT '',
    scenario_tags TEXT NOT NULL DEFAULT '[]',
    feature_tags TEXT NOT NULL DEFAULT '[]',
    reusable_conditions TEXT NOT NULL DEFAULT '[]',
    non_reusable_reasons TEXT NOT NULL DEFAULT '[]',

    confidence REAL NOT NULL DEFAULT 0.0,
    reusable_score REAL NOT NULL DEFAULT 0.0,
    evidence_level TEXT NOT NULL DEFAULT '', -- weak / medium / strong
    suggested_target TEXT NOT NULL DEFAULT '' -- experience / method / keep_staging
);

CREATE INDEX IF NOT EXISTS idx_pcs_case
    ON pricing_case_summaries(case_id, project_id, province);
CREATE INDEX IF NOT EXISTS idx_pcs_target
    ON pricing_case_summaries(suggested_target, review_status, is_deleted);
CREATE INDEX IF NOT EXISTS idx_pcs_quota
    ON pricing_case_summaries(final_quota_code);
CREATE INDEX IF NOT EXISTS idx_pcs_source_table
    ON pricing_case_summaries(source_table, source_record_id);
CREATE INDEX IF NOT EXISTS idx_pcs_content_hash
    ON pricing_case_summaries(content_hash);
CREATE UNIQUE INDEX IF NOT EXISTS uk_pcs_hash_source
    ON pricing_case_summaries(content_hash, source_type, source_id, source_record_id);


-- ============================================================
-- quick_notes_structured
-- ============================================================
CREATE TABLE IF NOT EXISTS quick_notes_structured (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    source_id TEXT NOT NULL,
    source_type TEXT NOT NULL DEFAULT '',
    source_table TEXT NOT NULL DEFAULT '',
    source_record_id TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    owner TEXT NOT NULL DEFAULT '',
    version INTEGER NOT NULL DEFAULT 1,
    evidence_ref TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'draft',
    content_hash TEXT NOT NULL DEFAULT '',
    review_status TEXT NOT NULL DEFAULT 'unreviewed',
    reviewer TEXT NOT NULL DEFAULT '',
    reviewed_at REAL,
    review_comment TEXT NOT NULL DEFAULT '',
    is_deleted INTEGER NOT NULL DEFAULT 0,

    note_title TEXT NOT NULL DEFAULT '',
    raw_note TEXT NOT NULL DEFAULT '',
    structured_json TEXT NOT NULL DEFAULT '{}',

    note_kind TEXT NOT NULL DEFAULT '',     -- rule_hint / method_hint / universal_hint / case_hint / misc
    province TEXT NOT NULL DEFAULT '',
    specialty TEXT NOT NULL DEFAULT '',
    tags TEXT NOT NULL DEFAULT '[]',

    suggested_target TEXT NOT NULL DEFAULT '', -- rule / method / universal / experience / keep_staging
    confidence REAL NOT NULL DEFAULT 0.0
);

CREATE INDEX IF NOT EXISTS idx_qns_kind
    ON quick_notes_structured(note_kind, suggested_target);
CREATE INDEX IF NOT EXISTS idx_qns_review
    ON quick_notes_structured(review_status, status, is_deleted);
CREATE INDEX IF NOT EXISTS idx_qns_source_table
    ON quick_notes_structured(source_table, source_record_id);
CREATE INDEX IF NOT EXISTS idx_qns_content_hash
    ON quick_notes_structured(content_hash);
CREATE UNIQUE INDEX IF NOT EXISTS uk_qns_hash_source
    ON quick_notes_structured(content_hash, source_type, source_id, source_record_id);


-- ============================================================
-- promotion_queue
-- ============================================================
CREATE TABLE IF NOT EXISTS promotion_queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    source_id TEXT NOT NULL,
    source_type TEXT NOT NULL DEFAULT '',
    source_table TEXT NOT NULL DEFAULT '',
    source_record_id TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    owner TEXT NOT NULL DEFAULT '',
    version INTEGER NOT NULL DEFAULT 1,
    evidence_ref TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'draft',   -- draft / reviewing / approved / rejected / promoted
    content_hash TEXT NOT NULL DEFAULT '',
    review_status TEXT NOT NULL DEFAULT 'unreviewed',
    reviewer TEXT NOT NULL DEFAULT '',
    reviewed_at REAL,
    review_comment TEXT NOT NULL DEFAULT '',
    is_deleted INTEGER NOT NULL DEFAULT 0,

    candidate_type TEXT NOT NULL DEFAULT '',    -- rule / method / universal / experience
    target_layer TEXT NOT NULL DEFAULT '',      -- RuleKnowledge / MethodCards / UniversalKB / ExperienceDB
    candidate_title TEXT NOT NULL DEFAULT '',
    candidate_summary TEXT NOT NULL DEFAULT '',
    candidate_payload TEXT NOT NULL DEFAULT '{}',

    priority INTEGER NOT NULL DEFAULT 50,
    approval_required INTEGER NOT NULL DEFAULT 1,

    promoted_at REAL,
    promoted_target_id TEXT NOT NULL DEFAULT '',
    promoted_target_ref TEXT NOT NULL DEFAULT '',
    target_version INTEGER,
    promotion_trace TEXT NOT NULL DEFAULT '',

    rejection_reason TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_pq_status
    ON promotion_queue(status, review_status, priority, is_deleted);
CREATE INDEX IF NOT EXISTS idx_pq_target
    ON promotion_queue(target_layer, candidate_type);
CREATE INDEX IF NOT EXISTS idx_pq_source
    ON promotion_queue(source_table, source_record_id);
CREATE INDEX IF NOT EXISTS idx_pq_content_hash
    ON promotion_queue(content_hash);
CREATE UNIQUE INDEX IF NOT EXISTS uk_pq_source_target
    ON promotion_queue(source_table, source_record_id, target_layer);


-- ============================================================
-- Views for admin review
-- ============================================================
CREATE VIEW IF NOT EXISTS v_pending_promotions AS
SELECT *
FROM promotion_queue
WHERE is_deleted = 0
  AND status IN ('draft', 'reviewing');

CREATE VIEW IF NOT EXISTS v_active_audit_errors AS
SELECT *
FROM audit_errors
WHERE is_deleted = 0
  AND status = 'active';

COMMIT;
