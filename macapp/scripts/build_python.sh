#!/bin/bash
# Builds the Python runtime and engine that ship inside Reeper.app, into macapp/build/.
# Run on an Apple Silicon Mac, before building the app in Xcode.
#
#   macapp/scripts/build_python.sh appstore   # Mac App Store edition: judges songs by Last.fm
#                                             # tags only. No Essentia (AGPL-3.0) or Deezer.
#   macapp/scripts/build_python.sh direct     # Direct-download edition: adds audio analysis
#                                             # (Essentia + models). Distribute under AGPL terms.
#
# Set PBS_URL to pin a specific python-build-standalone archive; by default the latest
# CPython 3.13 "install_only_stripped" build for aarch64-apple-darwin is used.
set -euo pipefail

EDITION=${1:-}
case "$EDITION" in
    appstore|direct) ;;
    *) echo "usage: $0 appstore|direct" >&2; exit 2 ;;
esac
[ "$(uname -s)" = "Darwin" ] || { echo "error: run this on a Mac" >&2; exit 1; }
[ "$(uname -m)" = "arm64" ] || { echo "error: run this on an Apple Silicon Mac" >&2; exit 1; }

HERE=$(cd "$(dirname "$0")" && pwd)
REPO=$(cd "$HERE/../.." && pwd)
OUT="$REPO/macapp/build"
PY_MINOR=3.13
ESSENTIA="essentia-tensorflow==2.1b6.dev1389"
SKLEARN="scikit-learn==1.9.1"

echo "==> Building the $EDITION edition into $OUT"
rm -rf "$OUT/python" "$OUT/engine" "$OUT/models" "$OUT/EDITION"
mkdir -p "$OUT"

# 1. A relocatable CPython from python-build-standalone.
if [ -z "${PBS_URL:-}" ]; then
    PBS_URL=$(curl -fsSL https://api.github.com/repos/astral-sh/python-build-standalone/releases/latest \
        | grep -o "https://[^\"]*cpython-${PY_MINOR}\.[0-9]*+[0-9]*-aarch64-apple-darwin-install_only_stripped\.tar\.gz" \
        | head -1)
fi
[ -n "$PBS_URL" ] || { echo "error: couldn't find a CPython $PY_MINOR build; set PBS_URL" >&2; exit 1; }
echo "==> Downloading $PBS_URL"
curl -fL --progress-bar "$PBS_URL" -o "$OUT/python.tar.gz"
tar -xzf "$OUT/python.tar.gz" -C "$OUT"
rm "$OUT/python.tar.gz"
PY="$OUT/python/bin/python3"
"$PY" --version

# 2. Dependencies.
echo "==> Installing dependencies"
"$PY" -m pip install --quiet --no-compile --disable-pip-version-check \
    -r "$REPO/requirements.txt" "$SKLEARN"
if [ "$EDITION" = direct ]; then
    "$PY" -m pip install --quiet --no-compile --disable-pip-version-check "$ESSENTIA"
    touch "$OUT/python/.reeper-audio"   # tells the app to show Essentia's credits
fi

# 3. Trim what the app never uses (and Tcl/Tk, which the App Store dislikes).
echo "==> Trimming"
LIB="$OUT/python/lib/python$PY_MINOR"
rm -rf "$LIB/test" "$LIB/idlelib" "$LIB/tkinter" "$LIB/turtledemo" "$LIB/ensurepip" "$LIB"/lib2to3 \
       "$LIB"/lib-dynload/_tkinter*.so "$LIB"/config-* \
       "$OUT"/python/lib/libtcl* "$OUT"/python/lib/libtk* "$OUT"/python/lib/tcl* "$OUT"/python/lib/tk* \
       "$OUT"/python/lib/itcl* "$OUT"/python/lib/pkgconfig "$OUT/python/include" "$OUT/python/share"
"$PY" -m pip uninstall --quiet --yes pip setuptools wheel >/dev/null 2>&1 || true
rm -f "$OUT"/python/bin/pip* "$OUT"/python/bin/idle* "$OUT"/python/bin/pydoc* "$OUT"/python/bin/2to3*
find "$LIB/site-packages" -type d -name tests -prune -exec rm -rf {} +
find "$OUT/python" -name "*.a" -delete
find "$OUT/python" -type d -name __pycache__ -prune -exec rm -rf {} +

# 4. The engine's own code.
echo "==> Copying the engine"
mkdir -p "$OUT/engine"
cp "$REPO/cleaner.py" "$OUT/engine/"
for pkg in mood reeper_engine; do
    rsync -a --exclude __pycache__ "$REPO/$pkg" "$OUT/engine/"
done

# 5. Precompile: the signed app bundle is read-only, so Python can't cache bytecode later.
echo "==> Precompiling"
"$PY" -m compileall -q -j 0 --invalidation-mode unchecked-hash "$OUT/python/lib" "$OUT/engine" >/dev/null

# 6. Audio models (direct edition only).
if [ "$EDITION" = direct ]; then
    echo "==> Downloading audio models"
    PYTHONPATH="$OUT/engine" "$PY" -s -c "
import sys
from mood.audio import setup_models
from mood.net import Http
sys.exit(1 if setup_models('$OUT/models', Http(min_interval=0)) else 0)"
fi

# 7. Smoke test: the engine starts and answers.
echo "==> Checking the engine starts"
REPLY=$(printf '{"id":"1","method":"models.status","params":{}}\n{"method":"shutdown"}\n' \
    | PYTHONPATH="$OUT/engine" PYTHONDONTWRITEBYTECODE=1 "$PY" -s -m reeper_engine 2>/dev/null || true)
echo "$REPLY" | grep -q '"type": "ready"' || { echo "error: the engine didn't start:" >&2; echo "$REPLY" >&2; exit 1; }
if [ "$EDITION" = direct ]; then
    PYTHONPATH="$OUT/engine" "$PY" -s -c "from mood.audio import AudioAnalyzer; AudioAnalyzer('$OUT/models')" \
        || { echo "error: the audio models don't load" >&2; exit 1; }
fi

echo "$EDITION" > "$OUT/EDITION"
echo "==> Done: $(du -sh "$OUT/python" | cut -f1) runtime, $EDITION edition."
echo "    Now build Reeper in Xcode; the 'Embed Python engine' phase copies it into the app."
