#!/usr/bin/env python3
"""subocr.py — extract burned-in (hardcoded) subtitles from a video.

Samples the subtitle band on a fixed interval, runs PP-OCRv4 on each cropped
band, drops watermark / platform-UI boxes, then merges consecutive frames that
hold the same sentence into a timecoded transcript.

Usage:
    python subocr.py VIDEO [--out DIR] [--interval 0.8] [--top 0.70]
                           [--bottom 1.00] [--scale 1.0] [--jobs 4]
                           [--det-len 640] [--intra 2] [--sim 0.72]
                           [--keep-frames] [--dry-run]

Output:
    DIR/subs.srt          timecoded subtitles
    DIR/subs.txt          one sentence per line
    DIR/ocr_raw.jsonl     per-frame OCR boxes (text, confidence, x-centre)
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

try:  # Windows consoles default to GBK, which breaks CJK logs through a pipe
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# Platform branding and interaction UI that lands in the subtitle band.
WATERMARK_WORDS = (
    "抖音", "快手", "小红书", "bilibili", "哔哩", "西瓜视频", "今日头条",
    "号:", "号：", "ID:", "tiktok", "douyin", "reels",
    "截图", "保存到相册", "复制链接", "分享给", "进入商城", "看后续",
    "关注", "点赞", "投币", "收藏", "转发", "评论区", "全屏观看",
)

CJK = re.compile(r"[\u4e00-\u9fff\u3040-\u30ff]")


def run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")


def probe(video: Path) -> tuple[float, int, int]:
    p = run(["ffprobe", "-v", "error", "-of", "json",
             "-show_entries", "format=duration",
             "-show_entries", "stream=width,height,codec_type", str(video)])
    if p.returncode != 0:
        sys.exit(f"ffprobe failed:\n{p.stderr.strip()[:400]}")
    meta = json.loads(p.stdout)
    dur = float(meta.get("format", {}).get("duration") or 0)
    vs = [s for s in meta.get("streams", []) if s.get("width")]
    if not vs:
        sys.exit("no video stream found")
    return dur, int(vs[0]["width"]), int(vs[0]["height"])


def cut_frames(video: Path, fdir: Path, interval: float, top: float,
               bottom: float, scale: float, width: int) -> list[Path]:
    """Sample frames on an interval, keep only the subtitle band."""
    fdir.mkdir(parents=True, exist_ok=True)
    for old in fdir.glob("*.jpg"):
        old.unlink()
    h = max(bottom - top, 0.05)
    tw = max(int(width * scale), 64)
    tw -= tw % 2  # mjpeg wants even width
    vf = (f"fps=1/{interval},"
          f"crop=iw:round(ih*{h:.4f}):0:round(ih*{top:.4f}),"
          f"scale={tw}:-2")
    p = run(["ffmpeg", "-v", "error", "-y", "-i", str(video),
             "-vf", vf, "-q:v", "2", str(fdir / "f%06d.jpg")])
    if p.returncode != 0:
        sys.exit(f"frame extraction failed:\n{p.stderr.strip()[:400]}")
    return sorted(fdir.glob("f*.jpg"))


def is_noise(text: str, cx: float, img_w: float) -> bool:
    t = text.strip()
    if not t:
        return True
    low = t.lower()
    if any(k.lower() in low for k in WATERMARK_WORDS):
        return True
    # account handles and badges hug the right edge and stay short
    if cx > img_w * 0.74 and len(t) < 14:
        return True
    # no CJK at all: pure digits, latin, or punctuation fragments
    if not CJK.search(t):
        return True
    # single glyph is almost always an icon fragment
    if len(t) <= 1:
        return True
    return False


def norm(s: str) -> str:
    return re.sub(r"[\s，。、！？：；\"'“”‘’()（）,.!?:;·…—\-~]", "", s)


def similar(a: str, b: str) -> float:
    """Character-overlap ratio. Tolerates OCR jitter that edit distance punishes."""
    na, nb = norm(a), norm(b)
    if not na or not nb:
        return 0.0
    ca: dict[str, int] = {}
    cb: dict[str, int] = {}
    for ch in na:
        ca[ch] = ca.get(ch, 0) + 1
    for ch in nb:
        cb[ch] = cb.get(ch, 0) + 1
    inter = sum(min(ca.get(k, 0), cb.get(k, 0)) for k in ca)
    return inter / max(len(na), len(nb))


def srt_time(t: float) -> str:
    h, r = divmod(max(t, 0.0), 3600)
    m, s = divmod(r, 60)
    return f"{int(h):02d}:{int(m):02d}:{int(s):02d},{int((s % 1) * 1000):03d}"


# ---------------------------------------------------------------- workers
_OCR = None
_IMG_W = 0.0


def _worker_init(img_w: int, det_len: int, intra: int) -> None:
    global _OCR, _IMG_W
    import os

    os.environ.setdefault("OMP_NUM_THREADS", str(max(intra, 1)))
    from rapidocr_onnxruntime import RapidOCR

    # limit_type MUST be max: the default (min) rescales the short side of a
    # wide subtitle band up by ~6x and costs 4.4x the detection time.
    _OCR = RapidOCR(det_limit_type="max", det_limit_side_len=det_len,
                    use_cls=False, intra_op_num_threads=intra)
    _IMG_W = float(img_w)


def _worker_one(item: tuple[int, str]) -> tuple[int, list[dict]]:
    idx, path = item
    res, _ = _OCR(str(path))
    keep = []
    if res:
        for box, text, score in res:
            cx = sum(pt[0] for pt in box) / len(box)
            if is_noise(text, cx, _IMG_W):
                continue
            keep.append({"text": text.strip(),
                         "score": round(float(score), 3),
                         "cx": round(cx)})
    return idx, keep


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Extract burned-in subtitles via OCR")
    ap.add_argument("video")
    ap.add_argument("--out", help="output directory (default: alongside input)")
    ap.add_argument("--interval", type=float, default=0.8,
                    help="seconds between sampled frames")
    ap.add_argument("--top", type=float, default=0.70,
                    help="subtitle band start, fraction of frame height")
    ap.add_argument("--bottom", type=float, default=1.00)
    ap.add_argument("--scale", type=float, default=1.0,
                    help="resize factor applied to the cropped band")
    ap.add_argument("--jobs", type=int, default=4, help="OCR worker processes")
    ap.add_argument("--det-len", type=int, default=640,
                    help="detector long-side limit")
    ap.add_argument("--intra", type=int, default=2,
                    help="onnxruntime threads per worker")
    ap.add_argument("--sim", type=float, default=0.72,
                    help="overlap ratio above which two frames are one sentence")
    ap.add_argument("--keep-frames", action="store_true")
    ap.add_argument("--dry-run", action="store_true",
                    help="sample and crop frames, then stop")
    a = ap.parse_args()

    video = Path(a.video).expanduser().resolve()
    if not video.exists():
        sys.exit(f"file not found: {video}")
    out = Path(a.out).expanduser().resolve() if a.out else video.with_suffix("")
    out.mkdir(parents=True, exist_ok=True)
    fdir = out / "_frames"

    dur, vw, vh = probe(video)
    print(f"[1/3] sampling  {video.name}  {dur:.1f}s  {vw}x{vh}  "
          f"interval={a.interval}", flush=True)
    files = cut_frames(video, fdir, a.interval, a.top, a.bottom, a.scale, vw)
    print(f"      {len(files)} frames -> {fdir}", flush=True)
    if not files:
        sys.exit("no frames produced; check --top/--bottom/--scale")

    from PIL import Image
    img_w = Image.open(files[0]).size[0]
    print(f"      band {img_w}px wide, right-edge UI cut at "
          f"{img_w * 0.74:.0f}px", flush=True)

    if a.dry_run:
        print("      dry run: inspect the frames, then rerun without --dry-run")
        return

    print(f"[2/3] OCR  jobs={a.jobs}  det_len={a.det_len}  intra={a.intra}",
          flush=True)
    results: dict[int, list[dict]] = {}
    if a.jobs <= 1:
        _worker_init(img_w, a.det_len, a.intra)
        for n, item in enumerate(enumerate(files), 1):
            idx, keep = _worker_one(item)
            results[idx] = keep
            if n % 50 == 0:
                print(f"      {n}/{len(files)}", flush=True)
    else:
        from multiprocessing import Pool

        with Pool(a.jobs, initializer=_worker_init,
                  initargs=(img_w, a.det_len, a.intra)) as pool:
            done = 0
            for idx, keep in pool.imap_unordered(
                    _worker_one, list(enumerate(files)), chunksize=4):
                results[idx] = keep
                done += 1
                if done % 50 == 0:
                    print(f"      {done}/{len(files)}", flush=True)

    raw, hits = [], []
    for i, f in enumerate(files):
        t = round(i * a.interval, 2)
        keep = results.get(i) or []
        raw.append({"t": t, "frame": f.name, "boxes": keep})
        if keep:
            keep.sort(key=lambda d: d["cx"])
            hits.append((t, " ".join(d["text"] for d in keep),
                         max(d["score"] for d in keep)))

    (out / "ocr_raw.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in raw),
        encoding="utf-8")

    print(f"[3/3] merging  {len(hits)} frames carry text", flush=True)
    groups: list[dict] = []
    for t, text, score in hits:
        prev = groups[-1] if groups else None
        if (prev and similar(prev["text"], text) >= a.sim
                and t - prev["end"] <= a.interval * 2.5):
            prev["end"] = t + a.interval
            if len(text) > len(prev["text"]) and score >= prev["score"] - 0.05:
                prev["text"] = text
            prev["score"] = max(prev["score"], score)
        else:
            groups.append({"start": t, "end": t + a.interval,
                           "text": text, "score": score})

    srt, txt = [], []
    for i, g in enumerate(groups, 1):
        srt.append(f"{i}\n{srt_time(g['start'])} --> {srt_time(g['end'])}\n"
                   f"{g['text']}\n")
        txt.append(g["text"])
    (out / "subs.srt").write_text("\n".join(srt), encoding="utf-8")
    (out / "subs.txt").write_text("\n".join(txt) + "\n", encoding="utf-8")

    if not a.keep_frames:
        for f in fdir.glob("*.jpg"):
            f.unlink()
        try:
            fdir.rmdir()
        except OSError:
            pass

    print(f"      {len(groups)} lines -> {out / 'subs.txt'}", flush=True)


if __name__ == "__main__":
    main()