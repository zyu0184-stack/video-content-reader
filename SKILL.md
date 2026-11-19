---
name: video-content-reader
description: Read the content of a video file or a folder of videos and get a usable transcript. Extracts burned-in (hard) subtitles by cropping the subtitle band and running PP-OCRv4, falls back to faster-whisper speech-to-text when there are no burned-in subs, and pulls embedded subtitle tracks and keyframes alongside. Use when asked to 读取视频 / 视频内容 / 视频转文字 / 提取字幕 / transcribe a video / summarize what a video says / 把这个视频讲了什么整理出来. Built for social-platform clips where the subtitle is printed into the picture, which is far more accurate than speech recognition on a Japanese or English dub over Chinese subs.
---

# Video Content Reader

A video is three independent channels: picture,声音, and printed text. Read them separately, then align.

Order of preference — this is the whole point of the skill:

| Priority | Source | When | Why |
|---|---|---|---|
| 1 | Burned-in subtitles (OCR) | A subtitle is printed into the picture | Authoritative. It is what the uploader intended the audience to read. |
| 2 | Embedded subtitle track | `ffprobe` shows a `subtitle` stream | Exact text, zero recognition error |
| 3 | Speech-to-text | Audio is human speech and nothing is printed | Needed when the clip is a talking head or a raw dub |
| 4 | Keyframes | Always, as a supplement | Says what is on screen when nobody is talking |

A clip ripped to a short-video platform almost always falls into case 1 with a Japanese or Korean original audio track. Running speech-to-text there produces garbage; OCR of the printed subtitle produces the actual script. Check for burned-in subtitles first.

Measured on one line of dialogue, same clip:

```
Whisper : 这一切也算是和天宇宙富有关,因为残怨家一直觉得有天宇宙富的人,无配成为宙术师。
OCR     : 这一切也算是和"天与咒缚"有养 因为禅院家一直觉得 有"天与咒缚"的人 不配成为咒术师
```

Three proper nouns (天与咒缚, 禅院家, 咒术师): OCR gets all three, transcription none. Quote this when a user asks why the OCR path is the default.

## Step 0 — Inspect before doing anything

Commands below use `python scripts/<name>.py`, which runs straight from a checkout with no install. When the package is installed the same flags work as `vidprobe` / `subocr` / `vidread` / `cleansubs`.

```bash
python scripts/probe_video.py VIDEO
```

Reports duration, resolution, codec, audio channels, embedded subtitle tracks, and — the part that matters — whether the bottom band of the picture contains a stable high-contrast text row across sampled frames. Exit code 0 when burned-in subtitles are detected, 1 when not.

Pick the branch from that output, not from the file extension.

## Step 1 — Burned-in subtitles

```bash
python scripts/subocr.py VIDEO --out DIR
```

Pipeline: sample frames at a fixed interval (default 0.8 s) → crop the subtitle band → OCR each band → drop watermark and platform-UI boxes → merge consecutive frames holding the same sentence → write `subs.srt` + `subs.txt`.

Key parameters, tuned on real footage:

| Flag | Default | Effect |
|---|---|---|
| `--interval` | 0.8 | Seconds between sampled frames. A subtitle line can be on screen for under a second; 1.2 already drops lines. Lower costs time linearly. |
| `--top` | 0.70 | Where the subtitle band starts, as a fraction of frame height |
| `--bottom` | 1.00 | Where it ends |
| `--scale` | 1.0 | Resize factor for the cropped band. 0.5 is faster with almost no accuracy loss on 1080p source |
| `--jobs` | 4 | OCR worker processes |
| `--sim` | 0.72 | Character-overlap ratio above which two frames count as the same sentence |
| `--keep-frames` | off | Keep the cropped JPEGs for inspection |

Two settings decide whether this runs in 5 minutes or 30, and neither is obvious from the docs:

1. **`det_limit_type='max'`** (set inside the script; do not remove). RapidOCR's default is `min`, which scales the *short* side of the input to `limit_side_len`. A cropped subtitle band is extremely wide and short — 960×163 becomes 4360×736 — and detection time explodes. Measured on the same 10 frames: **1.41 s/frame with `min`, 0.32 s/frame with `max`**. 4.4× for one config key.
2. **Explicit `intra_op_num_threads`**. Left at `-1` each worker grabs every core; four workers then fight over 8 hardware threads. Measured: `-1` → 3.11 fps, `4` → 3.82 fps, `1` → 2.41 fps. Multi-process speedup only materialises when per-process threads are capped.

Full numbers and the sweep that produced them: [references/ocr-tuning.md](references/ocr-tuning.md).

## Step 2 — Clean the transcript

OCR on a subtitle band picks up more than the subtitle. Three contamination classes, all handled by:

```bash
python scripts/clean_subs.py DIR/subs.txt
```

- **Platform UI** — 抖音号:, 截图保存到相册, 愛別離-style account names. Removed by a word list plus a position rule: boxes whose centre sits in the right 26% and are under 14 characters.
- **Foreign-language credits** — an anime ending's Japanese staff roll reads as a dense汉-name string with no punctuation. Kana lines are dropped outright.
- **Scroll repeats** — the same frame sampled while a graphic stays on screen yields six near-identical lines. Adjacent duplicates and any line repeated within a 12-line window are collapsed.

Then read the cleaned text. Expect residual character-substitution errors even after cleaning: OCR reproduces what the uploader typed, and uploaders of ripped anime routinely mistype character names. `宿滩`/`宿雄`/`宿怒` all mean 宿傩; `慈` is 茈; `星宿` is 新宿. Build a correction table by sampling the actual frames and comparing.

## Step 3 — Speech-to-text, only if Step 1 found nothing

```bash
python scripts/vidread.py VIDEO --lang auto
```

Extracts 16 kHz mono audio, transcribes with faster-whisper, writes `transcript.txt` + `transcript.srt`, and also dumps keyframes and any embedded subtitle track in one pass.

Three things the script already handles:

- **Simplified-Chinese output.** Whisper's `small` model returns Traditional for Mandarin input. `initial_prompt="以下是普通话的句子，请使用简体中文转写。"` fixes it completely — measured on the same clip: without prompt `今天下午3點開會,討論第四季度的預算分配問題`, with prompt `今天下午三点开会,讨论第四季度的预算分配问题。`
- **Thread count.** `cpu_threads=8` on an 8-core/16-thread CPU gives 1.76× realtime; 16 gives 1.51×. Hyperthreads hurt ctranslate2.
- **Model location.** Local directory wins over hub name. See [references/troubleshooting.md](references/troubleshooting.md) for the China-network model download path.

## Step 4 — Write the deliverable

For each clip, produce:

```
NAME.txt              one sentence per line, time order
NAME.srt              timecoded
NAME_已清洗.txt        after clean_subs.py
NAME_ocr原始.jsonl     per-frame boxes with coordinates and confidence
```

Then a single `README.md` covering: a clip table (file, duration, resolution, topic, line count), a per-clip skeleton of the argument or plot in sections, and the correction table. A transcript dump is not a deliverable on its own — the reader wants to know what the video says, not to re-read OCR output.

Report figures with units: frames sampled, wall-clock seconds, per-frame cost. "Fast" is not a measurement.

## Dependencies

```bash
pip install ".[ocr]"        # OCR path only
pip install ".[all]"        # OCR + speech-to-text
# no install wanted? the two libraries on their own:
pip install rapidocr-onnxruntime faster-whisper
# plus ffmpeg + ffprobe on PATH
```

`faster-whisper` and `rapidocr-onnxruntime` are only needed for their respective steps. OCR alone needs `rapidocr-onnxruntime`; the audio path needs `faster-whisper`.

## Known edges

- **HEVC sources** decode fine; nothing special needed.
- **Vertical video** (1080×1920): the subtitle band still sits in the lower third, but `--top 0.70` may clip a subtitle block that is stacked across two lines. Inspect one frame first.
- **Subtitle inside a decorative frame** (a white rounded box, a chat bubble): the box edge is high-contrast and may be detected as text. If `subs.txt` shows a line of stray punctuation, narrow `--top`.
- **Multiple text rows in the band** — bilingual subtitles, or a title card over the dialogue: both get OCR'd and joined with a space, ordered left to right. Split them by inspecting `_ocr原始.jsonl`, which keeps each box separately.
- **No audio track** — `vidread.py` skips transcription and still produces frames and subtitle tracks.