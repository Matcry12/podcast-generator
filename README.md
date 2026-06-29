# Podcast Render Server

A self-contained package that turns a Mac with an Apple Silicon GPU into a
podcast render server accessible over the internet. You double-click two files
to set it up and start it; the caller sends scripts and gets back MP3s.

---

## Install & Update (Git)

**First time — clone the repo:**
```bash
git clone https://github.com/Matcry12/podcast-generator.git
cd podcast-generator
```
Then follow **Mac Setup** below (double-click `setup.command`).

**Updating later — just pull:**
```bash
cd podcast-generator
git pull
```
If `requirements.txt` changed, re-run `setup.command` to install new deps; otherwise just
double-click `start.command` again. No re-downloading a zip.

> Tip: files obtained via `git clone` are normally **not** quarantined by macOS, so the
> `.command` files usually open without the security prompt below. If they don't, use one
> of the fixes in the next section.

---

## ⚠️  macOS Security Warning — Read This First

macOS quarantines files downloaded from the internet.  
**The .command files will be blocked if you just double-click them.**

**Fix (choose one):**

**Option A — Right-click to open (easiest):**
1. Right-click `setup.command` → **Open**
2. Click **Open** in the security dialog
3. Repeat for `start.command`

**Option B — Remove quarantine in Terminal:**
```bash
xattr -d com.apple.quarantine /path/to/podcast-server/*.command
```
Then double-click normally.

---

## Choosing a Voice Engine

The server supports four TTS backends. Pass `--backend` to `call_remote.py` to select one.

| Engine | Flag | Language | Speed | Voice selection | Best for |
|---|---|---|---|---|---|
| **kokoro** | `--backend kokoro` | EN | Fast (GPU) | Preset voices via `--voice-name NAME` | English, quick turnaround |
| **chatterbox** | `--backend chatterbox` | EN | Slower (GPU) | Clone any voice via `--voice clip.wav` | Custom / branded EN voices |
| **vieneu** | `--backend vieneu` | VI | Fast (**CPU**) | Clone from `--voice clip.wav` | Vietnamese, no GPU required |
| **omnivoice** | `--backend omnivoice` | 600+ lang | Fast (GPU/MPS) | Clone from `--voice clip.wav` | Vietnamese or multilingual |

**Kokoro** uses built-in preset voices — no clip cloning. Pass `--voice-name` to choose a voice.
Common presets: `af_heart` (default), `af_sarah`, `am_adam`.
For the full list see the [Kokoro voice list](https://huggingface.co/hexgrad/Kokoro-82M).

**Chatterbox** clones the speaker from a 6–10 second mono WAV clip you supply.
Pass `--voice yourclip.wav`; omit it to use the server's built-in narrator.

**VieNeu** is a Vietnamese-only TTS engine that runs entirely on CPU — no GPU required for
Vietnamese renders. Pass `--voice yourclip.wav` for voice cloning. See the
[VieNeu repo](https://github.com/pnnbao97/VieNeu-TTS) for details.

**OmniVoice** (k2-fsa) supports 600+ languages with zero-shot voice cloning, including
Vietnamese with 8,481 h of native training data. Uses Apple Silicon GPU (MPS) automatically;
falls back to CPU. Pass `--voice yourclip.wav` to clone a voice; omit for the generic model output.

All engines share one Python 3.11 virtual environment. The `setup.command` script tests
**all four engines** during setup so any coexistence problem is caught early.

---

## What You Need

| Requirement | Notes |
|---|---|
| A Mac with Apple Silicon (M1/M2/M3/M4) | For GPU-accelerated TTS |
| macOS 13 Ventura or later | Older versions untested |
| ngrok CLI installed and logged in | For sharing a public URL — install at [ngrok.com/download](https://ngrok.com/download) and run `ngrok authtoken <your-token>` once. You manage this yourself; setup.command does not touch ngrok. |
| ~14 GB disk space | For Python 3.11 env + all four engines (Chatterbox, Kokoro, VieNeu, OmniVoice) + Whisper models |

---

## Mac Setup — 3 Steps

### Step 1: Double-click `setup.command`

This installs everything automatically:
- Homebrew check (you'll need to install it first if missing)
- `ffmpeg` (for MP3 encoding)
- `espeak-ng` (required by the Kokoro TTS engine)
- `uv` (fast Python package manager)
- **Python 3.11** virtual environment + all dependencies  
  *(3.11 is required: Kokoro needs Python <3.13; Chatterbox is tested on 3.11 — one shared venv for both)*
- Runs a fast dummy-backend pipeline self-test (ffmpeg + core sanity check)
- **Pre-downloads all four engine models + Whisper, then runs a self-test for each** — every engine renders a short test line so any coexistence problem is caught here, not mid-job

**Budget ~20–30 minutes for first-time setup** — the combined model download is ~10 GB.
Models are downloaded, cached, then released (they do not stay in memory). If you're offline,
they'll download automatically on the first real render instead.

Each engine's self-test result is shown independently (✅ / ❌) so you know exactly which
engines are ready even if one fails.

### Step 2: Double-click `start.command`

The terminal window will show something like:

```
  LOCAL URL: http://localhost:8000
  TOKEN:     a3f9...long-string...

  Server is running LOCALLY.
  To expose this to a remote caller, in ANOTHER terminal run:
    ngrok http 8000
  — then send the caller the ngrok https URL and the TOKEN above.
```

### Step 3: Share the public URL with the caller (if needed)

Open a **second Terminal window** (leave the first one running) and run:

```bash
ngrok http 8000
```

ngrok will print a public `https://` URL. **Send that URL and the TOKEN to the caller.**  
Leave both terminal windows open — closing either one stops the tunnel or the server.

---

## When You're Done

**Close the Terminal window** that `start.command` opened.  
This stops the server and frees GPU memory.

To restart later, just double-click `start.command` again (no setup needed).

---

## Caller Side

The caller uses `call_remote.py` on their own machine. It requires Python 3.8+
and `requests` (`pip install requests`).

### Render a script to MP3

**Kokoro — fast English preset voice:**

```bash
python call_remote.py render \
  --url        https://abc123.ngrok-free.app \
  --token      eyJh...long-string... \
  --script     my_episode.json \
  --backend    kokoro \
  --voice-name af_heart \
  --out        episode.mp3
```

Other Kokoro preset voices: `af_heart` (default), `af_sarah`, `am_adam`.  
For the full list see the [Kokoro voice list](https://huggingface.co/hexgrad/Kokoro-82M).

**Chatterbox — English voice cloning from a WAV clip:**

```bash
python call_remote.py render \
  --url     https://abc123.ngrok-free.app \
  --token   eyJh...long-string... \
  --script  my_episode.json \
  --backend chatterbox \
  --voice   my_voice_sample.wav \
  --out     episode.mp3
```

Pass `--voice yourclip.wav` with a clean 6–10 second mono WAV (minimal background noise,
single speaker). Omit `--voice` to use the server's built-in narrator clip.

**VieNeu — Vietnamese (CPU, no GPU required):**

```bash
python call_remote.py render \
  --url     https://abc123.ngrok-free.app \
  --token   eyJh...long-string... \
  --script  my_episode.json \
  --backend vieneu \
  --voice   my_vi_voice_sample.wav \
  --out     episode.mp3
```

**OmniVoice — Vietnamese or multilingual (GPU/MPS):**

```bash
python call_remote.py render \
  --url     https://abc123.ngrok-free.app \
  --token   eyJh...long-string... \
  --script  my_episode.json \
  --backend omnivoice \
  --voice   my_voice_sample.wav \
  --out     episode.mp3
```

Omit `--voice` to use the generic model output (no cloning).

Output: `episode.mp3` + a confirmation line with the file size.

### Transcribe an MP3 (with optional script alignment)

```bash
python call_remote.py transcribe \
  --url   https://abc123.ngrok-free.app \
  --token eyJh...long-string... \
  --mp3   episode.mp3 \
  --script my_episode.json \
  --out   transcript.json
```

Output: `transcript.json` containing `{text, segments, overlapPct}`.

### Check server health

```bash
curl -s https://abc123.ngrok-free.app/health | python3 -m json.tool
```

Returns per-backend status with load state and any recent error:

```json
{
  "status": "ok",
  "device": "mps",
  "backends": {
    "kokoro":      {"loaded": false},
    "chatterbox":  {"loaded": false},
    "vieneu":      {"loaded": false},
    "omnivoice":   {"loaded": false, "last_error": "..."}
  }
}
```

`last_error` only appears when a backend has previously failed — useful for remote diagnosis.

### Script format (PRD shape)

```json
{
  "host_mode": "single",
  "voice": "narrator",
  "turns": [
    {"voice": "narrator", "line": "Welcome to the show.", "exag": 0.8},
    {"voice": "narrator", "line": "Today we are talking about AI agents.", "exag": 0.7}
  ]
}
```

Single-narrator is the supported mode this round. Every turn uses `narrator`, which maps to
the bundled voice (or to your `--voice` clip when you pass one).

---

## Troubleshooting

| Problem | Fix |
|---|---|
| "brew: command not found" | Install Homebrew: `/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"` |
| "Operation not permitted" | Right-click the .command file → Open, or run `xattr -d com.apple.quarantine *.command` |
| Caller can't reach the server (no public URL) | Did you run `ngrok http 8000` in a separate terminal? The server is local-only until you do. Make sure ngrok is installed and you have run `ngrok authtoken <your-token>` at least once. |
| 401 Unauthorized from caller | TOKEN is wrong — copy it exactly from the terminal window |
| First render is very slow | Models didn't pre-download during setup (e.g. you were offline) — they're downloading now; happens only once. Re-run setup.command to pre-download. |
| Render window closed by accident | Double-click start.command again; TOKEN will be different — update the caller |
