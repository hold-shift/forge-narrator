import re

import pytest

from forge_narrator.chunk import MAX_SSML_CHARS, split_ssml_for_polly
from forge_narrator.ssml import ssml_to_text


def test_short_block_unchanged():
    ssml = "<speak>Just a short paragraph.<break time='500ms'/></speak>"
    assert split_ssml_for_polly(ssml) == [ssml]


def _long_paragraph_ssml(n_sentences=400):
    body = " ".join(f"This is sentence number {i} in a very long paragraph."
                     for i in range(n_sentences))
    return f"<speak>{body}<break time=\"500ms\"/></speak>"


def test_long_block_splits_under_limit():
    ssml = _long_paragraph_ssml()
    assert len(ssml) > MAX_SSML_CHARS
    chunks = split_ssml_for_polly(ssml)
    assert len(chunks) > 1
    for c in chunks:
        assert len(c) <= MAX_SSML_CHARS
        assert c.startswith("<speak>") and c.endswith("</speak>")


def test_split_preserves_words_and_order():
    ssml = _long_paragraph_ssml()
    chunks = split_ssml_for_polly(ssml)
    rejoined = " ".join(ssml_to_text(c) for c in chunks)
    assert rejoined == ssml_to_text(ssml)


def test_trailing_break_only_on_last_chunk():
    ssml = _long_paragraph_ssml()
    chunks = split_ssml_for_polly(ssml)
    assert "<break" not in chunks[0]
    assert "<break" in chunks[-1]


def test_overlong_single_sentence_splits_on_words():
    one = "word " * 1000  # no sentence punctuation, ~5000 chars
    ssml = f"<speak>{one.strip()}</speak>"
    chunks = split_ssml_for_polly(ssml)
    assert len(chunks) > 1
    assert all(len(c) <= MAX_SSML_CHARS for c in chunks)


def test_unsplittable_markup_raises():
    # Over-limit block whose length comes from prosody markup we can't safely cut.
    inner = "<prosody rate='95%'>" + ("x " * 2000) + "</prosody>"
    ssml = f"<speak>{inner}</speak>"
    with pytest.raises(ValueError, match="cannot be safely split"):
        split_ssml_for_polly(ssml)


def test_ampersand_reescaped():
    # Valid input SSML already has &amp;; after splitting it must stay escaped so
    # each chunk is valid SSML, and decode back to a literal ampersand.
    body = "Mum &amp; Dad. " * 400
    ssml = f"<speak>{body.strip()}</speak>"
    chunks = split_ssml_for_polly(ssml)
    for c in chunks:
        assert re.search(r"&(?!amp;|lt;|gt;)", c) is None  # no bare ampersands
        assert "&" in ssml_to_text(c)  # decodes back to literal &
