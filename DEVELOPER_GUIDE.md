# Silicon CRM — Developer Understanding Guide

The back office of Kadlag Investment: a Django 5.2 + Postgres web app
(`bo.kadlaginvestment.com`) plus a native Android app that talks to the same
server over the same session cookie.

This document is the "why does this exist / how is it built / what is it for"
map of every module. `CLAUDE.md` holds the rules you must not break;
this file holds the understanding behind them. Read this first, then that.

---

## 1. What the system is for

A financial-advisory firm sells insurance and mutual funds through a small
team of advisors. Everything the firm needs to run day to day lives here:

- who the clients are, and who owns each one,
- what was sold, whether it was approved, and what the advisor earns for it,
- what renews, what expires, what is claimed,
- who is being chased (leads), and who must be called back today (follow-ups),
- what each employee is expected to do (targets, tasks) and how they actually did,
- and the reports the owner reads at the end of the month.

There is **one Django app**, `clients/`. It is not a mistake — everything in
the business touches a client, and splitting it into a dozen apps would only
buy circular imports. Separation is achieved by **packages inside the app**
(`models/`, `views/`, `urls/`, `services/`), one module per domain.

---

## 2. Architecture in one screen

```
Browser (Bootstrap, server-rendered templates)          Android app (Compose)
        │                                                        │
        │  HTML pages                                            │ JSON, same
        │                                                        │ session cookie
        ▼                                                        ▼
   clients/views/<domain>.py                        clients/views/app_*.py
        │                                                        │
        └───────────────────► clients/services/ ◄────────────────┘
                                    │            (the only business logic)
                                    ▼
                            clients/models/<domain>.py
                                    │
                                    ▼
                                 Postgres
                    ▲                            ▲
                    │                            │
        clients/management/commands/    clients/signals.py
        (cron: reminders, imports)      (side effects on save/delete)
```

**The rule the whole codebase is organised around:** a view parses input and
renders output; it does not decide anything. Anything a phone and a browser
can both do lives in `services/`, so the two surfaces cannot drift apart.
Every regression this codebase has had came from the same shape — logic
written twice, once for web and once for the app, and only one copy fixed.

### Layers

| Layer | Path | Rule |
|---|---|---|
| Models | `clients/models/` | Split by domain, re-exported in `__init__.py`. **Always import from `clients.models`**, never the sub-module — the split must stay an implementation detail. Normalisation (phone, policy number) lives in `save()`, because forms, APIs, imports and seeds all write rows and only the model covers all four. |
| URLs | `clients/urls/` | Split by domain, assembled under the single `clients` namespace. Every name stays `clients:<name>`. |
| Views | `clients/views/` | One module per domain. `app_*.py` are the mobile JSON APIs under `/clients/api/app/…`. |
| Services | `clients/services/` | The business logic. One implementation per rule, called by both surfaces. |
| Permissions | `clients/permissions.py` | The only place authorization is decided. |
| Signals | `clients/signals.py` | Cross-model side effects (client status recompute, push mirroring, audit rows, orphan cleanup). |
| Crons | `clients/management/commands/` | Every command is either in `CRONJOBS` or listed as a manual tool. Nothing in between. |
| Templates | `templates/` | Server-rendered, Bootstrap, one folder per module. |
| CSS | `static/css/ki-record.css` | The shared record shell. Shared rules are promoted here, never copied per template. |
| Tests | `clients/test/` | `manage.py test clients`. Each feature has a file that pins its rules. |

---

## 3. Platform modules (the things everything else stands on)

### 3.1 Authentication & permissions — `views/auth.py`, `permissions.py`

**Why:** three kinds of people use this — the owner (admin), a manager with a
configurable subset of admin rights, and advisors who should only see their
own book. Scattering `if user.is_superuser` across 20 view modules makes "who
can do what" unauditable.

**How:** roles live on `Employee.Role` (admin / manager / employee). Manager
rights are feature flags on the `ManagerAccessConfig` singleton, edited on the
Manager Rights page. `permissions.py` exposes `is_admin`, `is_admin_or_manager`,
`can(user, "approve_sales")` and the matching decorators `@admin_required`,
`@admin_or_manager_required`, `@can_required(flag)`. The decorators are
JSON-aware: an API or XHR caller gets a JSON 403, not an HTML error page — the
app would otherwise render a login page inside a data screen.

Login has a lockout (5 attempts / 15 minutes). Sessions last 30 days and roll
on every request (`SESSION_SAVE_EVERY_REQUEST`) so an advisor's phone is not
logged out mid-week.

**Use:** every view gate, every template menu item, every app endpoint.
`clients.test.test_permissions` / `test_role_permissions` pin it.

### 3.2 Clients — `models/clients.py`, `views/clients_views.py`

**Why:** the client record is the spine. Everything else points at it.

**How:** `Client` carries its own serial-number primary key (allocated under a
Postgres advisory lock so concurrent adds cannot collide), contact details,
PAN, DOB, the per-product holdings (SIP, lumpsum, health/life/motor cover, PMS)
and `mapped_to` — the employee who owns the relationship. The holdings columns
are **derived, not typed**: `signals.update_client_status` recomputes them from
approved sales on every sale save/delete. `ClientMappingAudit` records every
re-assignment, because "who owned this client in March" is a commission
question.

Phone numbers are normalised in `Client.save()` via `utils/phone_utils.py` —
see §5.9 for why that is not a detail.

**Use:** `/clients/all/`, `/clients/my/`, client profile, search, map, bulk
re-assign, and the client picker on every other screen.

### 3.3 Households (Families) — `models/clients.py:Family`, `views/clients_views.py`

**Why:** advice is given to a family, not to an individual. Two spouses and a
child are one relationship and one wallet.

**How:** an optional `Client.family` FK. `Family` computes its band (a wealth
label from total holdings) so the list can be read at a glance. Nothing is
mandatory — plenty of clients stand alone.

**Use:** `/clients/families/`, and the household block on a client profile.

### 3.4 Product catalog — `models/catalog.py`

**Why:** every report, picker, incentive rule and margin slab keys off a
product. It has to be one list.

**How:** `Product` is a two-level tree — main products (Life Insurance, Health
Insurance, Mutual Fund…) and sub-products (`Product.parent`, e.g. "Term Plan").
A sub-product exists for exactly one reason: **so a sale or a renewal can name
the exact plan sold.** Everything else in the system deals in main products,
with a sub-product's business folded into its parent.

That rule lives in `ProductQuerySet`:
`Product.objects.selectable().main().in_display_order()`. The only code allowed
to drop `.main()` is the sale entry forms, the renewal forms, and Product
Management (which is the catalog editor and must show the tree).

**Restricting a picker is only half the job** — the matching behind it must
roll up too, or a category filter silently matches nothing. Incentive rules
match `product_ref__parent_id`; campaigns do the same; the client product
filters fold children into the parent.

`ProductMarginSlab` holds the firm's own margin structure per product (volume
bands for health, flat rates elsewhere). `PlanPptRate` holds life-insurance
commission rates per plan per Premium Paying Term, in Advisor and MDRT columns
— life margins depend on PPT, which is why the sale form has a PPT calculator
and `Sale.margin_percent_snapshot` freezes the rate at sale time.

**Use:** `/clients/admin/products/`, every picker, every margin report.
`clients.test.test_product_subproducts` pins the main-products-only rule.

### 3.5 Audit log & firm settings — `models/ops.py`, `views/audit.py`

**Why:** money records need history, and "who deleted that sale" must have an
answer that survives the row being gone.

**How:** `AuditLog` is polymorphic by design — model name + pk, **no FK** — so
deleting the target keeps the history. Written from `signals.py` for sale
status changes, sale/client deletion, and from the app crash endpoint
(`app.crash`, the only crash signal there is, since the app is self-hosted with
no Play Console).

`FirmSettings` is a singleton: branding, PDF theme, and the MDRT qualification
**year**. Stored as a year rather than a boolean so it auto-reverts every
1 January with no cron.

**Use:** `/clients/admin/audit-log/`, `/clients/admin/firm-settings/`.

### 3.6 The record shell (UI) — `templates/_shell/`, `static/css/ki-record.css`

**Why:** twenty screens built by hand drift into twenty designs. The shell
makes "looks like the rest of the CRM" the default rather than an effort.

**How:** `base.html` renders a breadcrumb and a KPI strip for **any** view that
supplies `crumbs` / `kpis` — a page template never includes the partials
itself. `context_processors.breadcrumbs` derives crumbs from the URL name when
a view sets none. Detail pages use the record header (`.ki-rec`, the `.ki-mono`
monogram whose initials and colour are computed by template filters, `.ki-tag`
pills). Lists mask contact details with `mask_phone` / `mask_email` /
`mask_pan` — display-only; the DB and the tap-to-call links keep real values.

Hard constraints, enforced by `test_theme_coverage`: tables sit inside
`.ki-table-wrap` / `.ki-table-scroll` or they push the page sideways on a
phone; icon-only buttons need `aria-label`; shared CSS is promoted to
`ki-record.css` rather than copied.

One Django trap worth knowing: `{# #}` comments **cannot span lines** — a
multi-line one renders as visible page text. Use `{% comment %}`.

---

## 4. Business modules

### 4.1 Sales — `models/sales.py`, `views/sales.py`, `services/sales.py`

**Why:** the sale is the atomic business event. Everything downstream —
incentives, margins, client holdings, targets, reports — is derived from it.

**How:** `Sale` records client, employee, product (+ sub-product), amount,
cover, status (pending → approved / rejected) and the points earned.
`services/sales.py` owns the *workflow* (creation finalisation, approve/reject,
delete) so web pages and mobile endpoints cannot drift; `Sale.save()` →
`compute_points()` owns the *maths* by delegating to `services/incentives.py`.
`employee` is `PROTECT`ed — deleting an employee must never erase their sales
history; deactivate instead.

Domain facts encoded in the model:

- **A sale is booked when the insurer approves the policy**, which is not when
  the policy starts. Health/Life sales therefore carry a separate mandatory
  `policy_date` (commencement, read off the document) and a mandatory
  `policy_number`. Renewal anniversaries are measured off `renewal_basis`
  (`policy_date`, falling back to `date` only for legacy rows). Keying renewals
  off the sale date silently mis-dates every reminder — it happened.
- `policy_number` is upper-cased and stripped in `save()`, so `"ins123 "` and
  `"INS123"` cannot become two policies. Enforced in three places: the web
  forms' `clean()`, `app_sale_create`, and `save()`.
- **Multiyear health** (`policy_years` 2/3): the entered amount is the full
  multi-year premium, but only the first-year slice (`annual_premium`) is Fresh
  business now. Years 2..N are recognised as renewals on each anniversary,
  derived live — no records to maintain. Health-only.
- **EMI** (`emi_months` 5/8/11, due the 5th): multiyear premiums are usually
  paid monthly, and a missed EMI can cancel the policy. The `emi_reminders`
  cron pushes the client's mapped employee and auto-assigns a call task.
- `bonus_points` is tracked separately from `points` because the incentive
  ladder pays the *delta* against bonus already released; summing `points`
  would count the flat base rate as bonus and starve the ladder.

`Redemption`, `NetBusinessEntry`, `NetSipEntry` capture the outflow side so
net-business dashboards are honest about withdrawals.

**Use:** `/clients/sales/add/`, `/clients/sales/all/`, `/clients/sales/approve/`,
the app's Add Sale + Sales screens, and every report.

### 4.2 Renewals — `models/clients.py:Renewal`, `views/renewal_views.py`

**Why:** renewal premium is recurring revenue and is reported daily.

**How:** a `Renewal` names client, product (+ plan), premium, renewal date and
collection date, and links to the tracker policy it renews (`Renewal.policy`).
`insurance_kind` / `kind_q()` classify by `product_ref` first, because
product_type was never persisted on historical rows.

The list page opens on **this month's collection** — the figure that is
reported every day, and nobody should have to type two dates for it. Period
chips (This Month / Last Month / This FY / All) set the window; typing a date
is what "Custom" means; a search no longer clears the month.

**Each date bound works alone**: `renewal_date` and `premium_collected_on` each
filter with separate `__gte` / `__lte` clauses. The old `if start and end`
pairing meant a lone "Payment Start" filtered nothing at all — the same bug
sales once had.

**Use:** `/clients/renewals/all/`, add/edit, the app's Renewals screen.
`test_renewals_period` pins the chips and the bounds.

### 4.3 Insurance tracker, claims and meetings — `models/insurance.py`, `views/insurance.py`, `services/insurance_sync.py`, `services/claims.py`

**Why:** the firm has to answer "what cover does this family hold, and what
happens when they claim". A sale record alone cannot answer it — the old book
was sold before the tracker existed.

**How, tracker:** `InsurancePolicy` is the live policy register. It fills
itself from two directions (`services/insurance_sync.py`):

- an approved Health/Life **sale** auto-creates one policy, idempotent via
  `InsurancePolicy.source_sale`, start date = the sale's `policy_date`.
  Un-approving removes it, unless someone has since filled in a real policy
  number or insurer — then it is merely detached.
- a **renewal** for a client with no policy of that type back-fills one. That
  is how the pre-tracker book gets captured: the add-renewal page shows the
  client's existing policies as tick options, and "New policy" creates and
  links a fresh one. `link_renewal_to_policy` owns this, and **both** the web
  view and `app_renewal_create` call it — the app once skipped it, leaving
  every phone-entered renewal an orphan.

**How, claims:** claims are raised and worked in-app, not just in Django admin.
Stages are Intimated → File Received → Submitted → Settled / Rejected;
`services/claims.py:advance_stage` stamps the matching date once and logs a
`ClaimActivity` (the timeline and the notes share that model).
`ClaimDocument` stores files in the **client's** Drive folder and serves them
through a proxy. A claim follow-up is a `Task` (§4.6) — `ClaimReminder` was
deleted in migration 0123.

`Meeting` records client reviews and the next review booked forward.

**Use:** `/clients/insurance/`, `/clients/claims/`, `/clients/meetings/`, and
the app's Insurance screen — which is the module that most needed a phone: a
claim is intimated over a phone call and its papers are photographed on the
spot. The app's stage list, modes and document kinds are **served** by
`/api/app/insurance-meta/`, never hard-coded, so a new stage does not need an
APK release.

### 4.4 Lead pipeline (SPANCO) — `models/leads.py`, `views/leads.py`, `services/leads.py`

**Why:** the old pipeline computed a lead's position (pending / half-sold /
processed) from three hard-coded product rows — so a lead's stage was a *side
effect of what had been sold*, which is exactly backwards. You cannot manage a
funnel you cannot see.

**How:** six stages in pipeline order — **Suspect → Prospect → Approach →
Negotiation → Conclusion → Order**. `STAGE_HELP` carries the one-line meaning
of each and is rendered on the stepper, the board and the app, so the method is
taught by the screen rather than by a training deck.

**The stage is recorded, never derived.** Every move goes through
`services/leads.set_stage()`, which writes a `LeadStageEvent` (from → to, note,
who) and stamps `stage_changed_at`. Assigning `lead.stage` anywhere else makes
the funnel lie.

- **Lost keeps the stage it died at** (`mark_lost` + `lost_reason`) — that is
  what makes a weak step visible. The column is still `is_discarded` so no
  historical row moved; the UI says Lost.
- `funnel()` counts a lead as having *reached* every stage at or below where it
  stands, lost ones included (they did get that far), so stage-to-stage
  conversion is honest without replaying the event log.
- **Products are per lead** (`LeadInterest` → `Product`), not a fixed trio.
  Nothing is seeded: neither the web form nor `app_lead_create` invents
  requirements for a lead nobody has qualified.
- Conversion is allowed at **Order** only, and copies no cover/SIP figures —
  `signals.update_client_status` recomputes those from approved sales anyway.
- `STAGE_HOT` (Approach / Negotiation / Conclusion) is the live-pipeline band:
  qualified, in play, and where attention still changes the outcome. Those
  items get colour and priority on the agenda.

Migrations **0118** (schema) + **0119** (data) — kept separate because a
deferred `CREATE INDEX` cannot follow a bulk row update in one Postgres
transaction. 0119 put every existing lead back at Suspect (the owner's call);
where it stood is written into its first stage event.

**Use:** `/clients/leads/` (working list + stage KPIs), `/clients/leads/board/`
(six columns, read-only — moves happen on the detail page so they all go
through one logged path), `/clients/leads/pipeline/` (funnel, per-employee win
rates, leads stalled 14+ days). The app carries the pipeline on its home
screen, one swipeable page per stage from Approach on.

### 4.5 Incentives & campaigns — `models/incentives.py`, `services/incentives.py`, `views/sales.py`, `views/campaigns.py`

**Why:** advisor pay is the most-argued number in the firm. It must be
computed one way, explainable to the person earning it, and impossible to quote
differently on two screens. Points = rupees.

**How:** `services/incentives.py:quote()` is the single implementation. It is
called by `Sale.compute_points()` on every save **and** by the Incentive
Structure page's what-if calculator, so the page can never quote a number the
sale would not pay.

A rule's slabs are read one of two ways (`IncentiveRule.slab_mode`):

- **bonus** — rupees *earned to date* once the period's cumulative volume
  crosses a rung, paid on top of the flat unit rate, and only as the delta
  against what the period already released. (Life: 1.75% base + an Apr–Mar
  ladder.)
- **rate** — a percent resolved from the period's volume and applied to this
  sale instead of the unit rate. (Health: the seller's own monthly Fresh
  volume.)

`slab_period` is the window: calendar month, or Apr–Mar financial year.

Rules that are load-bearing and easy to break:

- **Health Port earns nothing.** The `policy_type == "port"` early return in
  `quote()` must stay — without it a Port sale falls through to the Fresh band
  and is paid the month's rate, which is the opposite of the rule. Port also
  never pushes Fresh into a higher band.
- **The FY prize is never released by a sale.** A rung reached in June is a
  standing, not a bill — the year can still climb. `quote()` returns no bonus
  for an FY-period ladder, so `bonus_points` stays 0, and the prize is handed
  over as cash after year-end and recorded as a `BonusPayout`. That record is
  the only thing that marks it released. `life_bonus_status` carries
  `payable_from`, `fy_closed`, `due_now`.
- **No monthly deduction exists.** The old 3L/6L/9L monthly grid used to be
  netted off the yearly ladder automatically — a month over ₹3L cancelled the
  prize it had just earned. Deleted 2026-07-31 (migration 0116). Never
  reintroduce a per-month adjustment to a per-year ladder.
- **Multiyear health pays one year at a time.** Year 1 lands with the sale;
  years 2..N land on the anniversary as an `IncentiveAccrual` created by the
  `multiyear_incentive_accruals` cron, idempotent via
  `unique_together(sale, year_index)` so a missed day catches up and a re-run
  is a no-op.
- **Points come from three places** — `Sale.points`, `IncentiveAccrual`, and
  `BonusPayout`. Any figure shown to an employee as "what I earned" must add
  all three. Sales *reports* stay sales-only on purpose: an accrual is earned
  points, not business volume.
- Changing a rule never rewrites saved sales. `recompute_sibling_sales` re-runs
  the rule's whole *period* after a status change (so rate bands re-rate when
  volume crosses) and re-saves the originating sale too — a sale is priced
  before it exists in the table, so its own amount is missing from its period
  until a second pass.

`Campaign` / `CampaignProduct` / `CampaignSlab` are time-bound, product-wise
promotions that replace the regular rule during their window.

**Use:** `/clients/incentives/` (admin structure), `/clients/incentives/payout/`
(the month's bill), `/clients/incentives/life-bonus/` (FY ladder position), and
`/clients/incentives/calculator/` — the **employee-facing** page, which must
never print firm margin or commission percentages; `explain()` renders
everything in points.

### 4.6 Tasks — `models/tasks.py`, `views/tasks.py`, `services/tasks.py`

**Why:** it replaced an external "Automate Tasks" tool. Once it existed and
rang phones reliably, it became the substrate every other reminder folds into.

**How:** `Task` carries assignee, creator, category, priority, due date/time,
status, an optional client link (tap-to-call from the task), checklist items,
comments, attachments (Drive-backed) and an immutable `TaskActivity` trail.
Supporting models: `TaskSubscriber`, `TaskTemplate`, `RecurringTaskRule`.

- One assignment to several people = **one row per person** sharing an
  `assign_group`, so each tracks its own status while the creator's Delegated
  list collapses them to "Mansi +4".
- **Acknowledgement** is real: the assignee taps Acknowledge, and
  `tasks_ring_due` re-rings unacknowledged high/critical tasks every four hours.
- **`Task.silent`** — a task assigned after hours can be marked "don't ring".
  It still assigns and still notifies; only the alarm is suppressed. There are
  **three ring paths and all three check it**: the assignment/comment push
  (`create_notification(..., ring=)`), the due-time ring (writes a plain
  notification instead, still stamping `due_alarm_sent_at`), and the app's
  **on-device exact alarm**, which is starved by withholding `due_at_ms`. Miss
  the third and the phone still goes off.
- Deletion is soft (recycle bin, admin-purgeable).

`services/tasks.py` gives every mutating view two things: `log_activity` and
`notify_task`. Creating a `Notification` auto-mirrors to FCM via
`signals.push_on_notification`, so no view calls push directly.

**Use:** `/clients/tasks/…` (Dashboard, My, Delegated, Subscribed, All,
Deleted, Activities, detail), and the app's Tasks module with its own bottom
nav.

### 4.7 Follow-ups are tasks — `services/followups.py`

**Why:** a lead follow-up used to be its own model with its own list, its own
Done button and its own reminder — and the reminder was the weak part: it
produced a plain tray notification nobody saw, while a task rings the phone
like an alarm clock. Two rows for one commitment was the thing to avoid: close
the task, leave the follow-up pending, and the calendar nags forever.

**How:** every dated follow-up on any record is a `Task` and nothing else.
`LeadFollowUp` and `ClaimReminder` were migrated into Task and dropped
(0121 schema → 0122 data → 0123 drop — kept separate; the index build must not
share a transaction with the bulk row write). `services/followups.py` is the
only way to create one.

- `Task.source_kind` / `source_id` point back at the record ("lead"/42). Not an
  FK, so **nothing cascades** — `signals._delete_followup_tasks` removes them
  when the lead or claim is deleted, or a dead record's follow-up rings forever.
- `created_by` is the **system** user (inactive, unusable password) — that is
  what marks a task as generated rather than typed. `assigned_to` is whoever
  OWNS the record, falling back to whoever scheduled it: an admin scheduling on
  someone else's lead must ring *that* phone.
- Priority is **medium** on purpose. High/critical re-ring every four hours,
  and twenty follow-ups a day at that volume is a storm people learn to swipe
  away. They land in a "Follow-up" category so the real task list stays
  readable.
- The chase ends by itself: `cancel_open()` runs when a lead is marked lost or
  converted.
- Closing and rescheduling are **task** actions (`task_set_status`,
  `task_reschedule`), and both clear `due_alarm_sent_at` — a task rescheduled
  without clearing it never rings again.

`test_followup_tasks` pins all of it.

### 4.8 Call tracking & call follow-ups — `models/calls.py`, `views/calls.py`, `services/calls.py`

**Why:** advisors work the phone. The firm needs to know who was called, what
came of it, and who must be called back — without anyone typing a call log.

**How:** the Android app listens for call state changes and POSTs each event to
`/api/calls/sync/`. A post-call popup offers to schedule a follow-up; a
per-minute cron pushes the reminder; tapping the notification dials the number.

- **One number = one pending reminder.** A new follow-up supersedes the older
  pending ones — marked `superseded`, **kept, not deleted**, because `attempts`
  ("chased four times, never picked up") is the useful number.
- **A connected outgoing call closes the follow-up it answers**
  (`_close_called_followups`), outcome "spoke". Only calls placed *after* the
  row was created count — the popup creates the next follow-up seconds later
  and that one must survive — and only connected ones: an unanswered dial is
  exactly when the reminder still matters.
- "Done" carries an **outcome**. Swipe right closes without asking; the Done
  button asks; the ringing alarm screen asks too (the most-used Done path).
- `services/calls.outcome_breakdown()` is one implementation feeding both the
  web Call Analytics page and the app's — the way the call counts should have
  been from the start.
- `services/calls.caller_names()` batches name resolution (one query per table,
  never per row) and covers the two gaps a stored FK cannot: the number belongs
  to a **lead**, or the client was added *after* the call was logged.
- `AppDeviceStatus` is a health snapshot posted by each device (cached popup
  config, SIM state, last popup shown/skipped and why, alarm permissions), so
  an admin can see from Call Analytics *why* a phone shows no popups. The
  `detect_silent_devices` cron flags phones with no synced calls for 3+ days —
  usually an uninstall.

**Call follow-ups deliberately stayed out of the task model** (unlike lead and
claim follow-ups): the popup creates them automatically on every unanswered
call, and one task per missed dial would bury the task list.

**Use:** `/clients/calls/analytics/`, `/clients/calls/followups/`, and the
app's Calls tab (`/api/app/followups/` serves pending + done-today + the
overdue badge count).

### 4.9 The common calendar and the agenda — `services/calendar_feed.py`, `views/calendar_views.py`

**Why:** everything dated in the CRM used to have its own little list. One
person's day is one list.

**How:** `feed_items()` aggregates every dated item into one normalised shape,
consumed by the FullCalendar page and by the dashboard agenda widget.

- **Every source must be team-scopable**, or folding a model into Task silently
  hides it from admins. `feed_items(team_followups=True)` widens the sources an
  admin sees to the whole team, optionally narrowed by employee. `_tasks()`
  honours it exactly like `_call_followups()` — it did not when lead follow-ups
  became Tasks, and for five days an admin saw all employees' calls and only
  their own tasks, i.e. no pipeline at all.
- **A follow-up is a Task, so the badge must say which kind.** They share the
  `task` source (one chip, one reminder pipeline) but carry a `source_label` of
  Lead / Claim / Task, and a lead follow-up carries the lead's SPANCO stage.
- **Adding a source is three edits, not one:** the feed, the agenda's
  `.agenda-source-<name>` badge rule, and the calendar page's filter checkbox +
  colour map. `insurance_renewal` had only the first, so it was unstyled on the
  agenda and completely unreachable on the calendar (the page always sends a
  `sources` list built from its checkboxes).
- **The agenda cannot show a lead nobody has dated** — which is exactly the
  lead that is dying. `services/leads.needs_attention()` answers that and is
  deliberately *not* part of the feed: live-pipeline leads with no open
  follow-up, or stalled 14+ days, rendered as their own panel on both
  dashboards.

`CalendarEvent` is the hand-made event (meetings, birthday calls), with
reschedule / skip / mark-done endpoints. `test_agenda_pipeline` and
`test_unified_calendar` pin the feed.

### 4.10 Dashboards, targets and reports — `views/dashboards.py`, `views/reports.py`, `services/targets.py`, `models/targets.py`

**Why:** the owner reads numbers daily; advisors need to know where they stand
against their own target, not the firm's.

**How:** targets are per-employee and per-head. `EmployeeTarget` holds an
individual's monthly target for a product (competency differs per person);
where there is no row, `Target` supplies the product-wide baseline. Daily
targets are *derived* by dividing the month across working days, never stored.
`close_month` (cron, 1st at 00:05) freezes the month into
`MonthlyTargetHistory` so past performance stops moving.

Reports: past performance, monthly business report, business analytics, and the
**Business Overview** — which is where `Expense` / `ExpenseCategory` are used,
so revenue can be read against cost.

Margin reports print **one row per main product**, but each plan's revenue is
valued at its own rate before blending — a rolled-up row must never reprice a
sub-product at its parent's margin. Health is the deliberate exception: its
slabs are a band on the *category's* monthly volume, so they resolve at the
category.

### 4.11 Employee performance — `services/employee_performance.py`, `views/team.py`

**Why:** the team detail page showed six numbers off `Sale` and stopped. Every
other piece already had a service behind it; nobody had put them against a
single person.

**How:** `snapshot(emp)` assembles one employee's whole record with the firm —
business (sales + renewal premium, FY and lifetime), earnings, target vs
actual, the SPANCO funnel and win rate, task and call activity, client value,
and a 12-month trend. `list_stats(employees)` does the list cards in **four
queries**, never a per-row lookup.

"Points earned" is sales points + accruals + bonus payouts (§4.5) — showing
only the first understates what somebody earned. `test_team_performance` pins it.

**Active and inactive people are two lists, not one list with a badge:** the
team list opens on Active, Inactive and All are tabs, and a search stays inside
the tab it was typed in.

### 4.12 People care — `models/hr.py`, `services/people.py`

**Why:** the stated premise is that keeping a team is mostly non-financial —
being noticed matters more often than being paid more. So recognition is
treated like any other piece of work: generated, surfaced, and nagged about
when it slips.

**How:** `Employee` owns names, DOB, joining date, position, domain,
reports-to, emergency contact, skills — not just a login. Derived:
`tenure_months`, `total_experience_months`, `tenure_display`,
`profile_completeness`. `EmployeeMilestone` + the `employee_milestones` cron
(daily 08:30) generate birthdays, work anniversaries and long-service years,
nudge admins about today and tomorrow, and flag anything that slipped past
unmarked. Admins are never asked to celebrate themselves.
`people.celebrate()` notifies the employee — that is the point of the module.

Employees maintain their own details at `/clients/me/profile/`. That form must
**never** expose salary, role, employee number, joining date or position:
self-service is not a route to a pay rise. `profile_completeness` counts only
fields an employee can reasonably fill; admin-owned gaps are reported
separately.

### 4.13 Notifications & push — `models/engagement.py`, `services/push.py`, `views/notifications.py`

**Why:** one delivery pipeline, or half the reminders never leave the server.

**How:** creating a `Notification` row is the whole API. `signals.push_on_notification`
mirrors it to FCM. `services/push.py` activates only when Firebase credentials
are configured and is a **silent no-op otherwise** — the pattern every external
integration in this codebase follows, so the app always runs without secrets.
`PushDevice` holds the tokens. Channels are per-kind
(`ki_followup_alarms` / `ki_task_alarms` / `ki_daily_digest`) and the icon is
the monochrome `ic_stat_ki`.

The topbar bell endpoints touch only `recipient=request.user`, so they need no
role gate.

### 4.14 Messaging (WhatsApp) — `models/engagement.py`, `views/messaging.py`

**Why:** bulk client communication without exporting a spreadsheet.

**How:** `MessageTemplate` with safe variable substitution (compiled once at
module load), `MessageLog` for what was sent. The screens do bulk send,
preview and CSV export. Note: the WhatsApp **Cloud API** integration was built
and then removed in 2026-07 when Meta setup was postponed — what remains is
template + log + export.

### 4.15 Business Links — `models/links.py`, `views/links.py`

**Why:** replaced the external "Automate Links" tool. Everyone needs the same
twenty portals and nobody should keep their own bookmark list.

**How:** `LinkCategory` / `Link` / `LinkFavorite`. Admins manage categories and
any link; employees add links and manage their own; everyone browses and
favourites. Reuses CRM auth, nav and design system — the point of bringing it
in-house.

**Use:** `/clients/links/`, and the app's "My Apps" tab (still WebView).

### 4.16 KYC issues & client merge — `views/kyc.py`, `services/client_merge.py`

**Why:** PAN is the client's identity key — without it, profiles can't be
matched or de-duplicated. And accidental duplicate profiles hold real business
records, so plain deletion would cascade them away.

**How:** the KYC screen lists clients missing PANs (each employee sees their own
mapped clients; managers and admins see everyone) and lets PANs be filled
inline. For admins it also surfaces likely duplicates with merge and safe-delete
actions. `merge_clients()` repoints every relation from the duplicate onto the
kept profile, copies over contact fields the kept profile is missing, then
deletes the emptied duplicate.

**Use:** `/clients/clients/kyc-issues/`, `/clients/clients/merge/`,
`/clients/clients/bulk-merge/`.

### 4.17 Financial planner — `services/financial_plan.py`, `views/planner.py`

**Why:** the previous planner calculated in the browser, and its PDF endpoint
rendered whatever numbers the page posted — so the report was not necessarily
the plan.

**How:** a port of `docs/planner_fin/Financial_Plan_Template.xlsx`. `compute()`
is the only implementation; the page and the PDF both call it, and **nothing
recalculates in the browser**. Excel's `FV/PV/PMT/NPER/RATE` are reimplemented
at the top of the module with Excel's sign convention. The workbook ships cached
formula results and `test_financial_plan.py` asserts against them cell by cell —
if a number drifts, the sheet wins.

Order matters and is not obvious: **insurance premiums are computed before the
investible surplus**, because the surplus is post-tax income less living
expenses less the protection bill, and every SIP is sized inside it. Section
order follows the sheet: protection → emergency fund → goals → retirement →
allocation. Each row carries the workbook's own "Formula / Logic Used" note,
because that column is half the value of the sheet.

`INPUT_GROUPS` / `CALC_GROUPS` drive the form, the parser and the defaults from
one list, so a new input cannot be added to only two of the three. Lookup tables
(income multiples, premium per lakh, glide path) are module constants — they are
indicative retail benchmarks and must be replaced with real quotes per client.

The PDF prints "Rs", not ₹: the base-14 PDF fonts have no rupee glyph and it
renders as a black box.

**Use:** `/clients/sales/financial-planner/` + its download endpoint. Stays a
WebView screen on the app.

### 4.18 Google Drive — `services/google_drive.py`

**Why:** client documents, policy copies, claim papers and task attachments
have to live somewhere the firm already uses.

**How:** Application Default Credentials — `google.auth.default()` picks up
whichever credential is present (a developer's `gcloud` login locally, the
attached service account in GCP). Per-client folders are created **lazily on
first request** (`Client.drive_folder_id`), never up front. Claim documents and
task attachments both reuse `get_or_create_client_folder`.

After adding an insurance sale or renewal, the success message *links* to the
client's Drive folder — a link, never a forced redirect, so daily bulk entry is
not interrupted.

---

## 5. Cross-cutting decisions worth knowing before you edit anything

### 5.1 One implementation per rule
Web view and app endpoint both call the same service. Every drift bug in this
repo's history is the same shape: `link_renewal_to_policy` called from the web
but not the app; `outcome_breakdown` written twice; incentive maths quoted
differently by the calculator than by the sale.

### 5.2 Normalise on the model, never in the form
`Client.save()`, `Lead.save()`, `Sale.save()`, `InsurancePolicy.save()`. Forms,
APIs, imports and seeds all write rows; only the model covers all four paths.

### 5.3 External integrations no-op when unconfigured
Firebase and Drive both read credentials from env and silently do nothing when
absent (`services/push.py` is the pattern). The app must always run without
secrets.

### 5.4 Migrations
Every schema change ships `makemigrations` in the same commit;
`makemigrations --check` must stay clean. Keep a schema migration and its data
migration **separate** — a deferred `CREATE INDEX` cannot follow a bulk row
update in one Postgres transaction (0118/0119, 0121/0122/0123).

### 5.5 Deletion semantics
`PROTECT` on business records (sales vs employees). No FK on audit or follow-up
back-pointers, with an explicit signal to clean up. Soft delete + recycle bin
for tasks.

### 5.6 Money and points are different things
Money is business volume and belongs in sales reports. Points are what a person
earned and must include accruals and bonus payouts. Never total them together.

### 5.7 Phone numbers are a matching key
A spreadsheet import once stored 2,168 of 2,937 client numbers as
`"9423440791.0"`. That is worse than an unmatchable string: every matcher strips
non-digits and takes the last ten, turning it into `4234407910` — a *different*
number. It did not fail to match, it matched the wrong person, and 174 clients
shared a mis-derived key that silently fed duplicate detection and KYC grouping.

There is exactly **one normaliser**, `phone_utils.digits10`; the two other names
for it are aliases. Never hand-roll `re.sub(r"\D", ...)[-10:]` again — that is
the line that read the float rows wrong. `manage.py fix_phone_floats` is the
one-off repair (dry run by default, `--apply` writes, idempotent).
`test_phone_normalisation` pins it.

### 5.8 Reminders ring on three paths
Server push, server cron, and an **on-device exact alarm** that fires with no
network. Any change to whether something rings has to be made in all three, or
the phone still goes off (see `Task.silent`).

---

## 6. Scheduled jobs (`CRONJOBS` in `config/settings.py`)

| When | Command | What it is for |
|---|---|---|
| 1st, 00:05 | `close_month` | Freeze the month into `MonthlyTargetHistory` |
| Sun 03:00 | `cleanup_data` | Old notifications, message logs, expired sessions |
| every minute | `send_followup_reminders` | Due call follow-ups + calendar events |
| every minute | `tasks_ring_due` | Exact due-time ring + 4h re-ring of unacked high/critical |
| */15 min | `tasks_mark_overdue` | Flip past-due tasks to Overdue and notify |
| hourly | `tasks_send_reminders` | Day-before / same-day task reminders (gated to the configured hour) |
| daily 00:20 | `tasks_generate_recurring` | Materialise recurring task instances |
| daily 06:10 | `multiyear_incentive_accruals` | Year 2..N points of multiyear health policies |
| daily 08:00, 3rd–5th | `emi_reminders` | EMI collection push + call task (due the 5th) |
| daily 08:30 | `employee_milestones` | Birthdays / anniversaries + admin nudge |
| daily 08:45 | `renewal_reminders` | 30/15/5 days before a policy renews |
| daily 09:15 | `detect_silent_devices` | Phones with no synced calls for 3+ days |

**Manual tools** (deliberately not scheduled): `fix_phone_floats`,
`prod_readiness_check`, `clear_unreleased_ladder_bonus`, and the seeds —
`seed_demo_crm`, `seed_demo_tasks_links`, `seed_incentive_structure`,
`seed_health_slabs`, `seed_life_rates`.

Every command must be in one list or the other. Anything that is in neither
gets deleted — that is the monthly 5S rule in `CLAUDE.md`.

---

## 7. The Android app — `mobile/`

**Why native:** the app started as a WebView shell (Capacitor) and is being
rebuilt screen by screen in Kotlin + Jetpack Compose, ordered by daily usage.
The reason is not aesthetics: call tracking, exact alarms that ring offline,
the camera, and contact access are all things a WebView cannot do.

**How it hangs together:**

- **Auth:** native screens reuse the WebView's session cookie
  (`CookieManager`). No tokens, no second auth system.
- **API:** plain Django JSON views under `/clients/api/app/…` — session auth
  plus a CSRF header, added one endpoint set per screen.
- **Shell:** `RouterActivity` (launcher) → `ShellActivity` (bottom nav: Home ·
  Clients · Add · Calls · Menu). Converted tabs render Compose; unconverted
  items open `WebActivity` at the right URL, so the app is fully usable
  throughout the migration.
- **Menu is grouped** (Work / Insurance / Reports / Admin / This phone), not a
  flat run in the order screens happened to ship. A new screen picks a group.

**Rules the lint test (`test_android_responsive`, 17 rules) enforces:**

- Sizes go through `rsp(n)` / `rdp(n)` (`ui/Responsive.kt`), never raw `n.sp` or
  a fixed height on anything interactive. This is separate from the user's
  font-scale accessibility setting, which Compose already applies — use
  `heightIn(min =)` so labels can grow.
- Three or more buttons in a row → `ActionRow` (wraps), not `Row` (squeezes).
- Never a bare glyph as a button: no ripple, no role, ~28dp target, and TalkBack
  reads the character. Use `IconButton` with a `contentDescription`.
- **Never rewrite a `TextField`'s text inside `onValueChange`** (`.uppercase()`,
  `.trim()`, a character filter). Handing the String overload a different value
  than it holds collapses the selection and throws the cursor to the end, so a
  mid-word typo cannot be fixed. This is what "the keyboard doesn't work"
  reports actually were. Shape input with `KeyboardOptions`; normalise on the
  server.
- Dates are picked, never typed. Navigation state is `rememberSaveable`.

**Shared building blocks — reuse, don't re-roll:** `ScreenHeader.kt`,
`Fields.kt` (`PickerField`, `DateField`, `pickDate/pickDateTime/pickTime`,
`moneyInput`), `Refresh.kt`, `Load.kt` (`rememberLoader`, cache-first fetch),
`Common.kt`, `Session.kt`, `Attach.kt` (camera + picker; writes through the
FileProvider the manifest already declares — **no CAMERA permission is declared
and none should be**, adding one would demand a runtime grant this flow does not
need).

**Offline:** screens paint the last-good response from `net/Cache.kt` and
refresh by pull. Writes that are safe to replay pass `offlineQueue = context`
(`net/Outbox.kt`); creating a sale or a client deliberately does not. A lead
stage move queues — it is the write that happens in a client's living room —
and `services.leads.set_stage` drops a replay that repeats the last event, so
the queue cannot log the same move twice. A queued write shows a message and
does **not** reload the screen; the reload would fail on the same dead network
and swap the record for an error box.

**Distribution:** self-hosted APK (`mobile/release.sh`), **no Play Store** —
settled decision. Updates are **mandatory**: when `/api/app/version/` reports a
newer versionCode, `ShellActivity` shows a blocking dialog. The check is only
reached with a working connection, so offline devices are not locked out.
Release builds run R8; anything reached by reflection (manifest-only
components, Capacitor plugins, the JS bridge) needs a keep rule in
`proguard-rules.pro` or it compiles fine and crashes on the device — smoke-test
the **release** APK. Field crashes post to `/api/app/crash/` and land in the
Audit Log.

`android/` at the repo root is the **retired** legacy TWA, kept only because
the signing keystore lives there. Do not build from it.

---

## 8. Testing

`.venv/bin/python manage.py test clients` — ~50 test modules in `clients/test/`,
each pinning one module's rules. The notable ones are not unit tests of
functions but **guards against known regressions**:

- `test_product_subproducts` — main-products-only outside sale entry
- `test_followup_tasks` — follow-ups really are tasks, and clean up
- `test_agenda_pipeline` — team scoping of every agenda source
- `test_phone_normalisation` — the float-phone class of bug
- `test_theme_coverage` — table wrappers, aria-labels, shared CSS
- `test_android_responsive` — the 17 Compose lint rules
- `test_financial_plan` — cell-by-cell against the workbook

A warning from this repo's own history: **presence-only assertions let visible
bugs ship.** Tests that assert something exists, without asserting the wrong
thing is absent, passed twice while the page was visibly broken. Tests are not
a visual check — say so when reporting.

---

## 9. Running and deploying

**Local:** `.venv/bin/python manage.py runserver` (Python 3.12). Env from
`.env`, documented in `.env.example` — every var the code reads must be listed
there.

**Branches:** work on `dev`; `main` is deploy-only, fast-forwarded from dev.

**Deploy:**

1. `dev` green: `manage.py test clients` + `manage.py check` +
   `makemigrations --check`.
2. `git push origin dev:main`
3. On the droplet: `cd ~/silicon-crm && git pull && venv/bin/pip install -r
   requirements.txt && venv/bin/python manage.py migrate && venv/bin/python
   manage.py collectstatic --noinput`
   - **`collectstatic` is not optional.** WhiteNoise serves from `staticfiles/`
     via `CompressedManifestStaticFilesStorage`, so a CSS/JS change that is not
     collected never reaches a browser: the page ships with the old stylesheet
     and looks broken in ways no test catches.
   - Reload gunicorn with `kill -HUP <master-pid>`. There is **no passwordless
     sudo** on the droplet and the master runs as `ubuntu`. Find it with
     `pgrep -af "[g]unicorn"` and HUP the **parent** — note the bracket, or the
     pattern matches your own shell.
   - Before a migration that rewrites or drops data, run `scripts/backup_db.sh`
     (dumps and mirrors to Drive in seconds).
4. If `CRONJOBS` changed: `manage.py crontab remove && manage.py crontab add`.

**Android release:** set `JAVA_HOME` to Android Studio's JBR, bump
`versionCode` + `versionName` in `mobile/android/app/build.gradle`, run
`mobile/release.sh`, update the status table in `mobile/NATIVE_MIGRATION.md`.
Deploy the backend **before** the APK whenever a screen calls a new endpoint —
updates are mandatory, so a release pushes the whole fleet onto it at once.

---

## 10. Where to start when you pick up a ticket

1. Find the domain in the table in §2 and open its `models/`, `views/` and
   `services/` module — in that order.
2. Read the docstring at the top of the service. Most of them explain the
   decision, not just the code.
3. Grep for the rule's test in `clients/test/`. If it exists, the rule is
   load-bearing and the test says exactly what may not change.
4. Change the service, not the view; change `save()`, not the form; add the
   test in the same commit.
