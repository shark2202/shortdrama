#!/usr/bin/env python3
"""按"提示词写作 5 条纪律"检查 story.json 的每一镜（文本启发式，全集扫描）。

为什么要这个 linter：审片结论会漏，而**纪律可以机器查**。
今晚最贵的教训是"用关键帧代替成片审片"导致 29 镜误判；
但纪律本身（唇语镜/否定约束/抽象镜/两人同框）是可以在写提示词时就查出来的。

5 条纪律（见 SOP §2.7）：
  1 唇语/口型镜必错 → 提示词别写"口型/无声/嘴唇在动"
  2 闪回/抽象/倒影镜必错 → 别写 flashback / reflection / 模糊轮廓
  3 画面出现第二个人 → 身份必漂移 → 必须写死第二人的辨识特征
  4 否定约束无效 → 别写 no people / empty / 无人的
  5 物件驱动的镜头最可靠 → 关键道具要单独给特写

用法：
  python3 tools/check_discipline.py /home/a/ai-work/duanju/D202611_001_E03
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

# 纪律 1：唇语/口型（模型会画成吃东西/怪表情）
LIP = re.compile(r"lip(s)?[^.]{0,40}(moving|mov\w+|form\w+ word|silent|no sound|without sound)|"
                 r"mouth[^.]{0,30}(mov\w+|form\w+ word|silent|no sound)", re.I)
# 纪律 2：闪回 / 倒影 / 抽象轮廓
ABSTRACT = re.compile(r"flashback|reflection|reflected|mirage|blurred silhouette|"
                      r"through (a )?(layer of )?water|rippl\w+ water", re.I)
# 纪律 4：否定约束
NEGATION = re.compile(r"no people|nobody|empty store|empty room|without people|"
                      r"not a single person|no one (anywhere|in)|contains only", re.I)
# 纪律 3：第二个人（同框但没给辨识特征）
TWO_PEOPLE = re.compile(r"\b(a man|a woman|the man|the woman|another)\b", re.I)
IDENTITY = re.compile(r"red[- ]framed|large red|round glasses|blue uniform|white apron|"
                      r"taped glasses|washed-out apron|chevron|checkered shirt", re.I)


def check_scene(sc: dict) -> list[str]:
    en = sc.get("prompt_en", "")
    cn = sc.get("prompt_cn", "")
    out = []
    if LIP.search(en):
        out.append("纪律1 唇语/口型镜：模型会画成吃东西 → 改写为'正在说话，嘴唇张开'")
    if ABSTRACT.search(en) or re.search(r"闪回|倒影", cn):
        out.append("纪律2 闪回/倒影/抽象镜 → 换成具体可见的物件状态")
    if NEGATION.search(en) or re.search(r"空无一人|没有(任何)?人", cn):
        out.append("纪律4 否定约束无效 → 只写存在什么（把人物描述彻底移除）")
    if TWO_PEOPLE.search(en) and not IDENTITY.search(en):
        out.append("纪律3 同框第二人没有辨识特征 → 写死特征（红框眼镜/白围裙/蓝制服）")
    if not cn.strip():
        out.append("缺 prompt_cn：关键帧/中文审片没有依据")
    return out


def main() -> int:
    job = Path(sys.argv[1]).expanduser().resolve()
    story = json.loads((job / "story.json").read_text(encoding="utf-8"))
    hits = 0
    for sc in story.get("scenes", []):
        problems = check_scene(sc)
        if problems:
            hits += 1
            print(f"{sc['id']}:")
            for p in problems:
                print(f"    - {p}")
    print(f"\n{job.name}: {len(story.get('scenes', []))} 镜中 {hits} 镜违反纪律")
    return 0


if __name__ == "__main__":
    sys.exit(main())
