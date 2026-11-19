#!/usr/bin/env python3
"""Shim: run the packaged reader without installing it.

    python scripts/subocr.py <file.mp4> -o <outdir>
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from video_content_reader.subocr import main

if __name__ == "__main__":
    main()