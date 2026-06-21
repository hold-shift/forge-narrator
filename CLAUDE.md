# forge-narrator

A standalone tool that turns a NotebookForge SSML manifest into narrated audio
with word-level timing, for the Skitch Family Archive TTS feature.

Build per `docs/TTS_Spec_B_AudioGenerator.md` (the build contract).
Read `docs/TTS_Spec_0_Overview.md` first for the shared architecture and the
file-based contract with NotebookForge.

## What it does
manifest.zip (from NotebookForge) → Polly (Brian, generative) per block,
cached by hash, synthesised in parallel → ffmpeg stitch → whispermlx alignment
→ three files (document.mp3, document.marks.json, document.blocks.json) in
out/{slug}/, which the operator uploads to S3.

## Rules
- Runs on the M2 MacBook Air. Aligner is **whispermlx** (MLX fork), NOT vanilla
  WhisperX, NOT the RTX PC. One machine only.
- Voice = Brian, engine = generative, region = eu-west-2. SSML path is GO
  (`<break>`/`<prosody>` confirmed working — see docs/SSML_FINDINGS.md).
- Never hardcode AWS keys — boto3 reads ~/.aws (user PollyAPI-user already set up).
- **Parallelise** Polly synthesis (8–10 concurrent); serial is ~20h, unacceptable.
- Cache block audio by hash; only changed blocks re-synthesise. Cost guard rails
  mandatory before any paid call (~$116 for the full archive).
- ffmpeg is a hard dependency (already installed via Homebrew).
- Validate output against poc/player.html (the proven sync checker).
- Never `git add -A`; stage explicit paths; don't push unless asked.

## Seed
`poc/` holds the working proof-of-concept: `align_mlx.py` (whispermlx alignment),
`polly_probe.py` (Polly synth + timing), `player.html` (sync checker), and test
fixtures. Adapt these rather than writing from scratch.

## Structure
- `src/forge_narrator/` — the package
- `poc/` — proof-of-concept seed files + fixtures
- `docs/` — specs
- `out/` — generated output (gitignored)
- `cache/` — hash-keyed block audio cache (gitignored)
- `tests/` — tests
