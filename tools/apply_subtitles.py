#!/usr/bin/env python3
"""把字幕补写建议应用到某一集（默认只写 dry-run 预览）。

用法：
  python3 tools/apply_subtitles.py <job-dir> <suggestions.json>          # 预览
  python3 tools/apply_subtitles.py <job-dir> <suggestions.json> --apply  # 真改

为什么要有这个：补字幕是内容决策，agent 给候选、人拍板；
采纳时必须**只改 subtitle_cn**，不动其它字段（剧情/提示词/角色），
且可回滚（先备份 story.json → story.json.bak）。
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="应用字幕补写建议")
    ap.add_argument("job", help="集目录（含 story.json）")
    ap.add_argument("suggestions", help="建议 JSON（suggestions 字段：{scene_id: 字幕}）")
    ap.add_argument("--apply", action="store_true", help="真改（默认只预览）")
    a = ap.parse_args(argv[1:])

    job = Path(a.job).expanduser().resolve()
    story_file = job / "story.json"
    data = json.loads(Path(a.suggestions).read_text(encoding="utf-8"))
    sug = data.get("suggestions", data)

    story = json.loads(story_file.read_text(encoding="utf-8"))
    changes = []
    for sc in story.get("scenes", []):
        sid = sc.get("id")
        if sid in sug and not (sc.get("subtitle_cn") or "").strip():
            changes.append((sid, sug[sid]))

    for sid, text in changes:
        print(f"  {sid}: + {text}")
    eff = len({(sc.get('subtitle_cn') or '').strip() for sc in story['scenes'] if (sc.get('subtitle_cn') or '').strip()})
    total = len(story.get("scenes", []))
    print(f"[apply] 将新增 {len(changes)} 条 → 有效字幕 {eff}/{total} → {eff+len(changes)}/{total}"
          f"（{(eff+len(changes))/total*100:.0f}%）")
    if not a.apply:
        print("[apply] 预览模式，未改动。加 --apply 才写入。")
        return 0

    for sc in story["scenes"]:
        sid = sc.get("id")
        if sid in sug and not (sc.get("subtitle_cn") or "").strip():
            sc["subtitle_cn"] = sug[sid]
    shutil.copyfile(story_file, story_file.with_suffix(".json.bak"))
    story_file.write_text(json.dumps(story, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"[apply] 已写入 {story_file}（备份 {story_file.name}.bak）")
    print("[apply] 接着跑：episode.py <job> shots assemble qc")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
