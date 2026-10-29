"""SSE framing and safe previews of a top-level JSON string argument."""
from __future__ import annotations

import codecs
import json


class SSEDecoder:
    def __init__(self, max_buffer: int = 8 * 1024 * 1024):
        self.decoder = codecs.getincrementaldecoder("utf-8")()
        self.buffer = ""
        self.max_buffer = max_buffer

    def feed(self, data: bytes) -> list[dict]:
        self.buffer += self.decoder.decode(data)
        self.buffer = self.buffer.replace("\r\n", "\n")
        if len(self.buffer) > self.max_buffer:
            raise ValueError("SSE frame too large")
        events = []
        while "\n\n" in self.buffer:
            frame, self.buffer = self.buffer.split("\n\n", 1)
            lines = frame.split("\n")
            name = next((line[6:].lstrip(" ") for line in lines if line.startswith("event:")), "message")
            parts = [line[5:].removeprefix(" ") for line in lines if line.startswith("data:")]
            if parts:
                events.append({"event": name, "data": "\n".join(parts)})
        return events


def partial_string_field(text: str, field: str = "answer") -> str | None:
    """Only inspect a real top-level field, not matching text inside another value.

    Earlier properties must be complete JSON. The target string may be incomplete;
    incomplete escapes/surrogate pairs are withheld until they are complete.
    """
    decoder = json.JSONDecoder()
    cursor = 0

    def whitespace(index):
        while index < len(text) and text[index].isspace():
            index += 1
        return index

    cursor = whitespace(cursor)
    if cursor >= len(text) or text[cursor] != "{":
        return None
    cursor += 1
    while cursor < len(text):
        cursor = whitespace(cursor)
        try:
            key, cursor = decoder.raw_decode(text, cursor)
        except ValueError:
            return None
        cursor = whitespace(cursor)
        if not isinstance(key, str) or cursor >= len(text) or text[cursor] != ":":
            return None
        cursor = whitespace(cursor + 1)
        if key == field:
            if cursor >= len(text) or text[cursor] != '"':
                return None
            start = cursor
            cursor += 1
            safe_end = cursor
            while cursor < len(text):
                char = text[cursor]
                if char == '"':
                    try:
                        return json.loads(text[start:cursor + 1])
                    except ValueError:
                        return None
                if char == "\\":
                    if cursor + 1 >= len(text):
                        break
                    if text[cursor + 1] == "u":
                        if cursor + 6 > len(text):
                            break
                        cursor += 6
                    else:
                        cursor += 2
                else:
                    cursor += 1
                safe_end = cursor
            try:
                value = json.loads(text[start:safe_end] + '"')
                if value and 0xD800 <= ord(value[-1]) <= 0xDBFF:
                    value = value[:-1]
                return value
            except ValueError:
                return None
        try:
            _, cursor = decoder.raw_decode(text, cursor)
        except ValueError:
            return None
        cursor = whitespace(cursor)
        if cursor >= len(text) or text[cursor] != ",":
            return None
        cursor += 1
    return None


class AnswerStream:
    """Forward real finish arguments as an explicitly unvalidated answer preview."""
    def __init__(self, emit):
        self.emit = emit
        self.items = {}
        self.event_count = 0
        self.delta_count = 0

    def accept(self, event: dict) -> None:
        self.event_count += 1
        index = event.get("output_index", event.get("item_id", 0))
        kind = event.get("type")
        if kind == "response.output_item.added":
            item = event.get("item", {})
            if item.get("type") == "function_call":
                self.items[index] = {"name": item.get("name"), "arguments": item.get("arguments", ""), "preview": ""}
        elif kind == "response.function_call_arguments.delta":
            item = self.items.get(index)
            if item is None:
                return
            item["arguments"] += event.get("delta", "")
            if item["name"] == "finish":
                preview = partial_string_field(item["arguments"])
                if preview is not None and preview.startswith(item["preview"]) and len(preview) > len(item["preview"]):
                    if not item["preview"]:
                        self.emit({"event": "answer_start", "validated": False})
                    self.emit({"event": "answer_delta", "delta": preview[len(item["preview"]):], "validated": False})
                    item["preview"] = preview
                    self.delta_count += 1
