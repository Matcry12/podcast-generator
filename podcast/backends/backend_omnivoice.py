"""OmniVoice TTS backend — multilingual zero-shot voice cloning (600+ languages).

Vietnamese is supported with 8,481 h of training data.  Registered as "omnivoice".

Device selection
----------------
Auto-detects MPS (Apple Silicon) → falls back to CPU.  Override with PODCAST_DEVICE:
  PODCAST_DEVICE=mps    → Metal (Apple Silicon GPU) — float32
  PODCAST_DEVICE=cuda   → CUDA — float16
  PODCAST_DEVICE=cpu    → CPU — float32

Default generation parameters (override via env vars or per-render backend_opts):
  PODCAST_OV_NUM_STEP       int   32   quality vs speed (16 = faster, 64 = higher quality)
  PODCAST_OV_GUIDANCE_SCALE float 2.0  how closely output follows the reference voice
  PODCAST_OV_SPEED          float 1.0  playback rate (0.9 = slower/warmer, 1.1 = faster)
  PODCAST_OV_DENOISE        bool  true noise removal on output
  PODCAST_OV_T_SHIFT        float 0.1  noise schedule shift (advanced)
  PODCAST_OV_CLASS_TEMP     float 0.0  token sampling temperature (0 = deterministic)
"""
from __future__ import annotations

import gc
import logging
import os
import threading

from podcast.backends.base import TTSBackend
from podcast.backends.registry import register_backend

logger = logging.getLogger(__name__)

_FIXED_SR = 24_000   # OmniVoice outputs 24 kHz


def _env_int(key: str, default: int) -> int:
    try:
        return int(os.environ.get(key, default))
    except (ValueError, TypeError):
        return default


def _env_float(key: str, default: float) -> float:
    try:
        return float(os.environ.get(key, default))
    except (ValueError, TypeError):
        return default


def _env_bool(key: str, default: bool) -> bool:
    v = os.environ.get(key, "").strip().lower()
    if v in ("0", "false", "no"):
        return False
    if v in ("1", "true", "yes"):
        return True
    return default


def _resolve_device() -> tuple[str, "torch.dtype"]:
    """Return (device_map, torch_dtype) based on PODCAST_DEVICE or auto-detection."""
    import torch
    explicit = os.environ.get("PODCAST_DEVICE", "").strip().lower()
    if explicit == "cuda":
        return "cuda:0", torch.float16
    if explicit == "mps":
        return "mps", torch.float32      # float32 on MPS: avoids rare fp16 op gaps
    if explicit == "cpu":
        return "cpu", torch.float32
    # Auto-detect
    if torch.backends.mps.is_available():
        return "mps", torch.float32
    return "cpu", torch.float32


def _is_file_path(voice: str) -> bool:
    return "/" in voice or "." in voice.split("/")[-1]


def _load_model(device_map: str, dtype: "torch.dtype"):
    from omnivoice import OmniVoice
    logger.info("Loading OmniVoice on %s (dtype=%s) ...", device_map, dtype)
    return OmniVoice.from_pretrained(
        "k2-fsa/OmniVoice",
        device_map=device_map,
        torch_dtype=dtype,
    )


@register_backend
class OmniVoiceBackend(TTSBackend):
    """OmniVoice TTS — multilingual voice cloning, 24 kHz."""

    name = "omnivoice"
    supported_opts: set[str] = {
        "num_step", "guidance_scale", "speed", "denoise",
        "t_shift", "class_temperature", "ref_text",
    }
    default_voice: str = ""
    default_voice_map: dict[str, str] = {"narrator": "", "host": ""}

    def __init__(self) -> None:
        self._model = None
        self._lock = threading.Lock()
        self.sr = _FIXED_SR

    def load(self) -> None:
        self._ensure_loaded()

    def _ensure_loaded(self) -> None:
        if self._model is not None:
            return
        with self._lock:
            if self._model is not None:
                return
            device_map, dtype = _resolve_device()
            self._model = _load_model(device_map, dtype)
            logger.info("OmniVoice ready (device=%s, sr=%d)", device_map, _FIXED_SR)

    def unload(self) -> None:
        """Free model memory after render (guards against OmniVoice memory leak #199)."""
        import torch
        with self._lock:
            if self._model is not None:
                del self._model
                self._model = None
                gc.collect()
                try:
                    if torch.backends.mps.is_available():
                        torch.mps.empty_cache()
                    elif torch.cuda.is_available():
                        torch.cuda.empty_cache()
                except Exception:
                    pass

    def synthesize(
        self,
        text: str,
        *,
        voice: str = "",
        ref_text: str = "",
        num_step: int | None = None,
        guidance_scale: float | None = None,
        speed: float | None = None,
        denoise: bool | None = None,
        t_shift: float | None = None,
        class_temperature: float | None = None,
        **_,
    ) -> "torch.Tensor":
        import numpy as np
        import torch

        self._ensure_loaded()

        kwargs: dict = {
            "num_step":          num_step          if num_step          is not None else _env_int("PODCAST_OV_NUM_STEP", 32),
            "guidance_scale":    guidance_scale    if guidance_scale    is not None else _env_float("PODCAST_OV_GUIDANCE_SCALE", 2.0),
            "speed":             speed             if speed             is not None else _env_float("PODCAST_OV_SPEED", 1.0),
            "denoise":           denoise           if denoise           is not None else _env_bool("PODCAST_OV_DENOISE", True),
            "t_shift":           t_shift           if t_shift           is not None else _env_float("PODCAST_OV_T_SHIFT", 0.1),
            "class_temperature": class_temperature if class_temperature is not None else _env_float("PODCAST_OV_CLASS_TEMP", 0.0),
            "postprocess_output": True,
            "preprocess_prompt":  True,
        }

        # Voice: file path → ref_audio= (+ optional ref_text to skip internal Whisper)
        # OmniVoice has no named preset voices — empty voice = generic model output
        if voice and _is_file_path(voice):
            kwargs["ref_audio"] = voice
            if ref_text:
                kwargs["ref_text"] = ref_text
                kwargs["preprocess_prompt"] = False  # skip Whisper — ref already transcribed

        audio_list = self._model.generate(text=text, **kwargs)
        audio = np.asarray(audio_list[0], dtype=np.float32)
        return torch.from_numpy(audio).reshape(1, -1)  # [1, N]
