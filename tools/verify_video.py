"""Process the provided video and save reproducible comparisons and output checks."""
import argparse
import json
import sys
import time
from collections import Counter
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.lightbars import DEFAULT_CONFIG, annotate, detect, export_comparison, load_config, process_video


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input")
    parser.add_argument("--out", default="results/lightbars")
    args = parser.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    config = load_config(DEFAULT_CONFIG)
    output = out / "lightbars.mp4"
    stats = process_video(args.input, output, config)
    cap = cv2.VideoCapture(args.input)
    selected = {1, 100, 250, 400, 600, 800, 1000, 1137}
    histogram, panels, summaries = Counter(), [], []
    index = 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        index += 1
        start = time.perf_counter()
        det = detect(frame, config)
        elapsed_ms = (time.perf_counter() - start) * 1000
        histogram[len(det.lightbars)] += 1
        if index in selected:
            summaries.append(export_comparison(frame, config, out / f"frame_{index:04}", index))
            panel = annotate(frame, det.lightbars, index, elapsed_ms)
            panels.append(cv2.resize(panel, (561, 360)))
    cap.release()
    if panels:
        if len(panels) % 2:
            panels.append(np.zeros_like(panels[0]))
        cv2.imwrite(str(out / "contact-sheet.jpg"), np.vstack([
            np.hstack(panels[i:i + 2]) for i in range(0, len(panels), 2)]))
    written = cv2.VideoCapture(str(output))
    count = 0
    size = None
    fps = written.get(cv2.CAP_PROP_FPS)
    while True:
        ok, frame = written.read()
        if not ok:
            break
        count += 1
        size = (frame.shape[1], frame.shape[0])
    written.release()
    assert count == stats["frames"] == index, "decoded output/input frame counts differ"
    assert size == stats["size"], "output size differs"
    assert abs(fps - stats["fps"]) < .01, "output FPS differs"
    report = {"input_filename": Path(args.input).name, "stats": stats,
              "decoded_output_frames": count, "output_fps": fps,
              "detection_count_histogram": dict(histogram), "comparisons": summaries,
              "note": "Counts are detector outputs, not a measured accuracy score; no manual ground truth."}
    (out / "verification.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"verified {count} frames, {size}, {fps:.3f} FPS; counts={dict(histogram)}")


if __name__ == "__main__":
    main()
