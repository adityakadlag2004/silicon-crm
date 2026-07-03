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
| 2 | Add Sale (client search, product picker) | `/api/app/clients/search/`, `/api/app/sales/` | |
| 3 | My/All Clients list + Client profile | `/api/app/clients/…` | |
| 4 | Call Follow-ups (native list + actions) | reuse `/api/calls/…` | |
| 5 | Sales list + Approve (admin) | `/api/app/sales/…` | |
| 6 | Renewals | | |
| 7 | Notifications screen | | |
| 8 | Leads pipeline | | |
| 9 | Reports (charts via Compose) | | |
| 10 | Team management (admin) | | |
| 11 | Campaigns / Incentives (admin) | | |
| 12 | Lead sheets (spreadsheet — hardest, last) | | |
| 13 | Native login + logout | `/api/app/login/` | |
| — | Financial planner | stays WebView (calc engine is fine there) | |

## Rules

- Every screen ships behind the same versionCode bump + direct APK flow.
- Web templates are NOT deleted — the website keeps working unchanged.
- New features: build the API + native screen together; web page optional
  going forward for employee-facing flows, required for admin flows until
  their screens are converted.
- Build: `cd mobile/android && export JAVA_HOME="/Applications/Android
  Studio.app/Contents/jbr/Contents/Home" && ./gradlew assembleRelease`
