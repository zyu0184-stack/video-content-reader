"""Read what a video says: burned-in subtitles via OCR, speech as the fallback.

Three readers, one interface:

    probe_video   decide which reader applies to a file
    subocr        crop the subtitle band and OCR it
    vidread       scene cut, keyframes, audio, optional transcription
    clean_subs    collapse OCR lines into readable subtitles

Each module exposes ``main()`` and is wired to a console script in
``pyproject.toml``; the files in ``scripts/`` are thin shims for people who
run the repo without installing it.
"""

__version__ = "0.1.0"

__all__ = ["__version__"]