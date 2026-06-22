# TTS Feature — Architecture Overview (read first)

Two tools, one file-based contract. This document is the shared context for both
build specs:
- `TTS_Spec_A_NotebookForge.md` — changes inside NotebookForge (the publishing tool).
- `TTS_Spec_B_AudioGenerator.md` — a standalone tool (`forge-narrator`) that runs on the M2 Mac.

---

## Why two tools

NotebookForge's job is document/image ingestion and publishing for NotebookLM.
Audio generation is paid (ElevenLabs) and is a separate concern. So it is split
out. NotebookForge stays "smart" (it knows document structure and decides how
each block should be spoken); the generator stays "dumb" (it just runs the SSML
it's given through ElevenLabs, stitches, and uploads).

Neither tool calls the other. The interface is a **manifest zip** exported by
NotebookForge and consumed by the generator. The generator's outputs are three
files placed on S3; their URLs are pasted back into NotebookForge by hand.

The generator runs on the **M2 MacBook Air** — there is NO separate alignment
machine. ElevenLabs' `/with-timestamps` endpoint returns word timing WITH the
audio in a single call, so there is no forced-alignment stage at all.

```
NotebookForge                                 Audio Generator (forge-narrator, Mac)
─────────────                                 ─────────────────────────────────────
TTS toggle (global setting)
per-doc "Narration" sidebar panel:
  [Export SSML]  ──── manifest.zip ──────────→ read manifest.json
  audio base URL (S3)                           per block: cache by hash
  sync status dot (in-sync / stale)             miss → ElevenLabs /with-timestamps
       ▲                                              → block mp3 + char timings
       │                                         stitch blocks → document.mp3
       │                                         group chars→words → document.marks.json
       │                                         emit document.blocks.json
       └──── paste S3 base URL ◀──── you upload the 3 files to S3
front-end player (published page)
  fetches  <base>/document.mp3
           <base>/document.marks.json
           <base>/document.blocks.json
  localStorage resume · Media Session · a11y
```

---

## Decisions locked (do not re-litigate in the build)

1. **Player data = three files**: `document.mp3`, `document.marks.json`,
   `document.blocks.json`, served from one S3 **base URL** with fixed filenames.
2. **Export block payload = SSML strings.** NotebookForge writes the exact SSML
   to send to ElevenLabs per block. The generator never decides how to speak text.
3. **Staleness tracking = yes.** NotebookForge stores the set of block hashes it
   last exported and shows an in-sync / stale dot by comparing live hashes.
4. **Provider = ElevenLabs**, `/v1/text-to-speech/{voice_id}/with-timestamps`,
   model **`eleven_v3`**. Voice **locked: `fjnwTZkKtQOJaYzGLa6n`** — chosen by
   audition over multiple v2/v3 reads; the earlier "George"
   (`JBFqnCBsd6RMkjVDRZzb`) and premium `gscOrkdeRphuXV3NcHOp` candidates are
   dropped. Switched from Amazon Polly after A/B testing — ElevenLabs is clearly
   higher quality on the memoir prose. **v3 returns character-level timing via
   `/with-timestamps`** (validated by probe + gap-variance analysis — genuine
   acoustic alignment, not interpolation), so v3 gives the better voice AND native
   word timing in one call; no forced-alignment stage is needed.
5. **Word timing = from the synthesis response, not forced alignment.**
   `/with-timestamps` returns per-character start/end times alongside the audio.
   The generator groups characters into words (split on whitespace; word start =
   first char's start, word end = last char's end). NO WhisperX / whispermlx /
   aligner stage exists. Prefer `normalized_alignment` (matches spoken output).
6. **Resume memory = localStorage**, keyed per document, storing word index +
   a content hash for graceful fallback if audio was regenerated.

---

## The hash (the spine of the whole system)

NotebookForge already has `blocks.content_hash()` and per-block structure in
`blocks.py` (types: `forgeNarrative`, `forgeFootnote`, `forgeImage`,
`forgeDedication`, `forgeDocGroup`). Reuse it.

Each narratable block gets a **block hash** = sha256 over:
`{ ssml_string, voice, model }`.

- The hash travels in the manifest.
- The generator caches synthesised audio (and its word marks) by this hash →
  only changed blocks are re-synthesised on regeneration.
- NotebookForge stores the exported hash set per document → staleness dot.
- Changing voice/model changes every hash → full regen, correctly.

---

## What is narratable (and what is stripped)

Walk the block tree (same walker shape as `blocks.plain_text`). Per block type:

| Block type | Narrated? | How |
|------------|-----------|-----|
| `forgeNarrative` (heading) | yes | announce; `<break>` after |
| `forgeNarrative` (paragraph) | yes | spoken as-is; `<break>` between paras |
| `forgeFootnote` | **yes — read inline, not highlighted** | own block; "Footnote:" lead-in; spoken; flagged `highlightable: false` (see below) |
| `forgeImage` caption / altText | **no** | stripped |
| `forgeDedication` | yes (once, up front) | spoken before body |
| `forgeDocGroup` | **no** | navigation furniture |
| ToC / masthead / nav | **no** | not in block tree anyway |

Heading vs paragraph distinction comes from the narrative block's level/style
field — check `narrative.py` for how heading level is represented.

### Footnotes — narrated but not visually tracked (operator decision)
Footnotes ARE read aloud, inline, at the point their marker appears in the prose.
But their words are **excluded from the highlight track**, because jumping the
visual focus down to a footnote and back mid-paragraph is disorienting.

This is achieved without breaking any invariant:
- Each footnote becomes its **own block** in the manifest, type `footnote`,
  inserted in reading order right after the prose block containing its marker.
- Its SSML leads with a spoken cue and is set apart by breaks, e.g.
  `<speak><break time="500ms"/>Footnote. <break time="200ms"/>{text}<break time="500ms"/></speak>`
- In `document.blocks.json` the footnote block carries `"highlightable": false`.
- Word order across SSML / mp3 / marks / blocks stays identical — a footnote
  block is just another block with `<break>` gaps around it, so the generator's
  time-window word→block mapping is unchanged. **The generator needs no special
  footnote handling.**
- Only the **player** treats it differently: when playback enters a
  `highlightable: false` block's time-window, the player stops advancing the
  word highlight (leave the last prose word lit, or dim it) and resumes
  highlighting when the next highlightable block begins. Auto-scroll likewise
  pauses through the footnote.

So the "every audio word is in the highlight track" idea is relaxed to "every
audio word is in the marks; only words in highlightable blocks are visually
tracked." The marks file still contains the footnote words (so the audio↔text
mapping is complete); the player just opts not to highlight them.

---

## Files the generator produces (the S3 contract)

- **`document.mp3`** — concatenated audio, whole document.
- **`document.marks.json`** — flat word list `[{word, start, end}]`, seconds.
  (Exactly the POC format, proven working.)
- **`document.blocks.json`** — ordered blocks for rendering the readable text:
  `[{ index, type: "heading"|"paragraph"|"footnote", text, char_start, char_end,
      time_start, time_end, highlightable }]`
  `highlightable` is `true` for heading/paragraph, `false` for footnote.
  The player renders text from this and maps words to marks by order;
  it highlights only blocks where `highlightable` is true.

All three share the same word ordering, so the player aligns marks↔blocks by a
running word index. Keep that invariant sacrosanct: **the order of words in the
SSML, the mp3, the marks, and the blocks must be identical.**

### How blocks map to time (with ElevenLabs /with-timestamps)
Each block is synthesised independently, so each block's char/word timings come
back **relative to that block's own audio, starting at 0**. The generator stitches
block mp3s in order and records each block's cumulative start offset; word times
are then shifted by that offset to get document-global times. So words map to
blocks **by construction** (the generator knows which block it sent), not by
re-deriving boundaries — simpler and more robust than the old forced-alignment
approach.

Consequence for NotebookForge: emit per-block SSML + hashes in order. The
inter-block `<break>` is still worth emitting for natural pacing between
paragraphs, and to keep a small silence gap at block seams so stitched audio
doesn't clip word edges — but it is no longer load-bearing for word→block
mapping (that now comes from per-block synthesis, not silence detection).

---

## Open questions to resolve during the build (flagged in each spec)

- Pronunciation fixes for AU + Vietnamese place names and military ranks.
  ElevenLabs supports **pronunciation dictionaries** (its own API) and SSML
  `<phoneme>` on some models — decide the mechanism during the build.
- Whether the generator uploads to S3 itself or just writes a local folder you
  upload manually (Spec B leans: write local folder, you upload — keeps creds
  off the tool initially).

(Footnote handling is now RESOLVED: read inline, own block, not highlighted —
see the "Footnotes" section above.)
