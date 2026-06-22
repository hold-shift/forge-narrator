#!/usr/bin/env python3
"""
ElevenLabs payload verification — does <break> register, and how to pace headings?
Tests the realistic manifest-dialect options and reports audio + measured gaps.

Reads ELEVENLABS_API_KEY from env (or the local key file via the wrapper).

Usage:
  python ssml_verify.py --voice-id JBFqnCBsd6RMkjVDRZzb
"""
import argparse
import base64
import json
import os
import sys
import urllib.request
import urllib.error

API_ROOT = "https://api.elevenlabs.io/v1"

# Each case: (name, text). We compare measured inter-word gaps to see if a
# <break> actually inserts silence, and how a heading→paragraph transition sounds.
CASES = [
    ("plain_two_sentences",
     "The boy I once knew but now remember. Junior hurries down the hill towards the railway yard."),
    ("break_tag_500ms",
     'The boy I once knew but now remember.<break time="0.5s" /> Junior hurries down the hill towards the railway yard.'),
    ("break_tag_1500ms",
     'The boy I once knew but now remember.<break time="1.5s" /> Junior hurries down the hill towards the railway yard.'),
    ("ellipsis_pause",
     "The boy I once knew but now remember ... Junior hurries down the hill towards the railway yard."),
    ("newline_pause",
     "The boy I once knew but now remember.\n\nJunior hurries down the hill towards the railway yard."),
]


def get_key():
    key = os.environ.get("ELEVENLABS_API_KEY")
    if not key:
        sys.exit("ELEVENLABS_API_KEY not set.")
    return key


def synth(voice_id, text, model, key):
    url = f"{API_ROOT}/text-to-speech/{voice_id}/with-timestamps"
    payload = {"text": text, "model_id": model}
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), method="POST")
    req.add_header("xi-api-key", key)
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        sys.exit(f"HTTP {e.code}: {e.read().decode()[:300]}")


def biggest_gap(alignment):
    """Find the largest silence between consecutive characters (the 'pause')."""
    chars = alignment["characters"]
    starts = alignment["character_start_times_seconds"]
    ends = alignment["character_end_times_seconds"]
    max_gap = 0.0
    where = ""
    for i in range(1, len(chars)):
        gap = starts[i] - ends[i - 1]
        if gap > max_gap:
            max_gap = gap
            ctx = "".join(chars[max(0, i - 12):i + 1]).replace("\n", "\\n")
            where = ctx
    return max_gap, where


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--voice-id", required=True)
    ap.add_argument("--model", default="eleven_multilingual_v2")
    args = ap.parse_args()
    key = get_key()

    print(f"Voice: {args.voice_id} | model: {args.model}\n")
    print(f"{'case':<22} {'dur(s)':>7} {'max gap(s)':>11}  at")
    print("-" * 70)
    for name, text in CASES:
        out = synth(args.voice_id, text, args.model, key)
        audio = base64.b64decode(out["audio_base64"])
        with open(f"verify_{name}.mp3", "wb") as f:
            f.write(audio)
        al = out.get("normalized_alignment") or out["alignment"]
        dur = al["character_end_times_seconds"][-1]
        gap, where = biggest_gap(al)
        print(f"{name:<22} {dur:>7.2f} {gap:>11.3f}  ...{where}")

    print("\nWrote verify_*.mp3 for each case — listen to compare the pause feel.")
    print("Interpretation:")
    print("  - If break_tag rows show a clearly larger max-gap than plain, <break> WORKS.")
    print("  - Compare 500ms vs 1500ms gap sizes to confirm the duration is respected.")
    print("  - ellipsis / newline are fallbacks if <break> is ignored.")


if __name__ == "__main__":
    main()
