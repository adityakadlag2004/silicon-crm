#!/bin/bash
# One-command app release: builds the APK and publishes it to the server.
# Every phone with the app shows an "Update available" dialog on next open.
#
# Usage:  ./release.sh "What changed in this version"
set -euo pipefail

NOTES="${1:-Bug fixes and improvements}"
DIR="$(cd "$(dirname "$0")" && pwd)"
SERVER="ubuntu@139.59.28.8"
REMOTE_DIR="~/silicon-crm/media/app"

export JAVA_HOME="/Applications/Android Studio.app/Contents/jbr/Contents/Home"
export ANDROID_HOME="${ANDROID_HOME:-$HOME/Library/Android/sdk}"

# The signing key lives in <repo>/android/ (gitignored, never committed).
# Without it Gradle quietly builds app-release-unsigned.apk and there is
# nothing to publish.
KEYDIR="$(cd "$DIR/.." && pwd)/android"
[ -f "$KEYDIR/keystore.properties" ] || {
  echo "Signing key missing: copy keystore.properties + upload-keystore.jks into $KEYDIR/"
  exit 1
}

cd "$DIR/android"

VERSION_CODE=$(grep -o 'versionCode [0-9]*' app/build.gradle | awk '{print $2}')
VERSION_NAME=$(grep -o 'versionName "[^"]*"' app/build.gradle | cut -d'"' -f2)

APK="app/build/outputs/apk/release/app-release.apk"
rm -f "$APK"   # a failed build must not publish the last one

echo "── Building v$VERSION_NAME (code $VERSION_CODE) ──"
./gradlew assembleRelease | grep -E "BUILD|error" || true

[ -f "$APK" ] || { echo "Build failed — no APK"; exit 1; }

# Updates are mandatory, so an APK signed with any other key would leave every
# phone stuck on an update it cannot install. Compare with the live app first.
APKSIGNER="$(ls -d "$ANDROID_HOME"/build-tools/* | sort -V | tail -1)/apksigner"
cert() { "$APKSIGNER" verify --print-certs "$1" | awk '/SHA-256/ && !f {print; f=1}'; }
LIVE="$(mktemp)"
curl -sf -o "$LIVE" https://bo.kadlaginvestment.com/clients/app/latest.apk
[ "$(cert "$APK")" = "$(cert "$LIVE")" ] || {
  echo "Not publishing: this APK is signed with a different key than the live app."
  exit 1
}

echo "── Publishing to $SERVER ──"
ssh "$SERVER" "mkdir -p $REMOTE_DIR"
scp "$APK" "$SERVER:$REMOTE_DIR/latest.apk"
ssh "$SERVER" "cat > $REMOTE_DIR/version.json" <<EOF
{"version_code": $VERSION_CODE, "version_name": "$VERSION_NAME", "notes": "$NOTES"}
EOF

cp "$APK" ~/Desktop/"KadlagBO-v$VERSION_NAME.apk"

echo ""
echo "✅ Released v$VERSION_NAME (code $VERSION_CODE)"
echo "   • Server: every device offers the update on next app open"
echo "   • Desktop copy: ~/Desktop/KadlagBO-v$VERSION_NAME.apk (for first-time installs)"
echo "   • Verify: curl -s https://bo.kadlaginvestment.com/clients/api/app/version/"
