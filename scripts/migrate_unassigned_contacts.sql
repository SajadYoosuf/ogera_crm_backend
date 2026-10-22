-- Allow contacts to be created without an owner.
-- Before: contacts.assigned_to was NOT NULL (auto-assigned on create).
-- After:  contacts.assigned_to is NULL for unassigned contacts; manager assigns later.

ALTER TABLE contacts
    MODIFY assigned_to BIGINT NULL;

-- Also allow contact_assignments.assigned_to to be NULL so the history table can
-- record an initial "created unassigned" state when no assignee was chosen.
ALTER TABLE contact_assignments
    MODIFY assigned_to BIGINT NULL;
