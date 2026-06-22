"""R2 upload — exercised with a monkeypatched wrangler runner (no network)."""

import subprocess

import pytest

from forge_narrator import upload as up
from forge_narrator.upload import OUTPUT_FILES, UploadError, upload_slug


def _make_out(tmp_path, slug="doc"):
    d = tmp_path / "out" / slug
    d.mkdir(parents=True)
    for name, _ctype in OUTPUT_FILES:
        (d / name).write_bytes(b"x")
    return tmp_path / "out"


def _recorder(monkeypatch, *, dev_url="https://pub-abc123.r2.dev", put_rc=0, dev_rc=0):
    """Install a fake run_wrangler; return the list it records calls into."""
    calls = []

    def fake(args):
        calls.append(args)
        if args[:4] == ["r2", "bucket", "dev-url", "get"]:
            return subprocess.CompletedProcess(args, dev_rc,
                                               stdout=f"Public URL: {dev_url}\n", stderr="")
        return subprocess.CompletedProcess(args, put_rc, stdout="", stderr="boom" if put_rc else "")

    monkeypatch.setattr(up, "run_wrangler", fake)
    return calls


def test_object_keys_content_types_and_remote(tmp_path, monkeypatch):
    out = _make_out(tmp_path, "junior")
    calls = _recorder(monkeypatch)
    url = upload_slug("junior", out_root=out, bucket="b", echo=lambda *a: None)

    puts = [c for c in calls if c[:3] == ["r2", "object", "put"]]
    assert len(puts) == 3
    expected = {
        "b/junior/document.mp3": "audio/mpeg",
        "b/junior/document.marks.json": "application/json",
        "b/junior/document.blocks.json": "application/json",
    }
    for c in puts:
        key = c[3]
        assert key in expected
        assert "--remote" in c
        assert "--file" in c
        assert c[c.index("--content-type") + 1] == expected[key]
    assert url == "https://pub-abc123.r2.dev/junior"


def test_base_url_resolved_from_dev_url_get(tmp_path, monkeypatch):
    out = _make_out(tmp_path, "junior")
    _recorder(monkeypatch, dev_url="https://pub-deadbeef99.r2.dev")
    url = upload_slug("junior", out_root=out, bucket="b", echo=lambda *a: None)
    assert url == "https://pub-deadbeef99.r2.dev/junior"


def test_base_url_flag_override_wins(tmp_path, monkeypatch):
    out = _make_out(tmp_path, "junior")
    calls = _recorder(monkeypatch)
    url = upload_slug("junior", out_root=out, bucket="b",
                      base_url="https://cdn.example.com/audio/", echo=lambda *a: None)
    assert url == "https://cdn.example.com/audio/junior"   # trailing slash stripped
    # override → no dev-url lookup, only the 3 puts
    assert not any(c[:4] == ["r2", "bucket", "dev-url", "get"] for c in calls)
    assert sum(c[:3] == ["r2", "object", "put"] for c in calls) == 3


def test_env_base_url_override_wins(tmp_path, monkeypatch):
    out = _make_out(tmp_path, "junior")
    calls = _recorder(monkeypatch)
    monkeypatch.setenv("FORGE_R2_BASE_URL", "https://files.skitch.me")
    url = upload_slug("junior", out_root=out, bucket="b", echo=lambda *a: None)
    assert url == "https://files.skitch.me/junior"
    assert not any(c[:4] == ["r2", "bucket", "dev-url", "get"] for c in calls)


def test_missing_file_errors(tmp_path, monkeypatch):
    out = _make_out(tmp_path, "junior")
    (out / "junior" / "document.mp3").unlink()
    _recorder(monkeypatch)
    with pytest.raises(UploadError, match="missing"):
        upload_slug("junior", out_root=out, bucket="b", echo=lambda *a: None)


def test_dry_run_runs_no_subprocess(tmp_path, monkeypatch):
    out = _make_out(tmp_path, "junior")
    calls = []
    monkeypatch.setattr(up, "run_wrangler", lambda args: calls.append(args))
    lines = []
    url = upload_slug("junior", out_root=out, bucket="b", dry_run=True, echo=lines.append)
    assert calls == []                              # no subprocess at all
    assert url == "https://pub-XXXX.r2.dev/junior"  # placeholder base
    # the three put commands are shown (with --remote), plus the dev-url get
    assert sum("r2 object put" in ln for ln in lines) == 3
    assert any("--remote" in ln for ln in lines)
    assert any("dev-url get" in ln for ln in lines)


def test_dry_run_with_override_shows_real_base(tmp_path, monkeypatch):
    out = _make_out(tmp_path, "junior")
    monkeypatch.setattr(up, "run_wrangler", lambda args: 1 / 0)  # must not be called
    url = upload_slug("junior", out_root=out, base_url="https://x.dev/", dry_run=True,
                      echo=lambda *a: None)
    assert url == "https://x.dev/junior"


def test_dev_url_failure_errors_with_setup_hint(tmp_path, monkeypatch):
    out = _make_out(tmp_path, "junior")
    _recorder(monkeypatch, dev_rc=1)
    with pytest.raises(UploadError, match="dev-url|setup|bucket"):
        upload_slug("junior", out_root=out, bucket="b", echo=lambda *a: None)


def test_put_failure_errors(tmp_path, monkeypatch):
    out = _make_out(tmp_path, "junior")
    _recorder(monkeypatch, put_rc=1)
    with pytest.raises(UploadError, match="upload failed"):
        upload_slug("junior", out_root=out, bucket="b", echo=lambda *a: None)
