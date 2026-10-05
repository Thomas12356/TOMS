import copy
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from urllib.parse import urlencode

from werkzeug.exceptions import BadRequest

from app import app
from services.banking.client import StarlingError
from services.banking.feed import history_pages, normalize_feed_item
from services.transactions.sync import SyncError, parse_options, run_sync


ACCOUNT = "aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa"
CATEGORY = "bbbbbbbb-bbbb-4bbb-bbbb-bbbbbbbbbbbb"
ITEM = "cccccccc-cccc-4ccc-cccc-cccccccccccc"
CURSOR = "dddddddd-dddd-4ddd-dddd-dddddddddddd"
NOW = datetime(2026, 10, 4, 12, tzinfo=timezone.utc)
OPENED = datetime(2019, 1, 1, tzinfo=timezone.utc)


def feed_item(item_uid=ITEM, *, status="PENDING", updated=None):
    return {"feedItemUid": item_uid, "categoryUid": CATEGORY,
            "amount": {"currency": "GBP", "minorUnits": 1250}, "direction": "OUT",
            "status": status, "transactionTime": "2019-02-01T00:00:00Z",
            "updatedAt": (updated or NOW).isoformat(), "counterPartyName": "Test shop"}


def discovery(path, **kwargs):
    if path == "/api/v2/accounts":
        return {"accounts": [{"accountUid": ACCOUNT, "defaultCategory": CATEGORY,
                              "currency": "GBP", "createdAt": OPENED.isoformat()}]}
    return {"savingsGoals": [], "spendingSpaces": []}


class MemoryStore:
    """Small stateful test double for orchestration, not a SQL implementation."""
    def __init__(self):
        self.state = {"history_from": None, "changes_through": None}
        self.pages = []
        self.finished_targets = []
        self.status = None
        self.unlocked = False
        self.available = True

    def ready(self): return True
    def lock(self): return self.available
    def unlock(self): self.unlocked = True
    def start_run(self, run_uid, options, snapshot): self.status = "running"
    def save_account(self, account): self.account = account
    def save_category(self, *args): pass
    def category(self, *args): return copy.deepcopy(self.state)
    def start_target(self, *args): self.target = args
    def save_page(self, *args): self.pages.append(args[-1])
    def finish_target(self, *args): self.finished_targets.append(args)
    def finish_run(self, run_uid, error=None): self.status = "failed" if error else "completed"
    def report(self, run_uid): return {"run_uid": run_uid, "status": self.status}


class FeedTests(unittest.TestCase):
    def test_history_follows_cursor_and_keeps_fixed_dates(self):
        path = f"/api/v2/feed/account/{ACCOUNT}/category/{CATEGORY}/paginated-transactions"
        link = "https://api.starlingbank.com" + path + "?" + urlencode({
            "cursor": CURSOR, "pageToFetch": "NEXT", "minTransactionTimestamp": OPENED.isoformat(),
            "maxTransactionTimestamp": NOW.isoformat()})
        replies = [{"feedItems": [feed_item()], "links": {"next": link}},
                   {"feedItems": [feed_item(CURSOR)], "links": {}}]
        with patch("services.banking.feed.starling_request", side_effect=replies) as request:
            pages = list(history_pages(ACCOUNT, CATEGORY, OPENED, NOW))
        self.assertEqual([len(page) for page in pages], [1, 1])
        params = request.call_args_list[1].kwargs["params"]
        self.assertEqual(params["cursor"], CURSOR)
        self.assertEqual(params["minTransactionTimestamp"], OPENED.isoformat())
        self.assertEqual(params["maxTransactionTimestamp"], NOW.isoformat())

    def test_unsafe_or_repeated_pagination_fails(self):
        path = f"/api/v2/feed/account/{ACCOUNT}/category/{CATEGORY}/paginated-transactions"
        for link in ("https://evil.example" + path + "?cursor=" + CURSOR,
                     "http://api.starlingbank.com" + path + "?cursor=" + CURSOR,
                     path + "?cursor=" + CURSOR + "&pageToFetch=PREVIOUS",
                     path + "?cursor=" + CURSOR + "&minTransactionTimestamp=2020-01-01T00:00:00Z"):
            with self.subTest(link=link):
                with patch("services.banking.feed.starling_request", return_value={"feedItems": [], "links": {"next": link}}):
                    with self.assertRaises(StarlingError):
                        list(history_pages(ACCOUNT, CATEGORY, OPENED, NOW))
        repeated = {"feedItems": [], "links": {"next": path + "?cursor=" + CURSOR}}
        with patch("services.banking.feed.starling_request", return_value=repeated):
            with self.assertRaises(StarlingError):
                list(history_pages(ACCOUNT, CATEGORY, OPENED, NOW))

    def test_invalid_items_and_missing_page_shape_fail(self):
        for change in ({"amount": {"minorUnits": 1.5, "currency": "GBP"}},
                       {"categoryUid": CURSOR}, {"direction": "OTHER"}, {"updatedAt": "invalid"}):
            with self.subTest(change=change):
                with self.assertRaises(StarlingError):
                    normalize_feed_item({**feed_item(), **change}, ACCOUNT, CATEGORY)
        with patch("services.banking.feed.starling_request", return_value={}):
            with self.assertRaises(StarlingError):
                list(history_pages(ACCOUNT, CATEGORY, OPENED, NOW))


class SyncTests(unittest.TestCase):
    def setUp(self):
        patcher = patch("services.transactions.sync.starling_request", side_effect=discovery)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_initial_import_uses_opening_date_and_commits_each_page(self):
        store = MemoryStore()
        with patch("services.transactions.sync.history_pages", return_value=[[{}], [{}]]) as pages:
            result = run_sync(store, {}, now=NOW)
        pages.assert_called_once_with(ACCOUNT, CATEGORY, OPENED, NOW)
        self.assertEqual(len(store.pages), 2)
        self.assertEqual(result["status"], "completed")
        self.assertTrue(store.unlocked)

    def test_later_page_failure_retains_progress_without_checkpoint(self):
        def pages(*args):
            yield [{}]
            raise StarlingError("Starling returned HTTP 429.", 429, 10)
        store = MemoryStore()
        with patch("services.transactions.sync.history_pages", side_effect=pages):
            with self.assertRaises(SyncError) as error:
                run_sync(store, {}, now=NOW)
        self.assertEqual(error.exception.status_code, 429)
        self.assertEqual(error.exception.retry_after, 10)
        self.assertIsNotNone(error.exception.run_uid)
        self.assertEqual(len(store.pages), 1)
        self.assertEqual(store.finished_targets, [])
        self.assertEqual(store.status, "failed")
        self.assertTrue(store.unlocked)

    def test_incremental_uses_five_minute_overlap(self):
        store = MemoryStore()
        previous = NOW - timedelta(days=1)
        store.state = {"history_from": OPENED, "changes_through": previous}
        with patch("services.transactions.sync.changed_items", return_value=[{}]) as changes:
            result = run_sync(store, {}, now=NOW)
        changes.assert_called_once_with(ACCOUNT, CATEGORY, previous - timedelta(minutes=5))
        self.assertEqual(store.target[3], "incremental")
        self.assertEqual(result["status"], "completed")

    def test_stale_checkpoint_and_large_change_response_use_history(self):
        for stale in (False, True):
            with self.subTest(stale=stale):
                store = MemoryStore()
                store.state = {"history_from": OPENED, "changes_through": NOW - timedelta(days=400 if stale else 1)}
                with patch("services.transactions.sync.changed_items", return_value=[{}] * 1000) as changes:
                    with patch("services.transactions.sync.history_pages", return_value=[[]]) as pages:
                        run_sync(store, {}, now=NOW)
                pages.assert_called_once_with(ACCOUNT, CATEGORY, OPENED, NOW)
                self.assertEqual(changes.call_count, 0 if stale else 1)

    def test_lock_conflict_and_invalid_options_do_not_start_sync(self):
        store = MemoryStore()
        store.available = False
        with self.assertRaises(SyncError) as error:
            run_sync(store, {}, now=NOW)
        self.assertEqual(error.exception.status_code, 409)
        self.assertIsNone(store.status)
        for options in ([], {"start": "bad"}, {"categoryUid": CATEGORY},
                        {"start": "2030-01-01"}, {"mode": "incremental", "start": "2020-01-01"}):
            with self.subTest(options=options):
                with self.assertRaises(BadRequest): parse_options(options, NOW)

    def test_sync_routes_require_auth_and_return_safe_reports(self):
        client = app.test_client()
        with patch.dict(app.config, {"APP_API_KEY": "test-key"}):
            with patch("routes.sync.SyncStore") as repository:
                self.assertEqual(client.post("/sync/transactions").status_code, 401)
                repository.assert_not_called()
            with patch("routes.sync.SyncStore", return_value=MemoryStore()):
                with patch("services.transactions.sync.history_pages", return_value=[[]]):
                    response = client.post("/sync/transactions", headers={"Authorization": "Bearer test-key"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["status"], "completed")
        self.assertEqual(response.headers["Cache-Control"], "no-store")

    def test_invalid_sync_options_never_connect_to_database(self):
        client = app.test_client()
        with patch.dict(app.config, {"APP_API_KEY": "test-key"}):
            with patch("routes.sync.SyncStore") as repository:
                response = client.post("/sync/transactions", json={"start": "invalid"},
                                       headers={"Authorization": "Bearer test-key"})
                self.assertEqual(response.status_code, 400)
                repository.assert_not_called()


if __name__ == "__main__":
    unittest.main()
