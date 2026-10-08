"""Task 2: tag36h11 detection and pose with pupil-apriltags, optional CV1 serial output (task 3).

Pipeline per frame: read -> exact resolution check -> undistort (new K = calibration K)
-> detect all tags with pose -> select target id -> draw / log / send.

Camera frame: X right, Y down, Z forward (optical axis).
Tag frame (AprilTag definition): origin at tag centre, X right, Y down, Z into the tag.
p_camera = R @ p_tag + t, t in meters.
"""

import argparse
import csv
import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from .protocol import ElapsedMs, SequenceCounter, SerialSender, format_frame

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff"}
SEND_PERIOD_S = 0.1
MAX_EMPTY_FRAMES = 50
NO_DISTORTION = np.zeros(5)


@dataclass
class Calibration:
    camera_matrix: np.ndarray
    dist_coeffs: np.ndarray
    image_size: tuple


def load_calibration(path):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    try:
        K = np.asarray(data["camera_matrix"], dtype=float)
        dist = np.asarray(data["dist_coeffs"], dtype=float).reshape(-1)
        width, height = (int(v) for v in data["image_size"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"{path}: invalid calibration file ({exc})") from exc
    if K.shape != (3, 3) or not np.all(np.isfinite(K)) or K[0, 0] <= 0 or K[1, 1] <= 0:
        raise ValueError(f"{path}: camera_matrix must be a finite 3x3 matrix with fx, fy > 0")
    if dist.size not in (4, 5, 8, 12, 14) or not np.all(np.isfinite(dist)):
        raise ValueError(f"{path}: dist_coeffs must hold 4/5/8/12/14 finite values")
    if width <= 0 or height <= 0:
        raise ValueError(f"{path}: image_size must be positive [width, height]")
    return Calibration(K, dist, (width, height))


def check_resolution(frame, image_size):
    height, width = frame.shape[:2]
    if (width, height) != tuple(image_size):
        return (f"frame resolution {width}x{height} does not match calibration "
                f"{image_size[0]}x{image_size[1]}")
    return None


def tag_object_corners(tag_size):
    """Tag-frame corners in the order pupil-apriltags returns image corners (apriltag_pose.c)."""
    h = tag_size / 2.0
    return np.array([[-h, h, 0.0], [h, h, 0.0], [h, -h, 0.0], [-h, -h, 0.0]])


def validate_pose(R, t, tol=1e-3):
    """Return None for a usable pose, otherwise a short reason."""
    if R is None or t is None:
        return "pose not estimated"
    R = np.asarray(R, dtype=float)
    t = np.asarray(t, dtype=float).reshape(-1)
    if R.shape != (3, 3) or t.shape != (3,):
        return "bad pose shape"
    if not (np.all(np.isfinite(R)) and np.all(np.isfinite(t))):
        return "non-finite pose"
    if np.linalg.norm(R.T @ R - np.eye(3)) > tol or abs(np.linalg.det(R) - 1.0) > tol:
        return "R is not a proper rotation"
    if t[2] <= 0:
        return "tag not in front of camera (z <= 0)"
    return None


@dataclass
class TagDetection:
    tag_id: int
    center: np.ndarray
    corners: np.ndarray
    R: np.ndarray
    t: np.ndarray
    error: str
    decision_margin: float = 0.0
    hamming: int = 0
    pose_err: float = float("nan")

    @property
    def valid(self):
        return self.error is None

    @property
    def distance(self):
        return float(np.linalg.norm(self.t))

    @property
    def rvec(self):
        return cv2.Rodrigues(self.R)[0].reshape(-1)


def make_detection(tag_id, center, corners, R, t, decision_margin=0.0, hamming=0,
                   pose_err=float("nan")):
    error = validate_pose(R, t)
    if error is None:
        R = np.asarray(R, dtype=float)
        t = np.asarray(t, dtype=float).reshape(-1)
    else:
        R = t = None
    return TagDetection(int(tag_id), np.asarray(center, dtype=float).reshape(2),
                        np.asarray(corners, dtype=float).reshape(4, 2), R, t, error,
                        float(decision_margin), int(hamming), float(pose_err))


def select_target(detections, target_id):
    """Pick the target from the full detection list without modifying it."""
    matches = [d for d in detections if d.tag_id == target_id]
    if not matches:
        return None, "target_missing"
    usable = [d for d in matches if d.valid]
    if not usable:
        return None, f"pose_invalid: {matches[0].error}"
    return max(usable, key=lambda d: d.decision_margin), "valid"


def project_points(points, R, t, K):
    """Project tag-frame points into the undistorted image (distortion already removed)."""
    rvec = cv2.Rodrigues(np.asarray(R, dtype=float))[0]
    image_points, _ = cv2.projectPoints(np.asarray(points, dtype=float).reshape(-1, 1, 3), rvec,
                                        np.asarray(t, dtype=float).reshape(3, 1), K, NO_DISTORTION)
    return image_points.reshape(-1, 2)


def corner_reprojection_error(detection, K, tag_size):
    projected = project_points(tag_object_corners(tag_size), detection.R, detection.t, K)
    return float(np.mean(np.linalg.norm(projected - detection.corners, axis=1)))


class TagPoseEstimator:
    def __init__(self, calibration, tag_size, quad_decimate=2.0, nthreads=2):
        try:
            from pupil_apriltags import Detector
        except ImportError as exc:
            raise RuntimeError("pupil-apriltags is not installed: pip install -r requirements.txt") from exc
        if tag_size <= 0:
            raise ValueError("tag size must be a positive length in meters")
        K, dist = calibration.camera_matrix, calibration.dist_coeffs
        self.tag_size = tag_size
        self.camera_matrix = K
        self._maps = cv2.initUndistortRectifyMap(K, dist, None, K, calibration.image_size,
                                                 cv2.CV_16SC2)
        self.camera_params = (float(K[0, 0]), float(K[1, 1]), float(K[0, 2]), float(K[1, 2]))
        self.detector = Detector(families="tag36h11", nthreads=nthreads,
                                 quad_decimate=quad_decimate, refine_edges=1)

    def undistort(self, frame):
        return cv2.remap(frame, self._maps[0], self._maps[1], cv2.INTER_LINEAR)

    def detect(self, undistorted):
        gray = cv2.cvtColor(undistorted, cv2.COLOR_BGR2GRAY) if undistorted.ndim == 3 else undistorted
        # AprilTag 需要黑框外的白边。标签贴到画面边缘时补一圈白边，位姿仍用原相机内参。
        pad = 80
        padded = cv2.copyMakeBorder(gray, pad, pad, pad, pad, cv2.BORDER_CONSTANT, value=255)
        fx, fy, cx, cy = self.camera_params
        results = self.detector.detect(padded, estimate_tag_pose=True,
                                       camera_params=(fx, fy, cx + pad, cy + pad),
                                       tag_size=self.tag_size)
        detections = []
        for result in results:
            center = np.asarray(result.center, dtype=float).reshape(2) - pad
            corners = np.asarray(result.corners, dtype=float).reshape(4, 2) - pad
            detections.append(make_detection(result.tag_id, center, corners, result.pose_R,
                                             result.pose_t, result.decision_margin, result.hamming,
                                             result.pose_err))
        return detections


def _pt(p):
    return int(round(float(p[0]))), int(round(float(p[1])))


def draw_overlay(image, detections, target, status, target_id, K, tag_size):
    out = image.copy()
    for det in detections:
        cv2.polylines(out, [np.round(det.corners).astype(np.int32)], True, (0, 200, 255), 1)
        cv2.putText(out, f"id{det.tag_id}", _pt(det.center), cv2.FONT_HERSHEY_SIMPLEX, 0.5,
                    (0, 200, 255), 1)
    lines = [f"detections: {len(detections)} ids={[d.tag_id for d in detections]}"]
    if target is not None:
        cv2.polylines(out, [np.round(target.corners).astype(np.int32)], True, (0, 255, 0), 2)
        for i, corner in enumerate(target.corners):
            cv2.circle(out, _pt(corner), 4, (0, 255, 0), -1)
            cv2.putText(out, str(i), _pt(corner + 6), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)
        cv2.circle(out, _pt(target.center), 5, (0, 0, 255), -1)
        length = tag_size / 2.0
        axes = project_points([[0, 0, 0], [length, 0, 0], [0, length, 0], [0, 0, length]],
                              target.R, target.t, K)
        for end, color, name in zip(axes[1:], [(0, 0, 255), (0, 255, 0), (255, 0, 0)], "XYZ"):
            cv2.line(out, _pt(axes[0]), _pt(end), color, 2)
            cv2.putText(out, name, _pt(end), cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2)
        x, y, z = target.t
        lines += [f"target id{target.tag_id} VALID",
                  f"t = ({x:+.3f}, {y:+.3f}, {z:+.3f}) m",
                  f"distance = {target.distance:.3f} m   depth z = {z:.3f} m"]
        lines += ["R = [" + " ".join(f"{v:+.3f}" for v in row) + "]" for row in target.R]
    else:
        lines.append(f"target id{target_id}: {status}")
    for i, text in enumerate(lines):
        org = (10, 24 + 22 * i)
        cv2.putText(out, text, org, cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 0), 4)
        cv2.putText(out, text, org, cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1)
    return out


class FrameSource:
    """Camera, video file, single image or image directory. read() -> (kind, frame)."""

    def __init__(self, camera=None, input_path=None, size=None):
        self.live = camera is not None
        self.images = None
        self.cap = None
        if self.live:
            device = int(camera) if str(camera).isdigit() else camera
            self.cap = cv2.VideoCapture(device)
            if size:
                self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, size[0])
                self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, size[1])
            if not self.cap.isOpened():
                raise RuntimeError(f"cannot open camera {camera}")
            return
        path = Path(input_path)
        if not path.exists():
            raise FileNotFoundError(f"input not found: {path}")
        if path.is_dir():
            self.images = sorted(p for p in path.iterdir() if p.suffix.lower() in IMAGE_EXTENSIONS)
        elif path.suffix.lower() in IMAGE_EXTENSIONS:
            self.images = [path]
        else:
            self.cap = cv2.VideoCapture(str(path))
            if not self.cap.isOpened():
                raise RuntimeError(f"cannot open video {path}")

    @property
    def fps(self):
        if self.images is not None:
            return 5.0
        fps = self.cap.get(cv2.CAP_PROP_FPS)
        return fps if fps and np.isfinite(fps) and fps > 0 else 30.0

    def read(self):
        if self.images is not None:
            if not self.images:
                return "end", None
            frame = cv2.imread(str(self.images.pop(0)))
            return ("frame", frame) if frame is not None else ("empty", None)
        ok, frame = self.cap.read()
        if ok and frame is not None and frame.size:
            return "frame", frame
        return ("empty", None) if self.live else ("end", None)

    def release(self):
        if self.cap is not None:
            self.cap.release()


LOG_HEADER = ["frame", "t_ms", "status", "num_detections", "detected_ids", "target_id",
              "x_m", "y_m", "z_m", "distance_m", "rx", "ry", "rz", "R_rowmajor", "pose_err",
              "corner_reproj_px", "serial_line"]


def _log_row(index, t_ms, status, detections, target, K, tag_size, line):
    row = [index, t_ms, status, len(detections), " ".join(str(d.tag_id) for d in detections)]
    if target is None:
        row += [-1] + [""] * 10
    else:
        row += [target.tag_id] + [f"{v:.6f}" for v in target.t] + [f"{target.distance:.6f}"]
        row += [f"{v:.6f}" for v in target.rvec]
        row += [" ".join(f"{v:.6f}" for v in target.R.reshape(-1)), f"{target.pose_err:.3e}",
                f"{corner_reprojection_error(target, K, tag_size):.3f}"]
    return row + [line.rstrip("\r\n")]


def run(args):
    calibration = load_calibration(args.calibration)
    estimator = TagPoseEstimator(calibration, args.tag_size, quad_decimate=args.decimate)
    source = FrameSource(args.camera, args.input, calibration.image_size)
    K = estimator.camera_matrix
    sender = SerialSender(args.serial) if args.serial else None
    clock, seq = ElapsedMs(), SequenceCounter()
    writer = log_file = log_writer = None
    if args.log:
        Path(args.log).parent.mkdir(parents=True, exist_ok=True)
        log_file = open(args.log, "w", newline="", encoding="utf-8")
        log_writer = csv.writer(log_file)
        log_writer.writerow(LOG_HEADER)
    index, empty_streak, next_tick, exit_code = 0, 0, 0.0, 0
    try:
        while args.max_frames is None or index < args.max_frames:
            kind, frame = source.read()
            if kind == "end":
                print("input finished")
                break
            detections, target, view = [], None, None
            if kind == "empty":
                status = "empty_frame"
                empty_streak += 1
            else:
                empty_streak = 0
                error = check_resolution(frame, calibration.image_size)
                if error:
                    print(f"error: {error}; calibrate at this resolution or capture at the "
                          "calibrated one", file=sys.stderr)
                    exit_code = 2
                    break
                undistorted = estimator.undistort(frame)
                detections = estimator.detect(undistorted)
                target, status = select_target(detections, args.target_id)
                view = draw_overlay(undistorted, detections, target, status, args.target_id, K,
                                    args.tag_size)
            t_ms = clock()
            line = ""
            now = time.monotonic()
            if now >= next_tick:
                next_tick = now + SEND_PERIOD_S
                if target is None:
                    line = format_frame(seq.next(), t_ms)
                else:
                    line = format_frame(seq.next(), t_ms, target.tag_id, target.t, target.rvec)
                if sender is not None:
                    sender.send(line)
                if target is None:
                    print(f"[{t_ms} ms] {status}, detections={len(detections)}")
                else:
                    x, y, z = target.t
                    print(f"[{t_ms} ms] id{target.tag_id} t=({x:+.4f}, {y:+.4f}, {z:+.4f}) m "
                          f"dist={target.distance:.4f} m z={z:.4f} m "
                          f"R={np.round(target.R, 4).tolist()} | {line.rstrip()}")
            if log_writer is not None:
                log_writer.writerow(_log_row(index, t_ms, status, detections, target, K,
                                             args.tag_size, line))
            index += 1
            if empty_streak >= MAX_EMPTY_FRAMES:
                print(f"error: {empty_streak} consecutive empty frames, stopping", file=sys.stderr)
                exit_code = 1
                break
            if view is None:
                continue
            if args.output_video:
                if writer is None:
                    Path(args.output_video).parent.mkdir(parents=True, exist_ok=True)
                    writer = cv2.VideoWriter(args.output_video, cv2.VideoWriter_fourcc(*"mp4v"),
                                             source.fps, (view.shape[1], view.shape[0]))
                    if not writer.isOpened():
                        raise RuntimeError(f"cannot create output video: {args.output_video}")
                writer.write(view)
            if not args.headless:
                cv2.imshow("tagpose (undistorted)", view)
                key = cv2.waitKey(0 if source.images is not None else 1) & 0xFF
                if key in (ord("q"), 27):
                    break
    finally:
        source.release()
        if writer is not None:
            writer.release()
        if log_file is not None:
            log_file.close()
        if sender is not None:
            sender.close()
        if not args.headless:
            cv2.destroyAllWindows()
    return exit_code


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="tag36h11 pose estimation (pupil-apriltags)")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--camera", help="camera index (0) or device path (/dev/video0)")
    source.add_argument("--input", help="video file, image file or image directory")
    parser.add_argument("--calibration", required=True, help="JSON written by src.calibrate")
    parser.add_argument("--tag-size", type=float, required=True,
                        help="measured outer edge of the black border, meters (e.g. 0.100)")
    parser.add_argument("--target-id", type=int, default=0)
    parser.add_argument("--serial", help="send CV1 frames to this port (e.g. /tmp/cv1_a)")
    parser.add_argument("--output-video", help="save annotated undistorted frames (mp4)")
    parser.add_argument("--log", help="per-frame CSV log")
    parser.add_argument("--headless", action="store_true", help="no display window")
    parser.add_argument("--max-frames", type=int, help="stop after N frames")
    parser.add_argument("--decimate", type=float, default=2.0,
                        help="pupil quad_decimate; use integers, 2.5 etc. detect nothing")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    try:
        return run(args)
    except (RuntimeError, ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
