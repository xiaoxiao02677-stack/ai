"""RealTransport: TCP-socket transport (Phase 16).

A REAL transport implementing the Phase-15 Transport interface —
bytes over a TCP socket to a pre-configured ESP32 endpoint. This is
deliberately the ONLY real transport in Phase 16 (no MQTT/HTTP/
WebSocket/cloud — banned by the phase spec).

Scope discipline:
- send(): opens (or reuses) a connection, writes the message bytes,
  and records TRANSPORT-level acceptance only. Transport success
  NEVER means the device executed anything (that is device_ack).
- receive(): reads a reply within a real timeout. No staged
  responses, NO default_response (the Phase-15 MockTransport
  convenience concept is structurally absent here — its LOW finding
  closes). Connection failures and timeouts surface as explicit
  exceptions; nothing is masked.

The transport moves bytes; it never interprets commands, touches
policy, or fabricates replies. Fail-closed everywhere.
"""

import socket
from typing import Optional

from .transport import Transport, TransportTimeout, TransportError


class RealTCPTransport(Transport):
    """TCP transport to one explicitly-configured ESP32 endpoint.

    The endpoint (host, port) is explicit configuration — no device
    discovery, no auto-connect to arbitrary hosts. send() and
    receive() speak newline-delimited UTF-8 frames (the ESP32 test
    firmware uses the same framing).
    """

    def __init__(self, host: str, port: int,
                 connect_timeout: float = 3.0,
                 io_timeout: float = 3.0,
                 max_frame_len: int = 65536):
        if not host or not isinstance(port, int) or not (0 < port < 65536):
            raise TransportError(
                f"invalid endpoint {host}:{port} — explicit configuration "
                f"required (no discovery)")
        self.host = host
        self.port = port
        self.connect_timeout = connect_timeout
        self.io_timeout = io_timeout
        self.max_frame_len = max_frame_len
        self._sock: Optional[socket.socket] = None
        self._buffer = b""

    # -- connection management ------------------------------------------------

    def _ensure_connected(self) -> socket.socket:
        if self._sock is None:
            try:
                sock = socket.create_connection(
                    (self.host, self.port),
                    timeout=self.connect_timeout)
                sock.settimeout(self.io_timeout)
                self._sock = sock
            except OSError as e:
                raise TransportError(
                    f"connection to {self.host}:{self.port} failed: {e}"
                ) from e
        return self._sock

    def disconnect(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None
            self._buffer = b""

    # -- Transport interface ----------------------------------------------------

    def send(self, message_id: str, message: str,
             timeout: float = 1.0) -> None:
        if not message_id or not isinstance(message_id, str):
            raise TransportError("message_id must be a non-empty string")
        if not isinstance(message, str):
            raise TransportError("message must be a string")
        frame = message.encode("utf-8")
        if len(frame) > self.max_frame_len:
            raise TransportError(
                f"message exceeds max frame length {self.max_frame_len}")
        sock = self._ensure_connected()
        try:
            # newline framing + explicit flush to the OS
            sock.sendall(frame + b"\n")
        except OSError as e:
            self.disconnect()
            raise TransportError(f"send failed: {e}") from e

    def receive(self, message_id: str,
                timeout: float = 3.0) -> Optional[str]:
        sock = self._sock
        if sock is None:
            raise TransportTimeout(
                "not connected — no reply channel (call send first)")
        deadline_remaining = float(timeout)
        try:
            # read until a newline frame is complete
            while b"\n" not in self._buffer:
                sock.settimeout(max(deadline_remaining, 0.05))
                chunk = sock.recv(4096)
                if not chunk:
                    # device closed the connection
                    self.disconnect()
                    raise TransportTimeout(
                        "device closed the connection")
                self._buffer += chunk
                if len(self._buffer) > self.max_frame_len:
                    self.disconnect()
                    raise TransportError(
                        "response exceeds max frame length — malformed")
            line, _, rest = self._buffer.partition(b"\n")
            self._buffer = rest
            return line.decode("utf-8", errors="strict")
        except socket.timeout as e:
            raise TransportTimeout(
                f"no reply within {timeout}s") from e
        except ConnectionResetError as e:
            self.disconnect()
            raise TransportError(
                f"connection reset by device: {e}") from e
        except UnicodeDecodeError as e:
            raise TransportError(
                f"malformed response bytes: {e}") from e
        except OSError as e:
            self.disconnect()
            raise TransportError(f"receive failed: {e}") from e
