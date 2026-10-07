import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

import cv2
import numpy as np

from src.print_targets import tag_cells
from src.tagpose import (check_resolution, corner_reprojection_error, load_calibration,
                         make_detection, project_points, select_target, tag_object_corners,
                         validate_pose)

K = np.array([[800.0, 0.0, 640.0], [0.0, 800.0, 360.0], [0.0, 0.0, 1.0]])
TAG_SIZE = 0.1
HAS_PUPIL = importlib.util.find_spec("pupil_apriltags") is not None
OFFICIAL_ID0_BLACK = np.array([
    [1, 1, 1, 1, 1, 1, 1, 1],
    [1, 0, 0, 1, 0, 1, 0, 1],
    [1, 1, 0, 0, 0, 1, 0, 1],
    [1, 1, 0, 0, 1, 1, 1, 1],
    [1, 0, 1, 0, 1, 1, 1, 1],
    [1, 1, 0, 1, 0, 0, 1, 1],
    [1, 1, 1, 1, 0, 1, 1, 1],
    [1, 1, 1, 1, 1, 1, 1, 1],
], dtype=bool)


class PrintedTagTest(unittest.TestCase):
    def test_id0_matches_official_apriltag_image(self):
        np.testing.assert_array_equal(tag_cells(0), OFFICIAL_ID0_BLACK)


def detection(tag_id, R=np.eye(3), t=(0.1, 0.0, 0.8), margin=50.0):
    corners = project_points(tag_object_corners(TAG_SIZE), R, t, K) if R is not None else np.zeros((4, 2))
    center = corners.mean(axis=0)
    return make_detection(tag_id, center, corners, R, t, decision_margin=margin)


class ValidatePoseTest(unittest.TestCase):
    def test_valid_pose(self):
        self.assertIsNone(validate_pose(np.eye(3), [0.0, 0.0, 0.5]))

    def test_rejections(self):
        reflection = np.diag([1.0, 1.0, -1.0])
        cases = [
            (None, [0, 0, 1]),
            (np.eye(3), None),
            (reflection, [0, 0, 1]),
            (np.eye(3) * 1.1, [0, 0, 1]),
            (np.eye(3), [0, 0, 0]),
            (np.eye(3), [0, 0, -1]),
            (np.eye(3), [0, np.nan, 1]),
            (np.full((3, 3), np.inf), [0, 0, 1]),
            (np.eye(2), [0, 0, 1]),
        ]
        for R, t in cases:
            self.assertIsNotNone(validate_pose(R, t), (R, t))

    def test_invalid_detection_has_no_pose(self):
        det = make_detection(0, (0, 0), np.zeros((4, 2)), np.eye(3), (0, 0, -1))
        self.assertFalse(det.valid)
        self.assertIsNone(det.t)


class SelectTargetTest(unittest.TestCase):
    def test_missing(self):
        target, status = select_target([detection(3)], 0)
        self.assertIsNone(target)
        self.assertEqual(status, "target_missing")
        self.assertEqual(select_target([], 0), (None, "target_missing"))

    def test_keeps_all_and_picks_target(self):
        detections = [detection(5), detection(0, margin=10), detection(0, margin=80)]
        target, status = select_target(detections, 0)
        self.assertEqual(status, "valid")
        self.assertIs(target, detections[2])
        self.assertEqual([d.tag_id for d in detections], [5, 0, 0])

    def test_pose_invalid(self):
        bad = make_detection(0, (0, 0), np.zeros((4, 2)), None, None)
        target, status = select_target([detection(1), bad], 0)
        self.assertIsNone(target)
        self.assertTrue(status.startswith("pose_invalid"))


class GeometryTest(unittest.TestCase):
    def test_distance_differs_from_depth(self):
        det = detection(0, t=(0.10, 0.00, 0.80))
        self.assertAlmostEqual(det.distance, 0.806226, places=5)
        self.assertAlmostEqual(det.t[2], 0.80)

    def test_rvec_matches_rotation(self):
        R = cv2.Rodrigues(np.array([0.1, -0.2, 0.3]))[0]
        np.testing.assert_allclose(detection(0, R=R).rvec, [0.1, -0.2, 0.3], atol=1e-9)

    def test_axes_projection_uses_pose(self):
        t = np.array([0.1, -0.05, 0.8])
        origin, x_end, y_end, _ = project_points(
            [[0, 0, 0], [0.05, 0, 0], [0, 0.05, 0], [0, 0, 0.05]], np.eye(3), t, K)
        np.testing.assert_allclose(origin, [640 + 800 * 0.1 / 0.8, 360 - 800 * 0.05 / 0.8])
        self.assertGreater(x_end[0], origin[0])
        self.assertGreater(y_end[1], origin[1])

    def test_corner_order_and_reprojection(self):
        det = detection(0, t=(0.0, 0.0, 0.8))
        bottom_left, bottom_right, top_right, top_left = det.corners
        self.assertLess(bottom_left[0], bottom_right[0])
        self.assertGreater(bottom_left[1], top_left[1])
        self.assertLess(top_right[1], bottom_right[1])
        self.assertLess(corner_reprojection_error(det, K, TAG_SIZE), 1e-6)


class CalibrationFileTest(unittest.TestCase):
    def write(self, data):
        handle = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
        with handle:
            json.dump(data, handle)
        self.addCleanup(Path(handle.name).unlink)
        return handle.name

    def test_load_and_resolution(self):
        path = self.write({"camera_matrix": K.tolist(), "dist_coeffs": [0.1, -0.2, 0, 0, 0.05],
                           "image_size": [1280, 720]})
        calib = load_calibration(path)
        self.assertEqual(calib.image_size, (1280, 720))
        self.assertIsNone(check_resolution(np.zeros((720, 1280, 3), np.uint8), calib.image_size))
        self.assertIsNotNone(check_resolution(np.zeros((480, 640, 3), np.uint8), calib.image_size))
        self.assertIsNotNone(check_resolution(np.zeros((1280, 720, 3), np.uint8), calib.image_size))

    def test_rejects_bad_files(self):
        for data in ({"camera_matrix": K.tolist(), "image_size": [1280, 720]},
                     {"camera_matrix": [[0, 0, 0]] * 3, "dist_coeffs": [0] * 5, "image_size": [1, 1]},
                     {"camera_matrix": K.tolist(), "dist_coeffs": [0, 0], "image_size": [1280, 720]}):
            with self.assertRaises(ValueError):
                load_calibration(self.write(data))


@unittest.skipUnless(HAS_PUPIL, "pupil-apriltags not installed")
class PupilSyntheticTest(unittest.TestCase):
    def test_recovers_known_pose(self):
        from pupil_apriltags import Detector

        cell_px = 40
        marker = np.full((10 * cell_px, 10 * cell_px), 255, np.uint8)
        big = cv2.resize(tag_cells(0).astype(np.uint8), (8 * cell_px, 8 * cell_px),
                         interpolation=cv2.INTER_NEAREST)
        marker[cell_px:9 * cell_px, cell_px:9 * cell_px][big > 0] = 0
        src = np.float32([[cell_px, cell_px], [9 * cell_px, cell_px],
                          [9 * cell_px, 9 * cell_px], [cell_px, 9 * cell_px]])
        h = TAG_SIZE / 2
        R = cv2.Rodrigues(np.array([0.2, -0.3, 0.1]))[0]
        t = np.array([0.05, -0.02, 0.6])
        dst = project_points([[-h, -h, 0], [h, -h, 0], [h, h, 0], [-h, h, 0]], R, t, K)
        H = cv2.getPerspectiveTransform(src - 0.5, np.float32(dst))
        image = cv2.warpPerspective(marker, H, (1280, 720), flags=cv2.INTER_LINEAR,
                                    borderValue=255)
        results = Detector(families="tag36h11").detect(
            image, estimate_tag_pose=True, camera_params=(800.0, 800.0, 640.0, 360.0),
            tag_size=TAG_SIZE)
        self.assertEqual([r.tag_id for r in results], [0])
        det = make_detection(0, results[0].center, results[0].corners, results[0].pose_R,
                             results[0].pose_t)
        self.assertTrue(det.valid)
        np.testing.assert_allclose(det.t, t, atol=0.01)
        angle = np.degrees(np.linalg.norm(cv2.Rodrigues(det.R.T @ R)[0]))
        self.assertLess(angle, 3.0)
        self.assertLess(corner_reprojection_error(det, K, TAG_SIZE), 2.0)


if __name__ == "__main__":
    unittest.main()
