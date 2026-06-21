#!/usr/bin/env python3
"""
whispermlx alignment test - mirror of align.py but using the MLX backend.
Times the run for comparison against the CPU WhisperX baseline.

Usage:
  python align_mlx.py --audio sample.mp3 --out sample.marks.mlx.json
"""
import argparse
import json
import sys
import time


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--audio", required=True)
    ap.add_argument("--out", default="marks.mlx.json")
    ap.add_argument("--model", default="small.en")
    args = ap.parse_args()

    try:
        import whispermlx
    except ImportError:
        sys.exit("whispermlx not installed.")

    t0 = time.time()

    # 1. Transcribe via MLX (GPU on Apple Silicon)
    model = whispermlx.load_model(args.model, device="cpu")
    result = model.transcribe(args.audio)
    t_transcribe = time.time()
    print(f"Transcribed {len(result['segments'])} segments "
          f"in {t_transcribe - t0:.1f}s", flush=True)

    lang = result.get("language", "en")

    # 2. Forced alignment (wav2vec2 - same as WhisperX)
    align_model, metadata = whispermlx.load_align_model(language_code=lang, device="cpu")
    aligned = whispermlx.align(
        result["segments"], align_model, metadata, args.audio, device="cpu",
        return_char_alignments=False,
    )
    t_align = time.time()
    print(f"Aligned in {t_align - t_transcribe:.1f}s", flush=True)

    # 3. Flatten
    words = []
    for seg in aligned["segments"]:
        for w in seg.get("words", []):
            if "start" in w and "end" in w:
                words.append({
                    "word": w["word"],
                    "start": round(float(w["start"]), 3),
                    "end": round(float(w["end"]), 3),
                })

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(words, f, indent=2, ensure_ascii=False)

    total = time.time() - t0
    print(f"Wrote {args.out} - {len(words)} words", flush=True)
    print(f"TOTAL: {total:.1f}s", flush=True)
    if words:
        print(f"First: {words[0]}", flush=True)
        print(f"Last:  {words[-1]}", flush=True)


if __name__ == "__main__":
    main()
