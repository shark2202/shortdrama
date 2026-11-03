#!/usr/bin/env python3
"""终端里看图（Pillow → ANSI 真彩色半块）。herdr TUI 没有图片预览时用这个。

为什么需要：短剧审核的证据是图（定妆候选、关键帧、视频抽帧），
但 TUI 里人看不到图 —— 只能把图"打印"到终端，或者把审核结论写成文字。

用法：
  /home/a/ai-work/.venv/bin/python shortdrama/tview.py qc/keyframes_sheet.png
  /home/a/ai-work/.venv/bin/python shortdrama/tview.py qc/keyframes_sheet.png --cols 140
  /home/a/ai-work/.venv/bin/python shortdrama/tview.py --grid frames --cols 140   # 目录里的图拼一屏
  /home/a/ai-work/.venv/bin/python shortdrama/tview.py --grid clips               # 视频抽帧后拼图
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

try:
    from PIL import Image
except ImportError:
    raise SystemExit("需要 Pillow：用 /home/a/ai-work/.venv/bin/python 运行")


def ansi_block(img: Image.Image, cols: int) -> str:
    """把图渲染成 ANSI 半块字符（每字符上下两像素：前景=上，背景=下）。"""
    w, h = img.size
    rows = max(2, int(cols * (h / w) / 2))
    rows += rows % 2                      # 上下配对需要偶数行
    img = img.convert("RGB").resize((cols, rows), Image.LANCZOS)
    px = img.load()
    out = []
    for y in range(0, rows, 2):
        line = []
        for x in range(cols):
            r1, g1, b1 = px[x, y]
            r2, g2, b2 = px[x, y + 1]
            line.append(f"\x1b[38;2;{r1};{g1};{b1}m\x1b[48;2;{r2};{g2};{b2}m▀")
        out.append("".join(line) + "\x1b[0m")
    return "\n".join(out)


def grid_sheet(paths: list[Path], out: Path, cols: int = 3) -> Path:
    """把多张图拼成一张（先拼再 ANSI，避免刷屏）。"""
    imgs = [Image.open(p).convert("RGB") for p in paths]
    if not imgs:
        raise SystemExit("没有图")
    tw = 480
    thumbs = [im.resize((tw, max(1, int(im.height * tw / im.width))), Image.LANCZOS) for im in imgs]
    rows = (len(thumbs) + cols - 1) // cols
    rh = max(t.height for t in thumbs)
    sheet = Image.new("RGB", (tw * cols, rh * rows), (16, 16, 16))
    for i, t in enumerate(thumbs):
        sheet.paste(t, ((i % cols) * tw, (i // cols) * rh))
    out.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out)
    return out


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="终端看图")
    ap.add_argument("image", nargs="?", help="图片路径")
    ap.add_argument("--grid", help="目录：把目录里的 png 拼一屏看")
    ap.add_argument("--cols", type=int, default=100, help="终端宽度（字符数，默认 100）")
    ap.add_argument("--out", default="", help="--grid 时的中间拼图路径")
    a = ap.parse_args(argv[1:])

    if a.grid:
        d = Path(a.grid)
        files = sorted([p for p in d.glob("*.png") if not p.name.startswith("_")])
        if not files:
            raise SystemExit(f"{d} 里没有 png")
        out = Path(a.out) if a.out else d / "_grid.png"
        sheet = grid_sheet(files, out)
        print(f"[tview] {len(files)} 张 → {sheet}")
        img = Image.open(sheet)
    elif a.image:
        img = Image.open(a.image)
    else:
        ap.print_help()
        return 2
    print(ansi_block(img, a.cols))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
