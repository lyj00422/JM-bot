"""Bounded round-robin raw events, preserving direct requests over passive traffic."""
import asyncio
import time
from collections import deque
from jmbot.core.ingress import classify, account_scope


class IngressQueue:
    def __init__(self, maxsize=128, per_scope=16, ttl=90):
        self.maxsize, self.per_scope, self.ttl = maxsize, per_scope, ttl
        self.queues, self.ready = {}, deque()
        self.available = asyncio.Event()
        self.dropped = 0

    def qsize(self):
        return sum(len(items) for items in self.queues.values())

    def put_nowait(self, event):
        route = classify(event)
        if route.domain not in {'chat', 'credential'} or route.scope is None:
            raise ValueError('only scoped chat or credential events can be queued')
        scope = account_scope(event, route.scope)
        items = self.queues.get(scope, deque())
        full = len(items) >= self.per_scope or self.qsize() >= self.maxsize
        if full:
            passive = next((i for i, (_, old) in enumerate(items) if not classify(old).direct), None)
            if passive is None and route.direct and self.qsize() >= self.maxsize:
                for other in self.queues.values():
                    index = next((i for i, (_, old) in enumerate(other) if not classify(old).direct), None)
                    if index is not None:
                        del other[index]
                        full = len(items) >= self.per_scope
                        break
            if passive is not None:
                del items[passive]
                self.dropped += 1
            elif full:
                self.dropped += 1
                raise asyncio.QueueFull
        if scope not in self.queues:
            self.queues[scope] = items
            self.ready.append(scope)
        items.append((time.monotonic(), event))
        self.available.set()

    async def get(self):
        while True:
            while self.ready:
                scope = self.ready.popleft()
                items = self.queues[scope]
                if not items:
                    del self.queues[scope]
                    continue
                accepted, event = items.popleft()
                if items:
                    self.ready.append(scope)
                else:
                    del self.queues[scope]
                if time.monotonic() - accepted <= self.ttl:
                    return event
                self.dropped += 1
            self.available.clear()
            await self.available.wait()

    def task_done(self):
        pass
