# Silicon CRM — Kadlag Investment back office

Django 5.2 + Postgres web app with a native Android app (Kotlin/Compose, Capacitor shell).
Production: **bo.kadlaginvestment.com** (DigitalOcean droplet `ubuntu@139.59.28.8`, `~/silicon-crm`).

## Project map (a place for everything)

| Path | Purpose |
|---|---|
| `clients/` | The single Django app: models, views, services, APIs |
| `clients/models/` | Models split by domain (`hr.py`, `sales.py`, `leads.py`, `insurance.py`, …), all re-exported in `__init__.py` — always import via `clients.models` |
| `clients/views/` | One module per domain (`tasks.py`, `sales.py`, `messaging.py`, `app_*.py` = mobile JSON APIs) |
| `clients/urls/` | URL patterns split by domain, assembled in `__init__.py` under the single `clients` namespace |
| `clients/services/` | Business logic shared by web + app views (`sales.py`, `targets.py`, `tasks.py`, `calendar_feed.py`, `push.py`, `rta_feed.py`, `google_drive.py`) |
| `clients/management/commands/` | Cron jobs — every command here must be in `CRONJOBS` (config/settings.py) or documented as a manual tool |
| `clients/test/` | All tests (`manage.py test clients`) |
| `config/settings.py` | Settings incl. `CRONJOBS`; env read from `.env` (template: `.env.example`) |
| `templates/` | Web UI (server-rendered, Bootstrap) |
| `templates/_shell/` | Shared record-shell partials (`crumb.html`, `kpi_strip.html`) — rendered by `base.html` for any view supplying `crumbs` / `kpis`; page templates never include them directly |
| `static/css/ki-record.css` | The record shell: breadcrumb, KPI strip, record header, monogram, tabs, field grid, data table, print + form rules. Shared CSS lives here, never duplicated per template |
| `mobile/` | Android app. Native screens in `android/app/src/main/java/bo/kadlaginvestment/crm/`; plan + status table in `NATIVE_MIGRATION.md` |
| `android/` | **Legacy TWA — retired.** Kept only because the signing keystore lives here (`upload-keystore.jks`, gitignored). Do not build from it |
| `scripts/` | Server ops: `backup_db.sh`, `server_cleanup.sh`, `seed_from_crmdb.py` |

Local dev: `.venv/bin/python manage.py runserver` (Python 3.12 venv at `.venv/`).

## Conventions

- Work on `dev`; `main` is deploy-only (fast-forwarded from dev).
- New backend logic goes in `clients/services/`, thin views call it. Mobile endpoints live under `/clients/api/app/…` (session-cookie auth, same as web).
- External integrations (Firebase, Drive) read credentials from env and **must silently no-op when unconfigured** — the app must always run without secrets (see `services/push.py` as the pattern).
- Every schema change: `makemigrations` in the same commit as the model change. `makemigrations --check` must stay clean.
- Every feature commit includes/updates tests. Full suite green before push: `.venv/bin/python manage.py test clients`.

## Main products vs sub-products

- A sub-product (`Product.parent`, e.g. "Term Plan" under "Life Insurance")
  exists so a **sale or a renewal** can name the exact plan sold. That is the
  whole of its job. **Every other picker, filter and report in the system
  deals in main products only**, with a sub-product's business folded into
  its category.
- The rule lives in one place — `ProductQuerySet` in `models/catalog.py`:
  `Product.objects.selectable().main().in_display_order()`. Only the sale
  entry forms (`_sale_products` / `_subproduct_choices`, `app_sale_meta`) and
  the renewal forms (`RenewalForm`, `EditRenewalForm`, `app_renewal_meta`)
  may drop `.main()`. Product Management is the third exception: it is the
  catalog editor, so it shows the tree.
- **Restricting a picker is only half the change — the matching behind it has
  to roll up, or a category selection silently covers nothing.** Both halves
  are done: incentive rules already matched `product_ref__parent_id`
  (`rule_sale_q`); campaigns now do (`Sale._active_campaign_product`, exact
  sub-product still beats its parent for legacy rows); the All Clients
  product filters fold children into the parent
  (`_client_product_totals_map`).
- The margin reports print one row per main product, but **each plan's
  revenue is still valued at its own rate** before blending — a rolled-up row
  must never reprice a sub-product at its parent's margin. Health is the
  exception on purpose: its slabs are a band on the *category's* monthly
  volume, so they resolve at the category. Per-plan rates are read on Product
  Management, where they are set.
- `clients.test.test_product_subproducts` pins all of this
  (`MainProductsOnlyOutsideSaleEntryTests`).

## Insurance sales & renewals

- A sale is booked when the company APPROVES the policy, so `Sale.date` (sale
  date) is NOT the policy start. Health/Life insurance sales carry a separate
  mandatory `Sale.policy_date` — the commencement date read off the policy
  document. The three sale forms require it for those products and hide it for
  the rest (JS toggle + server-side `clean()`).
- Annual renewal reminders are measured from `Sale.renewal_basis`
  (`policy_date`, falling back to `date` only for legacy rows). The calendar
  feed's `insurance_renewal` source expands each approved Health/Life sale's
  yearly `policy_anniversary` onto the selling employee's calendar. Never key
  insurance renewals off the sale date.

## Insurance Tracker sync

- Health/Life **sales** require a `policy_number` (and `policy_date`); it's
  the number that links a sale to its tracker policy and to future renewals.
  **Enforced in three places** — the web forms' `clean()`, the app API
  (`app_sale_create`), and `Sale.save()`, which upper-cases and strips the
  number so "ins123 " and "INS123" can't become two policies. The mobile Add
  Sale screen has both fields; it used to have neither, which silently dated
  every renewal reminder off the approval date.
- `InsurancePolicy.save()` upper-cases and strips `policy_number`, same as
  `Sale.save()` — the number arrives from the web form, the app API,
  `insurance_sync` and `link_renewal_to_policy`, and only the model covers all
  four. `link_renewal_to_policy` used to merely strip, which the Android
  screens' client-side uppercasing was accidentally masking.
- An approved Health/Life **sale** auto-creates one `InsurancePolicy`
  (idempotent, linked via `InsurancePolicy.source_sale`), start date = the
  sale's `policy_date`. Un-approving/rejecting removes it unless someone has
  filled in a real policy number/insurer, in which case it's just detached.
  Lives in `services/insurance_sync.py`, hooked from `services/sales.py`.
- The add-renewal page shows the client's existing Health/Life policies (via
  `client_policies_json`) as tick options once a client is picked. Picking one
  links the renewal to it (`Renewal.policy`); picking "New policy" reveals a
  policy-number field and creates+links a fresh policy — how the old book,
  sold before the tracker existed, gets captured. `link_renewal_to_policy`
  owns this, and **both** the web view and `app_renewal_create` call it (the
  app used to skip it, leaving every phone-entered renewal an orphan row).
  A policy's detail page lists its full renewal history.
- `Renewal.insurance_kind` / `Renewal.kind_q()` classify by `product_ref`
  first (product_type wasn't persisted on historical rows — the form derives
  it but it isn't a model-form field, so the view now copies it across).
- After adding an insurance sale/renewal, the success message links to the
  client's Drive folder to upload the policy (created on first click) — a
  link, never a forced redirect, so daily bulk entry isn't interrupted.

## Multiyear health policies + EMI

- A Health sale can be **multiyear** (`Sale.policy_years` 2/3): the entered
  `amount` is the full multi-year premium. Only the **first-year slice**
  (`amount / policy_years`, via `Sale.annual_premium`) is Fresh business now —
  the margin report counts that slice, not the full amount. Years 2..N are
  recognized as **renewals on each anniversary** at the health renewal rate
  (12.75%), derived live in `_month_renewal_breakdown` (no records to maintain).
  Multiyear is **Health-only** (forced off for other products in the sale form).
- Multiyear policies are often on **EMI** (`Sale.emi_months` 5/8/11), due the
  **5th**, starting the month AFTER the sale. The `emi_reminders` cron (in
  CRONJOBS, 8 AM on the 3rd–5th) pushes the client's **mapped employee**
  (`Client.mapped_to`, falling back to the seller) and auto-assigns one
  high-priority "call client for EMI" **task** per policy per month (deduped via
  `Task.assign_group = "emi:<sale>:<YYYY-MM>"`), due the 5th. A missed EMI can
  cancel the policy, so the reminder is the point.
- The app's Add Sale carries the multiyear term + EMI pickers too (Health only,
  same rules as the web form).
- **Renewal reminders** (`renewal_reminders` cron, daily 8:45 AM): single-year
  insurance policies renew manually, so at **30/15/5 days** before the next
  renewal the system pushes the client's **mapped employee** (fallback seller)
  and auto-assigns a "call to renew" **task** due on the renewal date (deduped by
  `assign_group = "renewal:<sale>:<date>"`). The task shows on the home calendar;
  push + the `insurance_renewal` feed marker cover the rest. `Sale.coverage_end()`
  / `Sale.next_renewal_date()` account for a **multiyear** term already paid — a
  multiyear policy stays silent until its term ends (policy_date + years). The
  `insurance_renewal` calendar feed now keys on the **mapped** employee and skips
  the paid multiyear years too.

## Employee incentive (points = rupees)

- `services/incentives.py` owns the maths — `quote()` is the single
  implementation, called by `Sale.compute_points()` on every save **and** by the
  Incentive Structure page's what-if calculator, so the page can never quote a
  number the sale wouldn't pay.
- A rule's slabs are read one of two ways (`IncentiveRule.slab_mode`):
  **bonus** = rupees *earned to date* once the period's cumulative volume
  crosses the rung, paid on top of the flat unit rate and only as the delta
  against what the period already released (Life: 1.75% base + Apr–Mar ladder);
  **rate** = a percent resolved from the period's volume and applied to the sale
  instead of the unit rate (Health: the seller's own monthly Fresh volume).
  `IncentiveRule.slab_period` is the window — calendar month or Apr–Mar FY.
- The delta is measured against `Sale.bonus_points`, **not** `points` — points
  now mixes base and bonus, and summing it would starve the ladder.
- Health **Port earns nothing** (settled 2026-07-31; `port_percent` deleted,
  migration 0117). It sits outside the Fresh ladder and never pushes Fresh into
  a higher band. **The `policy_type == "port"` early return in `quote()` must
  stay** — without it a Port sale falls through to the Fresh band and is paid
  the month's rate, which is the opposite of the rule. A **multiyear** premium is
  credited one year at a time (`Sale.creditable_amount()`), matching how the
  margin report already recognises it.
- Changing a rule never rewrites sales already saved — points are stored at save
  time. `recompute_sibling_sales` re-runs the rule's whole *period* (not just
  the month) after a status change, so rate bands re-rate when volume crosses.
- **Multiyear health pays one year at a time.** Year 1 lands with the sale;
  years 2..N land on the policy anniversary as an `IncentiveAccrual` row created
  by the `multiyear_incentive_accruals` cron (daily 6:10). The premium was paid
  up front, so there is no renewal to collect and nothing to enter — only the
  points arrive. Idempotent via `unique_together(sale, year_index)`, so a missed
  day catches up and a re-run is a no-op. Each year is priced by the band the
  employee's month has reached when it lands.
- **No monthly deduction exists.** The Life prize is decided by the financial
  year's volume and nothing else: whatever rung Apr–Mar reaches is what is paid,
  however the months fall. The old 3L/6L/9L/12L/15L monthly grid used to be
  netted off automatically (`deduct_legacy_monthly` / `legacy_deduct_from`,
  `LEGACY_MONTHLY_SLAB`) — **all of it was deleted on 2026-07-31, migration
  0116**, because a month over ₹3L cancelled the prize it had just earned. Don't
  reintroduce a per-month adjustment to a per-year ladder.
- **The FY prize is never released by a sale, and never month by month.** It is
  handed over as cash once the year has closed and recorded as a `BonusPayout` —
  that record is the *only* thing that marks it released. `quote()` therefore
  returns no bonus for an FY-period ladder, so `Sale.bonus_points` stays 0 and a
  ₹1L sale can never show a ₹3,000 release just because the year crossed a rung
  on it. A rung reached in June is a standing, not a bill: the year can still
  climb, so `life_bonus_status` carries `payable_from` (1 April after the FY),
  `fy_closed` and `due_now` — nothing is due while the year is open. Health's
  monthly rate bands are unaffected; they pay as they go.
- `recompute_sibling_sales` re-saves the originating sale too (guarded against a
  deleted row) — a sale is priced before it exists in the table, so its own
  amount is missing from its period until a second pass.
- **Points now come from two places** — `Sale.points` and `IncentiveAccrual`.
  Any total shown to an employee as "what I earned" must add
  `incentives.accrued_points(...)`: employee + admin dashboards, team detail,
  the app API stats and the calculator all do. Sales *reports* stay sales-only
  on purpose — an accrual is earned points, not a sale, and must never be
  counted as business volume.
- Admin screens: `/clients/incentives/payout/` (the month's bill, split
  base / ladder bonus / multiyear, plus dated bonus releases) and
  `/clients/incentives/life-bonus/` (FY ladder position per employee, month by
  month, where hand-made payouts get recorded).
- `/clients/incentives/calculator/` is the **employee-facing** page (Sales nav,
  no `manage_incentives` needed): plain-language structure, worked examples in
  points, and a what-if. It must never print firm margin/commission figures or
  raw percentages — `services.incentives.explain()` renders everything in
  points. `/clients/incentives/` is the admin structure page.

## Financial planner

- `services/financial_plan.py` is a port of
  `docs/planner_fin/Financial_Plan_Template.xlsx` — inputs, insurance, goals,
  retirement, allocation, the year-by-year projection and the eight standalone
  calculators. `compute()` is the only implementation; the page and the PDF
  both call it, so **nothing recalculates in the browser**. The old module did,
  and its PDF endpoint rendered whatever numbers the page posted.
- The workbook ships cached formula results, and
  `test/test_financial_plan.py` asserts against them cell by cell. Re-run it
  after touching any formula; if a number drifts from the sheet, the sheet
  wins. Excel's `FV/PV/PMT/NPER/RATE` are reimplemented at the top of the
  service with Excel's sign convention (money leaving you is negative).
- Order matters and is not obvious: **insurance premiums are computed before
  the investible surplus**, because the surplus is post-tax income less living
  expenses less the protection bill, and every SIP is sized inside it.
- Section order follows the sheet: protection → emergency fund → goals →
  retirement → allocation. Each row carries the workbook's own "Formula /
  Logic Used" note; that column is half the value of the sheet, so it renders
  with the numbers rather than being dropped.
- `INPUT_GROUPS` / `CALC_GROUPS` drive the form, the parser and the defaults
  from one list — a new input cannot be added to only two of the three.
  Percent fields are typed as percents and stored as fractions.
- Lookup tables (income multiples, term/health premium per lakh, glide path)
  are module constants, not admin-editable — they are indicative retail
  benchmarks and must be replaced with real quotes per client.
- The PDF prints "Rs", not ₹: the base-14 PDF fonts have no rupee glyph and it
  renders as a black box. Don't "fix" it back without bundling a font.

## Claim workflow

- Claims are raised and worked **in-app** now (not just admin): "Raise Claim"
  in the Insurance nav and on each policy. `services/claims.py` owns the
  workflow; every change logs a `ClaimActivity` (the timeline + notes share
  that model).
- Stages: Intimated → File Received → Submitted → Settled / Rejected.
  `advance_stage` stamps the matching date once and logs it. The detail page
  shows a stepper + an update form.
- `ClaimDocument` stores files in the **client's** Drive folder (reuses
  `get_or_create_client_folder`), served through a proxy like task
  attachments.
- `ClaimReminder` = a dated follow-up. It surfaces on the common calendar
  (`claim_reminder` feed source) and fires a **mobile push** at its time via
  the existing `send_followup_reminders` cron (a plain Notification mirrors to
  FCM). No new cron — it rides the every-minute one already in CRONJOBS. Every
  stage update / note can attach a follow-up in the same submit.

## Lead pipeline (SPANCO)

- Leads move through six stages — **Suspect → Prospect → Approach →
  Negotiation → Conclusion → Order** (`Lead.STAGE_CHOICES`, in pipeline order
  in `Lead.STAGE_SEQUENCE`; `STAGE_HELP` is the one-line meaning of each and
  is rendered on the stepper, the board and the app, so the method is taught
  by the screen).
- **The stage is recorded, never derived.** The old pipeline computed
  pending/half-sold/processed from three hard-coded product rows, so a lead's
  position was a side effect of what had been sold. Every move now goes
  through `services/leads.set_stage()`, which writes a `LeadStageEvent`
  (from → to, note, who) and stamps `stage_changed_at`. Never assign
  `lead.stage` anywhere else — the funnel is computed from these values.
- **Lost keeps the stage it died at** (`mark_lost` + `lost_reason`); that is
  what makes a weak step visible. `is_discarded` is the old column name, kept
  so no historical row moved; the UI calls it Lost.
- `funnel()` counts a lead as having *reached* every stage at or below where
  it stands (lost ones included — they did get that far), so stage-to-stage
  conversion is honest without replaying the event log.
- **Products are per lead, not a fixed trio.** `LeadInterest` points at the
  `Product` catalog: a lead that only wants a term plan carries one row.
  Nothing is seeded — neither the web form nor `app_lead_create` invents
  requirements for a lead nobody has qualified. `product` is nullable *only*
  so pre-SPANCO rows whose product left the catalog survived migration 0118
  with their label in `note`.
- Conversion is allowed at **Order** only, and copies no cover/SIP figures:
  `signals.update_client_status` recomputes those from approved sales, so a
  lead's indicative numbers would just be overwritten.
- Migrations **0118** (schema) + **0119** (data). 0119 puts **every existing
  lead at Suspect** — the owner's call on 2026-08-13: the old
  pending/half_sold/processed value was derived from what had been sold, so
  it is not a SPANCO position worth carrying. Nothing is lost even so: where
  a lead stood is written into its first stage event ("Previously: Half
  Sold"), lost leads stay lost, converted ones keep their client link, and
  every product row that carried real data became a `LeadInterest` whose note
  spells out the old target / achieved / status. The blank Health-Life-Wealth
  rows seeded onto every lead were dropped — they are the format being
  replaced and carried nothing. **Keep 0119 separate from 0118**: a deferred
  CREATE INDEX cannot follow a bulk row update in one Postgres transaction.
- `seed_demo_crm` fills the pipeline for testing: 17 leads across all six
  stages, walked through the stages they passed (so the funnel, the stage
  history and the "stalled 14+ days" panel all have real content), two lost
  with reasons, per-lead requirements off the main product catalog. Demo
  leads use ids ≥ 990000; `--undo` takes them and everything hanging off them.
- Screens: `/clients/leads/` (list, stage KPI tiles), `/clients/leads/board/`
  (six SPANCO columns, read-only — moves happen on the detail page so they
  all go through one logged path) and `/clients/leads/pipeline/` (funnel,
  per-employee win rates, leads stalled 14+ days, what the pipeline wants).
- The stepper CSS is `.ki-steps` / `.ki-step` in `ki-record.css`, shared with
  the claim workflow.

## Call follow-ups (the app's Calls tab)

- One number = one pending reminder. A new follow-up for a number retires the
  older pending ones as `superseded` — **kept, not deleted**: `attempts` counts
  how often that number has been chased, and the card shows it.
- A **connected outgoing call closes the follow-up it answers**
  (`_close_called_followups` in `views/calls.py`, run from `calls_sync`), with
  outcome "spoke". Only calls placed *after* the row was created count — the
  post-call popup creates the next follow-up seconds later and that one must
  survive — and only connected ones: an unanswered dial is exactly when the
  reminder still matters.
- "Done" carries an **outcome** (`CallFollowUp.OUTCOME_CHOICES`). Swiping a card
  right closes it without asking; the Done button asks. Swipe left dismisses.
- `/api/app/followups/` serves pending + `done_today` + the overdue count that
  badges the Calls tab (also on `/api/app/me/` so the badge is right at launch).
  `push-overdue/` reschedules every overdue row at once.
- **Lead follow-ups share the screen.** `LeadFollowUp` rows come through the
  same endpoint marked `kind: "lead"`, and `app_followup_action` routes by that
  field — staff had two call lists, one of them web-only. Row ids collide
  between the two models, so anything keyed by id must key on `kind + id`.
  LeadFollowUp has no dismissed state, so Dismiss closes it as done.
- **Outcomes are reported by `services/calls.outcome_breakdown()`** — one
  implementation feeding both the web Call Analytics page and
  `app_call_analytics`, the way the call counts should have been from the start.
- The post-call popup reads `/api/calls/context/` for a one-line history of the
  number (client, attempt count, prior calls, pending note) *after* it is on
  screen, so it costs no latency, and `/api/calls/close/` backs
  "Not interested — stop chasing". The ringing AlarmRingActivity asks for an
  outcome too; it is the most-used Done path there is.

## UI conventions (record shell)

- Every module screen leads with a breadcrumb and, for list screens, a KPI
  strip. Both render from `base.html` — a view just supplies `crumbs` /
  `kpis`, no template edit needed. `clients.context_processors.breadcrumbs`
  derives crumbs from the URL name when a view doesn't set them.
- KPI tiles: `{"label", "value", "color", optional "url"/"active"/"sub"}`.
  Build them from data the view already computed; don't add queries for a tile.
- Detail pages use the record header (`.ki-rec` + `.ki-mono` monogram +
  `.ki-tag` pills + `.ki-rec-summary`). Monogram initials/colour come from the
  `monogram` / `monocolor` filters — stable per name, nothing stored.
- Contact details are masked in lists via `mask_phone` / `mask_email` /
  `mask_pan`. Display-only; the DB and tap-to-call links keep real values.
- Shared CSS goes in `ki-record.css`. If two templates need the same rule,
  promote it rather than copying.
- Tables must sit inside `.ki-table-wrap` or `.ki-table-scroll`, or they push
  the page sideways on a phone. Icon-only buttons need `aria-label`.
  `manage.py test clients.test.test_theme_coverage` enforces all of this.
- Django's `{# #}` comments **cannot span lines** — a multi-line one renders
  as visible page text. Use `{% comment %}` for anything longer than one line.

## People / employee records

- `Employee` owns names, DOB, joining date, position, domain, reports_to,
  emergency contact and skills — not just a login. Derived: `tenure_months`,
  `total_experience_months`, `tenure_display` ("3y 6m"), `profile_completeness`.
- Employees fill their own details at `/clients/me/profile/`; that form must
  never expose salary, role, employee number, joining date or position —
  self-service is not a route to a pay rise.
- `EmployeeMilestone` + `services/people.py` turn dates into occasions.
  `employee_milestones` (daily 08:30) generates birthdays / anniversaries /
  long-service years and nudges admins about what lands today or tomorrow and
  anything that slipped past unmarked. Admins are never asked to celebrate
  themselves.
- Recognition is the point: `people.celebrate()` notifies the employee.

## Android UI rules

- Sizes go through `rsp(n)` / `rdp(n)` (`ui/Responsive.kt`), never raw `n.sp`
  or a fixed height on anything interactive. They scale against a 392dp
  baseline, clamped — type 0.92–1.15x, spacing 0.85–1.30x.
- This is separate from the user's font-scale accessibility setting, which
  Compose already applies to `.sp`. Don't fight it: use `heightIn(min =)` so
  labels can grow, never `height()`.
- Three or more buttons in a row → `ActionRow` (wraps), not `Row` (squeezes).
- **Shared building blocks — use these, don't re-roll them:**
  `ui/ScreenHeader.kt` (back arrow + title + actions), `ui/Fields.kt`
  (`PickerField`, `DateField`, `pickDate/pickDateTime/pickTime`, `moneyInput`),
  `ui/Refresh.kt` (`RefreshableBox`, `ErrorStrip`), `ui/Load.kt`
  (`rememberLoader` — cache-first fetch), `ui/Common.kt` (`AppMessage`,
  `EmptyState`, `StatusPill`, `rupees`), `ui/Session.kt` (role + unread badge).
- **Never** a bare glyph as a button (`Text("↻", clickable)`): no ripple, no
  role, ~28dp target, and TalkBack reads the character. Use `IconButton` with
  a `contentDescription`.
- Dates are picked, never typed. Times come from `pickTime`, which honours the
  device's 12/24h setting.
- **Never rewrite a `TextField`'s text inside `onValueChange`** (`.uppercase()`,
  `.trim()`, a character filter). Handing the String overload a value different
  from the one it holds collapses the selection and throws the cursor to the end,
  so a typo mid-word cannot be fixed — this is what "the keyboard doesn't work"
  reports actually are. Use `KeyboardOptions` (`capitalization`, `keyboardType`)
  to shape input, and normalise on the server. `moneyInput()` is safe only
  because those fields set `KeyboardType.Decimal`, so it never rewrites.
- Every `ApiClient.post` call site must handle `Result.Error`. Writes that are
  safe to replay pass `offlineQueue = context` so they survive no signal
  (`net/Outbox.kt`); creating a sale or client deliberately does not.
- Screens read data through `rememberLoader` (last-good response from
  `net/Cache.kt` paints first) and refresh by pull, not by a glyph.
- Navigation/tab state is `rememberSaveable`, or rotation dumps the user home.
- Notifications: monochrome `ic_stat_ki` only, on the right channel
  (`ki_followup_alarms` / `ki_task_alarms` / `ki_daily_digest`).
- `manage.py test clients.test.test_android_responsive` lints all of this
  (17 rules) and pins the scale maths.

## Deploy checklist (web)

1. `dev` green: `manage.py test clients` + `manage.py check` + `makemigrations --check`.
2. `git push origin dev:main`
3. On server: `cd ~/silicon-crm && git pull && venv/bin/pip install -r requirements.txt && venv/bin/python manage.py migrate && sudo systemctl restart gunicorn`
4. **If CRONJOBS changed:** `venv/bin/python manage.py crontab remove && venv/bin/python manage.py crontab add`

## Release checklist (Android)

1. `export JAVA_HOME="/Applications/Android Studio.app/Contents/jbr/Contents/Home"`
2. Bump `versionCode` + `versionName` in `mobile/android/app/build.gradle`.
3. Build + publish via `mobile/release.sh` (self-hosted updater — **no Play Store**, settled decision).
4. Update the status table in `mobile/NATIVE_MIGRATION.md` if a screen shipped.

Updates are **mandatory**: when `/api/app/version/` reports a newer
versionCode, `ShellActivity` shows a blocking dialog (no "Later", no
back/outside dismiss) so the app can't be used until the APK is installed.
Publishing a release therefore forces the whole fleet onto it — the version
check is only reached with a working connection, so offline devices aren't
locked out.

Release builds run **R8** (`minifyEnabled` + `shrinkResources`). Anything the
framework reaches by reflection — activities/receivers/services named only in
the manifest, Capacitor plugins, the JS bridge — needs a keep rule in
`app/proguard-rules.pro`, or it compiles fine and crashes on the device. Smoke-
test the release APK, not just the debug one.

Field crashes post themselves to `/api/app/crash/` on the next launch and land
in the Audit Log as `app.crash` (`CrashReporter.kt`) — that is the only crash
signal there is, since the app is self-hosted with no Play Console.

## Housekeeping standard (5S — run this audit monthly)

- [ ] `manage.py test clients` all green; `manage.py check` + `makemigrations --check` clean.
- [ ] Every management command is either in `CRONJOBS` or listed under Red-tag/manual tools below — delete anything that is neither.
- [ ] `git branch -a` — delete merged/stale branches; `git worktree list` — remove dead worktrees.
- [ ] No junk in tree: `find . -name .DS_Store -delete`, clear `logs/*.log`, no stray venvs/`__pycache__` at root.
- [ ] `requirements.txt` matches actual imports (no unused pins).
- [ ] `.env.example` documents every env var the code reads.
- [ ] Dependencies: `pip list --outdated` — patch security-relevant ones (Django especially).

### Red-tag area (suspect items — decide, don't ignore)

- (none currently — `monthly_snapshot` + `MonthlyIncentive` deleted 2026-07-16;
  the admin incentive report always computes live from `Sale` now)
- Manual tools (intentionally not in CRONJOBS): `prod_readiness_check`,
  `seed_demo_tasks_links`, `seed_demo_crm`, `seed_life_rates`, `seed_health_slabs`,
  `seed_incentive_structure`, `clear_unreleased_ladder_bonus`.
  `clear_unreleased_ladder_bonus` is the one-off that took the FY prize back off
  the sales carrying it (2026-08-05). Dry run by default, `--apply` writes,
  idempotent. It strips the prize only and never re-saves a row — save() would
  reprice a pre-restructure sale under today's structure and invent a base it was
  never booked with.
  `seed_incentive_structure` sets the *employee* incentive: Life = 1.75% base +
  the Apr–Mar bonus ladder, Health = the per-employee monthly Fresh rate bands
  (1.50%→3.50%, a flat tenth of the firm's own margin grid); Port earns nothing.
  Idempotent; re-run after changing either ladder.
  `seed_health_slabs` sets the Health Insurance Fresh margin slabs (volume-band,
  15%→35%) plus flat 15% Port/renewal; idempotent, re-run if the structure
  changes.
  `seed_life_rates` seeds/refreshes life-insurance plans + their per-PPT
  Advisor/MDRT commission rates from `docs/insurance/life_rates.json` (parsed
  from the insurer's Agency FYC-RYC chart). Run once after deploy, and again
  when the insurer publishes a new chart version. New plans land INACTIVE — an
  admin ticks the ones sold on Product Management.
  `seed_demo_crm` seeds Households/Insurance/Claims/Meetings/Leads for testing;
  it refuses to run when `DEBUG` is off unless `--force`, and `--undo`
  removes exactly what it created (demo client ids ≥ 990000, `DEMO-` codes).
