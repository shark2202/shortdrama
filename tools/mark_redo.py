#!/usr/bin/env python3
"""标记要重出的镜头（删 clip，下次 render 只重出这些），横屏 + 竖屏一起。

自主评审（逐镜抽帧看片）判定的重出清单：

E01  [1, 8]
    1  照片里的人脸与主角不一致（剧情是"照片上的人是我"，观众要能认出）
    8  画面混乱（超市货架过道，无信息）
E02  [全部 c3 镜] + [1]
    c3 镜 8/10/11/12/13/15/19/20/23/29/30/32/33/34/35
        → 换了新的周姐定妆图（去掉"剪得像男生"的歧义），不重渲就会一集两副脸
    1  整镜全糊（浅景深过度，无信息）
E03  [全部 c3 镜] + [15]
    c3 镜 7/9/10/11/18/19/22
    15 严重畸变（一堆扭曲的物体）

用法：python3 tools/mark_redo.py --root ../duanju
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# 换脸角色：c3 全部重出（新定妆图）
REDO_C3 = {"E02": [8, 10, 11, 12, 13, 15, 19, 20, 23, 29, 30, 32, 33, 34, 35],
           "E03": [7, 9, 10, 11, 18, 19, 22]}
# 其它废镜
REDO_OTHER = {"E01": [1, 8], "E02": [1], "E03": [15]}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="标记重出的镜头（删 clip）")
    ap.add_argument("--root", default="/home/a/ai-work/workspace/duanju")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    root = Path(args.root).expanduser().resolve()

    total = 0
    for ep in ("E01", "E02", "E03"):
        redo = sorted(set(REDO_C3.get(ep, [])) | set(REDO_OTHER.get(ep, [])))
        for suffix in ("", "_P"):
            job = root / f"{series_of(ep)}{suffix}"
            clips = job / "clips"
            if not clips.is_dir():
                continue
            removed = 0
            for idx in redo:
                c = clips / f"{idx:02d}.mp4"
                if c.exists():
                    if not args.dry_run:
                        c.unlink()
                    removed += 1
            total += removed
            print(f"  {job.name}: 标记重出 {len(redo)} 镜（已删 {removed} 个 clip）")
    if args.dry_run:
        print(f"[dry-run] 共将删 {total} 个 clip")
    else:
        print(f"共删 {total} 个 clip；下次 render 只重出这些镜")
    return 0


def series_of(ep: str) -> str:
    return f"D202611_001_{ep}"


if __name__ == "__main__":
    sys.exit(main())
