"""Chrome native-messaging host: relays one extension message at a time to the local worker service.

Chrome starts this process only for the extension id listed in the host manifest (allowed_origins). It never
listens on a port; it talks to the service over the per-user pipe with the service's key.
"""
import json
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from runner.client import call  # noqa: E402

CHANNELS = {"board", "outlook", "settings"}


def read():
    raw = sys.stdin.buffer.read(4)
    if len(raw) < 4:
        return None
    n = struct.unpack("<I", raw)[0]
    if n > 1024 * 1024:
        return None
    return json.loads(sys.stdin.buffer.read(n).decode("utf-8"))


def write(obj):
    data = json.dumps(obj).encode("utf-8")
    sys.stdout.buffer.write(struct.pack("<I", len(data)))
    sys.stdout.buffer.write(data)
    sys.stdout.buffer.flush()


def main():
    while True:
        msg = read()
        if msg is None:
            return
        rid = msg.get("rid")
        channel = msg.get("channel")
        if channel not in CHANNELS:
            write({"rid": rid, "ok": False, "error": "unknown channel"})
            continue
        try:
            resp = call(msg.get("msg") or {}, channel=channel)
        except FileNotFoundError:
            resp = {"ok": False, "error": "worker not installed", "offline": True}
        except (ConnectionError, OSError):
            resp = {"ok": False, "error": "worker service is not running", "offline": True}
        write({"rid": rid, **resp})


if __name__ == "__main__":
    main()
