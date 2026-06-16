#!/usr/bin/env python3
# call_remote.py — Caller-side client for the Podcast Render Server.
# Requires: requests  (pip install requests)
#
# Usage:
#   python call_remote.py render     --url URL --token TOKEN --script script.json --out out.mp3 \
#                                    [--backend chatterbox|kokoro|dummy] \
#                                    [--voice ref.wav]        # chatterbox voice-cloning only
#                                    [--voice-name af_heart]  # kokoro preset voice only
#   python call_remote.py transcribe --url URL --token TOKEN --mp3 file.mp3 [--script s.json] --out result.json
#
# Engines:
#   chatterbox  — voice cloning from a .wav clip (--voice); slower, GPU-intensive.
#   kokoro      — fast preset voices (--voice-name, e.g. af_heart / af_sarah / am_adam);
#                 no clip cloning. Default preset: af_heart.
#   dummy       — silent MP3 placeholder, for testing the pipeline only.
#
# The URL and TOKEN are printed by start.command on the Mac running the server.

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

try:
    import requests
except ImportError:
    sys.exit(
        "ERROR: 'requests' is not installed.\n"
        "Install it with:  pip install requests"
    )


# ── Shared helpers ────────────────────────────────────────────────────────────

def _auth_header(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _check_response(resp: "requests.Response", context: str) -> None:
    """Raise a clear, human-readable error on non-2xx responses."""
    if resp.status_code == 401:
        sys.exit(
            f"ERROR [{context}]: 401 Unauthorized — wrong or missing bearer token.\n"
            f"Check that --token matches the TOKEN printed by start.command."
        )
    if resp.status_code == 404:
        sys.exit(
            f"ERROR [{context}]: 404 Not Found — is the URL correct?\n"
            f"Received: {resp.text[:200]}"
        )
    if 400 <= resp.status_code < 500:
        sys.exit(
            f"ERROR [{context}]: {resp.status_code} Client Error\n"
            f"{resp.text[:400]}"
        )
    if resp.status_code >= 500:
        sys.exit(
            f"ERROR [{context}]: {resp.status_code} Server Error\n"
            f"{resp.text[:400]}"
        )


# ── Subcommand: render ────────────────────────────────────────────────────────

def cmd_render(args: argparse.Namespace) -> None:
    script_path = Path(args.script)
    if not script_path.exists():
        sys.exit(f"ERROR: Script file not found: {script_path}")

    # /render is multipart/form-data:
    #   script     — string field (raw JSON text of the script file)
    #   backend    — string field (optional; "chatterbox", "kokoro", or "dummy")
    #   voice      — file field   (optional .wav clip; chatterbox voice-cloning only)
    #   voice_name — string field (optional preset name; kokoro only, e.g. "af_heart")
    script_text = script_path.read_text()

    url = args.url.rstrip("/") + "/render"
    print(f"[render] POST {url}")
    print(f"[render] Script: {script_path}  ({script_path.stat().st_size} bytes)")

    # Build form data fields
    data: dict = {"script": script_text}
    backend = args.backend or None
    if backend:
        data["backend"] = backend
        print(f"[render] Backend: {backend}")

    # voice_name — kokoro preset voice (ignored by chatterbox)
    if args.voice_name:
        if backend and backend == "chatterbox":
            print(
                "[render] NOTE: --voice-name is ignored by the chatterbox backend "
                "(it uses --voice for clip cloning). "
                "Pass --backend kokoro to use preset voices."
            )
        else:
            data["voice_name"] = args.voice_name
            print(f"[render] Voice name (kokoro preset): {args.voice_name}")

    # Build file attachments (voice clip — chatterbox cloning only)
    files: dict = {}
    voice_fh = None
    if args.voice:
        if backend and backend == "kokoro":
            print(
                "[render] NOTE: --voice clip files are ignored by the kokoro backend "
                "(kokoro uses preset voices via --voice-name). "
                "Pass --backend chatterbox to use voice cloning."
            )
        voice_path = Path(args.voice)
        if not voice_path.exists():
            sys.exit(f"ERROR: Voice reference file not found: {voice_path}")
        voice_fh = voice_path.open("rb")
        files["voice"] = (voice_path.name, voice_fh, "audio/wav")
        print(f"[render] Voice ref: {voice_path}  ({voice_path.stat().st_size:,} bytes)")
    elif not args.voice_name:
        print("[render] Voice ref: (none — server will use bundled narrator.wav)")

    try:
        resp = requests.post(
            url,
            headers=_auth_header(args.token),
            data=data,
            files=files if files else None,
            stream=True,
            timeout=600,  # renders can take a while
        )
    except requests.exceptions.ConnectionError as e:
        sys.exit(f"ERROR: Could not connect to {url}\n{e}")
    except requests.exceptions.Timeout:
        sys.exit(f"ERROR: Request timed out after 600 s — render is still running on the server.")
    finally:
        if voice_fh:
            voice_fh.close()

    _check_response(resp, "render")

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    total = 0
    with out_path.open("wb") as f:
        for chunk in resp.iter_content(chunk_size=65536):
            if chunk:
                f.write(chunk)
                total += len(chunk)

    print(f"[render] ✅  Saved {total:,} bytes → {out_path.resolve()}")


# ── Subcommand: transcribe ────────────────────────────────────────────────────

def cmd_transcribe(args: argparse.Namespace) -> None:
    mp3_path = Path(args.mp3)
    if not mp3_path.exists():
        sys.exit(f"ERROR: MP3 file not found: {mp3_path}")

    url = args.url.rstrip("/") + "/transcribe"
    print(f"[transcribe] POST {url}")
    print(f"[transcribe] Audio: {mp3_path}  ({mp3_path.stat().st_size:,} bytes)")

    files: dict = {"file": (mp3_path.name, mp3_path.open("rb"), "audio/mpeg")}
    data: dict = {}

    if args.script:
        script_path = Path(args.script)
        if not script_path.exists():
            sys.exit(f"ERROR: Script file not found: {script_path}")
        data["script"] = script_path.read_text()

    try:
        resp = requests.post(
            url,
            headers=_auth_header(args.token),
            files=files,
            data=data,
            timeout=600,
        )
    except requests.exceptions.ConnectionError as e:
        sys.exit(f"ERROR: Could not connect to {url}\n{e}")
    except requests.exceptions.Timeout:
        sys.exit(f"ERROR: Request timed out after 600 s.")

    _check_response(resp, "transcribe")

    result = resp.json()
    overlap = result.get("overlapPct", result.get("overlap_pct", "n/a"))
    print(f"[transcribe] overlapPct: {overlap}")

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, indent=2, ensure_ascii=False))
    print(f"[transcribe] ✅  Saved result → {out_path.resolve()}")


# ── CLI ───────────────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="call_remote.py",
        description="Client for the Podcast Render Server. Requires the URL and TOKEN printed by start.command.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # render
    p_render = sub.add_parser("render", help="Render a script.json to an MP3 file.")
    p_render.add_argument("--url",     required=True,  help="Server base URL, e.g. https://xxxx.ngrok-free.app")
    p_render.add_argument("--token",   required=True,  help="Bearer token printed by start.command")
    p_render.add_argument("--script",  required=True,  help="Path to PRD-shape script.json")
    p_render.add_argument("--out",     required=True,  help="Output MP3 path, e.g. out.mp3")
    p_render.add_argument("--voice",      default=None, help="(Optional) path to a .wav reference clip (~6-10 s mono) for voice cloning — chatterbox backend only; omit to use the server's default narrator")
    p_render.add_argument("--voice-name", default=None, dest="voice_name", help="(Optional) kokoro preset voice name, e.g. af_heart, af_sarah, am_adam — kokoro backend only; default preset is af_heart")
    p_render.add_argument("--backend",    default=None, help="(Optional) TTS backend: chatterbox (voice cloning via --voice), kokoro (fast presets via --voice-name), or dummy (silent test). Server default: chatterbox")

    # transcribe
    p_trans = sub.add_parser("transcribe", help="Transcribe an MP3 and optionally align against a script.")
    p_trans.add_argument("--url",    required=True, help="Server base URL")
    p_trans.add_argument("--token",  required=True, help="Bearer token")
    p_trans.add_argument("--mp3",    required=True, help="Path to the MP3 file to transcribe")
    p_trans.add_argument("--script", default=None,  help="(Optional) path to script.json for alignment")
    p_trans.add_argument("--out",    required=True, help="Output JSON path for transcription result")

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "render":
        cmd_render(args)
    elif args.command == "transcribe":
        cmd_transcribe(args)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
