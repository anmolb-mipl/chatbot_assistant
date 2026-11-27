import datetime as dt
import json
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from .config import settings
from .db import get_db
from .deps import current_agent
from .models import Agent, AuditLog, Conversation, Message
from .realtime import hub
from .security import hash_password, make_agent_token, verify_password
from .services import add_message, allow_request, claim_conversation, conv_dict, ice_servers, msg_dict

router = APIRouter(prefix="/api/agent", tags=["agent"])
_DUMMY_HASH = hash_password("not-a-real-password")   # so unknown usernames cost the same time as real ones


class LoginIn(BaseModel):
    username: str = Field(max_length=50)
    password: str = Field(max_length=200)


class SignalIn(BaseModel):
    kind: Literal["answer", "candidate", "hangup"]
    data: dict | None = None


class ReplyIn(BaseModel):
    text: str = Field(min_length=1, max_length=1000)


@router.post("/login")
async def login(body: LoginIn, request: Request, db: AsyncSession = Depends(get_db)):
    ip = request.client.host if request.client else "?"
    if not await allow_request(f"rl:login:{ip}", 10):
        raise HTTPException(429, "Too many attempts. Try again in a minute.")
    agent = (await db.execute(select(Agent).where(Agent.username == body.username.strip().lower()))).scalar_one_or_none()
    ok = verify_password(agent.password_hash if agent else _DUMMY_HASH, body.password)
    if not agent or not agent.is_active or not ok:
        raise HTTPException(401, "Wrong username or password")
    return {"token": make_agent_token(agent), "name": agent.display_name}


@router.get("/conversations")
async def list_conversations(view: str = "active", agent: Agent = Depends(current_agent), db: AsyncSession = Depends(get_db)):
    q = select(Conversation, Agent.display_name).outerjoin(Agent, Conversation.agent_id == Agent.id)
    if view == "past":
        q = q.where(Conversation.status == "closed")
    else:  # waiting requests are shared by everyone; live chats only for their owner
        q = q.where(or_(Conversation.status == "waiting", and_(Conversation.status == "live", Conversation.agent_id == agent.id)))
    rows = (await db.execute(q.order_by(Conversation.updated_at.desc()).limit(100))).all()
    return [{**conv_dict(c, name), "mine": c.agent_id == agent.id} for c, name in rows]


async def _visible(db: AsyncSession, cid: str, agent: Agent) -> Conversation:
    c = await db.get(Conversation, cid)
    if not c or c.status == "bot":
        raise HTTPException(404, "Conversation not found")
    if c.status == "live" and c.agent_id != agent.id:
        raise HTTPException(403, "Accepted by another agent")
    return c


@router.get("/conversations/{cid}")
async def get_conversation(cid: str, agent: Agent = Depends(current_agent), db: AsyncSession = Depends(get_db)):
    c = await _visible(db, cid, agent)
    owner = await db.get(Agent, c.agent_id) if c.agent_id else None
    rows = (await db.execute(select(Message).where(Message.conversation_id == cid).order_by(Message.id))).scalars().all()
    return {"conversation": {**conv_dict(c, owner.display_name if owner else None), "mine": c.agent_id == agent.id},
            "messages": [msg_dict(m) for m in rows]}


@router.post("/conversations/{cid}/accept")
async def accept(cid: str, agent: Agent = Depends(current_agent), db: AsyncSession = Depends(get_db)):
    c = await db.get(Conversation, cid)
    if not c or c.status == "bot":
        raise HTTPException(404, "Conversation not found")
    outcome = await claim_conversation(db, cid, agent.id, settings.max_active_chats_per_agent)
    if outcome == "busy":
        await db.rollback()
        raise HTTPException(403, "You already have a live chat. End it before accepting another.")
    if outcome == "taken":                                       # someone else was faster
        await db.rollback()
        await db.refresh(c)
        owner = await db.get(Agent, c.agent_id) if c.agent_id else None
        raise HTTPException(409, f"Already accepted by {owner.display_name if owner else 'another agent'}")
    await db.refresh(c)
    if c.channel == "call":
        greeting = await add_message(db, c, "system", f"{agent.display_name} joined the call")
    else:
        greeting = await add_message(db, c, "agent", f"Hi, I am {agent.display_name}. How can I help you today?", auto=True)
    db.add(AuditLog(actor=f"agent:{agent.username}", action="accept", detail=cid))
    await db.commit()
    await hub.publish({"type": "queue.taken", "conversation_id": cid, "agent_id": agent.id, "agent_name": agent.display_name})
    await hub.publish({"type": "message.created", "conversation_id": cid, "agent_id": agent.id, "message": msg_dict(greeting)})
    return {"ok": True}


@router.post("/conversations/{cid}/messages")
async def reply(cid: str, body: ReplyIn, agent: Agent = Depends(current_agent), db: AsyncSession = Depends(get_db)):
    c = await _visible(db, cid, agent)
    if c.status != "live" or c.agent_id != agent.id:
        raise HTTPException(409, "You can only reply to your own live chats")
    m = await add_message(db, c, "agent", body.text.strip())
    await db.commit()
    await hub.publish({"type": "message.created", "conversation_id": cid, "agent_id": agent.id, "message": msg_dict(m)})
    return {"message": msg_dict(m)}


@router.post("/conversations/{cid}/close")
async def close(cid: str, agent: Agent = Depends(current_agent), db: AsyncSession = Depends(get_db)):
    c = await _visible(db, cid, agent)
    if c.status != "live" or c.agent_id != agent.id:
        raise HTTPException(409, "You can only end your own live chats")
    c.status = "closed"
    c.closed_at = dt.datetime.now(dt.timezone.utc)
    c.closed_by = "agent"
    db.add(AuditLog(actor=f"agent:{agent.username}", action="close", detail=cid))
    await db.commit()
    await hub.publish({"type": "queue.closed", "conversation_id": cid, "agent_id": agent.id})
    return {"ok": True}


@router.get("/ice")
async def ice(agent: Agent = Depends(current_agent)):
    return {"ice_servers": ice_servers()}


@router.post("/conversations/{cid}/signal")
async def signal(cid: str, body: SignalIn, agent: Agent = Depends(current_agent), db: AsyncSession = Depends(get_db)):
    """Voice call set-up messages (answer / network candidates) from the agent to the customer."""
    c = await _visible(db, cid, agent)
    if c.status != "live" or c.agent_id != agent.id or c.channel != "call":
        raise HTTPException(409, "There is no live call on this chat for you")
    if len(json.dumps(body.data or {})) > 20000:
        raise HTTPException(413, "Signal too large")
    if not await allow_request(f"rl:signal:{cid}", 600):
        raise HTTPException(429, "Too many signals")
    await hub.publish({"type": "call.signal", "conversation_id": cid, "agent_id": agent.id, "from": "agent", "kind": body.kind, "data": body.data})
    return {"ok": True}


@router.post("/conversations/{cid}/typing")
async def typing(cid: str, agent: Agent = Depends(current_agent), db: AsyncSession = Depends(get_db)):
    """The agent is typing: tell the customer. Nothing is stored."""
    c = await _visible(db, cid, agent)
    if c.status != "live" or c.agent_id != agent.id or not await allow_request(f"rl:typing:{cid}", 60):
        return {"ok": False}
    await hub.publish({"type": "typing", "conversation_id": cid, "agent_id": agent.id, "from": "agent"})
    return {"ok": True}