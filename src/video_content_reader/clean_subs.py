#!/usr/bin/env python3
"""clean_subs.py — strip mechanical noise from OCR'd burned-in subtitles.

Removes, in this order:
  1. lines containing kana (foreign-language credits rolling through the band)
  2. lines with no CJK at all (digits, handles, punctuation fragments)
  3. fragments of two characters or fewer unless they carry a subject term
  4. adjacent duplicate lines
  5. any line repeated inside a 12-line window (a static graphic re-sampled)

Usage:
    python clean_subs.py subs.txt [subs2.txt ...]
    python clean_subs.py --keep a.txt   # do not drop short lines

Writes <name>.clean.txt next to each input.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

KANA = re.compile(r"[\u3040-\u30ff]")
CJK = re.compile(r"[\u4e00-\u9fff]")
UI_PREFIX = re.compile(r"^(3級|3级|第?\d+[级級])\s*")
KEEP_SHORT = ("咒", "拳", "術", "术", "刃", "影", "帐", "帳")


def load(path: Path) -> list[str]:
    return [l.strip() for l in path.read_text(encoding="utf-8").splitlines()
            if l.strip()]


def clean(lines: list[str], keep_short: bool = False) -> list[str]:
    out: list[str] = []
    window: list[str] = []
    for raw in lines:
        if KANA.search(raw):
            continue
        ln = UI_PREFIX.sub("", raw).strip()
        if not ln:
            continue
        if not CJK.search(ln):
            continue
        if not keep_short and len(ln) <= 2 and not any(k in ln for k in KEEP_SHORT):
            continue
        if out and ln == out[-1]:
            continue
        if ln in window:
            continue
        out.append(ln)
        window.append(ln)
        if len(window) > 12:
            window.pop(0)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="+")
    ap.add_argument("--keep", action="store_true",
                    help="keep two-character lines")
    a = ap.parse_args()
    for f in a.files:
        p = Path(f).expanduser()
        if not p.exists():
            print(f"skip (missing): {p}", file=sys.stderr)
            continue
        before = load(p)
        after = clean(before, a.keep)
        dst = p.with_suffix(".clean.txt")
        dst.write_text("\n".join(after) + "\n", encoding="utf-8")
        print(f"{p.name}: {len(before)} -> {len(after)} lines  ({dst.name})")


if __name__ == "__main__":
    main()