"""Linux virtual serial pair helper (task 3).

create : run socat to make two connected PTYs: program -> A ===== B -> serial assistant.
listen : optional diagnostic receiver on B that splits by CRLF and checks CV1 checksums.
         It cannot share B with the assistant; final evidence must come from
         COMTool or SerialPortAssistant.
"""

import argparse
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime

from .protocol import BAUDRATE, parse_frame


def create(args):
    if os.name != "posix":
        print("error: PTY pairs need Linux (socat)", file=sys.stderr)
        return 1
    if shutil.which("socat") is None:
        print("error: socat not found (Ubuntu: sudo apt install socat)", file=sys.stderr)
        return 1
    cmd = ["socat", "-d", "-d", f"pty,raw,echo=0,link={args.a}", f"pty,raw,echo=0,link={args.b}"]
    print("running:", " ".join(cmd))
    proc = subprocess.Popen(cmd)
    try:
        for _ in range(50):
            if os.path.exists(args.a) and os.path.exists(args.b):
                break
            time.sleep(0.1)
        else:
            print("error: socat did not create the links", file=sys.stderr)
            return 1
        print(f"program side   A: {args.a} -> {os.path.realpath(args.a)}")
        print(f"assistant side B: {args.b} -> {os.path.realpath(args.b)}")
        print(f"configure both ends {BAUDRATE} 8N1, no flow control. Ctrl+C to close the pair.")
        return proc.wait()
    except KeyboardInterrupt:
        return 0
    finally:
        if proc.poll() is None:
            proc.terminate()
            proc.wait()


def listen(args):
    try:
        import serial
    except ImportError:
        print("error: pyserial is not installed", file=sys.stderr)
        return 1
    log = open(args.log, "a", encoding="utf-8") if args.log else None
    buffer = b""
    try:
        with serial.Serial(args.port, BAUDRATE, bytesize=serial.EIGHTBITS,
                           parity=serial.PARITY_NONE, stopbits=serial.STOPBITS_ONE,
                           xonxoff=False, rtscts=False, timeout=0.2) as port:
            print(f"listening on {args.port} (diagnostic only), Ctrl+C to stop")
            while True:
                buffer += port.read(256)
                while b"\r\n" in buffer:
                    raw, buffer = buffer.split(b"\r\n", 1)
                    try:
                        frame = parse_frame(raw)
                        note = "OK" if frame["valid"] else "OK invalid"
                    except (ValueError, UnicodeDecodeError) as exc:
                        note = f"BAD {exc}"
                    text = f"{datetime.now().isoformat(timespec='milliseconds')} {raw.decode('ascii', 'replace')} [{note}]"
                    print(text)
                    if log:
                        log.write(text + "\n")
                        log.flush()
    except KeyboardInterrupt:
        return 0
    except serial.SerialException as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    finally:
        if log:
            log.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description="virtual serial pair helper")
    sub = parser.add_subparsers(dest="command", required=True)
    c = sub.add_parser("create", help="create a connected PTY pair with socat")
    c.add_argument("--a", default="/tmp/cv1_a", help="program end")
    c.add_argument("--b", default="/tmp/cv1_b", help="assistant end")
    r = sub.add_parser("listen", help="diagnostic CV1 receiver")
    r.add_argument("--port", default="/tmp/cv1_b")
    r.add_argument("--log", help="append received lines to this file")
    args = parser.parse_args(argv)
    return create(args) if args.command == "create" else listen(args)


if __name__ == "__main__":
    sys.exit(main())
