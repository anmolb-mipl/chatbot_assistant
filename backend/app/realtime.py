"""Live updates: Redis pub/sub fans events out to every server process, which pushes them to its WebSockets."""
import asyncio
import json
import logging

import redis.asyncio as aioredis
from fastapi import WebSocket

log = logging.getLogger(__name__)
CHANNEL = "events"


class Hub:
    def __init__(self) -> None:
        self.customers: dict[str, set[WebSocket]] = {}   # conversation_id -> sockets
        self.agents: dict[int, set[WebSocket]] = {}      # agent_id -> sockets
        self.redis: aioredis.Redis | None = None
        self._task: asyncio.Task | None = None

    async def start(self, url: str) -> None:
        if not url:   # local mode: no Redis, events stay inside this one process
            log.info("REDIS_URL is empty: using in-process events (run a single server process)")
            return
        self.redis = aioredis.from_url(url, decode_responses=True)
        self._task = asyncio.create_task(self._listen())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
        if self.redis:
            await self.redis.aclose()

    async def publish(self, event: dict) -> None:
        if self.redis is None:
            await self._dispatch(event)
        else:
            await self.redis.publish(CHANNEL, json.dumps(event))

    def add_customer(self, cid: str, ws: WebSocket) -> None:
        self.customers.setdefault(cid, set()).add(ws)

    def add_agent(self, agent_id: int, ws: WebSocket) -> None:
        self.agents.setdefault(agent_id, set()).add(ws)

    def drop(self, ws: WebSocket) -> None:
        for group in (self.customers, self.agents):
            for key in list(group):
                group[key].discard(ws)
                if not group[key]:
                    del group[key]

    async def _listen(self) -> None:
        while True:
            try:
                pubsub = self.redis.pubsub()
                await pubsub.subscribe(CHANNEL)
                async for m in pubsub.listen():
                    if m.get("type") == "message":
                        await self._dispatch(json.loads(m["data"]))
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("pubsub error, retrying")
                await asyncio.sleep(2)

    async def _dispatch(self, ev: dict) -> None:
        # The customer of that conversation gets all its events.
        targets = list(self.customers.get(ev["conversation_id"], ()))
        if ev["type"].startswith("queue."):          # new request / taken / closed: every agent
            for sockets in self.agents.values():
                targets.extend(sockets)
        elif ev.get("agent_id") in self.agents:      # messages: only the agent who owns the chat
            targets.extend(self.agents[ev["agent_id"]])
        for ws in targets:
            try:
                await ws.send_json(ev)
            except Exception:
                self.drop(ws)


hub = Hub()
