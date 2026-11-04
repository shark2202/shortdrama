#!/usr/bin/env python3
"""把审片结论写回各集的 qc/review.md（结论列 + 备注列）。

为什么：审片的**结论**必须落在文件里（人能直接读、能 diff、能追溯），
而不是停在对话里。审片依据是**成片抽帧**，不是关键帧。

用法：
  python3 tools/write_verdicts.py <verdicts.json>
verdicts.json：
  {"D202611_001_E01": {"s1": ["合格", "已改词重出：照片无可读文字"], "s10": ["合格", ""]}}
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

JOBS = Path("/home/a/ai-work/duanju")


def main() -> int:
    data = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    for ep, verdicts in data.items():
        md = JOBS / ep / "qc" / "review.md"
        if not md.exists():
            print(f"跳过（没有 {md}）")
            continue
        out = []
        n = 0
        for ln in md.read_text(encoding="utf-8").splitlines():
            m = re.match(r"^\| (\d+) \| ", ln)
            if m:
                sid = "s" + m.group(1)
                v = verdicts.get(sid)
                if v:
                    label, note = (v + [""])[:2]
                    ln = re.sub(r"\| [^|]* \| [^|]* \|\s*$", f"| **{label}** | {note} |", ln)
                    n += 1
            out.append(ln)
        md.write_text("\n".join(out) + "\n", encoding="utf-8")
        print(f"{ep}: 写入 {n} 条结论 → {md}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
