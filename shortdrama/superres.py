#!/usr/bin/env python3
"""真超分：Real-ESRGAN 替代"3× lanczos 重采样"。

为什么必须做（用户反馈：成片清晰度不高）：
* H3 在 sm_75 上只能出 **640×352**（横）/ 352×640（竖）；
* 交付要 1920×1056 / 1080×1920 → 现在是**纯插值放大**，没有新增任何细节，
  AI 生成本来就偏软的帧再插一次 → 明显糊；
* 真超分会**重建高频细节**，是唯一能在不重渲（2 小时/集）的前提下提清晰度的办法。

两个后端（按可用性自动选）：
1. **torch + spandrel + CUDA**（首选）：权重 `models/superres/RealESRGAN_x4plus.pth`，
   4× 超分后再缩到交付尺寸（比直接 3× 多一层细节）。GPU 上约 0.1 s/帧。
2. **ncnn/Vulkan**（备选）：`~/realesrgan-ncnn-vulkan/realesrgan-ncnn-vulkan`，
   本机 Vulkan 走的是软件渲染（CPU），约 3.4 s/帧 → 只能当应急。

**显存纪律（C2）**：超分要吃 ~2–4GB 显存，而本地 LLM 常驻 19GB。
所以开跑前会**等显存干净**（默认 < 4000 MiB，最长等 `--wait` 秒），绝不与 LLM 抢卡。

用法：
    python3 shortdrama/superres.py <job> [--scale 3] [--wait 3600]
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

MODEL_DIR = Path("/home/a/ai-work/models/superres")
TORCH_WEIGHT = MODEL_DIR / "RealESRGAN_x4plus.pth"
NCNN_DIR = Path.home() / "realesrgan-ncnn-vulkan"
NCNN_BIN = NCNN_DIR / "realesrgan-ncnn-vulkan"
NCNN_MODEL = "realesr-animevideov3-x3"

# 显存干净阈值（MiB）：低于这个值才允许超分上卡
GPU_FREE_MB = int(os.environ.get("SHORTDRAMA_GPU_FREE_MB", "4000"))


def ffmpeg() -> str:
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        exe = shutil.which("ffmpeg")
        if not exe:
            raise SystemExit("找不到 ffmpeg")
        return exe


def gpu_used_mb() -> int:
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=memory.used",
                              "--format=csv,noheader,nounits"],
                             capture_output=True, text=True).stdout
        return int(out.strip().splitlines()[0])
    except Exception:
        return 10 ** 9          # 查不到就当不干净


def wait_gpu(timeout: int = 3600) -> bool:
    """等显存干净（LLM 让位）。返回是否等到。"""
    t0 = time.time()
    while True:
        used = gpu_used_mb()
        if used < GPU_FREE_MB:
            return True
        if time.time() - t0 > timeout:
            print(f"[superres] 等显存超时（{used} MiB > {GPU_FREE_MB}）")
            return False
        print(f"[superres] 等显存：当前 {used} MiB（要 < {GPU_FREE_MB}）…", flush=True)
        time.sleep(30)


# ---------------------------------------------------------------- torch 后端

def _torch_model():
    import spandrel
    import torch
    m = spandrel.ModelLoader().load_from_file(str(TORCH_WEIGHT))
    return m.to("cuda" if torch.cuda.is_available() else "cpu").eval()


def _superres_torch(job: Path, src: Path, dst: Path, scale: int, batch: int = 6) -> Path:
    import numpy as np
    import torch
    from PIL import Image

    work = job / "clips" / "_sr"
    if work.exists():
        shutil.rmtree(work)
    (work / "in").mkdir(parents=True)
    (work / "out").mkdir(parents=True)

    subprocess.run([ffmpeg(), "-y", "-hide_banner", "-loglevel", "error",
                    "-i", str(src), "-vsync", "0", str(work / "in" / "%05d.png")], check=True)
    frames = sorted((work / "in").glob("*.png"))
    if not frames:
        raise SystemExit(f"抽帧失败：{src}")

    model = _torch_model()
    print(f"[superres] torch 后端：{len(frames)} 帧，batch={batch}", flush=True)
    t0 = time.time()
    for i in range(0, len(frames), batch):
        chunk = frames[i:i + batch]
        arrs = [np.asarray(Image.open(f).convert("RGB"), dtype=np.float32) / 255.0 for f in chunk]
        # 补齐 batch（最后一批可能不足）→ 用最后一帧重复
        while len(arrs) < batch:
            arrs.append(arrs[-1])
        x = torch.from_numpy(np.stack(arrs)).permute(0, 3, 1, 2).to("cuda")
        with torch.no_grad():
            y = model(x)
        y = y.clamp(0, 1).permute(0, 2, 3, 1).cpu().numpy()
        for j, f in enumerate(chunk):
            img = Image.fromarray((y[j] * 255).round().astype("uint8"))
            img.save(work / "out" / f.name)
        if (i // batch) % 50 == 0:
            done = min(i + batch, len(frames))
            el = time.time() - t0
            print(f"[superres] {done}/{len(frames)} 帧（{el:.0f}s，预计还需 "
                  f"{(len(frames) - done) * el / max(1, done) / 60:.1f} 分钟）", flush=True)

    # 4× → 目标尺寸（scale 由 -vf scale 决定，见调用方）
    subprocess.run([ffmpeg(), "-y", "-hide_banner", "-loglevel", "error",
                    "-framerate", "24", "-i", str(work / "out" / "%05d.png"),
                    "-i", str(src), "-map", "0:v", "-map", "1:a?",
                    "-c:v", "libx264", "-preset", "medium", "-crf", "17",
                    "-pix_fmt", "yuv420p", "-r", "24", "-c:a", "copy",
                    "-movflags", "+faststart", str(dst)], check=True)
    shutil.rmtree(work, ignore_errors=True)
    return dst


# ---------------------------------------------------------------- ncnn 后端

def _superres_ncnn(job: Path, src: Path, dst: Path, scale: int) -> Path:
    work = job / "clips" / "_sr"
    if work.exists():
        shutil.rmtree(work)
    (work / "in").mkdir(parents=True)
    (work / "out").mkdir(parents=True)
    subprocess.run([ffmpeg(), "-y", "-hide_banner", "-loglevel", "error",
                    "-i", str(src), "-vsync", "0", str(work / "in" / "%05d.png")], check=True)
    frames = sorted((work / "in").glob("*.png"))
    for i, f in enumerate(frames, 1):
        subprocess.run([str(NCNN_BIN), "-i", str(f), "-o", str(work / "out" / f.name),
                        "-s", str(scale), "-m", str(NCNN_DIR / "models"),
                        "-n", NCNN_MODEL, "-f", "png"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
        if i % 500 == 0:
            print(f"[superres] ncnn {i}/{len(frames)} 帧", flush=True)
    subprocess.run([ffmpeg(), "-y", "-hide_banner", "-loglevel", "error",
                    "-framerate", "24", "-i", str(work / "out" / "%05d.png"),
                    "-i", str(src), "-map", "0:v", "-map", "1:a?",
                    "-c:v", "libx264", "-preset", "medium", "-crf", "17",
                    "-pix_fmt", "yuv420p", "-r", "24", "-c:a", "copy",
                    "-movflags", "+faststart", str(dst)], check=True)
    shutil.rmtree(work, ignore_errors=True)
    return dst


# ---------------------------------------------------------------- 对外接口

def backend() -> str | None:
    if os.environ.get("SHORTDRAMA_UPSCALE", "").lower() in ("off", "0", "false"):
        return None
    if TORCH_WEIGHT.exists():
        try:
            import torch  # noqa: F401
            import spandrel  # noqa: F401
            return "torch"
        except Exception:
            pass
    if NCNN_BIN.exists():
        return "ncnn"
    return None


def enabled() -> bool:
    return backend() is not None


def superres(job: Path, src: Path, dst: Path, scale: int = 3,
             wait: int = 3600, resize_to: tuple[int, int] | None = None) -> Path:
    """src（低分辨率视频）→ dst（超分视频，音轨原样复制）。"""
    be = backend()
    if be is None:
        raise SystemExit("没有可用的超分后端（装权重或设 SHORTDRAMA_UPSCALE=off）")
    if not wait_gpu(wait):
        raise SystemExit("显存一直被占（LLM 没让位），超分未执行")
    if be == "torch":
        tmp = dst.with_name(dst.stem + "_sr.mp4")
        _superres_torch(job, src, tmp, scale)
        if resize_to:
            dw, dh = resize_to
            subprocess.run([ffmpeg(), "-y", "-hide_banner", "-loglevel", "error",
                            "-i", str(tmp), "-vf", f"scale={dw}:{dh}:flags=lanczos",
                            "-c:v", "libx264", "-preset", "medium", "-crf", "18",
                            "-pix_fmt", "yuv420p", "-r", "24", "-c:a", "copy",
                            "-movflags", "+faststart", str(dst)], check=True)
            tmp.unlink(missing_ok=True)
        else:
            tmp.replace(dst)
    else:
        tmp = dst.with_name(dst.stem + "_sr.mp4")
        _superres_ncnn(job, src, tmp, scale)
        if resize_to:
            dw, dh = resize_to
            subprocess.run([ffmpeg(), "-y", "-hide_banner", "-loglevel", "error",
                            "-i", str(tmp), "-vf", f"scale={dw}:{dh}:flags=lanczos",
                            "-c:v", "libx264", "-preset", "medium", "-crf", "18",
                            "-pix_fmt", "yuv420p", "-r", "24", "-c:a", "copy",
                            "-movflags", "+faststart", str(dst)], check=True)
            tmp.unlink(missing_ok=True)
        else:
            tmp.replace(dst)
    return dst


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Real-ESRGAN 真超分")
    ap.add_argument("job", help="集目录")
    ap.add_argument("--scale", type=int, default=3)
    ap.add_argument("--wait", type=int, default=3600, help="等显存干净的秒数")
    ap.add_argument("--src", help="默认 <job>/clips/concat.mp4")
    ap.add_argument("--dst", help="默认 <job>/clips/upscaled.mp4")
    ap.add_argument("--resize", help="目标尺寸，如 1920x1056")
    a = ap.parse_args()
    job = Path(a.job).expanduser().resolve()
    src = Path(a.src) if a.src else job / "clips" / "concat.mp4"
    dst = Path(a.dst) if a.dst else job / "clips" / "upscaled.mp4"
    rt = tuple(int(x) for x in a.resize.split("x")) if a.resize else None
    print("[superres] 后端:", backend())
    superres(job, src, dst, a.scale, a.wait, rt)
    print(f"[superres] {src} → {dst}")
