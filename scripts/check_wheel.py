#!/usr/bin/env python3
"""Verify a built wheel carries the readers and the console scripts.

    python scripts/check_wheel.py dist/

Fails loudly instead of publishing an empty or half-populated wheel, which is
the usual outcome of a mis-configured packages.find.
"""

from __future__ import annotations

import sys
import zipfile
from pathlib import Path

MODULES = ("__init__", "probe_video", "subocr", "vidread", "clean_subs")
COMMANDS = ("vidprobe", "subocr", "vidread", "cleansubs")


def main() -> None:
    dist = Path(sys.argv[1] if len(sys.argv) > 1 else "dist")
    wheels = sorted(dist.glob("*.whl"))
    if not wheels:
        raise SystemExit(f"no wheel found in {dist}")
    wheel = wheels[0]

    with zipfile.ZipFile(wheel) as z:
        names = set(z.namelist())

        missing = [
            f"video_content_reader/{m}.py"
            for m in MODULES
            if f"video_content_reader/{m}.py" not in names
        ]
        if missing:
            raise SystemExit(f"{wheel.name} is missing: {', '.join(missing)}")

        entry = next((n for n in names if n.endswith("entry_points.txt")), None)
        if entry is None:
            raise SystemExit(f"{wheel.name} has no entry_points.txt")

        text = z.read(entry).decode("utf-8")
        absent = [c for c in COMMANDS if f"{c} =" not in text]
        if absent:
            raise SystemExit(f"{wheel.name} is missing commands: {', '.join(absent)}")

    print(
        f"{wheel.name}: {len(MODULES)} modules, {len(COMMANDS)} commands, "
        f"{wheel.stat().st_size:,} bytes"
    )


if __name__ == "__main__":
    main()