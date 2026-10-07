# 任务一：蓝色装甲板灯条识别

相关文件：`src/lightbars.py`（程序）、`config/lightbars.json`（参数）、`tests/test_lightbars.py`（合成图测试）。

> 状态说明：已用手册提供的考核视频 `test_video2..mov`（1137 帧，748×480，24.99 FPS）跑通整段。标记视频在 `results/lightbars/lightbars.mp4`，代表帧对比图在 `results/lightbars/frame_XXXX/`，逐帧统计在 `results/lightbars/verification.json`。下面的参数和对比数字来自这次运行，不是未验证的初始值。检测数量是程序输出，没有人工逐帧标注，不能当成准确率。

## 运行

在仓库根目录执行（依赖：Python 3、OpenCV 4.x 的 `opencv-python`、NumPy）：

```bash
python -m src.lightbars --input data/armor.mp4 --output output/lightbars.mp4 \
    --compare-dir output/compare --representative-frame 120
python -m unittest tests.test_lightbars -v
```

| 参数 | 含义 |
| --- | --- |
| `--input` | 输入视频（必填） |
| `--output` | 输出标记视频（必填），自动创建上级目录 |
| `--config` | 参数文件，默认 `config/lightbars.json` |
| `--compare-dir` | 对比图输出目录；不填则不导出 |
| `--representative-frame` | 导出对比图的帧号，从 1 开始，默认 1 |

退出码：`0` 成功；`1` 配置错误；`2` 输入错误（路径不存在、打不开、读不到第一帧）；`3` 输出错误（写视频或写图片失败）。

## 处理流程

```text
VideoCapture 逐帧读取 → 颜色分割(HSV) → 形态学(闭运算) → findContours
→ approxPolyDP + minAreaRect 几何筛选 → 在原图副本上逐根画旋转矩形 → VideoWriter 按原顺序写出
```

- **逐帧输出**：每读到一帧就检测并写出一帧，没有跳帧或重排；输出分辨率取首帧尺寸，FPS 取源视频（读不到时用 `video.fallback_fps` 并打印警告）。
- **左上角标注**：`frame 帧号  lightbars 灯条数量  耗时 ms`。帧号从 1 开始；耗时只统计检测部分（分割到筛选），不含解码和写视频。
- **边界情况**：路径错误、读不到第一帧会给出提示并返回非 0；读到视频末尾正常结束；没有灯条的帧照常写出，数量显示 0；写视频失败或输出文件为空时报错；中途尺寸变化的帧会缩放到首帧尺寸并警告。
- **不依赖固定位置**：所有判断只用颜色和轮廓自身的几何量（面积、长宽比、倾角、填充率、顶点数），代码里没有画面坐标或帧号相关的条件。

## 通道、灰度与颜色分割

对比图里的 `01_original`、`02_channel_b`、`03_channel_g`、`04_channel_r`、`05_gray` 用于观察：

- OpenCV 读入的是 **BGR** 顺序。蓝色灯条在 B 通道亮、R 通道暗；橙/红场地灯条正好相反，R 亮、B 暗。
- 灰度图只表示明暗（约 0.299R + 0.587G + 0.114B），蓝灯条和橙灯条都很亮，**灰度阈值区分不了颜色**，所以不单独用灰度分割。

**选用 HSV（`method: "hsv"`）**，理由：H 直接表示颜色类别，与亮度变化相对分离。OpenCV 8 位 HSV 中 H 范围 0～179（不是 0～360），蓝色大约在 100～125，红色在 0～10 和 170～179，橙色约 10～25。筛选依据：

| 参数 | 采用值 | 作用 |
| --- | --- | --- |
| `hsv_lower` / `hsv_upper` 的 H | 95～130 | 只保留蓝色，排除红/橙场地灯 |
| S 下限 | 80 | 排除灰白色区域 |
| V 下限 | 150 | 灯条是发光体，排除暗处蓝色（如装甲板本体、阴影） |

在第 1、250、400、600、800、1000、1137 帧上，橙色场地灯和红色指示灯没有进入最终框；蓝色灯条在 B 通道亮、在 R 通道暗，灰度图里两者都亮，所以不用灰度分割。过暗、几乎不发光的侧面灯条不会进掩膜，这是 V 下限的取舍，不是形态学能补回来的。

也可改为 `"b_minus_r"`（`B - R > b_minus_r_threshold`，即手册中的通道差分）或 `"both"`（两者取交集）进行对比。

## 形态学对比

`--compare-dir` 会对同一帧导出原始掩膜 `06_mask_raw`、配置里 `morphology.compare` 的每一组 `07_mask_*`、最终掩膜 `08_mask_final`，并在 `compare_summary.json` 里记录每组的白色像素数、轮廓数和通过筛选的灯条数，便于定量比较。

默认对比三组：

| 名称 | 操作 | 在考核视频上的作用 | 损伤 |
| --- | --- | --- | --- |
| `open_3x3` | 开运算 3×3 | 白像素明显减少 | 细灯条被吃掉。第 250 帧 2→1，第 400 帧 3→1，第 600 帧 4→2，第 1000 帧 2→1 |
| `close_3x3` | 闭运算 3×3 | 白像素只比原始掩膜多几个，小孔被填上 | 抽查帧的灯条数与原始掩膜相同，没有把两根灯条粘在一起 |
| `close_7x7` | 闭运算 7×7 | 与 3×3 闭运算几乎一样 | 这几帧上灯条间距大于核，没有额外收益，也没有粘连 |

考核视频对比（`verification.json`，灯条数是几何筛选之后的数量）：

| 帧号 | 组别 | 白色像素 | 轮廓数 | 灯条数 |
| --- | ---: | ---: | ---: | ---: |
| 1 | raw / open / close3 / close7 / final | 286 / 186 / 289 / 289 / 430 | 6 / 5 / 6 / 6 / 5 | 3 / 3 / 3 / 3 / 4 |
| 100 | raw / open / close3 / close7 / final | 443 / 308 / 443 / 443 / 628 | 5 / 3 / 5 / 5 / 5 | 3 / 3 / 3 / 3 / 4 |
| 250 | raw / open / close3 / close7 / final | 169 / 75 / 172 / 172 / 287 | 8 / 2 / 8 / 8 / 8 | 2 / 1 / 2 / 2 / 3 |
| 400 | raw / open / close3 / close7 / final | 193 / 82 / 196 / 196 / 308 | 6 / 2 / 6 / 6 / 6 | 3 / 1 / 3 / 3 / 3 |
| 600 | raw / open / close3 / close7 / final | 372 / 245 / 377 / 381 / 534 | 5 / 2 / 5 / 5 / 5 | 4 / 2 / 4 / 4 / 4 |
| 800 | raw / open / close3 / close7 / final | 489 / 436 / 492 / 492 / 654 | 5 / 5 / 5 / 5 / 5 | 3 / 4 / 3 / 3 / 3 |
| 1000 | raw / open / close3 / close7 / final | 133 / 41 / 134 / 134 / 223 | 5 / 1 / 5 / 5 / 5 | 2 / 1 / 2 / 2 / 3 |
| 1137 | raw / open / close3 / close7 / final | 1196 / 1091 / 1199 / 1199 / 1493 | 7 / 5 / 7 / 7 / 5 | 4 / 4 / 4 / 4 / 4 |

**最终配置是 `close_3x3` 再加一次 2×2 膨胀。** 闭运算负责补小孔，膨胀把偏细的灯条长到能通过面积和长宽比。第 1、100 帧因此从 3 根补到 4 根，第 250、1000 帧从 2 根补到 3 根。开运算会伤灯条，7×7 闭运算没有额外好处，所以都不用。第 400 帧左侧一对太暗，膨胀也补不回来。第 800 帧开运算多出的 1 根没有保留：膨胀后的最终结果仍是画面里三根清楚的灯条。

## 轮廓与几何筛选

`09_contours` 画出全部外轮廓（黄色），`10_polygons` 画出每个轮廓的 `approxPolyDP` 多边形（品红），通过筛选的用绿色加粗。最终框选使用 `minAreaRect` 旋转矩形，倾斜灯条也能紧贴。

| 参数 | 采用值 | 依据 |
| --- | --- | --- |
| `min_area` | 10 | 去掉噪点，同时保留远处只有十几像素的灯条。最初是 20，细灯条会被丢掉 |
| `min_aspect_ratio` / `max_aspect_ratio` | 2.0 / 35.0 | 灯条细长。上限从 15 放到 35，避免只剩一条亮缝的灯条被当成过细的线 |
| `max_tilt_deg` | 40 | 长边与竖直方向夹角；上坡时灯条有倾斜，横向橙色色条被排除 |
| `min_fill_ratio` | 0.4 | 轮廓面积 / 旋转矩形面积。从 0.5 放到 0.4，允许灯条中间有一点缺口 |
| `max_polygon_vertices` | 8 | 多边形近似后顶点过多说明形状不规则 |
| `approx_epsilon_ratio` | 0.02 | 近似误差 = 0.02 × 周长 |

整段 1137 帧的检测数量：4 根 779 帧，3 根 290 帧，2 根 59 帧，5 根 9 帧，0 根 0 帧。抽查 5 根的帧（158、824、1095）是正面装甲加侧面装甲，不是橙色场地灯。2 根的帧（如 616）是机器人大部分已经出画，只剩一块装甲。输出视频解码后仍是 1137 帧、748×480、24.99 FPS。检测部分合计 1484 ms，大约每帧 1.3 ms；代表帧叠加文字里看到的是 0.7～1.2 ms。

倾角由旋转矩形长边向量直接计算，不使用 `minAreaRect` 返回的角度，因为该角度的取值约定在不同 OpenCV 版本间有差异。

## 核对视频时做了什么

1. 在清晰帧上看 `06_mask_raw`：蓝灯条为白，橙红场地灯为黑，因此保持现在的 HSV 范围。
2. 比较 `07_mask_*` 与上表，确定最终用闭运算加一次小膨胀，不用开运算。
3. 漏掉的细灯条放宽了面积、长宽比上限和填充率；仍然漏掉的是掩膜里就没有的暗灯条。
4. 又看了平移、视角变化和上坡附近的帧（250、400、600、800、1000、1137），并解码整段输出视频核对帧数、分辨率和 FPS。

## 测试

`tests/test_lightbars.py` 只用程序生成的合成图和合成视频，覆盖：竖直与倾斜 25° 的蓝色细长矩形被接受、多根灯条逐根检出、红/橙灯条被拒绝（HSV 与 B−R 两种方法）、蓝色方块和横条被拒绝、无灯条帧正常、检测和标注不修改原图、逐帧写出且帧数/分辨率/FPS 与输入一致、对比图齐全、路径错误/非视频文件/写入失败返回对应退出码。合成测试通过**不代表**官方视频上的效果。

## 参考来源

只参考了 OpenCV 官方仓库中的接口用法，没有复制代码：

- [samples/python/camshift.py](https://github.com/opencv/opencv/blob/4.x/samples/python/camshift.py)：`cvtColor(BGR2HSV)` + `inRange` 生成掩膜。
- [samples/python/morphology.py](https://github.com/opencv/opencv/blob/4.x/samples/python/morphology.py)：`getStructuringElement` + `morphologyEx` 对比不同操作和核。
- [samples/python/squares.py](https://github.com/opencv/opencv/blob/4.x/samples/python/squares.py)：`findContours` + `approxPolyDP(cnt, 0.02 * 周长, True)` 的筛选思路。
- [Changing Colorspaces 教程](https://github.com/opencv/opencv/blob/4.x/doc/py_tutorials/py_imgproc/py_colorspaces/py_colorspaces.markdown)：用 HSV 提取蓝色物体、OpenCV 的 H 取值范围。
- [Contour Features 教程](https://github.com/opencv/opencv/blob/4.x/doc/py_tutorials/py_imgproc/py_contours/py_contour_features/py_contour_features.markdown)：`minAreaRect`、`boxPoints`、`approxPolyDP`、`contourArea`。

## 已知限制

- 太暗的灯条进不了 HSV 掩膜（例如第 400 帧左侧一对）。过曝到接近白色、饱和度很低的灯条中心也可能被截断，再靠闭运算和轻微膨胀补回。
- 只做单根灯条检测，不做装甲板配对、数字识别或跨帧跟踪。
- 本次视频验证环境为 Windows + Python 3.13.5 + OpenCV 4.14.0 + NumPy 2.5.3。尚未在 Ubuntu 上实际运行。
- 命令需在仓库根目录运行，`src.lightbars` 和 `tests.test_lightbars` 才能被正确导入。
- 输出编码默认 `mp4v`；若系统缺少该编码器会报输出错误，可在配置中改为 `MJPG` 并使用 `.avi` 扩展名。
