# TTS Feature — Architecture Overview (read first)

Two tools, one file-based contract. This document is the shared context for both
build specs:
- `TTS_Spec_A_NotebookForge.md` — changes inside NotebookForge (the publishing tool).
- `TTS_Spec_B_AudioGenerator.md` — a NEW standalone tool that runs on the RTX 3060 Ti PC.

---

## Why two tools

NotebookForge's job is document/image ingestion and publishing for NotebookLM.
Audio generation is paid (Polly), slow, and machine-specific (GPU alignment).
So it is split out. NotebookForge stays "smart" (it knows document structure and
decides how each block should be spoken); the generator stays "dumb" (it just
runs the SSML it's given through Polly, stitches, aligns, and uploads).

Neither tool calls the other. The interface is a **manifest zip** exported by
NotebookForge and consumed by the generator. The generator's outputs are three
files placed on S3; their URLs are pasted back into NotebookForge by hand.

```
NotebookForge                                 Audio Generator (RTX PC)
─────────────                                 ────────────────────────
TTS toggle (global setting)
per-doc "Narration" sidebar panel:
  [Export SSML]  ──── manifest.zip ──────────→ read manifest.json
  audio base URL (S3)                           per block: cache by hash
  sync status dot (in-sync / stale)             miss → Polly (Brian, generative)
       ▲                                         stitch blocks → document.mp3
       │                                         WhisperX (GPU) → document.marks.json
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
   to send to Polly per block. The generator never decides how to speak text.
3. **Staleness tracking = yes.** NotebookForge stores the set of block hashes it
   last exported and shows an in-sync / stale dot by comparing live hashes.
4. **Voice = Brian, engine = generative, region = eu-west-2 (London).**
   Generative voices do NOT support Polly Speech Marks — that is why WhisperX
   does the word timing. (Confirmed against AWS docs.)
5. **Resume memory = localStorage**, keyed per document, storing word index +
   a content hash for graceful fallback if audio was regenerated.

---

## The hash (the spine of the whole system)

NotebookForge already has `blocks.content_hash()` and per-block structure in
`blocks.py` (types: `forgeNarrative`, `forgeFootnote`, `forgeImage`,
`forgeDedication`, `forgeDocGroup`). Reuse it.

Each narratable block gets a **block hash** = sha256 over:
`{ ssml_string, voice, engine }`.

- The hash travels in the manifest.
- The generator caches Polly output by this hash → only changed blocks are
  re-synthesised on regeneration.
- NotebookForge stores the exported hash set per document → staleness dot.
- Changing voice/engine changes every hash → full regen, correctly.

---

## What is narratable (and what is stripped)

Walk the block tree (same walker shape as `blocks.plain_text`). Per block type:

| Block type | Narrated? | How |
|------------|-----------|-----|
| `forgeNarrative` (heading) | yes | announce; `<break>` after |
| `forgeNarrative` (paragraph) | yes | spoken as-is; `<break>` between paras |
| `forgeFootnote` | optional (v1: **skip**) | see open question in Spec A |
| `forgeImage` caption / altText | **no** | stripped |
| `forgeDedication` | yes (once, up front) | spoken before body |
| `forgeDocGroup` | **no** | navigation furniture |
| ToC / masthead / nav | **no** | not in block tree anyway |

Heading vs paragraph distinction comes from the narrative block's level/style
field — check `narrative.py` for how heading level is represented.

---

## Files the generator produces (the S3 contract)

- **`document.mp3`** — concatenated audio, whole document.
- **`document.marks.json`** — flat word list `[{word, start, end}]`, seconds.
  (Exactly the POC format, proven working.)
- **`document.blocks.json`** — ordered blocks for rendering the readable text:
  `[{ index, type: "heading"|"paragraph", text, char_start, char_end,
      time_start, time_end }]`
  The player renders text from this and maps words to marks by order.

All three share the same word ordering, so the player aligns marks↔blocks by a
running word index. Keep that invariant sacrosanct: **the order of words in the
SSML, the mp3, the marks, and the blocks must be identical.**

---

## Open questions to resolve during the build (flagged in each spec)

- Footnote handling in narration (skip / read inline / read at section end).
- Pronunciation lexicon for AU + Vietnamese place names and military ranks.
- Whether the generator uploads to S3 itself or just writes a local folder you
  upload manually (Spec B leans: write local folder, you upload — keeps creds
  off the tool initially).
