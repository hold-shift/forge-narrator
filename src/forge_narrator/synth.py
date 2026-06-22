"""ElevenLabs synthesis — parallel, cached, with 429 backoff (Spec B §3).

Each uncached block is POSTed to ``/v1/text-to-speech/{voice_id}/with-timestamps``
with ``model_id`` from the manifest. The response carries the audio AND
per-character timing in one call, so there is no alignment stage: we group the
characters into block-local word marks and cache both the mp3 and the marks.

Blocks are independent, so they are synthesised concurrently (configurable, ~5–10
in flight). HTTP 429 is retried with backoff (never fails the run); a genuine
error is retried once, then the run stops cleanly with the offending block index.

The API key comes from ``ELEVENLABS_API_KEY`` or a local gitignored key file. It
is NEVER hardcoded and NEVER logged.
"""

from __future__ import annotations

import base64
import json
import os
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

from .cache import BlockCache
from .manifest import Block, Manifest
from .marks import group_chars_to_words

API_ROOT = "https://api.elevenlabs.io/v1"

# Local gitignored key-file fallbacks, in order (env var is tried first). The key
# VALUE never lives in source — only these paths.
_KEY_FILES = (
    Path(".elevenlabs_key"),
    Path.home() / "ClaudeCode/forge-narrator/.elevenlabs_key",
    Path("/Users/cs/Documents/Claude/tts-test/.elevenlabs_key"),
)

# 429 backoff: exponential with a cap. Throttling must NOT fail the run.
_MAX_THROTTLE_RETRIES = 6
_BACKOFF_BASE = 1.5
_BACKOFF_CAP = 30.0
# Non-429 errors get exactly one retry, then we stop (§3b).
_SYNTH_RETRIES = 1
_TIMEOUT = 300  # seconds per request


class SynthesisError(Exception):
    """A block failed to synthesise after retries — the run should stop."""


@dataclass
class SynthResult:
    synthesised: int   # blocks that hit the API
    from_cache: int    # blocks served from cache


def get_api_key() -> str:
    """Return the ElevenLabs key from env or a local gitignored file.

    Never returns/raises the key value. Raises ``SynthesisError`` (with guidance,
    not the key) if none is found.
    """
    key = os.environ.get("ELEVENLABS_API_KEY")
    if key and key.strip():
        return key.strip()
    for f in _KEY_FILES:
        try:
            if f.is_file():
                content = f.read_text(encoding="utf-8").strip()
                if content:
                    return content
        except OSError:
            continue
    raise SynthesisError(
        "ELEVENLABS_API_KEY not set and no local key file found. "
        "Set the env var or create .elevenlabs_key (gitignored)."
    )


def _request(voice_id: str, model: str, text: str, key: str) -> dict:
    """One POST to /with-timestamps. Returns the parsed JSON or raises HTTPError."""
    url = f"{API_ROOT}/text-to-speech/{voice_id}/with-timestamps"
    body = json.dumps({"text": text, "model_id": model}).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("xi-api-key", key)
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "application/json")
    with urllib.request.urlopen(req, timeout=_TIMEOUT) as r:
        return json.loads(r.read())


def _synthesise_one(
    voice_id: str, model: str, block: Block, key: str, on_throttle=None,
) -> tuple[bytes, list[dict], float]:
    """Synthesise one block → (mp3 bytes, block-local word marks, seconds).

    Retries 429 with backoff; retries one other error; then stops. Auth failures
    fail fast. Error messages carry the block index + HTTP status, never the key.
    ``on_throttle(index, delay)`` is called (from this worker thread) on each 429.
    """
    t0 = time.time()
    throttle_tries = 0
    hard_tries = 0
    while True:
        try:
            out = _request(voice_id, model, block.synth_text, key)
            audio = base64.b64decode(out["audio_base64"])
            al = out.get("normalized_alignment") or out.get("alignment")
            if not al:
                raise SynthesisError(f"block {block.index}: response had no alignment")
            marks = group_chars_to_words(
                al["characters"],
                al["character_start_times_seconds"],
                al["character_end_times_seconds"],
            )
            return audio, marks, time.time() - t0
        except urllib.error.HTTPError as e:
            if e.code == 429:
                throttle_tries += 1
                if throttle_tries > _MAX_THROTTLE_RETRIES:
                    raise SynthesisError(
                        f"block {block.index}: still rate-limited (429) after "
                        f"{_MAX_THROTTLE_RETRIES} retries"
                    ) from None
                retry_after = e.headers.get("Retry-After")
                delay = (float(retry_after) if retry_after and retry_after.isdigit()
                         else min(_BACKOFF_CAP, _BACKOFF_BASE ** throttle_tries))
                if on_throttle:
                    on_throttle(block.index, delay)
                time.sleep(delay)
                continue
            if e.code in (401, 403):
                raise SynthesisError(
                    f"block {block.index}: authentication failed (HTTP {e.code}) — "
                    "check ELEVENLABS_API_KEY"
                ) from None
            hard_tries += 1
            detail = _safe_detail(e)
            if hard_tries > _SYNTH_RETRIES:
                raise SynthesisError(f"block {block.index}: HTTP {e.code} {detail}") from None
            time.sleep(1.0)
        except (urllib.error.URLError, TimeoutError) as e:
            hard_tries += 1
            if hard_tries > _SYNTH_RETRIES:
                raise SynthesisError(f"block {block.index}: network error: {e}") from None
            time.sleep(1.0)


def _safe_detail(e: urllib.error.HTTPError) -> str:
    """Short error body for diagnostics (ElevenLabs never echoes the key)."""
    try:
        return e.read().decode("utf-8", "replace")[:200]
    except Exception:  # noqa: BLE001
        return ""


def synthesise(
    manifest: Manifest,
    cache: BlockCache,
    *,
    concurrency: int = 8,
    on_block=None,
    on_throttle=None,
) -> SynthResult:
    """Ensure every block's audio + marks are cached, synthesising the misses.

    Callbacks (optional) report progress without this module knowing about cost or
    presentation (kept in the caller — one source of truth, Spec C §8):
    - ``on_block(index, cached: bool, chars: int, seconds: float | None)`` — once
      per block: cached blocks first (``seconds=None``), then each synthesised
      block as it completes. Called from the main thread (serialised).
    - ``on_throttle(index, delay: float)`` — on each 429 back-off (from a worker
      thread; the callback must be thread-safe).

    Raises ``SynthesisError`` on the first unrecoverable failure (after cancelling
    pending work) so the operator can fix and re-run — cached blocks make it cheap.
    """
    todo = [b for b in manifest.blocks if not cache.has(b.synth_hash)]
    cached = [b for b in manifest.blocks if cache.has(b.synth_hash)]
    from_cache = len(cached)

    if on_block:
        for b in cached:
            on_block(b.index, True, b.billed_chars, None)

    if not todo:
        return SynthResult(synthesised=0, from_cache=from_cache)

    key = get_api_key()  # fail fast (and before spending) if missing
    workers = max(1, min(concurrency, len(todo)))

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(_synthesise_one, manifest.voice, manifest.model, b, key, on_throttle): b
            for b in todo
        }
        try:
            for fut in as_completed(futures):
                block = futures[fut]
                audio, marks, seconds = fut.result()  # re-raises SynthesisError
                cache.put(block.synth_hash, audio, marks)
                if on_block:
                    on_block(block.index, False, block.billed_chars, seconds)
        except SynthesisError:
            for f in futures:
                f.cancel()
            raise

    return SynthResult(synthesised=len(todo), from_cache=from_cache)
