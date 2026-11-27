import datetime as dt
from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app import models  # noqa: F401
from app.config import settings
from app.db import Base
from app.models import Agent, Conversation
from app.security import decode_token, hash_password, make_agent_token, make_customer_token, verify_password
from app.services import claim_conversation, due_fallbacks, valid_contact
from app.tools import get_transaction_status


@pytest.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        yield session
    await engine.dispose()


def test_password_hashing():
    h = hash_password("s3cret!")
    assert h != "s3cret!"
    assert verify_password(h, "s3cret!")
    assert not verify_password(h, "wrong")


def test_token_types_cannot_be_swapped():
    agent_token = make_agent_token(SimpleNamespace(id=1, display_name="Riya", role="agent"))
    assert decode_token(agent_token, settings.agent_jwt_secret, "agent")["sub"] == "1"
    with pytest.raises(Exception):
        decode_token(agent_token, settings.agent_jwt_secret, "customer")
    with pytest.raises(Exception):
        decode_token(make_customer_token("demo_user"), settings.agent_jwt_secret, "agent")


async def test_only_one_agent_can_accept(db):
    a1 = Agent(username="riya", display_name="Riya", password_hash="x")
    a2 = Agent(username="aman", display_name="Aman", password_hash="x")
    conv = Conversation(customer_id="demo_user", status="waiting")
    db.add_all([a1, a2, conv])
    await db.commit()
    assert await claim_conversation(db, conv.id, a1.id) == "ok"
    assert await claim_conversation(db, conv.id, a2.id) == "taken"   # too late


async def test_agent_with_live_chat_cannot_accept_another(db):
    a1 = Agent(username="riya", display_name="Riya", password_hash="x")
    a2 = Agent(username="aman", display_name="Aman", password_hash="x")
    c1, c2 = Conversation(customer_id="u1", status="waiting"), Conversation(customer_id="u2", status="waiting")
    db.add_all([a1, a2, c1, c2])
    await db.commit()
    assert await claim_conversation(db, c1.id, a1.id) == "ok"
    assert await claim_conversation(db, c2.id, a1.id) == "busy"      # Riya is already in a chat
    assert await claim_conversation(db, c2.id, a2.id) == "ok"        # a free colleague can take it
    c1.status = "closed"                                              # Riya ends her chat...
    c3 = Conversation(customer_id="u3", status="waiting")
    db.add(c3)
    await db.commit()
    assert await claim_conversation(db, c3.id, a1.id) == "ok"        # ...and can accept again


async def test_transaction_lookup_checks_ownership(db):
    found, needs_agent = await get_transaction_status(db, "demo_user", "txn1002")
    assert found["found"] and found["status"] == "PENDING" and not needs_agent
    _, urgent = await get_transaction_status(db, "demo_user", "TXN1003")
    assert urgent
    other, _ = await get_transaction_status(db, "demo_user", "TXN2001")      # belongs to someone else
    assert other["found"] is False
    stranger, _ = await get_transaction_status(db, "someone_else", "TXN1002")
    assert stranger["found"] is False


def test_contact_validation():
    for ok in ["riya@example.com", "+91 98765 43210", "9876543210", "098-765-4321"]:
        assert valid_contact(ok), ok
    for bad in ["hello", "12", "a@b", "not an email", "<script>alert(1)</script>"]:
        assert not valid_contact(bad), bad


async def test_waiting_fallback_fires_once_and_only_when_due(db):
    t_now = dt.datetime.now(dt.timezone.utc)
    old = Conversation(customer_id="u1", status="waiting", waiting_since=t_now - dt.timedelta(minutes=10))
    fresh = Conversation(customer_id="u2", status="waiting", waiting_since=t_now)
    db.add_all([old, fresh])
    await db.commit()
    cutoff = t_now - dt.timedelta(minutes=3)
    due = await due_fallbacks(db, cutoff)
    assert [cid for cid, _ in due] == [old.id]       # only the chat that waited too long
    assert await due_fallbacks(db, cutoff) == []     # and only once
