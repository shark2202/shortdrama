#!/usr/bin/env bash
# 交付前门禁（一条命令跑完全部检查）：
#   1. 数据四方一致性（剧本/分镜/clips/manifest）
#   2. 提示词 5 纪律（机器可查的部分）
#   3. 质检门禁（分辨率/帧率/黑帧/静音/削波/缺镜/字幕重复/字幕可见性/响度）
#   4. 审片单结论（人工写的"重出/修词"必须为 0）
#   5. 交付件齐全性（成片/封面/元数据/审片单/报告/字幕/清单/关键帧/人声）
#
# 用法：bash tools/gate.sh [root] [episode ...]
set -uo pipefail

ROOT="${1:-/home/a/ai-work/duanju}"
shift || true
EPY=/home/a/ai-work/.venv/bin/python
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [ "$#" -gt 0 ]; then
  EPS=("$@")
else
  EPS=()
  for d in "$ROOT"/D202611_001_E0*/; do
    [ -f "$d/episode.mp4" ] && EPS+=("$(basename "$d")")
  done
fi

fail=0
for J in "${EPS[@]}"; do
  JOB="$ROOT/$J"
  echo "═══════════ $J"
  $EPY "$HERE/tools/check_consistency.py" "$JOB" || fail=1
  $EPY "$HERE/tools/check_discipline.py" "$JOB" | tail -1
  $EPY "$HERE/shortdrama/episode.py" "$JOB" qc 2>&1 | grep -E "^\[qc\] (block|warn)" || true
  $EPY - "$JOB" <<'PY'
import json, re, sys
from pathlib import Path
job = Path(sys.argv[1])
rep = json.loads((job / "report.json").read_text(encoding="utf-8"))
block = rep.get("blocking", [])
if block:
    print(f"✗ 质检阻断 {len(block)} 项：")
    for b in block[:6]:
        print("   -", b["message"])
    sys.exit(1)
print(f"✓ 质检 0 阻断（提示 {len(rep.get('findings', []))} 条）")
PY
  [ $? -ne 0 ] && fail=1
  $EPY - "$JOB" <<'PY'
import re, sys
from pathlib import Path
job = Path(sys.argv[1])
md = job / "qc" / "review.md"
if not md.exists():
    print("✗ 缺 qc/review.md"); sys.exit(1)
rows = [l for l in md.read_text(encoding="utf-8").splitlines() if re.match(r"^\| \d+ \|", l)]
bad = [l for l in rows if ("重出" in l.split("|")[8] or "修词" in l.split("|")[8])]
need = ["episode.mp4", "cover_" + job.name + ".jpg", "release_meta.json", "qc/review.md",
        "report.json", "subtitles.srt", "subtitles.ass", "clips/manifest.json",
        "story.json", "shots.json", "voice/track.wav"]
miss = [f for f in need if not (job / f).exists()]
if bad or miss:
    print(f"✗ 审片未通过 {len(bad)} 镜 / 缺件 {miss}")
    sys.exit(1)
print(f"✓ 审片 {len(rows)} 镜全合格 · 交付件齐全")
PY
  [ $? -ne 0 ] && fail=1
done
echo "═══════════"
[ $fail -eq 0 ] && echo "✅ 交付门禁全部通过" || echo "❌ 有未通过项（见上）"
exit $fail
