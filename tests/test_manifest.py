import json
import zipfile

import pytest

from forge_narrator.manifest import ManifestError, load_manifest


def _write_json(tmp_path, data, name="manifest.json"):
    p = tmp_path / name
    p.write_text(json.dumps(data), encoding="utf-8")
    return p


def _write_zip(tmp_path, data, name="manifest.zip", member="manifest.json"):
    p = tmp_path / name
    with zipfile.ZipFile(p, "w") as zf:
        zf.writestr(member, json.dumps(data))
    return p


def test_real_format_document_slug_and_derived_text(tmp_path):
    """NotebookForge ElevenLabs format: document_slug, no version, SSML-only blocks."""
    from forge_narrator.hashing import block_hash

    voice, model = "fjnwTZkKtQOJaYzGLa6n", "eleven_v3"
    ssml = 'Prologue<break time="0.4s" />'
    data = {
        "document_slug": "1934-1945_junior",
        "title": "Junior",
        "voice": voice,
        "model": model,
        "blocks": [
            {"index": 0, "type": "heading", "ssml": ssml,
             "hash": block_hash(ssml, voice, model)},
        ],
    }
    m = load_manifest(_write_json(tmp_path, data))
    assert m.slug == "1934-1945_junior"
    assert m.model == "eleven_v3"
    assert m.blocks[0].text == "Prologue"          # derived from SSML (break stripped)
    assert m.transcript == "Prologue"


def test_load_from_json(tmp_path, manifest_dict):
    m = load_manifest(_write_json(tmp_path, manifest_dict))
    assert m.slug == "test-doc"
    assert m.model == "eleven_v3"
    assert len(m.blocks) == 3
    assert m.blocks[0].type == "heading"


def test_load_from_zip(tmp_path, manifest_dict):
    m = load_manifest(_write_zip(tmp_path, manifest_dict))
    assert m.slug == "test-doc"
    assert len(m.blocks) == 3


def test_load_from_nested_zip(tmp_path, manifest_dict):
    p = _write_zip(tmp_path, manifest_dict, member="export/manifest.json")
    m = load_manifest(p)
    assert len(m.blocks) == 3


def test_transcript_and_chars(manifest_dict, tmp_path):
    m = load_manifest(_write_json(tmp_path, manifest_dict))
    assert "A Heading" in m.transcript
    assert m.transcript.count("\n\n") == 2  # 3 blocks → 2 separators
    assert m.total_billed_chars == sum(len(b.ssml) for b in m.blocks)


def test_missing_voice(tmp_path, manifest_dict):
    del manifest_dict["voice"]
    with pytest.raises(ManifestError, match="voice"):
        load_manifest(_write_json(tmp_path, manifest_dict))


def test_missing_model(tmp_path, manifest_dict):
    del manifest_dict["model"]
    with pytest.raises(ManifestError, match="model"):
        load_manifest(_write_json(tmp_path, manifest_dict))


def test_bad_version(tmp_path, manifest_dict):
    manifest_dict["version"] = 2
    with pytest.raises(ManifestError, match="version"):
        load_manifest(_write_json(tmp_path, manifest_dict))


def test_footnote_is_a_valid_type(tmp_path, manifest_dict):
    from forge_narrator.hashing import block_hash

    ssml = manifest_dict["blocks"][1]["ssml"]
    manifest_dict["blocks"][1]["type"] = "footnote"
    manifest_dict["blocks"][1]["hash"] = block_hash(ssml, manifest_dict["voice"],
                                                    manifest_dict["model"])
    m = load_manifest(_write_json(tmp_path, manifest_dict))
    assert m.blocks[1].type == "footnote"


def test_bad_block_type(tmp_path, manifest_dict):
    manifest_dict["blocks"][0]["type"] = "image"  # not narratable → invalid here
    with pytest.raises(ManifestError, match="type"):
        load_manifest(_write_json(tmp_path, manifest_dict))


def test_hash_mismatch_detected(tmp_path, manifest_dict):
    manifest_dict["blocks"][1]["hash"] = "deadbeef" * 8
    with pytest.raises(ManifestError, match="hash mismatch"):
        load_manifest(_write_json(tmp_path, manifest_dict))


def test_hash_check_skippable(tmp_path, manifest_dict):
    manifest_dict["blocks"][1]["hash"] = "deadbeef" * 8
    m = load_manifest(_write_json(tmp_path, manifest_dict), verify_hashes=False)
    assert m.blocks[1].hash == "deadbeef" * 8


def test_out_of_order_index(tmp_path, manifest_dict):
    manifest_dict["blocks"][2]["index"] = 5
    with pytest.raises(ManifestError, match="index"):
        load_manifest(_write_json(tmp_path, manifest_dict))


def test_empty_blocks(tmp_path, manifest_dict):
    manifest_dict["blocks"] = []
    with pytest.raises(ManifestError, match="non-empty"):
        load_manifest(_write_json(tmp_path, manifest_dict))


def test_missing_file(tmp_path):
    with pytest.raises(ManifestError, match="not found"):
        load_manifest(tmp_path / "nope.zip")
