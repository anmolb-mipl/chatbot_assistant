import datetime as dt
import json
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .agent_intent import wants_agent
from .ai import HUMAN_RE, run_bot
from .config import settings
from .db import get_db
from .deps import current_customer
from .faq import get_faq, match_system_reply
from .models import Agent, AuditLog, Conversation, Message
from .realtime import hub
from .services import GREETING, add_message, allow_request, conv_dict, msg_dict, ice_servers, request_agent, valid_contact

router = APIRouter(prefix="/api/conversations", tags=["customer"])


class MessageIn(BaseModel):
    text: str = Field(min_length=1, max_length=1000)


class HandoffIn(BaseModel):
    channel: Literal["chat", "call"] = "chat"


class SignalIn(BaseModel):
    kind: Literal["offer", "candidate", "hangup"]
    data: dict | None = None


class FaqIn(BaseModel):
    faq_id: str = Field(max_length=50)


class ContactIn(BaseModel):
    contact: str = Field(min_length=5, max_length=120)


class RatingIn(BaseModel):
    score: int = Field(ge=1, le=5)
    comment: str = Field(default="", max_length=500)


async def _own(db: AsyncSession, cid: str, customer_id: str, *, lock: bool = False) -> Conversation:
    conv = await db.get(Conversation, cid, with_for_update=lock)
    if not conv or conv.customer_id != customer_id:
        raise HTTPException(404, "Conversation not found")
    return conv


async def _payload(db: AsyncSession, conv: Conversation) -> dict:
    agent = await db.get(Agent, conv.agent_id) if conv.agent_id else None
    rows = (await db.execute(select(Message).where(Message.conversation_id == conv.id).order_by(Message.id))).scalars().all()
    return {"conversation": conv_dict(conv, agent.display_name if agent else None), "messages": [msg_dict(m) for m in rows]}


@router.post("/open")
async def open_conversation(customer_id: str = Depends(current_customer), db: AsyncSession = Depends(get_db)):
    """Resume the customer's open chat, or start a new one."""
    conv = (await db.execute(
        select(Conversation).where(Conversation.customer_id == customer_id, Conversation.status != "closed")
        .order_by(Conversation.created_at.desc()).limit(1))).scalar_one_or_none()
    if not conv:
        conv = Conversation(customer_id=customer_id)
        db.add(conv)
        await db.flush()
        await add_message(db, conv, "bot", GREETING)
        await db.commit()
    return await _payload(db, conv)


@router.get("/{cid}")
async def get_conversation(cid: str, customer_id: str = Depends(current_customer), db: AsyncSession = Depends(get_db)):
    return await _payload(db, await _own(db, cid, customer_id))


@router.post("/{cid}/handoff")
async def handoff(cid: str, body: HandoffIn | None = None, customer_id: str = Depends(current_customer), db: AsyncSession = Depends(get_db)):
    conv = await _own(db, cid, customer_id)
    if conv.status != "bot":
        return {"status": conv.status, "messages": []}
    channel = body.channel if body else "chat"
    msgs = await request_agent(db, conv, "Customer wants to call an agent" if channel == "call" else "Customer asked for an agent", channel)
    return {"status": "waiting", "messages": [msg_dict(m) for m in msgs]}


@router.post("/{cid}/messages")
async def send_message(cid: str, body: MessageIn, customer_id: str = Depends(current_customer), db: AsyncSession = Depends(get_db)):
    conv = await _own(db, cid, customer_id, lock=True)
    if conv.status == "closed":
        raise HTTPException(409, "This chat has ended")
    text = body.text.strip()
    if not text:
        raise HTTPException(422, "Empty message")

    # While the request is waiting nobody has joined yet, so the customer cannot send anything.
    if conv.status == "waiting":
        raise HTTPException(409, "Please wait: an agent will join you shortly. You can send messages once they join.")

    # With an agent: pass the message on.
    if conv.status == "live":
        m = await add_message(db, conv, "customer", text)
        await db.commit()
        await hub.publish({"type": "message.created", "conversation_id": conv.id, "agent_id": conv.agent_id, "message": msg_dict(m)})
        return {"status": conv.status, "messages": [msg_dict(m)]}

    # Bot mode. The customer typed a request for a person ("connect me to an agent"): no LLM call, the page opens the Chat / Call card.
    if wants_agent(text):
        user_msg = await add_message(db, conv, "customer", text)
        bot_msg = await add_message(db, conv, "bot", "Sure! Please choose how you would like to talk to an agent.")
        await db.commit()
        return {"status": "bot", "messages": [msg_dict(user_msg), msg_dict(bot_msg)], "show_agent_options": True}

    # Bot mode. Fixed rules first (greetings, exact FAQ wording, stock-tip refusal): instant, free, always the same.
    system = match_system_reply(text)
    if system:
        user_msg = await add_message(db, conv, "customer", text)
        conv.bot_misses = 0
        bot_msg = await add_message(db, conv, "bot", system.text)
        await db.commit()
        return {"status": "bot", "messages": [msg_dict(user_msg), msg_dict(bot_msg)], "show_faqs": system.show_faqs}

    # Everything else goes to the LLM.
    if not await allow_request(f"rl:bot:{customer_id}", settings.bot_rate_limit_per_min):
        raise HTTPException(429, "You are sending messages too fast. Please wait a moment.")
    if not await allow_request("rl:bot:global", settings.bot_global_rate_limit_per_min):
        raise HTTPException(429, "The assistant is very busy right now. Please try again in a minute.")
    user_msg = await add_message(db, conv, "customer", text)
    rows = (await db.execute(
        select(Message).where(Message.conversation_id == conv.id, Message.sender != "system")
        .order_by(Message.id.desc()).limit(10))).scalars().all()[::-1]
    history = [(r.sender, r.text) for r in rows]
    result = await run_bot(db, customer_id, history, conv.bot_misses, bool(HUMAN_RE.search(text)))

    if result.action in {"clarify", "error"}:
        conv.bot_misses += 1
    elif result.action == "answer":
        conv.bot_misses = 0

    if result.action == "handoff" or conv.bot_misses >= 2:
        summary = "Assistant could not help after two attempts" if result.action != "handoff" else result.summary
        notice = await request_agent(db, conv, summary or "Customer needs help")
        return {"status": "waiting", "messages": [msg_dict(user_msg)] + [msg_dict(m) for m in notice]}

    bot_msg = await add_message(db, conv, "bot", result.text)
    await db.commit()
    return {"status": "bot", "messages": [msg_dict(user_msg), msg_dict(bot_msg)]}


@router.post("/{cid}/contact")
async def leave_contact(cid: str, body: ContactIn, customer_id: str = Depends(current_customer), db: AsyncSession = Depends(get_db)):
    """While waiting for an agent, the customer can leave a phone number or email for a call back."""
    conv = await _own(db, cid, customer_id)
    if conv.status != "waiting":
        raise HTTPException(409, "You can leave contact details while waiting for an agent")
    contact = body.contact.strip()
    if not valid_contact(contact):
        raise HTTPException(422, "Please enter a valid phone number or email")
    conv.contact = contact
    note = await add_message(db, conv, "bot", "Thanks! We have saved your details and an agent will contact you as soon as possible.")
    db.add(AuditLog(actor=f"customer:{customer_id}", action="contact_left", detail=cid))   # the contact itself is not logged
    await db.commit()
    await hub.publish({"type": "queue.contact", "conversation_id": cid})
    return {"status": conv.status, "contact": contact, "messages": [msg_dict(note)]}


@router.post("/{cid}/end")
async def end_chat(cid: str, customer_id: str = Depends(current_customer), db: AsyncSession = Depends(get_db)):
    conv = await _own(db, cid, customer_id)
    if conv.status == "closed":
        return {"status": "closed", "messages": []}
    conv.status = "closed"
    conv.closed_at = dt.datetime.now(dt.timezone.utc)
    conv.closed_by = "customer"
    note = await add_message(db, conv, "system", "The customer ended the chat.")
    await db.commit()
    await hub.publish({"type": "queue.closed", "conversation_id": cid, "agent_id": conv.agent_id})
    return {"status": "closed", "messages": [msg_dict(note)]}


@router.post("/{cid}/rating")
async def rate_chat(cid: str, body: RatingIn, customer_id: str = Depends(current_customer), db: AsyncSession = Depends(get_db)):
    conv = await _own(db, cid, customer_id)
    if conv.status != "closed":
        raise HTTPException(409, "You can rate the chat after it ends")
    if conv.rating is not None:
        raise HTTPException(409, "Thanks, you already rated this chat")
    conv.rating = body.score
    conv.rating_comment = body.comment.strip() or None
    await db.commit()
    return {"ok": True}


@router.post("/{cid}/faq")
async def ask_faq(cid: str, body: FaqIn, customer_id: str = Depends(current_customer), db: AsyncSession = Depends(get_db)):
    """The customer tapped an FAQ button: the answer comes from our own list, no LLM call."""
    conv = await _own(db, cid, customer_id)
    if conv.status == "closed":
        raise HTTPException(409, "This chat has ended")
    if conv.status != "bot":
        raise HTTPException(409, "FAQ answers are available while chatting with the assistant")
    faq = get_faq(body.faq_id)
    if not faq:
        raise HTTPException(404, "Unknown FAQ")
    user_msg = await add_message(db, conv, "customer", faq["question"])
    conv.bot_misses = 0
    bot_msg = await add_message(db, conv, "bot", faq["answer"])
    await db.commit()
    return {"status": "bot", "messages": [msg_dict(user_msg), msg_dict(bot_msg)], "show_faqs": True}


@router.get("/{cid}/ice")
async def ice(cid: str, customer_id: str = Depends(current_customer), db: AsyncSession = Depends(get_db)):
    await _own(db, cid, customer_id)
    return {"ice_servers": ice_servers()}


@router.post("/{cid}/signal")
async def signal(cid: str, body: SignalIn, customer_id: str = Depends(current_customer), db: AsyncSession = Depends(get_db)):
    """Voice call set-up messages (offer / network candidates) from the customer to the agent who answered."""
    conv = await _own(db, cid, customer_id)
    if conv.status != "live" or conv.channel != "call" or conv.agent_id is None:
        raise HTTPException(409, "There is no live call on this chat")
    if len(json.dumps(body.data or {})) > 20000:
        raise HTTPException(413, "Signal too large")
    if not await allow_request(f"rl:signal:{cid}", 600):
        raise HTTPException(429, "Too many signals")
    await hub.publish({"type": "call.signal", "conversation_id": cid, "agent_id": conv.agent_id, "from": "customer", "kind": body.kind, "data": body.data})
    return {"ok": True}


@router.post("/{cid}/typing")
async def typing(cid: str, customer_id: str = Depends(current_customer), db: AsyncSession = Depends(get_db)):
    """The customer is typing: tell the agent (only while an agent is in the chat). Nothing is stored."""
    conv = await _own(db, cid, customer_id)
    if conv.status != "live" or not await allow_request(f"rl:typing:{cid}", 60):
        return {"ok": False}
    await hub.publish({"type": "typing", "conversation_id": cid, "agent_id": conv.agent_id, "from": "customer"})
    return {"ok": True}