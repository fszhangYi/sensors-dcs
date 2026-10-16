"""显示 Intel RealSense D405 画面。

快捷键（焦点必须在预览窗口上）：
  r  开始录制当前画面
  e  停止录制
  q  退出程序

点窗口关闭按钮也可以退出。
"""

from __future__ import annotations

import sys
import time
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np

WINDOW_NAME = "RealSense D405"
RECORD_DIR = Path(__file__).resolve().parent / "recordings"


class Recorder:
    def __init__(self) -> None:
        self.writer: cv2.VideoWriter | None = None
        self.path: Path | None = None

    @property
    def active(self) -> bool:
        return self.writer is not None

    def start(self, frame: np.ndarray) -> Path:
        self.stop()
        RECORD_DIR.mkdir(exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.path = RECORD_DIR / f"d405_{stamp}.avi"
        height, width = frame.shape[:2]
        self.writer = cv2.VideoWriter(
            str(self.path),
            cv2.VideoWriter_fourcc(*"MJPG"),
            30.0,
            (width, height),
        )
        if not self.writer.isOpened():
            self.writer = None
            raise RuntimeError(f"无法创建录像文件: {self.path}")
        print(f"开始录制: {self.path}")
        return self.path

    def write(self, frame: np.ndarray) -> None:
        if self.writer is None:
            return
        if frame.ndim == 2:
            frame = cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)
        self.writer.write(frame)

    def stop(self) -> Path | None:
        path = self.path
        if self.writer is not None:
            self.writer.release()
            print(f"停止录制: {path}")
        self.writer = None
        self.path = None
        return path


def colorize_depth(depth_frame) -> np.ndarray:
    import pyrealsense2 as rs

    colorizer = rs.colorizer()
    colorized = colorizer.colorize(depth_frame)
    return np.asanyarray(colorized.get_data())


def find_d405():
    import pyrealsense2 as rs

    context = rs.context()
    for device in context.devices:
        name = device.get_info(rs.camera_info.name)
        serial = device.get_info(rs.camera_info.serial_number)
        print(f"找到设备: {name}  序列号 {serial}")
        if "D405" in name or "405" in name:
            return serial, name
    if len(context.devices) == 1:
        device = context.devices[0]
        return (
            device.get_info(rs.camera_info.serial_number),
            device.get_info(rs.camera_info.name),
        )
    return None, None


def start_realsense():
    import pyrealsense2 as rs

    serial, name = find_d405()
    if serial is None:
        raise RuntimeError("没有找到 RealSense 设备")

    pipeline = rs.pipeline()
    configs = []

    cfg = rs.config()
    cfg.enable_device(serial)
    cfg.enable_stream(rs.stream.color, 640, 480, rs.format.bgr8, 30)
    cfg.enable_stream(rs.stream.depth, 640, 480, rs.format.z16, 30)
    configs.append(("color+depth 640x480", cfg))

    cfg = rs.config()
    cfg.enable_device(serial)
    cfg.enable_stream(rs.stream.infrared, 1, 640, 480, rs.format.y8, 30)
    cfg.enable_stream(rs.stream.depth, 640, 480, rs.format.z16, 30)
    configs.append(("ir+depth 640x480", cfg))

    cfg = rs.config()
    cfg.enable_device(serial)
    cfg.enable_stream(rs.stream.depth, 640, 480, rs.format.z16, 30)
    configs.append(("depth 640x480", cfg))

    last_error = None
    for label, config in configs:
        try:
            profile = pipeline.start(config)
            print(f"已打开 {name}，模式 {label}")
            return pipeline, profile
        except Exception as exc:
            last_error = exc
            try:
                pipeline.stop()
            except Exception:
                pass
            pipeline = rs.pipeline()
    raise RuntimeError(f"无法打开 D405 数据流: {last_error}")


def set_window_title(recording: bool) -> None:
    status = "REC" if recording else "LIVE"
    cv2.setWindowTitle(WINDOW_NAME, f"RealSense D405  {status}  r录制 e停录 q退出")


def handle_key(key: int, recorder: Recorder, frame: np.ndarray | None) -> str:
    if key in (-1, 255, 0):
        return "continue"
    letter = chr(key & 0xFF).lower()
    if letter == "q":
        return "quit"
    if letter == "r":
        if frame is None:
            print("还没有画面，不能开始录制")
        elif recorder.active:
            print("已经在录制，先按 e 停止")
        else:
            recorder.start(frame)
        return "continue"
    if letter == "e":
        if recorder.active:
            recorder.stop()
        else:
            print("当前没有在录制")
        return "continue"
    return "continue"


def window_closed() -> bool:
    try:
        return cv2.getWindowProperty(WINDOW_NAME, cv2.WND_PROP_VISIBLE) < 1
    except Exception:
        return True


def run_loop(get_frame) -> None:
    recorder = Recorder()
    cv2.namedWindow(WINDOW_NAME, cv2.WINDOW_NORMAL)
    cv2.resizeWindow(WINDOW_NAME, 1280, 480)
    print("快捷键: r 录制, e 停止录制, q 退出。请先点击预览窗口再按键。")
    try:
        while True:
            try:
                canvas = get_frame()
            except Exception as exc:
                print(f"取帧失败: {exc}")
                if cv2.waitKeyEx(30) & 0xFF in (ord("q"), ord("Q")):
                    break
                continue
            if canvas is None:
                key = cv2.waitKeyEx(30)
                if handle_key(key, recorder, None) == "quit" or window_closed():
                    break
                continue

            if recorder.active:
                recorder.write(canvas)
            cv2.imshow(WINDOW_NAME, canvas)
            set_window_title(recorder.active)
            key = cv2.waitKeyEx(10)
            if handle_key(key, recorder, canvas) == "quit" or window_closed():
                break
    finally:
        recorder.stop()
        cv2.destroyAllWindows()


def show_realsense() -> None:
    import pyrealsense2 as rs

    pipeline, _profile = start_realsense()

    def get_frame():
        try:
            frames = pipeline.wait_for_frames(1000)
        except RuntimeError as exc:
            if "didn't arrive" in str(exc).lower() or "timeout" in str(exc).lower():
                return None
            raise
        frames.keep()
        depth = frames.get_depth_frame()
        color = frames.get_color_frame()
        infrared = frames.get_infrared_frame(1) or frames.get_infrared_frame()
        panels = []
        if color:
            panels.append(np.asanyarray(color.get_data()))
        elif infrared:
            image = np.asanyarray(infrared.get_data())
            if image.ndim == 2:
                image = cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
            panels.append(image)
        if depth:
            panels.append(colorize_depth(depth))
        if not panels:
            return None
        height = min(panel.shape[0] for panel in panels)
        resized = [
            cv2.resize(panel, (int(panel.shape[1] * height / panel.shape[0]), height))
            for panel in panels
        ]
        return np.hstack(resized)

    try:
        run_loop(get_frame)
    finally:
        pipeline.stop()


def show_opencv() -> None:
    cap = None
    for index in range(8):
        candidate = cv2.VideoCapture(index, cv2.CAP_DSHOW)
        if candidate.isOpened():
            ok, frame = candidate.read()
            if ok and frame is not None:
                print(f"OpenCV 相机索引 {index}: {frame.shape}")
                cap = candidate
                break
        candidate.release()
    if cap is None:
        raise RuntimeError("OpenCV 没有找到可用相机")

    def get_frame():
        ok, frame = cap.read()
        return frame if ok else None

    try:
        run_loop(get_frame)
    finally:
        cap.release()


def main() -> int:
    try:
        show_realsense()
        return 0
    except Exception as exc:
        print(f"RealSense SDK 预览失败: {exc}")
        print("改用 Windows 相机接口...")
        try:
            show_opencv()
            return 0
        except Exception as fallback:
            print(f"UVC 预览也失败: {fallback}")
            return 1


if __name__ == "__main__":
    sys.exit(main())
