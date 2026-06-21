from forge_narrator.hashing import block_hash


def test_deterministic():
    a = block_hash("<speak>hi</speak>", "Brian", "generative")
    b = block_hash("<speak>hi</speak>", "Brian", "generative")
    assert a == b
    assert len(a) == 64  # sha256 hex


def test_voice_and_engine_change_hash():
    base = block_hash("<speak>hi</speak>", "Brian", "generative")
    assert block_hash("<speak>hi</speak>", "Amy", "generative") != base
    assert block_hash("<speak>hi</speak>", "Brian", "neural") != base
    assert block_hash("<speak>HI</speak>", "Brian", "generative") != base


def test_recipe_matches_real_notebookforge_export():
    # Regression lock: this (ssml, voice, engine) → hash triple was taken verbatim
    # from a real 1934-1945_junior.manifest.zip export. The recipe is plain
    # concatenation, no separator; if this breaks, the cache key diverged.
    ssml = ('<speak><break time="700ms"/><prosody rate="95%">Prologue</prosody>'
            '<break time="400ms"/></speak>')
    assert block_hash(ssml, "Brian", "generative") == (
        "6cbb94ac1c199d7663935de47b10247d8793911e09a59a377e15568ed9745155"
    )
