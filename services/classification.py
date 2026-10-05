"""Local transfer types: manual choices take precedence over bank inference."""

from datetime import timezone

from models import Transaction, TransactionClassification
from services.database import db


CLASSIFICATION_TYPES = {
    "income": {"label": "Income", "directions": ["IN"]},
    "expense": {"label": "Expense", "directions": ["OUT"]},
    "internal_transfer": {"label": "Transfer between your own accounts or spaces", "directions": ["IN", "OUT"]},
    "refund": {"label": "Refund", "directions": ["IN", "OUT"]},
    "other": {"label": "Other", "directions": ["IN", "OUT"]},
}


def effective_type():
    """SQL expression for filtering and reports; queries must outer-join classifications."""
    return db.func.coalesce(TransactionClassification.type, db.case(
        (Transaction.source == "INTERNAL_TRANSFER", "internal_transfer"),
        (Transaction.direction == "IN", "income"), else_="expense"))


def classification_details(transaction):
    record = transaction.classification
    if record is not None:
        return {"type": record.type, "origin": "manual", "notes": record.notes,
                "updated_at": record.updated_at.astimezone(timezone.utc).isoformat()}
    if transaction.source == "INTERNAL_TRANSFER":
        inferred = "internal_transfer"
    elif transaction.direction == "IN":
        inferred = "income"
    else:
        inferred = "expense"
    return {"type": inferred, "origin": "automatic", "notes": None, "updated_at": None}
