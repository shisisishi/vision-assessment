"""Pick sharp, mutually different chessboard frames from a calibration recording."""
import argparse
import shutil
from pathlib import Path

import cv2
import numpy as np

CRITERIA = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 30, 0.001)
FLAGS = cv2.CALIB_CB_ADAPTIVE_THRESH | cv2.CALIB_CB_NORMALIZE_IMAGE | cv2.CALIB_CB_FAST_CHECK


def read_corners(video, pattern, step):
    cap = cv2.VideoCapture(video)
    frames, corners, index = {}, {}, 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        if index % step == 0:
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            found, found_corners = cv2.findChessboardCorners(gray, pattern, FLAGS)
            if found:
                refined = cv2.cornerSubPix(gray, found_corners, (11, 11), (-1, -1), CRITERIA)
                corners[index] = refined.reshape(-1, 2)
                frames[index] = frame
        index += 1
    cap.release()
    return frames, corners


def pose_feature(c, cols, rows, width, height):
    tl, tr, bl, br = c[0], c[cols - 1], c[(rows - 1) * cols], c[rows * cols - 1]
    top, bottom = np.linalg.norm(tr - tl), np.linalg.norm(br - bl)
    left, right = np.linalg.norm(bl - tl), np.linalg.norm(br - tr)
    area = cv2.contourArea(cv2.convexHull(c.astype(np.float32))) / (width * height)
    angle = np.arctan2(tr[1] - tl[1], tr[0] - tl[0])
    return [c[:, 0].mean() / width, c[:, 1].mean() / height, np.sqrt(area),
            3 * (top - bottom) / (top + bottom), 3 * (left - right) / (left + right), angle / np.pi]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("video")
    parser.add_argument("--out", default="data/calib/images")
    parser.add_argument("--count", type=int, default=22)
    parser.add_argument("--max-motion", type=float, default=4.0,
                        help="mean corner motion (px) to neighbouring sampled frames")
    parser.add_argument("--step", type=int, default=2)
    parser.add_argument("--cols", type=int, default=9)
    parser.add_argument("--rows", type=int, default=6)
    args = parser.parse_args()

    frames, corners = read_corners(args.video, (args.cols, args.rows), args.step)
    if not frames:
        raise SystemExit("no chessboard found")
    height, width = next(iter(frames.values())).shape[:2]

    idxs, feats, motions = [], [], []
    for idx, c in corners.items():
        prev, nxt = corners.get(idx - args.step), corners.get(idx + args.step)
        if prev is None or nxt is None:
            continue
        motion = max(np.linalg.norm(c - prev, axis=1).mean(), np.linalg.norm(c - nxt, axis=1).mean())
        if motion >= args.max_motion:
            continue
        idxs.append(idx)
        feats.append(pose_feature(c, args.cols, args.rows, width, height))
        motions.append(motion)
    if not idxs:
        raise SystemExit("no sharp frame found")
    idxs, feats, motions = np.array(idxs), np.array(feats), np.array(motions)

    chosen = [int(np.argmin(motions))]
    while len(chosen) < min(args.count, len(idxs)):
        dist = np.min(np.linalg.norm(feats[:, None, :] - feats[chosen][None, :, :], axis=2), axis=1)
        dist -= 0.01 * motions
        for k in chosen:
            dist[np.abs(idxs - idxs[k]) < 15] = -1
        best = int(np.argmax(dist))
        if dist[best] < 0:
            break
        chosen.append(best)

    out = Path(args.out)
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    for n, k in enumerate(sorted(chosen, key=lambda k: idxs[k])):
        cv2.imwrite(str(out / f"calib_{n:02d}_frame_{idxs[k]:04d}.png"), frames[int(idxs[k])])
    print(f"sharp frames {len(idxs)}, picked {len(chosen)} -> {out}")


if __name__ == "__main__":
    main()
