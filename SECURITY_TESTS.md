# Security checks — 5 October 2026

The full suite passed: **141 tests**, with PostgreSQL checks enabled. This
includes **23 new adversarial tests** in `tests/test_security.py`, plus the
existing login, API protection, request-boundary and bank-client tests.

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
- **Bandit:** scanned `app.py`, `models.py`, `routes/` and `services/`; no findings.
- **pip check:** no incompatible installed dependencies.
- **git diff --check:** no whitespace errors.

Audit tools were installed into a separate temporary directory, without adding
production dependencies. To repeat those checks:

```bash
.venv/bin/pip install --target /tmp/toms-security-audit-packages pip-audit bandit
.venv/bin/pip freeze > /tmp/toms-security-installed.txt
PYTHONPATH=/tmp/toms-security-audit-packages .venv/bin/python -m pip_audit --no-deps --disable-pip -r /tmp/toms-security-installed.txt
PYTHONPATH=/tmp/toms-security-audit-packages .venv/bin/python -m bandit -r app.py models.py routes services
.venv/bin/pip check
```

The frozen inventory includes the installed transitive dependencies; `--no-deps`
disables resolving them again. The audit reports known advisories available at
run time, rather than proving those packages have no vulnerabilities.

## What these checks do not establish

This is application-level verification, not a live deployment penetration test.
No browser automation, live TLS/certificate validation, tailnet policy review,
host/firewall review or backup restoration test was performed. Tailscale Serve
and production cookie behaviour still need checking on the actual hosting setup.

The application remains single-owner. The tests do not establish isolation
between multiple customers, and Basic read credentials/API bearer keys must
still be long, random secrets. Successful automated checks do not guarantee
that the application is free of security flaws.
