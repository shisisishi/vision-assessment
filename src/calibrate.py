"""Task 2: chessboard capture and OpenCV camera calibration.

capture:   live preview, [s] saves the raw frame, [q]/Esc quits.
calibrate: 9x6 inner corners (10x7 squares), measured square size in meters,
           sub-pixel corners, one resolution only, JSON with K/dist/RMS/per-image errors.
Reference: https://docs.opencv.org/4.x/dc/dbb/tutorial_py_calibration.html
"""

import argparse
import glob
import json
import sys
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff"}
FIND_FLAGS = cv2.CALIB_CB_ADAPTIVE_THRESH | cv2.CALIB_CB_NORMALIZE_IMAGE
SUBPIX_CRITERIA = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 30, 0.001)


def capture(args):
    device = int(args.camera) if str(args.camera).isdigit() else args.camera
    cap = cv2.VideoCapture(device)
    if args.width and args.height:
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, args.width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, args.height)
    if not cap.isOpened():
        print(f"error: cannot open camera {args.camera}", file=sys.stderr)
        return 1
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    pattern = (args.cols, args.rows)
    saved = 0
    try:
        while True:
            ok, frame = cap.read()
            if not ok or frame is None:
                print("error: failed to read frame", file=sys.stderr)
                return 1
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            found, corners = cv2.findChessboardCorners(gray, pattern,
                                                       FIND_FLAGS | cv2.CALIB_CB_FAST_CHECK)
            preview = frame.copy()
            cv2.drawChessboardCorners(preview, pattern, corners, found)
            h, w = frame.shape[:2]
            cv2.putText(preview, f"{w}x{h} board={'yes' if found else 'no'} saved={saved}  "
                        "[s] save  [q] quit", (10, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.6,
                        (0, 255, 0) if found else (0, 0, 255), 2)
            cv2.imshow("calibration capture", preview)
            key = cv2.waitKey(1) & 0xFF
            if key == ord("s"):
                path = next(out_dir / f"calib_{i:03d}.png" for i in range(10000)
                            if not (out_dir / f"calib_{i:03d}.png").exists())
                cv2.imwrite(str(path), frame)
                saved += 1
                print(f"saved {path} ({w}x{h}){'' if found else '  [warning: board not detected]'}")
            elif key in (ord("q"), 27):
                break
    finally:
        cap.release()
        cv2.destroyAllWindows()
    print(f"{saved} images saved to {out_dir}")
    return 0


def collect_paths(patterns):
    paths = []
    for pattern in patterns:
        path = Path(pattern)
        if path.is_dir():
            paths += [p for p in path.iterdir() if p.suffix.lower() in IMAGE_EXTENSIONS]
        else:
            paths += [Path(p) for p in glob.glob(pattern)]
    return sorted(set(paths))


def per_image_rms(object_points, image_points, rvec, tvec, K, dist):
    projected, _ = cv2.projectPoints(object_points, rvec, tvec, K, dist)
    diff = projected.reshape(-1, 2) - image_points.reshape(-1, 2)
    return float(np.sqrt(np.mean(np.sum(diff ** 2, axis=1))))


def calibrate(args):
    if args.square <= 0:
        print("error: --square must be the measured square edge in meters", file=sys.stderr)
        return 1
    paths = collect_paths(args.images)
    if not paths:
        print(f"error: no images found for {args.images}", file=sys.stderr)
        return 1
    pattern = (args.cols, args.rows)
    board = np.zeros((args.cols * args.rows, 3), np.float32)
    board[:, :2] = np.mgrid[0:args.cols, 0:args.rows].T.reshape(-1, 2) * args.square
    image_size = None
    object_points, image_points, accepted, rejected = [], [], [], []
    vis_dir = Path(args.vis_dir) if args.vis_dir else None
    if vis_dir:
        vis_dir.mkdir(parents=True, exist_ok=True)
    for path in paths:
        image = cv2.imread(str(path))
        if image is None:
            rejected.append({"path": str(path), "reason": "unreadable"})
            continue
        size = (image.shape[1], image.shape[0])
        image_size = image_size or size
        if size != image_size:
            rejected.append({"path": str(path),
                             "reason": f"resolution {size[0]}x{size[1]} != {image_size[0]}x{image_size[1]}"})
            continue
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        found, corners = cv2.findChessboardCorners(gray, pattern, FIND_FLAGS)
        if not found:
            rejected.append({"path": str(path), "reason": f"{args.cols}x{args.rows} corners not found"})
            continue
        corners = cv2.cornerSubPix(gray, corners, (11, 11), (-1, -1), SUBPIX_CRITERIA)
        object_points.append(board)
        image_points.append(corners)
        accepted.append(str(path))
        if vis_dir:
            cv2.drawChessboardCorners(image, pattern, corners, True)
            cv2.imwrite(str(vis_dir / path.name), image)
    print(f"accepted {len(accepted)} / {len(paths)} images")
    for item in rejected:
        print(f"  rejected {item['path']}: {item['reason']}")
    if len(accepted) < args.min_images:
        print(f"error: need at least {args.min_images} accepted images", file=sys.stderr)
        return 1
    if len(accepted) < 15:
        print("warning: 15-25 varied images (centre, edges, distances, tilts) are recommended")
    rms, K, dist, rvecs, tvecs = cv2.calibrateCamera(object_points, image_points, image_size,
                                                     None, None)
    errors = [{"path": p, "rms_px": round(per_image_rms(o, i, r, t, K, dist), 4)}
              for p, o, i, r, t in zip(accepted, object_points, image_points, rvecs, tvecs)]
    result = {
        "image_size": list(image_size),
        "camera_matrix": K.tolist(),
        "dist_coeffs": dist.reshape(-1).tolist(),
        "dist_model": "OpenCV k1,k2,p1,p2,k3",
        "rms_reprojection_error_px": float(rms),
        "per_image_rms_px": errors,
        "board": {"inner_corners": [args.cols, args.rows], "square_size_m": args.square},
        "accepted": accepted,
        "rejected": rejected,
        "opencv_version": cv2.__version__,
        "created_at": datetime.now().isoformat(timespec="seconds"),
    }
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"image size {image_size[0]}x{image_size[1]}, RMS reprojection error {rms:.4f} px")
    print(f"K =\n{np.array2string(K, precision=3)}\ndist = {np.array2string(dist.reshape(-1), precision=5)}")
    for item in sorted(errors, key=lambda e: -e["rms_px"])[:5]:
        print(f"  worst: {item['path']} {item['rms_px']:.4f} px")
    print(f"saved {out}")
    return 0


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="chessboard capture and camera calibration")
    sub = parser.add_subparsers(dest="command", required=True)
    cap = sub.add_parser("capture", help="save chessboard frames from a camera")
    cap.add_argument("--camera", default="0", help="camera index or device path")
    cap.add_argument("--out", default="data/calib/images")
    cap.add_argument("--width", type=int, help="requested capture width")
    cap.add_argument("--height", type=int, help="requested capture height")
    cal = sub.add_parser("calibrate", help="calibrate from saved images")
    cal.add_argument("--images", nargs="+", default=["data/calib/images"],
                     help="image directory or glob pattern(s)")
    cal.add_argument("--square", type=float, required=True,
                     help="measured square edge length in meters (e.g. 0.0200)")
    cal.add_argument("--output", default="data/calib/camera.json")
    cal.add_argument("--vis-dir", help="save images with drawn corners here")
    cal.add_argument("--min-images", type=int, default=8)
    for p in (cap, cal):
        p.add_argument("--cols", type=int, default=9, help="inner corners per row")
        p.add_argument("--rows", type=int, default=6, help="inner corners per column")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    return capture(args) if args.command == "capture" else calibrate(args)


if __name__ == "__main__":
    sys.exit(main())
