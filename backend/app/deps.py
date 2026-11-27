from fastapi import Depends, Header, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from .config import settings
from .db import get_db
from .models import Agent
from .security import decode_token


async def current_customer(authorization: str = Header(default="")) -> str:
    try:
        return decode_token(authorization.removeprefix("Bearer ").strip(), settings.customer_jwt_secret, "customer")["sub"]
    except Exception:
        raise HTTPException(401, "Invalid or expired token")


async def current_agent(authorization: str = Header(default=""), db: AsyncSession = Depends(get_db)) -> Agent:
    try:
        claims = decode_token(authorization.removeprefix("Bearer ").strip(), settings.agent_jwt_secret, "agent")
        agent = await db.get(Agent, int(claims["sub"]))
    except Exception:
        raise HTTPException(401, "Invalid or expired token")
    if not agent or not agent.is_active:
        raise HTTPException(401, "Agent not active")
    return agent
