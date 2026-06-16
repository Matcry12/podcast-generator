"""podcast — pluggable TTS podcast synthesis package (portable server edition).

Quick start::

    from podcast import synthesize_podcast
    result = synthesize_podcast("script.json", "out/episode.mp3", backend="chatterbox")
    print(result.duration)

Backends are registered by importing their modules.  The default import
registers ``chatterbox`` (pip chatterbox-tts, lazy-loads on first use) and
``dummy`` (no GPU, for tests).  Add new backends by subclassing
:class:`~podcast.backends.base.TTSBackend` and decorating with
:func:`~podcast.backends.registry.register_backend`.
"""
from __future__ import annotations

# Core data types and entry point
from podcast.core import (
    PodcastResult,
    Script,
    Turn,
    load_script,
    script_from_ab_txt,
    script_from_json,
    script_from_text,
    synthesize_podcast,
    to_mp3,
)

# Backend infrastructure
from podcast.backends.registry import get_backend, list_backends, register_backend
from podcast.backends.base import TTSBackend

# Trigger backend registration (chatterbox + dummy)
import podcast.backends  # noqa: F401  (side-effect: registers chatterbox + dummy)

__all__ = [
    # synthesis
    "synthesize_podcast",
    # data classes
    "Turn",
    "Script",
    "PodcastResult",
    # loaders
    "load_script",
    "script_from_json",
    "script_from_ab_txt",
    "script_from_text",
    # backend API
    "get_backend",
    "list_backends",
    "register_backend",
    "TTSBackend",
    # audio util
    "to_mp3",
]
