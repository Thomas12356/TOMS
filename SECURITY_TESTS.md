# Security checks — 6 October 2026

The full suite passed: **276 tests**, with PostgreSQL checks enabled. This
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

## Income editing page — 6 October 2026

The full PostgreSQL-enabled suite passes 202 tests. Seven new tests cover exact
money conversion, invalid precision and oversized values, duplicate fields,
authentication and CSRF, income eligibility, reconciliation failures, escaped
text, currency changes, saved values, return navigation and confirmation reset.
They use rollback-only synthetic transactions and block bank requests.

`tests/income_browser_checks.py` verifies form controls, mobile overflow and
return links in Chromium at widths 320, 390, 768 and 1280 pixels. These checks use
synthetic data and intercepted requests; they do not verify deployment TLS.

## Owner-created income streams

Streams start empty and support Self employed, Employed and CIS. Regression tests
cover creation, renaming, archiving/restoring, filtering, escaped names, missing
CSRF, bearer rejection, invalid types/IDs, empty state and constrained return
navigation. Archived streams cannot be newly assigned, but existing assignments
remain editable. Stream choice never infers tax deducted. Migration 009 preserves
existing income records without seeding streams.

Chromium checks verify the create form, stream selector, management controls and
mobile navigation at 320, 390 and 1280 pixels, plus income form layout at 768 pixels.
Tests use synthetic data, rollback-only records and no bank requests.

## Expected gross income forecasts

The PostgreSQL-enabled suite passes 211 tests. Forecast regressions cover all
three periods, exact amounts and annual estimates, currencies, zero income,
missing/negative/oversized/invalid amounts, invalid periods, retained form inputs,
updates that preserve actual payments, and archiving that retains the forecast.
Migration 010 leaves existing forecasts unknown and enforces complete valid
forecast fields in PostgreSQL. Chromium checks cover create-form submission,
period selection, edit prefilling and yearly display on mobile and desktop.

## Unpaid holiday forecasts

The full PostgreSQL-enabled suite passes 214 tests. Three new tests cover weekly,
monthly and yearly proration, partial weeks, exact rounding, zero and 52 weeks,
creation/update persistence, unchanged actual income, invalid values and retained
form inputs. Migration 011 preserves existing estimates with zero unpaid weeks.
Chromium verifies entering and submitting partial holiday weeks alongside the
forecast on mobile and desktop.

## Holiday units

The full suite passes 216 tests. Added checks cover day/week equivalence, saved
unit preference, partial-day precision, maximum days, invalid units and rejected
out-of-range input. Chromium verifies choosing Days and submitting that unit on
mobile and desktop. Migration 012 preserves existing week values.

## Current UK official rule checks

The suite passes 223 tests with PostgreSQL enabled. New tests cover official
content extraction, pinned tax-year dates, changed bands/allowances/year,
withdrawn or malformed publications, cache reuse and corruption, network failure,
unchanged reviewed files, fixed unauthenticated URLs, blocked redirects and
response-size limits. Browser route tests cover login, bearer rejection and CSRF.
Chromium checks verify the tax page and navigation at mobile and desktop widths.

A live public GOV.UK check matched the reviewed England/Wales/Northern Ireland
2026–27 figures. The daily 07:55 Europe/London timer was enabled and its service
ran successfully. No banking information was sent. Only bands and standard
allowance rules are checked automatically; no completed tax calculation is claimed.

## Security review and regression tests — 6 October 2026

The complete PostgreSQL-enabled suite passes **236 tests** (13 new regressions).
The dashboard, review popup, income/stream forms and Tax rules Chromium scripts
all pass at their configured mobile and desktop widths. Tests use synthetic,
rollback-only transactions or disposable schemas, and block bank requests.

Fixed during review:

- Income forms now carry a version of the displayed payment and saved income.
  Changed bank currency or another income edit causes HTTP 409 before saving.
  Conflicts show current saved values; stale entered money is not carried into a
  new currency. Two concurrent saves are checked against real PostgreSQL: one
  succeeds and one is rejected as stale.
- Tax verification caches are tied to the exact reviewed rules hash, validate
  metadata and timestamps, and are read with a size bound. Changed local rule
  values/dates/formulas cannot inherit a previous successful source check.
- GOV.UK fetching has size limits, elapsed-time checks between chunks, separate
  network timeouts and rejects compressed responses before decompression.
  Redirects remain blocked and the client sends no bank credentials.
- Publication validation rejects malformed shapes and ignores script, style
  and template content. Failed atomic writes keep the prior cache and clean up
  temporary files. Duplicate background checks are not queued within a worker.
- Timer installers use `/usr/bin/systemctl`, avoiding executable lookup through
  an untrusted PATH.

Validation: Ruff's F checks pass. Bandit reports **zero application findings**.
The timer installers have eight reviewed low-severity B404/B603 subprocess
warnings: commands use fixed argument lists, an absolute executable, no shell
and no remote inputs. The installed dependency audit checks **24 packages** and
finds **no known advisories**. A fresh live public GOV.UK check still succeeds.
These scans are snapshots, not proof that no vulnerabilities exist.

The previously recorded PostgreSQL superuser configuration has been replaced
with a restricted `toms_app` login and separate non-superuser `toms_migrator`.
Runtime credentials cannot access migration records or modify table definitions.
Migration credentials are read only by the maintenance command; they are not
loaded into the Flask environment. See the database permission tests and README
for provisioning and the shared-OS-user limitation.
Current configuration has Secure/HttpOnly/SameSite=Lax cookies, a sufficiently
long signing key and debug disabled. Tailnet HTTPS configuration and the live
owner login were not externally penetration-tested.

## Part-year forecast checks — 6 October 2026

The PostgreSQL-enabled suite passes 240 tests. Four new tests cover inclusive
boundaries, full-year preservation, outside-year overlap and zero income,
weekly/monthly/yearly estimates, absence deducted within an active window,
date persistence/prefilling, unchanged saved payments, unsupported years,
invalid dates, reversed dates, excessive absence and retained invalid inputs.
PostgreSQL constraints independently reject invalid years and reversed dates.

Chromium income/streams and tax-page checks pass on mobile and desktop, including
submission of selected tax year and date fields. Ruff checks pass and Bandit
reports no application findings. No dependencies or authentication settings changed.

## Restricted database accounts — 6 October 2026

Provisioned and verified the live local database with `toms_app` for runtime
operations and `toms_migrator` for maintenance. Both roles are non-superusers.
The full PostgreSQL-enabled suite passes **243 tests** under the runtime role.
Permission probes verify SQLSTATE 42501 for reading migration history, creating
schemas, altering tables, truncating records and assuming either privileged
role. Probes are rolled back. A maintenance transaction verifies future table
DML grants without leaving a table behind. Missing maintenance credentials fail
closed. `flask db-upgrade` passes with the separate login; Ruff F checks,
Bandit and `git diff --check` pass. Both ignored credential files have mode 0600.
Restart any already-running server to replace its previously opened connections.

## Annual income-tax planning — 6 October 2026

The PostgreSQL-enabled suite passes **264 tests** with the restricted runtime
login. New coverage includes official-rate examples at allowance/basic/higher/
additional boundaries, penny rounding and the allowance taper; employment plus
self-employment; multiple employers sharing one allowance; entered PAYE/CIS
credits; excess deductions; part-year and archived forecasts; unknown deductions;
missing gross and foreign-currency blockers; zero receipts and no division by
zero. Increased withholding cannot increase the reserve target.

Authenticated route checks cover owner-only access (anonymous and bearer calls
cannot retrieve owner forecasts), no-store headers, escaped names, invalid and
duplicate query parameters, empty/incomplete rendering and no bank calls.
PostgreSQL rejects incompatible modes and missing/negative entered deductions.
Form checks cover save/prefill, unchanged recorded-payment deductions, forged
and duplicate fields, overflow/negative/excess amounts and retained invalid input.
The official-source validator now checks calculation bands against the verified
published ranges, rather than verifying only the displayed table.

Chromium checks pass for five tax-estimate states at 320, 390, 640, 768 and 1280px:
complete, CIS excess, unknown deductions, foreign currency and no streams.
Figures, mobile navigation, layout and JavaScript are checked. Income forms,
annual-deduction visibility/validation and submitted values pass at mobile and
desktop widths; existing ledger/sync/review browser checks also pass. The narrow
selector overflow found during testing is fixed. Ruff F checks, Bandit application
scan and diff whitespace checks pass. A live public GOV.UK verification succeeds.
Migration 014 applies through the maintenance login and a repeat upgrade is a
no-op. No live bank calls, dependencies or authentication settings changed.

This is an income-tax planning forecast, not payroll software or a complete
Self Assessment calculation. PAYE estimates assume a standard employment-only
allowance; entered forecasts account for tax-code differences. NI, reliefs,
expenses, trading allowance, payments on account and money already saved/paid
directly remain outside scope and are labelled on the page.

## Browser test-data mode — 6 October 2026

The full PostgreSQL-enabled suite passes **276 tests** under the restricted app
login. Twelve new tests run actual DataSession routing against two disposable
schemas containing identical live/demo account, stream and transaction UUIDs.
They verify sample seeding, switching back, classifications, confirmations,
income details, stream creation and tax estimates without changing the live
fixture; fixed balances and simulated sync without calling the bank/importer;
real authentication/logout; per-browser selection and unaffected API/background
bindings; and stale CSRF tokens being rejected after either direction of switch.

Additional probes cover missing CSRF, anonymous/bearer attempts, invalid and
duplicate toggle fields, fixed redirect destinations, idempotent and concurrent
first seeding, absence of demo login tables, refused raw/mixed-auth queries, and
bank/background-sync guards. PostgreSQL independently denies runtime ALTER on
demo tables, preserving restricted-role permissions in both schemas.

`tests/demo_browser_checks.py` starts a temporary loopback Flask server and runs
Chromium at 320, 390 and 1280px through real POST/redirect/cookie workflows:
switch on, close review, check demo balances, simulate sync, confirm one payment,
view tax estimates, create a stream and switch back to unchanged live fixtures.
All three widths pass. Existing ledger/sync/review, income/streams/tax-rules and
five-state tax-estimate layout checks pass. No external bank calls or real owner
records are used by these browser tests. Ruff F checks, Bandit application scan
and diff checks pass. Migration 015 is applied locally and repeat upgrade is a
no-op; test schema/table DDL remains confined to the maintenance login.

Test mode is browser-local and intentionally does not stop scheduled real bank
sync, change owner credentials or substitute fake HMRC rules. These boundaries
are labelled on screen. Demo edits persist across toggles, are shared by test-mode
browsers and are rebuilt when new migrations are applied. Sample tables contain
synthetic records only; no real account data is copied into them.

## Logged deductions and UK mileage — 6 October 2026

The forecast no longer credits hypothetical PAYE/CIS amounts. Payment tax credits
are filtered by UK tax-year midnight in Europe/London, settled status, GBP,
current income classification, assigned stream and review flags. Logged expenses
reduce taxable earnings rather than subtracting tax pound for pound. Loss relief
and incomplete/stale records block the business savings target.

Coverage includes 55p/25p car/van thresholds for 2026–27, motorcycles, employee
bicycles, fractional miles, shared trade/associated-employer bands across cars
and vans, annual reimbursement offsets, unsupported location/year rejection,
duplicate submissions and consistent groups. Owner form tests cover partial
expense amounts, bank corrections, stale versions, validation, archiving,
classification safeguards, mileage versus actual running-cost conflicts,
CSRF, bearer/anonymous denial and escaped purposes. Rule tests validate both
publications, changed years/rates, withdrawals and daily caching. Remote fetches
reuse the bounded, unauthenticated, no-redirect GOV.UK client.

Chromium exercises the creation dialog (including server validation and retained
input), expense and mileage create/remove requests against actual Flask routes,
mobile controls and sample/real database isolation at 320, 390 and 1280px.
Income/streams/rules layouts pass at four widths; tax estimate passes five states
at five widths. Ruff F, Bandit and whitespace checks pass. Migration 016 applies
through the maintenance login; repeat upgrade is a no-op. Both live HMRC mileage
publications verify successfully, and the existing daily timer includes them.
No bank calls or new dependencies are needed for this feature.

## Irregular shifts and deduction links — 6 October 2026

The full PostgreSQL-enabled suite passes **314 tests**. New coverage checks exact
hourly/total gross pay, unpaid breaks, penny rounding, overnight shifts, daylight
saving elapsed time, invalid/ambiguous clock readings and tax-year boundaries.
Owner routes reject duplicate submissions, stale edits, archived writes, forged
cross-stream shift IDs and invalid currencies. CSRF and API/browser boundaries
remain enforced. PostgreSQL rejects cross-stream deduction links, and linked
shifts cannot be removed until associations are cleared. Mileage associations
can be edited without changing allowance amounts; stream totals never add bank
receipts or count a linked deduction twice. Switching income mode preserves
regular forecast settings.

Chromium passes complete hourly/total shift creation, stream-mode changes,
combined tax totals and linked expense/mileage workflows at 320, 390 and 1280px.
The browser checks confirm real shift tables remain empty during demo edits,
mobile controls fit, and no JavaScript errors occur. Existing income/stream/rule
layout checks also pass. Ruff F, Bandit and whitespace checks pass; migration 017
is applied through the maintenance role and a repeated upgrade is a no-op.

## Overtime and final security review — 6 October 2026

The PostgreSQL-enabled suite passes **321 tests**. Seven new tests cover overtime
addition after absence adjustments, regular/individual mode semantics, editing
and removal, combined employment/business tax bands, escaped notes, and browser
security headers. The existing shift, expense and mileage version tests now
reject non-ASCII forged versions with a normal validation error. Stale form
creation is rejected after a stream-pattern change. A regression simulates a
writer changing a shift between the form snapshot and the locked read: fresh
locked state prevents accepting the stale edit. Shift and expense locked reads
explicitly refresh SQLAlchemy's identity map.

The live Flask/Chromium demo workflow passes at 320, 390 and 1280px, including
hourly overtime, retained validation errors, switching to total pay, edit/delete,
exact tax-total changes, shifts, deduction links and sample/real isolation.
All five browser scripts pass: ledger, review, income/rules, tax estimate and
live demo workflows. Browser automation does not weaken the production CSP;
its wait predicates use functions rather than string evaluation. The demo
workflow checks for policy violations as well as JavaScript errors.

A Content Security Policy limits scripts, styles and connections to this app,
blocks inline scripts and eval, disallows embedding and base elements, and
restricts form destinations. Permissions Policy disables camera, microphone and
geolocation; MIME-sniffing protection covers static files and error pages too.
Policy design follows the [OWASP CSP guidance](https://cheatsheetseries.owasp.org/cheatsheets/Content_Security_Policy_Cheat_Sheet.html).

Ruff F, Bandit and whitespace checks pass. pip-audit examined **20 installed app
packages** and found **no known vulnerabilities** on this review date. Existing
security tests also exercise login throttling/concurrency, session revocation,
CSRF, forged cookies/headers, API/browser separation, request-size limits,
escaping, safe errors and restricted PostgreSQL privileges. Migration 018 is
applied through the maintenance role; repeat upgrade is a no-op. Secure cookies
are enabled, debug is disabled, and both environment files have mode 0600.
The configured bind is 0.0.0.0 for LAN access; deployment should still use the
intended private network and HTTPS. This review covers application code and
local settings; it does not audit tailnet ACLs, firewall rules or a deployed proxy.

The user workflow is now near the top of README.md. Clear overtime wording and
Edit stream settings help, but a guided checklist would further reduce the
learning curve around forecasts versus receipts and credits versus deductions.

## Mileage groups removed — 6 October 2026

Mileage now uses one income stream per business or employer; grouping across
streams is no longer supported. The form, validator, calculator and model no
longer accept the group field. Migration 019 refuses to remove nonempty groups
on other installations, protecting their existing calculations. Locally there
were no grouped journeys. A rollback-only migration probe verifies that grouped
rows block the upgrade and ordinary journeys are preserved.

The full PostgreSQL suite passes 322 tests, including the mileage suggestion
regression. Chromium demo workflows pass at 320, 390 and 1280px with the group
field absent; Ruff F and diff checks pass. Repeat upgrade is a no-op.

## Stream business / employer relationships — 6 October 2026

The full PostgreSQL suite passes **329 tests**. Seven new owner-route tests
cover historical 8,000 + 4,000-mile thresholds shared across self-employed/CIS
sources, employment reimbursement offsets, creation into an existing pool,
chained selection, moving one member, archived members and separate vehicle
types. Invalid, unknown, self-referencing, cross-scheme and duplicate selections
are rejected; raw pool IDs are not accepted. CSRF and bearer/browser boundaries
remain enforced. Shared advisory locking coordinates settings with mileage
writes; row locks refresh current state before copying a pool identifier.

Chromium passes creation, compatible-choice filtering, prefilled settings,
shared threshold totals and separation/recalculation at 320, 390 and 1280px.
Those workflows also check overtime, shifts, linked deductions and real/demo
isolation. Existing income/rules layout checks pass at four widths. Ruff F,
Bandit and whitespace checks pass. Migration 020 is applied through the
maintenance login and repeat upgrade is a no-op.

Per-journey group fields remain absent. Existing streams get independent pool
identifiers until the owner selects their business/employer relationship. This
is a declared relationship, not an inference from names, vehicles or bank
payments. Related employment must meet HMRC's associated-employment rules;
employment cannot combine with a self-employed/CIS business.
