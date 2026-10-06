"""Dashboard jobs reuse the importer and its PostgreSQL lock."""

from threading import Lock, Thread

from models import SyncRun
from services.database.connection import db
from services.database.session import test_data_active
from services.error_logging import log_failure
from services.transactions.store import SyncStore
from services.transactions.sync import SyncError, run_sync

_job_lock = Lock()


def latest_sync():
    run = db.session.scalar(db.select(SyncRun).order_by(SyncRun.started_at.desc()).limit(1))
    last_success = db.session.scalar(db.select(db.func.max(SyncRun.finished_at)).where(SyncRun.status == "completed", SyncRun.requested_options == {}))
    return None if run is None else {
        "status": run.status, "started_at": run.started_at.isoformat(),
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
        "error": run.error,
        "last_success_at": last_success.isoformat() if last_success else None,
    }


def start_dashboard_sync(app, *, force=False):
    """Keep HTTP requests short, with at most one job per worker."""
    if test_data_active():
        raise RuntimeError('A bank sync cannot start from browser test mode.')
    if not _job_lock.acquire(blocking=False):
        return False

    def work():
        try:
            with app.app_context():
                try:
                    run_sync(SyncStore(), {}, minimum_age_seconds=0 if force else 60)
                except SyncError as error:
                    # The importer records failures; another worker owning the lock is normal.
                    if error.status_code != 409:
                        log_failure("sync.dashboard", error)
                except Exception as error:
                    db.session.rollback()
                    log_failure("sync.dashboard", error)
        finally:
            _job_lock.release()

    try:
        Thread(target=work, name="transaction-sync", daemon=True).start()
    except BaseException:
        _job_lock.release()
        raise
    return True
