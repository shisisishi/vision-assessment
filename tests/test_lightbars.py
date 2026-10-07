"""任务一合成图测试：python -m unittest tests.test_lightbars -v"""
import copy
import json
import tempfile
import unittest
from pathlib import Path

import cv2
import numpy as np

from src import lightbars

BLUE = (255, 120, 0)      # BGR，OpenCV HSV 中 H≈106
RED = (0, 0, 255)
ORANGE = (0, 140, 255)


def blank(height=480, width=640):
    return np.full((height, width, 3), 30, dtype=np.uint8)


def draw_bar(image, center, size=(10, 60), angle=0.0, color=BLUE):
    box = cv2.boxPoints((center, size, angle))
    cv2.fillPoly(image, [np.round(box).astype(np.int32)], color)


class DetectTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = lightbars.load_config(lightbars.DEFAULT_CONFIG)

    def detect(self, image):
        return lightbars.detect(image, self.config).lightbars

    def test_upright_blue_bar_accepted(self):
        image = blank()
        draw_bar(image, (200, 240))
        bars = self.detect(image)
        self.assertEqual(len(bars), 1)
        self.assertAlmostEqual(bars[0].center[0], 200, delta=2)
        self.assertAlmostEqual(bars[0].tilt_deg, 0, delta=3)

    def test_tilted_blue_bar_accepted(self):
        image = blank()
        draw_bar(image, (320, 240), angle=25)
        bars = self.detect(image)
        self.assertEqual(len(bars), 1)
        self.assertAlmostEqual(bars[0].tilt_deg, 25, delta=3)

    def test_each_bar_detected_individually(self):
        image = blank()
        for x, angle in ((100, 0), (180, 0), (400, -15), (480, -15)):
            draw_bar(image, (x, 240), angle=angle)
        self.assertEqual(len(self.detect(image)), 4)

    def test_red_and_orange_bars_rejected(self):
        image = blank()
        draw_bar(image, (200, 240), color=RED)
        draw_bar(image, (400, 240), color=ORANGE)
        self.assertEqual(self.detect(image), [])

    def test_red_rejected_by_b_minus_r(self):
        config = copy.deepcopy(self.config)
        config["segmentation"]["method"] = "b_minus_r"
        image = blank()
        draw_bar(image, (200, 240), color=RED)
        draw_bar(image, (400, 240))
        self.assertEqual(len(lightbars.detect(image, config).lightbars), 1)

    def test_blue_non_bar_shapes_rejected(self):
        image = blank()
        draw_bar(image, (150, 240), size=(60, 60))
        draw_bar(image, (400, 240), size=(80, 12))
        self.assertEqual(self.detect(image), [])

    def test_no_lightbar_frame(self):
        image = blank()
        bars = self.detect(image)
        self.assertEqual(bars, [])
        out = lightbars.annotate(image, bars, 1, 0.5)
        self.assertEqual(out.shape, image.shape)

    def test_detect_and_annotate_do_not_mutate_source(self):
        image = blank()
        draw_bar(image, (300, 240), angle=10)
        original = image.copy()
        bars = self.detect(image)
        out = lightbars.annotate(image, bars, 7, 1.2)
        self.assertTrue(np.array_equal(image, original))
        self.assertFalse(np.array_equal(out, original))


class VideoTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.config_path = self.dir / "config.json"
        config = lightbars.load_config(lightbars.DEFAULT_CONFIG)
        config["video"]["fourcc"] = "MJPG"
        self.config_path.write_text(json.dumps(config), encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def make_video(self, frames=5, fps=24.0, size=(320, 240)):
        path = self.dir / "input.avi"
        writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), fps, size)
        self.assertTrue(writer.isOpened())
        for i in range(frames):
            image = blank(size[1], size[0])
            for k in range(i % 3):
                draw_bar(image, (80 + 80 * k + 5 * i, 120), size=(8, 50), angle=10 * k)
            writer.write(image)
        writer.release()
        return path

    def test_every_frame_written_with_same_size_and_fps(self):
        source = self.make_video()
        output = self.dir / "out" / "result.avi"
        compare = self.dir / "compare"
        code = lightbars.main(["--input", str(source), "--output", str(output), "--config", str(self.config_path),
                               "--compare-dir", str(compare), "--representative-frame", "3"])
        self.assertEqual(code, 0)

        cap = cv2.VideoCapture(str(output))
        count = 0
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            count += 1
            self.assertEqual(frame.shape[:2], (240, 320))
        fps = cap.get(cv2.CAP_PROP_FPS)
        cap.release()
        self.assertEqual(count, 5)
        self.assertAlmostEqual(fps, 24.0, delta=0.01)

        for name in ("01_original", "02_channel_b", "03_channel_g", "04_channel_r", "05_gray", "06_mask_raw",
                     "07_mask_open_3x3", "07_mask_close_3x3", "08_mask_final", "09_contours", "10_polygons",
                     "11_final"):
            self.assertTrue((compare / f"{name}.png").is_file(), name)
        summary = json.loads((compare / "compare_summary.json").read_text(encoding="utf-8"))
        self.assertEqual(summary["frame"], 3)

    def test_missing_input_returns_error(self):
        code = lightbars.main(["--input", str(self.dir / "missing.mp4"), "--output", str(self.dir / "o.avi"),
                               "--config", str(self.config_path)])
        self.assertEqual(code, 2)

    def test_non_video_input_returns_error(self):
        bad = self.dir / "bad.mp4"
        bad.write_text("not a video", encoding="utf-8")
        code = lightbars.main(["--input", str(bad), "--output", str(self.dir / "o.avi"),
                               "--config", str(self.config_path)])
        self.assertEqual(code, 2)

    def test_writer_failure_returns_error(self):
        source = self.make_video(frames=2)
        code = lightbars.main(["--input", str(source), "--output", str(self.dir / "result.unknownext"),
                               "--config", str(self.config_path)])
        self.assertEqual(code, 3)


if __name__ == "__main__":
    unittest.main()
