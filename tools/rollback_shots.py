#!/usr/bin/env python3
"""回滚指定镜到 clips/bak/ 的旧片（复审发现新片更差时用）。

用法：
  python3 tools/rollback_shots.py <job-dir> s12 s26 s34
  python3 tools/rollback_shots.py <job-dir> --from-verdicts verdicts.json
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("job")
    ap.add_argument("shots", nargs="*", help="镜号，如 s12")
    ap.add_argument("--from-verdicts", default="",
                    help="verdicts.json：{\"episode\": {\"s12\": [\"回滚\", \"原因\"]}}")
    a = ap.parse_args()
    job = Path(a.job).expanduser().resolve()
    shots = list(a.shots)
    if a.from_verdicts:
        data = json.loads(Path(a.from_verdicts).read_text(encoding="utf-8"))
        for ep, v in data.items():
            if Path(ep).name == job.name or ep == job.name:
                shots += [k for k, (label, _n) in v.items() if label == "回滚"]
    if not shots:
        print("没有要回滚的镜")
        return 0
    bak = job / "clips" / "bak"
    n = 0
    for sid in shots:
        idx = int(sid[1:]) if sid.startswith("s") else int(sid)
        src = bak / f"{idx:02d}.mp4"
        dst = job / "clips" / f"{idx:02d}.mp4"
        if not src.exists():
            print(f"跳过（无旧片）{src}")
            continue
        shutil.copyfile(src, dst)
        print(f"回滚 {dst.name} ← {src}")
        n += 1
    print(f"回滚 {n} 镜（重跑 assemble 后生效）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
