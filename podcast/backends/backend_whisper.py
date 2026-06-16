"""Whisper transcription backend + vendored word-overlap diff.

Public API
----------
transcribe(mp3_path, model_name="small", device=None)
    -> {"text": str, "segments": [{"start": float, "end": float, "text": str}]}

overlap(script_text, transcript_text)
    -> {"overlapPct": float, "missing": [str, ...], "extra": [str, ...]}

The model is lazy-loaded on first call and kept warm for the process lifetime.
Default device is "cpu" — Whisper on MPS is unreliable; small/cpu is fine.

The overlap() function is vendored from verify_audio.py (_normalize +
_overlap_stats) so the shipped package needs no import from the repo tree.
"""
from __future__ import annotations

import logging
import re
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

# ── model cache ───────────────────────────────────────────────────────────────

_MODELS: dict[str, object] = {}   # (model_name, device) -> loaded model
_MODEL_LOCK = threading.Lock()


def _load_model(model_name: str, device: str):
    key = (model_name, device)
    if key in _MODELS:
        return _MODELS[key]
    with _MODEL_LOCK:
        if key in _MODELS:
            return _MODELS[key]
        import whisper  # noqa: PLC0415
        logger.info("Loading Whisper model '%s' on %s ...", model_name, device)
        model = whisper.load_model(model_name, device=device)
        _MODELS[key] = model
        logger.info("Whisper ready (model=%s, device=%s)", model_name, device)
        return model


# ── transcription ─────────────────────────────────────────────────────────────

def transcribe(
    mp3_path,
    model_name: str = "small",
    device: str | None = None,
) -> dict:
    """Transcribe *mp3_path* with Whisper and return a structured dict.

    Parameters
    ----------
    mp3_path:
        Path to an .mp3 (or any audio file Whisper accepts).
    model_name:
        Whisper model size: "tiny", "base", "small", "medium", "large".
        Default "small" — good accuracy/speed balance on CPU.
    device:
        Inference device. Default "cpu" (MPS is unreliable for Whisper).

    Returns
    -------
    {
        "text": str,                          # full transcript
        "segments": [                          # time-stamped segments
            {"start": float, "end": float, "text": str},
            ...
        ]
    }
    """
    if device is None:
        device = "cpu"

    model = _load_model(model_name, device)
    result = model.transcribe(str(mp3_path), verbose=False)

    segments = [
        {
            "start": float(s["start"]),
            "end": float(s["end"]),
            "text": s["text"].strip(),
        }
        for s in result["segments"]
    ]
    return {
        "text": result["text"].strip(),
        "segments": segments,
    }


# ── vendored overlap logic (from verify_audio.py) ─────────────────────────────
# Kept faithful to _normalize() + _overlap_stats() in verify_audio.py.

def _normalize(text: str) -> list[str]:
    """Lowercase, strip punctuation, split into words."""
    text = text.lower()
    text = re.sub(r"[^\w\s]", " ", text)
    return [w for w in text.split() if w]


def overlap(script_text: str, transcript_text: str) -> dict:
    """Compute word-level overlap between a script and a transcript.

    Vendored from verify_audio._overlap_stats() — identical normalization
    and scoring logic.

    Parameters
    ----------
    script_text:
        The intended spoken text (joined turn lines, or raw reference text).
    transcript_text:
        The Whisper transcript.

    Returns
    -------
    {
        "overlapPct": float,       # % of expected words found in actual (0-100)
        "missing":    [str, ...],  # top-5 contiguous missing spans
        "extra":      [str, ...],  # top-5 unexpected words
    }
    """
    exp_words = _normalize(script_text)
    act_words = _normalize(transcript_text)

    if not exp_words:
        return {"overlapPct": 0.0, "missing": [], "extra": []}

    exp_set = set(exp_words)
    act_set = set(act_words)

    missing_words = [w for w in exp_words if w not in act_set]
    extra_words   = [w for w in act_words  if w not in exp_set]

    overlap_pct = round(
        100.0 * (len(exp_words) - len(missing_words)) / len(exp_words),
        1,
    )

    # Find the 5 longest contiguous missing spans
    spans: list[list[str]] = []
    current: list[str] = []
    for w in exp_words:
        if w not in act_set:
            current.append(w)
        else:
            if current:
                spans.append(current)
                current = []
    if current:
        spans.append(current)
    spans.sort(key=len, reverse=True)
    top_missing = [" ".join(s) for s in spans[:5]]

    # Top-5 unexpected words (deduplicated, insertion order preserved)
    top_extra = list(dict.fromkeys(extra_words))[:5]

    return {
        "overlapPct": overlap_pct,
        "missing": top_missing,
        "extra": top_extra,
    }
