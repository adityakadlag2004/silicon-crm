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
| `clients/services/` | Business logic shared by web + app views (`sales.py`, `targets.py`, `tasks.py`, `calendar_feed.py`, `employee_performance.py`, `push.py`, `google_drive.py`) |
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

## What a client holds (the Portfolio cards)

- `signals.update_client_status` derives `sip_amount`, `lumsum_investment`,
  `life_cover`, `health_cover`, `motor_insured_value`, `pms_amount` and their
  `*_status` flags from the client's **approved** sales. The client profile's
  Portfolio cards read only these columns, so **anything this function forgets
  is invisible on the profile however many sales were booked**.
- **`lumsum_investment` is the exception: it is curated, never derived.** It is
  typed on the client add/edit form and carries figures from the original data
  import — 47 clients hold a real lumpsum with no lumpsum sale behind it, and
  the amounts are valuations (₹10,76,608.63), not round typed numbers. Deriving
  it looked like an obvious missing line; a dry run showed it would erase
  **₹34.3 lakh across 22 clients**, 7 of them to zero, on the next save of any
  of their sales. Don't add it back — the profile shows lumpsum from the sales
  book already, computed live by `services/holdings` which never reads this
  column. The lesson generalises: **run the sweep's dry run before assuming a
  denormalised column is safe to derive.**
- It did forget one thing until 2026-09-02:
  - **Sub-product sales did not roll up.** A sale names the exact plan sold
    ("PR Life Pro", parent `LIFE_INS`), and matching on `product_ref__code`
    alone counted none of them, so ₹2.75cr of life cover read as no life
    insurance. `_line_q` now matches code **or `product_ref__parent__code`**
    or the legacy name — the same roll-up the incentive rules and campaigns
    already used. This is the rule under "Main products vs sub-products";
    this signal was the one place still ignoring it.
- **An insurance flag is not a cover figure.** `cover_amount` is blank on a
  small tail of sales (8% health / 4% life), and keying `health_status` off
  cover alone told those clients they held no policy. The flag is now "holds an
  approved sale of that line, or has cover". SIP/PMS keep `amount > 0` — for an
  investment the amount *is* the holding, so a zero is a real zero.
- **These columns are derived from sales alone.** The CAMS/KFintech RTA feed
  used to overwrite `sip_amount` with the client's *active* registrations, so
  194 clients whose registrations had all ceased read ₹0 whatever they had
  bought. The whole Mutual Funds / RTA module was removed on 2026-09-02 (see
  "The Mutual Funds module is gone" below), so there is now one writer.
- **The signal only fires on a Sale save**, so changing what it derives leaves
  every existing row stale. `manage.py recompute_client_holdings` sweeps the
  book (dry run by default, `--apply` writes, idempotent). Its dry run does the
  real work inside a rolled-back transaction rather than writing and restoring,
  so asking "what would this change?" never writes to production.
- `clients.test.test_client_holdings` pins all of it.

## Health & Life is its own tab, and it is a stored record

- The client profile's **Health & Life** tab (right of Overview) renders
  `InsurancePolicy` rows — policy number, insurer, plan, cover, premium, cover
  period, nominee, status, and every renewal collected against each one.
- **It is a stored record, not a calculation.** A policy is written once, when
  its sale is approved (`sync_policy_from_sale`) or its first renewal is logged
  (`link_renewal_to_policy`), and updated in place thereafter. Opening the tab
  costs two queries — the policy list plus a `Prefetch` of its renewals —
  however long the client's history is. Never rebuild this tab by scanning
  Sale/Renewal: that is what the Portfolio tab does, and it is the thing the
  owner explicitly did not want here.
- **`_sale_insurance_type` rolls sub-products up to the parent.** A life sale
  names the exact plan ("PR Life Pro", parent LIFE_INS), and matching the code
  alone meant those sales created no policy at all — 5 on production, and the
  largest-cover life policies in the book. The sub-product's name is kept as
  the policy's `plan_name`.
- `sync_policy_from_sale` runs only from the sale approval path, so sales
  approved before it existed have no policy: 84 of 94 were in that state.
  `manage.py backfill_insurance_policies` fixes that (dry run by default,
  `--apply` writes, idempotent via `source_sale`). Purely additive — it only
  creates missing rows, unlike `recompute_client_holdings`.

## The client's Portfolio is the sales book

- `services/holdings.portfolio(client)` groups a client's **approved sales**
  and the **renewals** collected against them into one line per **main
  product**, and hands back the individual records behind each line — so the
  profile prints one row per policy, with its number, plan, cover, premium and
  who booked it. The six hard-coded Yes/No cards it replaced could say
  "Health: Yes, cover ₹5,00,000" but never *which* policy, how many, or what
  the number was.
- Sub-products fold into the parent line (`_main_product`), so a sale of
  "PR Life Pro" shows under **Life Insurance** with its plan name on the row.
- **A renewal adds to `collected`, never to `cover`** — it is a premium
  collected against a policy, not a new holding. A renewal for a line with no
  sale still opens its own line: that is the old book, and hiding it would
  leave exactly those profiles blank.
- KPI tiles are per line (SIP / Lumpsum / Health Cover / Life Cover), derived
  the same way. The MF Value tile is gone — it was feed data in a strip that
  now describes the sales book.
- `clients.test.test_client_holdings` pins the lines, the roll-up, the policy
  numbers and the renewal handling.

## Insurance Tracker + the client's book

- **The tracker is read one product line at a time.** Health and Life are
  different books with different renewal rhythms, so the type tabs are the
  primary control and *everything* below them — the KPI strip included — is
  scoped to the selected type (`book` in `policy_list`). A Health tab
  reporting Life's cover is worse than no number. The tile URLs carry
  `type=` so a tile click never silently widens the book.
- The `type` filter existed in the view for months with no UI; the page was
  one undivided list, which answered no question anybody asks.
- **Ordering is a work queue, not an archive**: live policies first
  (`_live` Case/When), then soonest renewal. Sorting on `end_date` alone
  floats every lapsed policy to the top, since their dates are all in the
  past — caught by rendering the page, not by the assertions.
- Policy numbers stay **masked in the list** (`mask_pan`, pinned by
  `test_insurance_modules`) and print in full on the detail page and on the
  client's own profile — masking is a list rule, not a record rule.
- **The client profile names the policies.** The Portfolio cards are the
  derived health/life *summary* recomputed from approved sales by
  `signals.update_client_status`; they never named a policy, so the profile
  could not answer "which policy, and when does it renew" — the first thing
  anyone opening a client asks. `policies` (with `renewal_count`) now renders
  under the cards, and an "Insured Cover" tile appears when there are any.
- `clients.test.test_insurance_tracker_views` pins the tabs, the scoping, the
  ordering and the profile.

## Renewal filing checklist

- `Renewal.policy_doc_submitted` (migration 0125) is one tick: "Renewal policy
  submitted to Google Drive". **Blank means not filed** — that is the whole
  point, so it must never default True and is deliberately not required.
- On both renewal forms (add + edit, so a missed tick is correctable), and
  shown as a Filed/Pending column on the renewals list and on the client
  profile's renewal history — the tick is only worth collecting if somebody
  can see what is outstanding.
- Not on the app's Add Renewal yet: a phone-entered renewal reads Pending
  until someone edits it on the web.

## Sale policy filing (the Drive tick on a sale)

- `Sale.policy_doc_submitted` (migration 0128) is the renewal tick one line up:
  the add-sale forms ask "Policy uploaded to Google Drive?" for Health/Life
  only (same JS toggle as policy date/number; `clean()` forces it False for
  everything else).
- **Blank is the point.** `services.sales._notify_policy_not_uploaded` runs
  inside `finalize_new_sale`, so a sale booked on the phone raises the same
  flag as one typed on the web: every active admin gets a "Policy not
  uploaded" notification. The sales list shows the badge, with a
  **Mark uploaded** button beside it.
- That button is its own endpoint (`mark_policy_uploaded`), not the edit form:
  a non-admin edit sends the sale back to pending approval, and filing a
  document must never un-approve a sale. It saves with
  `update_fields=["policy_doc_submitted"]`, so `save()`'s `compute_points()`
  can't reprice an old sale under today's structure.
- The add-sale forms also link straight to the picked client's Drive folder
  (`#client-drive`, any product) — the folder is created on first click.
- Not on the app's Add Sale yet: a phone-entered sale reads "not uploaded"
  until someone ticks it on the web, which is the right default anyway.
- `clients.test.test_sale_policy_upload` pins all of it.

## No duplicate renewals

- **One renewal per policy per cycle.** `insurance_sync.duplicate_renewal()`
  is the single check, called by **both** write paths *before* the row is
  saved — the web view and `app_renewal_create` never meet before the save, so
  a guard on either one alone leaves the other door open.
- The window scales with `Renewal.frequency` (`_CYCLE_DAYS`: yearly 300 days,
  half-yearly 150, quarterly 75, monthly 25) — a flat one-year window would
  refuse the second of twelve monthly collections.
- **A typed policy number identifies the policy just as well as a ticked one**,
  via `find_policy_by_number` (which upper-cases/strips the way
  `InsurancePolicy.save()` does). That is the case worth catching: it is
  exactly how one policy gets entered twice under two numbers.
- It is a **warning, not a block** — a genuine early renewal has to stay
  possible. Web re-renders the form with a tick ("add it anyway",
  `confirm_duplicate`); the app answers **409** with `duplicate: true` and
  shows the same AlertDialog Add Sale uses. Both re-post with
  `confirm_duplicate`.
- `clients.test.test_renewal_duplicates` pins both doors and the override.

## Renewals list

- The page **opens on this month's collection** — that figure is reported every
  day and nobody should type two dates for it. Period chips (This Month / Last
  Month / This FY / All) set the window; typing a date is what "Custom" means,
  and a *search* no longer clears the month the way it used to.
- **Type tabs are the primary control**, as on the Insurance Tracker: All /
  Health / Life / Other, each carrying its own count and premium, and
  everything below reads the selected book — the KPI strip, the totals line,
  the due counts. Tabs keep the period and the period chips keep the tab, so
  neither control silently widens the other.
- **`Renewal.kind_q` / `insurance_kind` roll sub-products up to the parent**
  (`product_ref__parent__code`). Matching the code alone filed every
  plan-level renewal ("PR Life Pro", parent LIFE_INS) under *Other* — the
  same roll-up rule as everywhere else, and `insurance_sync` reads the same
  property, so those renewals were also missing their tracker policy.
- **Due ≤30 days / Overdue are a separate control**, so they *replace* the
  payment window instead of narrowing it (due next month AND collected this
  month is nobody), and they sort soonest-first — a work queue, not a
  collection log. Their counts honour the employee scope; they were read off
  the whole table until 2026-09-03.
- **They read `Renewal.due_on_expr()`, not the column.** Only 31 of 192
  production rows carry `renewal_end_date`, so filtering the column alone
  showed a due list of zero on a live book: the expression falls back to one
  `CYCLE_DAYS` cycle on from `renewal_date`, per frequency. The Due column
  marks a derived date "est.".
- **Each date bound works alone.** Both `renewal_date` and
  `premium_collected_on` filter with separate `__gte` / `__lte` clauses; the old
  `if start and end` pair meant a lone "Payment Start" filtered nothing at all
  (the same bug sales had, fixed in 7ce020a).
- `clients.test.test_renewals_period` pins the chips and the bounds.

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
- **Renewal reminders** (`renewal_reminders` cron, daily 8:45 AM) walk the
  **Insurance Tracker, not the sales book**. A policy's `end_date` *is* its
  renewal due date, so `InsurancePolicy` is the only queryable that reaches the
  **old book** — those policies were back-filled from a renewal entry and have
  no `Sale` behind them, so a sale-keyed job left the most lapse-prone half of
  the register with no reminder at all (fixed 2026-09-02).
- A month (**30 days**) and a week (**7 days**) before `end_date`, the client's
  **mapped employee** (fallback `relationship_manager`) gets one high-priority
  **"Renewal Reminder · client · type number"** task due on the renewal date,
  deduped by `assign_group = "polrenew:<policy>:<date>"` (that field is
  `max_length=32` — keep the prefix short). The body carries the whole policy:
  number, insurer, plan, sum assured, premium, cover dates, client phone,
  nominee, last premium collected.
- **All three alert paths fire.** `services.tasks.ring_task` (shared with
  `tasks_ring_due`, which is why it lives in the service and not in either
  command) sends the ringing `task_alarm` push on each reminder day; the task
  carries a `due_time`, without which `tasks_ring_due` skips it and the app's
  on-device `due_at_ms` alarm never arms; and HIGH priority re-rings every 4h
  until acknowledged. A `silent` task still degrades to a plain tray push.
- **A collected renewal stops the chase by itself.** `insurance_sync._advance_cover`
  rolls `end_date` forward (and revives a `lapsed` policy) when a renewal is
  logged against it, so the policy leaves the reminder window with nobody
  closing a task. Without it `end_date` froze at creation, "Expiring ≤30d" went
  permanently stale, and the job chased premiums already banked.
- `sync_policy_from_sale` writes `Sale.coverage_end()`, not `+1 year` — a
  multiyear policy must expire when its **paid term** ends or the tracker calls
  a 3-year policy due after 12 months and reminds two years early.
- Still sale-keyed and therefore still blind to the old book: the
  `insurance_renewal` **calendar feed** (`services/calendar_feed.py`). Move it
  onto `InsurancePolicy.end_date` when the calendar next gets touched.

## Future points (the multiyear statement)

- `/clients/incentives/future-points/` is what an employee is owed but has not
  been paid yet: the later years of every multiyear health policy they sold,
  as a total, then one line per **financial year**, opening into the **months**
  the instalments fall due in. An employee sees their own; an admin/manager
  opens on the whole firm and can pick a person.
- **Nothing new is stored.** The rows are derived by
  `incentives.pending_accruals()` (now firm-wide when `employee=None`, and each
  row carries the policy, the client and the policy number) and grouped by
  `incentives.future_points()`. The `multiyear_incentive_accruals` cron is
  still the only thing that credits them, so the salary check is unchanged —
  the points join the month they land in like any others.
- Three levels, all native `<details>` — **FY → month → the policies in it**.
  No JS: the browser already has a disclosure widget.
- **Cancelling is the policy's status, not a new flag.** A multiyear premium is
  paid up front, but the EMIs stop and the policy goes — and then years 2..N
  were never earned. `insurance_sync.set_policy_status()` writes the tracker
  policy's status (`policy_cancel`, from the policy page or the Future Points
  row, with a reason stamped into `policy.notes`) and
  `incentives.accrual_schedule` reads it, so nothing further is ever scheduled.
  **No accrual row needs deleting: a year is only written on the day it falls
  due.** Reinstating puts the remaining years back.
- **`incentives.policy_stopped()` is the one gate, and any non-active status
  stops it** — lapsed is what an EMI-killed policy is often marked and matured
  means the term is over. Nothing sets those automatically, so each is somebody's
  deliberate act. A sale with **no** policy keeps accruing: the old book predates
  the tracker, and silence is not cancellation.
- **The policy is matched by `source_sale`, then by (client, policy number).**
  A policy back-filled from a renewal carries no `source_sale`, and reading the
  link alone left exactly those policies unable to stop anything — and their
  seller unable to press the button. The number is scoped to the client: two
  clients can carry the same string.
- **`emi_reminders` reads the same gate.** The policy is usually cancelled
  *because* the EMIs stopped, and the job was still assigning a monthly
  high-priority "call the client for the EMI" on a policy that no longer exists.
- `pending_accruals` prices the whole firm's book, so the rule and its slabs are
  read **once, not per sale** (`quote` sorts slabs in Python rather than with
  `.order_by()`, which would ignore the prefetch). 31 queries → 5 on the local
  book; flat as the book grows, pinned by a test.
- **Cancelling is not deleting, so it is not admin-only**: an admin may cancel
  any policy, and **the employee who sold it may cancel their own**. They are
  the one told the EMIs stopped, and the only points a cancellation takes away
  are theirs. Deleting a policy stays admin-only.
- **A policy pays for its term and no longer.** The term is what was sold
  (`policy_years`, 1–3): year 1 lands with the sale and years 2..N on the
  anniversaries inside the term, so a 3-year policy produces exactly three
  credit events and a 2-year policy two. `coverage_end()` is the wall — nothing
  is credited on or after it, running the job daily for a decade adds nothing,
  and re-running it is a no-op. What happens after the term is a **renewal**,
  entered as a `Renewal`, and **a renewal carries no points at all** (the model
  has no points field and no view computes one) — the employee is paid on the
  sale. `TermBoundaryTests` in `test_future_points` pins the whole mechanism by
  simulating twelve years of the cron.
- Year 1 is untouched by a cancellation — it was sold, delivered and paid for.
  Un-approving the sale is the tool for taking that back, and it takes
  everything.
- Deleting a policy is still separate and still blocked by claims/renewals
  (`policy_delete_blockers`); cancelling is the answer when the record has to
  stay.
- **A year landing this month is shown apart from the month's selling.** The
  credit is earned in that month and counts towards the salary check like any
  other points — but no sale was made, so folding it into one number makes a
  quiet month read as a good one. `incentives.accrued_rows(employee, start, end)`
  gives the policies behind `accrued_points`, and four screens split on it: both
  dashboards (points card / KPI foot, plus the list of which policies paid), the
  employee's month report (its own KPI tile + table, never inside the product
  rows) and the admin's month report (Top Performers stays "sold this month",
  with earlier-year credits beside it). The Incentive Payout page already split
  it. The split only renders when there is something to split.
- **The life FY ladder sits below it on the same page**, not on a separate one:
  both answer "what is owed that has not been paid yet", and one employee picker
  should drive both. The roster of everyone's standing renders only for an
  admin/manager looking at the whole team; an employee sees their own year and
  no one else's figures, and only a `manage_incentives` holder gets the
  record-a-payout box. The month rows are `<details>` like the rest of the page
  and **open into the policies behind the figure** — the old page showed
  ₹3,10,000 in June and no way to see which policies made it.
- `life_bonus_status` reads the year's sales in **one query** and groups them in
  Python (it was twelve monthly aggregates per employee, and the roster calls it
  per employee). That is also where the per-month policy list comes from.
- **The page is a personal statement.** A plain employee sees only their own
  rows — no employee picker, and `?employee=` is ignored for them, so the URL
  is not a way round it. Admins and managers get the whole firm with a picker.
- `seed_demo_crm` seeds 10 multiyear health sales (2- and 3-year, spread across
  employees, months and three financial years, each with its tracker policy) so
  the drill-down has content locally. `--undo` takes them with the demo clients.
  Local dev also needs `seed_incentive_structure` — an old rules table quotes
  these years at the pre-2026 unit rate and every row reads ₹15.
- `clients.test.test_future_points` pins the statement, the scoping, the
  cancellation and the page.

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
  base / ladder bonus / multiyear, plus dated bonus releases). The FY ladder
  position lives **under Future Points** now, not on a page of its own —
  `/clients/incentives/life-bonus/` is a redirect to `…/future-points/#life-bonus`
  (which `record_bonus_payout` also returns to).
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
- A claim follow-up is a **Task** (see "Follow-ups are tasks" below) — the
  `ClaimReminder` model was deleted, migration 0123. Every stage update / note
  can still attach one in the same submit.
- **The app works claims too** (`views/app_insurance_api.py` → `ui/InsuranceScreen.kt`,
  Menu → Insurance). Policies and claims are two doors into one screen stack.
  Every write goes through `services/claims.py`, so web and phone leave the same
  trail; the stage list, modes and document kinds are served by
  `/api/app/insurance-meta/` rather than hard-coded in the app. Documents upload
  as multipart to `/api/app/claims/<id>/document/?kind=…` — `kind` rides in the
  query string because the device's multipart helper sends the file field and
  nothing else — and are read back through the existing web proxy in the WebView.
  This is the module that most needed a phone: a claim is intimated over the
  phone and its papers are photographed on the spot.

## Follow-ups are tasks

- A dated follow-up on **any** record is a `Task` and nothing else —
  `services/followups.py` is the only way to make one. There is no
  `LeadFollowUp`, no `ClaimReminder`; both models were migrated into Task and
  dropped (0121 schema → 0122 data → 0123 drop; keep them separate, the
  index build must not share a transaction with the bulk row write).
- **Why:** a lead follow-up got a plain tray notification nobody saw, while a
  task rings the phone like an alarm clock. Folding them in buys the whole task
  pipeline free — `tasks_ring_due` at the exact due minute, the Android app's
  local AlarmManager copy (rings offline), `tasks_mark_overdue`, and one row on
  the common calendar. Two rows for one commitment was the thing to avoid:
  close the task, leave the follow-up pending, and the calendar nags forever.
- `Task.source_kind` / `source_id` point back at the record ("lead"/42). Not an
  FK, so **nothing cascades** — `signals._delete_followup_tasks` removes them
  when the lead/claim is deleted, or a dead record's follow-up keeps ringing.
- `created_by` is the **system** user (`followups.system_user()`, inactive +
  unusable password): that is what marks a task as generated rather than typed.
  `assigned_to` is whoever OWNS the record — the lead's employee, the claim's
  handler — falling back to whoever scheduled it. An admin scheduling on
  someone else's lead must ring *that* phone.
- Priority is **medium** on purpose: high/critical re-ring every four hours
  until acknowledged, and twenty follow-ups a day at that volume is a storm
  people learn to swipe away. They land in a "Follow-up" `TaskCategory` so the
  real task list stays readable.
- The chase ends by itself: `cancel_open()` runs when a lead is marked lost or
  converted. A won lead that keeps ringing is worse than no reminder.
- Closing and rescheduling are **task** actions (`task_set_status` — a bare
  POST means done — and `task_reschedule`, the calendar drag endpoint). Both
  clear `due_alarm_sent_at`; a task rescheduled without clearing it never rings
  again, which is why the web `task_set_due` used to go silent.
- The **app can see and schedule them too** — `app_lead_detail` serves
  `followups` and `/api/app/leads/<id>/followup/` schedules one through the
  same `followups.schedule`. It carried neither until 2026-08-19, so a lead
  with three calls booked read as "nothing scheduled" on the one screen you
  would go to to book one. A row taps through to `TasksActivity`: closing and
  rescheduling are task actions and must not be re-implemented per record.
- `clients.test.test_followup_tasks` pins all of this.

## "Don't ring" (Task.silent)

- A task assigned after hours can be marked **silent** (tick on the web assign
  modal, chip on the app's Assign screen). It is still assigned and still
  notifies — only the alarm-clock ring is suppressed. Unticked = unchanged.
- **Three ring paths, all of them check it:** the assignment/comment
  `task_alarm` push (`services.tasks.create_notification(..., ring=)`, passed
  `ring=not task.silent` by `notify_task`), the due-time ring (`tasks_ring_due`
  writes a plain `Notification` instead, still stamping `due_alarm_sent_at`),
  and the app's **on-device exact alarm** — armed from `due_at_ms` and rings
  offline with no server in the loop, so the task row withholds that field for
  a silent task. Miss that third one and the phone still goes off.
- Shared across a multi-assignee group (`GROUP_SHARED_FIELDS`).
  `RecurringTaskRule` has no such flag yet — a nightly recurring task still
  rings each instance.

## The agenda (dashboard) and the common calendar

- **Every source must be team-scopable, or folding a model into Task silently
  hides it from admins.** `feed_items(team_followups=True)` widens the sources
  an admin dashboard shows to the whole team, optionally narrowed with
  `employee_id`. `_tasks()` honours it exactly like `_call_followups()` does —
  it did not when lead follow-ups became Tasks, so from 2026-08-14 an admin saw
  all employees' calls and only their own tasks, i.e. no pipeline at all.
- **A follow-up is a Task, so the badge has to say which kind.** They share the
  `task` source (one filter chip, one reminder pipeline) but carry a
  `source_label` of Lead / Claim / Task from `Task.source_kind`, and a lead
  follow-up carries the lead's SPANCO `stage` as well. `Lead.STAGE_HOT`
  (Approach / Negotiation / Conclusion) is the live-pipeline band: those items
  wear the stage colour, get an accent bar, and sort first *within their day*.
- **Adding a source means three edits, not one**: the feed, the agenda's
  `.agenda-source-<name>` badge rule, and the calendar page's `.src-filter`
  checkbox + colour map. `insurance_renewal` had only the first, so it was
  unstyled on the agenda and — because the page always sends a `sources` list
  built from its checkboxes — completely unreachable on the calendar.
- **The agenda cannot show a lead nobody has dated**, which is exactly the lead
  that is dying. `services/leads.needs_attention()` is the answer and is
  deliberately *not* part of the feed: live-pipeline leads with no open
  follow-up task, or stalled 14+ days, rendered by
  `dashboards/_pipeline_attention.html` on both dashboards.
- `clients.test.test_agenda_pipeline` pins all of this.

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
- **The app home screen carries the pipeline too** — `app_dashboard` serves a
  `pipeline` block (stage standing + `services.leads.board()`, scoped by
  `_lead_qs`), rendered above the sales sections. `board()` is one page per
  stage **from Approach on** — Suspect and Prospect are the top of the funnel
  and belong on the list screen, not on a dashboard asking what to do today —
  and every lead carries its own follow-up (next due date, or a red tag when
  there is none) so chased and unchased sit in one place. The phone swipes
  through the pages; the stage chips are both the indicator and the jump-to.
  `needs_attention` still feeds the **web** dashboards, which show only the
  drifting ones. Tasks and call follow-ups are
  deliberately kept off it: they own the Tasks and Calls tabs, and a third copy
  is what people learn to ignore. A chip or a card routes through `routeLink`
  (`/clients/leads/?stage=x`, `/clients/leads/<id>/`) into the native
  `LeadsScreen` via the overlay string `leads:<id|stage>`.

## Date of birth, and the two alerts it drives

- `Client.date_of_birth` is **mandatory on every client added from 2026-09-04
  on** — enforced in the web `ClientForm.clean_date_of_birth` **and**
  `app_client_create`, because a rule guarded only in the browser is not a rule
  (the phone is a real add-client path). Both also refuse a future date and a
  year over 120 back: a typo'd year sits in the age reports forever.
- The column **stays nullable**. ~2,900 imported clients have none, and making
  it non-null would make every one of them uneditable. They are chased on the
  **KYC Issues** screen ("Clients missing date of birth", its own search +
  pagination + inline save), exactly as PAN, phone and email already were.
- `Client.age` is a property, never stored. **A missing DOB is `None`, not 0** —
  every caller must handle it.
- **Why it is collected.** `services/client_alerts.py` owns both alerts; they
  share one task-raising implementation and differ only in what they say:
  - **Birthday**, every year on the day (`client_birthday_tasks`, 8:05 AM):
    "wish them / send the valuation report / review the financial plan" ride as
    a **checklist**, so they can be ticked off rather than read. Key
    `bday:<client>:<YYYY>` — **the year is in the key**, or the birthday comes
    round once per lifetime. **MEDIUM priority on purpose**: high/critical
    re-ring every 4h until acknowledged, and a daily task at that volume is a
    storm people learn to swipe away.
  - **Retirement planning**, once, at 40 (`retirement_alerts`, 8:15 AM). HIGH
    priority — it fires once per client ever. Key `retire:<client>`.
- Both are **assigned to the mapped employee AND every active admin** as one
  multi-assignee group (sibling rows sharing `assign_group`), so each person
  closes their own copy. That group id doubles as the dedup key, so a re-run can
  never raise the work twice. An unmapped client's alert falls to the admins
  rather than into a hole. `Task.assign_group` is 32 chars — keep prefixes short.
- Volume: one group per birthday per year × (1 employee + every admin). At ~2,900
  clients and 3 admins that is roughly **30 task rows a day**. If it proves
  noisy, give admins one daily digest instead of a row per client — the change
  is `assignees()` in `client_alerts.py`.
- **The daily crons only ever see today's birthdays.** The clients already past
  40 are a **backlog**, not a cron job: `retirement_alerts --backlog` (dry run)
  / `--apply`. Running it automatically would raise hundreds of tasks the first
  morning after deploy. Typing an old client's DOB on KYC Issues is how a
  45-year-old first becomes visible, so that save's success message names the
  backlog command.
- The calendar's `birthday` source (`calendar_feed._birthdays`) still expands
  birthdays across any date range — that is the *planning* view ("who is coming
  up"), while the task is the work item for today. Both will show on a day view.
- `clients.test.test_client_dob_retirement` pins all of it.

## Merging duplicate clients

- `services/client_merge.merge_clients(keep, remove)` repoints every reverse
  relation by walking `Client._meta.related_objects`, fills any contact field
  the keeper is missing, then deletes the emptied profile. It is the only
  implementation — three screens call it, none reimplement it.
- The KYC screen's automatic duplicate groups only catch an **exact** shared
  PAN, phone or name, so "Rajesh Sharma" and "Sharma Rajesh Kumar" sat there as
  two profiles forever. **Find & merge by name** (admin-only, `?mq=`) uses
  `name_words_q` — every word, any order — and shows what each profile holds
  (`business_record_counts_bulk`) so the keeper is an informed pick. It posts to
  the existing `client_merge` endpoint; the picker is UI over logic that was
  already there.

## Phone numbers are a matching key

- **Normalised on the model, never in the form.** `Client.save()` and
  `Lead.save()` run `utils.phone_utils.clean_phone`, the same reasoning as
  `Sale.policy_number`: the web form, the app API, imports and seeds all write
  phones, and only the model covers every path.
- **Why it exists:** a spreadsheet import stored 2,168 of 2,937 client numbers
  as `"9423440791.0"`. That is worse than an unmatchable string — every matcher
  strips non-digits and takes the last ten, turning it into `4234407910`, a
  *different* number. It did not fail to match, it matched the wrong person:
  174 clients shared a mis-derived key, silently feeding duplicate detection
  and the KYC grouping. `manage.py fix_phone_floats` is the one-off repair
  (dry run by default, `--apply` writes, idempotent).
- **One normaliser: `phone_utils.digits10`.** `views/calls._normalize_digits`
  and `app_api._fu_digits` are aliases of it. Never hand-roll
  `re.sub(r"\D", ...)[-10:]` again — that is the exact line that read the
  float rows wrong.
- **A follow-up's client link is a snapshot, so names resolve at display time.**
  `services/calls.caller_names()` batches the lookup (one query per table,
  never per row) and covers the two gaps the stored FK cannot: the number
  belongs to a **lead**, and a client added *after* the call was logged. Used
  by the agenda feed, the post-call popup and `_fu_row` — which is where the
  app's ringing alarm gets its name (`FollowupAlarmScheduler.syncFromPending`
  reads `client` and falls back to the raw number), so fixing the payload fixed
  every phone in the field with no APK release.
- `clients.test.test_phone_normalisation` pins all of this.

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
- **Call follow-ups are the only thing on this screen.** Lead follow-ups used
  to be merged in as `kind: "lead"`; they are Tasks now and are worked on the
  Tasks screen, so the endpoint serves call rows only and `kind: "lead"`
  answers 410 for pre-v4.32 apps. Call follow-ups deliberately stayed out of
  the task model: the post-call popup creates them automatically on every
  unanswered call, and one task per missed dial would bury the task list.
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
- **Active and inactive people are two lists, not one list with a badge.** The
  team list opens on Active; Inactive and All are tabs, and a search stays
  inside the tab it was typed in.
- `services/employee_performance.py` is the whole record with the firm —
  business (sales + renewal premium, FY and lifetime), earnings, target vs
  actual, the SPANCO funnel and win rate, task/call activity, client value and a
  12-month trend. `snapshot(emp)` feeds the detail page, `list_stats(employees)`
  the list cards in four queries (never a per-row lookup).
- **"Points earned" is never `Sale.points` alone.** It is sales points +
  `incentives.accrued_points` (multiyear years that land without a sale) +
  `BonusPayout` (the FY ladder prize, handed over as cash). Showing only the
  first understates what somebody earned. `clients.test.test_team_performance`
  pins it.

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
- The Menu is **grouped** (`MenuEntry.group`: Work / Insurance / Reports /
  Admin / This phone), not one flat run in the order screens happened to ship.
  A new screen picks a group; it does not get appended to the end.
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
  (`net/Outbox.kt`); creating a sale or client deliberately does not. A **lead
  stage move** queues — it is the write that happens in a client's living room —
  and `services.leads.set_stage` drops a replay that repeats the last event, so
  the queue can never log the same move twice. A queued write shows a message
  and does **not** reload the screen: the reload would fail on the same dead
  network and swap the record for an error box.
- **Attaching a file is `ui/Attach.kt`, never re-rolled**: `rememberAttacher`
  gives a camera button and a picker button, `uploadUri` does the multipart
  upload and *returns the failure* instead of swallowing it. The camera writes
  through the FileProvider the manifest already declares; no CAMERA permission
  is declared and none should be — adding one would make Android demand a
  runtime grant this flow does not need. Task attachments and claim documents
  share it.
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
3. On server: `cd ~/silicon-crm && git pull && venv/bin/pip install -r requirements.txt && venv/bin/python manage.py migrate && venv/bin/python manage.py collectstatic --noinput`
   - **`collectstatic` is not optional.** WhiteNoise serves from `staticfiles/`
     via `CompressedManifestStaticFilesStorage`, so a CSS/JS change that isn't
     collected never reaches a browser — the page ships with the old
     stylesheet and looks broken in ways no test catches.
   - Reload gunicorn with `kill -HUP <master-pid>` (the master runs as
     `ubuntu`; there is no passwordless sudo on the droplet). Find it with
     `pgrep -af "[g]unicorn"` and HUP the **parent** — note the bracket, or
     the pattern matches your own shell and you HUP that instead.
   - Before a migration that rewrites or drops data, run
     `scripts/backup_db.sh` first; it dumps and mirrors to Drive in seconds.
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

## The Mutual Funds module is gone (2026-09-02)

- The CAMS/KFintech **RTA feed** and everything it built — `MutualFundFolio`,
  `MutualFundTransaction`, `SipRegistration`, `RTAFeedImport`, `ArnAccount`,
  the `/clients/mf/…` screens (RTA Feeds, Transactions, Folios, Match Folios,
  SIP Register, COB), `services/rta_feed.py`, the hourly `import_rta_feeds`
  cron and the `Product.rta_match` sale cross-check — were **deleted**, tables
  and all (migration **0126**). Owner's call: the feature was not used.
- The firm still **sells** mutual funds. SIP / Lumpsum are ordinary products in
  the catalog, and the client's Portfolio is the sales book (`services/holdings`)
  — that is unchanged, and it never read the feed.
- Three screens lost feed-derived sections and are otherwise intact: the client
  profile (MF Folios / SIP Register / Valuation Report), **KYC Issues** (the
  unlinked-folio tile, folio PAN suggestions and the PAN/name-conflict card),
  and **Approve Sales** (the RTA evidence row). PAN stays mandatory on new
  clients — it is the identity key behind duplicate detection.
- `RTA_FEED_IMAP_*` env vars, the `rta_formats/` samples and the feed-only pins
  (`dbfread`, `pyzipper`, `xlrd`, `msoffcrypto-tool`) are gone too. Don't
  reintroduce any of it without the owner asking.

## The Meetings module is gone (2026-09-02)

- The `Meeting` model, `/clients/meetings/`, its nav entry, admin and the
  "RM wise Next Meeting Chart" were **deleted**, table and all (migration
  **0127**). Owner's call: follow-ups and the SPANCO pipeline already do this
  job, and a third place to record "I am seeing this client" is one people
  learn to ignore.
- It was never wired in anyway — `Meeting.scheduled_at` was not a
  `calendar_feed` source, so a booked review appeared on no calendar and no
  agenda, nothing reminded anyone, and `is_overdue` was read by no code. It
  was a list that had to be maintained by hand to tell you what a follow-up
  task tells you for free.
- **Booking a meeting still works and is unchanged**: `CalendarEvent` keeps
  its `("meeting", "Meeting")` event type, so a meeting is put on the common
  calendar like any other event, and a dated commitment to call or visit a
  client is a `Task` via `services/followups.py`. Don't reintroduce a
  separate meetings table — put the date on the calendar or make it a task.

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
- Manual tools (intentionally not in CRONJOBS): `fix_phone_floats`,
  `prod_readiness_check`, `recompute_client_holdings`,
  `backfill_insurance_policies`, `retirement_alerts --backlog`,
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
