# Kadlag Investment BO — Android App (TWA)

This is a **Trusted Web Activity** wrapper: the app opens
`https://bo.kadlaginvestment.com` fullscreen using the phone's Chrome engine.
It is the same web app with the same backend, login, and permissions — every
feature (and every future feature) works with **zero extra mobile code**.

## What's already done

- Signed release artifacts built:
  - `app/build/outputs/bundle/release/app-release.aab` → upload this to Play Store
  - `app/build/outputs/apk/release/app-release.apk` → install directly on a phone for testing
- Upload keystore generated: `upload-keystore.jks` (credentials in `keystore.properties`)
- The Django backend serves `/.well-known/assetlinks.json` (see `config/urls.py`),
  pre-filled with the upload key's SHA-256 fingerprint.

## ⚠️ BACK UP THESE TWO FILES NOW

`upload-keystore.jks` and `keystore.properties` are **gitignored** (they must
never be committed). If you lose them you cannot ship updates to the app.
Copy both to a password manager / secure drive today.

## Requirement before the app works fullscreen

The production site must serve the assetlinks file. That happens automatically
once you deploy the current `config/urls.py` to the server. Verify with:

    curl https://bo.kadlaginvestment.com/.well-known/assetlinks.json

Until that responds, the app still works but shows a browser address bar.

## Test on a phone (today, no Play Store needed)

1. Copy `app/build/outputs/apk/release/app-release.apk` to an Android phone.
2. Open it, allow "install from unknown sources".
3. Log in as usual. (Needs the assetlinks deploy above for fullscreen mode.)

## Publish to Google Play (one-time setup)

1. Create a developer account at https://play.google.com/console ($25 one-time).
2. **Create app** → name "Kadlag Investment BO", App, Free.
3. Since this is an internal team tool, go to **Testing → Internal testing**
   (up to 100 testers by email, no public listing, fastest review) and create a
   release. Upload `app-release.aab`.
4. Google will ask to enable **Play App Signing** — accept (Google re-signs the
   app with its own key).
5. After upload, go to **Setup → App integrity → App signing key certificate**
   and copy the **SHA-256 certificate fingerprint**.
6. On the server, set the env var so the site trusts BOTH keys
   (Play-signed installs and your locally-signed test APKs):

       TWA_FINGERPRINTS="<PLAY_SHA256_FINGERPRINT>,C7:D8:FC:18:03:2E:42:60:15:60:AF:BA:52:39:CF:E1:B6:B1:A3:3C:DF:1E:76:BA:8C:1F:29:73:95:90:EB:FC"

   then restart the app service and re-check the curl above.
7. Add your team's Gmail addresses as internal testers, share the opt-in link.
   They install from Play Store like any app.
8. Complete the content declarations Play asks for (data safety, target
   audience). For data safety: the app collects login credentials and customer
   data entered by your own staff, not shared with third parties.

## Shipping an update

The app shell almost never needs updating — web changes appear instantly since
the app loads the live site. Rebuild only when you change the icon, name,
domain, or colors:

1. Edit `gradle.properties` (bump `twaVersionCode` by 1 every upload).
2. `./gradlew bundleRelease`
3. Upload the new AAB in Play Console.

## Configuration

Everything lives in `gradle.properties`: domain, launch path, app name,
application id, version, brand colors. `local.properties` points at the
Android SDK (machine-specific, gitignored).
