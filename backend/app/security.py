import datetime as dt

import jwt
from argon2 import PasswordHasher

from .config import settings

_ph = PasswordHasher()


def hash_password(password: str) -> str:
    return _ph.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _ph.verify(password_hash, password)
    except Exception:
        return False


def _make(claims: dict, secret: str, typ: str, hours: int) -> str:
    exp = dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=hours)
    return jwt.encode({**claims, "typ": typ, "exp": exp}, secret, algorithm="HS256")


def make_agent_token(agent) -> str:
    return _make({"sub": str(agent.id), "name": agent.display_name, "role": agent.role}, settings.agent_jwt_secret, "agent", 8)


def make_customer_token(customer_id: str) -> str:
    """YOUR app's backend should issue this (same secret) for the logged-in user."""
    return _make({"sub": customer_id}, settings.customer_jwt_secret, "customer", 2)


def decode_token(token: str, secret: str, typ: str) -> dict:
    claims = jwt.decode(token, secret, algorithms=["HS256"])
    if claims.get("typ") != typ:
        raise jwt.InvalidTokenError("wrong token type")
    return claims
