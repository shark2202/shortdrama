"""竖屏交付（**兜底路径**）：把横屏母版合成 1080×1920。

主路径不是这个 —— 实测 **H3 原生支持竖屏**（工作流的 width/height 参数，352×640 出片正常，
构图与人物一致性都没问题）。所以竖屏应当**原生生成**：在 story.json 里写
``"aspect": "portrait"``，episode.py render 就用 352×640 出片，assemble 直接缩到
1080×1920，**满屏、无留黑**。

本模块只用于：① 已有横屏母版想快速看一版竖屏；② 某镜横屏构图在竖屏里不能用但重渲太贵。

两种合法的做法（各有代价，按内容类型选）：
A. **中心 9:16 裁切** —— 画面占满屏幕，沉浸感最强，但只保留 31% 宽度。
   代价：横构图会废掉（本片 105 镜里 11 个全景镜会切掉关键信息）。
   适用：**人物镜**（近景/特写/中近景，裁中心损失小）。
B. **全宽横幅 + 上下信息区** —— 画面完整保留（1080×594），上下共 1326px 空出来。
   代价：69% 屏幕不是画面，但**正好放字幕条/角色条/花字/集数/进度**——这本来就是
   国内竖屏短剧的常态长相。适用：**环境镜/全景/需要完整构图的镜**。

本模块做 B（安全，不毁镜头）：横幅居中，上信息区放剧名+集数，下信息区放**字幕条**
（不透明的框 = 国内短剧常见的"字幕条"长相）。横屏母版保留，两版并行交付。

字幕安全区（1080×1920）：底部约 250–400px 被平台 UI 占用，
所以字幕落在 y≈1560（离底 360px），在下信息区、不被 UI 遮。

注意：本机 ffmpeg 是 imageio-ffmpeg 的静态构建，**没有 drawtext**（无 libfreetype），
但有 libass → 标题与字幕统一走一个 .ass 字幕文件渲染。
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

W, H = 1080, 1920
PICT_H = 594          # 横幅缩到 1080 宽后的高度（1056 * 1080/1920）
BAR_TOP = 360         # 上信息区高度
SUB_MARGIN_V = 360    # 字幕基线离底部（落在下信息区，不被平台 UI 遮）

FONT = "Noto Sans CJK SC"

# ASS 样式：标题（顶部居中，白）+ 字幕条（底部居中，不透明底框）
ASS_STYLES = f"""[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Top,{FONT},46,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,3,1,8,60,60,150,1
Style: Sub,{FONT},54,&H00FFFFFF,&H00FFFFFF,&H00000000,&H90000000,1,0,0,0,100,100,0,0,3,14,0,2,60,60,{SUB_MARGIN_V},1
"""


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


def _srt_time(t: str) -> str:
    h, m, rest = t.split(":")
    s, ms = rest.split(",")
    return f"{int(h)}:{int(m)}:{float(s):06.3f}"


def build_ass(srt: Path | str, out: Path | str, *, title: str = "",
              episode: str = "") -> Path:
    """把 SRT 转成竖屏用 ASS：顶部标题条 + 底部字幕条（同一个文件，libass 一次渲染）。"""
    srt, out = Path(srt), Path(out)
    head = (title + (f" · {episode}" if episode else "")) if (title or episode) else ""
    lines = [
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {W}",
        f"PlayResY: {H}",
        "",
        ASS_STYLES.rstrip(),
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    if head:
        lines.append(f"Dialogue: 0,0:00:00.00,99:59:59.00,Top,,0,0,0,,{head}")
    if srt.exists():
        text = srt.read_text(encoding="utf-8", errors="ignore")
        blocks = re.findall(r"(\d+)\s*\n([\d:,]+)\s*-->\s*([\d:,]+)\s*\n(.*?)(?:\n\s*\n|\Z)",
                            text, re.S)
        for _, st, en, body in blocks:
            body = body.strip().replace("\n", "\\N")
            if not body:
                continue
            lines.append(f"Dialogue: 0,{_srt_time(st)},{_srt_time(en)},Sub,,0,0,0,,{body}")
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out


def compose(src: Path | str, out: Path | str, *, srt: Path | str | None = None,
            title: str = "", episode: str = "", crf: int = 20) -> Path:
    """横屏母版 → 竖屏 1080×1920。

    src  : 横屏视频（**不要**是已经烧过字幕的版本，否则字幕会落在横幅中间）
    srt  : 字幕文件；给了就渲染成下信息区的字幕条
    """
    src, out = Path(src), Path(out)
    ff = ffmpeg()
    vf = f"scale={W}:{PICT_H},pad={W}:{H}:0:{BAR_TOP}:color=0x0d0f12"
    if srt and Path(srt).exists():
        ass = out.with_suffix(".ass")
        build_ass(srt, ass, title=title, episode=episode)
        vf += f",subtitles={_esc(str(ass.resolve()))}"
    cmd = [ff, "-y", "-hide_banner", "-loglevel", "error", "-i", str(src),
           "-vf", vf, "-c:v", "libx264", "-preset", "medium", "-crf", str(crf),
           "-pix_fmt", "yuv420p", "-r", "24", "-c:a", "aac", "-ar", "48000", "-b:a", "192k",
           "-movflags", "+faststart", str(out)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"竖屏合成失败：{r.stderr[-800:]}")
    return out


def _esc(s: str) -> str:
    """ffmpeg filter 参数里的转义。"""
    return (s.replace("\\", "\\\\").replace(":", r"\:").replace("'", "")
             .replace(",", r"\,").replace("[", r"\[").replace("]", r"\]"))


def probe(path: Path | str) -> dict:
    out = subprocess.run([ffmpeg(), "-hide_banner", "-i", str(path)],
                         capture_output=True, text=True).stderr
    dur = re.search(r"Duration: (\d+):(\d+):(\d+(?:\.\d+)?)", out)
    wh = re.search(r"Video:.*?(\d{2,5})x(\d{2,5})", out)
    return {
        "duration": (int(dur.group(1)) * 3600 + int(dur.group(2)) * 60 + float(dur.group(3)))
        if dur else 0,
        "w": int(wh.group(1)) if wh else 0,
        "h": int(wh.group(2)) if wh else 0,
    }
