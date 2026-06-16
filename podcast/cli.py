"""Command-line interface for the podcast TTS tool.

Usage examples:

    # Two-speaker dialogue from a JSON script:
    python -m podcast script.json out.mp3

    # A/B text file with the chatterbox backend:
    python -m podcast dialogue.txt out.mp3 --backend chatterbox

    # Single-speaker monologue, force a specific voice:
    python -m podcast narration.txt out.mp3 --voice fenrir --speaker narrator

    # Smoke-test without a GPU:
    python -m podcast script.json /tmp/smoke.mp3 --backend dummy

    # List registered backends:
    python -m podcast --list-backends
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m podcast",
        description="Generate a podcast mp3 from a script file.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "input",
        nargs="?",
        help="Script file (.json → JSON turns, .txt → A:/B: dialogue, other → monologue)",
    )
    parser.add_argument(
        "output",
        nargs="?",
        help="Output mp3 path",
    )
    parser.add_argument(
        "--backend",
        default="chatterbox",
        metavar="NAME",
        help="TTS backend to use (default: chatterbox)",
    )
    parser.add_argument(
        "--voice",
        default=None,
        metavar="VOICE",
        help="Override: force ALL turns to this voice",
    )
    parser.add_argument(
        "--speaker",
        default="host",
        metavar="LABEL",
        help="Speaker label for monologue text input (default: host)",
    )
    parser.add_argument(
        "--gap",
        type=float,
        default=0.2,
        metavar="SEC",
        help="Silence between turns in seconds (default: 0.2)",
    )
    parser.add_argument(
        "--fade",
        type=float,
        default=50.0,
        metavar="MS",
        help="Fade in/out duration per clip in milliseconds (default: 50.0)",
    )
    parser.add_argument(
        "--list-backends",
        action="store_true",
        help="Print registered backends and exit",
    )
    return parser


def _on_turn(i: int, n: int, turn) -> None:
    """Progress printer — mirrors the old chatterbox_render._log_turn style."""
    opts_str = ", ".join(f"{k}={v}" for k, v in turn.opts.items()) if turn.opts else ""
    opts_part = f" [{opts_str}]" if opts_str else ""
    preview = turn.text[:55] + ("…" if len(turn.text) > 55 else "")
    print(f"  [{i + 1:>2}/{n}] {turn.speaker:10s}{opts_part}: {preview}")


def main(argv=None) -> None:
    parser = _build_parser()
    args = parser.parse_args(argv)

    # ── --list-backends ───────────────────────────────────────────────────────
    if args.list_backends:
        from podcast.backends.registry import list_backends
        backends = list_backends()
        for name in backends:
            print(name)
        sys.exit(0)

    # ── validate required args ────────────────────────────────────────────────
    if not args.input or not args.output:
        parser.error("input and output are required unless --list-backends is used")

    # ── load script -----------------------------------------------------------
    from podcast.core import Script, Turn, load_script, script_from_text

    input_path = Path(args.input)
    if input_path.exists():
        script = load_script(input_path)
        # If --speaker was given and the file is a plain text (not json/txt),
        # override the default "host" speaker label.
        if input_path.suffix not in (".json", ".txt") and args.speaker != "host":
            for t in script.turns:
                t.speaker = args.speaker
    else:
        # Treat raw string as monologue
        script = script_from_text(args.input)
        for t in script.turns:
            t.speaker = args.speaker

    # ── optional voice override ───────────────────────────────────────────────
    voice_map: dict | None = None
    if args.voice:
        # Map every unique speaker to the forced voice
        speakers = {t.speaker for t in script.turns}
        voice_map = {s: args.voice for s in speakers}

    # ── render ────────────────────────────────────────────────────────────────
    from podcast.core import synthesize_podcast

    result = synthesize_podcast(
        script,
        args.output,
        backend=args.backend,
        voice_map=voice_map,
        gap_sec=args.gap,
        fade_ms=args.fade,
        on_turn=_on_turn,
    )

    print(
        f"✓ {result.path}  ({result.duration:.1f}s, {result.turns} turns,"
        f" backend={result.backend})"
    )


if __name__ == "__main__":
    main()
