"""Store imported transactions with Flask-SQLAlchemy models and sessions."""

from contextlib import contextmanager
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import text

from models import Account, Category, Transaction, SyncRun, SyncTarget
from services.database.connection import db


LOCK_ID = 6075157141257144322
# Changes affecting reconciliation, the tax period or the identified income source.
INCOME_REVIEW_FIELDS = ("amount_minor", "currency", "direction", "transaction_time",
                        "source", "counterparty_name", "reference")


def serialize(value):
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, dict):
        return {key: serialize(item) for key, item in value.items()}
    if isinstance(value, list):
        return [serialize(item) for item in value]
    return value


class SyncStore:
    def __init__(self, session=None, engine=None):
        self.session = session if session is not None else db.session
        self.engine = engine if engine is not None else db.engine
        self._lock_connection = None

    @contextmanager
    def commit(self):
        """Commit one unit of work, or reset the session so failure can be recorded."""
        try:
            yield
            self.session.commit()
        except BaseException:
            self.session.rollback()
            raise

    def ready(self):
        return self.session.scalar(db.select(db.func.to_regclass("toms.sync_runs"))) is not None

    def lock(self):
        # The lock needs to survive page commits. Hold it in a separate transaction;
        # closing this connection rolls it back and releases the lock before pooling.
        connection = self.engine.connect()
        try:
            acquired = connection.scalar(text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": LOCK_ID})
        except BaseException:
            connection.close()
            raise
        if acquired:
            self._lock_connection = connection
        else:
            connection.close()
        return acquired

    def unlock(self):
        if self._lock_connection is not None:
            connection, self._lock_connection = self._lock_connection, None
            connection.close()

    def recently_synced(self, snapshot, seconds):
        """Called under the import lock; only complete, unfiltered runs count."""
        last = self.session.scalar(db.select(SyncRun.finished_at).where(
            SyncRun.status == "completed", SyncRun.requested_options == {},
        ).order_by(SyncRun.finished_at.desc()).limit(1))
        return last is not None and (snapshot - last).total_seconds() < seconds

    def start_run(self, run_uid, options, snapshot):
        with self.commit():
            # Owning the lock proves previous running imports were interrupted.
            self.session.execute(db.update(SyncRun).where(SyncRun.status == "running").values(
                status="failed", finished_at=datetime.now(timezone.utc),
                error="Previous sync was interrupted; committed pages were retained."))
            self.session.execute(db.update(SyncTarget).where(SyncTarget.status == "running").values(status="failed"))
            self.session.add(SyncRun(run_uid=run_uid, status="running",
                                     requested_options=options, snapshot_at=snapshot))

    def save_account(self, account):
        with self.commit():
            self.session.merge(Account(
                account_uid=account["uid"], default_category_uid=account["category"],
                currency=account["currency"], name=account["raw"].get("name"),
                account_type=account["raw"].get("accountType"), opened_at=account["opened"],
                raw_payload=account["raw"], fetched_at=datetime.now(timezone.utc)))

    def save_category(self, account_uid, category_uid, name, kind):
        with self.commit():
            self.session.merge(Category(account_uid=account_uid, category_uid=category_uid,
                                        name=name, kind=kind))

    def category(self, account_uid, category_uid):
        return self.session.get(Category, (account_uid, category_uid)).as_dict()

    def start_target(self, run_uid, account_uid, category_uid, mode, start, end):
        with self.commit():
            self.session.add(SyncTarget(run_uid=run_uid, account_uid=account_uid,
                category_uid=category_uid, mode=mode, requested_start=start,
                requested_end=end, status="running"))

    def save_page(self, run_uid, account_uid, category_uid, items):
        with self.commit():
            changed = 0
            for item in items:
                key = (item["account_uid"], item["category_uid"], item["feed_item_uid"])
                # Income edits lock this same row; keep corrections and review flags atomic.
                transaction = self.session.get(Transaction, key, with_for_update=True)
                if transaction is None:
                    self.session.add(Transaction(**item))
                else:
                    if item["source_updated_at"] < transaction.source_updated_at:
                        continue
                    if (item["source_updated_at"] == transaction.source_updated_at
                            and item["raw_payload"] == transaction.raw_payload):
                        continue
                    if any(item[field] != getattr(transaction, field) for field in INCOME_REVIEW_FIELDS):
                        if transaction.income is not None:
                            transaction.income.needs_review = True
                    for field, value in item.items():
                        setattr(transaction, field, value)
                    transaction.fetched_at = datetime.now(timezone.utc)
                changed += 1

            run = self.session.get(SyncRun, run_uid)
            target = self.session.get(SyncTarget, (run_uid, account_uid, category_uid))
            for record in (run, target):
                record.pages_committed += 1
                record.items_received += len(items)
                record.rows_changed += changed
            if items:
                earliest = min(item["transaction_time"] for item in items)
                latest = max(item["transaction_time"] for item in items)
                target.earliest_received = min(target.earliest_received or earliest, earliest)
                target.latest_received = max(target.latest_received or latest, latest)

    def finish_target(self, run_uid, account_uid, category_uid, mode, start, end, snapshot):
        with self.commit():
            target = self.session.get(SyncTarget, (run_uid, account_uid, category_uid))
            target.status = "completed"
            category = self.session.get(Category, (account_uid, category_uid))
            if mode == "incremental":
                category.changes_through = snapshot
                category.history_through = max(category.history_through or snapshot, snapshot)
            elif (category.history_from is None or
                  (category.history_through is not None and
                   start <= category.history_through and end >= category.history_from)):
                # Merge overlapping history only; do not claim coverage across a gap.
                category.history_from = min(category.history_from or start, start)
                category.history_through = max(category.history_through or end, end)
                if end == snapshot:
                    category.changes_through = max(category.changes_through or snapshot, snapshot)

    def finish_run(self, run_uid, error=None):
        with self.commit():
            run = self.session.get(SyncRun, run_uid)
            run.status = "failed" if error else "completed"
            run.finished_at = datetime.now(timezone.utc)
            run.error = error
            if error:
                self.session.execute(db.update(SyncTarget).where(
                    SyncTarget.run_uid == run_uid, SyncTarget.status == "running").values(status="failed"))

    def report(self, run_uid):
        run = self.session.get(SyncRun, str(run_uid))
        if run is None:
            return None
        return serialize({**run.as_dict(), "targets": [target.as_dict() for target in run.targets]})

    def recent(self):
        runs = self.session.scalars(db.select(SyncRun).order_by(SyncRun.started_at.desc()).limit(20))
        return serialize([run.as_dict() for run in runs])
