import hashlib
import multiprocessing
import sqlite3
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from services.banking.rate_limit import RateLimitError, acquire_slot


def acquire_in_worker(database):
    try:
        acquire_slot("fake-bank-token", database=database, clock=lambda: 100001.0, max_wait=0)
        return "admitted"
    except RateLimitError:
        return "limited"


class RateLimitTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.database = Path(self.directory.name) / "limiter.sqlite3"
        self.now = 100000.0

    def clock(self):
        return self.now

    def sleep(self, delay):
        self.now += delay

    def acquire(self, **kwargs):
        return acquire_slot("fake-bank-token", database=self.database,
                            clock=self.clock, sleep=self.sleep, **kwargs)

    def test_spacing_persists_between_independent_calls(self):
        self.acquire()
        self.acquire()
        self.assertEqual(self.now, 100000.25)
        with sqlite3.connect(self.database) as connection:
            rows = connection.execute("SELECT token_hash, started FROM attempts ORDER BY started").fetchall()
        self.assertEqual(len(rows), 2)
        self.assertNotEqual(rows[0][0], "fake-bank-token")
        self.assertGreaterEqual(rows[1][1] - rows[0][1], 0.25)

    def test_concurrent_callers_share_the_same_admission_state(self):
        self.acquire()
        self.now += 1
        barrier = threading.Barrier(2)
        def worker():
            barrier.wait()
            try:
                self.acquire(max_wait=0)
                return "admitted"
            except RateLimitError:
                return "limited"
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _: worker(), range(2)))
        self.assertCountEqual(results, ["admitted", "limited"])

    def test_rolling_daily_limit_and_expiry(self):
        self.acquire()
        key = hashlib.sha256(b"fake-bank-token").hexdigest()
        with sqlite3.connect(self.database) as connection:
            connection.executemany("INSERT INTO attempts VALUES (?, ?)", [(key, self.now)] * 999)
        with self.assertRaises(RateLimitError) as error:
            self.acquire()
        self.assertEqual(error.exception.retry_after, 86400)
        self.now += 86400.01
        self.acquire()
        with sqlite3.connect(self.database) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM attempts").fetchone()[0], 1)

    def test_worker_processes_share_the_same_limit(self):
        self.acquire()
        with multiprocessing.get_context("spawn").Pool(2) as pool:
            results = pool.map(acquire_in_worker, [str(self.database)] * 2)
        self.assertCountEqual(results, ["admitted", "limited"])


if __name__ == "__main__":
    unittest.main()
