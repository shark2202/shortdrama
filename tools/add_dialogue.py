#!/usr/bin/env python3
"""用本地 LLM 补台词/旁白（短剧的信息通道）。

为什么必须做：
自主评审发现 3 集的**对白只有 2/7/7 条**（共 105 镜）—— 剧本是纯动作流的，
观众看三分钟几乎读不到信息。短剧是听觉媒介，台词/旁白是主信息通道。

关键设计（第一版失败后的教训）：
第一版把"哪些镜该有台词"也交给 LLM，它把"沉默也是戏"理解成**全部留空**
（实测 35 镜只写出 1 条，还把原有的好字幕覆盖掉了）。
→ 这次**由代码决定哪些镜要台词**（确定性规则），LLM 只负责写内容；
→ **合并**而不是覆盖：已有的字幕（时间码、"你该回来了。"这类钩子句）必须保留。

产出：story.json 的 dialogue_cn / subtitle_cn。**只改文本，不改镜头** →
不需要重渲，只需重新 assemble。

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

SYS = """你是竖屏短剧编剧，给指定镜头写台词或旁白。

这部戏：深夜便利店，两个陌生人之间那种克制的、几乎不说的关心。
悬疑靠"照片上的脸是她自己"推进；温情靠细节（递水、多看一眼、没说出口的话）。

写作纪律（硬要求）：
1. **每句 ≤ 16 字**（竖屏字幕一行放得下）。中文。
2. **不解释、不抒情、不总结**。不说"我很难过"，写动作和台词。
3. **不剧透**：不能说出"她三年前失踪""照片是假的"这类真相。可以暗示，不能揭穿。
4. **像人说话**：可以有停顿（用"……"）、可以答非所问。
5. 只写"看得见/听得见"的内容：画面是特写就写内心，画外就写画外音。
6. **不许留空** —— 你被指定的每一镜都必须给一句话（如果这镜真的没话，
   就写一句极短的环境感受或动作提示，如"雨停了"）。

输出：**只输出一个 JSON 对象**，不要 Markdown 代码块、不要解释。格式：
{"lines": [{"idx": 7, "dialogue_cn": "台词", "subtitle_cn": "上屏字幕"}]}
只包含我指定的镜号。"""

# 明确要有台词的镜（画面里有人在说话/反应）
TALK_CUES = ("说", "问", "答", "开口", "喊", "念", "低语", "喃喃", "对白", "台词", "解释")


def pick_targets(story: dict) -> list[int]:
    """决定哪些镜要台词（确定性规则）。

    规则：
      · 钩子镜（第 1 镜）和尾钩镜（最后 1 镜）**不写**——用画面说话更有力
      · 纯物镜（ref=None）只在少数几镜写旁白（信息量大的）
      · 有角色在场的镜：每 1 镜取 1（保证密度）
      · beat 里出现"说/问/开口"的镜：必写
    """
    scenes = story["scenes"]
    n = len(scenes)
    targets: list[int] = []
    for i, s in enumerate(scenes):
        if i == 0 or i == n - 1:
            continue
        beat = s.get("beat", "")
        if s.get("ref"):
            targets.append(s["idx"])
        elif any(c in beat for c in TALK_CUES):
            targets.append(s["idx"])
    return targets


def build_prompt(story: dict, targets: list[int]) -> str:
    chars = "\n".join(f"  {c['id']} {c.get('name','')}：{c.get('desc_cn','')}"
                      for c in story.get("characters", []))
    by_idx = {s["idx"]: s for s in story["scenes"]}
    listing = "\n".join(
        f"{i}. 画面：{by_idx[i].get('beat','')}"
        f"｜在场：{'、'.join(by_idx[i].get('chars') or []) or '无'}"
        for i in targets)
    return (f"剧名：{story.get('title','')}\n题材：{story.get('genre','')}\n"
            f"结尾：{story.get('ending','')}\n人物：\n{chars}\n\n"
            f"请为下面这 {len(targets)} 镜写台词/旁白（每镜一句，不许留空）：\n{listing}\n"
            f"只输出 JSON。")


def merge(story: dict, lines: list[dict]) -> int:
    """合并（不覆盖）：已有字幕的镜保留，新的只填空位。"""
    by_idx = {int(x["idx"]): x for x in lines if "idx" in x}
    n = 0
    for sc in story["scenes"]:
        if (sc.get("subtitle_cn") or "").strip():
            continue                      # 已有字幕 → 不动
        line = by_idx.get(sc["idx"])
        if not line:
            continue
        d = (line.get("dialogue_cn") or "").strip()
        sub = (line.get("subtitle_cn") or "").strip()
        if not d and not sub:
            continue
        sc["dialogue_cn"] = d
        sc["subtitle_cn"] = sub or d
        n += 1
    return n


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="用本地 LLM 补台词/旁白（合并式）")
    ap.add_argument("--root", default="/home/a/ai-work/workspace/duanju")
    ap.add_argument("--episode", required=True, help="E01 / E02 / E03")
    ap.add_argument("--base", default="http://127.0.0.1:8080/v1")
    ap.add_argument("--model", default="qwen3.8-local")
    ap.add_argument("--sync-portrait", action="store_true", default=True,
                    help="同时同步到 _P 竖屏版")
    args = ap.parse_args(argv)

    root = Path(args.root).expanduser().resolve()
    job = root / f"D202611_001_{args.episode}"
    story = json.loads((job / "story.json").read_text(encoding="utf-8"))

    targets = pick_targets(story)
    print(f"[{args.episode}] 目标 {len(targets)} 镜要有台词（LLM 只写内容）…", flush=True)
    t0 = time.time()
    raw = chat(args.base, args.model, SYS, build_prompt(story, targets),
               max_tokens=20000, temperature=0.8)
    data = extract_json(raw)
    lines = data.get("lines", [])
    n = merge(story, lines)
    (job / "story.json").write_text(
        json.dumps(story, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    got = sum(1 for s in story["scenes"] if (s.get("subtitle_cn") or "").strip())
    print(f"[{args.episode}] 新写 {n} 镜，字幕覆盖 {got}/{len(story['scenes'])}"
          f"（{time.time()-t0:.0f}s，LLM 返回 {len(lines)} 条）")

    # 同步到竖屏版（同一套剧本，只是画幅不同）
    if args.sync_portrait:
        pj = root / f"D202611_001_{args.episode}_P"
        if pj.is_dir():
            ps = json.loads((pj / "story.json").read_text(encoding="utf-8"))
            ps["scenes"] = story["scenes"]          # 台词一致
            for a, b in zip(ps.get("characters", []), story.get("characters", [])):
                a.update({k: v for k, v in b.items() if k in ("desc_cn", "identity_cn", "traits_en")})
            (pj / "story.json").write_text(
                json.dumps(ps, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            print(f"[{args.episode}] 已同步到 {pj.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
