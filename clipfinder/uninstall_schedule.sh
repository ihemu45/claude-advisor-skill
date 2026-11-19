#!/usr/bin/env bash
# Remove the launchd job installed by install_schedule.sh.
set -euo pipefail
LABEL="com.clipfinder.autoclip"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
rm -f "$PLIST"
echo "Removed $LABEL."
