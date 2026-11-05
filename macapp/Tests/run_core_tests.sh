#!/bin/sh
# Compiles the app's platform-independent core (macapp/Reeper/Core) with two test programs
# and runs them. Works on macOS (Xcode command-line tools) or Linux (Swift toolchain).
#
#   macapp/Tests/run_core_tests.sh [python]
#
# AuthCoreTest: PKCE (RFC 7636 vector), Spotify sign-in URLs, token parsing.
# EngineSmoke:  the engine client against the real engine protocol (tests/fake_engine.py).
set -eu
HERE=$(cd "$(dirname "$0")" && pwd)
REPO=$(cd "$HERE/../.." && pwd)
PYTHON=${1:-python3}
SWIFTC=${SWIFTC:-swiftc}
OUT=$(mktemp -d)
CORE="$REPO/macapp/Reeper/Core"

echo "== AuthCoreTest"
"$SWIFTC" -swift-version 5 "$CORE"/*.swift "$HERE/AuthCoreTest/main.swift" -o "$OUT/AuthCoreTest"
"$OUT/AuthCoreTest"

echo "== EngineSmoke"
"$SWIFTC" -swift-version 5 -O "$CORE"/*.swift "$HERE/EngineSmoke/main.swift" -o "$OUT/EngineSmoke"
"$OUT/EngineSmoke" "$PYTHON" "$REPO"
