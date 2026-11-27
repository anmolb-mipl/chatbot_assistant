import datetime as dt
import logging
import re
import time

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from .config import settings
from .models import Agent, Conversation, Message, now
from .realtime import hub

log = logging.getLogger(__name__)
GREETING = "Hi! I'm the TradeWise assistant. How can I help you today?"


def iso(t: dt.datetime) -> str:
    """SQLite returns naive datetimes; treat them as UTC so browsers show the right local time."""
    return (t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)).isoformat()


def msg_dict(m: Message) -> dict:
    return {"id": m.id, "sender": m.sender, "text": m.text, "auto": m.auto, "created_at": iso(m.created_at)}


def conv_dict(c: Conversation, agent_name: str | None = None) -> dict:
    return {"id": c.id, "status": c.status, "summary": c.summary, "agent_id": c.agent_id, "agent_name": agent_name,
            "updated_at": iso(c.updated_at), "created_at": iso(c.created_at), "contact": c.contact,
            "rating": c.rating, "rating_comment": c.rating_comment, "fallback_sent": c.fallback_sent, "closed_by": c.closed_by, "channel": c.channel}


async def add_message(db: AsyncSession, conv: Conversation, sender: str, text: str, auto: bool = False) -> Message:
    m = Message(conversation_id=conv.id, sender=sender, text=text[:2000], auto=auto)
    db.add(m)
    await db.flush()          # assigns m.id
    conv.updated_at = now()
    return m


async def claim_conversation(db: AsyncSession, conv_id: str, agent_id: int, max_active: int = 1) -> str:
    """Atomic accept. Returns 'ok', 'busy' (agent already has a live chat) or 'taken' (another agent was faster).

    - Locking the agent's row makes the same agent's simultaneous accepts run one after another,
      so they cannot slip past the 'one live chat at a time' limit.
    - WHERE status='waiting' means only ONE agent's UPDATE can match the chat.
    """
    await db.execute(select(Agent.id).where(Agent.id == agent_id).with_for_update())
    active = (await db.execute(
        select(func.count()).select_from(Conversation)
        .where(Conversation.agent_id == agent_id, Conversation.status == "live"))).scalar_one()
    if active >= max_active:
        return "busy"
    res = await db.execute(
        update(Conversation)
        .where(Conversation.id == conv_id, Conversation.status == "waiting")
        .values(status="live", agent_id=agent_id, updated_at=now())
        .returning(Conversation.id)
    )
    return "ok" if res.scalar_one_or_none() is not None else "taken"


def ice_servers() -> list[dict]:
    """Network helpers for voice calls. STUN finds the public address; a TURN relay is needed when direct connections are blocked."""
    servers: list[dict] = [{"urls": [u.strip() for u in settings.stun_urls.split(",") if u.strip()]}]
    if settings.turn_url:
        servers.append({"urls": [settings.turn_url], "username": settings.turn_username, "credential": settings.turn_credential})
    return servers


async def request_agent(db: AsyncSession, conv: Conversation, summary: str, channel: str = "chat") -> list[Message]:
    """Put the chat in the shared queue: every agent sees it until one accepts."""
    conv.status = "waiting"
    conv.summary = summary[:300]
    conv.waiting_since = now()
    conv.fallback_sent = False
    conv.channel = channel
    text = "Calling an agent... please keep this page open." if channel == "call" else "I'm connecting you to an agent. Please wait a moment."
    notice = await add_message(db, conv, "bot", text)
    await db.commit()
    await hub.publish({"type": "queue.waiting", "conversation_id": conv.id, "summary": conv.summary, "channel": channel})
    return [notice]


_local_hits: dict[str, tuple[float, int]] = {}


async def allow_request(key: str, limit: int) -> bool:
    """Simple per-minute rate limit (Redis when available, in memory in local mode)."""
    if hub.redis is None:
        t = time.monotonic()
        start, n = _local_hits.get(key, (t, 0))
        if t - start > 60:
            start, n = t, 0
        _local_hits[key] = (start, n + 1)
        return n + 1 <= limit
    try:
        n = await hub.redis.incr(key)
        if n == 1:
            await hub.redis.expire(key, 60)
        return n <= limit
    except Exception:
        log.exception("rate limiter unavailable")
        return True


FALLBACK_TEXT = ("All our agents are busy right now. Please leave your phone number or email below and we will contact you "
                 "as soon as possible. You can also keep waiting here.")
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_PHONE = re.compile(r"^\+?[0-9][0-9\s\-]{6,18}[0-9]$")


def valid_contact(text: str) -> bool:
    text = text.strip()
    return bool(_EMAIL.match(text) or _PHONE.match(text))


async def due_fallbacks(db: AsyncSession, cutoff) -> list[tuple[str, dict]]:
    """Chats that have waited past the cutoff get ONE 'all agents are busy' message.
    The UPDATE ... WHERE fallback_sent=false guard makes it fire once even with several server workers."""
    ids = (await db.execute(
        select(Conversation.id).where(
            Conversation.status == "waiting", Conversation.fallback_sent.is_(False),
            Conversation.waiting_since.is_not(None), Conversation.waiting_since < cutoff))).scalars().all()
    out: list[tuple[str, dict]] = []
    for cid in ids:
        res = await db.execute(
            update(Conversation)
            .where(Conversation.id == cid, Conversation.status == "waiting", Conversation.fallback_sent.is_(False))
            .values(fallback_sent=True).returning(Conversation.id))
        if res.scalar_one_or_none() is None:      # another worker got there first
            continue
        conv = await db.get(Conversation, cid)
        await db.refresh(conv)
        m = await add_message(db, conv, "bot", FALLBACK_TEXT)
        out.append((cid, msg_dict(m)))
    await db.commit()
    return out