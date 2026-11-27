import tempfile
from pathlib import Path

from app.db import _async_url
from app.seed import parse_seed_spec
from app.static_files import resolve_static
from app.tools import get_transaction_status


class FakeDB:
    def __init__(self):
        self.added = []

    def add(self, obj):
        self.added.append(obj)


def test_database_url_gets_the_asyncpg_driver():
    assert _async_url("postgres://u:p@h:5432/d") == "postgresql+asyncpg://u:p@h:5432/d"
    assert _async_url("postgresql://u:p@h/d") == "postgresql+asyncpg://u:p@h/d"
    assert _async_url("postgresql+asyncpg://u:p@h/d") == "postgresql+asyncpg://u:p@h/d"
    assert _async_url("sqlite+aiosqlite:///./local.db") == "sqlite+aiosqlite:///./local.db"


def test_seed_spec_parsing():
    assert parse_seed_spec("riya:Riya:pw1;aman:Aman Khan:pw:with:colons") == [
        ("riya", "Riya", "pw1"), ("aman", "Aman Khan", "pw:with:colons")]
    assert parse_seed_spec("  ; broken ; only:two ;neha:Neha:ok ") == [("neha", "Neha", "ok")]
    assert parse_seed_spec("") == []


def test_static_files_are_safe():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d, "site")
        (root / "assets").mkdir(parents=True)
        (root / "index.html").write_text("<html>app</html>")
        (root / "assets" / "app.js").write_text("js")
        Path(d, "secret.txt").write_text("secret")
        assert resolve_static(root, "").name == "index.html"
        assert resolve_static(root, "agent").name == "index.html"                 # React route -> index.html
        assert resolve_static(root, "assets/app.js").name == "app.js"
        assert resolve_static(root, "../secret.txt").name == "index.html"         # path trick does not leak files
        assert resolve_static(root, "api/healthz") is None                        # API typos stay 404
        assert resolve_static(root, "ws/customer") is None
        assert resolve_static(Path(d, "missing"), "agent") is None


async def test_every_demo_visitor_gets_their_own_view_of_the_demo_transactions():
    db = FakeDB()
    a, _ = await get_transaction_status(db, "demo_visitor1", "TXN1002")
    b, _ = await get_transaction_status(db, "demo_visitor2", "TXN1002")
    assert a["found"] and b["found"] and a["status"] == "PENDING"
    other, _ = await get_transaction_status(db, "demo_visitor1", "TXN2001")      # someone else's stays hidden
    assert other["found"] is False
    stranger, _ = await get_transaction_status(db, "real_customer_42", "TXN1002")  # not a demo visitor
    assert stranger["found"] is False
