"""Coordinate Starling request limits across threads and local worker processes."""

import hashlib
import math
import sqlite3
import time
from contextlib import closing
from pathlib import Path


# Keep all workers using the same instance/ database at the repository root.
DATABASE = Path(__file__).resolve().parents[2] / "instance" / "starling-rate-limit.sqlite3"


class RateLimitError(Exception):
    def __init__(self, retry_after):
        super().__init__("Starling request limit reached. Please try again later.")
        self.retry_after = max(1, math.ceil(retry_after))


def acquire_slot(token, *, database=None, clock=time.time, sleep=time.sleep,
                 max_wait=2.0):
    """Space starts by 250ms and allow at most 1000 attempts per rolling 24h.

    SQLite transactions serialize admission across workers sharing this file.
    Only a SHA-256 hash of the token is stored, never the token itself.
    """
    path = Path(database) if database is not None else DATABASE
    path.parent.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha256(token.encode("utf-8")).hexdigest()
    deadline = clock() + max_wait
    while True:
        now = clock()
        with closing(sqlite3.connect(path, timeout=2)) as connection, connection:
            connection.execute("CREATE TABLE IF NOT EXISTS attempts (token_hash TEXT, started REAL)")
            connection.execute("CREATE INDEX IF NOT EXISTS attempts_token_time ON attempts (token_hash, started)")
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("DELETE FROM attempts WHERE started <= ?", (now - 86400,))
            count, oldest, latest = connection.execute(
                "SELECT COUNT(*), MIN(started), MAX(started) FROM attempts WHERE token_hash = ?", (key,),
            ).fetchone()
            if count >= 1000:
                raise RateLimitError(oldest + 86400 - now)
            delay = max(0, latest + 0.25 - now) if latest is not None else 0
            if delay == 0:
                connection.execute("INSERT INTO attempts VALUES (?, ?)", (key, now))
                return
        if now + delay > deadline:
            raise RateLimitError(delay)
        sleep(delay)
