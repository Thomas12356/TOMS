"""Local transfer types: manual choices take precedence over bank inference."""

from datetime import timezone

from models import Transaction, TransactionClassification
from services.database.connection import db


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


def save_classification(transaction, kind, notes):
    """Apply the same manual rules for API calls and dashboard forms."""
    if kind not in CLASSIFICATION_TYPES:
        raise ValueError("Choose a valid classification.")
    if transaction.direction not in CLASSIFICATION_TYPES[kind]["directions"]:
        raise ValueError("income requires IN; expense requires OUT.")
    if transaction.income is not None and kind != "income":
        raise ValueError("Delete income details before changing this transaction to a different transfer type.")
    if getattr(transaction, "expense", None) is not None and kind != "expense":
        raise ValueError("Remove the tax deduction before changing the expense classification.")
    transaction.confirmed_at = None
    if transaction.classification is None:
        transaction.classification = TransactionClassification(type=kind, notes=notes)
    else:
        transaction.classification.type = kind
        transaction.classification.notes = notes


def clear_classification(transaction):
    """Restore bank inference without discarding incompatible income details."""
    if transaction.income is not None and (transaction.direction != "IN" or transaction.source == "INTERNAL_TRANSFER"):
        raise ValueError("Delete income details before restoring a non-income automatic classification.")
    if getattr(transaction, "expense", None) is not None and (transaction.direction != "OUT" or transaction.source == "INTERNAL_TRANSFER"):
        raise ValueError("Remove the tax deduction before restoring a non-expense classification.")
    transaction.classification = None
    transaction.confirmed_at = None
