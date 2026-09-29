#!/usr/bin/env bash
# TLS proxy control script (config/tlsctl.sh)
# start | stop | restart | status
cd "$(dirname "$0")"

start() {
  if pgrep -f 'tls_pro[x]y\.py' > /dev/null; then
    echo "already running"
    return 0
  fi
  setsid python3 tls_proxy.py > /tmp/tls_proxy.log 2>&1 < /dev/null &
  sleep 1.5
  if pgrep -f 'tls_pro[x]y\.py' > /dev/null; then
    echo "started, pid=$(pgrep -f 'tls_pro[x]y\.py' | head -1)"
  else
    echo "FAILED to start, log:"
    cat /tmp/tls_proxy.log 2>/dev/null
    return 1
  fi
}

stop() {
  pkill -f 'tls_pro[x]y\.py' 2>/dev/null
  sleep 0.5
  echo "stopped"
}

case "$1" in
  start) start ;;
  stop) stop ;;
  restart) stop; start ;;
  status)
    if pgrep -f 'tls_pro[x]y\.py' > /dev/null; then
      echo "running, pid=$(pgrep -f 'tls_pro[x]y\.py' | head -1)"
      ss -tlnp 2>/dev/null | grep ':12393 '
    else
      echo "not running"
    fi
    ;;
  *) echo "usage: $0 start|stop|restart|status" >&2; exit 1 ;;
esac
