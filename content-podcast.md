---
description: Turn a finished blog .md into a VALUABLE single-narrator podcast .mp3 via the remote render server (Kokoro or Chatterbox) — a spoken companion, not a read-aloud — with an integrated value/faithfulness/sayability self-check and Whisper QA.
argument-hint: <blog.md> [--engine kokoro|chatterbox] [--voice-name af_heart | --voice clip.wav] [--out episode.mp3]
---

# /content-podcast

Convert a finished blog post into a single-narrator podcast episode that is **worth a
listener's time** — a spoken *companion* to the article, not a read-aloud of it. You (the
assistant) decide what's genuinely valuable, rewrite it for the **ear**, self-check, then
call the **remote render server** to produce the `.mp3` and run QA.

## The bar (read this first — it governs every step)

The listener has NOT read the article and is doing something else (commuting, dishes). It
must make sense **with eyes closed**. Optimize for **value, not length** — never stretch
to fill time, never amputate to hit a target. The right length is "just enough": carry the
ideas worth hearing, drop everything else.

- **Companion, not transcript.** Convey the article's genuinely valuable ideas
  conceptually. Skip setup boilerplate, ceremony, and copy-paste detail — that lives on the
  page; point the listener there once for it.
- **Translate, don't transcribe.** Turn technical specifics into their concept ("a CI job
  that re-runs on every push"), not the raw syntax. Never read code, config, flags, or
  identifiers aloud.
- **Teach order, not article order.** Sequence ideas the way they actually land for a
  listener — lead with the payoff/why, not the article's preamble.
- **Natural spoken delivery.** Contractions, connective tissue ("and the thing is…",
  "so what actually happens is…"). Warm, concrete, a little wry. No hype, no filler.

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
| `--show` | no | inferred | Show name spoken in the welcome (e.g. `"Ship With A I"`). If omitted, infer the publication/brand from the article's source; if none is clear, use a plain welcome without a brand. Always spell it sayably (`AI` → "A I"). |
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

## Step 2 — Find the value, then write for the ear

First, **triage**: read the whole article and decide what's genuinely worth hearing — the
ideas, the "why", the one or two things that change how the listener works. Discard the
rest (boilerplate intros, exhaustive option lists, copy-paste blocks). Let the count of
ideas — not a word target — decide the length.

**Open natural — hook first, THEN welcome (do not skip this).** The very first turns are:
1. **Cold-open hook** (1–2 turns) — open on the single most surprising or highest-stakes
   point. NO greeting yet; make a stranger lean in.
2. **Bridge into the welcome** (1 turn) — connect *from* the hook into the welcome, don't
   reset. Start with a connector that ties back ("And if that sounds familiar…", "If any of
   that hits close to home…"), then welcome the listener to the show by name and say, in
   plain words, what this episode is about. One natural sentence or two — warm, brief.
   - With `--show`: e.g. *"And if that sounds familiar — welcome to Ship With A I. Today,
     how to make your A I pull-request reviews run while you sleep."*
   - Without a clear show name: a plain welcome — *"And if that sounds familiar, you're in
     the right place. Today we're digging into…"* — no invented brand.
3. Then flow into the body below.

Never drop a flat "Welcome to the show" with no connection to the hook, and never read the
show name in an un-sayable form. Close with a warm sign-off and one thing to try.

Then rewrite the kept ideas into natural single-narrator narration, one `turn` per
developed thought (mix longer breathing turns that build a point with short ones):

- **Headings** → spoken transitions ("So here's where it gets interesting…"), never read
  verbatim.
- **Links / URLs** → plain words; **never read a URL aloud**.
- **Code / config / commands** → the *concept* of what it does, never the syntax. "A small
  script that fails the build if coverage drops" — not the script.
- **Images / diagrams / tables** → convey the takeaway in words, or skip; never say "as
  shown below" or "see the diagram".
- **Signpost once** — at least once, point to the article for the copy-paste detail ("the
  full config's in the post if you want to lift it"). Audio = understanding; page = detail.

## Step 3 — Make every token sayable (eyes-free rules — this is what makes or breaks it)

The TTS reads literally. A raw identifier gets mangled ("T0" → "tee-zero", "DATABASE_URL"
→ "database underscore U R L"). Convert **every** un-sayable token to spoken words BEFORE
emitting:

- **Acronyms** → spaced letters: `MCP` → "M C P", `API` → "A P I", `OpenAPI` → "open A P I".
- **Identifiers / job names / codes** → the concept: `T0/T1/T3` → "the first job… the
  third job"; `STRIPE_SECRET_KEY` → "the Stripe secret key".
- **Numbers, %, versions, years** → words: `50%` → "about fifty percent", `2026` → "twenty
  twenty-six", `v1.2` → "version one point two", `204` → "a two-oh-four".
- **camelCase / dotted names** → spell or conceptualize: `settings.json` → "the settings
  file", `TypeORM` → "Type O R M".
- **Filenames / paths / flags** → spoken form: `CLAUDE.md` → "the CLAUDE dot M D file",
  `--strict` → "the strict flag".
- When in doubt, **prefer the concept over the exact token.** If a word is genuinely hard
  to pronounce (e.g. "idempotent") and not essential, say its plain meaning instead ("a bot
  you can safely run twice").

## Step 4 — Obey the renderer's chunker rules (hard requirement)

Every `line` MUST satisfy ALL of these or the render aborts:
- Starts with a capital letter.
- Ends with terminal punctuation: `.` `!` `?` (a closing quote after it is fine).
- No leading punctuation.
- Balanced quotes (opened and closed within the same line).

Fix any line that violates these before emitting.

## Step 5 — Self-check (integrated evaluation, max 2 revision passes)

Before emitting, review your own draft against the article AND the bar above:
- **Value** — would a listener finish this feeling it was worth their time? Is anything in
  here padding or boilerplate that should be cut? Is anything genuinely valuable missing?
- **Eyes-free** — does it make sense with eyes closed? Read every line as if hearing it:
  any surviving raw token, acronym, code, or number-as-digit? (Step 3) Fix them.
- **Faithfulness** — every claim supported by the article; nothing invented (no made-up
  numbers, no invented outcomes).
- **Standalone** — no "as shown below" / "the diagram" / bare-URL / "read this code"
  references that only make sense on the page.

If any check fails, revise (bounded: at most 2 passes), then proceed. This is a
self-review inside this one command — by design, not a separate grader.

## Step 6 — Emit `script.json` (PRD shape)

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

## Step 7 — Render via the remote server

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

## Step 8 — Whisper QA

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

## Step 9 — Report

Summarize for the user:
- engine + voice used,
- `<out>.mp3` path and size,
- QA verdict and overlap %,
- paths to `<slug>.json` and `<slug>.transcript.json`.

---

## Pitfalls

| ❌ Mistake | ✅ Correct |
|---|---|
| Reading the article 1:1 (audiobook) | Triage to the valuable ideas; companion, not transcript |
| Stretching to fill time / cutting to hit a target | Let value decide length — "just enough" |
| Leaving raw tokens (`T0`, `OpenAPI`, `50%`, `v1.2`) | Say them: "the first job", "open A P I", "fifty percent", "version one point two" |
| Reading code/config syntax aloud | Say what it *does*, not the syntax; signpost to the page |
| Reading URLs / "see the diagram" aloud | Convert to plain spoken words; skip visual-only refs |
| Passing `--voice clip.wav` with `--engine kokoro` | Kokoro can't clone — use `--voice-name`, ignore the clip |
| Emitting a line with no terminal punctuation | Every `line` ends in `.` `!` `?` or the render aborts |
| Treating QA `< 85%` as a hard failure | It's a review flag; surface mismatches, let a human judge |
| Hardcoding the URL/token | Read `PODCAST_URL` / `PODCAST_TOKEN`; ask if unset |
