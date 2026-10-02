"""P19-R Xiaozhi Real-Device Validation Console (server-side CLI).

THIN SHELL since the workshop extraction: the protocol implementation
(codec + connection + validation sequence) lives in
open_llm_vtuber/device_protocol/p19r_service.py — this CLI and the
YH Workshop web UI are two ENTRY POINTS over that ONE implementation.

Usage:
  uv run python tools/p19r_console.py --host <esp32-ip> [--port 3333]
      [--timeout 6] [--verbose] [--device-id <id>] [--yes] [--src PATH]
"""
import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")


def main():
    parser = argparse.ArgumentParser(
        description="P19-R Xiaozhi real-device validation console")
    parser.add_argument("--host", required=True,
                        help="Xiaozhi ESP32 IP address")
    parser.add_argument("--port", type=int, default=3333)
    parser.add_argument("--timeout", type=float, default=6.0,
                        help="per-read timeout seconds (default 6)")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="verbose/debug output")
    parser.add_argument("--device-id", default=None,
                        help="expected device_id (default: accept HELLO's)")
    parser.add_argument("--yes", action="store_true",
                        help="skip interactive physical-LED confirmation")
    parser.add_argument("--src", default=os.environ.get(
        "LTM_P19R_SRC", os.path.join(ROOT, "src")))
    args = parser.parse_args()

    sys.path.insert(0, os.path.abspath(args.src))
    from open_llm_vtuber.device_protocol.p19r_service import validate_device

    print("P19-R Xiaozhi Real-Device Validation Console")
    print(f"  target : {args.host}:{args.port}  timeout={args.timeout}s")

    ok_count = 0
    fail_count = 0
    failed_names = []

    gen = validate_device(args.host, args.port, args.timeout,
                          expected_device_id=args.device_id,
                          src=args.src)
    to_send = None
    try:
        while True:
            event = gen.send(to_send)
            to_send = None
            kind = event["kind"]
            if kind == "section":
                print(f"\n== {event['title']} ==")
            elif kind == "check":
                if event["ok"]:
                    ok_count += 1
                    print(f"  [OK  ] {event['name']}"
                          + (f"  ({event.get('detail', '')})"
                             if event.get("detail") else ""))
                else:
                    fail_count += 1
                    failed_names.append(event["name"])
                    print(f"  [FAIL] {event['name']}"
                          + (f"  ({event.get('detail', '')})"
                             if event.get("detail") else ""))
            elif kind == "info":
                print(f"  {event['text']}")
            elif kind == "device":
                st = event["state"]
                print(f"  device_id           : {st['device_id']}")
                print(f"  session_id (server) : {st['session_id']}")
                print(f"  protocol_version    : {st['protocol_version']}")
                print(f"  connection_state    : {st['connection_state']}")
                print(f"  last_seen           : {st['last_seen']}")
                print(f"  advertised ops      : {st['capabilities']}")
            elif kind == "led_confirm":
                if args.yes:
                    to_send = True   # auto-pass, marked BLOCKED below
                else:
                    phase = "已亮" if event["phase"] == "on" else "已灭"
                    try:
                        ans = input(f"  [人工确认] WS2812/LED {phase}? "
                                    f"[y/N] ")
                        to_send = ans.strip().lower() == "y"
                    except EOFError:
                        to_send = False
            elif kind == "fatal":
                print(f"\nFATAL: {event['reason']}")
    except StopIteration:
        pass

    # --yes: physical confirmation auto-passed; state that honestly
    if args.yes:
        print("  [physical LED: NOT confirmed — run without --yes to "
              "confirm]")

    print(f"\n{'=' * 60}")
    print(f"P19-R CONSOLE RESULT: {ok_count} passed, {fail_count} failed")
    print(f"{'=' * 60}")
    if failed_names:
        print("FAILED items:")
        for name in failed_names:
            print(f"  - {name}")
    return 1 if fail_count else 0


if __name__ == "__main__":
    sys.exit(main())
