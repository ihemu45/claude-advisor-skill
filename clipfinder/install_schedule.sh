#!/usr/bin/env bash
# Install the watcher as a macOS launchd job: runs `autoclip.py run` every 2 hours,
# even when Terminal is closed (the Mac must be awake). Logs go to <workdir>/logs/.
#   ./install_schedule.sh            # install (or re-install) and run once now
#   ./install_schedule.sh 3600       # custom interval in seconds
set -euo pipefail
cd "$(dirname "$0")"
INTERVAL="${1:-7200}"
LABEL="com.clipfinder.autoclip"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
PY="$(command -v python3)"
DIR="$(pwd)"
[ -f config.json ] || { echo "No config.json here. Copy config.example.json to config.json first."; exit 1; }
mkdir -p "$HOME/Library/LaunchAgents" logs
cat > "$PLIST" <<PL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key><array>
    <string>$PY</string><string>$DIR/autoclip.py</string><string>--config</string><string>$DIR/config.json</string><string>run</string>
  </array>
  <key>WorkingDirectory</key><string>$DIR</string>
  <key>StartInterval</key><integer>$INTERVAL</integer>
  <key>RunAtLoad</key><true/>
  <key>StandardOutPath</key><string>$DIR/logs/launchd.out.log</string>
  <key>StandardErrorPath</key><string>$DIR/logs/launchd.err.log</string>
  <key>EnvironmentVariables</key><dict>
    <key>PATH</key><string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
    <key>HOME</key><string>$HOME</string>
  </dict>
</dict></plist>
PL
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
echo "Installed. Runs every $INTERVAL seconds and once right now."
echo "Logs: $DIR/logs/launchd.out.log  |  Check queue: python3 autoclip.py status  |  Remove: ./uninstall_schedule.sh"
