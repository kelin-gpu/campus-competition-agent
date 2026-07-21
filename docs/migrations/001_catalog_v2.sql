-- Catalog v2 bootstrap migration for PostgreSQL.
-- Review and back up the target database before execution.

BEGIN;

CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS competition_catalog (
    catalog_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    normalized_title text NOT NULL UNIQUE,
    original_title text,
    organizer text,
    category text,
    contest_level text,
    authority_level text NOT NULL DEFAULT '中',
    policy_tags text,
    scope_type text NOT NULL DEFAULT '校外竞赛',
    source_name text,
    source_url text,
    is_ministry_approved boolean NOT NULL DEFAULT false,
    status text NOT NULL DEFAULT 'active',
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS event_edition (
    edition_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    catalog_id uuid NOT NULL REFERENCES competition_catalog(catalog_id) ON DELETE CASCADE,
    event_id text NOT NULL UNIQUE,
    title text NOT NULL,
    edition_year integer,
    signup_deadline timestamptz,
    event_time timestamptz,
    status text NOT NULL DEFAULT '待确认',
    source_name text,
    source_url text,
    summary text,
    target_major text NOT NULL DEFAULT '全校各专业',
    target_grade text,
    tags text,
    policy_tags text,
    extraction_method text,
    confidence text,
    verification_status text DEFAULT 'pending_review',
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS field_evidence (
    evidence_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    edition_id uuid NOT NULL REFERENCES event_edition(edition_id) ON DELETE CASCADE,
    field_name text NOT NULL,
    field_value text,
    source_url text,
    fetched_at timestamptz NOT NULL DEFAULT now(),
    extraction_method text,
    confidence text,
    verification_status text DEFAULT 'pending_review',
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_event_edition_catalog_year
    ON event_edition(catalog_id, edition_year);
CREATE INDEX IF NOT EXISTS ix_event_edition_status_deadline
    ON event_edition(status, signup_deadline);
CREATE UNIQUE INDEX IF NOT EXISTS ux_field_evidence_value_source
    ON field_evidence(edition_id, field_name, field_value, source_url);

-- Keep an existing legacy table instead of dropping it. The compatibility
-- view is created only when the old event_info relation is absent.
DO $$
BEGIN
    IF to_regclass('public.event_info') IS NULL THEN
        EXECUTE $view$
            CREATE VIEW event_info AS
            SELECT
                e.event_id,
                e.title,
                c.scope_type,
                c.category,
                e.summary,
                e.signup_deadline,
                e.event_time,
                e.target_major,
                e.target_grade,
                c.contest_level,
                e.tags,
                e.policy_tags,
                e.source_name,
                e.source_url,
                c.authority_level,
                e.status,
                c.organizer,
                e.updated_at AS update_time,
                NULL::text AS original_text,
                c.is_ministry_approved
            FROM event_edition e
            JOIN competition_catalog c ON c.catalog_id = e.catalog_id
        $view$;
    END IF;
END $$;

COMMIT;
