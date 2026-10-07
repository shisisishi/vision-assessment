"""DIAGNOSTIC ONLY: send fixed CV1 frames to check the serial link and checksum.

These values are NOT detection results and are not evidence for the real-time task.
  --mode samples : the two manual example lines (seq 42/43, *33 and *09), repeated
  --mode fixed   : real seq/t_ms, fixed pose (100,-50,800) mm, 1 s valid / 1 s invalid
"""

import argparse
import sys
import time

from .protocol import ElapsedMs, SequenceCounter, SerialSender, format_frame

MANUAL_SAMPLES = [
    format_frame(42, 12345, 0, (0.100, -0.050, 0.800), (0.0, 0.0, 0.0)),
    format_frame(43, 12445),
]


def main(argv=None):
    parser = argparse.ArgumentParser(description="CV1 fixed-value serial diagnostic")
    parser.add_argument("--port", required=True, help="e.g. /tmp/cv1_a")
    parser.add_argument("--mode", choices=["samples", "fixed"], default="fixed")
    parser.add_argument("--count", type=int, default=50, help="frames to send")
    parser.add_argument("--rate", type=float, default=10.0, help="frames per second")
    args = parser.parse_args(argv)

    print("DIAGNOSTIC ONLY: fixed values, not AprilTag detection results", file=sys.stderr)
    try:
        sender = SerialSender(args.port)
    except (RuntimeError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    clock, seq = ElapsedMs(), SequenceCounter()
    try:
        for i in range(args.count):
            if args.mode == "samples":
                line = MANUAL_SAMPLES[i % 2]
            elif (clock() // 1000) % 2 == 0:
                line = format_frame(seq.next(), clock(), 0, (0.100, -0.050, 0.800), (0.0, 0.0, 0.0))
            else:
                line = format_frame(seq.next(), clock())
            ok = sender.send(line)
            print(("sent " if ok else "FAILED ") + line.rstrip())
            time.sleep(1.0 / args.rate)
    except KeyboardInterrupt:
        pass
    finally:
        sender.close()
    return 1 if sender.failures else 0


if __name__ == "__main__":
    sys.exit(main())
