"""Abstract base class for TTS backends.

Every backend subclass must:
  - Set class attrs: ``name``, ``supported_opts``, ``default_voice``,
    ``default_voice_map`` (optional).
  - Implement ``load()`` (idempotent — safe to call multiple times).
  - Implement ``synthesize()`` returning a mono 2-D tensor [1, N] at ``self.sr``.

The ABC is intentionally thin so backends stay small and focused.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import torch


class TTSBackend(ABC):
    """Pluggable TTS backend contract."""

    # --- class-level metadata (override in subclasses) -----------------------
    name: str = ""
    supported_opts: set[str] = set()
    default_voice: str = ""
    default_voice_map: dict[str, str] = {}   # logical speaker → backend voice

    # --- instance state set after load() -------------------------------------
    sr: int = 0

    # --- lifecycle -----------------------------------------------------------

    def load(self) -> None:
        """Lazy-load model / heavy deps.  Idempotent — safe to call multiple times."""

    # --- synthesis -----------------------------------------------------------

    @abstractmethod
    def synthesize(self, text: str, *, voice: str, **opts) -> "torch.Tensor":
        """Synthesize *text* spoken by *voice*.

        Returns a mono 2-D float tensor of shape [1, samples] sampled at
        ``self.sr``.  Any backend-specific knobs (exag, speed, …) arrive as
        keyword args; unknown keys should be silently ignored via
        ``filter_opts()``.
        """

    # --- helpers -------------------------------------------------------------

    def resolve_voice(self, speaker: str, voice_map: dict | None) -> str:
        """Map a logical *speaker* label to a backend voice string.

        Resolution order:
        1. ``voice_map`` argument (caller override).
        2. ``self.default_voice_map`` (per-backend logical defaults).
        3. ``self.default_voice`` (hard fallback).
        """
        if voice_map and speaker in voice_map:
            return voice_map[speaker]
        if speaker in self.default_voice_map:
            return self.default_voice_map[speaker]
        return self.default_voice

    def filter_opts(self, opts: dict) -> dict:
        """Return only the keys from *opts* that this backend declares support for."""
        return {k: v for k, v in opts.items() if k in self.supported_opts}
