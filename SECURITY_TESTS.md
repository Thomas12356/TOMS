# Security checks — 5 October 2026

The full suite passed: **195 tests**, with PostgreSQL checks enabled. This
includes **24 adversarial tests** in `tests/test_security.py`, plus the
existing login, API protection, request-boundary and bank-client tests.
Ten classification-form tests also cover saves, automatic reset, income
protection, CSRF rejection, escaped notes and safe failures. Eleven owner-setup tests cover
one-time token rotation/expiry, setup closure and password-change protection.
Concurrent first-run requests are checked to create exactly one owner. Three
launcher tests check automatic startup tokens, reloader behaviour and Gunicorn
initialization without starting servers.

The complete suite and dependency/security scans were rerun after the automatic
startup-token changes on 5 October 2026, with the same passing results.

## What was tested

| Boundary | Evidence |
| --- | --- |
| API authentication | Every registered API route and its explicit methods reject missing, malformed and incorrect credentials before database or bank access. |
| Browser identity | Invalid signatures, unknown session IDs and spoofed Tailscale/proxy headers do not authenticate. Query parameters and cookies cannot supply an API key. |
| Session separation | A logged-in browser cannot access API/bank routes without API credentials. Invalid bearer credentials cannot fall back to a valid browser session. |
| CSRF | Missing, tampered, expired and another browser's tokens are rejected. The pre-login token cannot authorize logout after login. |
| Session lifecycle | Login clears pre-login state; logout revokes replayed cookies; each device has its own session; idle activity does not extend absolute expiry. |
| Password handling | Injection strings, oversized inputs and invalid password changes fail. Unknown usernames still perform scrypt verification. Password reset revokes previous sessions. |
| Database failures | Failure during session lookup or session creation fails closed, does not issue authenticated cookies, and does not expose private exception data. |
| Concurrent authentication | Eight workers issuing 32 requests receive exactly 20 login allowances. Resetting the password while an old-password login is paused cannot leave a usable new session. |
| Browser content | Bank-supplied HTML is escaped in the table and account options. Private responses carry no-store, nosniff, frame protection and referrer-policy headers. |
| Request limits | JSON without CSRF and oversized login forms are rejected before credential verification. |

Tests block outbound bank HTTP calls. Database scenarios use rollback-only
fixtures. Concurrent scenarios create and remove a randomly named temporary
PostgreSQL schema, avoiding the real owner's credentials and sessions.

## Flaws found and fixed

1. **Database errors retried authentication while rendering the error page.**
   Flask-Login's default template context processor eagerly loaded the user
   again, producing an unhandled 500. The dashboard now receives the user proxy
   explicitly, so database failures return a safe 503 without a second lookup.
2. **Password reset could race with an already verified login.** The login now
   locks the owner row during verification and session creation. A concurrent
   password reset waits, then revokes the newly created session too. This is
   exercised with separate PostgreSQL connections and threads.
3. **Browser-cached Basic credentials could authorize API writes.** Basic auth
   is now restricted to GET, HEAD and OPTIONS. POST, PUT, PATCH and DELETE require
   an explicit bearer token; the README's write examples were updated.

## Run the tests

```bash
RUN_POSTGRES_TESTS=1 .venv/bin/python -m unittest discover -s tests
RUN_POSTGRES_TESTS=1 .venv/bin/python -m unittest discover -s tests -p test_security.py
```

Apply migrations with `flask db-upgrade` first. Without `RUN_POSTGRES_TESTS=1`,
database and concurrency tests are skipped, so that run provides less coverage.
The concurrency tests require permission to create a temporary PostgreSQL schema.

## Automated audits

- **pip-audit:** checked all 24 installed application packages, including
  transitive dependencies; no known vulnerabilities were reported.
- **Bandit:** scanned `app.py`, `models.py`, `routes/`, `services/` and
  `gunicorn.conf.py`; no findings.
- **Ruff:** unused imports, undefined names and other F-rule checks passed.
- **pip check:** no incompatible installed dependencies.
- **git diff --check:** no whitespace errors.

Audit tools were installed into a separate temporary directory, without adding
production dependencies. To repeat those checks:

```bash
.venv/bin/pip install --target /tmp/toms-security-audit-packages pip-audit bandit
.venv/bin/pip freeze > /tmp/toms-security-installed.txt
PYTHONPATH=/tmp/toms-security-audit-packages .venv/bin/python -m pip_audit --no-deps --disable-pip -r /tmp/toms-security-installed.txt
PYTHONPATH=/tmp/toms-security-audit-packages .venv/bin/python -m bandit -r app.py models.py routes services gunicorn.conf.py
.venv/bin/pip check
```

The frozen inventory includes the installed transitive dependencies; `--no-deps`
disables resolving them again. The audit reports known advisories available at
run time, rather than proving those packages have no vulnerabilities.

## What these checks do not establish

This is application-level verification, not a live deployment penetration test.
Chromium checks use synthetic bank data and intercepted HTTP responses.
No live TLS/certificate validation, tailnet policy review, host/firewall review
or backup restoration test was performed. Tailscale Serve
and production cookie behaviour still need checking on the actual hosting setup.

The application remains single-owner. The tests do not establish isolation
between multiple customers, and Basic read credentials/API bearer keys must
still be long, random secrets. Successful automated checks do not guarantee
that the application is free of security flaws.

## Dashboard and scheduled sync checks

Four additional tests cover skipping fresh imports while releasing the shared
lock, importing stale data, rejecting unauthenticated/CSRF-free browser syncs,
and distinguishing automatic freshness checks from forced button clicks.
The complete PostgreSQL suite passed after these additions. The installed user
systemd service/timer passed `systemd-analyze --user verify`; the timer is active
and specifies 08:00/20:00 Europe/London. A live scheduled bank import has not been exercised by these tests.

## Mobile UI and latest changes — 5 October 2026

- The full PostgreSQL suite passed: **195 tests**. New cases exercise signed
  currency totals, unavailable balances, UTC day labels, configurable server
  binding, the exact 60-second freshness boundary and preserving the last
  successful timestamp after a failure.
- Chromium checks in `tests/browser_checks.py` exercise widths of 320, 390, 640,
  768 and 1280 pixels with synthetic data. They verify no page overflow,
  collapsible navigation/Escape, matching mobile button sizes, transaction gaps,
  tooltip visibility and bounds, fixed footer position, displayed balance totals,
  forced syncs and automatic stale refresh. No bank requests are made.
- Browser review found and fixed a freshness bug: a recent filtered import could
  suppress a needed full import. JavaScript now checks `last_success_at`, the
  server's timestamp for a completed unfiltered sync.
- A temporary HTTP health server bound to all interfaces responded on both
  `127.0.0.1` and `192.168.1.222`. This does not verify access from another device.
- Ruff, dependency compatibility and whitespace checks passed. The 24 installed
  application dependencies had no known advisories in the repeated pip-audit.
- Bandit reported no application-server findings. Including the deployment
  installer produces low-severity subprocess/PATH notices: its argument lists
  invoke fixed local `systemctl` commands without shell interpolation or external
  input. Those calls were reviewed rather than hidden with suppressions.

Playwright is optional and was installed outside the application virtualenv:

```bash
.venv/bin/pip install --target /tmp/toms-browser-check playwright
PYTHONPATH=/tmp/toms-browser-check .venv/bin/python -m playwright install chromium
PYTHONPATH=/tmp/toms-browser-check .venv/bin/python tests/browser_checks.py
```

Chromium also needs its native Linux libraries. In this environment those were
extracted into `/tmp/toms-browser-libs/root` without changing system packages;
the successful run additionally used
`LD_LIBRARY_PATH=/tmp/toms-browser-libs/root/usr/lib/x86_64-linux-gnu`.

## Live bank verification — 5 October 2026

The real `sync-transactions` command and a manually triggered
`toms-sync.service` both completed successfully with the configured bank token.
Each completed one account/category target, received zero new items and changed
zero rows. The ledger remained at 19 transactions, with all four manual
classifications unchanged and no duplicate transaction identifiers. There were
no income-detail records in this live dataset, so preservation of those records
continues to be established by the isolated tests rather than this live run.

The dashboard rendered successfully, `/dashboard/sync-status` returned the
successful full-import timestamp, and real balance requests returned complete
all-account totals for the one accessible account. No credential values,
transaction descriptions or balance amounts were printed during verification.

The user timer remains enabled/active for 08:00 and 20:00 Europe/London and the
service exited with status 0. User lingering is enabled so the timer can run
after logout. This verifies the actual scheduled service command, but an
08:00/20:00 clock-triggered execution was not observed during this session.
No newly occurring bank payment was available, so event-to-ledger latency was
not measured. Live browser authentication over tailnet HTTPS also remains a
separate deployment check.

## Transaction confirmation

Five new PostgreSQL tests cover explicit confirmation and idempotent retries,
account-scoped pending lists, missing CSRF/API-key rejection, stale-detail
conflicts, re-review after classification edits and invalid version input.
A sync test also verifies identical bank data preserves confirmation while a
pending-to-settled update clears it. Browser coverage now includes dismissing
the mobile popup without confirming, reopening it, individually confirming each
record and updating the visible ledger labels. The initial feature suite contained 183
passing tests; the deeper review below expands it to 195. Migration 008 adds nullable confirmation
storage and a partial index for unconfirmed transactions; existing records are
initially unconfirmed.

## Deeper confirmation review — 5 October 2026

The full suite passed **195 tests** with PostgreSQL enabled. Fifteen dedicated
review tests now cover anonymous/expired sessions, CSRF, malformed requests,
missing records, stale versions, record-specific fingerprints, income currency,
income API edits/deletion, account/page context, persistence in another client
and rollback after failed confirmation commits. Two additional concurrent tests
use disposable PostgreSQL schemas: simultaneous confirmations remain idempotent,
and a bank correction waits for the confirmation lock then clears confirmation.
No live owner records are changed by these tests.

`tests/review_browser_checks.py` adds Chromium scenarios at 320, 390 and 1280px:
close/Escape, reopening, long text and modal bounds, HTML escaping, stale-detail
conflicts, server failures, duplicate clicks, closing during an in-flight save,
next-list failures after a successful save, empty lists, retry controls, login
redirects and responses without explicit confirmation acknowledgement.
The broader five-width layout checks also remain in `tests/browser_checks.py`.
Both use synthetic bank data and intercepted requests.

Issues fixed during this review:

- Closing during an in-flight confirmation could reopen the next review. The
  user's dismissal is now retained for the current page.
- Income amounts could be labelled with the current bank currency after a
  currency correction. They now use their recorded income currency.
- Failed loading after a saved confirmation could leave the old confirm action
  enabled. The UI now disables it and offers an explicit retry for the next list.
- An expired session's login redirect could be mistaken for success. Browser
  JSON endpoints return 401, and the UI rejects redirects and requires the exact
  acknowledgement `confirmed: true` before changing a ledger label.
- Fingerprints now bind to the transaction identity so two otherwise identical
  payments cannot share a confirmation version.

Ruff, whitespace checks and the application Bandit scan passed with no findings.
A read-only call loaded the real 19 pending transactions and verified that their
confirmation count was unchanged. This is tested behaviour rather than a proof
that every possible browser, network or deployment condition is bug-free.

To run the additional browser checks with the existing temporary installation:

```bash
LD_LIBRARY_PATH=/tmp/toms-browser-libs/root/usr/lib/x86_64-linux-gnu PYTHONPATH=/tmp/toms-browser-check .venv/bin/python tests/review_browser_checks.py
```
