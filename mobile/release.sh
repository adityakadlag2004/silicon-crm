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

cd "$DIR/android"

VERSION_CODE=$(grep -o 'versionCode [0-9]*' app/build.gradle | awk '{print $2}')
VERSION_NAME=$(grep -o 'versionName "[^"]*"' app/build.gradle | cut -d'"' -f2)

echo "── Building v$VERSION_NAME (code $VERSION_CODE) ──"
./gradlew assembleRelease | grep -E "BUILD|error" || true

APK="app/build/outputs/apk/release/app-release.apk"
[ -f "$APK" ] || { echo "Build failed — no APK"; exit 1; }

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
