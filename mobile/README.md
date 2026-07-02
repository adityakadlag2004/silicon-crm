# Kadlag Investment BO — Android App v2 (Capacitor)

Native Android shell (Capacitor) around the live web app at
`https://bo.kadlaginvestment.com`. Same backend, same login, every web
feature works — plus **native capabilities**: OS push notifications now,
call-tracking/telephony plugins possible later.

This **replaces** the TWA project in `/android` (same application id
`bo.kadlaginvestment.crm`, same signing keystore, higher versionCode — so it
installs as an upgrade). The keystore still lives at
`/android/upload-keystore.jks` + `keystore.properties`; keep those backed up.

## Status

| Piece | State |
|---|---|
| App shell (all CRM features) | ✅ built — `android/app/build/outputs/apk/release/app-release.apk` |
| Push: device registration endpoint + `PushDevice` model | ✅ in backend (migration `0075`) |
| Push: FCM send on every `Notification` | ✅ in backend (`clients/services/push.py`) |
| Push: token registration in the web app when inside the shell | ✅ in `templates/base.html` |
| Firebase project + `google-services.json` | ❌ **you must do this** (10 min, below) |

Without the Firebase step the app works fully — push simply stays off.

## Enable push notifications (one-time, ~10 minutes)

1. Go to https://console.firebase.google.com → **Add project** (name: anything,
   Analytics off is fine).
2. In the project: **Add app → Android**. Package name **exactly**
   `bo.kadlaginvestment.crm`. Skip the "add SDK" instructions.
3. Download **`google-services.json`** → put it at
   `mobile/android/app/google-services.json`.
4. Rebuild the app (commands below) and reinstall on phones.
5. Backend key: Firebase console → ⚙ **Project settings → Service accounts →
   Generate new private key**. Copy the JSON file to the server (e.g.
   `/srv/silicon/firebase-service-account.json`, readable by the app user only).
6. On the server set the env var and restart:

       FIREBASE_CREDENTIALS=/srv/silicon/firebase-service-account.json

7. Test: log into the app on a phone (grant the notification permission),
   then have an employee log a sale — every admin device gets a push.

Every in-app notification (new sale to approve, lead assigned, …) is
mirrored as a push automatically via the `Notification` post_save signal.

## Build commands

Requires JDK 21 — Android Studio's bundled one works:

    cd mobile/android
    export JAVA_HOME="/Applications/Android Studio.app/Contents/jbr/Contents/Home"
    ./gradlew assembleRelease    # APK  → app/build/outputs/apk/release/
    ./gradlew bundleRelease      # AAB  → app/build/outputs/bundle/release/ (Play Store)

Bump `versionCode` in `mobile/android/app/build.gradle` before each Play upload.

## Server deploy checklist for this feature

    git pull
    pip install -r requirements.txt   # adds firebase-admin
    python manage.py migrate          # 0075 PushDevice
    # set FIREBASE_CREDENTIALS env (step 5-6 above), restart the app service

## Play Store

Same flow as before (see `/android/README.md`): Internal testing track,
upload the **AAB**. Google Play App Signing fingerprint does NOT matter for
Capacitor (no assetlinks requirement — the app is a WebView shell, always
fullscreen). The `/.well-known/assetlinks.json` endpoint can stay; it's
harmless and keeps old TWA installs working until everyone upgrades.

## Later: call tracking / recording

- The shell is ready for Capacitor plugins (`@capacitor-community/*` or a
  small custom plugin) to read call logs / phone state for **tracking**.
  Note Google Play restricts `READ_CALL_LOG` — plan for internal/managed
  distribution of that build, or scope tracking to calls initiated from the app.
- **Recording** of SIM calls is blocked by Android 10+ for third-party apps.
  The compliant route used by financial firms in India is cloud telephony
  (Exotel / MyOperator / Tata Smartflo): employees dial through it, recordings
  + metadata come back to the CRM via API/webhook. That integrates on the
  Django side; no app changes needed.
