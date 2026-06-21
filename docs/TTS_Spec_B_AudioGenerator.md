# TTS Spec B — Audio Generator (`forge-narrator`)

*Read `TTS_Spec_0_Overview.md` first. This is a standalone tool, separate from
NotebookForge. It consumes a manifest zip and produces the three S3 files. It is
the only tool that touches Polly and the aligner.*

Location: `/Users/cs/ClaudeCode/forge-narrator/` (own repo, own venv, own
`CLAUDE.md`). Runs on the **M2 MacBook Air** (not the RTX PC — see §0).

---

## 0. Findings from the proof-of-concept (already measured — do not re-derive)

A working POC exists in `poc/` and these facts are established:

- **Aligner = whispermlx** (an MLX/Apple-Silicon fork of WhisperX), NOT vanilla
  WhisperX, and NOT the RTX PC. It runs on the M2 Air's GPU via MLX, installs
  cleanly on Python 3.11, and was ~27% faster than CPU WhisperX on the test clip
  (26.4s vs 36.0s warm, identical 393-word output). This **collapses the whole
  tool to one machine** — there is no longer a two-machine split.
- **Voice = Brian, engine = generative, region = eu-west-2.** Quality approved.
- **SSML `<break>` and `<prosody rate>` are honoured** by Brian-generative
  (confirmed audibly, not just accepted). So the SSML path is GO — the manifest
  carries full SSML and the generator sends it as `TextType="ssml"`.
- **Measured Polly throughput: ~19 seconds per 1,000 characters** (generative is
  the slow engine). Serial, the full ~3.87M-char archive is ~20 hours; this is
  the pipeline bottleneck, NOT alignment. **Synthesis MUST be parallelised**
  (see §3) to bring wall-clock down to a few hours.
- **Measured cost: ~$0.055 per 1,831 chars** → ~$116 for the full archive
  (generative $30/M). Cost guard rails still required (§3).
- **ffmpeg is a hard dependency** (Homebrew `ffmpeg`); without it torchcodec
  fails to load and mp3 decode/concat is unreliable. The POC confirmed this.

The POC seed files in `poc/`:
- `align_mlx.py` — working whispermlx alignment (adapt into the pipeline).
- `polly_probe.py` — working Polly synth + timing (adapt into the synth step).
- `player.html` — the proven sync checker; use it to VALIDATE generator output.
- `speech_*.mp3`, `sample.marks.mlx.json`, `sample_text.txt` — test fixtures.

---

## 1. What it does (end to end)

```
manifest.zip (from NotebookForge)
   │
   1. unzip → manifest.json  (blocks[].ssml + hash, voice, engine)
   │
   2. per block:
        cache hit by hash?  → reuse cached block .mp3
        miss                → Polly SynthesizeSpeech (Brian, generative,
                              eu-west-2) → block .mp3 → cache by hash
   │
   3. stitch block mp3s in order → document.mp3
        record cumulative time offset per block
   │
   4. whispermlx forced alignment (MLX/GPU) on document.mp3
        + the known transcript (from blocks) → document.marks.json
   │
   5. emit document.blocks.json (block order, text, char + time spans)
   │
   6. write all three into out/{slug}/  (operator uploads to S3)
```

---

## 2. Tech / environment

### 2.0 SSML — already verified (GATE PASSED)
The SSML gating question is **resolved**: Brian-generative audibly honours
`<break>` and `<prosody rate>` (heading prosody confirmed excellent in the POC).
Build the **SSML path**: the manifest carries full SSML, the generator sends it
as `TextType="ssml"`. No need to re-run the probe; `poc/polly_probe.py --ssml`
is the evidence. Record this in `SSML_FINDINGS.md` for the record.

### 2.1 Stack
- **Python 3.11** (proven: whispermlx + mlx install cleanly on 3.11; 3.9 too old).
- `boto3` for Polly. AWS creds via standard chain (`~/.aws`), NEVER in code.
  (Already configured on the Air as user `PollyAPI-user` with
  `AmazonPollyFullAccess`.)
- **`whispermlx`** for alignment (the MLX fork — `pip install whispermlx`).
  Runs on the M2 GPU via MLX unified memory. The `device="cpu"` argument in its
  API is vestigial; MLX uses the GPU regardless. Default model `small.en`
  (proven: 393 words, no drift); `medium.en` available for tricky documents.
- **`ffmpeg`** — HARD dependency, install via Homebrew (`brew install ffmpeg`).
  Already installed on the Air (v8.x). Used for mp3 decode + concat stitching.
  Without it, torchcodec fails to load.
- Adapt `poc/align_mlx.py` for the alignment step rather than writing fresh.

---

## 3. Polly call (per uncached block)

```python
polly.synthesize_speech(
    Text=block["ssml"],
    TextType="ssml",                 # SSML confirmed working on generative
    OutputFormat="mp3",
    VoiceId=manifest["voice"],       # Brian
    Engine=manifest["engine"],       # generative
)
```

Region: `eu-west-2` (London — Brian generative available there).

### 3a. PARALLELISE — this is the performance-critical requirement
Measured throughput is ~19s per 1,000 chars; serial, the archive is ~20 hours.
Blocks are independent, so synthesise them **concurrently** (thread pool or async,
e.g. 8–10 in flight). This brings wall-clock to a few hours. Respect Polly's
rate limits / throttling — back off and retry on `ThrottlingException` rather
than failing the run. Do NOT synthesise serially.

### 3b. Guard rails
- Before running, print total character count across UNCACHED blocks and the
  estimated cost (generative = $30 / 1M chars; ~$0.055 per 1.8k chars measured)
  and require a `--yes` flag or
  interactive confirm. Prevents a surprise bill.
- Respect a configurable monthly character cap; refuse beyond it.
- If a generative block errors (rare model hallucination / emergency stop per
  AWS docs), retry once, then surface the block index and stop cleanly.

NOTE: generative does NOT emit Speech Marks — do not request them; WhisperX
provides word timing.

---

## 4. Caching (why regeneration is cheap)

A local cache dir, e.g. `cache/{hash}.mp3`. The hash is provided by the manifest
(`sha256(ssml + voice + engine)`), so:
- Re-running after a small NotebookForge edit re-synthesises only the blocks
  whose text/SSML changed; everything else is a cache hit.
- Cache never expires; it's keyed by content. Safe to keep indefinitely.
- A `--no-cache` flag forces full regen if ever needed.

---

## 5. Stitching + offsets

Concatenate block mp3s in manifest order into `document.mp3` (ffmpeg concat).
While stitching, accumulate each block's start/end time. This offset table feeds
`document.blocks.json` (§7) and lets the player map blocks→time without trusting
the aligner for block boundaries.

Since SSML `<break>` is confirmed working (§2.0), pacing comes from the SSML
itself; no generator-inserted silence is needed. (If ever switching to plain
text, insert fixed silence — 500 ms paragraph / 700 ms heading — here.)

---

## 6. whispermlx alignment

Run forced alignment on the stitched `document.mp3` using the **known
transcript** assembled from the blocks' plain text (transcript-constrained
alignment — more robust than blind transcription on proper nouns like
"Nui Dat", "Nuitat", ranks, etc.).

Output `document.marks.json` = flat `[{word, start, end}]` in seconds — exactly
the POC format that already worked (`poc/sample.marks.mlx.json` is a real
example). whispermlx uses the M2 GPU via MLX automatically.

Model: default `small.en`; allow `--model medium.en` for tricky documents.
(POC showed `small.en` aligned 393 words with no drift.)

---

## 7. document.blocks.json (player render source)

```json
[
  { "index": 0, "type": "heading",
    "text": "The boy I once knew but now remember",
    "word_start": 0, "word_end": 7,
    "time_start": 0.0, "time_end": 3.1 },
  { "index": 1, "type": "paragraph",
    "text": "Junior hurries down the hill …",
    "word_start": 7, "word_end": 96,
    "time_start": 3.1, "time_end": 47.8 }
]
```

`word_start`/`word_end` index into `marks.json` (the running word index — the
sacred invariant from the Overview). `time_*` come from the stitch offsets.
The player renders text from this file and highlights words via marks.

---

## 8. CLI shape

```
forge-narrator generate manifest.zip --out ./out [--yes] [--model small.en] [--no-cache]
forge-narrator estimate manifest.zip        # chars + cost, no API calls
```

`generate` writes `out/{slug}/document.mp3`, `document.marks.json`,
`document.blocks.json`. The operator uploads that folder to S3 and pastes the
base URL into NotebookForge.

(v1: the tool writes a local folder; it does NOT upload to S3 itself. Keeps AWS
write creds out of scope and the operator in control. A later `--upload`
option can add S3 PutObject if wanted.)

---

## 9. Build order
1. Project scaffold, venv (3.11), requirements (boto3, whisperx, ffmpeg present).
2. Manifest reader + `estimate` command (no API calls) — verify parsing first.
3. Polly per-block synth + hash cache.
4. ffmpeg stitch + offset table.
5. WhisperX alignment (reuse the POC `align.py` logic) → marks.json.
6. blocks.json emitter.
7. `generate` end-to-end on the real Junior manifest; compare against the POC
   player to confirm the three-file contract renders correctly.
8. Cost guard rails + confirmation.

## 10. Reuse from the POC
The working alignment code is at `/Users/cs/Documents/Claude/tts-test/align.py`
and the player at `.../player.html`. The marks.json format is already proven;
keep it byte-compatible so the POC player can validate generator output.

## 11. Constraints
- AWS credentials only via the standard chain; NEVER written to disk by the tool.
- Cost confirmation mandatory before any paid Polly call.
- Deterministic: same manifest in → same files out (modulo Polly's own
  generative variation, which is inherent to the engine).
