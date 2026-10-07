#!/usr/bin/env bash
# Send the recorded CV1 lines to one end of a socat pair.
# Open SerialPortAssistant or COMTool on the printed B path: 115200 8N1, no flow control.
set -euo pipefail
cd "$(dirname "$0")/.."
FILE="${1:-results/cv1_from_nearfar.txt}"
A="${CV1_A:-/tmp/cv1_a}"
B="${CV1_B:-/tmp/cv1_b}"
if ! command -v socat >/dev/null; then
    echo "need socat: sudo apt install socat" >&2
    exit 1
fi
if [[ ! -f "$FILE" ]]; then
    echo "missing $FILE" >&2
    exit 1
fi
socat -d -d "pty,raw,echo=0,link=$A" "pty,raw,echo=0,link=$B" &
SOCAT_PID=$!
cleanup() { kill "$SOCAT_PID" 2>/dev/null || true; }
trap cleanup EXIT
for _ in $(seq 1 50); do
    [[ -e "$A" && -e "$B" ]] && break
    sleep 0.1
done
echo "program side A: $A"
echo "assistant side B: $B -> $(readlink -f "$B")"
echo "open the assistant on B, 115200/8N1, no flow control, then press Enter"
read -r _
. .venv/bin/activate
python - "$A" "$FILE" <<'PY'
import sys
import time
import serial

port, path = sys.argv[1], sys.argv[2]
data = open(path, "rb").read().splitlines()
with serial.Serial(port, 115200, timeout=0, write_timeout=1,
                   xonxoff=False, rtscts=False, dsrdtr=False) as ser:
    for line in data:
        ser.write(line + b"\r\n")
        ser.flush()
        print(line.decode("ascii"))
        time.sleep(0.1)
print(f"sent {len(data)} lines")
PY
