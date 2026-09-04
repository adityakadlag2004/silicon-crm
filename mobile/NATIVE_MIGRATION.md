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
| 2 | Add Sale (client search, product picker; v4.32: "Already added?" dialog when the server matches an identical sale — same client/product/amount within 60 days — with Add anyway) | `/api/app/sale-meta/`, `/api/app/sales/create/` (409 + `duplicate`, `confirm_duplicate`) | **v3.1** |
| 3 | My/All Clients list + Client profile | `/api/app/clients/…` | **v3.1** |
| 4 | Call Follow-ups (native list + actions; v4.16: pick an exact date/time instead of the fixed +1h snooze, and a ＋ FAB to add a follow-up without a call; v4.17: pick the number from device contacts; v4.27: search + Due now/Today/Tomorrow/Later grouping with bulk "Move all", one-tap Later chips, swipe right = done / left = dismiss, outcome on Done, attempt count + last call per card, editable notes, WhatsApp, tap opens the client, overdue badge on the Calls tab; v4.28: lead follow-ups merged into the same list (`kind`), outcome asked on the ringing alarm screen, popup shows the number's history + "Not interested — stop chasing", outcome report on Call Analytics) | `/api/app/followups/…` (`reschedule`/`note` actions, `kind`, `done_today`, `outcomes`, `push-overdue/`), `/api/calls/followup/` (`custom_at`), `/api/calls/context/`, `/api/calls/close/` | **v3.1** |
| 5 | Sales list + Approve (admin) | `/api/app/sales/…` | **v3.1** |
| 6 | Native Menu + logout | `/api/app/logout/` | **v3.1** |
| 7 | Renewals (list + add) | `/api/app/renewals/…` | **v3.2** |
| 8 | Notifications screen | `/api/app/notifications/…` | **v3.2** |
| 9 | Leads pipeline — SPANCO stepper + one-tap "Move to «next stage»" with a note, stage filter chips with counts, per-lead product requirements off the catalog, remarks, stage history, convert, create (stages/labels/help all served, never hard-coded in the app) | `/api/app/leads/…` (`stage/`, `interest/`), `/api/app/lead-meta/` (stages + product catalog) | **v3.3**, SPANCO rebuild **v4.31.0** |
| 10 | Reports (trend chart, product mix, leaderboard; range chips, tap a bar or a person to drill down) | `/api/app/reports/summary/` | **v3.3**, reworked v4.36 |
| 11 | Team management (roster, edit, activate/deactivate, reset password, add) | `/api/app/team/…` | **v3.6** |
| 12 | Incentives & Campaigns builders (rules, slabs, campaigns, products) | `/api/app/incentives|campaigns/` + web AJAX writes | **v3.7** |
| 13 | ~~Lead sheets~~ — **module removed 2026-07** (screen deleted from app; `/api/app/sheets/…` stays as empty stubs until all devices update) | `/api/app/sheets/…` (stubs) | removed |
| 14 | Native login (session-cookie capture, native push + call-config bootstrap) | `/api/app/login/` | **v4.0** |
| 15 | Task Management module (own bottom nav: Dashboard/My/My Apps/Delegated/More; list+filters+status tabs, detail w/ checklist·comments·activity·actions, full-screen Assign form, Activities) | `/api/app/tasks/…` | **v4.15** |
| 16 | Follow-up alarms (exact on-device alarms + full-screen ringing AlarmRingActivity, daily digest, FCM `followup_alarm` data push;  one pending reminder per number — new follow-up supersedes older ones; popup SIM gate fails open + per-device popup diagnostics on Call Analytics roster + test-popup button in Office SIM; v4.13: task assignments/comments ring via `task_alarm` data push, status picker + tap-to-edit due date on task detail) | `/api/app/followups/…` (`scheduled_at_ms`, `superseded_ids`), device-status `diagnostics` | **v4.12** |
| 17 | Task power pack (due-time alarms on device + server `tasks_ring_due` cron; Acknowledge + 4h re-ring for unacked high/critical; overdue ≥2d escalation digest; client-linked tasks w/ tap-to-call + client-profile section; @mentions; templates; attach from phone; Today agenda screen; task search; on-time % scorecard; completed_at reopen fix) (v4.18: multi-assignee tasks collapse to one Delegated row "Mansi +2 others"; delete restricted to the assigner; per-person acknowledgement roster) | `/api/app/tasks/…` (`due_at_ms`, `acknowledged`, `client_id`, `assignee_label`, `can_delete`, `ack_roster`), `/api/app/tasks/templates/…`, `/api/app/today/` | **v4.14 (shipped v4.15; v4.18)** |
| 13 | ~~Lead Records / Sheets screen~~ — **removed v4.15** (module deleted server-side; SheetsScreen + menu entry gone from app) | — | removed |
| 21 | **Insurance module** — policy tracker (search, status/expiring chips, detail with claims + renewal history) and the full claim workflow (raise, stepper, stage moves with a note, follow-up in the same submit, documents by camera or picker, timeline). Stages/modes/document kinds are served, not hard-coded | `/api/app/insurance-meta/`, `/api/app/policies/…`, `/api/app/claims/…` | **v4.33.0** |
| 22 | Home screen carries the SPANCO pipeline (stage standing + the live leads nobody has dated); Today screen **deleted** — tasks, calls and renewals each have their own home and it was the third copy; Menu grouped into sections; lead stage moves survive no signal (`Outbox` + server-side replay dedupe) | `/api/app/dashboard/` (`pipeline`), `/api/app/today/` **removed** | **v4.33.0** |
| 23 | Lead detail shows its follow-ups and can schedule one (it had neither, so a chased lead read as unchased); a row opens the task in the Tasks module | `/api/app/leads/<id>/` (`followups`), `/api/app/leads/<id>/followup/` | **v4.34.0** |
| 24 | Home pipeline is swipeable: one page per stage from Approach on, every lead wearing its next follow-up date or a No-follow-up tag; chips double as page indicator | `/api/app/dashboard/` (`pipeline.board`) | **v4.35.0** |
| 25 | Renewals: "Already added?" dialog when the server matches a renewal already collected on this policy this cycle (window scales with frequency) — Add anyway, same idiom as Add Sale. Lead create picks a phone straight from the device contacts via the shared `ContactPickButton` (extracted from Follow-ups; a lint rule pins the single copy) | `/api/app/renewals/create/` (409 + `duplicate`, `confirm_duplicate`) | **v4.38.0** |
| 26 | Add Client requires the date of birth (the server rejects a blank, so the field is no longer "optional" and Save stays disabled until it is picked); client detail shows the date of birth and the age it implies — the age is what raises the birthday task and the retirement-planning task at 40 | `/api/app/clients/create/`, `/api/app/clients/<id>/` (`date_of_birth`, `age`) | **v4.39.0** |
| — | Business Links (My Apps tab) | opens web `/clients/links/` for now; native screens later | |
| — | Financial planner | stays WebView (calc engine is fine there) | |

| 17 | Reliability + a11y pass (audit fixes): policy date/number on Add Sale, renewal→policy linking, group task edit/delete, rolling session cookie, offline outbox for follow-up/task writes, response cache + pull-to-refresh, WebView uploads/downloads, monochrome notification icon + per-kind channels, `/api/app/me/`, crash reporting, R8 | `/api/app/me/`, `/api/app/crash/`, `policy_id` on renewals, paged `/api/app/tasks/` | **v4.23** |
| 18 | Mandatory updates: the version dialog no longer has "Later" and can't be dismissed — the app is unusable until the newer APK is installed | `/api/app/version/` (unchanged) | **v4.24** |
| 19 | Incentives screen reads slab semantics: health rate bands render as percentages (were shown as "2 pts" for a 2.00% band) and the edit field is labelled "Rate for this band (%)"; Port's flat rate named; the rupee-into-a-percent guard is server-side so old builds are covered too | `/api/app/incentives/` (`slab_mode`, `slab_unit`, `slab_period`, `port_percent`) | **v4.25** |
| 20 | Text fields stopped rewriting their own text in `onValueChange` (`.uppercase()`, `.trim()`, digit filters) — that is what collapsed the selection and threw the cursor to the end, making a mid-word typo unfixable. Input is shaped with `KeyboardOptions` now; the server normalises | — (`InsurancePolicy.save()` normalises policy_number) | **v4.26** |

## Rules

- Every screen ships behind the same versionCode bump + direct APK flow.
- Web templates are NOT deleted — the website keeps working unchanged.
- New features: build the API + native screen together; web page optional
  going forward for employee-facing flows, required for admin flows until
  their screens are converted.
- Build: `cd mobile/android && export JAVA_HOME="/Applications/Android
  Studio.app/Contents/jbr/Contents/Home" && ./gradlew assembleRelease`
