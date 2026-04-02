#!/bin/bash
# ============================================================
# macOS Auto-Start Setup for Job Search Automation
#
# Installs a LaunchAgent that starts the scheduler every time
# your Mac boots or you log in.
#
# If the laptop was off during the scheduled run time,
# the scheduler detects the missed run and starts immediately,
# sending you a Telegram notification.
#
# Usage:
#   bash macos_autostart.sh install    # install + enable
#   bash macos_autostart.sh uninstall  # remove
#   bash macos_autostart.sh status     # check if running
# ============================================================

LABEL="com.jobsearch.automation"
PLIST_DIR="$HOME/Library/LaunchAgents"
PLIST_PATH="$PLIST_DIR/$LABEL.plist"
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON="$PROJECT_DIR/.venv/bin/python"
LOG_DIR="$PROJECT_DIR/logs"

install_agent() {
    if [ ! -f "$PYTHON" ]; then
        echo "ERROR: .venv not found. Run 'bash setup.sh' first."
        exit 1
    fi

    mkdir -p "$PLIST_DIR" "$LOG_DIR"

    cat > "$PLIST_PATH" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>$LABEL</string>

    <key>ProgramArguments</key>
    <array>
        <string>$PYTHON</string>
        <string>$PROJECT_DIR/scheduler.py</string>
        <string>--startup</string>
    </array>

    <key>WorkingDirectory</key>
    <string>$PROJECT_DIR</string>

    <!-- Start when you log in -->
    <key>RunAtLoad</key>
    <true/>

    <!-- Restart automatically if it crashes -->
    <key>KeepAlive</key>
    <true/>

    <key>StandardOutPath</key>
    <string>$LOG_DIR/scheduler.log</string>

    <key>StandardErrorPath</key>
    <string>$LOG_DIR/scheduler_error.log</string>

    <!-- Wait 10 seconds after login before starting -->
    <key>ThrottleInterval</key>
    <integer>10</integer>
</dict>
</plist>
EOF

    # Load the agent
    launchctl unload "$PLIST_PATH" 2>/dev/null
    launchctl load -w "$PLIST_PATH"

    echo ""
    echo "✓ Auto-start installed and running."
    echo ""
    echo "The scheduler will now:"
    echo "  • Start automatically every time your Mac boots"
    echo "  • Run the job search daily at $(grep run_at $PROJECT_DIR/config.py | grep -o '"[0-9:]*"' | tr -d '"')"
    echo "  • If your Mac was off during the scheduled time:"
    echo "    → Detects the missed run on next boot"
    echo "    → Sends you a Telegram notification"
    echo "    → Starts the job search immediately"
    echo ""
    echo "Logs: $LOG_DIR/scheduler.log"
    echo ""
    echo "To check status:   bash macos_autostart.sh status"
    echo "To uninstall:      bash macos_autostart.sh uninstall"
}

uninstall_agent() {
    if [ -f "$PLIST_PATH" ]; then
        launchctl unload "$PLIST_PATH" 2>/dev/null
        rm "$PLIST_PATH"
        echo "✓ Auto-start removed. Scheduler will no longer start on boot."
    else
        echo "Auto-start is not installed."
    fi
}

status_agent() {
    echo "Project:  $PROJECT_DIR"
    echo "Plist:    $PLIST_PATH"
    echo ""
    if launchctl list | grep -q "$LABEL"; then
        echo "Status:   ✅ Running"
        launchctl list "$LABEL" 2>/dev/null | grep -E "PID|LastExitStatus"
    else
        echo "Status:   ⏸ Not running"
        if [ -f "$PLIST_PATH" ]; then
            echo "          (plist exists but agent is not loaded)"
        else
            echo "          (run 'bash macos_autostart.sh install' to enable)"
        fi
    fi
    if [ -f "$LOG_DIR/scheduler.log" ]; then
        echo ""
        echo "Last 5 log lines:"
        tail -5 "$LOG_DIR/scheduler.log"
    fi
}

case "${1:-}" in
    install)   install_agent ;;
    uninstall) uninstall_agent ;;
    status)    status_agent ;;
    *)
        echo "Usage: bash macos_autostart.sh [install|uninstall|status]"
        echo ""
        echo "  install    — start scheduler on every Mac boot"
        echo "  uninstall  — remove auto-start"
        echo "  status     — check if running"
        ;;
esac
