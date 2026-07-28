# Native UI Migration Program

Goal: replace the WebView screens with real Android UI (Kotlin + Jetpack
Compose, Material 3), **one screen per release**, ordered by daily usage.
The app stays fully usable throughout — unconverted screens keep opening
in the WebView.

## Architecture decisions

- **Auth:** native screens reuse the WebView's session cookie
  (`CookieManager`), exactly like `BackendClient` already does for call
  tracking. No tokens, no second auth system. Login stays in the WebView
  until late in the program.
- **API:** plain Django JSON views under `/clients/api/app/…` (same style
  as `views/calls.py`) — session auth + CSRF header. Added incrementally,
  one endpoint set per screen.
- **App structure:**
  - `RouterActivity` (launcher): session cookie present → `ShellActivity`,
    else WebView login (`MainActivity`). After login, web JS calls the
    `CallTracking.notifyLoggedIn()` bridge → native shell opens.
  - `ShellActivity`: native bottom nav (Home · Clients · Add · Calls · Menu).
    Converted tabs render Compose; unconverted items open `WebActivity`
    (plain WebView sharing the session) at the right URL.
  - Capacitor `MainActivity` remains for login + push/call-tracking plugin
    registration.
- **Design system:** Material 3. Brand gold `#E5B740` primary, cream
  `#FFFEF8` background, dark ink `#1f2937` text, existing product badge
  colors carried over. Cards, large touch targets, pull-to-refresh.

## Conversion order (one per release)

| # | Screen | API | Status |
|---|--------|-----|--------|
| 1 | Shell + Dashboard (role-aware) | `/api/app/dashboard/` | **v3.0** |
| 2 | Add Sale (client search, product picker) | `/api/app/sale-meta/`, `/api/app/sales/create/` | **v3.1** |
| 3 | My/All Clients list + Client profile | `/api/app/clients/…` | **v3.1** |
| 4 | Call Follow-ups (native list + actions; v4.16: pick an exact date/time instead of the fixed +1h snooze, and a ＋ FAB to add a follow-up without a call; v4.17: pick the number from device contacts) | `/api/app/followups/…` (`reschedule` action), `/api/calls/followup/` (`custom_at`) | **v3.1** |
| 5 | Sales list + Approve (admin) | `/api/app/sales/…` | **v3.1** |
| 6 | Native Menu + logout | `/api/app/logout/` | **v3.1** |
| 7 | Renewals (list + add) | `/api/app/renewals/…` | **v3.2** |
| 8 | Notifications screen | `/api/app/notifications/…` | **v3.2** |
| 9 | Leads pipeline (stages, progress, remarks, convert, create) | `/api/app/leads/…` | **v3.3** |
| 10 | Reports (trend chart, product mix, leaderboard) | `/api/app/reports/summary/` | **v3.3** |
| 11 | Team management (roster, edit, activate/deactivate, reset password, add) | `/api/app/team/…` | **v3.6** |
| 12 | Incentives & Campaigns builders (rules, slabs, campaigns, products) | `/api/app/incentives|campaigns/` + web AJAX writes | **v3.7** |
| 13 | ~~Lead sheets~~ — **module removed 2026-07** (screen deleted from app; `/api/app/sheets/…` stays as empty stubs until all devices update) | `/api/app/sheets/…` (stubs) | removed |
| 14 | Native login (session-cookie capture, native push + call-config bootstrap) | `/api/app/login/` | **v4.0** |
| 15 | Task Management module (own bottom nav: Dashboard/My/My Apps/Delegated/More; list+filters+status tabs, detail w/ checklist·comments·activity·actions, full-screen Assign form, Activities) | `/api/app/tasks/…` | **v4.15** |
| 16 | Follow-up alarms (exact on-device alarms + full-screen ringing AlarmRingActivity, daily digest, FCM `followup_alarm` data push;  one pending reminder per number — new follow-up supersedes older ones; popup SIM gate fails open + per-device popup diagnostics on Call Analytics roster + test-popup button in Office SIM; v4.13: task assignments/comments ring via `task_alarm` data push, status picker + tap-to-edit due date on task detail) | `/api/app/followups/…` (`scheduled_at_ms`, `superseded_ids`), device-status `diagnostics` | **v4.12** |
| 17 | Task power pack (due-time alarms on device + server `tasks_ring_due` cron; Acknowledge + 4h re-ring for unacked high/critical; overdue ≥2d escalation digest; client-linked tasks w/ tap-to-call + client-profile section; @mentions; templates; attach from phone; Today agenda screen; task search; on-time % scorecard; completed_at reopen fix) (v4.18: multi-assignee tasks collapse to one Delegated row "Mansi +2 others"; delete restricted to the assigner; per-person acknowledgement roster) | `/api/app/tasks/…` (`due_at_ms`, `acknowledged`, `client_id`, `assignee_label`, `can_delete`, `ack_roster`), `/api/app/tasks/templates/…`, `/api/app/today/` | **v4.14 (shipped v4.15; v4.18)** |
| 13 | ~~Lead Records / Sheets screen~~ — **removed v4.15** (module deleted server-side; SheetsScreen + menu entry gone from app) | — | removed |
| — | Business Links (My Apps tab) | opens web `/clients/links/` for now; native screens later | |
| — | Financial planner | stays WebView (calc engine is fine there) | |

| 17 | Reliability + a11y pass (audit fixes): policy date/number on Add Sale, renewal→policy linking, group task edit/delete, rolling session cookie, offline outbox for follow-up/task writes, response cache + pull-to-refresh, WebView uploads/downloads, monochrome notification icon + per-kind channels, `/api/app/me/`, crash reporting, R8 | `/api/app/me/`, `/api/app/crash/`, `policy_id` on renewals, paged `/api/app/tasks/` | **v4.23** |
| 18 | Mandatory updates: the version dialog no longer has "Later" and can't be dismissed — the app is unusable until the newer APK is installed | `/api/app/version/` (unchanged) | **v4.24** |

## Rules

- Every screen ships behind the same versionCode bump + direct APK flow.
- Web templates are NOT deleted — the website keeps working unchanged.
- New features: build the API + native screen together; web page optional
  going forward for employee-facing flows, required for admin flows until
  their screens are converted.
- Build: `cd mobile/android && export JAVA_HOME="/Applications/Android
  Studio.app/Contents/jbr/Contents/Home" && ./gradlew assembleRelease`
