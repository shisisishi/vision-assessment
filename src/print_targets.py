"""Generate printable SVGs with physical millimetre sizes.

- tag36h11 ID 0 from OpenCV's DICT_APRILTAG_36h11; --tag-size-mm is the outer edge of the
  black border (8 of the 10 cells; the outer white cell row is the quiet zone), which is
  the tag_size used by the pose estimator.
- 10x7-square chessboard (9x6 inner corners).
Print at 100% / "actual size", then measure the printed result with a ruler; the measured
value, not the nominal one, goes to --tag-size / --square.
"""

import argparse
import sys
from pathlib import Path

import cv2
import numpy as np


def tag_cells(tag_id):
    """8x8 bool grid (True = black) for the tag including its black border."""
    dictionary = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_APRILTAG_36h11)
    marker = cv2.aruco.generateImageMarker(dictionary, tag_id, 8, borderBits=1)
    # OpenCV's pattern is the AprilRobotics/apriltag-imgs image rotated by 180 degrees.
    return np.rot90(marker < 128, 2)


def _num(v):
    return f"{v:.3f}".rstrip("0").rstrip(".")


def svg_document(width, height, body):
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{_num(width)}mm" '
            f'height="{_num(height)}mm" viewBox="0 0 {_num(width)} {_num(height)}">\n'
            f'<rect width="{_num(width)}" height="{_num(height)}" fill="white"/>\n'
            f'{"".join(body)}</svg>\n')


def rect(x, y, w, h):
    return (f'<rect x="{_num(x)}" y="{_num(y)}" width="{_num(w)}" height="{_num(h)}" '
            'fill="black" shape-rendering="crispEdges"/>\n')


def text(x, y, content, size=3.0):
    return (f'<text x="{_num(x)}" y="{_num(y)}" font-family="sans-serif" '
            f'font-size="{_num(size)}">{content}</text>\n')


def ruler(x, y, length_mm):
    parts = [f'<line x1="{_num(x)}" y1="{_num(y)}" x2="{_num(x + length_mm)}" y2="{_num(y)}" '
             'stroke="black" stroke-width="0.2"/>\n']
    for mm in range(int(length_mm) + 1):
        tick = 5.0 if mm % 10 == 0 else 3.5 if mm % 5 == 0 else 2.0
        parts.append(f'<line x1="{_num(x + mm)}" y1="{_num(y)}" x2="{_num(x + mm)}" '
                     f'y2="{_num(y + tick)}" stroke="black" stroke-width="0.15"/>\n')
        if mm % 10 == 0:
            parts.append(text(x + mm - 1.0, y + 8.5, str(mm), 2.5))
    return parts


def tag_svg(tag_id, tag_mm, margin_mm):
    cells = tag_cells(tag_id)
    cell = tag_mm / cells.shape[0]
    margin = max(margin_mm, cell)
    width = tag_mm + 2 * margin
    ruler_len = min(100, int(width - 10))
    height = tag_mm + 2 * margin + 22
    body = [rect(margin + c * cell, margin + r * cell, cell, cell)
            for r, c in zip(*np.nonzero(cells))]
    y = tag_mm + 2 * margin
    body.append(text(5, y + 3, f"tag36h11 ID {tag_id}: black border outer edge = {_num(tag_mm)} mm "
                               f"(cell {_num(cell)} mm). Print at 100%, then measure.", 2.6))
    body += ruler(5, y + 7, ruler_len)
    body.append(text(5 + ruler_len + 2, y + 10, "mm", 2.5))
    return svg_document(width, height, body)


def chessboard_svg(cols, rows, square_mm, margin_mm):
    width = cols * square_mm + 2 * margin_mm
    ruler_len = min(100, int(width - 10))
    height = rows * square_mm + 2 * margin_mm + 22
    body = [rect(margin_mm + c * square_mm, margin_mm + r * square_mm, square_mm, square_mm)
            for r in range(rows) for c in range(cols) if (r + c) % 2 == 0]
    y = rows * square_mm + 2 * margin_mm
    body.append(text(5, y + 3, f"chessboard {cols}x{rows} squares = {cols - 1}x{rows - 1} inner "
                               f"corners, square = {_num(square_mm)} mm. Print at 100%, then measure.",
                     2.6))
    body += ruler(5, y + 7, ruler_len)
    body.append(text(5 + ruler_len + 2, y + 10, "mm", 2.5))
    return svg_document(width, height, body)


def verify_tag(tag_id, px_per_cell=20):
    """Rasterise the generated cells with a one-cell white border and decode them."""
    from pupil_apriltags import Detector

    cells = tag_cells(tag_id)
    image = np.full((10, 10), 255, np.uint8)
    image[1:9, 1:9][cells] = 0
    image = cv2.resize(image, None, fx=px_per_cell, fy=px_per_cell, interpolation=cv2.INTER_NEAREST)
    return [d.tag_id for d in Detector(families="tag36h11").detect(image)]


def main(argv=None):
    parser = argparse.ArgumentParser(description="generate printable AprilTag and chessboard SVGs")
    parser.add_argument("--out-dir", default="prints")
    parser.add_argument("--tag-id", type=int, default=0)
    parser.add_argument("--tag-size-mm", type=float, default=100.0,
                        help="outer edge of the black border")
    parser.add_argument("--tag-margin-mm", type=float, default=20.0,
                        help="white margin around the tag (at least one cell is enforced)")
    parser.add_argument("--square-mm", type=float, default=20.0)
    parser.add_argument("--board-margin-mm", type=float, default=15.0)
    parser.add_argument("--verify", action="store_true",
                        help="decode the generated tag with pupil-apriltags")
    args = parser.parse_args(argv)

    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    tag_path = out / f"tag36h11_id{args.tag_id}_{_num(args.tag_size_mm)}mm.svg"
    board_path = out / f"chessboard_10x7_{_num(args.square_mm)}mm.svg"
    tag_path.write_text(tag_svg(args.tag_id, args.tag_size_mm, args.tag_margin_mm), encoding="utf-8")
    board_path.write_text(chessboard_svg(10, 7, args.square_mm, args.board_margin_mm), encoding="utf-8")
    print(f"wrote {tag_path}\nwrote {board_path}")
    print("files only: print at 100% (no fit-to-page), then measure the black border / squares")
    if args.verify:
        try:
            ids = verify_tag(args.tag_id)
        except ImportError:
            print("error: --verify needs pupil-apriltags", file=sys.stderr)
            return 1
        print(f"pupil-apriltags decoded ids: {ids}")
        return 0 if ids == [args.tag_id] else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
