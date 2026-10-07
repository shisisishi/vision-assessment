"""任务一：蓝色装甲板灯条检测。

流程：视频读取 -> 颜色分割 -> 形态学 -> 轮廓提取 -> 几何筛选 -> 框选显示。
运行：python -m src.lightbars --input in.mp4 --output out.mp4 --compare-dir out/compare
"""
import argparse
import json
import math
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

DEFAULT_CONFIG = Path(__file__).resolve().parent.parent / "config" / "lightbars.json"
SEGMENT_METHODS = ("hsv", "b_minus_r", "both")
MORPH_OPS = {
    "open": cv2.MORPH_OPEN,
    "close": cv2.MORPH_CLOSE,
    "erode": cv2.MORPH_ERODE,
    "dilate": cv2.MORPH_DILATE,
}
KERNEL_SHAPES = {"rect": cv2.MORPH_RECT, "ellipse": cv2.MORPH_ELLIPSE, "cross": cv2.MORPH_CROSS}
BOX_COLOR = (0, 255, 0)


class InputError(RuntimeError):
    pass


class OutputError(RuntimeError):
    pass


@dataclass
class Lightbar:
    center: tuple        # (x, y)，像素
    length: float        # 旋转矩形长边
    width: float         # 旋转矩形短边
    tilt_deg: float      # 长边与竖直方向的夹角，0 表示竖直
    box: np.ndarray      # minAreaRect 四个角点，int32，形状 (4, 2)
    polygon: np.ndarray  # approxPolyDP 结果


@dataclass
class Detection:
    mask_raw: np.ndarray
    mask: np.ndarray
    contours: list
    lightbars: list = field(default_factory=list)


def load_config(path):
    with open(path, encoding="utf-8") as f:
        config = json.load(f)
    for key in ("segmentation", "morphology", "geometry", "video"):
        if key not in config:
            raise ValueError(f"配置缺少字段: {key}")
    method = config["segmentation"]["method"]
    if method not in SEGMENT_METHODS:
        raise ValueError(f"未知分割方法 {method}，可选: {SEGMENT_METHODS}")
    return config


def build_mask(frame, seg):
    masks = []
    if seg["method"] in ("hsv", "both"):
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        lower = np.array(seg["hsv_lower"], dtype=np.uint8)
        upper = np.array(seg["hsv_upper"], dtype=np.uint8)
        masks.append(cv2.inRange(hsv, lower, upper))
    if seg["method"] in ("b_minus_r", "both"):
        b, _, r = cv2.split(frame)
        _, diff_mask = cv2.threshold(cv2.subtract(b, r), seg["b_minus_r_threshold"], 255, cv2.THRESH_BINARY)
        masks.append(diff_mask)
    mask = masks[0]
    for other in masks[1:]:
        mask = cv2.bitwise_and(mask, other)
    return mask


def apply_morphology(mask, steps):
    for step in steps:
        w, h = step["kernel"]
        kernel = cv2.getStructuringElement(KERNEL_SHAPES[step.get("shape", "rect")], (int(w), int(h)))
        mask = cv2.morphologyEx(mask, MORPH_OPS[step["op"]], kernel, iterations=step.get("iterations", 1))
    return mask


def approx_polygon(contour, epsilon_ratio):
    return cv2.approxPolyDP(contour, epsilon_ratio * cv2.arcLength(contour, True), True)


def measure_contour(contour, geo):
    """返回 Lightbar；不符合灯条几何特征时返回 None。"""
    area = cv2.contourArea(contour)
    if area < geo["min_area"]:
        return None

    rect = cv2.minAreaRect(contour)
    box = cv2.boxPoints(rect)
    edge_a = box[1] - box[0]
    edge_b = box[2] - box[1]
    long_edge = edge_a if np.linalg.norm(edge_a) >= np.linalg.norm(edge_b) else edge_b
    length = float(max(np.linalg.norm(edge_a), np.linalg.norm(edge_b)))
    width = float(max(min(np.linalg.norm(edge_a), np.linalg.norm(edge_b)), 1.0))
    # 直接由长边向量计算倾角，避免 minAreaRect 角度约定在不同 OpenCV 版本间的差异
    tilt = math.degrees(math.atan2(abs(long_edge[0]), abs(long_edge[1])))
    polygon = approx_polygon(contour, geo["approx_epsilon_ratio"])

    aspect = length / width
    fill = area / (length * width)
    if not geo["min_aspect_ratio"] <= aspect <= geo["max_aspect_ratio"]:
        return None
    if tilt > geo["max_tilt_deg"] or fill < geo["min_fill_ratio"]:
        return None
    if len(polygon) > geo["max_polygon_vertices"]:
        return None
    return Lightbar(rect[0], length, width, tilt, np.round(box).astype(np.int32), polygon)


def find_lightbars(mask, geo):
    contours = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)[-2]
    bars = [bar for bar in (measure_contour(c, geo) for c in contours) if bar is not None]
    bars.sort(key=lambda bar: bar.center[0])
    return bars, list(contours)


def detect(frame, config):
    mask_raw = build_mask(frame, config["segmentation"])
    mask = apply_morphology(mask_raw, config["morphology"]["final"])
    bars, contours = find_lightbars(mask, config["geometry"])
    return Detection(mask_raw, mask, contours, bars)


def put_label(image, text):
    scale = max(0.5, image.shape[0] / 720 * 0.8)
    origin = (10, int(30 * scale / 0.8))
    cv2.putText(image, text, origin, cv2.FONT_HERSHEY_SIMPLEX, scale, (0, 0, 0), 4, cv2.LINE_AA)
    cv2.putText(image, text, origin, cv2.FONT_HERSHEY_SIMPLEX, scale, (255, 255, 255), 2, cv2.LINE_AA)


def annotate(frame, lightbars, frame_no, elapsed_ms):
    out = frame.copy()
    for bar in lightbars:
        cv2.polylines(out, [bar.box], True, BOX_COLOR, 2, cv2.LINE_AA)
    put_label(out, f"frame {frame_no}  lightbars {len(lightbars)}  {elapsed_ms:.1f} ms")
    return out


def export_comparison(frame, config, out_dir, frame_no):
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    geo = config["geometry"]

    start = time.perf_counter()
    det = detect(frame, config)
    elapsed_ms = (time.perf_counter() - start) * 1000

    b, g, r = cv2.split(frame)
    images = {
        "01_original": frame,
        "02_channel_b": b,
        "03_channel_g": g,
        "04_channel_r": r,
        "05_gray": cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY),
        "06_mask_raw": det.mask_raw,
    }
    summary = {"frame": frame_no, "segmentation": config["segmentation"], "variants": []}
    variants = [("raw", [])] + [(v["name"], v["steps"]) for v in config["morphology"]["compare"]]
    variants.append(("final", config["morphology"]["final"]))
    for name, steps in variants:
        mask = apply_morphology(det.mask_raw, steps)
        bars, contours = find_lightbars(mask, geo)
        if name not in ("raw", "final"):
            images[f"07_mask_{name}"] = mask
        summary["variants"].append({
            "name": name,
            "steps": steps,
            "white_pixels": int(cv2.countNonZero(mask)),
            "contours": len(contours),
            "lightbars": len(bars),
        })
    images["08_mask_final"] = det.mask

    contour_img = frame.copy()
    cv2.drawContours(contour_img, det.contours, -1, (0, 255, 255), 1)
    polygon_img = frame.copy()
    for contour in det.contours:
        cv2.polylines(polygon_img, [approx_polygon(contour, geo["approx_epsilon_ratio"])], True, (255, 0, 255), 1)
    for bar in det.lightbars:
        cv2.polylines(polygon_img, [bar.polygon], True, BOX_COLOR, 2)
    images["09_contours"] = contour_img
    images["10_polygons"] = polygon_img
    images["11_final"] = annotate(frame, det.lightbars, frame_no, elapsed_ms)

    for name, image in images.items():
        if not cv2.imwrite(str(out_dir / f"{name}.png"), image):
            raise OutputError(f"无法写入对比图: {out_dir / name}.png")
    with open(out_dir / "compare_summary.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    return summary


def process_video(input_path, output_path, config, compare_dir=None, representative_frame=1, log=print):
    input_path, output_path = Path(input_path), Path(output_path)
    if not input_path.is_file():
        raise InputError(f"输入文件不存在: {input_path}")
    cap = cv2.VideoCapture(str(input_path))
    if not cap.isOpened():
        raise InputError(f"无法打开视频: {input_path}")

    try:
        ok, frame = cap.read()
        if not ok or frame is None:
            raise InputError(f"无法读取第一帧（视频为空或无法解码）: {input_path}")
        fps = cap.get(cv2.CAP_PROP_FPS)
        if not fps or math.isnan(fps) or fps <= 0:
            fps = float(config["video"]["fallback_fps"])
            log(f"警告: 无法获取源视频 FPS，使用 fallback_fps={fps}")
        height, width = frame.shape[:2]
        reported_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

        output_path.parent.mkdir(parents=True, exist_ok=True)
        fourcc = cv2.VideoWriter_fourcc(*config["video"]["fourcc"])
        writer = cv2.VideoWriter(str(output_path), fourcc, fps, (width, height))
        if not writer.isOpened():
            raise OutputError(f"无法创建输出视频（检查路径、扩展名与 fourcc）: {output_path}")

        stats = {"frames": 0, "fps": fps, "size": (width, height), "zero_frames": 0, "total_ms": 0.0}
        try:
            while ok and frame is not None:
                stats["frames"] += 1
                frame_no = stats["frames"]
                if frame.shape[:2] != (height, width):
                    log(f"警告: 第 {frame_no} 帧尺寸与首帧不同，已缩放到 {width}x{height}")
                    frame = cv2.resize(frame, (width, height))

                start = time.perf_counter()
                det = detect(frame, config)
                elapsed_ms = (time.perf_counter() - start) * 1000
                writer.write(annotate(frame, det.lightbars, frame_no, elapsed_ms))

                stats["total_ms"] += elapsed_ms
                if not det.lightbars:
                    stats["zero_frames"] += 1
                if compare_dir and frame_no == representative_frame:
                    export_comparison(frame, config, compare_dir, frame_no)
                    log(f"已导出第 {frame_no} 帧对比图到 {compare_dir}")
                if frame_no % 100 == 0:
                    log(f"已处理 {frame_no} 帧")
                ok, frame = cap.read()
        finally:
            writer.release()
    finally:
        cap.release()

    if not output_path.is_file() or output_path.stat().st_size == 0:
        raise OutputError(f"输出视频为空或未生成: {output_path}")
    if compare_dir and representative_frame > stats["frames"]:
        log(f"警告: 视频只有 {stats['frames']} 帧，未导出第 {representative_frame} 帧对比图")
    if reported_frames > 0 and reported_frames != stats["frames"]:
        log(f"提示: 容器记录 {reported_frames} 帧，实际读取 {stats['frames']} 帧")
    return stats


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="backslashreplace")
    parser = argparse.ArgumentParser(description="任务一：蓝色装甲板灯条检测")
    parser.add_argument("--input", required=True, help="输入视频路径")
    parser.add_argument("--output", required=True, help="输出标记视频路径")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG), help="参数配置 JSON")
    parser.add_argument("--compare-dir", help="对比图输出目录，不填则不导出")
    parser.add_argument("--representative-frame", type=int, default=1, help="导出对比图的帧号，从 1 开始")
    args = parser.parse_args(argv)
    if args.representative_frame < 1:
        parser.error("--representative-frame 必须 >= 1")

    try:
        config = load_config(args.config)
    except (OSError, ValueError, KeyError) as e:
        print(f"配置错误: {e}", file=sys.stderr)
        return 1
    try:
        stats = process_video(args.input, args.output, config, args.compare_dir, args.representative_frame)
    except InputError as e:
        print(f"输入错误: {e}", file=sys.stderr)
        return 2
    except OutputError as e:
        print(f"输出错误: {e}", file=sys.stderr)
        return 3

    avg_ms = stats["total_ms"] / stats["frames"]
    width, height = stats["size"]
    print(f"完成: {stats['frames']} 帧, {width}x{height}, {stats['fps']:.3f} FPS, "
          f"平均检测 {avg_ms:.2f} ms/帧, 无灯条帧 {stats['zero_frames']}, 输出 {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
