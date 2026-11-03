#!/usr/bin/env python3
"""从已有横屏集派生竖屏集（并行 job 目录）。

    python3 tools/make_portrait_jobs.py --root ../duanju --series D202611_001

竖屏 = **原生生成**（H3 支持 width/height），所以竖屏版是一个独立的一集：
    D202611_001_E01  →  D202611_001_E01_P
每个 _P 目录里：story.json 加 "aspect": "portrait"、cast/ 软链到剧集级共享定妆图、
其余目录（clips/ logs/ qc/）为空待渲。

这样零代码改动就能跑：`episode.py <job> render` 自动按 aspect 出 352×640，
assemble 自动缩到 1080×1920，qc 自动按竖屏规格校验。
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="派生竖屏版 job 目录")
    ap.add_argument("--root", default="/home/a/ai-work/workspace/duanju")
    ap.add_argument("--series", default="D202611_001")
    ap.add_argument("--suffix", default="_P")
    ap.add_argument("--force", action="store_true", help="已存在就覆盖")
    args = ap.parse_args(argv)

    root = Path(args.root).expanduser().resolve()
    eps = sorted(root.glob(f"{args.series}_E*"))
    if not eps:
        print(f"没找到 {args.series}_E* 集")
        return 1
    for src in eps:
        if src.name.endswith(args.suffix):
            continue
        dst = src.with_name(src.name + args.suffix)
        if dst.exists():
            if not args.force:
                print(f"  跳过 {dst.name}（已存在，--force 可覆盖）")
                continue
            shutil.rmtree(dst)
        dst.mkdir(parents=True)
        story = json.loads((src / "story.json").read_text(encoding="utf-8"))
        story["aspect"] = "portrait"
        (dst / "story.json").write_text(
            json.dumps(story, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        # 定妆图共用（软链到剧集级 cast/，3 集 + 竖屏版共用同一套脸）
        os.symlink("../cast", dst / "cast")
        for sub in ("clips", "frames", "qc", "logs"):
            (dst / sub).mkdir(exist_ok=True)
        print(f"  {src.name} → {dst.name}（aspect=portrait, cast 共用）")
    print("渲染竖屏版：")
    for src in eps:
        dst = src.with_name(src.name + args.suffix)
        if dst.exists():
            print(f"  episode.py {dst} render")
    return 0


if __name__ == "__main__":
    sys.exit(main())
