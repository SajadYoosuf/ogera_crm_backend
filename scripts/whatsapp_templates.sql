-- WhatsApp message templates (one row per template name + language), per tenant schema.
--
-- Run this by hand, after scripts/whatsapp_config.sql. The app does not create tables, and
-- .env points at a live database: try it on a local database first.
-- Replace "ventino" with the tenant schema name and run once per tenant. Safe to run again.

SET search_path TO ventino;

CREATE TABLE IF NOT EXISTS whatsapp_templates (
    id                BIGSERIAL PRIMARY KEY,
    name              VARCHAR(512) NOT NULL,                 -- lowercase letters, digits, underscore (Meta's rule)
    language          VARCHAR(15)  NOT NULL DEFAULT 'en_US',
    category          VARCHAR(15)  NOT NULL DEFAULT 'MARKETING',   -- MARKETING | UTILITY | AUTHENTICATION
    header_type       VARCHAR(10),                           -- text | image | video | document
    header_content    TEXT,                                  -- header text (text headers only)
    header_media_url  TEXT,                                  -- sample media link (media headers)
    header_handle     TEXT,                                  -- Meta resumable-upload handle (media headers)
    body_text         TEXT         NOT NULL,
    footer_text       VARCHAR(60),
    buttons           JSONB,                                 -- [{type, text, url?, phone_number?, example?}]
    sample_values     JSONB,                                 -- {body: [..], header: [..]} required for variables
    status            VARCHAR(20)  NOT NULL DEFAULT 'DRAFT', -- Meta's own status words, plus DRAFT
    meta_template_id  VARCHAR(50),                           -- set once Meta accepts the template
    rejection_reason  TEXT,
    quality_score     VARCHAR(10),                           -- GREEN | YELLOW | RED
    submission_error  TEXT,                                  -- last error Meta returned on submit or edit
    last_submitted_at TIMESTAMPTZ,
    created_by        BIGINT REFERENCES employees(id) ON DELETE SET NULL,
    created_at        TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at        TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    CONSTRAINT whatsapp_templates_name_language_key UNIQUE (name, language),
    CONSTRAINT whatsapp_templates_status_check CHECK (status IN (
        'DRAFT', 'PENDING', 'APPROVED', 'REJECTED', 'PAUSED', 'DISABLED', 'IN_APPEAL', 'PENDING_DELETION'
    )),
    CONSTRAINT whatsapp_templates_category_check CHECK (category IN ('MARKETING', 'UTILITY', 'AUTHENTICATION')),
    CONSTRAINT whatsapp_templates_header_type_check CHECK (header_type IS NULL OR header_type IN ('text', 'image', 'video', 'document')),
    CONSTRAINT whatsapp_templates_quality_check CHECK (quality_score IS NULL OR quality_score IN ('GREEN', 'YELLOW', 'RED'))
);

CREATE INDEX IF NOT EXISTS idx_whatsapp_templates_status ON whatsapp_templates (status);
CREATE INDEX IF NOT EXISTS idx_whatsapp_templates_meta_id ON whatsapp_templates (meta_template_id);

-- Which template a sent message used (so the inbox can show it). Both columns are optional.
ALTER TABLE whatsapp_messages ADD COLUMN IF NOT EXISTS template_id BIGINT REFERENCES whatsapp_templates(id) ON DELETE SET NULL;
ALTER TABLE whatsapp_messages ADD COLUMN IF NOT EXISTS template_params JSONB;
