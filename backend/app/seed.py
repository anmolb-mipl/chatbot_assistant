"""Create or update an agent:  python -m app.seed riya Riya 'a-strong-password' [team]"""
import asyncio
import logging
import sys

from sqlalchemy import select

from .db import Base, SessionLocal, engine
from .models import Agent
from .security import hash_password, verify_password

log = logging.getLogger(__name__)


async def main(username: str, display: str, password: str, team: str = "technical") -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with SessionLocal() as db:
        agent = (await db.execute(select(Agent).where(Agent.username == username.lower()))).scalar_one_or_none()
        if agent:
            agent.display_name, agent.password_hash, agent.team, agent.is_active = display, hash_password(password), team, True
        else:
            db.add(Agent(username=username.lower(), display_name=display, password_hash=hash_password(password), team=team))
        await db.commit()
    print(f"Agent '{username.lower()}' saved.")



def parse_seed_spec(spec: str) -> list[tuple[str, str, str]]:
    """'riya:Riya:password;aman:Aman:password2' -> [(username, display name, password), ...]. Invalid entries are skipped."""
    out: list[tuple[str, str, str]] = []
    for item in spec.split(";"):
        item = item.strip()
        if not item:
            continue
        parts = item.split(":", 2)   # the password may itself contain ':'
        if len(parts) != 3 or not all(p.strip() for p in parts):
            log.warning("SEED_AGENTS: skipped an entry that is not username:Display Name:password")
            continue
        out.append((parts[0].strip().lower(), parts[1].strip(), parts[2].strip()))
    return out


async def seed_from_spec(spec: str) -> None:
    """Create the agents listed in SEED_AGENTS if missing; update name and password if they changed."""
    async with SessionLocal() as db:
        for username, display, password in parse_seed_spec(spec):
            agent = (await db.execute(select(Agent).where(Agent.username == username))).scalar_one_or_none()
            if agent is None:
                db.add(Agent(username=username, display_name=display, password_hash=hash_password(password), team="technical"))
            else:
                agent.display_name, agent.is_active = display, True
                if not verify_password(agent.password_hash, password):
                    agent.password_hash = hash_password(password)
        await db.commit()
    log.info("SEED_AGENTS processed")


if __name__ == "__main__":
    if len(sys.argv) < 4:
        sys.exit("usage: python -m app.seed <username> <Display Name> <password> [team]")
    asyncio.run(main(*sys.argv[1:5]))
