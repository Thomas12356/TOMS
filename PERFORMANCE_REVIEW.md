# Performance review — 11 October 2026

Branch: `codex/performance-review`. Application behaviour is unchanged.

## Delivered follow-up — 12 October 2026

Implemented on `codex/bounded-financial-reads`. Home now shares a single streamed
payment pass between planning and readiness, batches invoice evidence, and retains
20 priority invoice actions while counting all actions and currency totals.
Reconciliation streams evidence, keeps full totals/counts with 50 detail rows,
and offers five recent invoice choices per source/currency with a link to all
invoices. It retains compact allocation/work maps, so memory is not constant for
every possible workload. Tax downloads stream allowlisted evidence through
private anonymous temporary files rather than keeping the complete ZIP and all
records in memory. Snapshot isolation, cross-year allocations, stale versions,
unknown amounts, currency separation and CSV formula protection are preserved.

Final rerun uses the same synthetic 10k/50k workloads and three-request warm
medians as the baseline below. These are Python allocation peaks, not process
RSS; timings exclude browser rendering/network transfer.

| 50k request | Baseline → delivered time | Baseline → delivered peak | Delivered SQL count |
| --- | ---: | ---: | ---: |
| Home | 2009.5 → 1492.2 ms | 84.8 → 6.5 MiB | 60 |
| Reconciliation | 2905.7 → 1409.8 ms | 103.3 → 6.9 MiB | 42 |
| Tax ZIP | 5354.8 → 3392.2 ms | 239.6 → 6.2 MiB | 17 |

All 574 backend tests completed: 570 passed, four optional backup restore checks
skipped. All 22 browser scripts passed, including matching, invoices, Home,
exports, tax profile, failure recovery and five-width usability checks. Ruff F,
Bandit and whitespace checks passed. New regression tests cover complete totals
beyond visible limits, late-batch stale invoice priority, candidate counts,
streamed/archive parity, private temporary files and failure/response cleanup.
No live bank traffic, credential setup, migration or backup restore was performed.
The owner five-task walkthrough remains outstanding. A ledger ordering index is
still a separate measured follow-up; this change adds no index.

## What was measured

The actual owner GET routes were exercised through Flask's test client using
disposable PostgreSQL schemas. No working financial records were read or changed;
the working schema's index definitions were read from the catalog to reproduce
migration indexes. All bank traffic and background rule/import requests were
blocked by the existing test fixture. No new credentials were created.

Two datasets contained 10,000 / 50,000 transactions, 3,334 / 16,667 current-year
income receipts, 1,000 / 5,000 work records, and 250 / 1,250 issued invoices with
one partial payment allocation and one sending record each. Half the invoices
were issued outside the current year. Their linked work and payment budgets still
participated in reconciliation. Receipts were GBP, one in five transactions was
unconfirmed, and each transaction had 2 KiB of synthetic raw bank JSON. Invoice
work versions deliberately differed to exercise changed-work review.

This is a single-account stress workload with one income source, no mileage,
expense claims or manual classifications, and no completed annual forecast.
It measures evidence loading, readiness and report generation rather than the
cost of many-source tax arithmetic. Every measured request returned HTTP 200.

After one warm-up request, each timing is the median of three requests. Timings
include database calls, ORM materialisation, Jinja rendering and ZIP generation;
they exclude a real browser's rendering and network download. Python peak
allocations were measured in a separate request with tracemalloc, so tracing does
not distort timing. These are allocation peaks, not total process RSS.

## Results

| Request | 10k median | 50k median | SQL statements, 10k / 50k | Python peak at 50k |
| --- | ---: | ---: | ---: | ---: |
| Ledger, first page | 26 ms | 49 ms | 7 / 7 | 0.9 MiB |
| Ledger, last page (200 / 1,000) | 28 ms | 79 ms | 7 / 7 | 0.9 MiB |
| Ledger, description search | 49 ms | 161 ms | 7 / 7 | 1.0 MiB |
| Ledger, needs-attention filter | 45 ms | 126 ms | 7 / 7 | 1.0 MiB |
| Home | 401 ms | 2,010 ms | 25 / 37 | 84.8 MiB |
| Work and payment matching | 605 ms | 2,906 ms | 26 / 92 | 103.3 MiB |
| Tax evidence ZIP | 1,066 ms | 5,355 ms | 39 / 126 | 239.6 MiB |

The ledger keeps only 50 displayed records in memory; its seven SQL statements
stay constant as history grows. Home, matching and exports build complete ORM
evidence collections. Their cost grows substantially even though Home and
matching cap the displayed action lists. The higher statement counts come mainly
from batched relationship loads; no separate query per invoice was observed.

At 50k records, PostgreSQL's slowest observed queries included full transaction
scans and sorting. The last ledger page used an external merge sort, as did the
shared planning-payment query on Home and export. Read-only EXPLAIN ANALYZE
confirmed temporary disk writes in those plans. Description search scanned the
transaction table. The existing account/time index and unconfirmed-only ordered
index do not cover the unfiltered all-account ledger order.

These results are local warm-cache observations, not service guarantees or a
concurrency/load test. Fixtures include ORM-defined indexes in addition to the
deployed migration indexes. Different data distributions, cold caches, database
latency and concurrent exports can change the results.

## Priorities supported by the measurements

1. **Home first.** Separate complete counts/totals from the small set of displayed
   action records. Use shared SQL aggregates and bounded detail queries where the
   existing readiness rules allow it. Avoid loading every invoice, item and
   payment just to build a few actions. Preserve one snapshot, all-year allocation
   budgets, stale evidence checks and exact currency-separated totals.
2. **Export memory next.** Produce the JSON/CSV/ZIP evidence incrementally into a
   private temporary file instead of retaining full ORM collections, record
   dictionaries and large serialised strings at once. Preserve the snapshot,
   complete cross-year evidence, field allowlists and formula protection. Retest
   cleanup, failed exports and concurrent requests before adopting this design.
3. **Matching detail loading.** Keep complete comparisons, but fetch bounded
   display records separately. The current template can repeat up to 50 invoice
   candidates for each of 50 receipts; this workload produced about 375 KiB of
   HTML. A focused invoice picker could reduce that page weight while retaining
   explicit owner allocation.
4. **Ledger indexes after those changes.** Benchmark a global index matching
   `(transaction_time DESC, account_uid, category_uid, feed_item_uid)` in disposable
   schemas. Evaluate date-window queries and deep pagination separately. Search
   indexing is lower priority at the measured 161 ms; avoid adding an extension
   or changing search semantics without a demonstrated need.

None of these optimisations has been applied. Do not truncate evidence to make
totals faster, treat matching totals as proof of payment, or cache private results
across owners/datasets. Broad usability changes still await the owner walkthrough.

## Reproduce and validation

```sh
RUN_POSTGRES_TESTS=1 .venv/bin/python tests/performance_review.py
# Smaller smoke run:
RUN_POSTGRES_TESTS=1 .venv/bin/python tests/performance_review.py --transactions 100 --repeats 1
```

The opt-in harness uses the existing maintenance access for disposable schemas;
it requires no CREATEDB login and does not run backup restores. Schema cleanup is
registered before seeding, and the final harness checks removal after each run.
It prints only synthetic dataset counts, route metrics and query-plan summaries;
it never prints SQL parameters, records or credentials. Detailed measurement
output for this review is kept locally in `/tmp/toms-performance-review-final.jsonl`.

Both large datasets passed, and the final 100-record harness smoke run verified
schema removal. The full backend suite passed again: 568 tests, 564 passed and
four optional backup integration tests skipped. Ruff F, application/harness
Bandit and whitespace checks passed. No browser changes were made.
