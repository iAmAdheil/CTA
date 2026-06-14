#!/usr/bin/env bash
# harness-history.sh — browse recorded harness runs in your web browser.
#
#   bash ~/agent-harness/scripts/harness-history.sh
#
# Post-mortem only: this starts a LOCAL, on-demand web server rooted at the
# history store (~/.agent-harness-history by default), copies the repo's viewer
# assets in, rebuilds the index, and opens your browser. Nothing runs in the
# background — Ctrl-C stops the server when you're done browsing.
#
# Env:
#   HARNESS_HISTORY_DIR   history store location (default: ~/.agent-harness-history)
#   HARNESS_HISTORY_PORT  port to serve on (default: a free port near 8787)
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HARNESS_ROOT="$(cd "$HERE/.." && pwd)"
HISTORY_DIR="${HARNESS_HISTORY_DIR:-$HOME/.agent-harness-history}"

# Pick a python for the http server + the index build. Prefer python3 (+the repo
# on PYTHONPATH so `-m harness.history` resolves); fall back to the conda env.
if PYTHONPATH="$HARNESS_ROOT" python3 -c 'import harness.history' >/dev/null 2>&1; then
  PY3=(python3); export PYTHONPATH="$HARNESS_ROOT${PYTHONPATH:+:$PYTHONPATH}"
elif command -v conda >/dev/null 2>&1; then
  PY3=(conda run --no-capture-output -n "${HARNESS_CONDA_ENV:-harness}" python)
else
  echo "Need python3 (or conda) to serve the history viewer." >&2
  exit 1
fi

if [ ! -d "$HISTORY_DIR" ] || [ -z "$(ls -A "$HISTORY_DIR" 2>/dev/null)" ]; then
  echo "No history yet at $HISTORY_DIR"
  echo "Launch the harness (scripts/harness-up.sh) in a project and a run will be recorded here."
  exit 0
fi

# 1. Stage the viewer assets inside the docroot (the static page can only reach
#    the data when it's served from the same root).
mkdir -p "$HISTORY_DIR/_viewer"
cp -f "$HARNESS_ROOT"/viewer/* "$HISTORY_DIR/_viewer/" 2>/dev/null || {
  echo "! viewer assets missing at $HARNESS_ROOT/viewer" >&2; exit 1; }

# 2. (Re)build the index the landing page reads.
"${PY3[@]}" -m harness.history build-index

# 3. Resolve a port (explicit override, else a free one near 8787).
PORT="${HARNESS_HISTORY_PORT:-}"
if [ -z "$PORT" ]; then
  PORT="$("${PY3[@]}" - <<'PY'
import socket
for p in range(8787, 8807):
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", p)); print(p); break
    except OSError:
        continue
    finally:
        s.close()
else:
    s = socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()
PY
)"
fi

URL="http://localhost:$PORT/_viewer/"
echo "Serving $HISTORY_DIR"
echo "  → $URL"
echo "  (Ctrl-C to stop)"
case "${OSTYPE:-}" in
  darwin*) (sleep 0.6; open "$URL") >/dev/null 2>&1 & ;;
  *)       (sleep 0.6; xdg-open "$URL" >/dev/null 2>&1) & ;;
esac

exec "${PY3[@]}" -m http.server "$PORT" --directory "$HISTORY_DIR"
