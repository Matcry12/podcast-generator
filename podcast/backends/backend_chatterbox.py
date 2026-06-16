"""Portable Chatterbox TTS backend — uses pip `chatterbox-tts`, no Video maker repo.

Registered as "chatterbox" via @register_backend.

Model is lazy-loaded on the first synthesize() call and kept warm for the
process lifetime (RAM is reclaimed only when the process exits).

Device selection (first match wins)
-------------------------------------
1. PODCAST_DEVICE env var — set to "cpu", "mps", or "cuda" to override.
   Useful on Mac Studio to force CPU if MPS misbehaves despite the fallback env.
2. Auto: "mps" if torch.backends.mps.is_available() else "cpu".

MPS hardening
-------------
PYTORCH_ENABLE_MPS_FALLBACK=1 is set at module import time (above) so that
unsupported MPS ops (e.g. conv channels > 65536, chatterbox#147) silently
re-dispatch to CPU rather than raising NotImplementedError.  chatterbox 0.1.7's
from_pretrained already handles load-time MPS→CPU, so this covers RUNTIME ops.

Belt-and-suspenders: if from_pretrained OR generate() raises RuntimeError /
NotImplementedError on MPS, the backend automatically reloads the model on CPU
(once), logs a WARNING, and continues.  After a CPU fallback the model stays on
CPU for the rest of the process.

API used (chatterbox-tts 0.1.x):
    from chatterbox.tts import ChatterboxTTS
    model = ChatterboxTTS.from_pretrained(device=DEVICE)
    wav = model.generate(text, audio_prompt_path=REF_WAV, exaggeration=exag, cfg_weight=cfg)
    # wav is a torch.Tensor; model.sr is the sample rate

Dtype monkey-patches (replicated from the Video maker wrapper) are applied
before the first generate() call to avoid float32/float64 crashes in
chatterbox 0.1.7's s3tokenizer and voice_encoder paths when a reference
clip is supplied.

Voice map — this is a single-speaker server; all logical names collapse to
the bundled narrator clip:
    default_voice_map = {"narrator": "narrator", "heart": "narrator", "A": "narrator"}
"""
from __future__ import annotations

# MPS op fallback — must be set BEFORE torch is imported anywhere in this process.
# Fixes: NotImplementedError: Output channels > 65536 not supported at the MPS device
# (resemble-ai/chatterbox#147).  Unsupported MPS ops silently fall back to CPU.
import os
os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")

import logging
import threading
from pathlib import Path

from podcast.backends.base import TTSBackend
from podcast.backends.registry import register_backend

logger = logging.getLogger(__name__)

# Path to the bundled narrator reference clip (relative to this file's package root).
# Layout: podcast-server/voices/narrator.wav
_VOICES_DIR = Path(__file__).resolve().parents[3] / "voices"
REF_WAV = _VOICES_DIR / "narrator.wav"

# ── dtype patches (mandatory for chatterbox 0.1.7 with reference audio) ──────

_PATCHED = False
_PATCH_LOCK = threading.Lock()


def _patch_chatterbox_dtype() -> None:
    """Apply float32 coercion patches to s3tokenizer and voice_encoder.

    These patches are identical to those in the Video maker tts_chatterbox.py.
    Without them, chatterbox 0.1.7 crashes with float32/float64 dtype
    mismatches when audio_prompt_path is supplied.
    """
    global _PATCHED
    with _PATCH_LOCK:
        if _PATCHED:
            return
        import torch
        from chatterbox.models.s3tokenizer import s3tokenizer as s3t
        from chatterbox.models.voice_encoder import voice_encoder as ve

        _orig_mel = s3t.S3Tokenizer.log_mel_spectrogram

        def _mel_patched(self, audio, padding: int = 0):
            if not torch.is_tensor(audio):
                audio = torch.from_numpy(audio)
            audio = audio.to(dtype=torch.float32, device=self.device)
            return _orig_mel(self, audio, padding=padding)

        s3t.S3Tokenizer.log_mel_spectrogram = _mel_patched

        _orig_inf = ve.VoiceEncoder.inference

        def _inf_patched(self, mels, mel_lens, *args, **kwargs):
            if torch.is_tensor(mels):
                mels = mels.to(dtype=torch.float32)
            return _orig_inf(self, mels, mel_lens, *args, **kwargs)

        ve.VoiceEncoder.inference = _inf_patched
        _PATCHED = True
        logger.debug("Chatterbox dtype patches applied.")


# ── device selection ──────────────────────────────────────────────────────────

_VALID_DEVICES = {"cpu", "mps", "cuda"}


def _auto_device() -> str:
    """Return the device to load Chatterbox on.

    Resolution order:
    1. PODCAST_DEVICE env var — "cpu", "mps", or "cuda" (case-insensitive).
       Invalid values are logged and ignored; auto-selection proceeds.
    2. "mps" if torch.backends.mps.is_available() (Apple Silicon).
    3. "cpu" otherwise.

    Note: torch is NOT imported when PODCAST_DEVICE is set to a valid value,
    so the env-var path works even before torch is available.
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


def _load_model(device: str):
    """Load ChatterboxTTS on *device* and return the model instance."""
    from chatterbox.tts import ChatterboxTTS  # noqa: PLC0415
    logger.info("Loading ChatterboxTTS on %s ...", device)
    model = ChatterboxTTS.from_pretrained(device=device)
    logger.info("ChatterboxTTS ready (device=%s, sr=%d)", device, int(model.sr))
    return model


# ── backend ───────────────────────────────────────────────────────────────────

@register_backend
class ChatterboxBackend(TTSBackend):
    """Portable Chatterbox voice-cloning TTS (pip chatterbox-tts)."""

    name = "chatterbox"
    supported_opts: set[str] = {"exag", "cfg"}
    default_voice: str = "narrator"
    default_voice_map: dict[str, str] = {
        "narrator": "narrator",
        "heart": "narrator",
        "A": "narrator",
        "host": "narrator",
        "guest": "narrator",
    }

    # Default synthesis parameters (from spec + lab findings)
    _DEFAULT_EXAG: float = 0.8
    _DEFAULT_CFG: float = 0.1

    def __init__(self) -> None:
        self._model = None      # loaded on first synthesize()
        self._device: str = ""  # set after load; tracks current model device
        self._lock = threading.Lock()
        self.sr = 0

    # ── lifecycle ─────────────────────────────────────────────────────────────

    def load(self) -> None:
        """No-op at import time — model loads lazily on first synthesize() call.

        Implements the TTSBackend contract; core.py calls load() before
        synthesize().  We load here if not yet loaded so the warm-cache
        path is handled correctly.
        """
        self._ensure_loaded()

    def _ensure_loaded(self) -> None:
        """Load the ChatterboxTTS model if not already loaded (thread-safe).

        If MPS is selected and from_pretrained raises RuntimeError or
        NotImplementedError, the backend falls back to CPU automatically
        (see _mps_fallback_to_cpu).
        """
        if self._model is not None:
            return
        with self._lock:
            if self._model is not None:
                return
            _patch_chatterbox_dtype()
            device = _auto_device()
            try:
                model = _load_model(device)
            except (RuntimeError, NotImplementedError) as exc:
                if device == "mps":
                    model = self._mps_fallback_to_cpu(exc, phase="load")
                    device = "cpu"
                else:
                    raise
            self._model = model
            self._device = device
            self.sr = int(model.sr)

    def _mps_fallback_to_cpu(self, exc: Exception, phase: str):
        """Log a warning and reload the model on CPU after an MPS failure.

        *phase* is "load" or "generate" — used only for the log message.
        Returns the CPU model instance.
        """
        logger.warning(
            "MPS failed during %s (%s: %s); falling back to CPU.",
            phase, type(exc).__name__, exc,
        )
        return _load_model("cpu")

    # ── synthesis ─────────────────────────────────────────────────────────────

    def _resolve_ref_wav(self, voice: str) -> Path:
        """Return the reference WAV path for *voice*.

        Resolution order:
        1. If *voice* is an existing file path → use it directly (per-request
           custom clip uploaded via /render multipart ``voice`` field).
        2. Known logical names (narrator / heart / A / host / guest) → bundled
           voices/narrator.wav.
        3. Anything else → bundled voices/narrator.wav (safe fallback).

        Raises FileNotFoundError if the resolved path does not exist.
        """
        candidate = Path(voice)
        if candidate.is_absolute() and candidate.exists():
            return candidate

        # Logical name → bundled clip
        ref = REF_WAV
        if not ref.exists():
            raise FileNotFoundError(
                f"Narrator reference clip missing: {ref}\n"
                "Drop voices/narrator.wav (a ~10 s WAV of the narrator) next to this package."
            )
        return ref

    def synthesize(
        self,
        text: str,
        *,
        voice: str = "narrator",
        exag: float = _DEFAULT_EXAG,
        cfg: float = _DEFAULT_CFG,
        **_,
    ) -> "torch.Tensor":  # type: ignore[name-defined]
        """Synthesize *text* and return a mono 2-D tensor [1, N] at self.sr.

        *voice* may be a logical name ("narrator", "heart", …) or an absolute
        path to a custom .wav clip uploaded per-request.  Resolution is handled
        by ``_resolve_ref_wav()``.
        *exag* and *cfg* are passed through as exaggeration / cfg_weight.
        Extra kwargs are silently dropped (already filtered by filter_opts upstream).

        If the model is on MPS and generate() raises RuntimeError /
        NotImplementedError, the model is transparently reloaded on CPU and the
        synthesis is retried once.  Subsequent calls use the CPU model.
        """
        import torch

        self._ensure_loaded()

        ref_wav = self._resolve_ref_wav(voice)
        kwargs = dict(
            audio_prompt_path=str(ref_wav),
            exaggeration=exag,
            cfg_weight=cfg,
        )

        try:
            wav = self._model.generate(text, **kwargs)
        except (RuntimeError, NotImplementedError) as exc:
            if self._device == "mps":
                # Reload on CPU, update cached state, retry once.
                with self._lock:
                    if self._device == "mps":   # another thread may have already fallen back
                        cpu_model = self._mps_fallback_to_cpu(exc, phase="generate")
                        self._model = cpu_model
                        self._device = "cpu"
                        self.sr = int(cpu_model.sr)
                wav = self._model.generate(text, **kwargs)
            else:
                raise

        # Ensure mono 2-D [1, N]
        if wav.dim() == 1:
            wav = wav.unsqueeze(0)
        if wav.shape[0] > 1:
            wav = wav.mean(dim=0, keepdim=True)
        return wav
