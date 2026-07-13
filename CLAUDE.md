# Silicon CRM — Kadlag Investment back office

Django 5.2 + Postgres web app with a native Android app (Kotlin/Compose, Capacitor shell).
Production: **bo.kadlaginvestment.com** (DigitalOcean droplet `ubuntu@139.59.28.8`, `~/silicon-crm`).

## Project map (a place for everything)

| Path | Purpose |
|---|---|
| `clients/` | The single Django app: models, views, services, APIs |
| `clients/views/` | One module per domain (`tasks.py`, `sales.py`, `messaging.py`, `app_*.py` = mobile JSON APIs) |
| `clients/services/` | Business logic shared by web + app views (`tasks.py`, `push.py`, `google_drive.py`) |
| `clients/management/commands/` | Cron jobs — every command here must be in `CRONJOBS` (config/settings.py) or documented as a manual tool |
| `clients/test/` | All tests (`manage.py test clients`) |
| `config/settings.py` | Settings incl. `CRONJOBS`; env read from `.env` (template: `.env.example`) |
| `templates/` | Web UI (server-rendered, Bootstrap) |
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

- `monthly_snapshot` command: only writer of `MonthlyIncentive`, which the admin
  incentive report reads as an optional cache ("snapshot if exists, else compute
  live"). Not in CRONJOBS. Decide: schedule it or delete command + model.
- Manual tools (intentionally not in CRONJOBS): `prod_readiness_check`,
  `seed_demo_tasks_links`, `monthly_snapshot` (pending above).
