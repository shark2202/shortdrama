#!/usr/bin/env python3
"""逐镜逻辑体检：把"观众看不懂/前后矛盾"找出来，变成可核对的清单。

为什么需要（用户反馈：部分内容不符合逻辑）：
短剧的逻辑问题不是"审美"，是**可指认的**：指代不明、与画面描述相反、前后事实冲突、
答非所问、没有主语的复述。这些都能用文字核对 → 交给 LLM 找，人来拍板。

用法：
    python3 tools/logic_audit.py --root /home/a/ai-work/duanju --episode E01
    python3 tools/logic_audit.py --root ... --all        # 三集一起
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tools.polish import chat  # noqa: E402

SYS = """你是短剧剧本审校。给你一集的镜头清单（镜号 / 画面描述 / 字幕 / 台词 / 在场角色），只找**逻辑问题**：

1. 指代不明：字幕/台词里的"他/她/这个/那"指不到在场的人。
2. 与画面描述矛盾：画面写"没接住"，字幕却写"接住了"（或反之）。
3. 前后事实冲突：物件状态、时间、地点、称呼在相邻镜之间对不上。
4. 答非所问：角色说的话跟这一镜发生的事没关系。
5. 观众看不懂的复述：没有主语的动作陈述（"没放下。""压平了。""闭了一下。"）——
   当字幕可以，当"角色说的话"不行（人物不会那样说话）。

只报**有把握**的问题，最多 8 条/集。不要报画面质量、表演、审美、运镜。
输出一个 JSON 对象：{"issues":[{"idx":12,"type":"指代不明","why":"一句话说明","fix":"建议改成"}]}
没有把握的问题就不要输出。"""

USER = """剧名：{title}
下面是这一集的镜头清单：

{shots}

按上面 5 类找逻辑问题，只输出 JSON。"""


def load(p: Path) -> dict:
    return json.loads(p.read_text(encoding="utf-8"))


def audit(story: dict, base: str = "http://127.0.0.1:8080/v1",
          model: str = "qwen3.8-local") -> list[dict]:
    rows = []
    for sc in story.get("scenes", []):
        rows.append(
            f"[{sc['idx']:2d}] 在场：{'、'.join(sc.get('chars') or []) or '无'} | "
            f"画面：{(sc.get('beat') or '').strip()} | "
            f"字幕：{(sc.get('subtitle_cn') or '').strip() or '-'} | "
            f"台词：{(sc.get('dialogue_cn') or '').strip() or '-'}")
    raw = chat(base, model, SYS, USER.format(title=story.get("title", ""),
                                            shots="\n".join(rows)),
               max_tokens=4000, temperature=0.2)
    try:
        start = raw.index("{")
        end = raw.rindex("}") + 1
        data = json.loads(raw[start:end])
    except Exception:
        return [{"idx": 0, "type": "解析失败", "why": raw[:200], "fix": "-"}]
    return data.get("issues", [])


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="逐镜逻辑体检")
    ap.add_argument("--root", default="/home/a/ai-work/duanju")
    ap.add_argument("--episode", help="E01 / E01_P …")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--out", help="输出 JSON 路径")
    a = ap.parse_args(argv)

    root = Path(a.root).expanduser().resolve()
    eps = sorted(p for p in root.glob("D202611_001_E*") if p.is_dir())
    if a.episode:
        eps = [p for p in eps if a.episode in p.name]
    result = {}
    for job in eps:
        story = load(job / "story.json")
        issues = audit(story)
        result[job.name] = issues
        print(f"── {job.name}: {len(issues)} 条")
        for it in issues:
            print(f"   [{it.get('idx')}] {it.get('type')}：{it.get('why')} → {it.get('fix')}")
    if a.out:
        Path(a.out).write_text(json.dumps(result, ensure_ascii=False, indent=2),
                               encoding="utf-8")
        print(f"[logic] 已写 {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
