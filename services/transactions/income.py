"""User-entered income details; no tax rates or deductions are inferred."""

from datetime import timezone
from werkzeug.exceptions import BadRequest

from services.validation import optional_text

INCOME_TYPES = {
    "roofing": "Roofing", "amazon_flex": "Amazon Flex",
    "employment": "Employment", "other": "Other income",
    "personal_gift": "Personal cash gift", "inheritance": "Inheritance",
    "loan_received": "Loan received", "loan_repayment": "Repayment of money you lent (principal)",
    "tax_refund": "Tax refund", "personal_item_sale": "Sale of personal belongings",
    "tax_free_benefit": "Tax-free benefit",
}
TAX_TREATMENTS = {
    "non_taxable": "Non-taxable (confirmed by you)",
    "unknown": "Not confirmed", "no_tax_deducted": "No tax deducted",
    "cis": "CIS deducted", "paye": "PAYE deducted",
    "other_deduction": "Other tax deduction",
}


def income_body(body):
    """Validate an edit form's JSON and return the values to save on TransactionIncome."""
    allowed = {"income_type", "tax_treatment", "source_name", "gross_minor", "tax_deducted_minor",
               "adjustment_minor", "adjustment_notes"}
    if not isinstance(body, dict) or set(body) - allowed:
        raise BadRequest("Use income_type, tax_treatment, source_name, gross_minor, tax_deducted_minor, adjustment_minor and adjustment_notes.")
    kind, treatment = body.get("income_type"), body.get("tax_treatment", "unknown")
    if not isinstance(kind, str) or kind not in INCOME_TYPES:
        raise BadRequest("Invalid income_type. See /transactions/income-types.")
    if not isinstance(treatment, str) or treatment not in TAX_TREATMENTS:
        raise BadRequest("Invalid tax_treatment. See /transactions/income-types.")
    try:
        source = optional_text(body.get("source_name"), field="source_name", maximum=200)
        notes = optional_text(body.get("adjustment_notes"), field="adjustment_notes", maximum=2000)
    except ValueError as error:
        raise BadRequest(str(error)) from None
    values = {"income_type": kind, "tax_treatment": treatment,
              "source_name": source}
    for field in ("gross_minor", "tax_deducted_minor"):
        value = body.get(field)
        if value is not None and (type(value) is not int or not 0 <= value <= 2**63 - 1):
            raise BadRequest(f"{field} must be a nonnegative integer in minor units or null.")
        values[field] = value
    adjustment = body.get("adjustment_minor", 0)
    if type(adjustment) is not int or not -(2**63) <= adjustment <= 2**63 - 1:
        raise BadRequest("adjustment_minor must be a signed integer in minor units.")
    if adjustment and not notes:
        raise BadRequest("Explain nonzero adjustment_minor in adjustment_notes.")
    values.update(adjustment_minor=adjustment, adjustment_notes=notes)
    gross, tax = values["gross_minor"], values["tax_deducted_minor"]
    if gross is not None and tax is not None and tax > gross:
        raise BadRequest("tax_deducted_minor cannot exceed gross_minor.")
    if treatment == "unknown" and tax is not None:
        raise BadRequest("Confirm tax_treatment before recording a tax deduction.")
    if treatment in ("no_tax_deducted", "non_taxable") and tax not in (None, 0):
        raise BadRequest("no_tax_deducted and non_taxable require a zero or unknown tax amount.")
    return values


def validate_reconciliation(values, net_received):
    """Check gross - tax + adjustment = deposit when all components are known.

    This checks the entered amounts; it does not calculate tax liability.
    """
    gross, tax = values["gross_minor"], values["tax_deducted_minor"]
    if gross is None:
        return
    # These explicit treatments establish zero withholding even if no amount was entered.
    if values["tax_treatment"] in ("no_tax_deducted", "non_taxable"):
        tax = 0
    before_tax = gross + values["adjustment_minor"]
    if tax is None:
        if before_tax < net_received:
            raise BadRequest("gross_minor plus adjustment_minor cannot be less than the bank deposit.")
    elif before_tax - tax != net_received:
        raise BadRequest("Amounts must reconcile: gross_minor - tax_deducted_minor + adjustment_minor = bank deposit. Explain any adjustment in adjustment_notes.")


def income_details(transaction):
    record = transaction.income
    if record is None:
        return None
    return {"income_stream_id": getattr(record, "income_stream_id", None), "income_type": record.income_type, "tax_treatment": record.tax_treatment,
            "source_name": record.source_name, "gross_minor": record.gross_minor,
            "tax_deducted_minor": record.tax_deducted_minor,
            "adjustment_minor": record.adjustment_minor, "adjustment_notes": record.adjustment_notes,
            "net_received_minor": transaction.amount_minor, "currency": transaction.currency,
            "recorded_currency": record.recorded_currency, "needs_review": record.needs_review,
            "updated_at": record.updated_at.astimezone(timezone.utc).isoformat()}


def save_income(transaction, values):
    """Shared API/form save: reconcile the deposit and reopen owner confirmation."""
    from models import TransactionIncome
    from services.transactions.classification import classification_details

    if transaction.direction != "IN" or classification_details(transaction)["type"] != "income":
        raise BadRequest("Income details require an incoming transaction classified as income.")
    validate_reconciliation(values, transaction.amount_minor)
    values = dict(values, recorded_currency=transaction.currency, needs_review=False)
    if transaction.income is None:
        transaction.income = TransactionIncome(**values)
    else:
        for field, value in values.items():
            setattr(transaction.income, field, value)
    transaction.confirmed_at = None
