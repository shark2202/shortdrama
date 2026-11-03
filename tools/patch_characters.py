#!/usr/bin/env python3
"""修人物一致性 + 补英文特征词（写进 story.json）。

两个根因（都是自主评审看片发现的）：

1. **周姐的描述有歧义**："短发剪得像男生" → Z-Image/H3 把它读成"男性化的脸"，
   于是 E02/E03 里她有 9 镜出成年轻男性（近景特写最容易漂）。
   → 改成明确的"灰白齐耳短发 + 红色大框眼镜 + 中年女性"，去掉"像男生"。

2. **英文提示词里没有角色的外形锚点**：ref2va 完全靠参考图撑住人脸，
   而 prompt_en 只描述动作。参考图一旦漂，模型没有第二重约束。
   → 给每个角色一句 `traits_en`，在 build_shots 里追加到 ref2va 的提示词。

用法：
    python3 tools/patch_characters.py --root ../duanju
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# 英文特征词：H3 的文本编码器只吃英文，外形必须在这里也写一遍
TRAITS_EN = {
    "c1": "a young Asian woman in a blue convenience store uniform with a small plaster "
          "bandage on her left wrist and long straight dark hair",
    "c2": "an Asian man in a soaked dark blue shirt, round glasses with taped temples, "
          "short stubble and wet hair",
    "c3": "an older Asian woman with short grey hair and large red-framed reading glasses, "
          "deep nasolabial folds, wearing a faded white apron",
}

# 周姐的描述：去掉"像男生"的歧义
C3_DESC = ("方脸，下颌角宽，左眉比右眉高半指。灰白色齐耳短发，发尾参差不齐，鬓角发白。"
           "法令纹深，眼袋浮肿，手指粗，指甲剪得很短。红色大框老花镜，"
           "洗得发白的围裙，口袋里塞着半截铅笔。")
C3_IDENTITY = "亚洲女性，52岁"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="修人物描述 + 补英文特征词")
    ap.add_argument("--root", default="/home/a/ai-work/workspace/duanju")
    args = ap.parse_args(argv)
    root = Path(args.root).expanduser().resolve()

    for f in sorted(root.glob("D202611_001_E*/story.json")):
        story = json.loads(f.read_text(encoding="utf-8"))
        changed = []
        for c in story.get("characters", []):
            cid = c.get("id")
            if cid in TRAITS_EN and c.get("traits_en") != TRAITS_EN[cid]:
                c["traits_en"] = TRAITS_EN[cid]
                changed.append(f"{cid}+traits")
            if cid == "c3":
                if c.get("desc_cn") != C3_DESC:
                    c["desc_cn"] = C3_DESC
                    changed.append("c3.desc")
                if c.get("identity_cn") != C3_IDENTITY:
                    c["identity_cn"] = C3_IDENTITY
                    changed.append("c3.identity")
        f.write_text(json.dumps(story, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"  {f.parent.name}: {', '.join(changed) or '无变化'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
