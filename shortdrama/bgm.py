"""BGM 情绪床：给成片垫一条不抢戏的音乐底。

为什么需要：
自主评审发现成片**完全没有 BGM** —— 音轨只有各镜 H3 原生环境音（雨、底噪）。
短剧是听觉媒介，情绪的大半在音乐上；没有音乐床，观感像"监控录像"而不是作品。

选曲与混音原则：
- 音乐必须**比环境音低 12–14 dB**（垫底，不是配乐盖过一切）
- 低通掉刺耳的高频（环境音是雨/底噪，高频会打架）
- 首尾淡入淡出，且**每集用同一段**（系列要有音乐主题，不能每集换）
- 用同一段音乐跨集 = 听觉上的"同一首歌"，这是短剧系列感的低成本做法

素材：Wikimedia Commons 上的 CC BY 4.0 曲目（可商用，需署名）。
署名信息随素材一起放在 assets/bgm.CREDITS，交付时一并给出。
"""

from __future__ import annotations

import subprocess
from pathlib import Path

# BGM 目标响度：比环境音（逐镜归一到 -20 LUFS）低 12 dB 左右
BGM_TARGET_LUFS = -32.0
BGM_LOWPASS_HZ = 6500      # 去掉刺耳高频，和雨声/底噪不打架


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


def prepare(track: Path | str, duration: float, out: Path | str,
            *, target_lufs: float = BGM_TARGET_LUFS, fade: float = 3.0) -> Path:
    """把一段音乐处理成可垫底的床：裁/循环到时长 → 低通 → 淡入淡出 → 归一到目标响度。"""
    track, out = Path(track), Path(out)
    ff = ffmpeg()
    # -stream_loop -1 保证够长；再按 duration 裁
    cmd = [ff, "-y", "-hide_banner", "-loglevel", "error",
           "-stream_loop", "-1", "-i", str(track),
           "-af", (f"lowpass=f={BGM_LOWPASS_HZ},"
                   f"afade=t=in:st=0:d={fade},"
                   f"afade=t=out:st={max(0, duration-fade):.2f}:d={fade},"
                   f"loudnorm=I={target_lufs}:TP=-6:LRA=11:linear=false"),
           "-t", f"{duration:.2f}", "-c:a", "aac", "-ar", "48000", "-b:a", "192k",
           str(out)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"BGM 处理失败：{r.stderr[-500:]}")
    return out


def mix_filter(bgm_path: Path | str) -> str:
    """把 BGM 混进去的滤镜（放在整片 loudnorm 之前，让整片归一把两者一起算）。"""
    return f"[1:a]volume=-6dB[m];[0:a][m]amix=inputs=2:duration=first:dropout_transition=0[aout]"


def credits(track: Path | str) -> str:
    """署名块（CC BY 4.0 要求）。"""
    return (
        f"BGM: {Path(track).name}\n"
        f"作者: Scott Buckley（https://www.scottbuckley.com.au）\n"
        f"授权: Creative Commons Attribution 4.0 (CC BY 4.0)\n"
        f"来源: Wikimedia Commons\n"
        f"说明: 本片 BGM 由该曲目处理而成（裁剪/低通/淡入淡出/响度归一），已按 CC BY 4.0 署名。\n"
    )
