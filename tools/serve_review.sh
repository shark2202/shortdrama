#!/usr/bin/env bash
# 把审片台做成 systemd user service（独立于任何会话，开机自启，崩了自动重启）。
#
# 为什么需要：之前是手工 nohup 起的 → 会话结束/重启就没了；审片是"随时要能用"的东西，
# 必须是常驻服务（systemd user unit + Linger=yes → 不登录也在）。
#
# 用法：
#   bash tools/serve_review.sh install     # 安装并启动
#   bash tools/serve_review.sh status     # 看状态
#   bash tools/serve_review.sh url        # 打印访问 URL
#   bash tools/serve_review.sh stop       # 停
#
# 令牌：固定存在 ~/.config/shortdrama/studio.token（600），重启后 URL 不变。
set -euo pipefail

# systemd user bus 在 SSH 会话里通常没有环境变量 → 自己补上
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
export DBUS_SESSION_BUS_ADDRESS="unix:path=${XDG_RUNTIME_DIR}/bus"

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UNIT_DIR="$HOME/.config/systemd/user"
UNIT="$UNIT_DIR/shortdrama-studio.service"
CONF_DIR="$HOME/.config/shortdrama"
TOKEN_FILE="$CONF_DIR/studio.token"
PY=/home/a/ai-work/.venv/bin/python
ROOT=${SHORTDRAMA_ROOT:-/home/a/ai-work/duanju}
PORT=${SHORTDRAMA_PORT:-8095}
SERIES=${SHORTDRAMA_SERIES:-auto}
HOST=${SHORTDRAMA_HOST:-0.0.0.0}

ensure_token() {
  mkdir -p "$CONF_DIR"
  if [ ! -s "$TOKEN_FILE" ]; then
    head -c 12 /dev/urandom | base64 | tr -dc 'a-zA-Z0-9' | head -c 10 > "$TOKEN_FILE"
    chmod 600 "$TOKEN_FILE"
  fi
}

write_unit() {
  mkdir -p "$UNIT_DIR"
  ensure_token
  TOKEN="$(cat "$TOKEN_FILE")"
  cat > "$UNIT" <<EOF
[Unit]
Description=短剧审片台（任务看板 / 审片 / 放映 / 交付审批）
After=network.target

[Service]
Type=simple
WorkingDirectory=$HERE
ExecStart=$PY $HERE/sd studio $ROOT --series $SERIES --host $HOST --port $PORT --token $TOKEN
Restart=always
RestartSec=5
# 审片台只读文件 + 写决定文件，不需要 GPU
Nice=5

[Install]
WantedBy=default.target
EOF
  systemctl --user daemon-reload
}

case "${1:-install}" in
  install)
    write_unit
    systemctl --user enable --now shortdrama-studio.service
    sleep 2
    systemctl --user --no-pager status shortdrama-studio.service | head -12
    echo
    bash "$0" url
    ;;
  status)
    systemctl --user --no-pager status shortdrama-studio.service | head -12
    ;;
  url)
    K="$(cat "$TOKEN_FILE" 2>/dev/null || echo 无令牌)"
    echo "本机   http://127.0.0.1:${PORT}/"
    for ip in $(hostname -I 2>/dev/null); do
      case "$ip" in 127.*) ;; *) echo "局域网 http://${ip}:${PORT}/?k=${K}" ;; esac
    done
    ;;
  stop)
    systemctl --user stop shortdrama-studio.service
    echo "已停"
    ;;
  logs)
    journalctl --user -u shortdrama-studio.service -n "${2:-40}" --no-pager
    ;;
  *)
    echo "用法：$0 {install|status|url|stop|logs}" >&2
    exit 1
    ;;
esac
