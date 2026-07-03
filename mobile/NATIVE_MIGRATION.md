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
| 4 | Call Follow-ups (native list + actions) | `/api/app/followups/…` | **v3.1** |
| 5 | Sales list + Approve (admin) | `/api/app/sales/…` | **v3.1** |
| 6 | Native Menu + logout | `/api/app/logout/` | **v3.1** |
| 7 | Renewals (list + add) | `/api/app/renewals/…` | **v3.2** |
| 8 | Notifications screen | `/api/app/notifications/…` | **v3.2** |
| 9 | Leads pipeline | | |
| 10 | Reports (charts via Compose) | | |
| 11 | Team management (admin) | | |
| 12 | Campaigns / Incentives (admin) | | |
| 13 | Lead sheets (spreadsheet — hardest, last) | | |
| 14 | Native login | `/api/app/login/` | |
| — | Financial planner | stays WebView (calc engine is fine there) | |

## Rules

- Every screen ships behind the same versionCode bump + direct APK flow.
- Web templates are NOT deleted — the website keeps working unchanged.
- New features: build the API + native screen together; web page optional
  going forward for employee-facing flows, required for admin flows until
  their screens are converted.
- Build: `cd mobile/android && export JAVA_HOME="/Applications/Android
  Studio.app/Contents/jbr/Contents/Home" && ./gradlew assembleRelease`
