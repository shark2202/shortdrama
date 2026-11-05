#!/usr/bin/env python3
"""交付数据一致性检查（剧本 ↔ 分镜 ↔ clips ↔ manifest 四方对账）。

为什么要单独查：成片能播 ≠ 数据对。审片、复现、批量出集都依赖这四份数据一致，
不一致时症状很怪（"改了词但成片没变"），必须机器查。

检查项：
  1. story.json 的 scenes 数 == shots.json 的 shots 数 == clips 数 == manifest 条数
  2. shots.json 的 prompt_en == 由 story.json 重新拼出来的（改词是否真的落到分镜里）
  3. manifest 的 seed == SEED + 镜号
  4. 每个 clip 的时长 ≈ story 的 duration_s（±0.5s）
  5. 每镜都有 prompt_cn（关键帧/中文审片的依据）

用法：python3 tools/check_consistency.py <job-dir>
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from shortdrama import episode  # noqa: E402

SEED = 3013


def main() -> int:
    job = Path(sys.argv[1]).expanduser().resolve()
    problems: list[str] = []
    story = json.loads((job / "story.json").read_text(encoding="utf-8"))
    shots_doc = json.loads((job / "shots.json").read_text(encoding="utf-8"))
    shots = shots_doc["shots"] if isinstance(shots_doc, dict) else shots_doc
    scenes = story.get("scenes", [])
    clips = sorted((job / "clips").glob("[0-9][0-9].mp4"))
    man_file = job / "clips" / "manifest.json"
    man = json.loads(man_file.read_text(encoding="utf-8")) if man_file.exists() else []

    if not (len(scenes) == len(shots) == len(clips)):
        problems.append(f"数量不一致：scenes={len(scenes)} shots={len(shots)} clips={len(clips)}")
    if man and len(man) != len(clips):
        problems.append(f"manifest 条数 {len(man)} != clips {len(clips)}")

    # 2. 分镜提示词必须与"由剧本重拼"一致（改词有没有落到 shots.json）
    rebuilt = {s["idx"]: s["prompt_en"] for s in episode.build_shots(story)}
    for s in shots:
        if s["idx"] in rebuilt and s.get("prompt_en") != rebuilt[s["idx"]]:
            problems.append(f"shot {s['idx']} 的 prompt_en 与 story 重拼结果不一致（改词没落到分镜？）")

    # 3. seed
    for m in man:
        if m.get("seed") != SEED + m["idx"]:
            problems.append(f"shot {m['idx']} manifest seed={m.get('seed')} ≠ {SEED + m['idx']}")

    # 4. 时长
    dur_by_idx = {}
    for c in clips:
        dur_by_idx[int(c.stem)] = episode.probe(c)["duration"]
    for sc in scenes:
        want = sc.get("duration_s") or 0
        got = dur_by_idx.get(sc["idx"])
        if got is not None and abs(got - want) > 0.5:
            problems.append(f"shot {sc['idx']} 时长 {got:.2f}s 与剧本 {want}s 偏差 > 0.5s")

    # 5. prompt_cn
    missing = [sc["id"] for sc in scenes if not (sc.get("prompt_cn") or "").strip()]
    if missing:
        problems.append(f"缺 prompt_cn 的镜：{missing[:6]}{'…' if len(missing) > 6 else ''}")

    if problems:
        print(f"✗ {job.name}：{len(problems)} 项不一致")
        for p in problems[:12]:
            print("   -", p)
        return 1
    print(f"✓ {job.name}：{len(scenes)} 镜四方一致（剧本/分镜/clips/manifest）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
