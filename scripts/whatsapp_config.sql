-- WhatsApp Business connection settings (one row per tenant).
--
-- Run this by hand. The app does not create tables, and .env points at a live
-- database: try it on a local database first, then have it applied to production.
--
-- PART 1 runs once for every tenant schema (replace "ventino" with the schema name).
-- PART 2 runs once, in the public schema.
-- Both parts are safe to run again.

-- ── PART 1: per tenant ─────────────────────────────────────────────────────────
SET search_path TO ventino;

CREATE TABLE IF NOT EXISTS whatsapp_config (
    id                    BIGSERIAL PRIMARY KEY,
    phone_number_id       VARCHAR(50)  NOT NULL,
    waba_id               VARCHAR(50)  NOT NULL,
    app_id                VARCHAR(50),
    business_id           VARCHAR(50),
    api_version           VARCHAR(10)  NOT NULL DEFAULT 'v21.0',
    access_token_enc      TEXT         NOT NULL,   -- AES-256-GCM, never sent to the browser
    app_secret_enc        TEXT,                    -- AES-256-GCM, checks webhook signatures
    verify_token          VARCHAR(100) NOT NULL,   -- echoed back to Meta when the webhook is set up
    display_phone_number  VARCHAR(30),             -- filled in by "Test connection"
    verified_name         VARCHAR(100),
    quality_rating        VARCHAR(10),
    status                VARCHAR(15)  NOT NULL DEFAULT 'disconnected',  -- connected | disconnected | error
    connected_at          TIMESTAMPTZ,
    last_checked_at       TIMESTAMPTZ,
    last_error            TEXT,
    updated_by            BIGINT REFERENCES employees(id) ON DELETE SET NULL,
    created_at            TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at            TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

-- At most one connection per tenant.
CREATE UNIQUE INDEX IF NOT EXISTS one_whatsapp_config ON whatsapp_config ((TRUE));

-- ── PART 2: public ─────────────────────────────────────────────────────────────
-- An inbound webhook carries no login, only Meta's WABA id. This table says which
-- tenant schema that WABA belongs to.
CREATE TABLE IF NOT EXISTS public.whatsapp_tenant_map (
    waba_id          VARCHAR(50) PRIMARY KEY,
    phone_number_id  VARCHAR(50) UNIQUE,
    schema_name      VARCHAR(63) NOT NULL REFERENCES public.tenants(schema_name) ON DELETE CASCADE
);
