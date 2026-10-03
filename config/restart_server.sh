#!/usr/bin/env bash
# Restart script — kills run_server.py and relaunches detached on the internal
# port (127.0.0.1:12395), then ensures the TLS proxy owns the public port 12393.
# Order matters: backend must release 12393 before the proxy binds it.
cd "$(dirname "$0")/.."

# user-agnostic restart: always run the server as the project owner
# (uu), no matter who invokes this script (root ssh, cron, panel).
if [ "$(id -un)" != "uu" ]; then
  exec su - uu -c "cd '/home/uu/桌面/Open-LLM-VTuber' && bash config/restart_server.sh"
fi

pkill -f run_server.py 2>/dev/null
sleep 2
pkill -9 -f run_server.py 2>/dev/null

UV_BIN=/root/.local/bin/uv
[ -x "$UV_BIN" ] || UV_BIN=/opt/uv-public/uv
setsid "$UV_BIN" run run_server.py > /tmp/ollvm.log 2>&1 < /dev/null &

# wait for the backend to come up on the internal port
for i in $(seq 1 20); do
  if curl -s -o /dev/null --max-time 2 http://127.0.0.1:12395/; then
    break
  fi
  sleep 1
done

# restart the TLS proxy so it binds 12393 fresh
bash config/tlsctl.sh restart > /dev/null 2>&1 || true

echo "restarted at $(date)" >> /tmp/ollvm-restart.log
