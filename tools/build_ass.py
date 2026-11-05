#!/usr/bin/env python3
"""生成 ASS 字幕（精确控制安全区），代替 ffmpeg 的 `subtitles=` 滤镜。

为什么不用 subtitles=：libass 把 SRT 转 ASS 时用默认 PlayResX=384 / PlayResY=288，
`MarginV` 会按 **288 的比例**放大到实际画面高度：
- 横屏 1920×1056：MarginV=56 → 56/288*1056 = 205px（可见，但偏高）
- **竖屏 1080×1920：MarginV=360 → 360/288*1920 = 2400px → 字幕被推出画面，完全看不见**

（审计实测发现：竖屏成片里根本没有字幕。）

所以自己写 ASS：PlayResX/Y = 实际像素，MarginV 就是真像素，
竖屏 300px（避开抖音底部 UI ~15%）、横屏 56px。

用法：
  python3 tools/build_ass.py <job-dir> [--width 1080 --height 1920]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path


def srt_time(seconds: float) -> str:
    ms = int(round(seconds * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:d}:{m:02d}:{s:02d}.{ms//10:02d}"


def build_ass(shots: list[dict], durations: list[float], width: int, height: int,
              margin_v: int, font_size: int, border_style: int) -> str:
    head = [
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {width}",
        f"PlayResY: {height}",
        "WrapStyle: 2",
        "ScaledBorderAndShadow: yes",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
        "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
        "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        # 白字 + 黑描边 + 半透明底框（竖屏底框更易读，也更像平台原生字幕）
        f"Style: Default,Noto Sans CJK SC,{font_size},&H00FFFFFF,&H00FFFFFF,&H00000000,"
        f"&H60000000,0,0,0,0,100,100,0,0,{border_style},"
        f"{4 if border_style == 1 else 2},0,2,60,60,{margin_v},1",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    lines, t, n = head, 0.0, 0
    last: dict[str, int] = {}          # 相邻 ≤2 镜重复 = 噪音，去重；隔得远的 = 叙事回环，保留
    for i, (shot, dur) in enumerate(zip(shots, durations)):
        text = (shot.get("subtitle_cn") or "").strip()
        if text and i - last.get(text, -99) > 2:
            last[text] = i
            n += 1
            lines.append(f"Dialogue: 0,{srt_time(t)},{srt_time(t + dur)},Default,,0,0,0,,"
                         f"{text}")
        t += dur
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("job")
    ap.add_argument("--width", type=int, default=0)
    ap.add_argument("--height", type=int, default=0)
    a = ap.parse_args()
    job = Path(a.job).expanduser().resolve()
    story = json.loads((job / "story.json").read_text(encoding="utf-8"))
    shots = json.loads((job / "shots.json").read_text(encoding="utf-8"))["shots"]

    # 时长按实际 clip 读（没有就按剧本估）
    durations = []
    for s in shots:
        clip = job / "clips" / f"{s['idx']:02d}.mp4"
        if clip.exists():
            import subprocess
            out = subprocess.run(
                ["ffmpeg", "-hide_banner", "-i", str(clip)], capture_output=True, text=True
            ).stderr
            m = re.search(r"Duration: (\d+):(\d+):(\d+(?:\.\d+)?)", out)
            durations.append(int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
                              if m else (s.get("duration_s") or 5.2))
        else:
            durations.append(s.get("duration_s") or 5.2)

    portrait = bool(story.get("aspect") == "portrait")
    width = a.width or (1080 if portrait else 1920)
    height = a.height or (1920 if portrait else 1056)
    margin_v = 300 if portrait else 56        # 真像素：竖屏避让平台底部 UI
    font_size = 54 if portrait else 42
    border_style = 3 if portrait else 1
    ass = build_ass(shots, durations, width, height, margin_v, font_size, border_style)
    out = job / "subtitles.ass"
    out.write_text(ass, encoding="utf-8")
    print(f"[ass] {out} PlayRes={width}x{height} MarginV={margin_v} FontSize={font_size} "
          f"条数={ass.count('Dialogue:')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
