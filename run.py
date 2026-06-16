"""Podcast render server entrypoint.

1. Ensures PODCAST_TOKEN is set (generates one if not).
2. Starts uvicorn on 0.0.0.0:PORT and prints the local URL + TOKEN.
3. If NGROK_AUTHTOKEN is set AND pyngrok is installed, also opens a public
   ngrok tunnel automatically.  This is OPTIONAL — the default is local-only.
4. The Mac user can expose the server manually by running:
       ngrok http <PORT>
   in a separate terminal and sharing the ngrok URL + TOKEN with the caller.

Environment variables
---------------------
PORT              Listening port (default 8000).
PODCAST_TOKEN     Bearer token for /render + /transcribe.  Auto-generated
                  as secrets.token_hex(16) if unset or empty.
NGROK_AUTHTOKEN   Optional. If set and pyngrok is installed, opens an auto
                  tunnel.  If unset (the default), the server runs locally;
                  run ngrok manually in another terminal.
PODCAST_DEVICE    Force TTS device: "cpu", "mps", or "cuda".  Default: auto
                  (mps on Apple Silicon, cpu otherwise).  Set to "cpu" if MPS
                  causes issues despite PYTORCH_ENABLE_MPS_FALLBACK=1.
"""
from __future__ import annotations

# MPS op fallback — must be set BEFORE torch is imported anywhere in this process.
# Fixes: NotImplementedError: Output channels > 65536 not supported at the MPS device
# (resemble-ai/chatterbox#147).  Set here defensively because torch loads lazily on
# first /render — backend_chatterbox.py also sets this, but belt-and-suspenders.
import os
os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")

import secrets
import sys

# ── token setup ───────────────────────────────────────────────────────────────

token = os.environ.get("PODCAST_TOKEN", "").strip()
if not token:
    token = secrets.token_hex(16)
    os.environ["PODCAST_TOKEN"] = token

port = int(os.environ.get("PORT", "8000"))

# ── ngrok tunnel (optional — only when NGROK_AUTHTOKEN is set) ────────────────

public_url: str | None = None

ngrok_auth = os.environ.get("NGROK_AUTHTOKEN", "").strip()
if ngrok_auth:
    try:
        from pyngrok import ngrok, conf  # noqa: PLC0415
        conf.get_default().auth_token = ngrok_auth
        tunnel = ngrok.connect(port, "http")
        public_url = tunnel.public_url
        # Prefer https
        if public_url.startswith("http://"):
            public_url = "https://" + public_url[len("http://"):]
    except ImportError:
        print("[run.py] pyngrok not installed; running locally — start ngrok manually.", file=sys.stderr)
    except Exception as exc:  # noqa: BLE001
        print(f"[run.py] WARNING: ngrok tunnel failed: {exc}", file=sys.stderr)
        print("[run.py] Falling back to local URL only.", file=sys.stderr)

local_url = f"http://localhost:{port}"
url = public_url if public_url else local_url

# ── print connection info ─────────────────────────────────────────────────────

print()
print("=" * 60)
print("  Podcast Render Server")
print("=" * 60)
print(f"  LOCAL URL: {local_url}")
if public_url:
    print(f"  PUBLIC URL (ngrok): {public_url}")
print(f"  TOKEN:     {token}")
print()
if public_url:
    print("  Send the PUBLIC URL and TOKEN to your caller.")
else:
    print("  Server is running LOCALLY.")
    print("  To expose this to a remote caller, in ANOTHER terminal run:")
    print(f"    ngrok http {port}")
    print("  — then send the caller the ngrok https URL and the TOKEN above.")
print("=" * 60)
print()
print("  Example render call (replace URL with ngrok URL if using tunnel):")
print(f'  curl -s -X POST "{local_url}/render" \\')
print(f'    -H "Authorization: Bearer {token}" \\')
print(f'    -F "script={{\\"host_mode\\":\\"single\\",\\"voice\\":\\"narrator\\",\\"turns\\":[{{\\"voice\\":\\"narrator\\",\\"line\\":\\"Hello world.\\"}}]}}" \\')
print(f'    --output out.mp3')
print()
print("  Device override (if MPS causes issues):")
print("    PODCAST_DEVICE=cpu python run.py")
print()
sys.stdout.flush()

# ── start uvicorn ─────────────────────────────────────────────────────────────

import uvicorn  # noqa: E402 — import after env is set so server.py reads the token

uvicorn.run(
    "server:app",
    host="0.0.0.0",
    port=port,
    log_level="info",
)
