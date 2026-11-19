#!/usr/bin/env python3
"""Shim: run the packaged reader without installing it.

    python scripts/clean_subs.py <subs.txt> -o <out.txt>
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from video_content_reader.clean_subs import main

if __name__ == "__main__":
    main()