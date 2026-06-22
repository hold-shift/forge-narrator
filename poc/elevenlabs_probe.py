#!/usr/bin/env python3
"""
ElevenLabs A/B test — synthesise the same Junior paragraph that Polly did,
using the /with-timestamps endpoint, and save:
  - elevenlabs_<voice>.mp3            (audio, to compare against polly_probe.mp3)
  - elevenlabs_<voice>.marks.json     (word-level marks, grouped from char timings)

Key handling: reads ELEVENLABS_API_KEY from the environment. NEVER pass the key
as an argument; never print it. Set it in your own shell:
    export ELEVENLABS_API_KEY="sk_..."

Usage:
  python elevenlabs_probe.py --voice-id <id> [--model eleven_multilingual_v2]
  python elevenlabs_probe.py --list-voices       # print available voices + ids
"""
import argparse
import base64
import json
import os
import sys
import time
import urllib.request
import urllib.error

API_ROOT = "https://api.elevenlabs.io/v1"

# Same paragraph used for the Polly probe, for a like-for-like comparison.
SAMPLE = (
    "Junior hurries down the hill from the convent towards the railway yard. "
    "It is becoming cold and dew is forming on the ground. There will be a frost "
    "in the morning. It is dark already and not yet six o'clock. Mum will have "
    "something nice for tea although Junior is not all that fond of food. The fire "
    "in the stove though will be nice and after tea sitting around the stove in warm "
    "pyjamas with the oven open is best. Junior decides to take the short cut across "
    "the railway marshalling yard at the bottom of the shallow valley and head "
    "straight up Atkinson Street to home. Mum and Dad had often warned of the danger "
    "of doing this; you might trip on the track in the dark, or the shunters come all "
    "the way up there. But Junior knows every inch of this place."
)


def get_key():
    key = os.environ.get("ELEVENLABS_API_KEY")
    if not key:
        sys.exit("ELEVENLABS_API_KEY not set. Run:  export ELEVENLABS_API_KEY=\"sk_...\"")
    return key


def http_json(url, key, method="GET", payload=None):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("xi-api-key", key)
    if data:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        sys.exit(f"HTTP {e.code}: {e.read().decode()[:400]}")


def list_voices(key):
    out = http_json(f"{API_ROOT}/voices", key)
    print(f"{'NAME':<28} {'VOICE_ID':<24} LABELS")
    for v in out.get("voices", []):
        labels = v.get("labels", {})
        desc = ", ".join(f"{k}={val}" for k, val in labels.items())
        print(f"{v['name']:<28} {v['voice_id']:<24} {desc}")


def group_chars_to_words(chars, starts, ends):
    """Turn per-character alignment into [{word,start,end}]."""
    words = []
    cur = ""
    cur_start = None
    for ch, st, en in zip(chars, starts, ends):
        if ch.isspace():
            if cur:
                words.append({"word": cur, "start": round(cur_start, 3),
                              "end": round(prev_end, 3)})
                cur = ""
                cur_start = None
        else:
            if not cur:
                cur_start = st
            cur += ch
            prev_end = en
    if cur:
        words.append({"word": cur, "start": round(cur_start, 3),
                      "end": round(prev_end, 3)})
    return words


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--voice-id")
    ap.add_argument("--voice-name", default="voice")
    ap.add_argument("--text", help="path to a text file to synthesise instead of the built-in sample")
    ap.add_argument("--model", default="eleven_multilingual_v2")
    ap.add_argument("--list-voices", action="store_true")
    args = ap.parse_args()

    key = get_key()

    if args.list_voices:
        list_voices(key)
        return

    if not args.voice_id:
        sys.exit("Provide --voice-id (run --list-voices to find one).")

    url = f"{API_ROOT}/text-to-speech/{args.voice_id}/with-timestamps"
    text = SAMPLE
    if args.text:
        with open(args.text, encoding="utf-8") as tf:
            text = tf.read().strip()
    payload = {"text": text, "model_id": args.model}

    print(f"Model: {args.model} | chars: {len(text)}")
    print("Calling ElevenLabs /with-timestamps ...", flush=True)
    t0 = time.time()
    out = http_json(url, key, method="POST", payload=payload)
    elapsed = time.time() - t0

    # Audio
    audio = base64.b64decode(out["audio_base64"])
    mp3_path = f"elevenlabs_{args.voice_name}.mp3"
    with open(mp3_path, "wb") as f:
        f.write(audio)

    # Prefer normalized_alignment (matches what's actually spoken)
    al = out.get("normalized_alignment") or out.get("alignment")
    words = group_chars_to_words(
        al["characters"],
        al["character_start_times_seconds"],
        al["character_end_times_seconds"],
    )
    marks_path = f"elevenlabs_{args.voice_name}.marks.json"
    with open(marks_path, "w", encoding="utf-8") as f:
        json.dump(words, f, indent=2, ensure_ascii=False)

    print(f"\nDone in {elapsed:.1f}s")
    print(f"  {mp3_path} ({len(audio)/1024:.0f} KB)")
    print(f"  {marks_path} ({len(words)} words)")
    print(f"  first word: {words[0]}")
    print(f"  last word:  {words[-1]}")
    print(f"\nTimestamps came back WITH the audio — no separate alignment step.")


if __name__ == "__main__":
    main()
