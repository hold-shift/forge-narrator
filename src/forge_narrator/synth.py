"""Polly synthesis — parallel, cached, with throttle backoff (Spec B §3).

Each uncached block is synthesised with Brian / generative / eu-west-2 and stored
in the content-addressed cache. Blocks are independent, so we run 8–10 concurrently
(serial would be ~20 h for the full archive). Throttling is retried with backoff;
a genuine synthesis error is retried once, then the run stops cleanly with the
offending block index (Spec B §3b).

Generative voices do NOT emit Speech Marks — we never request them; whispermlx
provides word timing downstream.
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass

from .cache import BlockCache
from .chunk import split_ssml_for_polly
from .ffmpeg import concat_mp3_bytes
from .manifest import Block, Manifest

REGION = "eu-west-2"

# Throttle backoff: exponential with a cap. ThrottlingException is expected under
# concurrency and must NOT fail the run.
_MAX_THROTTLE_RETRIES = 6
_BACKOFF_BASE = 1.5
_BACKOFF_CAP = 30.0

# Non-throttle synthesis errors get exactly one retry, then we stop (§3b).
_SYNTH_RETRIES = 1


class SynthesisError(Exception):
    """A block failed to synthesise after retries — the run should stop."""


@dataclass
class SynthResult:
    synthesised: int   # blocks that hit Polly
    from_cache: int    # blocks served from cache


def _make_client():
    try:
        import boto3
    except ImportError as e:
        raise SynthesisError("boto3 not installed (pip install boto3)") from e
    return boto3.client("polly", region_name=REGION)


def _is_throttle(exc: Exception) -> bool:
    name = exc.__class__.__name__
    if name in ("ThrottlingException", "TooManyRequestsException"):
        return True
    # botocore ClientError carries the code in its response.
    code = getattr(exc, "response", {}).get("Error", {}).get("Code", "")
    return code in ("ThrottlingException", "TooManyRequestsException")


def _polly_call(client, manifest: Manifest, ssml: str, block_index: int) -> bytes:
    """One Polly SynthesizeSpeech call, retrying throttles (backoff) and one error."""
    throttle_tries = 0
    hard_tries = 0
    while True:
        try:
            resp = client.synthesize_speech(
                Text=ssml,
                TextType="ssml",
                OutputFormat="mp3",
                VoiceId=manifest.voice,
                Engine=manifest.engine,
            )
            return resp["AudioStream"].read()
        except Exception as e:  # noqa: BLE001 — classify below
            if _is_throttle(e):
                throttle_tries += 1
                if throttle_tries > _MAX_THROTTLE_RETRIES:
                    raise SynthesisError(
                        f"block {block_index}: still throttled after "
                        f"{_MAX_THROTTLE_RETRIES} retries"
                    ) from e
                delay = min(_BACKOFF_CAP, _BACKOFF_BASE ** throttle_tries)
                time.sleep(delay)
                continue
            hard_tries += 1
            if hard_tries > _SYNTH_RETRIES:
                raise SynthesisError(f"block {block_index}: {e}") from e
            # one quick retry for transient/model errors
            time.sleep(1.0)


def _synthesise_one(client, manifest: Manifest, block: Block) -> bytes:
    """Synthesise one block, splitting over-long SSML across calls and rejoining."""
    try:
        chunks = split_ssml_for_polly(block.ssml)
    except ValueError as e:
        raise SynthesisError(f"block {block.index}: {e}") from e
    parts = [_polly_call(client, manifest, c, block.index) for c in chunks]
    return concat_mp3_bytes(parts)


def synthesise(
    manifest: Manifest,
    cache: BlockCache,
    *,
    concurrency: int = 9,
    progress=None,
) -> SynthResult:
    """Ensure every block's audio is in the cache, synthesising the misses.

    ``progress`` (optional) is called with ``(done, total)`` after each block.
    Returns counts. Raises ``SynthesisError`` on the first unrecoverable failure
    (after cancelling pending work) so the operator can fix and re-run — cached
    blocks make the re-run cheap.
    """
    todo = [b for b in manifest.blocks if not cache.has(b.hash)]
    from_cache = len(manifest.blocks) - len(todo)
    total = len(manifest.blocks)
    done = from_cache
    if progress and from_cache:
        progress(done, total)

    if not todo:
        return SynthResult(synthesised=0, from_cache=from_cache)

    client = _make_client()
    workers = max(1, min(concurrency, len(todo)))

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_synthesise_one, client, manifest, b): b for b in todo}
        try:
            for fut in as_completed(futures):
                block = futures[fut]
                audio = fut.result()  # re-raises SynthesisError
                cache.put(block.hash, audio)
                done += 1
                if progress:
                    progress(done, total)
        except SynthesisError:
            for f in futures:
                f.cancel()
            raise

    return SynthResult(synthesised=len(todo), from_cache=from_cache)
