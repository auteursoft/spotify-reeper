#!/bin/bash
# Signs an archived Reeper.app for the Mac App Store and wraps it in an installer package
# you upload with Apple's Transporter app (or `xcrun altool`). See macapp/APP_STORE.md.
#
#   macapp/scripts/package_appstore.sh <path/to/Reeper.app> <App Store provisioning profile>
#
# Environment:
#   APP_IDENTITY        e.g. "Apple Distribution: Your Name (TEAMID)"
#   INSTALLER_IDENTITY  e.g. "3rd Party Mac Developer Installer: Your Name (TEAMID)"
#
# Why a script: Xcode's export re-signs the app and its standard nested code, but not the
# Python runtime inside Contents/Resources, which App Store validation then rejects. This
# signs everything, inside out, with your distribution identity.
set -euo pipefail

APP_IN=${1:-}
PROFILE=${2:-}
[ -d "$APP_IN" ] && [ -f "$PROFILE" ] || { echo "usage: $0 <Reeper.app> <profile.provisionprofile>" >&2; exit 2; }
: "${APP_IDENTITY:?set APP_IDENTITY to your Apple Distribution identity}"
: "${INSTALLER_IDENTITY:?set INSTALLER_IDENTITY to your Mac Installer Distribution identity}"

HERE=$(cd "$(dirname "$0")" && pwd)
RES="$HERE/../Reeper/Resources"
OUT="$HERE/../build/dist-appstore"
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

rm -rf "$OUT"
mkdir -p "$OUT"
APP="$OUT/Reeper.app"
ditto "$APP_IN" "$APP"

[ -f "$APP/Contents/Resources/python/.reeper-audio" ] && {
    echo "error: this app contains the audio (Essentia, AGPL) edition. Build the App Store edition:" >&2
    echo "       macapp/scripts/build_python.sh appstore, then archive again." >&2
    exit 1
}

# The profile's entitlements (application identifier, team) must be in the signature.
security cms -D -i "$PROFILE" > "$WORK/profile.plist"
/usr/libexec/PlistBuddy -x -c "Print :Entitlements" "$WORK/profile.plist" > "$WORK/profile-ent.plist"
APP_ID=$(/usr/libexec/PlistBuddy -c "Print :com.apple.application-identifier" "$WORK/profile-ent.plist")
TEAM_ID=$(/usr/libexec/PlistBuddy -c "Print :com.apple.developer.team-identifier" "$WORK/profile-ent.plist")
cp "$RES/Reeper.entitlements" "$WORK/app.entitlements"
/usr/libexec/PlistBuddy -c "Add :com.apple.application-identifier string $APP_ID" "$WORK/app.entitlements"
/usr/libexec/PlistBuddy -c "Add :com.apple.developer.team-identifier string $TEAM_ID" "$WORK/app.entitlements"

cp "$PROFILE" "$APP/Contents/embedded.provisionprofile"

echo "==> Signing nested code"
"$HERE/sign_nested.sh" "$APP/Contents/Resources/python" "$APP_IDENTITY" "$RES/PythonHelper.entitlements" --timestamp

echo "==> Signing the app"
codesign --force --sign "$APP_IDENTITY" --options runtime --timestamp \
    --entitlements "$WORK/app.entitlements" "$APP"
codesign --verify --deep --strict --verbose=2 "$APP"

echo "==> Building the installer package"
productbuild --component "$APP" /Applications --sign "$INSTALLER_IDENTITY" "$OUT/Reeper.pkg"

echo "==> Done: $OUT/Reeper.pkg"
echo "    Upload it with Transporter (Mac App Store), then submit the build in App Store Connect."
