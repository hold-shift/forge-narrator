#!/usr/bin/env python3
"""
Polly generative timing + cost probe.

Synthesises ONE chunk of text with Brian / generative / eu-west-2, times it,
and reports seconds-per-character so we can estimate the whole archive.

Credentials: boto3 reads them from your environment / ~/.aws automatically.
This script NEVER takes a key as an argument and never prints secrets.

Usage:
  python polly_probe.py                 # uses built-in ~3000-char sample
  python polly_probe.py --text foo.txt  # time a specific text file
  python polly_probe.py --ssml          # send as SSML (tests <break>/<prosody>)
"""
import argparse
import time
import sys

REGION = "eu-west-2"
VOICE = "Brian"
ENGINE = "generative"

# ~3000 characters of Junior-style prose (a realistic single chunk)
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
    "the way up there. But Junior knows every inch of this place. He comes here often, "
    "sometimes with Pixie the Kelpie dog who lives with them and who is as much a part "
    "of the family as any person. Pixie knows the yard too, and the two of them have "
    "spent many hours exploring around the tracks and the big water tank that fills "
    "the steam engines. The name Junior came about because his father was also Robert, "
    "and to avoid confusion the boy had been called Junior from infancy. It was a name "
    "that followed him through the primary school roll books. It was only in later years "
    "as a young teenager that the name became an embarrassment. His friend Robert "
    "Phillips was known at home as Chummy and to Junior that sounded worse. Although an "
    "only child, sometimes there was mention of a sister no longer there and even the "
    "name Elizabeth. Mum had told Junior he was born in the big red brick hospital down "
    "on the flat near the river, the one with the long verandahs where sick people sat "
    "in the sun. Dad worked for the Forestry Department and had done so for as long as "
    "Junior could remember. He wore khaki clothes to work and drove a green truck."
)

SSML_SAMPLE = (
    "<speak><break time='700ms'/><prosody rate='95%'>The boy I once knew but now "
    "remember</prosody><break time='400ms'/>Junior hurries down the hill from the "
    "convent towards the railway yard. It is becoming cold and dew is forming on the "
    "ground.<break time='500ms'/>There will be a frost in the morning.</speak>"
)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--text", help="path to a text file to synthesise")
    ap.add_argument("--ssml", action="store_true", help="send SSML sample (tests tags)")
    ap.add_argument("--out", default="polly_probe.mp3")
    args = ap.parse_args()

    try:
        import boto3
    except ImportError:
        sys.exit("boto3 not installed. Run: pip install boto3")

    if args.ssml:
        text = SSML_SAMPLE
        text_type = "ssml"
        char_count = len(SSML_SAMPLE)
    elif args.text:
        with open(args.text, encoding="utf-8") as f:
            text = f.read().strip()
        text_type = "text"
        char_count = len(text)
    else:
        text = SAMPLE
        text_type = "text"
        char_count = len(SAMPLE)

    print(f"Region: {REGION} | Voice: {VOICE} | Engine: {ENGINE}")
    print(f"TextType: {text_type} | characters: {char_count}")
    print("Calling Polly...", flush=True)

    try:
        polly = boto3.client("polly", region_name=REGION)
    except Exception as e:
        sys.exit(f"Could not create Polly client (credentials?): {e}")

    t0 = time.time()
    try:
        resp = polly.synthesize_speech(
            Text=text,
            TextType=text_type,
            OutputFormat="mp3",
            VoiceId=VOICE,
            Engine=ENGINE,
        )
        audio = resp["AudioStream"].read()
    except Exception as e:
        sys.exit(f"Polly call failed: {e}")
    elapsed = time.time() - t0

    with open(args.out, "wb") as out:
        out.write(audio)

    # Cost: generative = $30 per 1,000,000 chars
    cost_this = char_count / 1_000_000 * 30
    per_char = elapsed / char_count

    print(f"\nDone in {elapsed:.1f}s  ->  {args.out} ({len(audio)/1024:.0f} KB)")
    print(f"Seconds per 1,000 chars: {per_char*1000:.2f}s")
    print(f"Cost this call: ${cost_this:.4f}")
    print("\n--- Full archive projection (645,778 words ~ 3.87M chars) ---")
    total_chars = 3_870_000
    proj_secs = per_char * total_chars
    print(f"Estimated Polly wall-clock (serial, 1 request at a time): "
          f"{proj_secs/3600:.1f} hours")
    print(f"Estimated Polly cost: ${total_chars/1_000_000*30:.0f}")
    print("(Serial estimate; real pipeline can parallelise requests to go faster.)")


if __name__ == "__main__":
    main()
