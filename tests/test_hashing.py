from forge_narrator.hashing import block_hash


def test_deterministic():
    a = block_hash("<speak>hi</speak>", "Brian", "generative")
    b = block_hash("<speak>hi</speak>", "Brian", "generative")
    assert a == b
    assert len(a) == 64  # sha256 hex


def test_voice_and_model_change_hash():
    base = block_hash("hi", "voiceA", "eleven_v3")
    assert block_hash("hi", "voiceB", "eleven_v3") != base   # voice change
    assert block_hash("hi", "voiceA", "eleven_v2") != base   # model change
    assert block_hash("HI", "voiceA", "eleven_v3") != base   # ssml change


def test_recipe_is_plain_concatenation():
    # The recipe is plain concatenation of (ssml, voice, model), no separator —
    # reverse-engineered from a real NotebookForge export and locked here so the
    # cache key can't silently diverge. (Verified historically against a real
    # Polly-era hash: sha256(ssml + "Brian" + "generative").)
    import hashlib
    assert block_hash("ssml", "voice", "model") == (
        hashlib.sha256(b"ssmlvoicemodel").hexdigest()
    )
