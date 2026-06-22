"""Make the src/ package importable and expose shared fixtures."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from forge_narrator.hashing import block_hash  # noqa: E402

VOICE = "fjnwTZkKtQOJaYzGLa6n"   # locked ElevenLabs voice
MODEL = "eleven_v3"


def make_block(index: int, btype: str, text: str, ssml: str) -> dict:
    return {
        "index": index,
        "type": btype,
        "text": text,
        "ssml": ssml,
        "hash": block_hash(ssml, VOICE, MODEL),
    }


@pytest.fixture
def manifest_dict() -> dict:
    """A minimal valid manifest: one heading + two paragraphs."""
    # eleven_v3 dialect: plain-text block payloads (no tags). The 'ssml' field is
    # the spoken text itself.
    blocks = [
        make_block(0, "heading", "A Heading", "A Heading"),
        make_block(1, "paragraph", "First paragraph here.", "First paragraph here."),
        make_block(2, "paragraph", "Second paragraph follows.", "Second paragraph follows."),
    ]
    return {
        "version": 1,
        "slug": "test-doc",
        "title": "Test Doc",
        "voice": VOICE,
        "model": MODEL,
        "blocks": blocks,
    }


@pytest.fixture
def poc_mp3() -> Path:
    """A small real mp3 from the POC, for ffmpeg integration tests."""
    matches = sorted((ROOT / "poc").glob("speech_*.mp3"))
    if not matches:
        pytest.skip("no POC mp3 fixture present")
    return matches[0]
