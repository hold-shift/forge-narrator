# forge-narrator

Turns a NotebookForge SSML **manifest zip** into narrated audio with word-level
timing — the three S3 files for the Skitch Family Archive TTS player:
`document.mp3`, `document.marks.json`, `document.blocks.json`.

Standalone tool, one machine (M2 MacBook Air). See `docs/TTS_Spec_B_AudioGenerator.md`
for the build contract and `docs/TTS_Spec_0_Overview.md` for the shared architecture.

## Pipeline

```
manifest.zip → Polly (Brian, generative, eu-west-2) per block, cached by hash,
             synthesised 8–10 in parallel → ffmpeg stitch (+ offset table)
             → whispermlx forced alignment → out/{slug}/ (3 files)
```

## Setup

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e .            # runtime: boto3
pip install whispermlx     # alignment backend (Apple Silicon, Python 3.11)
brew install ffmpeg        # HARD dependency (ffmpeg + ffprobe), already on the Air
```

AWS credentials come from the standard chain (`~/.aws`, user `PollyAPI-user`).
**Never** put keys in code or args.

## Use

```bash
# Cost/throughput preview — no API calls, no aligner needed:
forge-narrator estimate manifest.zip

# Full run — gated by a cost confirmation before any paid Polly call:
forge-narrator generate manifest.zip --out ./out
forge-narrator generate manifest.zip --yes          # skip the prompt
forge-narrator generate manifest.zip --char-cap 4000000   # monthly safety cap
forge-narrator generate manifest.zip --no-cache     # force full re-synthesis
forge-narrator generate manifest.zip --model medium.en    # tricky documents
```

Output lands in `out/{slug}/`. The operator uploads that folder to S3 and pastes
the base URL into NotebookForge.

### Cost guard rails (mandatory)

`generate` prints the uncached character count and estimated cost
(generative = $30 / 1M chars) and requires `--yes` or an interactive `y`. The full
archive is ~$116; caching means re-runs after a small edit re-synthesise only the
changed blocks.

## Validating output against the POC player

`document.marks.json` is byte-compatible with the proven POC checker
(`poc/player.html`). To sanity-check sync for a generated document:

```bash
cd out/{slug}
cp document.marks.json sample.marks.json          # player fetches this name
# edit poc/player.html's <audio src> to document.mp3, or copy player.html here
python3 -m http.server 8000                        # then open the page
```

Press play and confirm the amber highlight tracks the spoken word.
(`document.blocks.json` is the richer render source the real front-end player uses;
the POC checker only needs the flat marks.)

## Manifest schema (the contract NotebookForge writes)

```jsonc
{
  "version": 1,
  "slug": "junior",
  "title": "Junior",
  "voice": "Brian",
  "engine": "generative",
  "blocks": [
    { "index": 0, "type": "heading",   "text": "…", "ssml": "<speak>…</speak>", "hash": "<sha256>" },
    { "index": 1, "type": "paragraph", "text": "…", "ssml": "<speak>…</speak>", "hash": "<sha256>" }
  ]
}
```

`hash = sha256(ssml \0 voice \0 engine)` — the cache key and staleness spine.
Only `heading`/`paragraph` blocks appear; everything else is stripped by
NotebookForge per the Overview's "What is narratable" table. Blocks are in order,
and that order is sacred: SSML → mp3 → marks → blocks share one word index.

## Tests

```bash
pip install pytest && pytest          # offline: parsing, cost, stitch (real ffmpeg), alignment invariant
```

The alignment test injects a fake `whispermlx` so it runs without a GPU model; a
real end-to-end run needs `whispermlx` installed and makes paid Polly calls.

## Development fixture

`tests/fixtures/build_fixture.py` regenerates a small valid `manifest.zip`/`.json`
from the POC sample prose for manual `estimate`/`generate` testing.
