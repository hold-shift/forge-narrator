# forge-narrator

[![Licence: MIT](https://img.shields.io/badge/licence-MIT-blue.svg)](#licence)
![Version](https://img.shields.io/badge/version-0.1.0-success)
![Python](https://img.shields.io/badge/python-3.11-3776AB?logo=python&logoColor=white)
![Voice: ElevenLabs](https://img.shields.io/badge/voice-ElevenLabs%20eleven__v3-5A37F0)
![Built for NotebookLM](https://img.shields.io/badge/built%20for-NotebookLM-4285F4?logo=googlebard&logoColor=white)

**Turn a [Notebook Forge](https://github.com/hold-shift/notebook-forge) document
into narrated audio with word‑level timing — a single MP3 plus the timing data a
web player needs to highlight each word as it's spoken and let the reader
click‑to‑seek anywhere in the text.**

forge-narrator is the standalone audio companion to Notebook Forge. Notebook
Forge stays "smart" — it knows the document structure and writes the exact text
to speak per block; forge-narrator stays "dumb" — it runs that text through
ElevenLabs, stitches the audio, derives the word timings, and (optionally)
publishes the result. The two tools never call each other: the interface is a
**manifest zip** exported by Notebook Forge and three output files served from a
public base URL.

> ### ℹ️ Why a separate tool
>
> Audio generation is paid, slow, and machine‑specific, so it's split out of the
> publishing app. ElevenLabs' `/with-timestamps` endpoint returns **per‑character
> timing alongside the audio in one call**, so there is no forced‑alignment stage
> — word timing is a by‑product of synthesis. Everything runs locally on one Mac;
> nothing is sent anywhere except ElevenLabs (synthesis) and Cloudflare R2 (the
> upload you explicitly trigger).

## How it works

```
   manifest.zip   (from Notebook Forge — per-block plain text + hashes, voice, model)
        │
        ▼
   ┌────────────────────────────────────────────────────┐
   │ per block, cached by content hash (parallel):       │
   │   ElevenLabs /with-timestamps → block mp3 + char    │
   │   timings → grouped into word marks                 │
   │   only changed blocks are re-synthesised            │
   └───────────────────────────┬─────────────────────────┘
                               ▼
   ┌────────────────────────────────────────────────────┐
   │ ffmpeg stitch + deterministic silence seams         │
   │   → document.mp3          (constant bitrate, seekable)│
   │ shift each block's marks by its stitch offset        │
   │   → document.marks.json   ([{word,start,end}], secs) │
   │ emit document.blocks.json (text + word/time spans)   │
   └───────────────────────────┬─────────────────────────┘
                               │  forge-narrator upload {slug}
                               ▼
   Cloudflare R2 → https://pub-….r2.dev/{slug}
                   paste into Notebook Forge → document's Narration panel
```

The three files share one word ordering, so the player aligns marks ↔ text by a
running word index. That invariant is sacrosanct: the order of words in the text,
the mp3, the marks and the blocks is identical.

## Requirements

- **macOS** (the M2 MacBook Air — single machine, no GPU/alignment stage).
- **Python 3.11**.
- **ffmpeg + ffprobe** — a hard dependency (`brew install ffmpeg`).
- An **ElevenLabs API key** (synthesis is paid; voice `fjnwTZkKtQOJaYzGLa6n`,
  model `eleven_v3`).
- For publishing only: **[wrangler](https://developers.cloudflare.com/workers/wrangler/)**
  (used via `npx`, no global install needed) and a **Cloudflare R2** bucket.

## Install

```sh
git clone https://github.com/hold-shift/forge-narrator.git
cd forge-narrator

python3.11 -m venv .venv && source .venv/bin/activate
pip install -e .            # core (no runtime Python deps — ElevenLabs via stdlib)
pip install -e '.[web]'     # + the local web console (FastAPI · uvicorn)
brew install ffmpeg         # ffmpeg + ffprobe
```

## Usage

```sh
# 1. Preview — characters + ElevenLabs credit cost, no API calls
forge-narrator estimate manifest.zip

# 2. Generate — synthesise → stitch → assemble into out/{slug}/
forge-narrator generate manifest.zip                 # prompts for cost confirmation
forge-narrator generate manifest.zip --yes           # skip the prompt
forge-narrator generate manifest.zip --upload        # … and publish to R2 when done
forge-narrator generate manifest.zip --no-cache      # force full re-synthesis
forge-narrator generate manifest.zip --char-cap 200000   # refuse beyond a budget

# 3. Publish — push out/{slug}/ to R2 and print the base URL
forge-narrator upload {slug} --dry-run               # show the commands, upload nothing
forge-narrator upload {slug}

# 4. Web console — the whole flow in a browser (localhost only)
forge-narrator serve                                 # → http://127.0.0.1:8765
```

ElevenLabs bills **1 character = 1 credit**. `generate` always prints the uncached
character/credit count and an approximate cost and won't make a paid call without
`--yes` (or the web **Generate** button). Caching is content‑addressed, so after a
small Notebook Forge edit only the changed blocks are re‑synthesised — a one‑line
fix is pennies, not a full re‑render.

## Web console

`forge-narrator serve` launches a single‑page console (FastAPI, bound to
`127.0.0.1`) over the same pipeline: **choose** a manifest → **pre‑flight** cost
summary → **Generate** (the cost gate, re‑checked server‑side) with live progress
→ **Preview** in the player (served with HTTP Range, so click‑to‑seek works) →
**Publish to R2** and copy the base URL. The ElevenLabs and Cloudflare credentials
never leave the backend.

## Publishing to R2

forge-narrator shells out to `wrangler` (no extra Python deps); the Cloudflare
token lives inside wrangler — never read, stored, or logged here.

```sh
# one-time setup
npx --yes wrangler@latest login
npx --yes wrangler@latest r2 bucket create notebook-forge-audio
npx --yes wrangler@latest r2 bucket dev-url enable notebook-forge-audio
npx --yes wrangler@latest r2 bucket cors set notebook-forge-audio --file out/r2-cors.json
```

Objects land at `{bucket}/{slug}/document.{mp3,marks.json,blocks.json}`, so the
base URL is `{base}/{slug}` (the player appends `/document.mp3` etc.). Re‑running
overwrites. The tool never creates the bucket, enables public access, or sets
CORS — those are the one‑time setup above.

## The manifest (the contract Notebook Forge writes)

```jsonc
{
  "document_slug": "junior",
  "title": "Junior",
  "voice": "fjnwTZkKtQOJaYzGLa6n",   // ElevenLabs voice id
  "model": "eleven_v3",
  "blocks": [
    { "index": 0, "type": "heading",   "ssml": "Prologue",  "hash": "<sha256>" },
    { "index": 1, "type": "paragraph", "ssml": "Junior …",  "hash": "<sha256>" }
  ]
}
```

`hash = sha256(ssml + voice + model)` — the cache key and staleness spine. Blocks
carry **plain text** (eleven_v3 does not honour SSML tags — see
`docs/SSML_FINDINGS.md`); `heading` / `paragraph` / `footnote` types appear, in
order. Pacing (pauses before/after headings, between paragraphs) is added by the
stitcher as silence seams — the manifest stays pure text.

## Configuration

| Variable | Purpose | Default / how to set |
|---|---|---|
| `ELEVENLABS_API_KEY` | synthesis | env var, or a gitignored `.elevenlabs_key` file |
| `FORGE_R2_BUCKET` | R2 bucket name | `notebook-forge-audio` (or `--bucket`) |
| `FORGE_R2_BASE_URL` | custom-domain base URL | resolved from wrangler (or `--base-url`) |

The Cloudflare token is held by wrangler (`wrangler login`); the ElevenLabs key
is read from the env var or the local key file. Neither is ever written to logs,
output, or git.

## Project layout

```
src/forge_narrator/
  cli.py            estimate · generate · serve · upload
  manifest.py       manifest reader + validation; hashing.py · cache.py · cost.py
  synth.py          ElevenLabs /with-timestamps (parallel, cached, 429 backoff)
  stitch.py         ffmpeg stitch + silence seams + offsets
  marks.py          char→word grouping + document-marks assembly
  blocks_json.py    the player's render source
  pipeline.py       end-to-end generate (with progress callback)
  upload.py         Cloudflare R2 upload via wrangler
  web/              FastAPI console (server.py) + static SPA
poc/                proof-of-concept seeds + player.html (the sync checker)
docs/               specs (Overview · Audio Generator · Web Interface) + SSML findings
tests/              pytest suite (offline — no network, no paid calls)
out/  ·  cache/     generated output + hash-keyed cache (gitignored)
```

## Tests

```sh
pip install -e '.[dev]' && pytest
```

The whole suite runs offline — no API key, no network, no wrangler (the ElevenLabs
and wrangler calls are monkeypatched; the ffmpeg stitch test uses a small POC clip).

## Licence

Code: **MIT** — free to use, modify and distribute. Generated audio, manifests and
the documents they come from are not part of this repository and remain yours.
