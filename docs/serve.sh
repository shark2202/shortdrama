#!/usr/bin/env bash
# 一键：把 docs/ 目录作为静态文件服务（无构建、无依赖，只用 python3）。
#   bash docs/serve.sh                  默认 0.0.0.0:8090
#   PORT=8091 bash docs/serve.sh        换端口
#   BIND=127.0.0.1 bash docs/serve.sh   只本机
# 手册: http://<本机IP>:8090/自动化短剧平台建设方案.html
set -euo pipefail
cd "$(dirname "$0")"
exec python3 -m http.server "${PORT:-8090}" --bind "${BIND:-0.0.0.0}"
