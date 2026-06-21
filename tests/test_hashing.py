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


def test_separator_prevents_field_ambiguity():
    # Without a separator these would collide.
    assert block_hash("ab", "c", "d") != block_hash("a", "bc", "d")
