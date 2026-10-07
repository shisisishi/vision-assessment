import math
import unittest

import numpy as np

from src.protocol import UINT32_MOD, SequenceCounter, checksum, format_frame, parse_frame

VALID_SAMPLE = "$CV1,42,12345,1,0,100.0,-50.0,800.0,0.000000,0.000000,0.000000*33"
INVALID_SAMPLE = "$CV1,43,12445,0,-1,0.0,0.0,0.0,0.000000,0.000000,0.000000*09"
INVALID_FIELDS = ["0", "-1", "0.0", "0.0", "0.0", "0.000000", "0.000000", "0.000000"]


def fields(line):
    return line[1:line.index("*")].split(",")


class ManualSampleTest(unittest.TestCase):
    def test_valid_sample(self):
        line = format_frame(42, 12345, 0, (0.100, -0.050, 0.800), (0.0, 0.0, 0.0))
        self.assertEqual(line, VALID_SAMPLE + "\r\n")

    def test_invalid_sample(self):
        self.assertEqual(format_frame(43, 12445), INVALID_SAMPLE + "\r\n")

    def test_checksums(self):
        self.assertEqual(checksum(VALID_SAMPLE[1:-3]), "33")
        self.assertEqual(checksum(INVALID_SAMPLE[1:-3]), "09")

    def test_real_crlf_bytes(self):
        data = format_frame(0, 0).encode("ascii")
        self.assertTrue(data.endswith(b"\r\n"))
        self.assertEqual(data.count(b"\r\n"), 1)
        self.assertNotIn(b"\\r", data)
        self.assertNotIn(b" ", data)


class InvalidPoseTest(unittest.TestCase):
    def assert_invalid(self, line):
        self.assertEqual(fields(line)[3:], INVALID_FIELDS)
        parse_frame(line)

    def test_nan_and_inf(self):
        for bad in (math.nan, math.inf, -math.inf):
            self.assert_invalid(format_frame(1, 2, 0, (0.1, bad, 0.8), (0, 0, 0)))
            self.assert_invalid(format_frame(1, 2, 0, (0.1, 0.0, 0.8), (0, bad, 0)))

    def test_not_in_front_of_camera(self):
        self.assert_invalid(format_frame(1, 2, 0, (0.1, 0.0, -0.8), (0, 0, 0)))
        self.assert_invalid(format_frame(1, 2, 0, (0.1, 0.0, 0.0), (0, 0, 0)))

    def test_missing_target(self):
        self.assert_invalid(format_frame(1, 2, None, (0.1, 0.0, 0.8), (0, 0, 0)))
        self.assert_invalid(format_frame(1, 2, 0, None, None))
        self.assert_invalid(format_frame(1, 2, 0, (0.1, 0.0), (0, 0, 0)))


class FormattingTest(unittest.TestCase):
    def test_fixed_point_without_scientific_notation(self):
        line = format_frame(7, 99, 3, (1e-7, -1e-9, 12.3456), (1e-9, -2e-8, 3.14159265))
        self.assertNotIn("e", ",".join(fields(line)[1:]).lower())
        self.assertEqual(fields(line)[3:], ["1", "3", "0.0", "0.0", "12345.6",
                                            "0.000000", "0.000000", "3.141593"])

    def test_numpy_column_vectors(self):
        line = format_frame(5, 10, 0, np.array([[0.1234], [-0.0456], [0.789]]),
                            np.array([[0.1], [-0.2], [0.3]]))
        self.assertEqual(fields(line)[5:], ["123.4", "-45.6", "789.0",
                                            "0.100000", "-0.200000", "0.300000"])

    def test_parse_roundtrip_and_bad_checksum(self):
        frame = parse_frame(VALID_SAMPLE + "\r\n")
        self.assertEqual((frame["seq"], frame["t_ms"], frame["valid"], frame["id"]),
                         (42, 12345, True, 0))
        self.assertEqual(frame["xyz_mm"], (100.0, -50.0, 800.0))
        with self.assertRaises(ValueError):
            parse_frame(VALID_SAMPLE[:-2] + "34")


class SequenceTest(unittest.TestCase):
    def test_counter_increments_from_zero(self):
        counter = SequenceCounter()
        self.assertEqual([counter.next() for _ in range(3)], [0, 1, 2])

    def test_uint32_rollover(self):
        counter = SequenceCounter(UINT32_MOD - 1)
        self.assertEqual(counter.next(), 4294967295)
        self.assertEqual(counter.next(), 0)
        self.assertEqual(fields(format_frame(UINT32_MOD, 0))[1], "0")
        self.assertEqual(fields(format_frame(UINT32_MOD + 5, 0))[1], "5")


if __name__ == "__main__":
    unittest.main()
