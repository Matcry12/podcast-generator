"""VieNeu TTS backend — Vietnamese + bilingual EN/VI synthesis.

Registered as "vieneu" via @register_backend.

Model is lazy-loaded on the first synthesize() call and unloaded after each
render completes (core.py calls be.unload() in a try/finally).

Device selection
-----------------
Default: cpu/onnx — VieNeu v3 Turbo's recommended fast path, works everywhere
and is highly optimised via ONNX Runtime (fastest for v3turbo on Apple Silicon).

Override via PODCAST_DEVICE env var:
  PODCAST_DEVICE=mps   → PyTorch + MPS (Apple Silicon GPU)
  PODCAST_DEVICE=cuda  → PyTorch + CUDA
"""
from __future__ import annotations

import gc
import logging
import os
import threading

from podcast.backends.base import TTSBackend
from podcast.backends.registry import register_backend

logger = logging.getLogger(__name__)

_FIXED_SR = 48_000   # v3 Turbo outputs 48 kHz audio


def _load_vieneu():
    """Instantiate VieNeu v3turbo with the configured device."""
    from vieneu import Vieneu  # noqa: PLC0415

    device = os.environ.get("PODCAST_DEVICE", "").strip().lower()
    if device in ("mps", "cuda"):
        logger.info("Loading VieNeu v3turbo on %s (PyTorch) ...", device)
        return Vieneu(mode="v3turbo", device=device, backend="pytorch")

    logger.info("Loading VieNeu v3turbo on cpu (ONNX) ...")
    return Vieneu(mode="v3turbo", device="cpu", backend="onnx")


def _is_file_path(voice: str) -> bool:
    return "/" in voice or voice.endswith(".wav") or voice.endswith(".mp3")


@register_backend
class VieNeuBackend(TTSBackend):
    """VieNeu TTS (Vietnamese + bilingual EN/VI, v3 Turbo, 48 kHz)."""

    name = "vieneu"
    supported_opts: set[str] = {"temperature", "top_k", "emotion"}
    default_voice: str = ""
    default_voice_map: dict[str, str] = {"narrator": "", "host": ""}

    def __init__(self) -> None:
        self._tts = None
        self._lock = threading.Lock()
        self.sr = _FIXED_SR

    def load(self) -> None:
        self._ensure_loaded()

    def _ensure_loaded(self) -> None:
        if self._tts is not None:
            return
        with self._lock:
            if self._tts is not None:
                return
            self._tts = _load_vieneu()
            logger.info("VieNeu ready (sr=%d)", _FIXED_SR)

    def unload(self) -> None:
        """Free model memory immediately after render."""
        with self._lock:
            if self._tts is not None:
                try:
                    self._tts.close()
                except Exception:
                    pass
                self._tts = None
                gc.collect()

    def synthesize(
        self,
        text: str,
        *,
        voice: str = "",
        temperature: float = 1.0,
        top_k: int = 50,
        emotion: str = "natural",
        **_,
    ) -> "torch.Tensor":  # type: ignore[name-defined]
        """Synthesize *text* and return a mono 2-D tensor [1, N] at 48 kHz."""
        import numpy as np
        import torch

        self._ensure_loaded()

        kwargs: dict = {"temperature": temperature, "top_k": top_k}
        if voice and _is_file_path(voice):
            kwargs["ref_audio"] = voice
        elif voice:
            kwargs["voice"] = voice

        audio = self._tts.infer(text, **kwargs)  # numpy float32

        if not isinstance(audio, torch.Tensor):
            audio = torch.from_numpy(np.asarray(audio, dtype=np.float32))
        else:
            audio = audio.to(dtype=torch.float32)
        return audio.reshape(1, -1)  # [1, N]
