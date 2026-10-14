"""In-process pub/sub so the dashboard (SSE), CLI and MCP see live batch progress."""
from __future__ import annotations

import asyncio
import time
from collections import defaultdict, deque


class EventBus:
    def __init__(self, history: int = 500):
        self._subs: dict[object, set[asyncio.Queue]] = defaultdict(set)
        self._history: dict[object, deque] = defaultdict(lambda: deque(maxlen=history))

    def publish(self, topic, event: dict):
        event = {"ts": time.time(), **event}
        self._history[topic].append(event)
        for q in list(self._subs[topic]) + list(self._subs["*"]):
            try:
                q.put_nowait({"topic": topic, **event})
            except asyncio.QueueFull:
                pass

    def history(self, topic) -> list[dict]:
        return list(self._history[topic])

    def subscribe(self, topic) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=1000)
        self._subs[topic].add(q)
        return q

    def unsubscribe(self, topic, q: asyncio.Queue):
        self._subs[topic].discard(q)
