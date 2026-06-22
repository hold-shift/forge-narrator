# TTS Spec C — Web Interface (`forge-narrator serve`)

*Read `TTS_Spec_0_Overview.md` and `TTS_Spec_B_AudioGenerator.md` first. This
spec adds a **local operator console** to forge-narrator. It changes no pipeline
behaviour — it is a presentation + orchestration layer over the existing
`estimate` / `generate` code paths. Runs on the M2 MacBook Air, localhost only.*

---

## 1. Purpose & shape

Today the operator runs `forge-narrator estimate manifest.zip` then
`forge-narrator generate manifest.zip --out ./out --yes` and watches a terminal.
The web interface replaces that with a single-page console that:

1. Lets the operator **pick the NotebookForge export** (`manifest.zip`) from the
   filesystem via a normal file dialog.
2. **Inspects** it and shows a pre-flight summary (document, block counts, total
   characters, cache hits, estimated credits/cost, voice + model).
3. Gates generation behind a single **Generate** click — this is the cost
   confirmation, exactly equivalent to the CLI's `--yes`.
4. Shows **live progress** during conversion: phase, per-block progress, which
   block is in flight, cache hits vs API calls, characters done, running cost,
   ETA, and any 429 back-off.
5. On completion, lists the three output files in `out/{slug}/` and offers a
   **Preview** that opens the existing player against them.

It is a single-user tool on the operator's own Mac. No accounts, no public
surface, no S3 upload (still manual — see Non-goals).

---

## 2. Architecture

```
Browser (localhost SPA)                 forge-narrator backend (FastAPI, 127.0.0.1)
───────────────────────                 ───────────────────────────────────────────
[Choose manifest.zip] ── upload ───────► POST /api/upload        → store in work dir,
                                                                    parse manifest.json
pre-flight summary  ◄── JSON ──────────  GET  /api/inspect/{id}  → calls pipeline cost/
                                                                    cache logic (no API)
[Generate]          ── POST ───────────► POST /api/generate/{id} → start background run
progress stream     ◄── SSE ───────────  GET  /api/progress/{id} → emits ProgressEvents
done: 3 file paths  ◄── final SSE ─────  (run writes out/{slug}/ via existing pipeline)
[Preview]           ── open ───────────► GET  /preview/{slug}/   → player.html + out files
                                                                    served WITH Range support
```

Key properties:

- **The backend wraps the existing pipeline package; it does not reimplement it.**
  `/api/inspect` calls the same estimate/cost/cache code as `forge-narrator
  estimate`; `/api/generate` calls the same per-block synth → stitch → marks
  assembly as `forge-narrator generate`. The web layer is orchestration +
  presentation only. The CLI and the web UI are two front-ends over one pipeline.
- **The ElevenLabs key never leaves the backend.** Read from `ELEVENLABS_API_KEY`
  (or the local gitignored key file) exactly as the CLI does. Never sent to the
  browser, never logged, never in an SSE event.
- **Model/voice are read from the manifest, not hardcoded.** The console displays
  whatever `voice` + `model` the manifest carries (§Overview: voice id + model
  come from the manifest). The UI is model-agnostic.

---

## 3. User flow (four states)

### 3.1 Select
- A single file input accepting `.zip` (the `manifest.zip`). Also accept a bare
  `manifest.json` for convenience.
- On choose → POST the file to `/api/upload`. Backend stores it under a per-run
  work dir (e.g. `work/{run_id}/`), unzips, validates it parses as a manifest,
  returns a `run_id`. Reject anything that isn't a valid manifest with a clear
  message (don't crash).

### 3.2 Pre-flight (the cost gate's information)
`GET /api/inspect/{run_id}` returns, computed by the existing cost/cache code:

| Field | Source |
|-------|--------|
| `slug` | manifest |
| `voice`, `model` | manifest |
| `block_total` | count of blocks |
| `block_narratable` | blocks with SSML to synth |
| `blocks_cached` | hash present in `cache/` |
| `blocks_to_synth` | narratable − cached |
| `chars_total`, `chars_to_synth` | sum of block char counts (uncached only billed) |
| `credits_est`, `cost_usd_est` | `chars_to_synth` × credit rate (1 char = 1 credit on v2/v3) |
| `over_cap` | true if `chars_to_synth` exceeds configured cap |

Rendered as a summary card. The **Generate** button is disabled if `over_cap`.
Prominent line: *"Will synthesise N blocks (~X,XXX chars), reuse M cached. Est.
≈ Z credits ≈ \$Z.ZZ."* This card **is** the cost confirmation — no paid call
happens before the click.

### 3.3 Progress
On **Generate** → `POST /api/generate/{run_id}` starts a background task and the
page subscribes to `GET /api/progress/{run_id}` (SSE). Show:

- **Phase indicator**: `parsing → synthesising → stitching → assembling → done`.
- **Per-block bar**: `block i / N`, with each block marked cache-hit (instant) or
  API call. Synthesis is concurrent (§B 3a) so report *completed* count, not a
  strict sequential index.
- **Live counters**: characters done / total, running credit + dollar tally, ETA
  (from completed-block rate).
- **Log tail**: terse line per block (`✓ block 42 cached`, `✓ block 43 synth 1.1s`,
  `⏳ 429 — backing off 2s`).
- **Errors**: on a block failure after its retry (§B 3b), surface the block index
  and message, mark the run failed, stop cleanly. Offer **Retry** (re-runs;
  cached blocks make it cheap).

### 3.4 Done
- Show the three output paths under `out/{slug}/` (`document.mp3`,
  `document.marks.json`, `document.blocks.json`) with final duration, word count,
  total credits spent.
- **Preview** button → opens `/preview/{slug}/` (the player against these exact
  outputs).
- Reminder banner: *"Next: upload these three files to S3 and paste the base URL
  into NotebookForge."* (Upload stays manual — Non-goals.)

---

## 4. Backend endpoints

| Method | Path | Purpose |
|--------|------|---------|
| `GET`  | `/` | serve the SPA (static `index.html`) |
| `POST` | `/api/upload` | accept manifest.zip, store, parse, return `{run_id}` |
| `GET`  | `/api/inspect/{run_id}` | pre-flight summary (no API calls) |
| `POST` | `/api/generate/{run_id}` | start background generation; return `{started:true}` |
| `GET`  | `/api/progress/{run_id}` | **SSE** stream of `ProgressEvent`s |
| `GET`  | `/preview/{slug}/*` | serve player + `out/{slug}/` files **with HTTP Range** |

---

## 5. Progress events (SSE schema)

One JSON object per `event: progress` message:

```json
{
  "phase": "synthesising",
  "blocks_done": 43,
  "blocks_total": 87,
  "blocks_cached": 30,
  "chars_done": 18420,
  "chars_total": 41230,
  "credits_spent": 11500,
  "cost_usd": 1.15,
  "eta_seconds": 38,
  "level": "info",
  "message": "block 43 synth 1.1s"
}
```

Terminal messages: `event: done` with the final summary + output paths, or
`event: error` with `{block_index, message}`. SSE chosen over WebSocket: one-way,
trivial to emit from a background task, auto-reconnects, no extra deps.

---

## 6. Cost-confirmation gate (mandatory)

The **Generate** click is the only thing that authorises paid calls — the GUI
equivalent of `--yes`. Enforce server-side too: `/api/generate` must re-check the
character cap and refuse (HTTP 409) if `over_cap`, regardless of what the UI sent.
Never start synthesis from `/api/inspect`. This preserves §B's guard-rail
invariant: no paid call without explicit confirmation.

---

## 7. Player preview (reuse, with the Range fix)

Reuse `poc/player.html` (the validated sync checker) unchanged in contract — it
fetches `document.marks.json` + `document.mp3` and renders/ highlights from
`document.blocks.json` (footnotes `highlightable:false` are read but not tracked).

**Critical:** serve the preview with **HTTP Range support**. Python's stock
`http.server` does *not* honour `Range`, which silently breaks `<audio>` seeking
(click-to-jump does nothing) — confirmed during validation. FastAPI/Starlette's
`FileResponse` handles Range correctly, so serving `out/{slug}/` and the player
through the backend fixes this for free. Do not serve previews via bare
`http.server`.

---

## 8. Reuse & layering contract (do not violate)

- The web package imports and calls the existing pipeline functions
  (`estimate`/cost, per-block synth, stitch, marks assembly). **No synthesis,
  hashing, caching, stitching, or marks logic is duplicated in the web layer.**
- To stream progress, the pipeline's generate path should accept an optional
  **progress callback** (`on_progress(event: dict)`); the CLI passes a callback
  that prints, the web server passes one that pushes onto the SSE queue. If the
  current `generate` doesn't expose a callback hook, adding one is the only
  pipeline change this spec implies — and it benefits the CLI too.
- One source of truth: a future change to cost/cache/voice logic must not need
  editing in two places.

---

## 9. Security & constraints

- **Bind `127.0.0.1` only** (never `0.0.0.0`). Single-user localhost tool; no auth.
- Key via env / local gitignored file; never to browser, never logged, never in
  an SSE event or error payload.
- Uploaded manifests live in a per-run work dir; safe to clear on exit or via a
  small `work/` GC.
- No `git add -A`; stage explicit paths; don't push unless asked.

---

## 10. Suggested stack & layout

- **FastAPI + uvicorn** (async SSE, built-in Range via `FileResponse`, tiny dep
  surface). Flask + a generator response works too, but FastAPI is the cleaner
  fit for SSE. Add `fastapi`, `uvicorn` to `requirements.txt`.
- **Frontend: one static `index.html`, vanilla JS, no build step.** Match the
  player's khaki/editorial aesthetic (`--khaki #6b6b3a`, Georgia body, system-ui
  chrome) for visual consistency with the published player.
- Layout:
  ```
  src/forge_narrator/web/
    __init__.py
    server.py        # FastAPI app, endpoints, SSE
    static/index.html
  ```
- **Launch:** `forge-narrator serve [--port 8765] [--out ./out]` → starts uvicorn,
  prints the localhost URL. Add `serve` to the CLI alongside `estimate`/`generate`.

---

## 11. Non-goals (v1)

- **No S3 upload** — the operator still uploads the three files and pastes the
  base URL into NotebookForge by hand (per Overview contract). A future "Upload to
  S3" button is a clean later addition once creds handling is decided.
- **No manifest editing** — the console runs what NotebookForge exported; it does
  not let you change SSML or blocks.
- **Not a public/multi-user service** — localhost, single operator.
- **No re-implementation of the pipeline** — see §8.

---

## 12. Build order

1. Add an `on_progress` callback hook to the `generate` pipeline path (CLI passes
   a printing callback — behaviour unchanged).
2. `web/server.py`: `/api/upload`, `/api/inspect` over the existing cost/cache code.
3. `web/static/index.html`: Select + Pre-flight states wired to those two endpoints.
4. `/api/generate` + `/api/progress` (SSE) driven by the progress callback; wire
   the Progress + Done states.
5. `/preview/{slug}/` via `FileResponse` (Range-correct); reuse `poc/player.html`.
6. `forge-narrator serve` CLI command.
7. Manual end-to-end on the Junior manifest: select → inspect → generate → watch
   progress → preview. Confirm click-to-seek works (Range).

---

## 13. Model/voice decisions (reconciled across the spec set)

This console is model-agnostic (it displays whatever voice/model the manifest
carries). For the record, the rest of the spec set has been updated to the
validated decisions: model **`eleven_v3`** (confirmed to return word timings via
`/with-timestamps`), voice **locked `fjnwTZkKtQOJaYzGLa6n`** (George and gscOrkde
dropped), `whispermlx` / `align_mlx.py` no longer needed, and inline **accent tags
rejected** as inconsistent. See Spec 0 §4, Spec A §2 / §3b / §3d, and Spec B §3 / §12.
