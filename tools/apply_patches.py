#!/usr/bin/env python3
"""应用"审片 → 改词/补字幕"补丁，并把要重出的镜从 clips 里摘掉。

为什么要这个：审片结论要能落到数据上，还要横屏/竖屏两套 job 同步；
手工改 story.json 一定漏。这里做三件事，一步步可回滚（先备份）：
  1. 改 prompt_en / prompt_cn（重出镜）
  2. 补 subtitle_cn（E01 字幕覆盖不足）
  3. 删掉受影响的 clips/NN.mp4（渲染阶段会自动只补这些镜）

用法：
  python3 tools/apply_patches.py <fixes.json> [--apply]        # 预览
  python3 tools/apply_patches.py <fixes.json> --apply         # 写入
  python3 tools/apply_patches.py <fixes.json> --apply --sub <subtitles.json>
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path

JOBS = Path("/home/a/ai-work/duanju")


def variants(ep: str) -> list[str]:
    """一个集的横屏 + 竖屏（_P）变体。"""
    out = [ep]
    p = JOBS / f"{ep}_P"
    if p.is_dir():
        out.append(f"{ep}_P")
    return out

def load(p: Path) -> dict:
    return json.loads(p.read_text(encoding="utf-8"))


PLACEHOLDER = re.compile(r"^[\s。，、…\.\?!？！]*$")


def is_placeholder(text: str) -> bool:
    """空或只有省略号/标点的字幕 = 占位（烧出来观众只看到三个点）。"""
    return not (text or "").strip() or bool(PLACEHOLDER.match(text.strip()))


def dump(p: Path, d) -> None:
    p.write_text(json.dumps(d, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("fixes", help="shot_fixes.json")
    ap.add_argument("--sub", default="", help="字幕补写 json（{scene_id: 字幕}）")
    ap.add_argument("--apply", action="store_true", help="真改（默认预览）")
    a = ap.parse_args()

    data = load(Path(a.fixes))
    fixes = data.get("fixes", {})
    subdata = load(Path(a.sub)).get("suggestions", {}) if a.sub else {}

    def subs_for(ep_name: str) -> dict:
        """字幕表可以是 {集: {镜: 字幕}} 或 {镜: 字幕}。"""
        v = subdata.get(ep_name)
        if isinstance(v, dict):
            return v
        return {} if any(isinstance(x, dict) for x in subdata.values()) else subdata

    plan = []
    for ep, scenes in fixes.items():
        for name in variants(ep):
            job = JOBS / name
            sf = job / "story.json"
            if not sf.exists():
                print(f"跳过（不存在）：{job}")
                continue
            story = load(sf)
            subs = subs_for(name)
            changed = []
            for sc in story.get("scenes", []):
                sid = sc.get("id")
                fx = scenes.get(sid)
                if fx:
                    for k in ("prompt_en", "prompt_cn"):
                        if fx.get(k) and sc.get(k) != fx[k]:
                            sc[k] = fx[k]
                            changed.append(f"{sid}.{k}")
                # 只覆盖占位字幕（空 / 只有省略号），不碰已经写好的
                if sid in subs and is_placeholder(sc.get("subtitle_cn", "")):
                    sc["subtitle_cn"] = subs[sid]
                    changed.append(f"{sid}.subtitle_cn")
            if not changed:
                continue
            plan.append((job, story, changed, sorted(scenes.keys())))

    for job, story, changed, shots in plan:
        print(f"{job.name}: 改 {len(changed)} 处 → {', '.join(changed[:8])}{'…' if len(changed) > 8 else ''}")
    if not a.apply:
        print("[patch] 预览模式，未改动。加 --apply 写入。")
        return 0

    for job, story, changed, shots in plan:
        sf = job / "story.json"
        shutil.copyfile(sf, sf.with_suffix(".json.bak"))
        dump(sf, story)
        # 摘掉要重出的镜（横竖屏都摘）
        for sid in shots:
            idx = int(sid[1:])
            for clip in (job / "clips").glob(f"{idx:02d}.mp4"):
                clip.unlink()
                print(f"  摘掉待重出镜头 {clip.relative_to(job)}")
        # 重新生成 shots.json（提示词变了）
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from shortdrama import episode
        episode.stage_shots(job)
        print(f"  {job.name}: story.json 已更新（备份 story.json.bak），shots.json 已重建")
    print("[patch] 接着跑：episode.py <job> render → assemble → qc")
    return 0


if __name__ == "__main__":
    sys.exit(main())
