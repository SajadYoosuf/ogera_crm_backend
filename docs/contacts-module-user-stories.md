# Contacts Module — User Stories by Role

## Admin

| ID | User Story | Acceptance Criteria |
|----|-----------|---------------------|
| A-01 | As an admin, I can create a Consumer, Partner, or Dealer contact so the right pipeline is applied from the start. | Contact created with correct `contact_type`; duplicate phone returns 409. |
| A-02 | As an admin, I can view all contacts across the team regardless of assignment. | `GET /contacts` returns every non-deleted contact in the tenant. |
| A-03 | As an admin, I can assign or reassign any contact to any employee. | `PUT /contacts/{id}` with `assigned_to` updates the assignment; visible in timeline. |
| A-04 | As an admin, I can move a contact to any pipeline stage including terminal stages. | `POST /contacts/{id}/stage` succeeds; terminal slug without `loss_reason` returns 422. |
| A-05 | As an admin, I can mark a contact as lost or inactive with a reason so the team knows why. | Stage move to terminal slug + `loss_reason` saved; shown in contact record. |
| A-06 | As an admin, I can log call/visit/whatsapp/email activities on any contact. | `POST /contacts/{id}/interactions/log` returns 201; entry visible in timeline. |
| A-07 | As an admin, I can schedule a follow-up task on any contact and assign it to a staff member. | `POST /contacts/{id}/interactions/task` creates task with `due_at` and `assigned_to`. |
| A-08 | As an admin, I can reassign a task to a different employee if the original assignee is unavailable. | `PATCH /contacts/{id}/interactions/{iid}/reassign` updates `assigned_to`. |
| A-09 | As an admin, I can delete a wrong or duplicate interaction log. | `DELETE /contacts/{id}/interactions/{iid}` returns 204. |
| A-10 | As an admin, I can view the full timeline of a contact to audit all actions taken. | `GET /contacts/{id}/timeline` returns entries newest-first including stage changes, logs, tasks. |
| A-11 | As an admin, I can soft-delete a contact to remove it from active lists without losing history. | `DELETE /contacts/{id}` returns 204; subsequent GET returns 404. |
| A-12 | As an admin, I can see the pipeline board for Consumer, Partner, and Dealer tabs to get a full picture. | Pipeline page loads all three tabs; contacts appear in correct stage columns. |

---

## Manager

| ID | User Story | Acceptance Criteria |
|----|-----------|---------------------|
| M-01 | As a manager, I can view all contacts in my tenant so I can oversee the team's pipeline. | `GET /contacts` returns all contacts, not just assigned ones. |
| M-02 | As a manager, I can move any contact along the pipeline to unblock stalled deals. | `POST /contacts/{id}/stage` succeeds for any contact regardless of assignment. |
| M-03 | As a manager, I can mark contacts as lost/inactive with a reason for reporting purposes. | Terminal stage move with `loss_reason` saves correctly. |
| M-04 | As a manager, I can reassign tasks to balance workload across the team. | `PATCH /contacts/{id}/interactions/{iid}/reassign` returns 200 for manager token. |
| M-05 | As a manager, I can delete incorrect interaction logs to keep contact history clean. | `DELETE /contacts/{id}/interactions/{iid}` returns 204 for manager token. |
| M-06 | As a manager, I can view the full timeline of any contact to understand what happened. | `GET /contacts/{id}/timeline` returns all history regardless of who owns the contact. |
| M-07 | As a manager, I can see the pipeline board across Consumer / Partner / Dealer tabs to monitor team progress. | All three tabs load with correct stage columns and contact counts. |

---

## Sales Executive (Staff)

| ID | User Story | Acceptance Criteria |
|----|-----------|---------------------|
| S-01 | As a sales executive, I can see contacts assigned to me so I know what to work on today. | `GET /contacts` returns contacts where `assigned_to` = me. |
| S-02 | As a sales executive, I can see contacts I created even if they were reassigned. | Contacts where `created_by` = me are visible in my list. |
| S-03 | As a sales executive, I can see a contact if I have an open task on it, so I don't lose context. | Contacts with a pending task `assigned_to` me are accessible via GET. |
| S-04 | As a sales executive, I can create a new contact and it auto-starts my pipeline work. | `POST /contacts` returns 201 with my employee ID as `created_by`. |
| S-05 | As a sales executive, I can log a call, visit, WhatsApp, or email against my contact. | `POST /contacts/{id}/interactions/log` with valid `activity_type` returns 201 for my contact. |
| S-06 | As a sales executive, I cannot log on a contact that isn't mine, to prevent data pollution. | `POST /contacts/{unrelated_id}/interactions/log` returns 403. |
| S-07 | As a sales executive, I can schedule a follow-up task on my contact so I don't forget. | `POST /contacts/{id}/interactions/task` creates a task with `due_at`. |
| S-08 | As a sales executive, I can mark my task as completed with an outcome note. | `PATCH /contact-interactions/{iid}` with `status: completed` and `outcome` returns 200. |
| S-09 | As a sales executive, I can reschedule a pending task when the client asks to push the date. | `PATCH /contacts/{id}/interactions/{iid}/reschedule` with new `due_at` returns 200. |
| S-10 | As a sales executive, I can move my contact to the next pipeline stage as the deal progresses. | `POST /contacts/{id}/stage` with a valid stage ID for the contact's type returns 200. |
| S-11 | As a sales executive, I can view the interaction history of my contact to prepare for a call. | `GET /contacts/{id}/interactions` and `GET /contacts/{id}/timeline` return history for my contact. |
| S-12 | As a sales executive, I can see the pipeline board for my contact type so I know where each deal stands. | Pipeline page loads; my contacts appear in their current stage column. |
