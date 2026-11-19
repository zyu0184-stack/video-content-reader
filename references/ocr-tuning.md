# OCR tuning

Every number below was measured on the reference machine, not estimated. Reproduce with the commands shown if the hardware differs.

Reference machine: AMD Ryzen 7 8845HS (8 cores / 16 threads, 3.8 GHz), Windows 11, Python 3.11.9, `rapidocr-onnxruntime` 1.4.4, `onnxruntime` 1.32.0, CPU only.

Test material: 10 frames cropped from a 1280×720 clip, subtitle band `crop=iw:ih*0.30:0:ih*0.70`, resized to 640 px wide. Each configuration processed all 10 frames after one warm-up call.

## 1. `det_limit_type` — the single largest factor, 4.4×

| Setting | Seconds per frame | Relative speed | Sample output |
|---|---|---|---|
| default (`limit_type: min`, `side_len: 736`) | 1.41 | 1.00× | `五语声明` |
| `limit_type: max`, `side_len: 640` | **0.32** | **4.41×** | `五语声明` |
| `limit_type: max`, `side_len: 640`, `use_cls: false` | 0.34 | 4.15× | `五语声明` |
| `limit_type: max`, `side_len: 480`, `use_cls: false` | 0.35 | 4.03× | `五语声明` |

The mechanism: DBNet's `limit_type` selects which side gets normalized to `limit_side_len`. With `min`, a video frame of 1280×720 cropped to a 30% band is 1280×216 — its short side is 216 px, so it is upsampled 3.4× to reach 736. At `--scale 0.5` the band is 640×108, upsampled **6.8×** to 4360×736. Detection cost scales with pixel count, so the smaller the input, the worse the penalty. The default is tuned for photographs, where the short side is where text height lives. A subtitle band is the opposite shape.

`max` caps the long side instead, so a wide band is never enlarged. Recognition accuracy did not move: the same `五语声明` came out of every row above.

Setting `side_len` below 640 (480, 320) does not help further — 480 measured slightly slower than 640, because the detector starts losing small glyphs and the recognition pass compensates with more boxes.

`use_cls: false` is free: it only saves 0.02 s/frame, but subtitles never rotate 180°.

```python
from rapidocr_onnxruntime import RapidOCR
ocr = RapidOCR(det_limit_type="max", det_limit_side_len=640, use_cls=False)
```

## 2. `intra_op_num_threads` — 1.6× spread within one process

Single process, same 10 frames:

| `intra_op_num_threads` | Seconds per frame | Frames per second |
|---|---|---|
| `-1` (all 16 threads) | 0.32 | 3.11 |
| 4 | **0.26** | **3.82** |
| 2 | 0.30 | 3.30 |
| 1 | 0.41 | 2.41 |

`-1` lets onnxruntime grab every logical core, which beats 4 only in theory. Four threads already saturate the memory bandwidth this model needs; extra threads wait on the same cache lines. One thread is clearly too few — the three sub-models (detection, direction, recognition) run sequentially and each benefits from parallel convolution.

## 3. Multi-process — only pays off with capped per-process threads

Four videos, 1445 frames total, `--jobs 4`:

| Configuration | Total wall clock | Aggregate throughput |
|---|---|---|
| `jobs=4`, `intra=2` | 522 s | 2.77 frames/s |
| `jobs=1`, `intra=-1` (projected from 3.11 fps) | — | 3.11 frames/s |

At `intra=-1` the four workers each spawn 16 threads → 64 threads on 8 cores. Measured: CPU time accumulated at 13% of wall clock, throughput **below** a single process. Capping `intra=2` is what turns four workers into a net gain over a long batch (522 s for 19 minutes of footage versus an estimated 465 s single-process — the two are close, and multi-process wins once the clips get long enough to amortise the 4 × model load).

Rule of thumb: `jobs × intra ≤ physical cores`. On 8 cores that is `4 × 2` or `2 × 4`; do not exceed it.

## 4. `--interval` — recall versus time, linear trade

The same 50-second clip, subtitle band, `--scale 1.0`:

| `--interval` | Frames | Lines recovered | Missing |
|---|---|---|---|
| 1.2 s | 42 | 23 | dropped a 4-character line that was on screen for one frame |
| 1.0 s | 50 | 24 | none |
| 0.8 s | 63 | 24 | none |

Short-video subtitles are cut to the beat and a line can hold for under a second. 1.2 s is where recall starts breaking. 0.8 s costs 25% more frames than 1.0 s for the same recall on this clip, and is the default because the clip that lost a line at 1.2 s is not unusual.

Cost scales linearly with `1 / interval`. For a 20-minute video, 0.8 s means 1500 frames — at 2.77 frames/s that is roughly 9 minutes of OCR.

## 5. `--scale` — weaker than expected, keep it at 1.0

| Band width | Seconds per frame (with `limit_type: max`) |
|---|---|
| 1280 px | ~0.35 |
| 640 px | ~0.32 |
| 2560 px (2× upscale) | 1.4+ |

Once `limit_type` is `max`, downscaling the band from 1280 to 640 buys under 10%. Below 640 the recognition rate starts to drop on small glyphs. Keep 1.0 for 1080p source, 0.5 only when the source is 4K.

## 6. Whisper thread count, for the audio branch

70.77 s of Mandarin audio, `small` model, int8:

| `cpu_threads` | Wall clock | Realtime factor |
|---|---|---|
| 4 (faster-whisper default) | 66.9 s | 1.06× |
| **8** | **40.2 s** | **1.76×** |
| 16 | 46.8 s | 1.51× |

Same pattern as the OCR path: at 16 threads the barrier synchronization inside ctranslate2 costs more than the extra parallelism returns. Eight physical cores is the optimum on this chip.

## 7. Summary of defaults

```
subocr.py   --interval 0.8  --scale 1.0  --jobs 4  --intra 2  --det-len 640
vidread.py  --max-frames 24  --scene 0.35  --whisper small  (cpu_threads=8)
```

Expected throughput on this hardware: 2.7 frames/s of subtitle OCR, 1.76× realtime for transcription.