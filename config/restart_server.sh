#!/usr/bin/env bash
# Restart script — kills run_server.py and relaunches detached on the internal
# port (127.0.0.1:12395), then ensures the TLS proxy owns the public port 12393.
# Order matters: backend must release 12393 before the proxy binds it.
cd "$(dirname "$0")/.."

pkill -f run_server.py 2>/dev/null
sleep 2
pkill -9 -f run_server.py 2>/dev/null

setsid /root/.local/bin/uv run run_server.py > /tmp/ollvm.log 2>&1 < /dev/null &

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
