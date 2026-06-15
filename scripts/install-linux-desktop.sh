#!/usr/bin/env bash
#
# Install a freedesktop .desktop launcher for AutoSeg Evaluator (Linux).
#
# The portable .zip bundle is Windows-only; on Linux you run from source
# (pip install -e .). This registers an application-menu entry with the app
# icon so AutoSeg Evaluator shows up like a native app (and its window gets
# the icon in the taskbar/dock).
#
# Run it from the Python environment where autoseg-evaluator is installed
# (e.g. your activated venv). Override the interpreter with:
#     PYTHON=/path/to/python ./scripts/install-linux-desktop.sh
#
set -euo pipefail

PYTHON="${PYTHON:-python3}"

# Resolve the absolute interpreter path + packaged icon from the install.
if ! { read -r PY_ABS && read -r ICON; } < <(
    "$PYTHON" - <<'PYEOF'
import sys
from pathlib import Path

import autoseg_evaluator

print(sys.executable)
print(Path(autoseg_evaluator.__file__).resolve().parent / "assets" / "icon.png")
PYEOF
); then
    echo "Could not import autoseg_evaluator with '$PYTHON'." >&2
    echo "Activate the venv where it is installed, or set PYTHON=/path/to/python." >&2
    exit 1
fi

if [ ! -f "$ICON" ]; then
    echo "Packaged icon not found at: $ICON" >&2
    exit 1
fi

APPDIR="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
mkdir -p "$APPDIR"
DESKTOP="$APPDIR/autoseg-evaluator.desktop"

cat > "$DESKTOP" <<EOF
[Desktop Entry]
Type=Application
Version=1.0
Name=AutoSeg Evaluator
GenericName=Segmentation Quality Assessment
Comment=Segmentation quality assessment for radiotherapy
Exec="$PY_ABS" -m autoseg_evaluator
Icon=$ICON
Terminal=false
Categories=Science;MedicalSoftware;Education;
StartupNotify=true
StartupWMClass=autoseg-evaluator
EOF

update-desktop-database "$APPDIR" >/dev/null 2>&1 || true

echo "Installed: $DESKTOP"
echo "  Exec: \"$PY_ABS\" -m autoseg_evaluator"
echo "  Icon: $ICON"
echo "Find 'AutoSeg Evaluator' in your application menu (you may need to log out/in)."
