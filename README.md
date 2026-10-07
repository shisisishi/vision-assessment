# 视觉组招新考核

Python + OpenCV 实现。任务一（蓝色灯条识别）见 [`docs/lightbars.md`](docs/lightbars.md)；本文档覆盖任务0（环境）、任务二（相机标定与 AprilTag 位姿）和任务三（CV1 模拟串口）。

> **当前状态（如实说明）**：任务一已完成，标记视频和对比图在 `results/lightbars/`。任务二标定在 `data/calib/camera.json`（1280×720，重投影误差 2.63 px）。打印尺寸与标称值一致：黑框 100.0 mm，方格 20.0 mm。AprilTag 有两段结果：`results/tagpose_demo.mp4` 里标签离开画面后又出现；`results/tagpose_nearfar.mp4` 里直线距离从 0.138 m 变到 0.281 m，1338 帧中 556 帧有效。任务三代码和协议测试已通过。SerialPortAssistant 0.5.35 打开 COM21（115200、8N1、无流控），收到 `results/cv1_from_nearfar.txt` 的全部报文，导出在 `results/serial_assistant_rx.log`：seq 34 有效，35–38 无效，39 起再次有效。Ubuntu 24.04.5（WSL2）已安装，同一套 38 项测试已通过。个人远程仓库：https://github.com/shisisishi/vision-assessment 。

## 目录结构

| 路径 | 职责 |
| --- | --- |
| `src/calibrate.py` | 任务二：棋盘格采集（`capture`）与标定（`calibrate`） |
| `src/tagpose.py` | 任务二：输入读取、去畸变、检测、目标选择、显示/日志；可选接入串口 |
| `src/protocol.py` | 任务三：CV1 报文格式、序号、时间戳、pyserial 发送（与视觉处理分离） |
| `src/serial_pair.py` | 任务三：用 socat 创建 Linux 虚拟串口对；可选的诊断接收器 |
| `src/serial_demo.py` | 任务三：**仅诊断**，发送固定值报文检查链路与校验 |
| `src/print_targets.py` | 生成 AprilTag ID0 与 10x7 棋盘格的打印 SVG |
| `docs/camera.md` | 摄像头、打印实测尺寸（黑框 100.0 mm、方格 20.0 mm）、坐标系示意 |
| `tests/` | `unittest` 单元测试 |
| `tools/setup_ubuntu.sh` | 创建虚拟环境并安装依赖 |
| `output/` | 临时输出（已在 `.gitignore` 中忽略）；需提交的结果放到 `results/` 或 `data/` |

## 任务0：环境

### 版本记录

Ubuntu 24.04.5 LTS 已在 WSL2 里跑过，版本如下。虚拟环境在发行版内的 `/opt/vision-venv`，没有改动桌面上的 Windows `.venv`。

| 项目 | Ubuntu 24.04.5（WSL2，2026-10-07） | 本机 Windows（2026-10-07） |
| --- | --- | --- |
| 系统 | Ubuntu 24.04.5 LTS，内核 6.18.40.1-microsoft-standard-WSL2 | Windows 10.0.26200 |
| Python | 3.12.3 | 3.13.5 |
| OpenCV（opencv-python） | 4.14.0 | 4.14.0 |
| numpy | 2.5.3 | 2.5.3 |
| AprilTag 库：pupil-apriltags | 1.0.4.post11（`tag36h11`，合成图位姿测试已通过） | 1.0.4.post11（`tag36h11`，合成图位姿测试已通过） |
| pyserial | 3.5 | 3.5 |
| socat | 1.8.0.0 | 无（Windows 上没有这条虚拟串口） |
| 串口助手 | 未安装 | SerialPortAssistant 0.5.35 |
| 开发工具 | Cursor | Cursor |

`setup_ubuntu.sh` 结束时会打印上述 Python 库的版本，可直接抄入表格。

### 安装

```bash
sudo apt install python3-venv socat        # 如尚未安装
bash tools/setup_ubuntu.sh                 # 创建 .venv 并 pip install -r requirements.txt
source .venv/bin/activate
```

依赖（`requirements.txt`）：`opencv-python`（4.x）、`numpy`、`pupil-apriltags`、`pyserial`。

AprilTag 使用独立依赖 [pupil-apriltags](https://github.com/pupil-labs/apriltags)，它是 [AprilRobotics/apriltag](https://github.com/AprilRobotics/apriltag)（AprilTag 3 C 库）的 Python 绑定，支持 `families="tag36h11"` 以及 `detect(..., estimate_tag_pose=True, camera_params=(fx, fy, cx, cy), tag_size=...)` 位姿接口。

### 最小验证

```bash
python -c "import cv2; print(cv2.__version__)"
python -c "from pupil_apriltags import Detector; Detector(families='tag36h11'); print('tag36h11 OK')"
python -m unittest discover -s tests -v
```

`src.protocol`、`src.calibrate` 的 `--help` 及协议测试不依赖 pupil-apriltags / pyserial（这两个库在需要时才导入）。

## 任务二：相机标定与 AprilTag 位姿

### 1. 生成打印文件

```bash
python -m src.print_targets --out-dir prints --tag-size-mm 100 --square-mm 20
python -m src.print_targets --verify      # 可选：用 pupil-apriltags 解码生成的图案
```

- `prints/tag36h11_id0_100mm.svg`：tag36h11 ID0，图案取自 OpenCV `DICT_APRILTAG_36h11`。本机测试发现 OpenCV 生成的图案相对官方 `apriltag-imgs/tag36h11/tag36_11_00000.png` 旋转了 180°，程序已转回官方朝向，`tests/test_tagpose.py` 会逐格比对。`100 mm` 指**黑色外框外边长**，四周保留白边（不少于一格）。
- `prints/chessboard_10x7_20mm.svg`：10x7 方格（9x6 内角点），建议 A4 横向打印。
- 两个 SVG 都按毫米写入物理尺寸并附 100 mm 刻度尺。**打印时选择 100% / 实际大小**，打印后用尺子核对刻度尺，再测量黑框外边长和方格边长，记录到 `docs/camera.md`。程序只生成文件，不代表已打印。

### 2. 采集棋盘格

```bash
python -m src.calibrate capture --camera 0 --width 1280 --height 720 --out data/calib/images
```

窗口实时显示角点检测预览；`s` 保存当前**原始帧**（不含叠加标记），`q`/Esc 退出。建议 15～25 张，覆盖画面中部与四边、不同距离和倾斜方向。

### 3. 标定

```bash
python -m src.calibrate calibrate --images data/calib/images --square 0.0200 \
    --output data/calib/camera.json --vis-dir data/calib/vis
```

| 参数 | 含义 |
| --- | --- |
| `--images` | 图片目录或通配符 |
| `--square` | **实测**方格边长，单位米 |
| `--cols/--rows` | 内角点数，默认 9x6 |
| `--vis-dir` | 保存画出角点的图片，用于检查 |
| `--min-images` | 最少有效图片数，默认 8 |

流程：`findChessboardCorners` → `cornerSubPix` 亚像素细化 → `calibrateCamera`。所有图片必须同一分辨率，分辨率不同、无法读取或找不到 9x6 角点的图片会被拒绝并记录原因。

`camera.json` 内容：`image_size`、`camera_matrix`（K）、`dist_coeffs`（k1,k2,p1,p2,k3）、`rms_reprojection_error_px`、`per_image_rms_px`、`accepted`、`rejected`、棋盘格规格和 OpenCV 版本。

重投影误差：用标定得到的 K、畸变和每张图的外参把棋盘格三维角点投影回图像，与检测到的角点比较的像素偏差（RMS）。数值越小说明模型越符合观测；单张误差明显偏大，通常说明该图模糊或角点检测有误，可以删除后重新标定。

### 4. 位姿解算

```bash
# 实时摄像头
python -m src.tagpose --camera 0 --calibration data/calib/camera.json --tag-size 0.1000 \
    --output-video results/tagpose_demo.mp4 --log results/tagpose_log.csv
# 视频 / 图片 / 图片目录
python -m src.tagpose --input some_video.mp4 --calibration data/calib/camera.json \
    --tag-size 0.1000 --headless --max-frames 300
```

| 参数 | 含义 |
| --- | --- |
| `--camera` / `--input` | 二选一：摄像头编号或设备路径；视频、单张图片或图片目录 |
| `--calibration` | `src.calibrate` 生成的 JSON |
| `--tag-size` | **实测**黑色外框外边长，单位米 |
| `--target-id` | 目标 ID，默认 0 |
| `--serial` | 可选，CV1 报文发送端口（任务三） |
| `--output-video` | 可选，保存标注后的视频 |
| `--log` | 可选，逐帧 CSV（检测 ID、状态、t、距离、rvec、R、发送的报文） |
| `--headless` | 不显示窗口 |
| `--max-frames` | 处理帧数上限 |
| `--decimate` | pupil-apriltags `quad_decimate`，默认 1.0（不降采样） |

每帧流程：

1. **分辨率检查**：帧尺寸必须与 `image_size` 完全一致，否则报错退出（不缩放、不裁剪）。摄像头打开时会请求标定分辨率。
2. **去畸变**：`initUndistortRectifyMap(K, dist, None, K)` + `remap`，新内参取原 K，因此去畸变图像对应的内参仍是 K。
3. **检测与位姿**：在去畸变灰度图上调用 pupil-apriltags，`camera_params` 取自 K，畸变已去除。
4. **校验**：R 必须有限、正交且 det≈+1，t 有限且 z>0，否则该检测标记为位姿不可用。
5. **目标选择**：保留全部检测结果，再单独从中选出 `--target-id`（同 ID 多个时取 `decision_margin` 最大者）。状态为 `valid` / `target_missing` / `pose_invalid: 原因` / `empty_frame`。
6. **显示**：所有检测画黄色框和 ID；目标画绿色框、角点编号 0–3、红色中心点，以及用目标的 R、t 投影得到的坐标轴（X 红、Y 绿、Z 蓝，长度为半个 tag_size）；文字显示 R、t、距离和 Z 深度。显示的是去畸变后的图像。

### 坐标与单位

- 相机坐标系：X 向右，Y 向下，Z 向前（光轴）。
- Tag 坐标系：沿用 AprilTag 定义，原点在 Tag 中心，X 向右，Y 向下，Z 指向 Tag 内部；示意图与角点顺序见 [`docs/camera.md`](docs/camera.md)。由于 Z 指向 Tag 内部，画面中蓝色 Z 轴指向远离相机的方向。
- `p_camera = R × p_tag + t`，程序内部单位为米。
- `distance = ‖t‖` 是直线距离，`z = t[2]` 是光轴深度，两者分开显示。例：t=(0.10, 0.00, 0.80) m 时，distance≈0.806 m，z=0.80 m。

## 任务三：CV1 模拟串口

### 链路

```text
src.tagpose --serial A ==== socat PTY 对 ==== B ← COMTool / SerialPortAssistant
```

```bash
# 终端 1：创建串口对（保持运行），会打印 A/B 对应的 /dev/pts/N
python -m src.serial_pair create --a /tmp/cv1_a --b /tmp/cv1_b
# 串口助手打开 B（若端口列表中没有，手动填入打印出的 /dev/pts/N），115200 / 8 数据位 / 无校验 / 1 停止位 / 无流控
# 终端 2：先用固定报文检查格式和校验（仅诊断，不是检测结果）
python -m src.serial_demo --port /tmp/cv1_a --mode samples --count 4
# 再接入任务二实时结果
python -m src.tagpose --camera 0 --calibration data/calib/camera.json --tag-size 0.1000 \
    --serial /tmp/cv1_a --log results/tagpose_log.csv
```

`python -m src.serial_pair listen --port /tmp/cv1_b --log results/rx_check.log` 是可选的诊断接收器，按 CRLF 拆帧并检查校验；它与串口助手不能同时打开 B。**最终接收演示和日志必须来自 COMTool 或 SerialPortAssistant。**

### 报文格式

```text
$CV1,seq,t_ms,valid,id,x_mm,y_mm,z_mm,rx,ry,rz*HH\r\n
```

- 串口：115200/8N1，无硬件/软件流控。
- `seq`：uint32，从 0 开始，每生成一帧加一，`4294967295` 之后回绕到 0。
- `t_ms`：相对程序启动的单调时钟毫秒（`time.monotonic`）。
- `valid=1`：选中目标且位姿通过校验；`id` 为目标 ID。
- `x_mm,y_mm,z_mm`：t（米）×1000，保留 1 位小数。
- `rx,ry,rz`：`cv2.Rodrigues(R)` 得到的旋转向量，弧度，保留 6 位小数；不是欧拉角。
- 定点格式，不使用科学计数法；`-0.0` 统一输出为 `0.0`。
- `HH`：`$` 与 `*` 之间所有字节异或，两位大写十六进制；行尾是真实的 CR LF 两个字节。
- 无效（`valid=0`）：未检测到目标、位姿校验失败、读到空帧，或数值非有限 / z≤0。此时 `id=-1`，坐标和姿态全为 0，每帧都重新判断，不会沿用上一帧的有效位姿。
- 发送频率约 10 Hz（主循环每 0.1 s 发送一次当前帧结果）。
- 发送失败（异常或写入字节数不足）会在 stderr 打印 `[serial] send failed ...` 及累计失败次数；程序自身的打印不代表助手已收到。

已验证的示例（见 `tests/test_protocol.py`）：

```text
$CV1,42,12345,1,0,100.0,-50.0,800.0,0.000000,0.000000,0.000000*33
$CV1,43,12445,0,-1,0.0,0.0,0.0,0.000000,0.000000,0.000000*09
```

## 测试

```bash
python -m unittest discover -s tests -v
```

- `test_protocol.py`：手册两条示例（`*33`、`*09`）逐字节一致、真实 CRLF、NaN/Inf/z≤0/缺失目标转为无效帧、定点格式、uint32 回绕、解析与校验错误。
- `test_tagpose.py`：位姿校验（反射、非正交、NaN、z≤0）、目标选择、距离与深度区分、坐标轴投影、角点顺序、标定文件与分辨率检查、打印图案与官方 ID0 一致；装有 pupil-apriltags 时还会用合成图像检查已知位姿能否被正确恢复。

2026-10-07 在本机 Windows 上 `python -m unittest discover -s tests -v` 为 38 项全部通过，其中包括 pupil-apriltags 的合成位姿恢复。同日在 Ubuntu 24.04.5 上用 `/opt/vision-venv/bin/python -m unittest discover -s tests -v` 再跑一遍，同样 38 项全部通过。

## 待补充的真实数据与环境

- [x] 灯条标记视频与对比图：`results/lightbars/`
- [x] 棋盘格原图、`data/calib/camera.json`、角点可视化 `data/calib/vis/`
- [x] 打印尺寸与标称值一致：黑框 100.0 mm，方格 20.0 mm
- [x] AprilTag 远近演示：`results/tagpose_nearfar.mp4`，距离 0.138～0.281 m
- [x] 离开再出现：`results/tagpose_demo.mp4`；报文摘录 `results/cv1_from_nearfar.txt` 已由串口助手收到，日志在 `results/serial_assistant_rx.log`
- [x] Ubuntu 24.04.5（WSL2）实际运行，版本表已填写；38 项测试通过
- [x] 个人远程 Git 仓库：https://github.com/shisisishi/vision-assessment
- [x] 串口助手接收演示与导出日志：COM20 发送，COM21 由 SerialPortAssistant 接收（有效 → 无效 → 再有效）

## 已知问题

- 任务一已用手册视频验证，说明见 `docs/lightbars.md`。原片在 `C:\Users\33873\Downloads\test_video2..mov`，来自手册百度网盘（提取码 `xik2`），仓库内不重复存放。
- 打印尺寸已确认为黑框 100.0 mm、方格 20.0 mm。位姿演示使用红米后置摄像头经 scrcpy 的 1280×720 画面。手机自带相机录的 720×1280 竖屏视频不能套用这份标定。四段原始采集视频留在本机 `data/calib/capture.mp4` 与 `data/tagpose/capture*.mp4`，不放入远程仓库。
- SerialPortAssistant 0.5.35 已安装。COM3–COM6 是蓝牙串口。com0com 3.0 在安全启动下驱动错误码 52，没有可用端口。本机改用带签名的用户态虚拟串口，桥为 COM20 ↔ COM21。助手打开 COM21，发送端写 COM20。
- Ubuntu 24.04.5 装在 `D:\WSL\Ubuntu-24.04`，名称 `Ubuntu-24.04`。启动：`wsl -d Ubuntu-24.04`。依赖在 `/opt/vision-venv`。商店下载「适用于 Linux 的 Windows 子系统 3.0.1」曾停在 87.1%，当时本机 `wsl --version` 已是 3.0.1.0，发行版是从清华镜像的 `ubuntu-24.04.5-wsl-amd64.wsl` 本地安装的。
- 部分摄像头不支持请求的分辨率，此时程序会因分辨率不一致报错，需要按实际分辨率重新采集和标定。
- 部分串口助手只列出 `/dev/ttyS*`、`/dev/ttyUSB*`，可能需要手动填写 `/dev/pts/N`；WSL 下需确认端口确实可见。
- 去畸变图像沿用原 K（未调用 `getOptimalNewCameraMatrix`），边缘可能有少量黑边或被裁掉的视野。
- 角点重投影误差（CSV `corner_reproj_px`）只是诊断量，不用于判定位姿是否有效。
- 发送节拍取决于主循环速度；相机读取阻塞时无法保证 10 Hz（手册不考核此项）。
- 若 pupil-apriltags 的预编译包与所装 numpy 版本不兼容，需要降低 numpy 版本。

## 参考来源

- 考核手册：视觉组招新考核手册（北京，26.9）
- AprilTag 3 C 库与位姿说明：<https://github.com/AprilRobotics/apriltag>（README「Pose Estimation」、`apriltag_pose.c`、`tag36h11.c`）
- 官方 Tag 图像：<https://github.com/AprilRobotics/apriltag-imgs>
- pupil-apriltags：<https://github.com/pupil-labs/apriltags>
- OpenCV 相机标定教程：<https://docs.opencv.org/4.x/dc/dbb/tutorial_py_calibration.html>
- OpenCV ArUco / AprilTag 字典：<https://docs.opencv.org/4.x/d5/dae/tutorial_aruco_detection.html>
- pyserial：<https://pyserial.readthedocs.io/>
- socat：<http://www.dest-unreach.org/socat/>
- SerialPortAssistant：<https://github.com/KangLin/SerialPortAssistant>；COMTool：<https://github.com/Neutree/COMTool>
