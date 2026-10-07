#!/usr/bin/env bash
# Create .venv and install Python dependencies. Does not run apt; prints hints instead.
set -euo pipefail
cd "$(dirname "$0")/.."

PYTHON="${PYTHON:-python3}"
if ! "$PYTHON" -m venv --help >/dev/null 2>&1; then
    echo "python venv module missing: sudo apt install python3-venv" >&2
    exit 1
fi

"$PYTHON" -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

python - <<'EOF'
import platform
from importlib.metadata import version

import cv2

print("python", platform.python_version())
print("opencv", cv2.__version__)
for name in ("numpy", "pupil-apriltags", "pyserial"):
    print(name, version(name))
from pupil_apriltags import Detector
Detector(families="tag36h11")
print("pupil-apriltags tag36h11 detector OK")
EOF

command -v socat >/dev/null || echo "hint: virtual serial pair needs socat: sudo apt install socat"
echo "done. activate with: source .venv/bin/activate"
