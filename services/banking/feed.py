"""Validate Starling feed pages and safely follow their pagination cursors."""

from urllib.parse import parse_qs, urlsplit

from services.banking.client import StarlingError, starling_request
from services.validation import money, timestamp, uid


def invalid_response():
    return StarlingError("Starling returned an invalid transaction response.")


def normalize_feed_item(item, account_uid, category_uid):
    try:
        if not isinstance(item, dict) or uid(item.get("categoryUid")) != category_uid:
            raise ValueError("Unexpected category.")
        amount, currency = money(item.get("amount"))
        source_amount, source_currency = money(item["sourceAmount"]) if item.get("sourceAmount") is not None else (None, None)
        if item.get("direction") not in ("IN", "OUT") or not isinstance(item.get("status"), str) or not item["status"]:
            raise ValueError("Invalid status or direction.")
        return {
            "account_uid": account_uid, "category_uid": category_uid,
            "feed_item_uid": uid(item.get("feedItemUid")), "amount_minor": amount,
            "currency": currency, "direction": item["direction"], "status": item["status"],
            "transaction_time": timestamp(item.get("transactionTime")),
            "source_updated_at": timestamp(item.get("updatedAt")),
            "settlement_time": timestamp(item["settlementTime"]) if item.get("settlementTime") else None,
            "source": item.get("source"), "spending_category": item.get("spendingCategory"),
            "counterparty_name": item.get("counterPartyName"), "reference": item.get("reference"),
            "source_amount_minor": source_amount, "source_currency": source_currency,
            "raw_payload": item,
        }
    except (ValueError, TypeError, KeyError, AttributeError):
        raise invalid_response() from None


def parse_items(payload, account_uid, category_uid):
    if not isinstance(payload, dict) or not isinstance(payload.get("feedItems"), list):
        raise invalid_response()
    # Validate the entire page before any of it is persisted.
    return [normalize_feed_item(item, account_uid, category_uid) for item in payload["feedItems"]]


def next_cursor(link, path, start, end):
    """Extract a cursor; never send credentials to a URL supplied by the API."""
    if not isinstance(link, str):
        raise invalid_response()
    try:
        parsed = urlsplit(link)
    except ValueError:
        raise invalid_response() from None
    if (parsed.scheme and parsed.scheme != "https") or (parsed.netloc and parsed.netloc != "api.starlingbank.com"):
        raise invalid_response()
    if parsed.path != path or parsed.fragment:
        raise invalid_response()
    query = parse_qs(parsed.query, keep_blank_values=True)
    if any(len(values) != 1 for values in query.values()):
        raise invalid_response()
    if set(query) - {"minTransactionTimestamp", "maxTransactionTimestamp", "cursor", "pageToFetch"}:
        raise invalid_response()
    try:
        cursor = uid(query["cursor"][0])
        if query.get("pageToFetch", ["NEXT"])[0] != "NEXT":
            raise ValueError
        for key, expected in (("minTransactionTimestamp", start), ("maxTransactionTimestamp", end)):
            if key in query and timestamp(query[key][0]) != expected:
                raise ValueError
    except (ValueError, KeyError, TypeError):
        raise invalid_response() from None
    return cursor


def history_pages(account_uid, category_uid, start, end):
    path = f"/api/v2/feed/account/{account_uid}/category/{category_uid}/paginated-transactions"
    params = {"minTransactionTimestamp": start.isoformat(), "maxTransactionTimestamp": end.isoformat()}
    seen = set()
    for _ in range(10000):
        payload = starling_request(path, params=params, scope="transaction:read")
        items = parse_items(payload, account_uid, category_uid)
        links = payload.get("links")
        if not isinstance(links, dict):
            raise invalid_response()
        following = links.get("next")
        cursor = next_cursor(following, path, start, end) if following is not None else None
        if cursor in seen:
            raise StarlingError("Starling returned a repeated pagination cursor.")
        yield items
        if cursor is None:
            return
        seen.add(cursor)
        params = {**params, "cursor": cursor, "pageToFetch": "NEXT"}
    raise StarlingError("Starling pagination exceeded the safety limit.")


def changed_items(account_uid, category_uid, since):
    path = f"/api/v2/feed/account/{account_uid}/category/{category_uid}"
    return parse_items(starling_request(path, params={"changesSince": since.isoformat()},
                                      scope="transaction:read"), account_uid, category_uid)
