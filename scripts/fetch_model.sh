#!/usr/bin/env bash
# fetch_model.sh — download a CT2 whisper model without going through the hub API.
#
# Why this exists: huggingface_hub's snapshot_download calls /api/models/<repo>
# first. On a mirror that endpoint can 500 while the file CDN is healthy, which
# makes WhisperModel("small") fail even though the bytes are reachable. Resolve
# the pointer URLs directly instead.
#
# Usage:
#   ./fetch_model.sh small
#   ./fetch_model.sh medium --dest /data/models
#   HF_MIRROR=https://huggingface.co ./fetch_model.sh large-v3
#
# Sizes (CT2 fp16 on disk): tiny 75M  base 145M  small 484M  medium 1.5G
#                            large-v3 3.1G

set -uo pipefail

MODEL="${1:-small}"
MIRROR="${HF_MIRROR:-https://hf-mirror.com}"
REPO="Systran/faster-whisper-${MODEL}"
BASE="${MIRROR}/${REPO}/resolve/main"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEST="${2:-${SCRIPT_DIR}/../models/faster-whisper-${MODEL}}"
[ "${2:-}" = "--dest" ] && DEST="${3:-${DEST}}"

mkdir -p "$DEST"
echo "model  ${MODEL}"
echo "from   ${BASE}"
echo "into   ${DEST}"
echo

# model.bin is the only required payload; the rest are metadata. vocabulary
# exists as .txt for older repos and .json for newer ones, so both are tried.
REQUIRED=(model.bin config.json tokenizer.json)
OPTIONAL=(vocabulary.txt vocabulary.json preprocessor_config.json)

fetch() {
  local name="$1" required="$2"
  local target="${DEST}/${name}"
  if [ -s "$target" ] && [ "$(stat -c%s "$target" 2>/dev/null || stat -f%z "$target")" -gt 1024 ]; then
    echo "  = ${name} (present)"
    return 0
  fi

  local tmp="${target}.part"
  for attempt in 1 2 3; do
    # -C - resumes a partial download; the CDN supports range requests
    if curl -fsSL --retry 3 --retry-delay 2 -C - \
            --connect-timeout 20 --max-time 3600 \
            -o "$tmp" "${BASE}/${name}"; then
      mv -f "$tmp" "$target"
      local sz
      sz=$(stat -c%s "$target" 2>/dev/null || stat -f%z "$target")
      echo "  + ${name} (${sz} bytes)"
      return 0
    fi
    echo "  ! ${name} attempt ${attempt} failed"
    sleep 2
  done

  rm -f "$tmp"
  if [ "$required" = "required" ]; then
    echo "  x ${name} could not be downloaded" >&2
    return 1
  fi
  return 0
}

fail=0
for f in "${REQUIRED[@]}"; do fetch "$f" required || fail=1; done
for f in "${OPTIONAL[@]}"; do fetch "$f" optional || true; done

if [ "$fail" -ne 0 ]; then
  echo
  echo "model download incomplete. Try another mirror:" >&2
  echo "  HF_MIRROR=https://huggingface.co $0 $MODEL" >&2
  exit 1
fi

# the mirror answers 404 with a tiny HTML body; such a file must not be kept
for f in "${OPTIONAL[@]}"; do
  t="${DEST}/${f}"
  if [ -f "$t" ] && [ "$(stat -c%s "$t" 2>/dev/null || stat -f%z "$t")" -lt 200 ]; then
    case "$f" in
      preprocessor_config.json) : ;;
      *) [ "$(head -c1 "$t")" = "{" ] || rm -f "$t" ;;
    esac
  fi
done

echo
du -sh "$DEST" 2>/dev/null || ls -la "$DEST"
echo
echo "load it with:"
echo "  WhisperModel(\"${DEST}\", device=\"cpu\", compute_type=\"int8\")"
echo "  python scripts/vidread.py VIDEO --model-path \"${DEST}\""