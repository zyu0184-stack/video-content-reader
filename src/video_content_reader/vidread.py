#!/usr/bin/env python3
"""vidread.py — read a video through all available channels in one pass.

Produces keyframes, a 16 kHz mono audio track, every embedded subtitle track,
and a faster-whisper transcript. Use this when a video has no burned-in
subtitles; use subocr.py when it does.

Usage:
    python vidread.py VIDEO [--out DIR] [--max-frames 24] [--scene 0.35]
                            [--interval 2] [--whisper small|medium|large-v3]
                            [--lang auto|zh|en|ja] [--model-path DIR]
                            [--no-whisper]

Output:
    DIR/meta.json              ffprobe streams
    DIR/frames/f0001.jpg       keyframes + index.json (approximate seconds)
    DIR/audio.wav              16 kHz mono PCM
    DIR/subs/track0.srt        embedded subtitle tracks
    DIR/transcript.txt/.srt    speech-to-text
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# Whisper's small model emits Traditional characters for Mandarin input.
# A Traditional-looking prompt of Mandarin keeps the output Simplified.
SIMPLIFIED_PROMPT = "以下是普通话的句子，请使用简体中文转写。"


def run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")


def ffprobe(video: Path) -> dict:
    p = run(["ffprobe", "-v", "error", "-of", "json",
             "-show_format", "-show_streams", str(video)])
    if p.returncode != 0:
        sys.exit(f"ffprobe failed:\n{p.stderr.strip()[:400]}")
    return json.loads(p.stdout)


def extract_frames(video: Path, out: Path, max_frames: int, scene: float,
                   interval: float) -> list[Path]:
    fdir = out / "frames"
    fdir.mkdir(parents=True, exist_ok=True)
    for old in fdir.glob("*.jpg"):
        old.unlink()

    def grab(vf: str) -> list[Path]:
        # -fps_mode replaced -vsync in ffmpeg 7; -pix_fmt keeps 10-bit sources
        # from failing inside the mjpeg encoder
        run(["ffmpeg", "-v", "error", "-y", "-i", str(video),
             "-vf", vf, "-fps_mode", "vfr", "-pix_fmt", "yuvj420p",
             "-q:v", "3", str(fdir / "f%04d.jpg")])
        return sorted(fdir.glob("f*.jpg"))

    files = grab(f"select='gt(scene\\,{scene})',scale=1280:-2")
    if len(files) < 8:  # too few scene cuts: fall back to fixed interval
        for f in files:
            f.unlink()
        files = grab(f"fps=1/{max(interval, 1.0)},scale=1280:-2")

    if len(files) > max_frames:
        idx = [round(i * (len(files) - 1) / (max_frames - 1))
               for i in range(max_frames)]
        files = [files[i] for i in sorted(set(idx))]

    dur = float(json.loads(run([
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "json", str(video)]).stdout)["format"]["duration"] or 0)
    n = len(files)
    index = [{"file": f.name,
              "approx_seconds": round(i * dur / max(n - 1, 1), 2)}
             for i, f in enumerate(files)]
    (fdir / "index.json").write_text(
        json.dumps({"duration": dur, "count": n, "frames": index},
                   ensure_ascii=False, indent=2), encoding="utf-8")
    return files


def extract_audio(video: Path, out: Path) -> Path | None:
    wav = out / "audio.wav"
    p = run(["ffmpeg", "-v", "error", "-y", "-i", str(video),
             "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(wav)])
    if p.returncode != 0 or not wav.exists() or wav.stat().st_size < 1024:
        return None
    return wav


def extract_subs(video: Path, out: Path, meta: dict) -> list[Path]:
    sdir = out / "subs"
    sdir.mkdir(parents=True, exist_ok=True)
    made = []
    n_sub = sum(1 for s in meta.get("streams", [])
                if s.get("codec_type") == "subtitle")
    for n in range(n_sub):
        dst = sdir / f"track{n}.srt"
        p = run(["ffmpeg", "-v", "error", "-y", "-i", str(video),
                 "-map", f"0:s:{n}", "-c:s", "srt", str(dst)])
        if p.returncode == 0 and dst.exists() and dst.stat().st_size > 0:
            made.append(dst)
    return made


def srt_time(t: float) -> str:
    h, r = divmod(max(t, 0.0), 3600)
    m, s = divmod(r, 60)
    return f"{int(h):02d}:{int(m):02d}:{int(s):02d},{int((s % 1) * 1000):03d}"


def resolve_model(spec: str, explicit: str | None) -> str:
    """Local directory wins, then models/faster-whisper-<name>, then hub name.

    The models directory is searched from the current working directory, the
    repo root, the legacy scripts/ layout, and the user cache, so the same code
    works from a checkout, from an installed wheel, and from scripts/ shims.
    """
    if explicit:
        p = Path(explicit).expanduser()
        if p.is_dir():
            return str(p.resolve())
    p = Path(spec).expanduser()
    if p.is_dir():
        return str(p.resolve())
    name = f"faster-whisper-{spec}"
    here = Path(__file__).resolve()
    for base in (Path.cwd(), here.parent.parent.parent, here.parent.parent,
                 Path.home() / ".cache" / "faster-whisper"):
        candidate = base / "models" / name
        if candidate.is_dir():
            return str(candidate)
    return spec


def detect_language(model, wav: Path, seconds: float = 30.0) -> tuple[str, float]:
    probe = wav.parent / "_probe.wav"
    run(["ffmpeg", "-v", "error", "-y", "-i", str(wav), "-t", str(seconds),
         "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(probe)])
    src = probe if probe.exists() else wav
    segs, info = model.transcribe(str(src), language=None, beam_size=1,
                                  vad_filter=False,
                                  condition_on_previous_text=False)
    for _ in segs:  # language detection is resolved before the first segment
        break
    probe.unlink(missing_ok=True)
    return info.language, float(info.language_probability)


def transcribe(wav: Path, out: Path, spec: str, lang: str | None,
               model_path: str | None) -> Path | None:
    import os
    os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        print("  faster-whisper missing: python -m pip install faster-whisper",
              file=sys.stderr)
        return None

    target = resolve_model(spec, model_path)
    # 8 physical cores beat 16 hyperthreads by 1.4x on ctranslate2 int8
    threads = min(8, os.cpu_count() or 4)
    try:
        model = WhisperModel(target, device="cpu", compute_type="int8",
                             cpu_threads=threads)
    except Exception as exc:
        print(f"  model load failed ({target}): {exc}", file=sys.stderr)
        print("  see references/troubleshooting.md for offline model setup",
              file=sys.stderr)
        return None

    if lang in (None, "", "auto"):
        lang, prob = detect_language(model, wav)
        print(f"      detected {lang}  p={prob:.2f}", flush=True)

    kw = dict(vad_filter=True, beam_size=5, condition_on_previous_text=False)
    if lang and lang.lower().startswith("zh"):
        kw["initial_prompt"] = SIMPLIFIED_PROMPT

    segs, info = model.transcribe(str(wav), language=lang, **kw)
    lines, srt = [], []
    for i, s in enumerate(segs, 1):
        txt = s.text.strip()
        lines.append(f"[{srt_time(s.start)}] {txt}")
        srt.append(f"{i}\n{srt_time(s.start)} --> {srt_time(s.end)}\n{txt}\n")
    (out / "transcript.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (out / "transcript.srt").write_text("\n".join(srt) + "\n", encoding="utf-8")
    print(f"      {len(lines)} segments  lang={info.language} "
          f"p={info.language_probability:.2f}  model={Path(target).name}",
          flush=True)
    return out / "transcript.txt"


def main() -> None:
    ap = argparse.ArgumentParser(description="Read a video through all channels")
    ap.add_argument("video")
    ap.add_argument("--out", help="output directory (default: alongside input)")
    ap.add_argument("--max-frames", type=int, default=24)
    ap.add_argument("--scene", type=float, default=0.35,
                    help="scene-change threshold 0-1")
    ap.add_argument("--interval", type=float, default=2.0,
                    help="fallback sampling interval in seconds")
    ap.add_argument("--whisper", default="small",
                    help="model size or local directory")
    ap.add_argument("--model-path", help="explicit faster-whisper model directory")
    ap.add_argument("--lang", default="auto")
    ap.add_argument("--no-whisper", action="store_true")
    a = ap.parse_args()

    video = Path(a.video).expanduser().resolve()
    if not video.exists():
        sys.exit(f"file not found: {video}")
    out = Path(a.out).expanduser().resolve() if a.out else video.with_suffix("")
    out.mkdir(parents=True, exist_ok=True)

    print(f"[1/5] probe  {video.name}", flush=True)
    meta = ffprobe(video)
    (out / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    dur = float(meta.get("format", {}).get("duration") or 0)
    print(f"      {dur:.1f}s  {len(meta.get('streams', []))} streams", flush=True)

    print(f"[2/5] keyframes  max={a.max_frames} scene={a.scene}", flush=True)
    frames = extract_frames(video, out, a.max_frames, a.scene, a.interval)
    print(f"      {len(frames)} -> {out / 'frames'}", flush=True)

    print("[3/5] audio", flush=True)
    wav = extract_audio(video, out)
    print(f"      {'ok' if wav else 'no audio track'}", flush=True)

    print("[4/5] embedded subtitles", flush=True)
    subs = extract_subs(video, out, meta)
    print(f"      {len(subs)} track(s)", flush=True)

    print("[5/5] speech-to-text", flush=True)
    if a.no_whisper or wav is None:
        print("      skipped", flush=True)
    else:
        transcribe(wav, out, a.whisper, a.lang, a.model_path)

    print(f"\ndone: {out}", flush=True)


if __name__ == "__main__":
    main()