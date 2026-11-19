# Troubleshooting

Failures met while building this on Windows, with the fix that worked. Each is a real error message, not a guess.

## ffmpeg

### `Unrecognized option 'vsync'` — ffmpeg 7 removed it

```
Unrecognized option 'vsync'.
Error splitting the argument list: Option not found
```

`-vsync vfr` is gone. Use `-fps_mode vfr`. This bit hardest because the audio extraction path in the same script kept working, so the frame path failed silently as "0 frames produced" rather than as a visible error.

### `Non full-range YUV is non-standard` from the mjpeg encoder

```
[mjpeg] Non full-range YUV is non-standard, set strict_std_compliance to at most unofficial
[vost] Error while opening encoder - maybe incorrect parameters such as bit_rate, rate, width or height
```

Happens on synthetic or 10-bit sources. Add `-pix_fmt yuvj420p` to the frame extraction command.

### `crop` receives a fractional dimension

`crop=iw:ih*0.3:0:ih*0.7` fails when the product is not integral. Wrap in `round()`:
`crop=iw:round(ih*0.3000):0:round(ih*0.7000)`.

## OCR

### `3 frames/second` at 100% CPU on a subtitle band

The band is being upsampled by the detector. See [ocr-tuning.md](ocr-tuning.md) section 1 — set `det_limit_type="max"`.

### OCR returns the platform watermark as subtitle text

Every detection is a box with coordinates. Filter by position and content, not by string matching alone:

```python
if cx > img_w * 0.74 and len(text) < 14:   # right-edge account handles
    skip
if not re.search(r"[\u4e00-\u9fff]", text):  # digits, latin, punctuation only
    skip
if len(text) <= 1:                           # icon fragments
    skip
```

A word list on top handles the rest (`抖音`, `截图`, `保存到相册`, `号：`). Both layers are needed: the word list misses account names, the position rule misses a watermark placed centre-bottom.

### The same sentence appears six times in a row

A static graphic stays on screen while frames keep sampling. Collapse any line seen inside the last 12 lines, plus adjacent exact duplicates. `clean_subs.py` does this.

### Japanese staff credits appear as a string of Chinese names

An anime ending rolled through the subtitle band during sampling. Those lines contain kana, so dropping every line with `[\u3040-\u30ff]` removes them. It also removes genuine Japanese dialogue, which is the desired behaviour when the target is the Chinese subtitle.

### Chinese comes out Traditional

Whisper's `small` model defaults to Traditional for Mandarin. Pass `initial_prompt`:

```
以下是普通话的句子，请使用简体中文转写。
```

Measured before/after on identical audio — `今天下午3點開會,討論第四季度的預算分配問題` becomes `今天下午三点开会,讨论第四季度的预算分配问题。`

## Model download

### `huggingface.co` connect timeout

```
httpx2.ConnectTimeout: [WinError 10060] 由于连接方在一段时间后没有正确答复...
huggingface_hub.errors.LocalEntryNotFoundError
```

Direct access to the hub is blocked on some networks. Two mirrors behave differently:

| Host | `/api/models/...` | `/resolve/main/model.bin` |
|---|---|---|
| huggingface.co | timeout | timeout |
| hf-mirror.com | **500 Internal Server Error** | **200, full size** |

`snapshot_download` validates through the API first, so it fails even though the file CDN answers. Use `scripts/fetch_model.sh`, which resolves the pointer URLs directly and writes a local directory `WhisperModel` can load.

Check reachability first:

```bash
curl -sSIL --max-time 20 \
  "https://hf-mirror.com/Systran/faster-whisper-small/resolve/main/model.bin" \
  | grep -iE "^HTTP|content-length"
```

Expect `200` and `Content-Length: 483546902`.

### Mirror returns a 15-byte file

`preprocessor_config.json` does not exist in some CT2 repos; the mirror answers with a small HTML error page instead of 404. Delete any file under ~200 bytes whose first byte is not `{`. `faster-whisper` does not read that file.

## Python / Windows

### Chinese log output turns to `??????` through a pipe

The Windows console defaults to GBK. `print()` writes GBK, the pipe is read as UTF-8. Force the stream:

```python
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
```

Or set `PYTHONIOENCODING=utf-8` in the shell. Both are needed in different contexts — the script-level fix covers subprocess use, the env var covers interactive runs.

### `multiprocessing` workers never start on Windows

Windows uses `spawn`, not `fork`. Worker functions must be module-level, arguments picklable, and the entry point guarded by `if __name__ == "__main__":`. A closure or a lambda passed to `Pool` fails with `AttributeError: Can't pickle local object`.

### Import error without a stack trace

`KMP_DUPLICATE_LIB_OK=TRUE` in the environment suppresses an OpenMP runtime conflict between `torch` and `onnxruntime` when both are installed. Symptom without it: the interpreter aborts with `OMP: Error #15` during import.

## Timing symptoms

| Symptom | Likely cause | Check |
|---|---|---|
| CPU time ≈ 13% of wall clock across N workers | thread oversubscription | `jobs × intra > cores` |
| One worker at 100%, others idle | `chunksize` too large relative to frame count | reduce `chunksize`, or `jobs=1` for short clips |
| First run far slower than later runs | detector warm-up on first inference | measure after one warm-up call |
| No output for minutes, then everything at once | stdout buffering through a pipe | `print(..., flush=True)` |