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


def test_load_from_json(tmp_path, manifest_dict):
    m = load_manifest(_write_json(tmp_path, manifest_dict))
    assert m.slug == "test-doc"
    assert m.voice == "Brian"
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


def test_missing_field(tmp_path, manifest_dict):
    del manifest_dict["voice"]
    with pytest.raises(ManifestError, match="voice"):
        load_manifest(_write_json(tmp_path, manifest_dict))


def test_bad_version(tmp_path, manifest_dict):
    manifest_dict["version"] = 2
    with pytest.raises(ManifestError, match="version"):
        load_manifest(_write_json(tmp_path, manifest_dict))


def test_bad_block_type(tmp_path, manifest_dict):
    manifest_dict["blocks"][0]["type"] = "footnote"
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
