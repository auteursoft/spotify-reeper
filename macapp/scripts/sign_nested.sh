#!/bin/bash
# Signs every Mach-O file under a folder, inside out: libraries first, then executables.
# Xcode only signs the app itself, so the bundled Python runtime is signed here.
#
#   sign_nested.sh <folder> <identity> [helper-entitlements] [--timestamp]
#
# The python3 executable gets the helper entitlements (sandbox + inherit) when given;
# leave them out for an unsandboxed build, where a helper that inherits a sandbox would crash.
set -euo pipefail

DIR=$1
IDENTITY=$2
HELPER_ENTITLEMENTS=${3:-}
TIMESTAMP=--timestamp=none
[ "${4:-}" = "--timestamp" ] && TIMESTAMP=--timestamp

[ -d "$DIR" ] || { echo "error: $DIR doesn't exist" >&2; exit 1; }

is_macho() { file -b "$1" | grep -q "Mach-O"; }

count=0
# Libraries and extension modules.
while IFS= read -r -d '' f; do
    if is_macho "$f"; then
        codesign --force --sign "$IDENTITY" --options runtime "$TIMESTAMP" "$f"
        count=$((count + 1))
    fi
done < <(find "$DIR" -type f \( -name "*.so" -o -name "*.dylib" \) -print0)

# Executables (python3.13 and any other binaries).
while IFS= read -r -d '' f; do
    case "$f" in *.so|*.dylib) continue ;; esac
    if is_macho "$f"; then
        if [ -n "$HELPER_ENTITLEMENTS" ]; then
            codesign --force --sign "$IDENTITY" --options runtime "$TIMESTAMP" \
                --entitlements "$HELPER_ENTITLEMENTS" "$f"
        else
            codesign --force --sign "$IDENTITY" --options runtime "$TIMESTAMP" "$f"
        fi
        count=$((count + 1))
    fi
done < <(find "$DIR" -type f -perm -u+x -print0)

echo "Signed $count nested binaries in $DIR"
