"""Tools the bot may use. Replace DEMO_TXNS with a call to your real payments / broker system."""
from sqlalchemy.ext.asyncio import AsyncSession

from .config import settings
from .models import AuditLog

DEMO_TXNS = {
    "TXN1001": {"owner": "demo_user", "type": "Add funds (UPI)", "amount": 5000, "status": "SUCCESS", "time": "20 Nov, 10:15 AM", "note": "Credited to your balance."},
    "TXN1002": {"owner": "demo_user", "type": "Withdrawal", "amount": 2500, "status": "PENDING", "time": "20 Nov, 11:42 AM", "note": "Sent to your bank. Usually reaches in 1 working day."},
    "TXN1003": {"owner": "demo_user", "type": "Add funds (UPI)", "amount": 10000, "status": "FAILED_AMOUNT_DEBITED", "time": "19 Nov, 4:05 PM", "note": "Money left your bank but was not credited.", "needs_agent": True},
    "TXN1004": {"owner": "demo_user", "type": "Buy order", "amount": 15000, "status": "REJECTED", "time": "20 Nov, 9:20 AM", "note": "Rejected: insufficient margin."},
    "TXN2001": {"owner": "someone_else", "type": "Withdrawal", "amount": 99000, "status": "SUCCESS", "time": "18 Nov", "note": ""},
}


async def get_transaction_status(db: AsyncSession, customer_id: str, txn_id: str) -> tuple[dict, bool]:
    """Read-only. customer_id comes from the verified login token, never from the chat text."""
    tid = str(txn_id).strip().upper()[:40]
    db.add(AuditLog(actor=f"customer:{customer_id}", action="txn_lookup", detail=tid))
    rec = DEMO_TXNS.get(tid)
    # Ownership check. Demo mode only: every "demo_<visitor>" customer sees the sample transactions as their own.
    demo_visitor = settings.env == "dev" and customer_id.startswith("demo_")
    if not rec or not (rec["owner"] == customer_id or (demo_visitor and rec["owner"] == "demo_user")):
        return {"found": False, "message": "No transaction with this ID on this account."}, False
    shown = {k: v for k, v in rec.items() if k not in ("owner", "needs_agent")}
    return {"found": True, "transaction_id": tid, **shown}, bool(rec.get("needs_agent"))
