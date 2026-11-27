from contextlib import contextmanager
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app import ai, main
from app.config import settings
from app.db import get_db
from app.main import app
from app.security import make_customer_token


@pytest.mark.parametrize("first,second", [
    ("timeout", "http_error"),
    ("http_error", "empty"),
    ("clarify", "clarify"),
    ("clarify", "blocked"),
])
def test_second_unanswered_turn_queues_agent(monkeypatch, tmp_path, first, second):
    with bot_client(monkeypatch, tmp_path, [first, second]) as client:
        token = make_customer_token("fallback-test")
        headers = {"Authorization": f"Bearer {token}"}
        conversation = client.post("/api/conversations/open", headers=headers).json()["conversation"]
        path = f"/api/conversations/{conversation['id']}"
        with client.websocket_connect(f"/ws/customer?token={token}&conversation_id={conversation['id']}") as socket:
            responses = [
                client.post(f"{path}/messages", json={"text": "Please explain the qwerty feature."}, headers=headers)
                for _ in range(2)
            ]
            assert [response.status_code for response in responses] == [200, 200]
            assert [response.json()["status"] for response in responses] == ["bot", "waiting"]
            assert socket.receive_json()["type"] == "queue.waiting"

        stored = client.get(path, headers=headers).json()["conversation"]
        assert stored["status"] == "waiting"
        assert stored["channel"] == "chat"


def test_answer_resets_unanswered_turn_count(monkeypatch, tmp_path):
    with bot_client(monkeypatch, tmp_path, ["http_error", "answer", "empty"]) as client:
        headers = {"Authorization": f"Bearer {make_customer_token('reset-test')}"}
        conversation = client.post("/api/conversations/open", headers=headers).json()["conversation"]
        for _ in range(3):
            reply = client.post(
                f"/api/conversations/{conversation['id']}/messages", headers=headers, json={"text": "Explain the qwerty feature."}
            )
            assert reply.json()["status"] == "bot"


@contextmanager
def bot_client(monkeypatch, tmp_path, replies):
    replies = iter(replies)

    def upstream(request):
        result = next(replies)
        if result == "timeout":
            raise httpx.ReadTimeout("simulated Gemini timeout", request=request)
        if result == "http_error":
            return httpx.Response(429, json={"error": "quota"})
        if result == "blocked":
            return httpx.Response(200, json={"promptFeedback": {"blockReason": "SAFETY"}})
        text = {"clarify": "[CLARIFY] Can you provide more details?", "answer": "Here is your answer.", "empty": ""}[result]
        return httpx.Response(200, json={"candidates": [{"content": {"role": "model", "parts": [{"text": text}]}}]})

    def mock_client(**options):
        return httpx.AsyncClient(transport=httpx.MockTransport(upstream), **options)

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'bot.db'}")
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    async def db_override():
        async with sessions() as db:
            yield db

    monkeypatch.setattr(main, "engine", engine)
    monkeypatch.setattr(main, "SessionLocal", sessions)
    monkeypatch.setattr(settings, "seed_agents", "")
    monkeypatch.setattr(settings, "redis_url", "")
    monkeypatch.setattr(settings, "auto_create_tables", True)
    monkeypatch.setattr(settings, "gemini_api_key", "mock-key")
    monkeypatch.setattr(ai, "httpx", SimpleNamespace(
        AsyncClient=mock_client, HTTPStatusError=httpx.HTTPStatusError, RequestError=httpx.RequestError,
    ))
    monkeypatch.setitem(app.dependency_overrides, get_db, db_override)
    with TestClient(app) as client:
        yield client
