import asyncio
import datetime as dt
import logging
import secrets
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from . import models  # noqa: F401  (registers tables)
from .config import settings
from .db import Base, SessionLocal, engine
from .faq import public_faqs
from .models import Agent, Conversation, now
from .realtime import hub
from .routes_agent import router as agent_router
from .routes_customer import router as customer_router
from .security import decode_token, make_customer_token
from .seed import seed_from_spec
from .services import due_fallbacks
from .static_files import resolve_static

logging.basicConfig(level=logging.INFO)


async def _watch_waiting() -> None:
    """Every 15 s: chats waiting longer than the timeout get the 'all agents are busy' message."""
    while True:
        await asyncio.sleep(15)
        try:
            cutoff = now() - dt.timedelta(seconds=settings.agent_wait_timeout_seconds)
            async with SessionLocal() as db:
                due = await due_fallbacks(db, cutoff)
            for cid, message in due:
                await hub.publish({"type": "message.created", "conversation_id": cid, "agent_id": None, "message": message})
                await hub.publish({"type": "queue.timeout", "conversation_id": cid})
        except asyncio.CancelledError:
            raise
        except Exception:
            logging.exception("waiting-chat watcher failed")


@asynccontextmanager
async def lifespan(app: FastAPI):
    if settings.env == "prod" and ("change-me" in settings.agent_jwt_secret or "change-me" in settings.customer_jwt_secret):
        raise RuntimeError("Set real JWT secrets before running in production")
    if settings.auto_create_tables:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    if settings.seed_agents:
        await seed_from_spec(settings.seed_agents)
    await hub.start(settings.redis_url)
    watcher = asyncio.create_task(_watch_waiting())
    yield
    watcher.cancel()
    await hub.stop()
    await engine.dispose()


app = FastAPI(title="TradeWise Support", lifespan=lifespan, docs_url="/api/docs" if settings.env == "dev" else None, redoc_url=None, openapi_url="/api/openapi.json" if settings.env == "dev" else None)
app.add_middleware(CORSMiddleware, allow_origins=[o.strip() for o in settings.cors_origins.split(",")],
                   allow_methods=["GET", "POST"], allow_headers=["Authorization", "Content-Type"])
app.include_router(customer_router)
app.include_router(agent_router)


@app.get("/api/faqs")
async def faqs():
    return public_faqs()


@app.get("/api/healthz")
async def healthz():
    return {"ok": True}


if settings.env == "dev":
    @app.get("/api/dev/customer-token")
    async def dev_customer_token(visitor: str = ""):
        """Dev / demo only. Each browser gets its OWN demo customer, so visitors never see each other's chats.
        In production YOUR app issues the customer token after the user logs in."""
        clean = "".join(ch for ch in visitor if ch.isalnum())[:32] or secrets.token_hex(8)
        return {"token": make_customer_token(f"demo_{clean}")}


@app.websocket("/ws/customer")
async def ws_customer(ws: WebSocket, token: str, conversation_id: str):
    try:
        customer_id = decode_token(token, settings.customer_jwt_secret, "customer")["sub"]
        async with SessionLocal() as db:
            conv = await db.get(Conversation, conversation_id)
        if not conv or conv.customer_id != customer_id:
            raise ValueError("not your conversation")
    except Exception:
        await ws.close(code=4401)
        return
    await ws.accept()
    hub.add_customer(conversation_id, ws)
    try:
        while True:
            await ws.receive_text()      # keep-alive pings; nothing else is accepted from the client
    except WebSocketDisconnect:
        pass
    finally:
        hub.drop(ws)


@app.websocket("/ws/agent")
async def ws_agent(ws: WebSocket, token: str):
    try:
        agent_id = int(decode_token(token, settings.agent_jwt_secret, "agent")["sub"])
        async with SessionLocal() as db:
            agent = await db.get(Agent, agent_id)
        if not agent or not agent.is_active:
            raise ValueError("inactive")
    except Exception:
        await ws.close(code=4401)
        return
    await ws.accept()
    hub.add_agent(agent_id, ws)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        hub.drop(ws)


# Single-container hosting: serve the built website from here. Registered LAST so every API / WebSocket route wins.
if settings.static_dir:
    _static_root = Path(settings.static_dir)

    @app.get("/{full_path:path}", include_in_schema=False)
    async def serve_frontend(full_path: str):
        f = resolve_static(_static_root, full_path)
        if f is None:
            raise HTTPException(404, "Not found")
        return FileResponse(f, headers={"Cache-Control": "no-cache"} if f.name == "index.html" else None)
