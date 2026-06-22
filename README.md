# forge-narrator

Turns a NotebookForge SSML **manifest zip** into narrated audio with word-level
timing — the three S3 files for the Skitch Family Archive TTS player:
`document.mp3`, `document.marks.json`, `document.blocks.json`.

Standalone tool, one machine (M2 MacBook Air). See `docs/TTS_Spec_B_AudioGenerator.md`
for the build contract and `docs/TTS_Spec_0_Overview.md` for the shared architecture.

> Provider: **ElevenLabs** (`eleven_v3`, voice `fjnwTZkKtQOJaYzGLa6n`). The
> `/with-timestamps` endpoint returns word timing *with* the audio, so there is
> **no alignment stage** — timing is a by-product of synthesis.

## Pipeline

```
manifest.zip → ElevenLabs /with-timestamps per block (cached by hash,
             synthesised ~8 in parallel) → group chars→words → ffmpeg stitch
             (+ offset table) → marks shifted by offsets → out/{slug}/ (3 files)
```

## Setup

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e .            # no runtime Python deps (ElevenLabs via stdlib urllib)
brew install ffmpeg        # HARD dependency (ffmpeg + ffprobe), already on the Air
```

The ElevenLabs API key is read from `ELEVENLABS_API_KEY` or a local **gitignored**
`.elevenlabs_key` file. **Never** hardcoded or logged.

## Use

```bash
# Cost preview — no API calls:
forge-narrator estimate manifest.zip

# Full run — gated by a cost confirmation before any paid call:
forge-narrator generate manifest.zip --out ./out
forge-narrator generate manifest.zip --yes              # skip the prompt
forge-narrator generate manifest.zip --char-cap 200000  # safety cap (refuse beyond)
forge-narrator generate manifest.zip --no-cache         # force full re-synthesis
forge-narrator generate manifest.zip --concurrency 6    # tune parallel requests
```

Output lands in `out/{slug}/`. The operator uploads that folder to S3 and pastes
the base URL into NotebookForge.

## Web console (`serve`)

A local single-page console over the same pipeline (Spec C): pick a manifest,
see the pre-flight cost summary, click **Generate**, watch live progress, then
**Preview** the result in the player.

```bash
pip install -e '.[web]'                       # FastAPI + uvicorn (one-time)
forge-narrator serve                          # → http://127.0.0.1:8765
forge-narrator serve --port 8791 --char-cap 4000000
```

Localhost only (binds `127.0.0.1`). The **Generate** button is the cost gate
(equivalent to `--yes`), re-checked server-side. The preview is served with HTTP
Range support, so click-to-seek works (bare `python -m http.server` does not — it
breaks `<audio>` seeking). The API key stays server-side — never sent to the
browser or logged.

### Cost guard rails (mandatory)

ElevenLabs bills **1 character = 1 credit**. `generate` prints the uncached
character/credit count and an approximate cost, and requires `--yes` or an
interactive `y` before any paid call. The full archive (~3.87M chars) is
~$390–450 on the fast-and-overage path; caching means re-runs after a small edit
re-synthesise only the changed blocks.

## Validating output against the POC player

`document.marks.json` is byte-compatible with the proven POC checker
(`poc/player.html`). To sanity-check sync for a generated document:

```bash
cd out/{slug}
cp document.marks.json sample.marks.json          # player fetches this name
# point poc/player.html's <audio src> at document.mp3 (or copy player.html here)
python3 -m http.server 8000                        # then open the page
```

Press play and confirm the amber highlight tracks the spoken word.
(`document.blocks.json` is the richer render source the real front-end player uses;
the POC checker only needs the flat marks.)

## Manifest schema (the contract NotebookForge writes)

```jsonc
{
  "document_slug": "junior",
  "title": "Junior",
  "voice": "fjnwTZkKtQOJaYzGLa6n",   // ElevenLabs voice id
  "model": "eleven_v3",
  "blocks": [
    { "index": 0, "type": "heading",   "ssml": "…", "hash": "<sha256>" },
    { "index": 1, "type": "paragraph", "ssml": "…", "hash": "<sha256>" }
  ]
}
```

`hash = sha256(ssml + voice + model)` — the cache key and staleness spine (plain
concatenation, no separator). Blocks are SSML-only (the generator derives readable
text from the SSML). `heading`/`paragraph`/`footnote` blocks appear; footnotes are
narrated but flagged `highlightable: false`. Blocks are in order, and that order is
sacred: SSML → mp3 → marks → blocks share one word index.

## Tests

```bash
pip install -e '.[dev]' && pytest    # offline: parsing, cost, char→word grouping,
                                     # offset-shift assembly, blocks.json, real-ffmpeg stitch
```

All tests run offline (no API key, no network). A real end-to-end `generate` makes
paid ElevenLabs calls.

## Development fixture

`tests/fixtures/build_fixture.py` regenerates a small valid `manifest.zip`/`.json`
from the POC sample prose (ElevenLabs dialect) for manual `estimate`/`generate` testing.
```
