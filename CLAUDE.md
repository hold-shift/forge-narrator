# forge-narrator

A standalone tool that turns a NotebookForge SSML manifest into narrated audio
with word-level timing, for the Skitch Family Archive TTS feature.

Build per `docs/TTS_Spec_B_AudioGenerator.md` (the build contract).
Read `docs/TTS_Spec_0_Overview.md` first for the shared architecture and the
file-based contract with NotebookForge.

> **Current task: switch provider Polly → ElevenLabs.** The tool was first built
> against Amazon Polly + whispermlx and validated end-to-end. We are now switching
> to ElevenLabs, whose `/with-timestamps` endpoint returns word timing WITH the
> audio — which DELETES the whole alignment stage. Spec B is a CHANGE spec marking
> what stays / changes / is deleted.

## What it does
manifest.zip (from NotebookForge) → ElevenLabs `/with-timestamps` per block,
cached by hash, synthesised in parallel → ffmpeg stitch → assemble marks from the
per-block char timings (no aligner) → three files (document.mp3,
document.marks.json, document.blocks.json) in out/{slug}/, which the operator
uploads to S3.

## Rules
- Runs on the M2 MacBook Air. NO aligner, NO GPU stage — timing comes from the
  synthesis response.
- Provider = ElevenLabs, `/v1/text-to-speech/{voice_id}/with-timestamps`,
  model **`eleven_v3`**. Voice **locked: `fjnwTZkKtQOJaYzGLa6n`** (George and
  gscOrkde candidates dropped).
- Never hardcode or log the ElevenLabs key — read ELEVENLABS_API_KEY from env /
  a local gitignored file.
- **Parallelise** synthesis; back off on HTTP 429; concurrency configurable.
- Cache block audio AND block marks by hash = sha256(ssml + voice + model);
  only changed blocks re-synthesise. Cost guard rails mandatory before any paid
  call (~$390–450 fast-and-overage path for the full archive; 1 char = 1 credit).
- ffmpeg is a hard dependency (already installed).
- Re-verify SSML for ElevenLabs (it does NOT honour Polly's `<prosody>`); record
  in docs/SSML_FINDINGS.md.
- Validate output against poc/player.html (the proven sync checker).
- Never `git add -A`; stage explicit paths; don't push unless asked.

## Seed
`poc/` holds the working proof-of-concept. For the ElevenLabs switch the key seed
is `poc/elevenlabs_probe.py` — it already calls `/with-timestamps`, captures
`normalized_alignment`, and groups characters → words in the marks format the
player consumes. `poc/player.html` is the sync checker. (The Polly/whispermlx POC
files remain for reference but are superseded.)

## Structure
- `src/forge_narrator/` — the package (already built; synth.py rewritten,
  align.py deleted as part of the switch)
- `poc/` — proof-of-concept seed files + fixtures
- `docs/` — specs
- `out/` — generated output (gitignored)
- `cache/` — hash-keyed block audio + marks cache (gitignored)
- `tests/` — tests
