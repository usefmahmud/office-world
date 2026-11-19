"""WebSocket fan-out with delta coalescing.

Each client gets its own bounded queue. If a client falls behind we keep only
the newest pending frame (never grow memory, never block the tick loop).
"""

from __future__ import annotations

import asyncio
import json
from typing import Any


class Client:
    def __init__(self, maxsize: int = 2) -> None:
        self.queue: asyncio.Queue[str] = asyncio.Queue(maxsize=maxsize)

    def send(self, frame: dict[str, Any]) -> None:
        payload = json.dumps(frame, ensure_ascii=False, separators=(",", ":"))
        try:
            self.queue.put_nowait(payload)
        except asyncio.QueueFull:
            try:
                self.queue.get_nowait()  # drop the stale frame
            except asyncio.QueueEmpty:
                pass
            try:
                self.queue.put_nowait(payload)
            except asyncio.QueueFull:
                pass


class Hub:
    def __init__(self) -> None:
        self._clients: set[Client] = set()

    @property
    def size(self) -> int:
        return len(self._clients)

    def add(self, client: Client) -> None:
        self._clients.add(client)

    def remove(self, client: Client) -> None:
        self._clients.discard(client)

    def broadcast(self, frame: dict[str, Any]) -> None:
        for client in list(self._clients):
            client.send(frame)
