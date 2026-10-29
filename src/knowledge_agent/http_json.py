"""Bound caller wait across connect + body reads, including keep-alive bytes."""
from __future__ import annotations

import json
import math
from queue import Empty, Queue
from threading import Event, Thread
import time

from .streaming import SSEDecoder


class RequestDeadlineExceeded(TimeoutError):
    pass


class RequestCancelled(KeyboardInterrupt):
    pass


def _bounded(operation, timeout: float, cancel_event=None):
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
            outcome = (True, operation(deadline, stopped))
        except Exception as exc:
            outcome = (False, exc)
        if not stopped.is_set():
            results.put_nowait(outcome)

    Thread(target=worker, daemon=True, name="bounded-http-json").start()
    try:
        # Short waits also give Windows/Python 3.11-3.12 frequent opportunities
        # to process Ctrl+C rather than waiting on one long condition wait.
        while True:
            if cancel_event is not None and cancel_event.is_set():
                raise RequestCancelled
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


def _check(deadline, stopped):
    if stopped.is_set() or time.monotonic() >= deadline:
        raise RequestDeadlineExceeded


def read_json(request, opener, timeout: float, max_bytes: int = 8 * 1024 * 1024, *, cancel_event=None) -> dict:
    def operation(deadline, stopped):
        with opener.open(request, timeout=timeout) as response:
            chunks, size = [], 0
            while True:
                _check(deadline, stopped)
                chunk = response.read1(64 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > max_bytes:
                    raise ValueError("HTTP response exceeds maximum body size")
                chunks.append(chunk)
            return json.loads(b"".join(chunks).decode("utf-8"))
    return _bounded(operation, timeout, cancel_event)


def read_sse(request, opener, timeout: float, on_event, *, cancel_event=None) -> dict:
    def operation(deadline, stopped):
        decoder, size = SSEDecoder(), 0
        with opener.open(request, timeout=timeout) as response:
            while True:
                _check(deadline, stopped)
                chunk = response.read1(64 * 1024)
                if not chunk:
                    raise ValueError("SSE ended without a terminal response")
                size += len(chunk)
                if size > 8 * 1024 * 1024:
                    raise ValueError("SSE response too large")
                for frame in decoder.feed(chunk):
                    _check(deadline, stopped)
                    event = json.loads(frame["data"])
                    if not isinstance(event, dict):
                        raise ValueError("invalid SSE event")
                    on_event(event)
                    if event.get("type") in {"response.completed", "response.incomplete", "response.failed"}:
                        if not isinstance(event.get("response"), dict):
                            raise ValueError("terminal SSE event has no response")
                        return event["response"]
    return _bounded(operation, timeout, cancel_event)
