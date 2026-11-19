#!/usr/bin/env bash
# publish.sh — push this repo to GitHub once credentials exist.
#
#   ./publish.sh                      # create public video-content-reader, push
#   ./publish.sh my-name private      # custom name, private
#   ./publish.sh --existing owner/repo  # push to a repo that already exists
#
# Requires: gh authenticated (`gh auth status`), or a remote already configured.

set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

NAME="video-content-reader"
VIS="public"
EXISTING=""

case "${1:-}" in
  --existing) EXISTING="${2:?usage: publish.sh --existing owner/repo}" ;;
  "") ;;
  *) NAME="$1"; VIS="${2:-public}" ;;
esac

if [ -n "$EXISTING" ]; then
  git remote get-url origin >/dev/null 2>&1 && git remote remove origin
  git remote add origin "git@github.com:${EXISTING}.git"
  git push -u origin main
  echo "pushed to https://github.com/${EXISTING}"
  exit 0
fi

if ! gh auth status >/dev/null 2>&1; then
  echo "gh is not authenticated. run: gh auth login" >&2
  echo "then rerun: $0 $NAME $VIS" >&2
  exit 1
fi

gh repo create "$NAME" "--$VIS" --source=. --remote=origin --push \
  --description "Read a video's burned-in subtitles via OCR; fall back to whisper when nothing is printed."

echo
echo "live at: $(gh repo view --json url -q .url)"