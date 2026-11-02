#!/usr/bin/env python3
"""修掉"画面内可读文字"：改成不可辨认 + 把信息挪进 subtitle_cn。

    python3 tools/fix_text_shots.py --story ../duanju/D202611_001_E01/story.json

为什么：H3 / Z-Image 画出的文字是乱码（不认字形）。照片上的日期、监控时间戳、
手写字——凡是"观众需要读懂"的画面文字都会废掉。
改法：prompt_en 改成"字迹存在但不可辨认"，可读内容写进 subtitle_cn（后期 ffmpeg 烧，
永远正确）。只改命中的镜头，其余原样保留。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from shortdrama import textcheck  # noqa: E402
from tools.polish import chat, extract_json  # noqa: E402

SYS = """你是短剧分镜修复师。你只做一件事：把"画面里需要观众读懂的文字"改掉。

原因：H3 视频模型不认字形，画面里的任何文字都会被画成乱码。所以：
- prompt_en 里**不能**要求出现可辨认的文字（日期、时间戳、手写字、招牌、海报文字…）
- 正确写法：保留"这里有一行字/一个日期"的**质感**，但明确说不可辨认、模糊、被水洇开、过小看不清
  例如 "a faded date stamp, too small and blurred to read"、"blurred ink handwriting, illegible"
- 观众真正需要读到的内容，放进 `subtitle_cn`（后期烧字幕，一定正确）
- **不要**改动作、运镜、光线、人物；只处理文字这一项

输出：**只输出一个 JSON 对象**，不要解释。格式：
{"shots": [{"idx": 1, "prompt_en": "改后的英文提示词", "beat": "改后的中文描述",
            "subtitle_cn": "需要上屏的文字（没有就空字符串）"}]}
只包含要改的镜头。"""


def fix_story(base: str, model: str, story_path: Path) -> int:
    story = json.loads(story_path.read_text(encoding="utf-8"))
    hits = textcheck.find_shots(story)
    if not hits:
        print(f"  {story_path.name}：无问题")
        return 0
    print(f"  {story_path.name}：{len(hits)} 镜需要修（{[s['idx'] for s in hits]}）")
    listing = "\n".join(
        f"- idx {s['idx']}：\n    beat: {s.get('beat','')}\n"
        f"    prompt_en: {s.get('prompt_en','')}\n    subtitle_cn: {s.get('subtitle_cn','')}"
        for s in hits)
    raw = chat(base, model, SYS,
               f"这集有这些镜头需要修：\n{listing}\n\n请只输出 JSON。",
               max_tokens=4000, temperature=0.6)
    data = extract_json(raw)
    by_idx = {s["idx"]: s for s in story["scenes"]}
    n = 0
    for fix in data.get("shots", []):
        s = by_idx.get(fix.get("idx"))
        if not s:
            continue
        if fix.get("prompt_en"):
            s["prompt_en"] = fix["prompt_en"]
        if fix.get("beat"):
            s["beat"] = fix["beat"]
        if "subtitle_cn" in fix:
            s["subtitle_cn"] = fix["subtitle_cn"]
        n += 1
    # 确定性兜底：LLM 改写后仍要求可读文字的，直接在 prompt 末尾补"不可辨认"。
    # 模型改写不可靠（会把 printed date 换成另一种说法），这里用规则兜住。
    for s in story["scenes"]:
        if textcheck.check_shot(s):
            s["prompt_en"] = s.get("prompt_en", "").rstrip(",. ") + \
                ", any text in frame is too small and blurred to read"
            n += 1
    story_path.write_text(json.dumps(story, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    left = textcheck.check_story(story)
    print(f"    改了 {n} 镜，剩余问题 {len(left)}")
    for p in left:
        print(f"    ⚠ {p}")
    return len(left)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="修掉画面内可读文字")
    ap.add_argument("--story", nargs="+", required=True)
    ap.add_argument("--base", default="http://127.0.0.1:8080/v1")
    ap.add_argument("--model", default="qwen3.8-local")
    args = ap.parse_args(argv)
    print("[fix-text] 扫描并修画面文字 …")
    total = 0
    for s in args.story:
        total += fix_story(args.base, args.model, Path(s).expanduser().resolve())
    print(f"[fix-text] 完成，剩余 {total} 个问题")
    return 0


if __name__ == "__main__":
    sys.exit(main())
