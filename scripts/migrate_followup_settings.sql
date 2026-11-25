-- Follow-up settings (docs/followups-tasks-prd.md, section 8.3) and the task_status / task_priority enum options.
-- Run once per tenant schema, with the schema selected, e.g.:  SET search_path TO ventino;
-- Safe to re-run: the table uses IF NOT EXISTS, and rows are inserted only when missing.
-- Touches nothing else: `interactions` and `record_logs` are NOT changed.
--
-- ROLLOUT: run on a local PostgreSQL copy first. Run on the live database only with explicit approval.
-- Until this runs, the API uses built-in defaults (services/followup_settings.py) and
-- PATCH /settings/followups answers 503.

BEGIN;

CREATE TABLE IF NOT EXISTS followup_settings (
    id                           SERIAL PRIMARY KEY,
    default_due_time             TIME        NOT NULL DEFAULT '09:00',
    morning_time                 TIME        NOT NULL DEFAULT '10:00',
    afternoon_time               TIME        NOT NULL DEFAULT '14:00',
    evening_time                 TIME        NOT NULL DEFAULT '17:00',
    reminders_enabled            BOOLEAN     NOT NULL DEFAULT TRUE,
    reminder_mode                VARCHAR(20) NOT NULL DEFAULT 'due_day_only'
                                 CHECK (reminder_mode IN ('due_day_only', 'daily_until_done')),
    reminder_max_days            INTEGER     NOT NULL DEFAULT 7 CHECK (reminder_max_days >= 0),
    due_today_reminder_hour      SMALLINT    NOT NULL DEFAULT 9 CHECK (due_today_reminder_hour BETWEEN 0 AND 23),
    due_now_reminder_enabled     BOOLEAN     NOT NULL DEFAULT TRUE,
    due_now_window_minutes       SMALLINT    NOT NULL DEFAULT 10,
    overdue_reminder_after_hours SMALLINT    NOT NULL DEFAULT 24,
    retry_followup_days          SMALLINT    NOT NULL DEFAULT 3,
    auto_followup_hours          SMALLINT    NOT NULL DEFAULT 48,
    allow_staff_reschedule       BOOLEAN     NOT NULL DEFAULT TRUE,
    allow_staff_create_followup  BOOLEAN     NOT NULL DEFAULT TRUE,
    status_colors                JSONB,
    updated_by                   INTEGER REFERENCES employees(id) ON DELETE SET NULL,
    updated_at                   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- The single settings row, only if the table is empty.
INSERT INTO followup_settings (status_colors)
SELECT '{"todo": "gray", "inprogress": "blue", "completed": "teal"}'::jsonb
WHERE NOT EXISTS (SELECT 1 FROM followup_settings);

-- Task statuses and priorities: labels are editable in Settings, values are fixed by the code.
INSERT INTO enum_values (category, value, label, sort_order, is_active)
SELECT v.category, v.value, v.label, v.sort_order, 1
FROM (VALUES
    ('task_status',   'todo',       'To do',       1),
    ('task_status',   'inprogress', 'In progress', 2),
    ('task_status',   'completed',  'Completed',   3),
    ('task_priority', 'high',       'High',        1),
    ('task_priority', 'medium',     'Medium',      2),
    ('task_priority', 'low',        'Low',         3)
) AS v(category, value, label, sort_order)
WHERE NOT EXISTS (
    SELECT 1 FROM enum_values e WHERE e.category = v.category AND e.value = v.value
);

COMMIT;
