"""CV1 ASCII serial protocol (task 3) and a minimal pyserial sender.

Frame: $CV1,seq,t_ms,valid,id,x_mm,y_mm,z_mm,rx,ry,rz*HH\r\n
HH = XOR of every byte between '$' and '*', two upper-case hex digits.
"""

import sys
import time

import numpy as np

UINT32_MOD = 1 << 32
BAUDRATE = 115200


def checksum(body):
    value = 0
    for byte in body.encode("ascii"):
        value ^= byte
    return f"{value:02X}"


def _fixed(value, digits):
    text = f"{value:.{digits}f}"
    if float(text) == 0.0:
        text = f"{0.0:.{digits}f}"
    return text


def _pose_values(tag_id, t_m, rvec):
    if tag_id is None or t_m is None or rvec is None or int(tag_id) < 0:
        return None
    try:
        t = np.asarray(t_m, dtype=float).reshape(-1)
        r = np.asarray(rvec, dtype=float).reshape(-1)
    except (TypeError, ValueError):
        return None
    if t.size != 3 or r.size != 3:
        return None
    if not (np.all(np.isfinite(t)) and np.all(np.isfinite(r))) or t[2] <= 0:
        return None
    return t * 1000.0, r


def format_frame(seq, t_ms, tag_id=None, t_m=None, rvec=None):
    """Build one CV1 line (with CRLF). Any missing/non-finite/behind-camera pose gives valid=0."""
    head = [str(int(seq) % UINT32_MOD), str(int(t_ms))]
    values = _pose_values(tag_id, t_m, rvec)
    if values is None:
        fields = head + ["0", "-1", "0.0", "0.0", "0.0", "0.000000", "0.000000", "0.000000"]
    else:
        t_mm, r = values
        fields = head + ["1", str(int(tag_id))]
        fields += [_fixed(v, 1) for v in t_mm] + [_fixed(v, 6) for v in r]
    body = "CV1," + ",".join(fields)
    return f"${body}*{checksum(body)}\r\n"


def parse_frame(line):
    """Parse a CV1 line into a dict; raises ValueError on framing or checksum errors."""
    if isinstance(line, bytes):
        line = line.decode("ascii")
    line = line.rstrip("\r\n")
    if not line.startswith("$") or len(line) < 4 or line[-3] != "*":
        raise ValueError(f"bad framing: {line!r}")
    body, received = line[1:-3], line[-2:]
    expected = checksum(body)
    if received != expected:
        raise ValueError(f"checksum mismatch: got {received}, expected {expected}")
    fields = body.split(",")
    if len(fields) != 11 or fields[0] != "CV1":
        raise ValueError(f"bad field count or version: {line!r}")
    return {
        "seq": int(fields[1]),
        "t_ms": int(fields[2]),
        "valid": fields[3] == "1",
        "id": int(fields[4]),
        "xyz_mm": tuple(float(v) for v in fields[5:8]),
        "rvec": tuple(float(v) for v in fields[8:11]),
    }


class SequenceCounter:
    """uint32 sequence: returns 0, 1, ... and wraps from 2**32-1 to 0."""

    def __init__(self, start=0):
        self._value = int(start) % UINT32_MOD

    def next(self):
        value = self._value
        self._value = (value + 1) % UINT32_MOD
        return value


class ElapsedMs:
    """Monotonic milliseconds since construction (program start)."""

    def __init__(self):
        self._start = time.monotonic()

    def __call__(self):
        return int((time.monotonic() - self._start) * 1000)


class SerialSender:
    """115200/8N1, no flow control. Failed writes are reported on stderr and counted."""

    def __init__(self, port):
        try:
            import serial
        except ImportError as exc:
            raise RuntimeError("pyserial is not installed: pip install -r requirements.txt") from exc
        self._errors = (serial.SerialException, OSError)
        self.port = port
        self.failures = 0
        self._serial = serial.Serial(
            port,
            BAUDRATE,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            xonxoff=False,
            rtscts=False,
            dsrdtr=False,
            timeout=0,
            write_timeout=0.5,
        )

    def send(self, line):
        data = line.encode("ascii")
        try:
            written = self._serial.write(data)
            ok = written == len(data)
            reason = f"wrote {written}/{len(data)} bytes"
        except self._errors as exc:
            ok, reason = False, str(exc)
        if not ok:
            self.failures += 1
            print(f"[serial] send failed on {self.port} ({reason}), total failures={self.failures}",
                  file=sys.stderr)
        return ok

    def close(self):
        self._serial.close()
