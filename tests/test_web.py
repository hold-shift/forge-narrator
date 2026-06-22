"""Web console: upload/inspect + the server-side cost gate (offline, no API calls).

Uses FastAPI's TestClient. Does NOT exercise a real `generate` (that would need
the ElevenLabs key / network) — only the parsing, inspect, and 409 cost-gate
paths, which is where the web layer's own logic lives.
"""

import io
import zipfile

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
from fastapi.testclient import TestClient  # noqa: E402

from forge_narrator.hashing import block_hash  # noqa: E402
from forge_narrator.web.server import create_app  # noqa: E402

VOICE, MODEL = "fjnwTZkKtQOJaYzGLa6n", "eleven_v3"


def _manifest_zip_bytes() -> bytes:
    ssml = "Just a heading"
    data = {
        "document_slug": "web-test",
        "voice": VOICE,
        "model": MODEL,
        "blocks": [
            {"index": 0, "type": "heading", "ssml": ssml,
             "hash": block_hash(ssml, VOICE, MODEL)},
        ],
    }
    import json
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("manifest.json", json.dumps(data))
    return buf.getvalue()


def _client(tmp_path, char_cap=None):
    app = create_app(
        out_root=str(tmp_path / "out"),
        cache_dir=str(tmp_path / "cache"),   # empty → block is uncached
        char_cap=char_cap,
    )
    return TestClient(app)


def test_index_served(tmp_path):
    c = _client(tmp_path)
    r = c.get("/")
    assert r.status_code == 200
    assert "forge-narrator" in r.text


def test_upload_and_inspect(tmp_path):
    c = _client(tmp_path)
    r = c.post("/api/upload", files={"file": ("manifest.zip", _manifest_zip_bytes(), "application/zip")})
    assert r.status_code == 200
    run_id = r.json()["run_id"]
    assert r.json()["slug"] == "web-test"

    d = c.get(f"/api/inspect/{run_id}").json()
    assert d["voice"] == VOICE and d["model"] == MODEL
    assert d["block_total"] == 1
    assert d["blocks_to_synth"] == 1          # empty cache
    # heading gets a synthesis-only trailing period (anti-clip), so +1 char
    assert d["chars_to_synth"] == len("Just a heading.")
    assert d["over_cap"] is False


def test_upload_rejects_non_manifest(tmp_path):
    c = _client(tmp_path)
    r = c.post("/api/upload", files={"file": ("notes.txt", b"hello", "text/plain")})
    assert r.status_code == 400


def test_upload_rejects_bad_zip(tmp_path):
    c = _client(tmp_path)
    r = c.post("/api/upload", files={"file": ("manifest.zip", b"not a zip", "application/zip")})
    assert r.status_code == 400


def test_cost_gate_blocks_generate_over_cap(tmp_path):
    # cap below the manifest's char count + empty cache → over_cap → 409, no synth.
    c = _client(tmp_path, char_cap=5)
    run_id = c.post("/api/upload", files={"file": ("m.zip", _manifest_zip_bytes(), "application/zip")}).json()["run_id"]
    assert c.get(f"/api/inspect/{run_id}").json()["over_cap"] is True
    r = c.post(f"/api/generate/{run_id}")
    assert r.status_code == 409
    assert "cap" in r.json()["detail"].lower()


def test_inspect_unknown_run_404(tmp_path):
    c = _client(tmp_path)
    assert c.get("/api/inspect/nope").status_code == 404
