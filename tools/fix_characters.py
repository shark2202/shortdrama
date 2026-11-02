#!/usr/bin/env python3
"""用本地 LLM 重写人物造型，直到通过 facecheck 检查（挡"大众脸"）。

    python3 tools/fix_characters.py --outline ../duanju/series_outline.json

做法：把每个角色的现有描述 + 检查器报出的问题丢给 LLM，让它只重写 desc_cn，
保留原描述里已经具体的部分（发型/道具/气质），补齐缺的维度。
改完再跑一次 facecheck，不通过就再要一轮（最多 3 轮）。

只改 desc_cn —— 名字、剧情、人设不动。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from shortdrama import facecheck  # noqa: E402
from tools.polish import chat, extract_json  # noqa: E402

SYS = """你是短剧造型指导（美术指导）。你负责把角色外貌描述写成**能被 AI 出图准确还原**的造型说明书。

一条合格的角色造型描述必须同时包含这 6 个维度，缺一个就出"大众脸"：
1. **脸型/骨相**：方脸/长脸/圆脸/心形脸/菱形脸 + 下颌角/颧骨/下颌线等骨相特征
2. **发型细节**：中分/偏分/刘海长短/寸头/银丝/发尾颜色…（发型是最强辨识点）
3. **不对称特征**：痣的位置/疤/单侧眉更高/酒窝/龅牙/下颌角不对称…（对称脸=大众脸）
4. **年龄痕迹**：法令纹/鱼尾纹/抬头纹/颈纹/眼袋/胡茬/粗糙的手/发际线…（光写"24岁"没用）
5. **具体衣着**：款式 + 颜色 + 材质 + 穿戴状态（"蓝色便利店制服，袖子卷过肘"）
6. **随身物件/职业痕迹**：工牌/挂绳/笔/本/钥匙/疤贴…（人物的生活质感）

硬要求：
- **禁止空泛词**：漂亮/美丽/帅哥/美女/网红/气质出众/温柔/好看/清秀… 一律不许出现
- 禁止写"像省电模式""像被雨淋湿的书"这种比喻（AI 画不出来）——比喻只能放在
  `mood` 字段里单独给
- 只描述**看得见的**，不要写情绪形容词（"悲伤""疲惫"看不见）
- 中文，40–90 字
- **不要**加任何风格词（霓虹/洋红/胶片颗粒/低照度）——那是画面风格，不是造型

输出：**只输出一个 JSON 对象**，不要解释。格式：
{"id":"c1","name":"姓名","desc_cn":"造型描述（40-90字）","mood":"气质比喻（可选，一句话）"}"""


def fix_character(base: str, model: str, ch: dict, problems: list[str],
                  attempts: int = 3) -> tuple[dict, list[str]]:
    """反复让 LLM 重写，直到 facecheck 通过。返回 (新角色, 剩余问题)。"""
    cur = dict(ch)
    for i in range(attempts):
        ask = (f"角色 {cur.get('id')} {cur.get('name')}\n"
               f"当前描述：{cur.get('desc_cn', '')}\n"
               f"检查器指出的问题：\n" + "\n".join(f"- {p}" for p in problems) + "\n"
               f"请重写 desc_cn，必须补齐上面所有缺失维度，同时保留原描述里已经具体的部分。")
        raw = chat(base, model, SYS, ask, max_tokens=2000, temperature=0.75)
        data = extract_json(raw)
        if not data.get("desc_cn"):
            continue
        cur["desc_cn"] = data["desc_cn"]
        if data.get("mood"):
            cur["mood"] = data["mood"]
        problems = facecheck.check(cur["desc_cn"])
        if not problems:
            return cur, []
        print(f"      第 {i+1} 轮后仍有 {len(problems)} 个问题，继续")
        time.sleep(1)
    return cur, problems


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="用 LLM 把人物造型改到通过 facecheck")
    ap.add_argument("--outline", required=True)
    ap.add_argument("--base", default="http://127.0.0.1:8080/v1")
    ap.add_argument("--model", default="qwen3.8-local")
    ap.add_argument("--out", help="写回的路径（默认原地覆盖）")
    args = ap.parse_args(argv)

    path = Path(args.outline).expanduser().resolve()
    outline = json.loads(path.read_text(encoding="utf-8"))
    print(f"[1/2] 检查 {len(outline.get('characters', []))} 个角色的造型 …")
    for ch in outline.get("characters", []):
        problems = facecheck.check(ch.get("desc_cn", ""))
        status = "✓" if not problems else f"✗ {len(problems)} 个问题"
        print(f"      {ch.get('id')} {ch.get('name')}：{status}")

    print("[2/2] 让 LLM 重写造型 …")
    changed = []
    for ch in outline.get("characters", []):
        problems = facecheck.check(ch.get("desc_cn", ""))
        if not problems:
            continue
        print(f"      重写 {ch.get('id')} {ch.get('name')} …")
        new_ch, left = fix_character(args.base, args.model, ch, problems)
        ch["desc_cn"] = new_ch["desc_cn"]
        if new_ch.get("mood"):
            ch["mood"] = new_ch["mood"]
        changed.append(ch["id"])
        for p in left:
            print(f"      ⚠ {ch['id']} 仍未通过：{p}")

    dest = Path(args.out) if args.out else path
    dest.write_text(json.dumps(outline, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"      已写 {dest}（改动 {len(changed)} 个角色）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
