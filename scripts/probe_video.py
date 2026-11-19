#!/usr/bin/env python3
"""Shim: run the packaged reader without installing it.

    python scripts/probe_video.py <file.mp4>
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from video_content_reader.probe_video import main

if __name__ == "__main__":
    main()