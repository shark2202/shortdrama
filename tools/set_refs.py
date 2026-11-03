"""给每镜定"参考图来源"：ref = 角色 id，或 null = 无角色在场（用 t2v）。

为什么必须显式写死：
H3 的 ref2va 工作流会把提示词强制改成
    "<Picture 1> and <Picture 2> show the same character. <原文>, <风格>, natural ambient audio: ..."
于是**任何镜头都会带上两张角色参考图**。对"照片背面特写""监控画面""鞋尖滴水"
这类镜头，这句话是错的——H3 会去画人，而不是画那张照片。

所以每镜必须声明：
- ref = "c1"/"c2"/"c3" → 该镜在场的人，用他的定妆图（ref2va）
- ref = null → 镜内无人（物镜/环境/剪影/监控回放），用纯 t2v 工作流，不带任何参考图

例外（有意为之）：照片里**就是角色本人**的那几镜（E01-1、E01-21、E02-27）
仍然用 ref2va —— 因为观众要靠脸认出"照片里的人是她"，参考图正是这个用途。

分类依据是逐镜人眼核对的 beat，不是正则猜的。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# episode -> {镜号: ref}（只列出与 LLM 原始 primary 不同的那些，其余沿用）
OVERRIDES: dict[str, dict[int, str | None]] = {
    # E01：人影剪影 / 鞋 / 袋子 / 照片背面 → 无角色在场
    "E01": {6: None, 8: None, 16: None, 18: None, 32: None, 34: None, 35: None},
    # E02：照片白墙特写 / 撕边照片 / 箭头涂鸦 → 无角色；撕口下的半张脸是陈渡
    "E02": {1: None, 21: None, 25: None, 27: "c2"},
    # E03：监控画面（1/13/15/16）/ 抽屉里的盒子 / 照片特写 / 云缝 → 无角色
    "E03": {1: None, 13: None, 15: None, 16: None, 20: None, 27: None,
            33: None, 34: None},
}


def apply(episodes_dir: Path, episodes: list[str]) -> None:
    for ep in episodes:
        f = episodes_dir / f"D202611_001_{ep}" / "story.json"
        if not f.exists():
            print(f"  跳过 {f}（不存在）")
            continue
        story = json.loads(f.read_text(encoding="utf-8"))
        over = OVERRIDES.get(ep, {})
        n = 0
        for sc in story["scenes"]:
            idx = sc["idx"]
            if idx in over:
                sc["ref"] = over[idx]
            else:
                sc["ref"] = sc.get("primary")
            n += 1
        t2v = sum(1 for sc in story["scenes"] if not sc.get("ref"))
        f.write_text(json.dumps(story, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"  {ep}: {n} 镜已标 ref，其中 {t2v} 镜走 t2v（无参考图）")


def main(argv: list[str] | None = None) -> int:
    argv = argv or sys.argv[1:]
    root = Path(argv[0] if argv else
                "/home/a/ai-work/workspace/duanju").expanduser().resolve()
    eps = argv[1:] if len(argv) > 1 else ["E01", "E02", "E03"]
    print("[refs] 标注每镜的参考图来源 …")
    apply(root, eps)
    return 0


if __name__ == "__main__":
    sys.exit(main())
