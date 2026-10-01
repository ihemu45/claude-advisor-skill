#!/usr/bin/env bash
# Rebuild dist/<skill>.zip for every skill in skills/ (for claude.ai upload).
set -euo pipefail
cd "$(dirname "$0")/../skills"
mkdir -p ../dist
for dir in */; do
  name="${dir%/}"
  rm -f "../dist/$name.zip"
  zip -qr "../dist/$name.zip" "$name"
  echo "built dist/$name.zip"
done
