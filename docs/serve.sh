#!/usr/bin/env bash
# 一键：把 docs/ 目录作为静态文件服务（无构建、无依赖，只用 python3）。
#   bash docs/serve.sh                  默认 0.0.0.0:8091
#   PORT=8092 bash docs/serve.sh        换端口
#   BIND=127.0.0.1 bash docs/serve.sh   只本机
# 注：8090 已被 ai-2080ti22g/docs/serve.sh 占用（那份同时覆盖本方案）。
# 手册: http://<本机IP>:8091/自动化短剧平台建设方案.html
set -euo pipefail
cd "$(dirname "$0")"
exec python3 -m http.server "${PORT:-8091}" --bind "${BIND:-0.0.0.0}"
