#!/usr/bin/env bash
# Snap the two demo windows to exact halves of a 1920x1080 display:
#   left half  -> Billflow (Brave Browser)
#   right half -> Tryton
#
# Run this once before presenting. The LegacyBridge agent reads Tryton's LIVE
# window rect at run time, so this script only has to produce a clean, visible
# 50/50 split — the exact pixels don't have to be perfect.
#
#   bash demo/layout.sh
set -euo pipefail

MENUBAR=25                 # keep the macOS menu bar visible at the top
H=$((1080 - MENUBAR))      # window height: fill from below the menu bar to the bottom

osascript <<EOF
tell application "System Events"
    -- Billflow (Brave) -> left half
    tell (first process whose name is "Brave Browser")
        set position of window 1 to {0, ${MENUBAR}}
        set size of window 1 to {960, ${H}}
    end tell
    -- Tryton -> right half
    tell (first process whose name contains "Tryton")
        set position of window 1 to {960, ${MENUBAR}}
        set size of window 1 to {960, ${H}}
    end tell
end tell
EOF

echo "Snapped: Billflow left (0,${MENUBAR} 960x${H}) | Tryton right (960,${MENUBAR} 960x${H})."
