from forge_narrator.ssml import ssml_to_text


def test_heading_strips_break_and_prosody():
    ssml = ('<speak><break time="700ms"/><prosody rate="95%">Prologue</prosody>'
            '<break time="400ms"/></speak>')
    assert ssml_to_text(ssml) == "Prologue"


def test_paragraph_keeps_prose_and_smart_quotes():
    ssml = '<speak>Dad says “Junior, Junior” and smiles.<break time="500ms"/></speak>'
    assert ssml_to_text(ssml) == "Dad says “Junior, Junior” and smiles."


def test_whitespace_normalised():
    ssml = "<speak>  two   spaces\nand a newline </speak>"
    assert ssml_to_text(ssml) == "two spaces and a newline"


def test_entities_decoded():
    assert ssml_to_text("<speak>Mum &amp; Dad</speak>") == "Mum & Dad"


def test_malformed_falls_back_to_strip():
    # Not valid XML (unclosed prosody) — regex fallback still recovers the words.
    assert ssml_to_text("<speak>Half <prosody>open</speak>") == "Half open"
