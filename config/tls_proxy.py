#!/usr/bin/env python3
"""
TLS termination proxy for Open-LLM-VTuber (config/tls_proxy.py)
================================================================
Single public port (:12393) for everything:

  * TLS traffic  -> byte-level pipe to the backend on 127.0.0.1:12395.
                    Works for HTTP and WebSocket alike (nothing is parsed).
  * plain HTTP   -> 301 redirect to https://<same host><path>, so machines
                    that still type http:// land on the secure page instead
                    of a confusing "empty reply from server".

The backend itself must NOT bind the public port anymore - it listens on
127.0.0.1:12395 (conf.yaml: system_config.host / system_config.port).

Cert is self-signed, auto-generated on first start (config/tls/). Browsers
warn once; accept to continue. TLS unlocks getUserMedia (mic) and
getDisplayMedia (screen share), which require a secure context.

Usage (on the server, from the project root):
    setsid python3 config/tls_proxy.py > /tmp/tls_proxy.log 2>&1 < /dev/null &
Or via config/tlsctl.sh start|stop|restart|status
"""

import os
import socket
import ssl
import subprocess
import sys
import threading
from pathlib import Path

LISTEN_HOST = os.environ.get("TLS_PROXY_LISTEN", "0.0.0.0")
LISTEN_PORT = int(os.environ.get("TLS_PROXY_PORT", "12393"))
UPSTREAM_HOST = os.environ.get("TLS_PROXY_UPSTREAM", "127.0.0.1")
UPSTREAM_PORT = int(os.environ.get("TLS_PROXY_UPSTREAM_PORT", "12395"))
CERT_DIR = Path(__file__).resolve().parent / "tls"
CERT_FILE = CERT_DIR / "cert.pem"
KEY_FILE = CERT_DIR / "key.pem"

SSL_CTX = None


def log(*args):
    print("[tls-proxy]", *args, flush=True)


def lan_ip():
    try:
        return (
            subprocess.run(
                ["hostname", "-I"], capture_output=True, text=True, timeout=5
            )
            .stdout.split()[0]
            .strip()
        )
    except Exception:
        return "127.0.0.1"


def ensure_cert():
    CERT_DIR.mkdir(parents=True, exist_ok=True)
    if CERT_FILE.exists() and KEY_FILE.exists():
        return
    ip = lan_ip()
    log("generating self-signed cert for /CN=" + ip)
    subprocess.run(
        [
            "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
            "-keyout", str(KEY_FILE),
            "-out", str(CERT_FILE),
            "-days", "3650",
            "-subj", f"/CN={ip}",
            "-addext", f"subjectAltName=IP:{ip},DNS:localhost",
        ],
        check=True,
        capture_output=True,
    )
    log("cert written to", CERT_DIR)


def build_ssl_ctx():
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(str(CERT_FILE), str(KEY_FILE))
    return ctx


def nodelay(sock):
    try:
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    except OSError:
        pass


def close_quietly(sock):
    try:
        sock.close()
    except OSError:
        pass


def pipe(src, dst):
    try:
        while True:
            data = src.recv(65536)
            if not data:
                break
            dst.sendall(data)
    except socket.timeout:
        # Should not happen once connect timeouts are cleared; keep it loud.
        log("pipe idle timeout on", src.getsockname(), "-> killing connection")
    except OSError:
        pass
    finally:
        for s in (src, dst):
            try:
                s.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass


def connect_upstream():
    try:
        up = socket.create_connection((UPSTREAM_HOST, UPSTREAM_PORT), timeout=10)
        # create_connection leaves `timeout` on the socket as a recv/send
        # timeout. For the long-lived pipe this meant: any connection idle
        # >10s died silently (WS disconnects). Clear it back to blocking.
        up.settimeout(None)
        nodelay(up)
        return up
    except OSError as e:
        log("upstream connect failed:", e)
        return None


def redirect_response(head: bytes) -> bytes:
    """Build a 301-to-https response for a plain-HTTP request head."""
    path = "/"
    host = None
    for i, raw in enumerate(head.split(b"\r\n")):
        if i == 0:
            parts = raw.split()
            if len(parts) >= 2:
                target = parts[1].decode(errors="replace")
                # absolute-form (http://host/path) -> origin-form
                if target.startswith("http://") or target.startswith("https://"):
                    rest = target.split("://", 1)[1]
                    slash = rest.find("/")
                    target = rest[slash:] if slash >= 0 else "/"
                path = target or "/"
        elif raw.lower().startswith(b"host:"):
            host = raw.split(b":", 1)[1].strip().decode(errors="replace")
    if not host:
        host = f"{lan_ip()}:{LISTEN_PORT}"
    return (
        "HTTP/1.1 301 Moved Permanently\r\n"
        f"Location: https://{host}{path}\r\n"
        "Content-Length: 0\r\n"
        "Connection: close\r\n\r\n"
    ).encode()


def handle_plain_http(client):
    """Plain HTTP hit the TLS port: answer with a 301 to https."""
    try:
        client.settimeout(10)
        head = b""
        while b"\r\n\r\n" not in head and len(head) < 65536:
            chunk = client.recv(4096)
            if not chunk:
                break
            head += chunk
        client.sendall(redirect_response(head))
    except OSError:
        pass
    finally:
        close_quietly(client)


def handle_tls(client):
    try:
        client.settimeout(30)
        tls = SSL_CTX.wrap_socket(client, server_side=True)
    except (ssl.SSLError, OSError) as e:
        log("tls handshake failed:", e)
        close_quietly(client)
        return
    tls.settimeout(None)
    nodelay(tls)
    up = connect_upstream()
    if up is None:
        close_quietly(tls)
        return
    t1 = threading.Thread(target=pipe, args=(tls, up), daemon=True)
    t2 = threading.Thread(target=pipe, args=(up, tls), daemon=True)
    t1.start()
    t2.start()
    t1.join()
    t2.join()
    close_quietly(tls)
    close_quietly(up)


def handle(client):
    try:
        client.settimeout(15)
        first = client.recv(1, socket.MSG_PEEK)
    except OSError:
        close_quietly(client)
        return
    if first == b"\x16":  # TLS ClientHello
        handle_tls(client)
    elif first:  # looks like plain HTTP
        handle_plain_http(client)
    else:
        close_quietly(client)


def main():
    global SSL_CTX
    ensure_cert()
    SSL_CTX = build_ssl_ctx()
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((LISTEN_HOST, LISTEN_PORT))
    srv.listen(128)
    log(
        f"https://{LISTEN_HOST}:{LISTEN_PORT} -> {UPSTREAM_HOST}:{UPSTREAM_PORT} "
        "(plain http on this port gets 301 to https)"
    )
    while True:
        conn, addr = srv.accept()
        nodelay(conn)
        threading.Thread(target=handle, args=(conn,), daemon=True).start()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
