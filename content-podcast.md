---
description: Turn a finished blog .md into a single-narrator podcast .mp3 via the remote render server (Kokoro or Chatterbox), with an integrated comprehension self-check and Whisper QA.
argument-hint: <blog.md> [--engine kokoro|chatterbox] [--voice-name af_heart | --voice clip.wav] [--out episode.mp3]
---

# /content-podcast

Convert a finished blog post into a single-narrator podcast episode. You (the
assistant) rewrite the prose into spoken segments, self-check the draft against the
source, then call the **remote render server** to produce the `.mp3` and run QA.

The render models (Chatterbox / Kokoro / Whisper) live on a remote Mac reached over an
ngrok tunnel. This command never loads a model locally — it only writes `script.json`
and drives `call_remote.py`.

---

## Inputs

Parse from `$ARGUMENTS`:

| Arg | Required | Default | Meaning |
|---|---|---|---|
| `<blog.md>` (first positional) | yes | — | Path to the finished blog markdown. |
| `--engine` | no | `chatterbox` | `kokoro` (fast preset voices) or `chatterbox` (voice cloning). |
| `--voice-name` | no | `af_heart` | **Kokoro only** — preset voice (`af_heart`, `af_sarah`, `am_adam`, …). |
| `--voice` | no | bundled narrator | **Chatterbox only** — path to a 6–10s mono `.wav` to clone. |
| `--out` | no | `<slug>.mp3` | Output mp3 path. |

**Engine ↔ voice rule (enforce this):**
- `--engine kokoro` → use `--voice-name`; **ignore** `--voice` (Kokoro cannot clone a clip).
- `--engine chatterbox` → use `--voice` if given, else the server's bundled narrator;
  **ignore** `--voice-name`.
- If the user passes the wrong voice flag for the engine, tell them and fall back to the
  engine's default rather than failing.

**Server connection** comes from the environment (the caller sets these once):
- `PODCAST_URL` — the ngrok https URL of the render server.
- `PODCAST_TOKEN` — the bearer token printed by `start.command`.

If either is missing, stop and ask the user for the URL + token from the Mac host.

---

## Step 1 — Read the blog

Read `<blog.md>`. Derive `<slug>` from the filename (strip directories + `.md`).
If `--out` was not given, set it to `<slug>.mp3`.

## Step 2 — Rewrite prose → spoken segments

Rewrite the article into natural single-narrator narration. One `turn` per segment
(roughly one paragraph). Apply these transforms:

- **Headings** → spoken transitions ("Next, let's talk about …"), not read verbatim.
- **Links** → plain words; **never read a URL aloud**.
- **Code fences** → a short spoken summary of what the code does, or skip if incidental.
- **Images / diagrams** → skip; never say "as shown below" or "see the diagram".
- Preserve meaning and a warm, natural narrating voice. No new facts.

## Step 3 — Obey the renderer's chunker rules (hard requirement)

Every `line` MUST satisfy ALL of these or the render aborts:
- Starts with a capital letter.
- Ends with terminal punctuation: `.` `!` `?` (a closing quote after it is fine).
- No leading punctuation.
- Balanced quotes.

Fix any line that violates these before emitting.

## Step 4 — Self-check (integrated evaluation, max 2 revision passes)

Before emitting, review your own draft against the blog:
- **Coverage** — does the narration carry the blog's 3–7 key takeaways? Target ≥ 80%.
- **Faithfulness** — every claim is supported by the blog; nothing invented.
- **Standalone** — no "as shown below" / "the diagram" / bare-URL / "read this code"
  references that only make sense on the page.

If any check fails, revise (bounded: at most 2 passes), then proceed. This is a
self-review inside this one command — by design, not a separate grader.

## Step 5 — Emit `script.json` (PRD shape)

Write `<slug>.json` next to the output:

```json
{
  "host_mode": "single",
  "voice": "narrator",
  "turns": [
    { "voice": "narrator", "line": "First spoken segment." },
    { "voice": "narrator", "line": "Second spoken segment." }
  ]
}
```

- `host_mode` is always `"single"` this round.
- Every turn uses `"voice": "narrator"` — the *engine voice* is chosen at render time by
  the flags below, not inside the script.
- `exag` (0.0–1.0) is **Chatterbox-only** expressiveness; you may add it per turn for
  chatterbox renders. Kokoro ignores it harmlessly, so it is safe to leave in either way.

## Step 6 — Render via the remote server

Run `call_remote.py` with the engine-appropriate flags.

**Kokoro (fast preset voice):**
```bash
python call_remote.py render \
  --url   "$PODCAST_URL" \
  --token "$PODCAST_TOKEN" \
  --script <slug>.json \
  --backend kokoro \
  --voice-name <voice-name> \
  --out <out>.mp3
```

**Chatterbox (voice cloning, or bundled narrator if no clip):**
```bash
python call_remote.py render \
  --url   "$PODCAST_URL" \
  --token "$PODCAST_TOKEN" \
  --script <slug>.json \
  --backend chatterbox \
  --voice <clip.wav> \   # omit this line to use the server's bundled narrator
  --out <out>.mp3
```

On success the server returns the mp3 and `call_remote.py` writes `<out>.mp3` + a size line.

## Step 7 — Whisper QA

Transcribe the result and diff it against the script:
```bash
python call_remote.py transcribe \
  --url   "$PODCAST_URL" \
  --token "$PODCAST_TOKEN" \
  --mp3   <out>.mp3 \
  --script <slug>.json \
  --out   <slug>.transcript.json
```

Read `overlapPct` from the transcript JSON:
- **≥ 85%** → report `QA: PASS (NN% overlap)`.
- **< 85%** → report `QA: WARN (NN% overlap)` and list the mismatched segments. A low
  overlap is often a Whisper mistake (homophones, proper nouns), not a bad render — flag
  it for human review, do not silently fail.

## Step 8 — Report

Summarize for the user:
- engine + voice used,
- `<out>.mp3` path and size,
- QA verdict and overlap %,
- paths to `<slug>.json` and `<slug>.transcript.json`.

---

## Pitfalls

| ❌ Mistake | ✅ Correct |
|---|---|
| Reading URLs / "see the diagram" aloud | Convert to plain spoken words; skip visual-only refs |
| Passing `--voice clip.wav` with `--engine kokoro` | Kokoro can't clone — use `--voice-name`, ignore the clip |
| Emitting a line with no terminal punctuation | Every `line` ends in `.` `!` `?` or the render aborts |
| Treating QA `< 85%` as a hard failure | It's a review flag; surface mismatches, let a human judge |
| Hardcoding the URL/token | Read `PODCAST_URL` / `PODCAST_TOKEN`; ask if unset |
