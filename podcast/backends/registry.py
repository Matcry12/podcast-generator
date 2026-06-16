"""Backend registry — maps backend names to TTSBackend subclasses.

Usage:
    from podcast.backends.registry import register_backend, get_backend, list_backends

    @register_backend
    class MyBackend(TTSBackend):
        name = "my"
        ...

    be = get_backend("my")   # instantiated + cached
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from podcast.backends.base import TTSBackend as _TTSBackendType

_REGISTRY: dict[str, type] = {}          # name → class
_INSTANCES: dict[str, "_TTSBackendType"] = {}  # name → singleton instance


def register_backend(cls):
    """Class decorator — register a TTSBackend subclass by its ``name``."""
    if not cls.name:
        raise ValueError(f"register_backend: {cls.__qualname__} has no 'name' attribute")
    _REGISTRY[cls.name] = cls
    return cls


def get_backend(name: str) -> "_TTSBackendType":
    """Return a cached instance of the named backend.

    Raises ValueError listing available backends if *name* is unknown.
    """
    if name not in _REGISTRY:
        available = ", ".join(sorted(_REGISTRY)) or "(none registered)"
        raise ValueError(
            f"Unknown backend '{name}'. Available: {available}"
        )
    if name not in _INSTANCES:
        _INSTANCES[name] = _REGISTRY[name]()
    return _INSTANCES[name]


def list_backends() -> list[str]:
    """Return a sorted list of registered backend names."""
    return sorted(_REGISTRY)
