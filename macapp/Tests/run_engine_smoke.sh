#!/bin/sh
# Compiles the app's engine client with the smoke test and runs it against the real
# engine protocol (tests/fake_engine.py). Works on macOS (Xcode command-line tools) or Linux.
#
#   macapp/Tests/run_engine_smoke.sh [python]
set -eu
HERE=$(cd "$(dirname "$0")" && pwd)
REPO=$(cd "$HERE/../.." && pwd)
PYTHON=${1:-python3}
SWIFTC=${SWIFTC:-swiftc}
OUT=$(mktemp -d)/EngineSmoke
"$SWIFTC" -swift-version 5 -O \
    "$REPO/macapp/Reeper/Core/JSONValue.swift" \
    "$REPO/macapp/Reeper/Core/EngineModels.swift" \
    "$REPO/macapp/Reeper/Core/EngineClient.swift" \
    "$HERE/EngineSmoke/main.swift" \
    -o "$OUT"
"$OUT" "$PYTHON" "$REPO"
