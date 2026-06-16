#!/bin/bash
# start.command — Launch the Podcast Render Server.
# Double-click this file in Finder to start the server.
#
# What it does:
#   1. Activates the Python virtual environment created by setup.command
#   2. Starts the server (run.py), which:
#      - Binds an HTTP server on 0.0.0.0:PORT (default 8000)
#      - Prints the local URL + bearer token
#
# ⚠️  KEEP THIS WINDOW OPEN while rendering.
#     Closing it stops the server and frees memory.
#
# When you see "LOCAL URL: ..." and "TOKEN: ..." printed below:
#   - To share with a remote caller, open a NEW terminal and run:
#       ngrok http 8000
#   - Send the caller the ngrok URL + TOKEN shown below.

set -euo pipefail

# ── Move to the directory that contains this script ───────────────────────────

cd "$(dirname "$0")"

# ── Sanity checks ─────────────────────────────────────────────────────────────

if [[ ! -d .venv ]]; then
    echo "❌  Virtual environment not found."
    echo "    Please double-click setup.command first, then try again."
    exit 1
fi

if [[ ! -f run.py ]]; then
    echo "❌  run.py not found in $(pwd)."
    echo "    Make sure the full podcast-server package is present."
    exit 1
fi

# ── Launch ────────────────────────────────────────────────────────────────────

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Podcast Render Server starting…"
echo "  This starts the LOCAL server. Leave this window open while rendering."
echo "  To share with a remote caller, open a NEW terminal and run:"
echo "    ngrok http 8000"
echo "  — then send the caller the ngrok URL + the TOKEN shown below."
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

# exec replaces this shell process with python — Terminal window title stays clean
exec .venv/bin/python run.py
