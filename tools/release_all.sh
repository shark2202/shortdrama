#!/usr/bin/env bash
# 一键发布流水线：合成 → 配音混音 → 质检 → 封面/元数据 → 发布清单。
#
# 顺序有讲究：
#   assemble 先做（它会重烧字幕、重做响度/BGM）→ 再混人声（否则人声会被冲掉）
#   → 再质检（质检看的是最终成片）→ 最后生成封面与清单（读成片实测规格）。
#
# 用法：bash tools/release_all.sh [root] [episode ...]
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

for J in "${EPS[@]}"; do
  JOB="$ROOT/$J"
  echo "═══════ $J  assemble"
  $EPY "$HERE/shortdrama/episode.py" "$JOB" assemble 2>&1 | grep -E "^\[assemble\]|^\[qc\]|Error|Traceback" | tail -4
  echo "═══════ $J  voice"
  $EPY "$HERE/tools/make_voice.py" "$JOB" 2>&1 | grep -E "^\[voice\]|Traceback" | tail -3
  echo "═══════ $J  qc"
  $EPY "$HERE/shortdrama/episode.py" "$JOB" qc 2>&1 | grep -E "^\[qc\]" | tail -6
done

echo "═══════ 封面 / 元数据 / 发布清单"
$EPY "$HERE/tools/make_release.py" --root "$ROOT" --series D202611_001_E0 2>&1 | tail -8
