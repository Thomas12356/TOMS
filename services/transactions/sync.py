"""Manual history imports and incremental updates with durable page commits."""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy.exc import SQLAlchemyError
from werkzeug.exceptions import BadRequest

from services.error_logging import log_failure
from services.banking.client import StarlingError, starling_request
from services.banking.feed import changed_items, history_pages
from services.validation import timestamp, uid


class SyncError(Exception):
    def __init__(self, message, status_code=502, run_uid=None, retry_after=None):
        super().__init__(message)
        self.status_code, self.run_uid, self.retry_after = status_code, run_uid, retry_after


def parse_options(options, now):
    """Validate sync JSON, converting IDs and dates into values the importer can use."""
    if not isinstance(options, dict) or set(options) - {"start", "end", "mode", "accountUid", "categoryUid"}:
        raise BadRequest("Use start, end, mode, accountUid, or categoryUid in a JSON object.")
    if options.get("mode", "auto") not in ("auto", "history", "incremental"):
        raise BadRequest("mode must be auto, history, or incremental.")
    result = dict(options)
    try:
        for key in ("accountUid", "categoryUid"):
            if key in result:
                result[key] = uid(result[key])
        for key in ("start", "end"):
            if key in result:
                value = result[key]
                if isinstance(value, str) and len(value) == 10:
                    value += "T00:00:00Z"
                result[key] = timestamp(value)
        if "categoryUid" in result and "accountUid" not in result:
            raise ValueError("categoryUid requires accountUid.")
        if result.get("end", now) > now or result.get("start", now) > result.get("end", now):
            raise ValueError("Use an ordered date range ending no later than now.")
        if result.get("mode") == "incremental" and ("start" in result or "end" in result):
            raise ValueError("Date ranges require history mode.")
    except (TypeError, ValueError, AttributeError):
        raise BadRequest("Invalid IDs or date range. Use UUIDs and YYYY-MM-DD or timestamps with a timezone.") from None
    return result


def discover_accounts(selected=None):
    payload = starling_request("/api/v2/accounts", scope="account-list:read")
    if not isinstance(payload, dict) or not isinstance(payload.get("accounts"), list):
        raise StarlingError("Starling returned an invalid account list.")
    accounts = []
    try:
        for raw in payload["accounts"]:
            account_uid = uid(raw["accountUid"])
            if selected and account_uid != selected:
                continue
            accounts.append({"uid": account_uid, "category": uid(raw["defaultCategory"]),
                             "currency": raw["currency"], "raw": raw,
                             "opened": timestamp(raw["createdAt"]) if raw.get("createdAt") else None})
    except (TypeError, ValueError, KeyError, AttributeError):
        raise StarlingError("Starling returned an invalid account list.") from None
    if not accounts:
        raise SyncError("No accessible account matches this import.", 404)
    return accounts


def discover_categories(account, selected=None):
    categories = [(account["category"], account["raw"].get("name"), "main")]
    if selected:
        # Allows known archived space IDs, which aren't returned by /spaces.
        return [(selected, None, "manual")] if selected != account["category"] else categories
    payload = starling_request(f"/api/v2/account/{account['uid']}/spaces", scope="space:read")
    try:
        for collection, key, kind in (("savingsGoals", "savingsGoalUid", "savings"),
                                      ("spendingSpaces", "spaceUid", "spending")):
            if not isinstance(payload[collection], list):
                raise ValueError
            categories.extend((uid(space[key]), space.get("name"), kind) for space in payload[collection])
    except (KeyError, TypeError, ValueError, AttributeError):
        raise StarlingError("Starling returned an invalid space list.") from None
    return list({category[0]: category for category in categories}.values())


@dataclass(frozen=True)
class SyncPlan:
    mode: str
    start: datetime
    end: datetime


def choose_sync_plan(account, state, options, snapshot):
    """Choose dates without performing requests or changing checkpoints."""
    end = options.get("end", snapshot)
    needs_history = (
        options.get("mode") == "history" or "start" in options or "end" in options
        or state["changes_through"] is None or state["history_from"] is None
        or snapshot - state["changes_through"] >= timedelta(days=364)
    )
    if not needs_history:
        return SyncPlan("incremental", state["changes_through"] - timedelta(minutes=5), end)
    start = options.get("start") or state["history_from"] or account["opened"]
    if start is None:
        raise SyncError("Account opening date is unavailable. Supply start for the import.", 400)
    if start > end:
        raise SyncError("The requested end is before this account's opening date.", 400)
    return SyncPlan("history", start, end)


def sync_category(store, run_uid, account, category_uid, state, options, snapshot):
    """Import one category, saving each page before advancing its checkpoint."""
    plan = choose_sync_plan(account, state, options, snapshot)
    if plan.mode == "incremental":
        items = changed_items(account["uid"], category_uid, plan.start)
        # A large unpaginated response may be truncated; rescan history safely.
        if len(items) >= 1000:
            plan = SyncPlan("history", state["history_from"], plan.end)
        else:
            pages = [items]
    if plan.mode == "history":
        pages = history_pages(account["uid"], category_uid, plan.start, plan.end)
    store.start_target(run_uid, account["uid"], category_uid, plan.mode, plan.start, plan.end)
    for items in pages:
        store.save_page(run_uid, account["uid"], category_uid, items)
    store.finish_target(run_uid, account["uid"], category_uid,
                        plan.mode, plan.start, plan.end, snapshot)


def run_sync(store, raw_options, *, now=None):
    """Main import flow: validate, lock, discover accounts, save pages, finish, unlock.

    `store` handles database writes; this function decides the order of the work.
    """
    snapshot = now or datetime.now(timezone.utc)
    options = parse_options(raw_options, snapshot)
    if not store.ready():
        raise SyncError("Database tables are missing. Run flask db-upgrade first.", 503)
    if not store.lock():
        raise SyncError("Another transaction sync is running.", 409)
    run_uid = str(uuid4())
    started = False
    try:
        store.start_run(run_uid, raw_options, snapshot)
        started = True
        for account in discover_accounts(options.get("accountUid")):
            store.save_account(account)
            for category_uid, name, kind in discover_categories(account, options.get("categoryUid")):
                store.save_category(account["uid"], category_uid, name, kind)
                state = store.category(account["uid"], category_uid)
                sync_category(store, run_uid, account, category_uid, state, options, snapshot)
        store.finish_run(run_uid)
        return store.report(run_uid)
    except Exception as error:
        if isinstance(error, (StarlingError, SyncError)):
            message, status = str(error), error.status_code
        elif isinstance(error, SQLAlchemyError):
            log_failure("sync.database", error, run_uid=run_uid if started else None)
            message, status = "Database error during sync. Committed pages were retained.", 503
        else:
            log_failure("sync.unexpected", error, run_uid=run_uid if started else None)
            message, status = "Unexpected sync error. Committed pages were retained.", 500
        if started:
            try:
                store.finish_run(run_uid, message)
            except SQLAlchemyError as recording_error:
                log_failure("sync.record_failure", recording_error, run_uid=run_uid)
                # If unavailable, the next run detects the interrupted run.
        raise SyncError(message, status, run_uid if started else None, getattr(error, "retry_after", None)) from None
    finally:
        try:
            store.unlock()
        except SQLAlchemyError as unlock_error:
            log_failure("sync.unlock", unlock_error, run_uid=run_uid)
            # Closing the separate lock transaction releases the advisory lock.
