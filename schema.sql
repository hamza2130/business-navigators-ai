-- Multi-tenant PostgreSQL schema with Row-Level Security enforcing tenant
-- isolation at the database layer, not just in application code (SRS NFR:
-- "Tenant isolation enforced in the database (Row-Level Security), not
-- just app code").
--
-- Single live tenant today (Business Navigators, id='default'), but every
-- table carries tenant_id from day one so a second tenant can be added
-- with zero schema changes (SRS: "database designed as multi-tenant SaaS
-- from day one").
--
-- Run as a superuser (e.g. `psql -f schema.sql`), then have the
-- application connect as app_user, NOT as the migration user - RLS does
-- not apply to a table's owner or to a superuser unless the role also
-- lacks BYPASSRLS, which is why app_user below is created explicitly
-- without it.

CREATE TABLE IF NOT EXISTS tenants (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

INSERT INTO tenants (id, name) VALUES ('default', 'Business Navigators')
ON CONFLICT (id) DO NOTHING;

-- ==========================================================================
CREATE TABLE IF NOT EXISTS leads (
    tenant_id TEXT NOT NULL REFERENCES tenants(id) DEFAULT current_setting('app.tenant_id', true),
    phone_number TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'NEW',
    score INTEGER NOT NULL DEFAULT 0,
    name TEXT,
    business_type TEXT,
    document_id_number TEXT,
    document_expiry_date TEXT,
    document_status TEXT NOT NULL DEFAULT 'PENDING',
    human_takeover BOOLEAN NOT NULL DEFAULT FALSE,
    reminder_30d_sent_at TIMESTAMPTZ,
    reminder_7d_sent_at TIMESTAMPTZ,
    turnover TEXT,
    industry TEXT,
    vat_status TEXT,
    service_interest TEXT,
    lead_tier TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, phone_number)
);
ALTER TABLE leads ENABLE ROW LEVEL SECURITY;
ALTER TABLE leads FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON leads;
CREATE POLICY tenant_isolation ON leads
    USING (tenant_id = current_setting('app.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('app.tenant_id', true));

-- ==========================================================================
CREATE TABLE IF NOT EXISTS knowledge_base (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenants(id) DEFAULT current_setting('app.tenant_id', true),
    category TEXT NOT NULL,
    topic TEXT NOT NULL,
    content TEXT NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
ALTER TABLE knowledge_base ENABLE ROW LEVEL SECURITY;
ALTER TABLE knowledge_base FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON knowledge_base;
CREATE POLICY tenant_isolation ON knowledge_base
    USING (tenant_id = current_setting('app.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('app.tenant_id', true));

-- ==========================================================================
CREATE TABLE IF NOT EXISTS messages (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenants(id) DEFAULT current_setting('app.tenant_id', true),
    identifier TEXT NOT NULL,
    channel TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    external_message_id TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_messages_identifier ON messages(tenant_id, identifier, created_at);
-- Idempotency guard is scoped per-tenant: two different tenants could
-- (in principle) each receive a message that happens to carry the same
-- external id from two different Meta apps.
CREATE UNIQUE INDEX IF NOT EXISTS idx_messages_external_id
    ON messages(tenant_id, external_message_id) WHERE external_message_id IS NOT NULL;
ALTER TABLE messages ENABLE ROW LEVEL SECURITY;
ALTER TABLE messages FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON messages;
CREATE POLICY tenant_isolation ON messages
    USING (tenant_id = current_setting('app.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('app.tenant_id', true));

-- ==========================================================================
CREATE TABLE IF NOT EXISTS scoring_rules (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenants(id) DEFAULT current_setting('app.tenant_id', true),
    keyword TEXT NOT NULL,
    weight INTEGER NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, keyword)
);
ALTER TABLE scoring_rules ENABLE ROW LEVEL SECURITY;
ALTER TABLE scoring_rules FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON scoring_rules;
CREATE POLICY tenant_isolation ON scoring_rules
    USING (tenant_id = current_setting('app.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('app.tenant_id', true));

-- ==========================================================================
CREATE TABLE IF NOT EXISTS booking_keywords (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenants(id) DEFAULT current_setting('app.tenant_id', true),
    keyword TEXT NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    UNIQUE (tenant_id, keyword)
);
ALTER TABLE booking_keywords ENABLE ROW LEVEL SECURITY;
ALTER TABLE booking_keywords FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON booking_keywords;
CREATE POLICY tenant_isolation ON booking_keywords
    USING (tenant_id = current_setting('app.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('app.tenant_id', true));

-- ==========================================================================
CREATE TABLE IF NOT EXISTS app_settings (
    tenant_id TEXT NOT NULL REFERENCES tenants(id) DEFAULT current_setting('app.tenant_id', true),
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    PRIMARY KEY (tenant_id, key)
);
ALTER TABLE app_settings ENABLE ROW LEVEL SECURITY;
ALTER TABLE app_settings FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON app_settings;
CREATE POLICY tenant_isolation ON app_settings
    USING (tenant_id = current_setting('app.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('app.tenant_id', true));

-- ==========================================================================
-- Documents (FR-8: private S3 storage, signed-URL access, access logged;
-- FR-14: review queue with an audit trail). One row per uploaded file -
-- a lead can have more than one document over time (passport now, a
-- renewed trade licence later), unlike the single-slot columns on leads.
CREATE TABLE IF NOT EXISTS documents (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenants(id) DEFAULT current_setting('app.tenant_id', true),
    lead_phone_number TEXT NOT NULL,
    storage_key TEXT NOT NULL,
    original_filename TEXT,
    content_type TEXT,
    size_bytes INTEGER,
    extracted_expiry_date TEXT,
    -- PENDING (uploaded, not yet reviewed) -> APPROVED / REJECTED by staff,
    -- or FAILED (OCR couldn't read it - see ocr_service.py).
    status TEXT NOT NULL DEFAULT 'PENDING'
        CHECK (status IN ('PENDING', 'APPROVED', 'REJECTED', 'FAILED')),
    uploaded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    reviewed_by TEXT,
    reviewed_at TIMESTAMPTZ,
    review_note TEXT
);
CREATE INDEX IF NOT EXISTS idx_documents_lead ON documents(tenant_id, lead_phone_number);
ALTER TABLE documents ENABLE ROW LEVEL SECURITY;
ALTER TABLE documents FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON documents;
CREATE POLICY tenant_isolation ON documents
    USING (tenant_id = current_setting('app.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('app.tenant_id', true));

-- Every signed-URL issuance is logged here (SRS NFR: "Audit logging of
-- staff actions and document access").
CREATE TABLE IF NOT EXISTS document_access_log (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenants(id) DEFAULT current_setting('app.tenant_id', true),
    document_id BIGINT NOT NULL,
    accessed_by TEXT NOT NULL,
    accessed_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
ALTER TABLE document_access_log ENABLE ROW LEVEL SECURITY;
ALTER TABLE document_access_log FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON document_access_log;
CREATE POLICY tenant_isolation ON document_access_log
    USING (tenant_id = current_setting('app.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('app.tenant_id', true));

-- ==========================================================================
-- Application role: connects WITHOUT BYPASSRLS so the policies above are
-- actually enforced (a superuser or table owner bypasses RLS regardless
-- of ENABLE/FORCE). Password is a migration-time placeholder - override
-- via ALTER ROLE in a real deployment and never commit the real one.
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'app_user') THEN
        CREATE ROLE app_user LOGIN PASSWORD 'change_me_in_production' NOBYPASSRLS;
    END IF;
END
$$;

-- GRANT ON DATABASE needs a literal identifier, not an expression, so this
-- targets whichever database this script is actually being run against
-- (production DB, a local test DB, etc.) via dynamic SQL rather than a
-- hardcoded name that would silently grant on the wrong database when
-- this file is applied anywhere else.
DO $$
BEGIN
    EXECUTE format('GRANT CONNECT ON DATABASE %I TO app_user', current_database());
END
$$;
GRANT USAGE ON SCHEMA public TO app_user;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO app_user;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO app_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO app_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO app_user;

-- Seed default scoring rules / booking keywords for the 'default' tenant,
-- matching what used to be hardcoded in main.py. Only runs if empty, same
-- upgrade-safety as the old SQLite migration.
INSERT INTO scoring_rules (tenant_id, keyword, weight)
SELECT 'default', kw, 15 FROM unnest(ARRAY[
    'setup','compliance','growth','tax','license','consultation',
    'booking','visa','cost','appointment','schedule','meet',
    'expire','expiry','expiration','document'
]) AS kw
WHERE NOT EXISTS (SELECT 1 FROM scoring_rules WHERE tenant_id = 'default');

INSERT INTO booking_keywords (tenant_id, keyword)
SELECT 'default', kw FROM unnest(ARRAY[
    'book','booking','appointment','schedule','meet','meeting','call'
]) AS kw
WHERE NOT EXISTS (SELECT 1 FROM booking_keywords WHERE tenant_id = 'default');

INSERT INTO app_settings (tenant_id, key, value) VALUES
    ('default', 'hot_threshold', '70'),
    ('default', 'medium_threshold', '30'),
    ('default', 'base_engagement_boost', '5')
ON CONFLICT (tenant_id, key) DO NOTHING;
