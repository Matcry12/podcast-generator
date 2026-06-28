"""FastAPI server — Chatterbox TTS render + Whisper transcribe over HTTP.

Endpoints
---------
GET  /health         Always 200; no auth required.
POST /render         multipart/form-data — render script.json to audio/mpeg bytes.
POST /transcribe     multipart/form-data — transcribe mp3 via Whisper.

/render multipart fields
------------------------
  script   (string, required)  — the script.json text (PRD shape)
  voice    (file,   optional)  — custom .wav reference clip; bundled narrator.wav used if absent
  backend  (string, optional)  — TTS backend name, default "chatterbox"; use "dummy" for smoke tests

Auth
----
Read env PODCAST_TOKEN.  If set and non-empty, /render and /transcribe require:
    Authorization: Bearer <token>
/health never requires auth.  If PODCAST_TOKEN is unset/empty, auth is DISABLED
(useful for local testing).

Port
----
Read env PORT (default 8000).  server.py only defines `app`; uvicorn is launched
by run.py.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Optional

import asyncio

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, Response

app = FastAPI(title="Podcast Render Server", version="1.0.0")

# One render at a time — models are singletons; concurrent renders share the same
# instance and unload() from one would null the model mid-generate in another.
_render_lock = asyncio.Semaphore(1)

# Last error per backend — cleared on success, set on failure, exposed via /health.
_last_errors: dict[str, str] = {}

# ── auth ──────────────────────────────────────────────────────────────────────

def _get_token() -> str | None:
    """Return the expected bearer token, or None if auth is disabled."""
    return os.environ.get("PODCAST_TOKEN") or None


def _require_auth(request: Request) -> None:
    """Raise HTTP 401 if auth is enabled and the request lacks a valid token."""
    token = _get_token()
    if token is None:
        return  # auth disabled
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing Authorization: Bearer <token>")
    provided = auth_header[len("Bearer "):]
    if provided != token:
        raise HTTPException(status_code=401, detail="Invalid token")


# ── helpers ───────────────────────────────────────────────────────────────────

def _device_str() -> str:
    """Return the auto-selected device name (for /health)."""
    try:
        import torch
        if torch.backends.mps.is_available():
            return "mps"
        if torch.cuda.is_available():
            return "cuda"
        return "cpu"
    except ImportError:
        return "cpu"


def _backend_status() -> dict:
    """Return load state for every registered backend."""
    try:
        from podcast.backends.registry import _INSTANCES, list_backends
        status = {}
        for name in list_backends():
            be = _INSTANCES.get(name)
            if be is None:
                status[name] = "registered"
            else:
                # check whichever attr the backend uses for its model
                loaded = (
                    getattr(be, "_model", None) is not None
                    or getattr(be, "_pipeline", None) is not None
                    or getattr(be, "_tts", None) is not None
                )
                status[name] = "loaded" if loaded else "idle"
        return status
    except Exception:  # noqa: BLE001
        return {}


def _script_text_from_json(script_json: dict) -> str:
    """Extract concatenated turn text from a PRD-shape script dict."""
    turns = script_json.get("turns", [])
    parts = [t.get("line") or t.get("text", "") for t in turns]
    return " ".join(p for p in parts if p)


# ── /health ───────────────────────────────────────────────────────────────────

@app.get("/health")
async def health() -> JSONResponse:
    backend_status = _backend_status()
    # Annotate any backend that has a recorded error
    for name, err in _last_errors.items():
        if name in backend_status:
            backend_status[name] = {"state": backend_status[name], "last_error": err}
        else:
            backend_status[name] = {"state": "unknown", "last_error": err}

    return JSONResponse({
        "status": "ok",
        "device": _device_str(),
        "render_busy": _render_lock.locked(),
        "backends": backend_status,
    })


# ── /render ───────────────────────────────────────────────────────────────────

@app.post("/render")
async def render(
    request: Request,
    script: str = Form(..., description="script.json text (PRD shape)"),
    voice: Optional[UploadFile] = File(default=None, description="Custom .wav reference clip (optional, chatterbox only)"),
    voice_name: Optional[str] = Form(default=None, description="Kokoro/VieNeu preset voice name (optional)"),
    backend: str = Form(default="chatterbox", description="TTS backend name; use 'dummy' for smoke tests"),
    backend_opts: Optional[str] = Form(default=None, description="JSON dict of backend-specific render params, e.g. '{\"num_step\":16}' (omnivoice)"),
) -> Response:
    """Render a script.json (PRD shape) to an MP3 and return the bytes.

    multipart/form-data fields:
      script     (string, required) — the script.json text, e.g.::

          {"host_mode":"single","voice":"narrator",
           "turns":[{"voice":"narrator","line":"Hello world."}]}

      voice      (file, optional)   — a .wav reference clip for voice cloning.
                                      Chatterbox only; ignored for kokoro/dummy.
                                      If omitted, voices/narrator.wav is used.
      voice_name (string, optional) — Kokoro preset voice name (e.g. "af_heart").
                                      Kokoro only; ignored for chatterbox/dummy.
                                      If omitted, backend default "af_heart" is used.
      backend    (string, optional) — TTS backend, default "chatterbox".
                                      Use "dummy" for GPU-free smoke tests.

    Returns Content-Type: audio/mpeg on success.
    Returns 400 JSON {"error": "...", "turn": <int|null>} on validation failure.
    """
    _require_auth(request)

    # --- parse script field ---
    try:
        body = json.loads(script)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail={"error": f"Invalid JSON in 'script' field: {exc}", "turn": None})

    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail={"error": "'script' must be a JSON object", "turn": None})

    turns_raw = body.get("turns")
    if not turns_raw:
        raise HTTPException(status_code=400, detail={"error": "script has no 'turns'", "turn": None})

    # Ensure the server's own podcast package is importable
    import sys
    _server_dir = Path(__file__).resolve().parent
    if str(_server_dir) not in sys.path:
        sys.path.insert(0, str(_server_dir))

    from podcast.core import synthesize_podcast, script_from_json

    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        script_path = td_path / "script.json"
        mp3_path = td_path / "render.mp3"

        # --- resolve voice_map by backend ---
        voice_map: dict | None = None
        custom_wav: Path | None = None

        if backend == "chatterbox":
            # Chatterbox: optional uploaded WAV clip for voice cloning.
            if voice is not None and voice.filename:
                custom_wav = td_path / "custom_voice.wav"
                custom_wav.write_bytes(await voice.read())
                # Pass the temp path as the resolved voice so the backend uses it directly.
                voice_map = {"narrator": str(custom_wav)}
            # else: None → backend uses bundled narrator.wav

        elif backend == "kokoro":
            # Kokoro: optional preset name string; uploaded voice file is ignored.
            if voice_name:
                voice_map = {"narrator": voice_name}
            # else: None → backend default "af_heart"

        elif backend == "vieneu":
            if voice is not None and voice.filename:
                suffix = Path(voice.filename).suffix or ".wav"
                custom_wav = td_path / f"vi_ref{suffix}"
                custom_wav.write_bytes(await voice.read())
                voice_map = {"narrator": str(custom_wav)}
            elif voice_name:
                voice_map = {"narrator": voice_name}

        elif backend == "omnivoice":
            if voice is not None and voice.filename:
                suffix = Path(voice.filename).suffix or ".wav"
                custom_wav = td_path / f"ov_ref{suffix}"
                custom_wav.write_bytes(await voice.read())
                voice_map = {"narrator": str(custom_wav)}

        else:
            # dummy (or any future backend): no voice customisation.
            pass

        # Parse optional backend_opts JSON (e.g. num_step, guidance_scale for omnivoice)
        parsed_backend_opts: dict | None = None
        if backend_opts:
            try:
                parsed_backend_opts = json.loads(backend_opts)
                if not isinstance(parsed_backend_opts, dict):
                    raise ValueError("backend_opts must be a JSON object")
            except Exception as exc:  # noqa: BLE001
                raise HTTPException(status_code=400, detail={"error": f"Invalid backend_opts JSON: {exc}", "turn": None})

        script_path.write_text(json.dumps(body), encoding="utf-8")

        try:
            script_obj = script_from_json(script_path)
        except (ValueError, KeyError) as exc:
            raise HTTPException(status_code=400, detail={"error": str(exc), "turn": None})

        # Per-turn validation: catch chunker errors before hitting the model
        try:
            for i, turn in enumerate(script_obj.turns):
                _validate_turn_text(turn.text, i)
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=400, detail={"error": str(exc), "turn": None})

        async with _render_lock:
            try:
                synthesize_podcast(
                    script_obj,
                    mp3_path,
                    backend=backend,
                    voice_map=voice_map,
                    global_opts=parsed_backend_opts,
                )
                _last_errors.pop(backend, None)  # clear on success
            except ValueError as exc:
                _last_errors[backend] = str(exc)
                raise HTTPException(status_code=400, detail={"error": str(exc), "turn": None})
            except Exception as exc:  # noqa: BLE001
                _last_errors[backend] = f"{type(exc).__name__}: {exc}"
                raise HTTPException(status_code=500, detail={"error": str(exc), "turn": None})

        mp3_bytes = mp3_path.read_bytes()
        # custom_wav lives inside td and is removed when the context manager exits

    return Response(content=mp3_bytes, media_type="audio/mpeg")


def _validate_turn_text(text: str, turn_idx: int) -> None:
    """Raise HTTPException 400 if *text* violates the render chunker rules."""
    t = text.strip()
    if not t:
        raise HTTPException(status_code=400, detail={"error": "Turn text is empty", "turn": turn_idx})
    if t[0] not in '"\'[' and not t[0].isupper():
        raise HTTPException(
            status_code=400,
            detail={"error": f"Turn text must start with a capital letter or quote: {t[:60]!r}", "turn": turn_idx},
        )
    if t.rstrip()[-1] not in '.!?"\'':
        raise HTTPException(
            status_code=400,
            detail={"error": f"Turn text must end with terminal punctuation: {t[-60:]!r}", "turn": turn_idx},
        )


# ── /transcribe ───────────────────────────────────────────────────────────────

@app.post("/transcribe")
async def transcribe_audio(
    request: Request,
    file: UploadFile = File(..., description="MP3 (or any Whisper-compatible audio)"),
    script: Optional[str] = Form(default=None, description="Reference text or script.json for overlap scoring"),
) -> JSONResponse:
    """Transcribe an uploaded audio file with Whisper.

    Multipart fields:
      file   — the audio file (required)
      script — reference text OR a script.json string (optional).
               When provided, overlapPct is computed; otherwise it is null.

    Returns::

        {
          "text": "...",
          "segments": [{"start": 0.0, "end": 1.2, "text": "..."}, ...],
          "overlapPct": 87.5   // or null
        }
    """
    _require_auth(request)

    import sys
    _server_dir = Path(__file__).resolve().parent
    if str(_server_dir) not in sys.path:
        sys.path.insert(0, str(_server_dir))

    from podcast.backends.backend_whisper import transcribe, overlap

    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        suffix = Path(file.filename or "audio.mp3").suffix or ".mp3"
        audio_path = td_path / f"upload{suffix}"
        audio_path.write_bytes(await file.read())

        try:
            result = transcribe(audio_path)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=500, detail={"error": f"Transcription failed: {exc}"})

    # --- optional overlap scoring ---
    overlap_pct: float | None = None
    if script:
        # script may be raw text or a JSON script.json string
        script_text = script
        try:
            parsed = json.loads(script)
            if isinstance(parsed, dict) and "turns" in parsed:
                script_text = _script_text_from_json(parsed)
        except (json.JSONDecodeError, TypeError):
            pass  # treat as raw reference text

        try:
            ov = overlap(script_text, result["text"])
            overlap_pct = ov["overlapPct"]
        except Exception as exc:  # noqa: BLE001
            logger_msg = f"overlap computation failed (non-fatal): {exc}"
            import logging
            logging.getLogger(__name__).warning(logger_msg)

    return JSONResponse({
        "text": result["text"],
        "segments": result["segments"],
        "overlapPct": overlap_pct,
    })
