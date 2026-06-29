"""Core podcast synthesis engine.

Public API:
    synthesize_podcast(script, output_mp3, *, backend, voice_map, gap_sec, fade_ms, on_turn)
    load_script(path)        — dispatch by extension
    script_from_json(path)
    script_from_ab_txt(path)
    script_from_text(path_or_str)

Data classes:
    Turn            — one utterance (speaker, text, opts)
    Script          — full dialogue (turns + voice_map)
    PodcastResult   — render outcome (path, duration, sr, turns, backend)
"""
from __future__ import annotations

import json
import re
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Sequence

# torch and torchaudio are imported lazily inside the functions that need them
# so that ``import podcast`` (and list_backends()) work under plain python3
# without the TTS venv — matching the same pattern as build_audio.py.


# ── data classes ──────────────────────────────────────────────────────────────

@dataclass
class Turn:
    """One utterance in a podcast script."""
    speaker: str
    text: str
    opts: dict = field(default_factory=dict)   # backend-specific knobs (exag, cfg, …)


@dataclass
class Script:
    """An ordered list of turns plus an optional speaker→voice mapping."""
    turns: list[Turn]
    voice_map: dict[str, str] = field(default_factory=dict)


@dataclass
class PodcastResult:
    """Outcome of a successful render."""
    path: Path
    duration: float   # seconds
    sr: int
    turns: int
    backend: str


# ── script loaders ────────────────────────────────────────────────────────────

def script_from_json(path) -> Script:
    """Parse a JSON script file.

    Three supported shapes:

    **Two-host** (existing)::

        {
          "turns": [{"speaker": "A", "line": "Hello.", "exag": 0.9}, ...],
          "speaker_map": {"A": "heart", "B": "fenrir"}   // optional
        }

    **Monologue** (single-speaker shorthand)::

        {
          "voice": "heart",
          "turns": [{"line": "First segment."}, {"line": "Second.", "exag": 0.6}]
        }

    **PRD shape** (single-host with explicit per-turn voice)::

        {
          "host_mode": "single",
          "voice": "narrator",
          "turns": [{"voice": "narrator", "line": "First segment.", "exag": 0.6}]
        }

    Speaker resolution per turn (first match wins):
      1. ``turn["voice"]``   — PRD shape per-turn logical voice
      2. ``turn["speaker"]`` — two-host shape
      3. top-level ``voice`` — monologue / PRD shape fallback

    ``host_mode`` is accepted and ignored (reserved for future dialogue support).

    When top-level ``voice`` is present and no ``speaker_map`` is given, the
    top-level voice becomes an identity mapping so the backend's
    ``resolve_voice()`` finds it in the map without a model lookup.

    ``line`` **or** ``text`` are accepted as the turn text field.
    ``exag`` (and any extra per-turn keys) are stored in ``Turn.opts``.
    """
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    top_voice: str | None = data.get("voice")
    voice_map: dict[str, str] = data.get("speaker_map", {})

    # Monologue / PRD form: top-level voice becomes an identity mapping so the
    # backend's resolve_voice() finds it in the map without a model lookup.
    if top_voice and not voice_map:
        voice_map = {top_voice: top_voice}

    turns: list[Turn] = []
    for t in data["turns"]:
        # Per-turn speaker resolution: turn voice > turn speaker > top-level voice.
        speaker = t.get("voice") or t.get("speaker") or top_voice
        if speaker is None:
            raise ValueError(
                "script_from_json: turn has no 'voice', no 'speaker', and no top-level "
                f"'voice' — turn data: {t!r}"
            )
        # Accept 'line' or 'text' as the text field ('line' preferred).
        if "line" in t:
            text = t["line"]
        elif "text" in t:
            text = t["text"]
        else:
            raise ValueError(
                f"script_from_json: turn has neither 'line' nor 'text' key — turn data: {t!r}"
            )
        opts = {k: v for k, v in t.items() if k not in ("voice", "speaker", "line", "text")}
        turns.append(Turn(speaker=speaker, text=text, opts=opts))
    return Script(turns=turns, voice_map=voice_map)


def script_from_ab_txt(path) -> Script:
    """Parse an ``A:`` / ``B:`` dialogue text file into a Script.

    Lines not matching ``A:`` or ``B:`` (comments, blanks) are skipped.
    """
    turns: list[Turn] = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        m = re.match(r"\s*([AB]):\s*(.+)", line)
        if m:
            turns.append(Turn(speaker=m.group(1), text=m.group(2).strip()))
    return Script(turns=turns)


def script_from_text(path_or_str) -> Script:
    """Treat the entire input as a single-speaker monologue (speaker=``host``).

    *path_or_str* may be a file path (read from disk) or a raw string
    (used as-is).  No sentence splitting — the whole text is one Turn so
    callers can control chunking upstream.
    """
    p = Path(path_or_str)
    try:
        text = p.read_text(encoding="utf-8") if p.exists() else str(path_or_str)
    except (OSError, ValueError):
        text = str(path_or_str)
    return Script(turns=[Turn(speaker="host", text=text.strip())])


def load_script(path) -> Script:
    """Auto-select a loader by file extension.

    - ``.json`` → :func:`script_from_json`
    - ``.txt``  → :func:`script_from_ab_txt`
    - anything else → :func:`script_from_text`
    """
    p = Path(path)
    if p.suffix == ".json":
        return script_from_json(p)
    if p.suffix == ".txt":
        return script_from_ab_txt(p)
    return script_from_text(p)


# ── audio helpers (ported from chatterbox_render.py) ─────────────────────────

def _to_mono_2d(w: "torch.Tensor") -> "torch.Tensor":
    """Ensure tensor is 2-D mono [1, N]."""
    if w.dim() == 1:
        return w.unsqueeze(0)
    if w.shape[0] > 1:
        return w.mean(dim=0, keepdim=True)
    return w


def _apply_fade(wav: "torch.Tensor", sr: int, fade_ms: float = 50.0) -> "torch.Tensor":
    """Apply a linear fade-in and fade-out to eliminate boundary clicks."""
    import torch
    n = min(int(sr * fade_ms / 1000), wav.shape[-1] // 2)
    if n < 2:
        return wav
    ramp = torch.linspace(0.0, 1.0, n)
    wav = wav.clone()
    wav[..., :n] *= ramp
    wav[..., -n:] *= ramp.flip(0)
    return wav


def _resample(wav: "torch.Tensor", src_sr: int, dst_sr: int) -> "torch.Tensor":
    """Resample *wav* from *src_sr* to *dst_sr*.  No-op when rates are equal."""
    if src_sr == dst_sr:
        return wav
    import torchaudio as ta
    return ta.functional.resample(wav, src_sr, dst_sr)


def to_mp3(src_path, mp3_path) -> None:
    """Encode any audio file to mp3 via ffmpeg (libmp3lame, VBR q2)."""
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", str(src_path),
         "-codec:a", "libmp3lame", "-q:a", "2", str(mp3_path)],
        check=True,
    )


# ── main render entry point ───────────────────────────────────────────────────

def synthesize_podcast(
    script,
    output_mp3,
    *,
    backend: str = "chatterbox",
    voice_map: dict | None = None,
    gap_sec: float = 0.2,
    fade_ms: float = 50.0,
    on_turn: Callable[[int, int, Turn], None] | None = None,
    global_opts: dict | None = None,
) -> PodcastResult:
    """Synthesize a full podcast and write it to *output_mp3*.

    Parameters
    ----------
    script:
        A :class:`Script`, a ``list[Turn]``, or a path/string passed to
        :func:`load_script`.
    output_mp3:
        Destination ``.mp3`` path.  Parent directories are created as needed.
    backend:
        Name of a registered :class:`~podcast.backends.base.TTSBackend`.
    voice_map:
        Optional caller override mapping speaker labels to backend voices.
        Merged *over* ``script.voice_map`` (caller wins on conflict).
    gap_sec:
        Silence duration between turns (seconds).
    fade_ms:
        Fade-in / fade-out duration per clip (milliseconds).
    on_turn:
        Optional progress callback ``(i, n, turn)`` called before each turn.

    Returns
    -------
    :class:`PodcastResult`
    """
    # --- normalise input -----------------------------------------------------
    if isinstance(script, (str, Path)):
        script = load_script(script)
    elif isinstance(script, list):
        script = Script(turns=script)

    turns: list[Turn] = script.turns
    if not turns:
        raise ValueError("synthesize_podcast: script has no turns to render")

    # --- resolve backend -----------------------------------------------------
    from podcast.backends.registry import get_backend  # local import — stays lazy

    be = get_backend(backend)
    be.load()
    sr = be.sr

    # --- merge voice maps (caller > script) ----------------------------------
    effective_voice_map: dict[str, str] = dict(script.voice_map)
    if voice_map:
        effective_voice_map.update(voice_map)

    # --- synthesize turns ----------------------------------------------------
    import torch
    import torchaudio as ta

    output_mp3 = Path(output_mp3)
    output_mp3.parent.mkdir(parents=True, exist_ok=True)
    silence = torch.zeros(1, int(sr * gap_sec))

    segments: list[torch.Tensor] = []
    n = len(turns)

    try:
        with tempfile.TemporaryDirectory() as td:
            for i, turn in enumerate(turns):
                if on_turn:
                    on_turn(i, n, turn)

                voice = be.resolve_voice(turn.speaker, effective_voice_map)
                merged = {**(global_opts or {}), **turn.opts}
                opts = be.filter_opts(merged)
                wav = be.synthesize(turn.text, voice=voice, **opts)

                wav = _resample(wav, sr, sr)   # no-op; placeholder if sr diverges
                wav = _to_mono_2d(wav)
                wav = _apply_fade(wav, sr, fade_ms)
                segments.append(wav)

                if i < n - 1:
                    segments.append(silence)

            full = torch.cat(segments, dim=-1)
            wav_out = Path(td) / "full.wav"
            ta.save(str(wav_out), full, sr, backend="soundfile")
            to_mp3(wav_out, output_mp3)
    finally:
        be.unload()  # free model RAM immediately; audio already written to disk

    duration = full.shape[-1] / sr
    return PodcastResult(
        path=output_mp3,
        duration=duration,
        sr=sr,
        turns=n,
        backend=backend,
    )
