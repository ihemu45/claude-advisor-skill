#!/usr/bin/env bash
# End-to-end self-test: synthetic 4-minute stream + fake chat -> highlights + vertical clips.
# Needs python3 and ffmpeg on PATH. Takes 1-3 minutes on a laptop.
set -euo pipefail
cd "$(dirname "$0")"
[ -f testdata/test_stream.mp4 ] || python3 make_test_data.py testdata
python3 find_highlights.py \
  --chat testdata/chat_twitchdownloader.json \
  --video testdata/test_stream.mp4 \
  --vertical center --thumbs --top 5 --min-gap 40 \
  --out demo_out
echo
cat demo_out/highlights.md
echo
echo "Clips and thumbnails are in demo_out/. Expected spikes: ~00:01:00, ~00:02:30, ~00:03:20."
