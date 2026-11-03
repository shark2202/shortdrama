#!/usr/bin/env bash
# 3 集全流程：shots → render → assemble → qc，串行（单卡只能串行）。
#
#   bash tools/run_all_episodes.sh [剧集根目录]
#
# 断点续跑：render 阶段按 clips/NN.mp4 跳过已存在的镜头；已完成的集
# 会因为 clips/ 齐全而秒过，不会重渲。
set -uo pipefail

ROOT="${1:-/home/a/ai-work/workspace/duanju}"
SERIES="${SERIES:-D202611_001}"
EPISODES="${EPISODES:-E01 E02 E03}"
PY=/home/a/ai-work/.venv/bin/python
DRIVER="$(cd "$(dirname "$0")/.." && pwd)/shortdrama/episode.py"

mkdir -p "$ROOT/logs"
SUMMARY="$ROOT/logs/run_summary.txt"
: > "$SUMMARY"

for ep in $EPISODES; do
    JOB="$ROOT/${SERIES}_${ep}"
    [ -d "$JOB" ] || { echo "跳过 $JOB（不存在）"; continue; }
    LOG="$JOB/logs/run.log"
    mkdir -p "$JOB/logs"
    echo "=== ${SERIES}_${ep} 开始 $(date '+%F %T') ===" | tee -a "$LOG"
    for stage in shots render assemble qc; do
        echo "--- stage: $stage ---" | tee -a "$LOG"
        "$PY" "$DRIVER" "$JOB" "$stage" >>"$LOG" 2>&1
        rc=$?
        echo "[$ep/$stage] rc=$rc $(date '+%T')" | tee -a "$SUMMARY"
        if [ $rc -ne 0 ]; then
            echo "[$ep] $stage 失败（rc=$rc），该集停止，进下一集" | tee -a "$SUMMARY"
            break
        fi
    done
    echo "=== ${SERIES}_${ep} 结束 $(date '+%F %T') ===" | tee -a "$LOG"
done

echo "=== 汇总 ===" | tee -a "$SUMMARY"
cat "$SUMMARY"
