#!/usr/bin/env bash
# start_all.sh — 一键启动全部页面服务（幂等：已在跑则跳过）
#
# 拓扑：
#   run_server.py  -> 127.0.0.1:12395  后端（聊天/配置面板/记忆面板/web-tool/WS/ASR/TTS）
#   tls_proxy.py   -> 0.0.0.0:12393    公网 TLS 入口（字节级转发 -> 12395）
#
# 用法：
#   bash start_all.sh          # 启动（已启动的部分自动跳过）
#   bash start_all.sh --status # 只看状态
set -u
cd "$(dirname "$0")"

GREEN='\033[0;32m'; RED='\033[0;31m'; YELLOW='\033[1;33m'; NC='\033[0m'
ok()   { echo -e "  ${GREEN}[OK]${NC} $1"; }
warn() { echo -e "  ${YELLOW}[--]${NC} $1"; }
bad()  { echo -e "  ${RED}[FAIL]${NC} $1"; }

wait_http() {  # wait_http <url> <label> [max_tries]
  local url="$1" label="$2" tries="${3:-45}" i
  local flag=""; [[ "$url" == https:* ]] && flag="-k"
  for i in $(seq 1 "$tries"); do
    if curl -s $flag -m 2 -o /dev/null "$url"; then ok "$label 就绪"; return 0; fi
    sleep 2
  done
  bad "$label $((tries * 2))s 内未就绪"; return 1
}

status_all() {
  echo "=== 服务状态 ==="
  if pgrep -f 'run_serve[r]\.py' > /dev/null; then
    ok "后端 run_server.py  运行中 (pid $(pgrep -f 'run_serve[r]\.py' | head -1))"
  else
    warn "后端未运行"
  fi
  if pgrep -f 'tls_pro[x]y\.py' > /dev/null; then
    ok "TLS 代理 tls_proxy   运行中 (pid $(pgrep -f 'tls_pro[x]y\.py' | head -1))  公网 :12393"
  else
    warn "TLS 代理未运行（公网 12393 不可达，仅本机 12395 可用）"
  fi
  echo "=== 页面可达性 ==="
  local checks=(
    "http://127.0.0.1:12395/|聊天前端(内网)"
    "http://127.0.0.1:12395/config/|配置面板(内网)"
    "http://127.0.0.1:12395/memory/|记忆面板(内网)"
    "http://127.0.0.1:12395/web-tool/index.html|Web工具(内网)"
    "https://127.0.0.1:12393/|聊天前端(公网TLS)"
    "https://127.0.0.1:12393/config/|配置面板(公网TLS)"
    "https://127.0.0.1:12393/memory/|记忆面板(公网TLS)"
  )
  for c in "${checks[@]}"; do
    local url="${c%%|*}" label="${c##*|}"
    local flag=""; [[ "$url" == https:* ]] && flag="-k"
    if curl -s $flag -m 3 -o /dev/null "$url"; then ok "$label  $url"; else warn "$label  $url 不可达"; fi
  done
}

# ---- 1. backend ----
echo "=== [1/2] 后端 (127.0.0.1:12395) ==="
if curl -s -m 2 -o /dev/null http://127.0.0.1:12395/; then
  ok "后端已在运行，跳过"
else
  echo "  启动 run_server.py ..."
  pkill -f 'run_serve[r]\.py' 2>/dev/null; sleep 1
  setsid /root/.local/bin/uv run run_server.py > /tmp/ollvm.log 2>&1 < /dev/null &
  wait_http "http://127.0.0.1:12395/" "后端" || { bad "查看日志: tail -50 /tmp/ollvm.log"; exit 1; }
fi

# ---- 2. tls proxy (public entry) ----
echo "=== [2/2] TLS 公网入口 (:12393) ==="
if curl -sk -m 2 -o /dev/null https://127.0.0.1:12393/; then
  ok "TLS 代理已在运行，跳过"
else
  echo "  启动 tls_proxy.py ..."
  bash config/tlsctl.sh start
  wait_http "https://127.0.0.1:12393/" "TLS 入口" || { bad "查看日志: tail -20 /tmp/tls_proxy.log"; exit 1; }
fi

echo ""
status_all
echo ""
IP=$(hostname -I 2>/dev/null | awk '{print $1}')
echo "一键启动完成。公网访问入口:  https://<服务器IP>:12393/   (当前服务器内网IP: ${IP:-未知})"
echo "聊天页设置中 WebSocket 地址请填:  ws://<服务器IP>:12393/client-ws"
