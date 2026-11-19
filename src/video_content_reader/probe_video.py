#!/usr/bin/env python3
"""probe_video.py — decide how to read a video before spending time on it.

Prints container/stream facts, then samples frames and checks whether the
bottom band carries printed text. Exit code 0 = burned-in subtitles found,
1 = none found, 2 = could not decide (no OCR backend).

Usage:
    python probe_video.py VIDEO [--frames 12] [--top 0.70]
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

CJK = "".join(chr(c) for c in range(0x4E00, 0x9FFF + 1))
KANA = "".join(chr(c) for c in range(0x3040, 0x30FF + 1))


def run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")


def probe(video: Path) -> dict:
    p = run(["ffprobe", "-v", "error", "-of", "json",
             "-show_entries", "format=duration,size,bit_rate,format_name",
             "-show_entries",
             "stream=index,codec_type,codec_name,width,height,r_frame_rate,"
             "channels,sample_rate:stream_tags=language,title",
             str(video)])
    if p.returncode != 0:
        sys.exit(f"ffprobe failed:\n{p.stderr.strip()[:400]}")
    return json.loads(p.stdout)


def human_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f}{unit}"
        n /= 1024
    return f"{n:.1f}TB"


def sample_band(video: Path, tmp: Path, dur: float, count: int,
                top: float) -> list[Path]:
    tmp.mkdir(parents=True, exist_ok=True)
    for old in tmp.glob("*.jpg"):
        old.unlink()
    for i in range(count):
        t = dur * (i + 0.5) / count
        run(["ffmpeg", "-v", "error", "-y", "-ss", f"{t:.3f}", "-i", str(video),
             "-frames:v", "1",
             "-vf", f"crop=iw:round(ih*{1 - top:.4f}):0:round(ih*{top:.4f})",
             str(tmp / f"s{i:02d}.jpg")])
    return sorted(tmp.glob("s*.jpg"))


def has_text_ocr(frames: list[Path]) -> tuple[bool, list[str]]:
    """Use the OCR backend when present — far more reliable than pixel heuristics."""
    try:
        from rapidocr_onnxruntime import RapidOCR
    except ImportError:
        return False, []
    ocr = RapidOCR(det_limit_type="max", det_limit_side_len=640, use_cls=False)
    samples = []
    hit = 0
    for f in frames:
        res, _ = ocr(str(f))
        texts = [t for _, t, s in (res or []) if s > 0.6]
        if texts:
            hit += 1
            samples.extend(texts[:2])
    return hit >= max(2, len(frames) // 3), samples[:8]


def has_text_pixels(frames: list[Path]) -> bool:
    """Fallback: a subtitle row is a narrow band of very bright pixels
    sitting on a mostly non-bright row profile."""
    try:
        import cv2
        import numpy as np
    except ImportError:
        return False
    votes = 0
    for f in frames:
        img = cv2.imread(str(f), cv2.IMREAD_GRAYSCALE)
        if img is None:
            continue
        bright = (img > 205).astype(np.float32)
        rows = bright.mean(axis=1)
        if rows.size == 0:
            continue
        # a text row covers 3-40% of the width; a white background covers all of it
        band = ((rows > 0.03) & (rows < 0.45)).sum()
        if band >= 4:
            votes += 1
    return votes >= max(2, len(frames) // 2)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("video")
    ap.add_argument("--frames", type=int, default=12)
    ap.add_argument("--top", type=float, default=0.70)
    a = ap.parse_args()

    video = Path(a.video).expanduser().resolve()
    if not video.exists():
        sys.exit(f"file not found: {video}")

    meta = probe(video)
    fmt = meta.get("format", {})
    dur = float(fmt.get("duration") or 0)
    print(f"file      {video.name}")
    print(f"container {fmt.get('format_name', '?')}   "
          f"{human_size(int(fmt.get('size') or 0))}   "
          f"{dur:.1f}s ({int(dur // 60)}:{int(dur % 60):02d})")

    streams = meta.get("streams", [])
    subs = [s for s in streams if s.get("codec_type") == "subtitle"]
    audio = [s for s in streams if s.get("codec_type") == "audio"]
    vids = [s for s in streams if s.get("codec_type") == "video"]

    for s in vids:
        print(f"video     #{s['index']} {s.get('codec_name')} "
              f"{s.get('width')}x{s.get('height')} "
              f"{s.get('r_frame_rate')} fps")
    for s in audio:
        lang = (s.get("tags") or {}).get("language", "?")
        print(f"audio     #{s['index']} {s.get('codec_name')} "
              f"{s.get('channels')}ch {s.get('sample_rate')}Hz lang={lang}")
    for s in subs:
        lang = (s.get("tags") or {}).get("language", "?")
        print(f"subtitle  #{s['index']} {s.get('codec_name')} lang={lang}")
    if not subs:
        print("subtitle  none embedded")

    tmp = video.parent / "_inspect"
    frames = sample_band(video, tmp, dur, a.frames, a.top)
    print(f"\nsampled   {len(frames)} frames from the bottom "
          f"{int((1 - a.top) * 100)}% of the picture")

    found, samples = has_text_ocr(frames)
    backend = "ocr"
    if not samples:
        found = has_text_pixels(frames)
        backend = "pixels"

    for f in tmp.glob("*.jpg"):
        f.unlink()
    try:
        tmp.rmdir()
    except OSError:
        pass

    print(f"burned-in {'YES' if found else 'no '}   (detector: {backend})")
    if samples:
        print("sample:   " + " | ".join(samples))

    # The hint has to name the command the caller can actually type: subocr and
    # vidread when the wheel is installed, the scripts/ path when it is not.
    if Path(sys.argv[0]).stem == "vidprobe":
        ocr_cmd, asr_cmd = "subocr", "vidread"
    else:
        ocr_cmd, asr_cmd = "python scripts/subocr.py", "python scripts/vidread.py"

    print("\nnext:")
    if found:
        print(f'  {ocr_cmd} "{video.name}" --interval 0.8 --jobs 4')
        if subs:
            print("  (an embedded track also exists — compare both)")
    else:
        print(f'  {asr_cmd} "{video.name}" --lang auto')
        if subs:
            print("  embedded subtitle track present — ffmpeg -map 0:s:0 out.srt")

    sys.exit(0 if found else 1)


if __name__ == "__main__":
    main()