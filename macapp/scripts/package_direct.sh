#!/bin/bash
# Signs Reeper.app with your Developer ID, has Apple notarize it, and makes a .dmg you can
# share directly (no App Store). Works with either edition; the audio edition is the usual
# choice here. See macapp/README.md.
#
#   macapp/scripts/package_direct.sh <path/to/Reeper.app>
#
# Environment:
#   DEVELOPER_ID     e.g. "Developer ID Application: Your Name (TEAMID)"
#   NOTARY_PROFILE   a notarytool keychain profile, created once with:
#                    xcrun notarytool store-credentials reeper-notary --apple-id you@example.com --team-id TEAMID
set -euo pipefail

APP_IN=${1:-}
[ -d "$APP_IN" ] || { echo "usage: $0 <Reeper.app>" >&2; exit 2; }
: "${DEVELOPER_ID:?set DEVELOPER_ID to your Developer ID Application identity}"
: "${NOTARY_PROFILE:?set NOTARY_PROFILE to your notarytool keychain profile name}"

HERE=$(cd "$(dirname "$0")" && pwd)
RES="$HERE/../Reeper/Resources"
OUT="$HERE/../build/dist-direct"
rm -rf "$OUT"
mkdir -p "$OUT"
APP="$OUT/Reeper.app"
ditto "$APP_IN" "$APP"
rm -f "$APP/Contents/embedded.provisionprofile"

echo "==> Signing"
"$HERE/sign_nested.sh" "$APP/Contents/Resources/python" "$DEVELOPER_ID" "$RES/PythonHelper.entitlements" --timestamp
codesign --force --sign "$DEVELOPER_ID" --options runtime --timestamp \
    --entitlements "$RES/Reeper.entitlements" "$APP"
codesign --verify --deep --strict --verbose=2 "$APP"

echo "==> Notarizing (this takes a few minutes)"
ditto -c -k --keepParent "$APP" "$OUT/Reeper.zip"
xcrun notarytool submit "$OUT/Reeper.zip" --keychain-profile "$NOTARY_PROFILE" --wait
xcrun stapler staple "$APP"
rm "$OUT/Reeper.zip"

echo "==> Making the disk image"
STAGE=$(mktemp -d)
ditto "$APP" "$STAGE/Reeper.app"
ln -s /Applications "$STAGE/Applications"
hdiutil create -volname Reeper -srcfolder "$STAGE" -ov -format UDZO "$OUT/Reeper.dmg" >/dev/null
rm -rf "$STAGE"
codesign --sign "$DEVELOPER_ID" --timestamp "$OUT/Reeper.dmg"
xcrun notarytool submit "$OUT/Reeper.dmg" --keychain-profile "$NOTARY_PROFILE" --wait
xcrun stapler staple "$OUT/Reeper.dmg"
spctl --assess --type open --context context:primary-signature --verbose "$OUT/Reeper.dmg" || true

echo "==> Done: $OUT/Reeper.dmg"
