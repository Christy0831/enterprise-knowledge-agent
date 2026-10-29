"""Bound caller wait across connect + body reads, including keep-alive bytes."""
from __future__ import annotations

import json
import math
from queue import Empty, Queue
from threading import Event, Thread
import time


class RequestDeadlineExceeded(TimeoutError):
    pass


def read_json(request, opener, timeout: float, max_bytes: int = 8 * 1024 * 1024) -> dict:
    """urllib socket timeout is per blocking I/O, not a whole-request deadline.

    A daemon worker bounds caller wait even during DNS/TLS or a chunk-header read.
    On timeout/cancel it closes its response at the next I/O boundary; no retry is
    made and late results are discarded. This does not cancel remote inference.
    """
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("HTTP总时限须为有限正数")
    deadline = time.monotonic() + timeout
    stopped = Event()
    results = Queue(maxsize=1)

    def worker():
        try:
            with opener.open(request, timeout=timeout) as response:
                chunks, size = [], 0
                while True:
                    if stopped.is_set() or time.monotonic() >= deadline:
                        raise RequestDeadlineExceeded
                    chunk = response.read1(64 * 1024)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > max_bytes:
                        raise ValueError("HTTP response exceeds maximum body size")
                    chunks.append(chunk)
                value = json.loads(b"".join(chunks).decode("utf-8"))
            outcome = (True, value)
        except Exception as exc:
            outcome = (False, exc)
        if not stopped.is_set():
            results.put_nowait(outcome)

    Thread(target=worker, daemon=True, name="bounded-http-json").start()
    try:
        # Short waits also give Windows/Python 3.11-3.12 frequent opportunities
        # to process Ctrl+C rather than waiting on one long condition wait.
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise RequestDeadlineExceeded
            try:
                ok, value = results.get(timeout=min(0.1, remaining))
                break
            except Empty:
                continue
        if time.monotonic() >= deadline:
            raise RequestDeadlineExceeded
        if ok:
            return value
        raise value
    finally:
        stopped.set()
