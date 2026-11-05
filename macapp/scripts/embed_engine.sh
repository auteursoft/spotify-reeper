#!/bin/bash
# Xcode build phase "Embed Python engine": copies macapp/build/{python,engine,models} into
# Reeper.app/Contents/Resources and signs the Python runtime for this build.
set -euo pipefail

BUILD="$SRCROOT/build"
DEST="$TARGET_BUILD_DIR/$UNLOCALIZED_RESOURCES_FOLDER_PATH"

if [ ! -x "$BUILD/python/bin/python3" ]; then
    if [ "$CONFIGURATION" = "Debug" ]; then
        echo "warning: No bundled Python. Debug runs use REEPER_PYTHON and REEPER_SOURCE from the scheme; run macapp/scripts/build_python.sh to bundle the engine."
        exit 0
    fi
    echo "error: The Python engine isn't built. Run: macapp/scripts/build_python.sh appstore (or direct)"
    exit 1
fi

echo "Embedding the $(cat "$BUILD/EDITION" 2>/dev/null || echo unknown) edition engine"
rsync -a --delete "$BUILD/python/" "$DEST/python/"
rsync -a --delete "$BUILD/engine/" "$DEST/engine/"
if [ -d "$BUILD/models" ]; then
    rsync -a --delete "$BUILD/models/" "$DEST/models/"
else
    rm -rf "$DEST/models"
fi

IDENTITY="${EXPANDED_CODE_SIGN_IDENTITY:-}"
[ -n "$IDENTITY" ] || IDENTITY="-"   # ad-hoc when not signing

# Only a sandboxed app may launch a helper that inherits its sandbox.
HELPER=""
if [ -n "${CODE_SIGN_ENTITLEMENTS:-}" ] && grep -q "com.apple.security.app-sandbox" "$SRCROOT/$CODE_SIGN_ENTITLEMENTS"; then
    HELPER="$SRCROOT/Reeper/Resources/PythonHelper.entitlements"
fi
"$SRCROOT/scripts/sign_nested.sh" "$DEST/python" "$IDENTITY" "$HELPER"
