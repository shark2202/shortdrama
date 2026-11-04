"""系列级画面一致性：把每集的亮度/色调拉到同一基准。

自主评审发现：3 集的平均亮度 **E01=100 / E02=80 / E03=85** —— 差 25%。
连续追剧时观众会感到"换了一部片"。这是**系列级**问题，单看每一集都"正常"。

专业做法（调色师）：拿系列的中位亮度做基准，每集按差值补一次曝光。
本模块做同样的事，但用可测量的方式：
    1. 采样测 YAVG（取几段，不必整片）
    2. 算 eq=brightness 的补偿量（实测响应是线性的：+0.04 → +7 YAVG）
    3. 在最终编码时应用

只动亮度不改色相 —— 色相统一要逐镜调，风险高收益低；亮度是观感最刺眼的一项。
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

# 系列的亮度基准（YAVG，0–255）。取三集的中位附近。
TARGET_YAVG = 90.0
# eq=brightness 每 1.0 单位对应的 YAVG 变化（实测：0.04 → +7）
YAVG_PER_BRIGHTNESS = 175.0


def ffmpeg() -> str:
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        known = ("/home/a/ai-work/.venv/lib/python3.10/site-packages/imageio_ffmpeg/"
                 "binaries/ffmpeg-linux-x86_64-v7.0.2")
        if Path(known).exists():
            return known
        import shutil
        return shutil.which("ffmpeg") or "ffmpeg"


def measure_brightness(path: Path | str, samples: int = 3, each: int = 6) -> float:
    """采样几段测平均亮度（比整片快得多，够用）。"""
    acc = 0.0
    used = 0
    for i in range(samples):
        start = i * 30
        p = subprocess.run(
            # 注意：metadata=print 走 info 级日志，不能加 -loglevel error，否则被吞掉
            [ffmpeg(), "-hide_banner", "-ss", str(start),
             "-t", str(each), "-i", str(path),
             "-vf", "signalstats,metadata=print:key=lavfi.signalstats.YAVG",
             "-f", "null", "-"], capture_output=True, text=True)
        vals = [float(v) for v in re.findall(r"YAVG=([\d.]+)", p.stderr)]
        if vals:
            acc += sum(vals) / len(vals)
            used += 1
    return acc / used if used else 0.0


def brightness_filter(current: float, target: float = TARGET_YAVG) -> str:
    """算要补多少亮度。偏差小于 1.5 YAVG 就不动（避免无谓地重编码画面）。"""
    delta = (target - current) / YAVG_PER_BRIGHTNESS
    if abs(delta) < 0.009:          # ≈1.5 YAVG
        return ""
    return f"eq=brightness={delta:.4f}"
