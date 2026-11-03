#!/usr/bin/env python3
"""用本地 LLM 给每一镜补台词/旁白（短剧的信息通道）。

为什么必须做：
自主评审发现 3 集的**对白只有 2/7/7 条**（共 105 镜）—— 剧本是纯动作流的，
观众看三分钟几乎读不到信息。短剧是听觉媒介，台词/旁白是主信息通道，
字幕覆盖率 20% 远低于可交付线（40%）。

产出：shots.json 的 subtitle_cn（每镜一句或留空表示沉默镜）+ dialogue_cn。
**只改文本，不改镜头** → 不需要重渲，只需重新 assemble（省 6 小时 GPU）。

用法：
    python3 tools/add_dialogue.py --root ../duanju --episode E01
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from tools.polish import chat, extract_json  # noqa: E402

SYS = """你是竖屏短剧编剧，为一部**都市温情悬疑**短剧补台词与旁白。

这部戏的调子：深夜便利店，两个陌生人之间那种克制的、几乎不说的关心。
悬疑靠"照片上的脸是她自己"推进；温情靠细节（递水、多看一眼、没说出口的话）。

写作纪律（硬要求）：
1. **每句 ≤ 16 字**（竖屏字幕一行放得下，超过就换行难看）。中文。
2. **不解释、不抒情、不总结**。不说"我很难过"，写动作和台词。
3. **不剧透**：不能说出"她三年前失踪""照片是假的"这类真相。可以暗示，不能揭穿。
4. **台词要像人说话**：可以有停顿（用"……"）、可以答非所问、可以有方言味。
5. **旁白（narration）只在必要处用**，语气是叙述者，不要像旁白腔（"我们都知道…"这种不要）。
6. 沉默也是戏：有些镜就该没有字（那镜是"动作/氛围"镜）。**不要每镜都硬塞台词**。
7. 台词要能对上画面：画面是特写就写内心，画外就写画外音。
8. **不要出现画面里已有的信息**（比如墙上写着"2025.1.15"就别再念一遍）。

输出：**只输出一个 JSON 对象**，不要 Markdown 代码块、不要解释。格式：
{"lines": [{"idx": 1, "dialogue_cn": "台词或旁白", "subtitle_cn": "上屏字幕（通常同台词；纯氛围镜留空字符串）"}]}
每镜都要给一条（即使 dialogue_cn 为空字符串）。"""


def build_prompt(story: dict) -> str:
    chars = "\n".join(f"  {c['id']} {c.get('name','')}：{c.get('desc_cn','')}"
                      for c in story.get("characters", []))
    shots = "\n".join(
        f"{s['idx']}. 画面：{s.get('beat','')}"
        f"｜在场：{'、'.join(s.get('chars') or []) or '无'}"
        f"｜环境音：{s.get('sfx','') or '无'}"
        for s in story["scenes"])
    return (f"剧名：{story.get('title','')}\n"
            f"题材：{story.get('genre','')}\n"
            f"风格：{story.get('style_cn','')}\n"
            f"结尾：{story.get('ending','')}\n"
            f"人物：\n{chars}\n\n"
            f"逐镜内容（按顺序）：\n{shots}\n\n"
            f"请为这 35 镜补台词/旁白。注意：钩子镜（第 1 镜）和尾钩镜（最后 1 镜）"
            f"通常**不���台词**——用画面说话更有力。\n只输出 JSON。")


def apply_lines(story: dict, lines: list[dict]) -> int:
    by_idx = {int(x["idx"]): x for x in lines if "idx" in x}
    n = 0
    for sc in story["scenes"]:
        line = by_idx.get(sc["idx"])
        if not line:
            continue
        d = (line.get("dialogue_cn") or "").strip()
        sub = (line.get("subtitle_cn") or "").strip()
        sc["dialogue_cn"] = d
        sc["subtitle_cn"] = sub or d
        n += 1
    return n


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="用本地 LLM 补台词/旁白")
    ap.add_argument("--root", default="/home/a/ai-work/workspace/duanju")
    ap.add_argument("--episode", required=True, help="E01 / E02 / E03（或加 _P）")
    ap.add_argument("--base", default="http://127.0.0.1:8080/v1")
    ap.add_argument("--model", default="qwen3.8-local")
    ap.add_argument("--shots", default="shots.json", help="改哪份（默认 shots.json）")
    args = ap.parse_args(argv)

    root = Path(args.root).expanduser().resolve()
    job = root / f"D202611_001_{args.episode}"
    story = json.loads((job / "story.json").read_text(encoding="utf-8"))

    print(f"[{args.episode}] 请求 LLM 补台词 …", flush=True)
    t0 = time.time()
    raw = chat(args.base, args.model, SYS, build_prompt(story),
               max_tokens=9000, temperature=0.75)
    data = extract_json(raw)
    lines = data.get("lines", [])
    n = apply_lines(story, lines)
    (job / "story.json").write_text(
        json.dumps(story, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    got = sum(1 for s in story["scenes"] if (s.get("subtitle_cn") or "").strip())
    print(f"[{args.episode}] {n} 镜已写，字幕覆盖 {got}/{len(story['scenes'])}"
          f"（{time.time()-t0:.0f}s）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
