"""Log diagnostic metadata without exception messages, SQL values or bank data."""

import logging
import re
import traceback
from collections import deque
from pathlib import Path


logger = logging.getLogger("toms.errors")


def log_failure(operation, error, *, run_uid=None):
    """Keep the error class, SQLSTATE and code locations; exclude messages and locals.

    SQLAlchemy exception strings can include query parameters and connection
    details, so neither str(error) nor exc_info is safe here.
    """
    sqlstate = getattr(getattr(error, "orig", None), "sqlstate", None)
    if not isinstance(sqlstate, str) or not re.fullmatch(r"[0-9A-Z]{5}", sqlstate):
        sqlstate = "-"
    frames = deque(maxlen=8)
    for frame, line in traceback.walk_tb(error.__traceback__):
        frames.append(f"{Path(frame.f_code.co_filename).name}:{line}:{frame.f_code.co_name}")
    logger.error("operation=%s exception=%s sqlstate=%s run_uid=%s frames=%s",
                 operation, type(error).__name__, sqlstate, run_uid or "-",
                 " > ".join(frames) or "-")
