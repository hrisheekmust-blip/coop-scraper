"""Lightweight client for the worker service (used by the native host and the CLI; imports nothing heavy)."""
from __future__ import annotations

from multiprocessing.connection import Client
from pathlib import Path

from .config import pipe_address, pipe_key


def call(msg: dict, channel: str = "cli", root: Path | None = None, timeout: float = 10) -> dict:
    addr, fam = pipe_address(root)
    c = Client(addr, family=fam, authkey=pipe_key(root))
    try:
        c.send({"channel": channel, "msg": msg})
        if not c.poll(timeout):
            return {"ok": False, "error": "the worker service didn't answer"}
        return c.recv()
    finally:
        c.close()
