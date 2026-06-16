"""Dummy TTS backend — no GPU, no model, no external deps (beyond torch).

Returns silence (or a faint 440 Hz sine) sized proportionally to the input
text.  Useful for:
  - Unit tests / CI without a GPU.
  - End-to-end pipeline smoke tests.
  - Rapid iteration on script structure before committing to a full render.

Registration: automatically registered at import time via ``@register_backend``.
"""
from __future__ import annotations

import math

from podcast.backends.base import TTSBackend
from podcast.backends.registry import register_backend


@register_backend
class DummyBackend(TTSBackend):
    """Silent (near-silence) placeholder backend — no model required."""

    name = "dummy"
    supported_opts: set[str] = set()
    default_voice: str = "dummy"
    default_voice_map: dict[str, str] = {}

    # Fixed sample rate — matches a typical TTS model output rate.
    _FIXED_SR = 24_000
    # Seconds of audio per character (keeps silence proportional to text length).
    _SEC_PER_CHAR = 0.06
    _MIN_SEC = 0.3

    def load(self) -> None:
        """No-op — dummy backend has no model to load."""
        self.sr = self._FIXED_SR

    def synthesize(self, text: str, *, voice: str = "dummy", **_) -> "torch.Tensor":  # type: ignore[name-defined]
        """Return a near-silent tone tensor proportional to *text* length.

        Shape: [1, N]  at 24 kHz.  The faint 440 Hz sine (amplitude 0.001)
        makes it easy to verify in an audio editor that segments were actually
        generated (pure zeros look like a single flat line in every tool).
        """
        import torch

        duration = max(self._MIN_SEC, len(text) * self._SEC_PER_CHAR)
        n = int(self._FIXED_SR * duration)
        # Faint sine so the waveform is visibly non-zero without being audible
        t = torch.arange(n, dtype=torch.float32) / self._FIXED_SR
        wav = 0.001 * torch.sin(2 * math.pi * 440 * t)
        return wav.unsqueeze(0)  # [1, N]
