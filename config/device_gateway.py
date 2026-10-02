"""YH Workshop device gateway — TCP :3333 INBOUND listener.

For client-mode firmware (the standard Xiaozhi firmware layout): the
DEVICE connects to the SERVER. This gateway accepts those connections,
registers each device into the workshop runtime (P17 session + P18
observer), and exposes a queue-backed channel per connection so the
P19-R validation sequence (p19r_service.validate_device) can run over
an inbound link.

The gateway is TRANSPORT-ONLY + session registration:
  - never executes LED commands on its own (the full P3-P19 chain is
    the only execution path, driven by workshop_panel /led)
  - read/forward only: hello + advertisement parsed to register;
    heartbeats refresh last_seen; commands/acks are NOT answered here
    (device-directed traffic belongs to validate_device over the
    channel or to the future adapter wiring)
"""

import json
import socket
import threading
import time
from typing import Any, Dict, Optional

try:
    from src.open_llm_vtuber.device_protocol import (  # noqa: E402
        DeviceHello, CapabilityAdvertisement, DeviceSessionError)
    from src.open_llm_vtuber.device_protocol.p19r_service import (
        XiaozhiCodec)  # noqa: E402
except ImportError:
    from open_llm_vtuber.device_protocol import (  # type: ignore
        DeviceHello, CapabilityAdvertisement, DeviceSessionError)
    from open_llm_vtuber.device_protocol.p19r_service import (  # type: ignore
        XiaozhiCodec)

HEARTBEAT_MAX_AGE_S = 30.0


class GatewayChannel:
    """Duck-typed DeviceConnection for an inbound link.

    send(): push an outbound frame (queue polled by the connection's
    sender thread). read_frames()/wait_ack(): pop frames the reader
    thread has enqueued. The channel's close() only marks closed — the
    owning connection owns the socket lifecycle.
    """

    def __init__(self):
        self._in = []            # device -> us (reader appends)
        self._out = []           # us -> device (sender drains)
        self._cv = threading.Condition()
        self.closed = False

    def send(self, obj_or_bytes):
        if isinstance(obj_or_bytes, bytes):
            data = obj_or_bytes
        else:
            data = (json.dumps(obj_or_bytes, ensure_ascii=False)
                    + "\n").encode()
        if not data.endswith(b"\n"):
            data += b"\n"
        with self._cv:
            if self.closed:
                raise OSError("channel closed")
            self._out.append(data)
            self._cv.notify_all()

    def _feed_in(self, frame: dict):
        with self._cv:
            self._in.append(frame)
            self._cv.notify_all()

    def read_frames(self, idle_wait=1.0, max_frames=8, hard_deadline=6.0):
        frames = []
        deadline = time.time() + hard_deadline
        with self._cv:
            while len(frames) < max_frames and time.time() < deadline:
                if self._in:
                    frames.append(self._in.pop(0))
                    continue
                remain = deadline - time.time()
                if remain <= 0:
                    break
                self._cv.wait(min(0.2, remain))
        return frames

    def wait_ack(self, timeout=6.0):
        deadline = time.time() + timeout
        with self._cv:
            while time.time() < deadline:
                for i, doc in enumerate(self._in):
                    ack = XiaozhiCodec.parse_ack(doc)
                    if ack is not None:
                        del self._in[i]
                        return ack
                remain = deadline - time.time()
                if remain <= 0:
                    break
                self._cv.wait(min(0.2, remain))
        return None

    def close(self):
        with self._cv:
            self.closed = True
            self._cv.notify_all()

    # -- sender-side drain (called by the owning connection) --
    def pop_out(self, timeout_s=0.2):
        with self._cv:
            if self._out:
                return self._out.pop(0)
            self._cv.wait(timeout_s)
            if self._out:
                return self._out.pop(0)
        return None


class DeviceGateway(threading.Thread):
    """TCP :3333 inbound listener with per-connection reader/sender.

    on_device_attach(hello, adv, ip, port): callback into the workshop
    runtime (workshop_panel wires it to _attach_device)."""

    def __init__(self, host: str = "0.0.0.0", port: int = 3333,
                 on_device_attach=None, on_device_detach=None,
                 on_frame=None):
        super().__init__(daemon=True, name="yhw-device-gateway")
        self.host = host
        self.port = port
        self.on_device_attach = on_device_attach
        self.on_device_detach = on_device_detach   # (device_id) -> None
        # on_frame(device_id, doc): refresh session liveness per frame
        self.on_frame = on_frame
        self._srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.channels: Dict[str, GatewayChannel] = {}
        self.conns: Dict[str, socket.socket] = {}
        self._lock = threading.Lock()
        self.started_at: Optional[float] = None

    def run(self):
        try:
            self._srv.bind((self.host, self.port))
            self._srv.listen(8)
        except OSError as e:
            print(f"[yhw-gateway] bind {self.host}:{self.port} failed: {e}")
            return
        self.started_at = time.time()
        print(f"[yhw-gateway] listening on {self.host}:{self.port}")
        while True:
            try:
                sock, addr = self._srv.accept()
            except OSError:
                break
            sock.settimeout(2.0)
            threading.Thread(target=self._handle, args=(sock, addr),
                             daemon=True).start()

    def _handle(self, sock: socket.socket, addr):
        chan = GatewayChannel()
        sock.settimeout(2.0)

        def sender():
            while True:
                data = chan.pop_out(0.3)
                if data is None:
                    if chan.closed:
                        break
                    continue
                try:
                    sock.sendall(data)
                except OSError:
                    break

        threading.Thread(target=sender, daemon=True).start()

        device_id = None
        buf = b""
        try:
            # phase 1: read HELLO + ADVERTISEMENT (register the device)
            _adv_seen = False
            while device_id is None or not _adv_seen:
                try:
                    chunk = sock.recv(4096)
                except socket.timeout:
                    continue
                if not chunk:
                    break
                buf += chunk
                while b"\n" in buf:
                    line, _, buf = buf.partition(b"\n")
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        doc = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if doc.get("type") == "HELLO" or \
                            doc.get("message_type") == "device_hello":
                        try:
                            hello, wire_sess = \
                                XiaozhiCodec.parse_hello(doc)
                        except DeviceSessionError:
                            continue
                        if device_id is None:
                            device_id = hello.device_id
                            adv = None
                            with self._lock:
                                self.channels[device_id] = chan
                                self.conns[device_id] = sock
                            # register once we have the advertisement too
                            _pending_hello = hello
                            _pending_wire = wire_sess
                    elif (device_id is not None
                          and (doc.get("type") == "ADVERTISEMENT"
                               or doc.get("message_type")
                               == "capability_advertisement")):
                        try:
                            adv = XiaozhiCodec.parse_advertisement(
                                doc, device_id)
                        except DeviceSessionError:
                            adv = None
                        if self.on_device_attach is not None:
                            self.on_device_attach(
                                _pending_hello, adv, addr[0], addr[1],
                                _pending_wire)
                        _adv_seen = True
                        break
            # phase 2: pass frames into the channel (heartbeats refresh)
            while True:
                try:
                    chunk = sock.recv(4096)
                except socket.timeout:
                    continue
                if not chunk:
                    break
                buf += chunk
                while b"\n" in buf:
                    line, _, buf = buf.partition(b"\n")
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        doc = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    chan._feed_in(doc)
                    if self.on_frame is not None and device_id is not None:
                        try:
                            self.on_frame(device_id, doc)
                        except Exception:
                            pass
        except OSError:
            pass
        finally:
            with self._lock:
                if device_id is not None:
                    self.channels.pop(device_id, None)
                    self.conns.pop(device_id, None)
            if device_id is not None and self.on_device_detach is not None:
                try:
                    self.on_device_detach(device_id)
                except Exception:
                    pass
            chan.close()
            try:
                sock.close()
            except OSError:
                pass

    def get_channel(self, device_id: str) -> Optional[GatewayChannel]:
        with self._lock:
            return self.channels.get(device_id)

    def get_peer(self, device_id: str) -> Optional[tuple]:
        with self._lock:
            sock = self.conns.get(device_id)
        if sock is None:
            return None
        try:
            return sock.getpeername()
        except OSError:
            return None

    def stop(self):
        try:
            self._srv.close()
        except OSError:
            pass
        with self._lock:
            for chan in self.channels.values():
                chan.close()
