# TTS Spec B — Audio Generator (`forge-narrator`)

*Read `TTS_Spec_0_Overview.md` first. Standalone tool, separate from
NotebookForge. Consumes a manifest zip, produces the three S3 files. It is the
only tool that touches the TTS provider.*

Location: `/Users/cs/ClaudeCode/forge-narrator/`. Runs on the **M2 MacBook Air**.

> **STATUS: this is now a CHANGE spec.** forge-narrator was already built and
> validated against Polly + whispermlx (26 tests, end-to-end run on the Junior
> fixture). The decision is to **switch the provider from Amazon Polly to
> ElevenLabs**, which also **removes the whole alignment stage**. Most of the
> pipeline (manifest, hashing, cache, stitch, blocks.json, CLI, guard rails)
> stays; `synth.py` is rewritten and `align.py` is deleted. Sections below are
> marked CHANGE / DELETE / UNCHANGED.

---

## 0. Why the change

A/B test on real Junior prose: ElevenLabs is clearly higher quality than Polly
generative (Brian). Decisively better, operator-confirmed. Crucially, ElevenLabs'
`/with-timestamps` endpoint returns **character-level timing alongside the audio
in one call** — the Speech-Marks capability Polly generative lacked, which is the
only reason whispermlx existed. So switching providers also **deletes the entire
alignment stage**: the pipeline gets simpler, not more complex.

Proven in the POC: `poc/elevenlabs_probe.py` already calls `/with-timestamps`,
captures `normalized_alignment`, and groups characters → words in the exact
`marks.json` format the player consumes. Use it as the seed for the new `synth.py`.

---

## 1. What it does (end to end) — CHANGED

```
manifest.zip (from NotebookForge)
   │
   1. unzip → manifest.json  (blocks[].ssml + hash, voice, model)
   │
   2. per block (PARALLEL):
        cache hit by hash?  → reuse cached block .mp3 + block .marks
        miss → ElevenLabs POST /v1/text-to-speech/{voice_id}/with-timestamps
                 → block .mp3  +  normalized_alignment (char timings)
                 → group chars→words → block-local word marks
                 → cache both by hash
   │
   3. stitch block mp3s in order → document.mp3
        record each block's cumulative start offset
   │
   4. assemble document.marks.json:
        for each block in order, shift its block-local word times by the
        block's stitch offset → flat [{word,start,end}] in document time
   │
   5. emit document.blocks.json (block order, text, word + time spans, highlightable)
   │
   6. write all three into out/{slug}/  (operator uploads to S3)
```

No alignment step. No transcript step. Timing is a by-product of synthesis.

---

## 2. Tech / environment — CHANGED

### 2.1 Stack
- **Python 3.11** (unchanged).
- **HTTP to ElevenLabs** — no SDK needed; stdlib `urllib` works (the POC probe
  uses it). `requests` is fine too if preferred. **Remove `boto3`.**
- **Remove `whispermlx` / `mlx` / `torch`** entirely — no aligner. This drops the
  heaviest dependencies and the model-download step.
- **`ffmpeg`** — STILL a hard dependency (mp3 decode + concat stitching). Already
  installed on the Air.
- **API key** via env var `ELEVENLABS_API_KEY`, read from a local gitignored file
  if needed (the POC reads `/Users/cs/Documents/Claude/tts-test/.elevenlabs_key`).
  NEVER hardcode; never log the key.

### 2.2 SSML — must be re-verified for ElevenLabs
**Do NOT assume Polly's SSML carries over.** ElevenLabs does NOT honour
`<prosody>`, and its `<break>` handling differs (it supports `<break time="..."/>`
on some models but discourages overuse; expressiveness is model-driven). So:
- First task of this change: a tiny probe — synthesise a heading + paragraph with
  the manifest's SSML through the chosen ElevenLabs voice/model, listen, and
  record what's honoured in `SSML_FINDINGS.md` (overwrite the Polly findings).
- The manifest's SSML dialect is decided in Spec A; the two specs must agree.
  Likely outcome: lighter SSML (maybe just `<break>` between blocks, or plain
  text with the generator relying on per-block synthesis for pacing). Confirm,
  don't guess.
- The inter-block break is **no longer load-bearing** for word→block mapping
  (that now comes from per-block synthesis — see Overview). Keep a small seam
  silence if it helps stitching not clip; otherwise SSML is purely for prosody.

---

## 3. ElevenLabs call (per uncached block) — REPLACES the Polly section

```
POST https://api.elevenlabs.io/v1/text-to-speech/{voice_id}/with-timestamps
  headers: xi-api-key: $ELEVENLABS_API_KEY,  Content-Type: application/json
  body: { "text": <block ssml or text>, "model_id": "eleven_v3" }
  → 200: { audio_base64, alignment, normalized_alignment }
```

- Decode `audio_base64` → block mp3.
- Use **`normalized_alignment`** (matches spoken output). Group its `characters`
  + `character_start_times_seconds` + `character_end_times_seconds` into words:
  split on whitespace; word.start = first char start, word.end = last char end.
  (Exact logic already in `poc/elevenlabs_probe.py::group_chars_to_words`.)
- Voice id + model come from the manifest (`voice`, `model`); region N/A.

### 3a. PARALLELISE — still required
Synthesise blocks concurrently (thread pool, ~5–10 in flight). ElevenLabs has
per-tier concurrency limits (Free/Starter low; Creator/Pro higher) — make
concurrency configurable and back off on HTTP 429 rather than failing the run.

### 3b. Guard rails — CHANGED to ElevenLabs credit model
- Billing: **1 character = 1 credit** on Multilingual v2/v3. Estimate cost from
  uncached character count. Full archive ≈ 3.87M chars; chosen path is
  fast-and-overage on a paid tier (~$390–450). `estimate` prints char count and
  credit cost; `generate` requires `--yes` / interactive confirm.
- Respect a configurable character cap; refuse beyond it.
- On block error: retry once (with backoff on 429), then surface the block index
  and stop cleanly.

---

## 4. Caching — UNCHANGED (but cache the marks too)
`cache/{hash}.mp3` keyed by `sha256(ssml + voice + model)`. **Also cache the
block-local word marks** (`cache/{hash}.marks.json`) so a cached block contributes
its timing on re-run without re-calling the API. Hash must include the model id
(and voice) so changing either invalidates correctly. `--no-cache` forces regen.

---

## 5. Stitching + offsets — UNCHANGED
ffmpeg concat block mp3s in manifest order → `document.mp3`, accumulating each
block's cumulative start offset. These offsets shift block-local word times into
document-global time (§1 step 4) and feed `document.blocks.json`.

---

## 6. Word timing — REPLACES the whispermlx section (DELETE align.py)
There is no alignment. `document.marks.json` is assembled by concatenating each
block's word marks in order, each shifted by that block's stitch offset. Format
is the same flat `[{word,start,end}]` in seconds — byte-compatible with the POC
player. **Delete `align.py`, the whispermlx dependency, and the alignment tests**
(replace with tests that the char→word grouping + offset-shift produce correct
monotonic marks).

---

## 7. document.blocks.json — UNCHANGED
```json
[
  { "index": 0, "type": "heading", "text": "…",
    "word_start": 0, "word_end": 7, "time_start": 0.0, "time_end": 3.1,
    "highlightable": true },
  { "index": 1, "type": "footnote", "text": "…",
    "word_start": 96, "word_end": 110, "time_start": 47.8, "time_end": 55.0,
    "highlightable": false }
]
```
`word_*` index into `marks.json`; `time_*` from stitch offsets; `highlightable`
false for footnotes (see Overview "Footnotes"). Unchanged by the provider switch.

---

## 8. CLI shape — UNCHANGED
```
forge-narrator generate manifest.zip --out ./out [--yes] [--no-cache] [--concurrency N]
forge-narrator estimate manifest.zip        # chars + credit cost, no API calls
```
(`--model` for whisper is gone; add `--concurrency` instead.)

---

## 9. Change order (for the switch)
1. Add `poc/elevenlabs_probe.py` to the repo's POC seeds (done in tts-test).
2. SSML re-verification probe → `SSML_FINDINGS.md` (overwrite Polly findings).
3. Rewrite `synth.py`: ElevenLabs `/with-timestamps`, char→word grouping, cache
   mp3 + marks. Remove boto3.
4. Delete `align.py` + whispermlx dep + alignment tests.
5. Rewrite `pipeline.py` marks assembly: concat block marks shifted by offsets
   (replaces the align call).
6. Update `cost.py` + `cli.py` guard rails to the credit model; add `--concurrency`.
7. Update tests (char→word grouping, offset-shift monotonicity, cache-with-marks).
8. End-to-end `generate` on the Junior fixture; validate with `poc/player.html`.
9. Update `CLAUDE.md` + README to ElevenLabs; drop Polly/whispermlx references.

## 10. Reuse from the POC
`poc/elevenlabs_probe.py` — the working `/with-timestamps` call + `group_chars_to_words`.
`poc/player.html` — the sync checker; validate generator output against it.
The marks.json format is proven; keep it byte-compatible.

## 11. Constraints
- ElevenLabs key only via env/local gitignored file; NEVER hardcoded or logged.
- Cost confirmation mandatory before any paid call.
- No `git add -A`; stage explicit paths; don't push unless asked.

## 12. Model = eleven_v3 (RESOLVED — supersedes the earlier "v3 deferred" note)
**Decision: synthesise on `eleven_v3`.** The earlier version of this section
deferred v3 on the belief that v3 could not return word timings — that belief was
wrong. Validated directly: `eleven_v3` **does** return per-character timing via
`/with-timestamps` (HTTP 200, full populated `alignment`, monotonic; gap-variance
analysis confirms genuine acoustic alignment, not uniform interpolation). So v3
gives the better voice quality AND native word timing in one call — no
forced-alignment stage, no two-stage pipeline.

Consequences:
- **`whispermlx` / `align_mlx.py` are no longer needed at all.** They existed only
  as the contingency for "if we ever pick v3 we'll need to recover timings." v3
  emits timings natively, so that contingency is closed; `poc/align_mlx.py` can be
  retired.
- The cache hash includes the model id, so switching from any earlier model to
  `eleven_v3` invalidates every block → a one-time full regeneration (correct).

**Audio / delivery tags (v3).** v3 honours bracketed delivery cues. Tested on the
memoir prose:
- `[pause]` produced a real ~1s gap and `[reflective]` softened the delivery —
  both behaved well and are usable, sparingly, where the prose warrants it.
- **`[Australian accent]` was tested and REJECTED.** Its effect is
  non-deterministic and decays within a generation (honoured on the first
  sentence, dropped on the next; a fixed seed did not fix it). Do not build any
  accent device on tags — accent is a property of the chosen voice, not a tag.
- Whether to emit any delivery tags in the manifest at all is a Spec A / SSML
  decision, verified in `SSML_FINDINGS.md`. Keep any tag vocabulary light and
  curated; the model stays isolated behind a `synth.py` parameter.

**Voice locked: `fjnwTZkKtQOJaYzGLa6n`** (gscOrkde and George dropped).
