"""响度处理与测量（EBU R128 / ITU-R BS.1770）。

为什么要两遍 loudnorm：
单遍（single-pass）是动态估计，**不保证命中目标**——实测要 -16 LUFS 时实际只有 -18.1，
而且 LRA=11 的目标根本没压下来（实测 LRA 仍是 21）。
两遍（先测后用）是静态增益，结果可复现、可验收。

为什么要 `alimiter ... :level=disabled`：
ffmpeg 的 alimiter 默认 `level=true`（自动电平），限幅之后又把电平推回去 —— 实测
这一步就是削波的直接成因：链末端 -0.5 dB → AAC 编码后 0.0 dBFS（数字削波）。

还要留编码余量：AAC 重编码有 +0.5 dB 左右的过冲，所以滤镜端要压到 -1.5 dBTP 才能
保证**成片** ≤ -1.0 dBFS。

短剧的交付目标（比通用网络视频稍紧）：
    I  = -16 LUFS   （手机外放场景可以到 -14；先按 -16 走）
    TP ≤ -1.0 dBFS （成片，含编码过冲）
    LRA ≤ 12 LU     （集与集之间响度一致；电影感 LRA 19+ 在手机外放会忽大忽小）
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any

TARGET_I = -16.0
TARGET_TP = -1.5          # 滤镜端留 0.5 dB 给 AAC 编码过冲
TARGET_LRA = 12.0
LIMITER = "alimiter=limit=0.794:attack=1:release=50:level=disabled"   # -2.0 dBFS：给 AAC 编码的 +0.5~0.6 dB 过冲留余量


def ffmpeg() -> str:
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        import shutil
        exe = shutil.which("ffmpeg")
        if not exe:
            raise SystemExit("找不到 ffmpeg")
        return exe


def measure(path: Path | str) -> dict[str, Any]:
    """测一条音轨：I(LUFS) / TP(dBFS 样本峰值) / LRA(LU) / true peak。"""
    out = subprocess.run(
        [ffmpeg(), "-hide_banner", "-i", str(path), "-af", "ebur128=peak=true", "-f", "null", "-"],
        capture_output=True, text=True).stderr
    vol = subprocess.run(
        [ffmpeg(), "-hide_banner", "-i", str(path), "-af", "volumedetect", "-f", "null", "-"],
        capture_output=True, text=True).stderr

    def last(pattern: str, text: str, default: float | None = None) -> float | None:
        m = re.findall(pattern, text)
        return float(m[-1][0] if isinstance(m[-1], tuple) else m[-1]) if m else default

    i = last(r"I:\s*(-?[\d.]+) LUFS", out)
    lra = last(r"LRA:\s*(-?[\d.]+) LU", out)
    tp_true = last(r"TPK:\s*(-?[\d.]+)\s+(-?[\d.]+) dBFS", out)
    peak = last(r"max_volume:\s*(-?[\d.]+) dB", vol)
    # ebur128 的 TPK 行有两个数：true peak 与 sample peak，取 true peak
    tpk = re.findall(r"TPK:\s*(-?[\d.]+)\s+(-?[\d.]+) dBFS", out)
    return {
        "integrated_lufs": i,
        "lra_lu": lra,
        "true_peak_dbtp": float(tpk[-1][0]) if tpk else tp_true,
        "sample_peak_dbfs": peak,
    }


def _measure_for_loudnorm(path: Path | str) -> dict[str, str]:
    """第一遍：拿到 loudnorm 的 measured_* 参数。"""
    out = subprocess.run(
        [ffmpeg(), "-hide_banner", "-i", str(path),
         "-af", f"loudnorm=I={TARGET_I}:TP={TARGET_TP}:LRA={TARGET_LRA}:print_format=json",
         "-f", "null", "-"], capture_output=True, text=True).stderr
    m = re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", out, re.S)
    if not m:
        raise RuntimeError("loudnorm 第一遍没吐出 JSON：\n" + out[-800:])
    return json.loads(m.group(0))


def audio_chain_for(src: Path | str) -> str:
    """两遍 loudnorm + 限幅的完整滤镜链（供 assemble 用）。"""
    meas = _measure_for_loudnorm(src)
    second = (f"loudnorm=I={TARGET_I}:TP={TARGET_TP}:LRA={TARGET_LRA}"
              f":measured_I={meas['input_i']}:measured_TP={meas['input_tp']}"
              f":measured_LRA={meas['input_lra']}:measured_thresh={meas['input_thresh']}"
              f":offset={meas.get('target_offset', 0)}:linear=true")
    # 前后各一道限幅：前面压住源里已有的瞬态，后面兜住 AAC 编码前的余量
    return f"{LIMITER},{second},{LIMITER}"


def check(measured: dict[str, Any], *, target_i: float = TARGET_I,
          max_peak_dbfs: float = -1.0, max_lra: float = 14.0) -> list[dict[str, str]]:
    """按交付标准判定，返回 findings（与 qc.py 同结构）。"""
    out: list[dict[str, str]] = []
    i = measured.get("integrated_lufs")
    if i is None:
        out.append({"level": "warn", "where": "audio",
                    "message": "测不到整体响度（音轨可能缺失）"})
    elif abs(i - target_i) > 1.0:
        out.append({"level": "warn", "where": "audio",
                    "message": f"整体响度 {i:.1f} LUFS 偏离目标 {target_i:.0f} 超过 1 LU"})
    pk = measured.get("sample_peak_dbfs")
    if pk is not None and pk > max_peak_dbfs:
        out.append({"level": "block", "where": "audio",
                    "message": f"样本峰值 {pk:.1f} dBFS > {max_peak_dbfs:.1f}（会数字削波）"})
    lra = measured.get("lra_lu")
    if lra is not None and lra > max_lra:
        out.append({"level": "warn", "where": "audio",
                    "message": f"响度动态范围 LRA {lra:.1f} LU > {max_lra:.0f}（集与集响度会忽大忽小）"})
    return out
