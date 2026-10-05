"""A version of the displayed details prevents confirmation of stale information."""
import hashlib
import json

from services.transactions.classification import classification_details
from services.transactions.income import income_details

REVIEW_FIELDS = ("amount_minor", "currency", "direction", "status", "transaction_time",
                 "settlement_time", "source", "spending_category", "counterparty_name",
                 "reference", "source_amount_minor", "source_currency")


def review_version(transaction):
    values = {field: getattr(transaction, field) for field in REVIEW_FIELDS}
    values['identity'] = [transaction.account_uid, transaction.category_uid, transaction.feed_item_uid]
    values['classification'] = classification_details(transaction)
    values['income'] = income_details(transaction)
    return hashlib.sha256(json.dumps(values, sort_keys=True, default=str).encode()).hexdigest()
