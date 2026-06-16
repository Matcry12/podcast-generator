"""Kokoro TTS backend — uses pip `kokoro`, no model file download required.

Registered as "kokoro" via @register_backend.

Pipeline is lazy-loaded on the first synthesize() call and kept warm for the
process lifetime (RAM is reclaimed only when the process exits).

Device selection (first match wins)
-------------------------------------
1. PODCAST_DEVICE env var — set to "cpu", "mps", or "cuda" to override.
2. Auto: "mps" if torch.backends.mps.is_available() else "cpu".

Note: KPipeline is constructed with a ``device`` kwarg only if its __init__
accepts one; otherwise it is constructed plainly.  This avoids breakage across
kokoro versions.

Voice map — Kokoro voices are PRESET NAME strings, not audio reference clips.
Voice cloning (reference WAV files) is NOT supported by Kokoro; if a resolved
voice value looks like a file path it is ignored and "af_heart" is used instead.

API used (kokoro>=0.9.4):
    from kokoro import KPipeline
    pipeline = KPipeline(lang_code='a')    # 'a' = American English
    for _gs, _ps, audio in pipeline(text, voice='af_heart'):
        ...   # audio is float32 numpy/torch array at 24000 Hz; may yield chunks
"""
from __future__ import annotations

import inspect
import logging
import os
import threading
from typing import TYPE_CHECKING

from podcast.backends.base import TTSBackend
from podcast.backends.registry import register_backend

if TYPE_CHECKING:
    import torch

logger = logging.getLogger(__name__)

# ── device selection ──────────────────────────────────────────────────────────

_VALID_DEVICES = {"cpu", "mps", "cuda"}


def _auto_device() -> str:
    """Return the device to load Kokoro on.

    Resolution order:
    1. PODCAST_DEVICE env var — "cpu", "mps", or "cuda" (case-insensitive).
       Invalid values are logged and ignored; auto-selection proceeds.
    2. "mps" if torch.backends.mps.is_available() (Apple Silicon).
    3. "cpu" otherwise.
    """
    override = os.environ.get("PODCAST_DEVICE", "").strip().lower()
    if override:
        if override in _VALID_DEVICES:
            return override
        logger.warning(
            "PODCAST_DEVICE=%r is not one of %s; ignoring and using auto-selection.",
            override, sorted(_VALID_DEVICES),
        )

    import torch  # noqa: PLC0415 — lazy, only reached when no valid override
    if torch.backends.mps.is_available():
        return "mps"
    return "cpu"


def _is_file_path(voice: str) -> bool:
    """Return True if *voice* looks like a filesystem path rather than a preset name."""
    return "/" in voice or voice.endswith(".wav") or voice.endswith(".mp3")


def _load_pipeline(device: str):
    """Construct and return a KPipeline instance for American English.

    Passes ``device`` only if KPipeline.__init__ accepts it, so this works
    across kokoro versions that may not expose a device argument.
    """
    from kokoro import KPipeline  # noqa: PLC0415

    sig = inspect.signature(KPipeline.__init__)
    params = set(sig.parameters)

    if "device" in params:
        logger.info("Loading KPipeline(lang_code='a', device=%s) ...", device)
        pipeline = KPipeline(lang_code="a", device=device)
    else:
        logger.info("Loading KPipeline(lang_code='a') [no device param in this kokoro version] ...")
        pipeline = KPipeline(lang_code="a")

    logger.info("KPipeline ready (sr=24000)")
    return pipeline


# ── backend ───────────────────────────────────────────────────────────────────

@register_backend
class KokoroBackend(TTSBackend):
    """Kokoro TTS backend using preset voice names (no voice cloning)."""

    name = "kokoro"
    supported_opts: set[str] = {"speed"}
    default_voice: str = "af_heart"
    default_voice_map: dict[str, str] = {
        "narrator": "af_heart",
        "A": "af_heart",
    }

    # Fixed sample rate for all Kokoro output.
    _FIXED_SR = 24_000

    def __init__(self) -> None:
        self._pipeline = None   # loaded on first synthesize()
        self._lock = threading.Lock()
        self.sr = self._FIXED_SR

    # ── lifecycle ─────────────────────────────────────────────────────────────

    def load(self) -> None:
        """No-op at import time — pipeline loads lazily on first synthesize() call.

        Implements the TTSBackend contract; core.py calls load() before
        synthesize().  We load here if not yet loaded so the warm-cache
        path is handled correctly.
        """
        self._ensure_loaded()

    def _ensure_loaded(self) -> None:
        """Load the KPipeline if not already loaded (thread-safe)."""
        if self._pipeline is not None:
            return
        with self._lock:
            if self._pipeline is not None:
                return
            device = _auto_device()
            self._pipeline = _load_pipeline(device)
            self.sr = self._FIXED_SR

    # ── synthesis ─────────────────────────────────────────────────────────────

    def synthesize(
        self,
        text: str,
        *,
        voice: str = "af_heart",
        speed: float = 1.0,
        **_,
    ) -> "torch.Tensor":
        """Synthesize *text* and return a mono 2-D tensor [1, N] at 24000 Hz.

        *voice* must be a Kokoro preset name string (e.g. "af_heart").
        If it looks like a file path it is ignored and "af_heart" is used,
        because Kokoro does not support reference audio cloning.

        *speed* is passed through to the pipeline (default 1.0).
        Extra kwargs are silently dropped (already filtered by filter_opts upstream).
        """
        import torch

        self._ensure_loaded()

        # Reject file paths — Kokoro uses preset names only.
        if _is_file_path(voice):
            logger.warning(
                "KokoroBackend: voice=%r looks like a file path; "
                "Kokoro does not support reference audio — falling back to 'af_heart'.",
                voice,
            )
            voice = "af_heart"

        chunks: list["torch.Tensor"] = []
        for _gs, _ps, audio in self._pipeline(text, voice=voice, speed=speed):
            if audio is None:
                continue
            # audio may be a numpy array or a torch tensor
            if not isinstance(audio, torch.Tensor):
                chunk = torch.from_numpy(audio)
            else:
                chunk = audio
            # Ensure float32 and 1-D before collecting
            chunk = chunk.to(dtype=torch.float32).reshape(-1)
            chunks.append(chunk)

        if not chunks:
            # Return minimal silence rather than crash
            logger.warning("KokoroBackend: pipeline yielded no audio for text=%r", text[:60])
            return torch.zeros(1, self._FIXED_SR, dtype=torch.float32)

        wav = torch.cat(chunks)       # [N]
        return wav.unsqueeze(0)       # [1, N]
