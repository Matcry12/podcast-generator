"""TTS backend sub-package (portable — no Video maker dependency).

Importing this package registers the built-in backends:
  - chatterbox: pip chatterbox-tts, lazy-loads on first use
  - kokoro: pip kokoro, lazy-loads on first use
  - dummy: no model, for smoke tests
"""
from podcast.backends.base import TTSBackend
from podcast.backends.registry import get_backend, list_backends, register_backend

# Register backends (side-effect: registration via @register_backend decorator)
from podcast.backends import backend_chatterbox, backend_kokoro, dummy  # noqa: F401

__all__ = [
    "TTSBackend",
    "get_backend",
    "list_backends",
    "register_backend",
]
