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
- `manage.py test clients.test.test_android_responsive` lints all of this and
  pins the scale maths.

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
  `seed_demo_tasks_links`, `seed_demo_crm`.
  `seed_demo_crm` seeds Households/Insurance/Claims/Meetings for testing;
  it refuses to run when `DEBUG` is off unless `--force`, and `--undo`
  removes exactly what it created (demo client ids ≥ 990000, `DEMO-` codes).
