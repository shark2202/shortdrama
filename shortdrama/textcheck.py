"""画面内文字检查：挡住"模型出字必乱码"。

H3 / Z-Image 都会把文字画成乱码（不认字形）。所以**画面里不能出现需要被读懂的文字**：
照片上的日期、监控时间戳、招牌、手写字、海报……一律会废。

可读信息走 `subtitle_cn`（后期 ffmpeg 烧字幕，永远正确）或 `dialogue_cn`（口播）。
画面内可以保留"有一行字"的**质感**，但必须是模糊/不可辨认的。

这是方案 §9 没覆盖的坑：H3 出的视频里，任何需要观众读的文字都是风险点。
"""

from __future__ import annotations

import re

# prompt_en / beat 里出现这些 = 画面里有需要读懂的文字
TEXT_MARKERS = [
    r"\bdate\b", r"\bdated\b", r"\btimestamp\b", r"\bclock reads\b",
    r"\bthe text reads\b", r"\bhandwritten\b", r"\bhandwriting\b", r"\bwritten\b",
    r"\bwriting on\b", r"\bsign says\b", r"\bsignage\b", r"\bneon sign\b",
    r"\bposter\b", r"\bprinted\b", r"\blabel reads\b", r"\bwords\b", r"\bletters\b",
    r"\bnumbers\b", r"\bdigits\b", r"\bthe date\b", r"\bcaption\b", r"\btitle card\b",
    r"\bopen on the text\b", r"\bdisplaying\b", r"\bscreen shows\b", r"\bmonitor shows\b",
]

# 明确说了"不可辨认/模糊" = 安全
SAFE_MARKERS = [
    "illegible", "unreadable", "blurred handwriting", "blurred text", "out of focus",
    "too small to read", "cannot be read", "indistinct", "smudged", "blurred writing",
]


def check_shot(shot: dict) -> list[str]:
    """单个镜头：返回问题清单（空 = 安全）。"""
    problems: list[str] = []
    idx = shot.get("idx", "?")
    blob = " ".join(str(shot.get(k, "")) for k in ("prompt_en", "beat", "shot_type"))
    low = blob.lower()
    if any(re.search(p, low) for p in SAFE_MARKERS):
        return problems
    hits = [p for p in TEXT_MARKERS if re.search(p, low)]
    if hits:
        problems.append(
            f"{idx} 镜要求画面出现可读文字（{hits[:3]}）：模型出字必乱码。"
            f"改成'字迹模糊不可辨认'，把要传达的内容写进 subtitle_cn")
    return problems


def check_story(story: dict) -> list[str]:
    out: list[str] = []
    for s in story.get("scenes", []):
        out += check_shot(s)
    return out


def find_shots(story: dict) -> list[dict]:
    """需要修的镜头（供 LLM 定点修）。"""
    return [s for s in story.get("scenes", []) if check_shot(s)]
