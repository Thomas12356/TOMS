"""Exercise the Flask Starling routes with live upstream requests when invoked."""

import os
from datetime import datetime, timedelta, timezone

from flask import request, url_for
from werkzeug.exceptions import BadRequest

from services.validation import uid


ID_KEYS = ("accountUid", "categoryUid", "feedItemUid", "payeeUid",
           "payeeAccountUid", "savingsGoalUid", "paymentOrderUid", "mandateUid")
PATH_IDS = {"account_uid": "accountUid", "category_uid": "categoryUid",
            "feed_item_uid": "feedItemUid", "payee_uid": "payeeUid",
            "savings_goal_uid": "savingsGoalUid", "payment_order_uid": "paymentOrderUid",
            "mandate_uid": "mandateUid"}

# One operation per requested permission, with Starling's shared receipt PUT
# representing metadata:create and metadata:edit. Never discover extra routes.
CHECKS = {
    "starling.accounts": ("account-list:read",),
    "starling.account_holder_name": ("account-holder-name:read",),
    "starling.balance": ("balance:read",),
    "starling.confirmation_of_funds": ("confirmation-of-funds:read",),
    "starling.mandates": ("mandate:read",),
    "starling.submit_receipt": ("metadata:create", "metadata:edit"),
    "starling.payees": ("payee:read",),
    "starling.payee_image": ("payee-image:read",),
    "starling.payee_transactions": ("payee-transaction:read",),
    "starling.payment_order": ("pay-local:read",),
    "starling.receipts": ("receipts:read",),
    "starling.savings_goals": ("savings-goal:read",),
    "starling.savings_goal_transfer": ("savings-goal-transfer:read",),
    "starling.scheduled_payments": ("scheduled-payment:read",),
    "starling.spaces": ("space:read",),
    "starling.standing_orders": ("standing-order:read",),
    "starling.statement_pdf": ("statement-pdf:read",),
    "starling.statement_csv": ("statement-csv:read",),
    "starling.feed_export": ("feed-export-csv:read",),
    "starling.transactions": ("transaction:read",),
}


def diagnostic_options(options):
    allowed = set(ID_KEYS) | {"includeMetadataWrite", "receipt"}
    if not isinstance(options, dict) or set(options) - allowed:
        raise BadRequest("Send a JSON object containing IDs, includeMetadataWrite, or receipt.")
    configured = dict(options)
    configured_account = os.getenv("STARLING_ACCOUNT_UID", "").strip()
    use_env_category = "accountUid" not in options or options["accountUid"] == configured_account
    for key, env_key in (("accountUid", "STARLING_ACCOUNT_UID"),
                         ("categoryUid", "STARLING_CATEGORY_UID")):
        value = os.getenv(env_key, "").strip()
        if key == "categoryUid" and not use_env_category:
            continue
        if key not in configured and value:
            configured[key] = value
    ids = {}
    for key in ID_KEYS:
        if key in configured:
            try:
                ids[key] = uid(configured[key])
            except (ValueError, TypeError, AttributeError):
                raise BadRequest(f"{key} must be a UUID string.") from None
    write = options.get("includeMetadataWrite", False)
    if not isinstance(write, bool):
        raise BadRequest("includeMetadataWrite must be a boolean.")
    if "receipt" in options and not write:
        raise BadRequest("Set includeMetadataWrite to true to submit a receipt.")
    if write and (not isinstance(options.get("receipt"), dict)
                  or any(key not in options for key in ("accountUid", "categoryUid", "feedItemUid"))):
        raise BadRequest("A metadata write requires receipt and explicit accountUid, categoryUid, and feedItemUid.")

    return ids, write


def diagnostic_filters(now):
    since = (now - timedelta(days=30)).date().isoformat()
    previous_month = now.date().replace(day=1) - timedelta(days=1)
    timestamp = (now - timedelta(days=30)).isoformat()
    return {
        "confirmation_of_funds": {"targetAmountInMinorUnits": 0},
        "payee_transactions": {"since": since},
        "statement_pdf": {"yearMonth": previous_month.strftime("%Y-%m")},
        "statement_csv": {"yearMonth": previous_month.strftime("%Y-%m")},
        "feed_export": {"start": since, "end": now.date().isoformat()},
        "transactions": {"changesSince": timestamp},
    }


def first_record(payload, key):
    records = payload.get(key) if isinstance(payload, dict) else None
    return next((record for record in records if isinstance(record, dict)), {}) if isinstance(records, list) else {}


class DiagnosticRunner:
    """Execute only allowlisted checks and retain their discovery/report state."""

    def __init__(self, app, options, ids, write):
        self.app = app
        self.ids = ids
        self.write = write
        self.receipt = options.get("receipt")
        self.authorization = request.headers.get("Authorization", "")
        self.filters = diagnostic_filters(datetime.now(timezone.utc))
        self.rules = {rule.endpoint: rule for rule in app.url_map.iter_rules()
                      if rule.endpoint in CHECKS}
        self.results = []
        self.completed = set()
        self.rate_limited = False

    def call(self, endpoint):
        rule = self.rules[endpoint]
        method = "PUT" if "PUT" in rule.methods else "GET"
        entry = {"endpoint": rule.rule, "method": method, "scopes": list(CHECKS[endpoint])}
        self.results.append(entry)
        self.completed.add(endpoint)
        if method == "PUT" and not self.write:
            entry.update(status="skipped", reason="Metadata writes require includeMetadataWrite=true and a receipt.")
            return None
        if self.rate_limited:
            entry.update(status="skipped", reason="Starling rate limit reached; try again later.")
            return None
        local_ids = dict(self.ids)
        if endpoint in ("starling.payee_transactions", "starling.scheduled_payments"):
            local_ids["accountUid"] = self.ids.get("payeeAccountUid")
        missing = [PATH_IDS[key] for key in sorted(rule.arguments)
                   if not local_ids.get(PATH_IDS[key])]
        if endpoint in ("starling.payee_transactions", "starling.scheduled_payments"):
            missing = ["payeeAccountUid" if key == "accountUid" else key for key in missing]
        if missing:
            entry.update(status="skipped", reason="Missing resource IDs: " + ", ".join(missing))
            return None
        arguments = {key: local_ids[PATH_IDS[key]] for key in rule.arguments}
        query = self.filters.get(endpoint.split(".")[-1], {})
        path = url_for(endpoint, _method=method, _external=False, **arguments, **query)
        entry["url"] = path
        # Internal Flask dispatch exercises the actual endpoint and its validation
        # without making HTTP requests back to the development server.
        with self.app.test_client() as client:
            response = client.open(path, method=method,
                                   headers={"Authorization": self.authorization},
                                   json=self.receipt if method == "PUT" else None)
        payload = response.get_json(silent=True)
        entry["httpStatus"] = response.status_code
        entry["status"] = "passed" if 200 <= response.status_code < 300 else "failed"
        if entry["status"] == "failed":
            entry["error"] = payload.get("error", "Request failed.") if isinstance(payload, dict) else "Request failed."
        else:
            entry["contentType"] = response.mimetype
            entry["bytesReceived"] = len(response.data)
        if response.status_code == 429:
            self.rate_limited = True
        return payload if entry["status"] == "passed" else None

    def remember(self, key, value):
        if key not in self.ids and isinstance(value, str):
            try:
                self.ids[key] = uid(value)
            except ValueError:
                pass

    def discover_accounts(self):
        accounts = self.call("starling.accounts")
        account_records = accounts.get("accounts", []) if isinstance(accounts, dict) else []
        if not isinstance(account_records, list):
            account_records = []
        if "accountUid" in self.ids:
            account = next((record for record in account_records
                            if isinstance(record, dict) and record.get("accountUid") == self.ids["accountUid"]), {})
        else:
            # Prefer the GBP account when multiple currencies are available.
            account = next((record for record in account_records
                            if isinstance(record, dict) and record.get("currency") == "GBP"),
                           first_record(accounts, "accounts"))
        self.remember("accountUid", account.get("accountUid"))
        self.remember("categoryUid", account.get("defaultCategory"))

    def discover_payees(self):
        payees = self.call("starling.payees")
        payee_records = payees.get("payees", []) if isinstance(payees, dict) else []
        if not isinstance(payee_records, list):
            payee_records = []
        payee = next((record for record in payee_records
                      if isinstance(record, dict) and record.get("payeeUid") == self.ids.get("payeeUid")), None)
        if payee is None and "payeeUid" not in self.ids:
            payee = first_record(payees, "payees")
        if payee:
            self.remember("payeeUid", payee.get("payeeUid"))
            self.remember("payeeAccountUid", first_record(payee, "accounts").get("payeeAccountUid"))

    def discover_resources(self):
        self.discover_accounts()
        self.discover_payees()
        # These dependencies need only the first accessible resource.
        for endpoint, collection, field, target in (
            ("starling.mandates", "mandates", "uid", "mandateUid"),
            ("starling.savings_goals", "savingsGoalList", "savingsGoalUid", "savingsGoalUid"),
            ("starling.transactions", "feedItems", "feedItemUid", "feedItemUid"),
            ("starling.standing_orders", "standingOrders", "paymentOrderUid", "paymentOrderUid"),
        ):
            self.remember(target, first_record(self.call(endpoint), collection).get(field))

    def report(self):
        summary = {status: sum(result["status"] == status for result in self.results)
                   for status in ("passed", "failed", "skipped")}
        summary["total"] = len(self.results)
        return {"allPassed": summary["passed"] == summary["total"],
                "summary": summary, "results": self.results}


def run_diagnostics(app, options):
    """Exercise Flask routes; writes require explicit IDs, a receipt and opt-in."""
    ids, write = diagnostic_options(options)
    runner = DiagnosticRunner(app, options, ids, write)
    runner.discover_resources()
    for endpoint in CHECKS:
        if endpoint not in runner.completed:
            runner.call(endpoint)
    return runner.report()
