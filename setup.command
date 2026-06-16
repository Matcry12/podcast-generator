#!/bin/bash
# setup.command — First-time setup for the Podcast Render Server.
# Double-click this file in Finder to run.
#
# What it does:
#   1. Checks for Homebrew (brew)
#   2. Installs ffmpeg via brew (needed for MP3 encoding)
#   3. Installs espeak-ng via brew (needed by the Kokoro TTS engine)
#   4. Installs uv (fast Python package manager)
#   5. Creates a Python 3.11 virtual environment and installs dependencies
#   6. Runs a fast dummy-backend self-test (pipeline + ffmpeg sanity check)
#   7. Pre-downloads models + runs a DUAL real-engine self-test:
#      both Chatterbox and Kokoro do a mini render so any coexistence
#      problems are caught here, not mid-use.
#
# If anything goes wrong, the script will stop and show an ❌ error.
# Re-run after fixing the issue — the script is safe to run multiple times.

set -euo pipefail

# ── Helpers ───────────────────────────────────────────────────────────────────

GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
BOLD='\033[1m'
NC='\033[0m' # No Color

info()    { echo -e "${BOLD}[setup]${NC} $*"; }
success() { echo -e "${GREEN}✅ $*${NC}"; }
warn()    { echo -e "${YELLOW}⚠️  $*${NC}"; }
fail()    { echo -e "${RED}❌ $*${NC}"; exit 1; }

# ── 0. Move to the directory that contains this script ────────────────────────

cd "$(dirname "$0")"
info "Working directory: $(pwd)"

# ── 1. Homebrew ───────────────────────────────────────────────────────────────

info "Checking for Homebrew..."
if ! command -v brew &>/dev/null; then
    echo ""
    echo -e "${RED}Homebrew is not installed.${NC}"
    echo ""
    echo "Please install it first by opening Terminal and running:"
    echo ""
    echo '    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"'
    echo ""
    echo "Then double-click setup.command again."
    exit 1
fi
success "Homebrew found: $(brew --version | head -1)"

# ── 2. ffmpeg ─────────────────────────────────────────────────────────────────

info "Checking for ffmpeg (needed for MP3 encoding)..."
if ! command -v ffmpeg &>/dev/null; then
    info "Installing ffmpeg via Homebrew (this may take a few minutes)..."
    brew install ffmpeg
    success "ffmpeg installed."
else
    success "ffmpeg already installed: $(ffmpeg -version 2>&1 | head -1)"
fi

# ── 2b. espeak-ng ─────────────────────────────────────────────────────────────

info "Checking for espeak-ng (needed by the Kokoro TTS engine)..."
if ! command -v espeak-ng &>/dev/null; then
    info "Installing espeak-ng via Homebrew..."
    brew install espeak-ng
    success "espeak-ng installed."
else
    success "espeak-ng already installed: $(espeak-ng --version 2>&1 | head -1)"
fi

# ── 3. uv (fast Python package manager) ──────────────────────────────────────

info "Checking for uv (Python package manager)..."
if ! command -v uv &>/dev/null; then
    info "Installing uv..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    # uv installs to ~/.cargo/bin or ~/.local/bin — add both to PATH for this session
    export PATH="$HOME/.cargo/bin:$HOME/.local/bin:$PATH"
    if ! command -v uv &>/dev/null; then
        fail "uv was installed but is not on PATH. Please restart Terminal and run setup.command again."
    fi
    success "uv installed."
else
    success "uv already installed: $(uv --version)"
fi

# ── 4. Python virtual environment + dependencies ──────────────────────────────

# Python 3.11 is required: Kokoro TTS requires Python <3.13, and Chatterbox is
# tested and pinned against 3.11. Using one shared venv avoids duplicate
# torch/transformers installs and lets the dual self-test surface any runtime
# coexistence problems between the two engines early.
info "Creating Python 3.11 virtual environment in .venv/ ..."
info "(uv will auto-download Python 3.11 if not already present — first run only)"
uv venv --python 3.11 .venv

info "Installing Python dependencies from requirements.txt ..."
if [[ ! -f requirements.txt ]]; then
    fail "requirements.txt not found in $(pwd). Make sure the full podcast-server package is present."
fi
uv pip install -r requirements.txt
success "Python dependencies installed."

# ── 5. Self-test ──────────────────────────────────────────────────────────────

echo ""
info "Running self-test with the dummy backend..."
echo "    (This confirms Python environment, ffmpeg, and the render pipeline work.)"
echo ""

SELFTEST_SCRIPT=$(cat <<'PYEOF'
import sys, json, tempfile, os
from pathlib import Path

# Make sure the podcast package is importable from the script's directory
sys.path.insert(0, str(Path(__file__).parent))

try:
    from podcast.core import synthesize_podcast, Script, Turn
except ImportError as e:
    print(f"FAIL: Cannot import podcast package: {e}", file=sys.stderr)
    sys.exit(1)

# Build a minimal two-turn PRD-shape script
script = Script(
    turns=[
        Turn(speaker="A", text="Hello. This is a self test."),
        Turn(speaker="B", text="Pipeline confirmed working."),
    ],
    voice_map={"A": "dummy", "B": "dummy"},
)

with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
    out_path = Path(tmp.name)

try:
    result = synthesize_podcast(
        script,
        out_path,
        backend="dummy",
        voice_map={"A": "dummy", "B": "dummy"},
    )
    size = out_path.stat().st_size
    print(f"OK: rendered {result.turns} turns → {size} bytes at {out_path}")
except Exception as e:
    print(f"FAIL: {e}", file=sys.stderr)
    sys.exit(1)
finally:
    try:
        out_path.unlink()
    except Exception:
        pass
PYEOF
)

if .venv/bin/python - <<< "$SELFTEST_SCRIPT"; then
    success "Self-test passed — pipeline and ffmpeg are working correctly."
else
    fail "Self-test failed. Check the error above. Common fix: ensure ffmpeg is installed and requirements.txt was fully installed."
fi

# ── 6. Pre-download models + DUAL real-engine self-test ───────────────────────

echo ""
info "Pre-downloading models + running dual real-engine self-test..."
echo "    This downloads several GB of model weights (one-time)."
echo "    Both Chatterbox and Kokoro will each render a short test line so any"
echo "    runtime coexistence problem is caught NOW — not in the middle of a job."
echo "    This can take several minutes depending on your connection."
echo ""

DUAL_TEST_SCRIPT=$(cat <<'PYEOF'
import sys, tempfile, os
from pathlib import Path

# Ensure the podcast package is importable from the script's directory.
sys.path.insert(0, str(Path(__file__).parent))

# ── Pick the device (matches what the server backends use) ────────────────────
try:
    import torch
    if torch.backends.mps.is_available():
        device = "mps"
    elif torch.cuda.is_available():
        device = "cuda"
    else:
        device = "cpu"
except Exception:
    device = "cpu"

failed_engines = []

# ── Whisper pre-download ──────────────────────────────────────────────────────
try:
    print("  -> Whisper (small, cpu) ...", flush=True)
    import whisper
    _w = whisper.load_model("small", device="cpu")
    del _w
    print("     Whisper model ready.", flush=True)
except Exception as e:
    print(f"     WARN: could not pre-download Whisper: {e}", file=sys.stderr, flush=True)

# ── Chatterbox real mini-render ───────────────────────────────────────────────
print(f"\n  -> Chatterbox real-engine test (device={device}) ...", flush=True)
try:
    from podcast.core import synthesize_podcast, Script, Turn

    cb_script = Script(
        turns=[Turn(speaker="A", text="Chatterbox engine check.")],
        voice_map={"A": "narrator"},
    )
    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
        cb_out = Path(tmp.name)
    try:
        result = synthesize_podcast(
            cb_script, cb_out, backend="chatterbox",
            voice_map={"A": "narrator"},
        )
        size = cb_out.stat().st_size
        if size == 0:
            raise RuntimeError("Output file is empty")
        print(f"     ✅  Chatterbox OK — {result.turns} turn(s), {size:,} bytes", flush=True)
    finally:
        try:
            cb_out.unlink()
        except Exception:
            pass
except Exception as e:
    failed_engines.append("chatterbox")
    print(f"     ❌  Chatterbox FAILED: {e}", file=sys.stderr, flush=True)

# ── Kokoro real mini-render ───────────────────────────────────────────────────
print("\n  -> Kokoro real-engine test (voice=af_heart) ...", flush=True)
try:
    from podcast.core import synthesize_podcast, Script, Turn

    ko_script = Script(
        turns=[Turn(speaker="A", text="Kokoro engine check.")],
        voice_map={"A": "af_heart"},
    )
    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
        ko_out = Path(tmp.name)
    try:
        result = synthesize_podcast(
            ko_script, ko_out, backend="kokoro",
            voice_map={"A": "af_heart"},
        )
        size = ko_out.stat().st_size
        if size == 0:
            raise RuntimeError("Output file is empty")
        print(f"     ✅  Kokoro OK — {result.turns} turn(s), {size:,} bytes", flush=True)
    finally:
        try:
            ko_out.unlink()
        except Exception:
            pass
except Exception as e:
    failed_engines.append("kokoro")
    print(f"     ❌  Kokoro FAILED: {e}", file=sys.stderr, flush=True)

# ── Summary ───────────────────────────────────────────────────────────────────
print("", flush=True)
if not failed_engines:
    print("Both engines passed. Your first render will be fast.", flush=True)
    sys.exit(0)
else:
    working = [e for e in ("chatterbox", "kokoro") if e not in failed_engines]
    print(
        f"WARNING: {', '.join(failed_engines)} failed the self-test.\n"
        f"Working engine(s): {', '.join(working) if working else 'NONE'}.\n"
        "You can still use the working engine(s). Re-run setup.command after\n"
        "fixing the issue (check errors above) to re-validate.",
        file=sys.stderr, flush=True,
    )
    sys.exit(2)
PYEOF
)

if .venv/bin/python - <<< "$DUAL_TEST_SCRIPT"; then
    success "All models downloaded and both engines verified."
else
    EXIT_CODE=$?
    if [[ $EXIT_CODE -eq 2 ]]; then
        warn "One or more engines failed their self-test (see ❌ above)."
        warn "The working engine(s) can still be used. Re-run setup.command"
        warn "after addressing the error to re-validate the failing engine."
    else
        warn "Model download/self-test did not fully complete (e.g. no internet)."
        warn "Models will download automatically on the first real render instead"
        warn "— a one-time delay. Re-run setup.command later to pre-download."
    fi
fi

# ── Done ──────────────────────────────────────────────────────────────────────

echo ""
echo -e "${GREEN}${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${GREEN}${BOLD}  Setup complete!${NC}"
echo -e "${GREEN}${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""
echo "  Next step: Double-click  start.command  to launch the render server."
echo ""
echo -e "${YELLOW}  Note: ngrok is NOT installed by this script.${NC}"
echo "  If you want a public URL so a remote caller can reach this server:"
echo "    1. Install ngrok and log in yourself: https://ngrok.com/download"
echo "    2. After start.command is running, open a NEW terminal and run:"
echo "         ngrok http 8000"
echo ""
