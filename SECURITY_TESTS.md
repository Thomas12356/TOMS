# Security checks — 6 October 2026

## Repeatable verification tooling (2026-10-11)

Use the project's application interpreter and separately pinned development tools:

```bash
.venv/bin/python scripts/setup_checks.py --local-linux-libs
.venv/bin/python scripts/verify.py --postgres --browser all
```

The setup script writes ignored `.venv-tools/`, installs Chromium in Playwright's
normal per-user cache, and unpacks the missing Ubuntu 24.04 x86_64 libraries into
`.venv-tools/native/root`. It does not install system packages or start the app.
Omit `--local-linux-libs` on systems with Chromium's libraries already installed.
The verification runner adds this private library path automatically; it no longer
depends on temporary `/tmp` tooling. Other Linux distributions need their native
Chromium dependencies. Downloading packages requires network access.

The runner includes backend checks, selected/all browser workflows, Ruff F,
application Bandit and whitespace checks, stopping on the first failure. For a
focused browser run, use `--skip-backend --skip-static --browser
reliability_browser_checks`. PostgreSQL workflow fixtures require `--postgres`.
Optional backup integration tests remain separately opt-in as documented below.
Verification/setup subprocesses use argument lists without a shell; browser
script names are confined to the repository's tests directory.

Read-path benchmark, using disposable synthetic schemas and mocked bank traffic:

```bash
RUN_POSTGRES_TESTS=1 .venv/bin/python scripts/benchmark.py
RUN_POSTGRES_TESTS=1 .venv/bin/python scripts/benchmark.py --multi-year
```

Measured on 2026-10-11 (one request per size, not a latency guarantee):

| Transactions | Notices: seconds / queries | Reconciliation: seconds / queries |
| --- | --- | --- |
| 100 | 0.103 / 12 | 0.061 / 11 |
| 1,000 | 0.306 / 17 | 0.149 / 13 |
| 10,000 | 2.918 / 107 | 0.984 / 49 |

With half the generated receipts in the prior year, 10,000 records measured
2.202 seconds / 80 queries for notices and 0.457 seconds / 31 queries for
reconciliation. These fixtures emphasize incomplete incoming receipts and do not
measure every invoice/allocation case. Phase 7 should target under one second and
25 queries at 10,000 receipts on this same fixture, while preserving complete
totals and cross-year allocation budgets; repeat timings before claiming success.
The synthetic fixtures leave the working records unchanged.

Baseline journeys exercised through the browser scripts include first-owner and
password validation screens, employment/overtime, freelance/CIS receipt details,
expense/mileage edits, confirmation, invoice issue/allocation, checklist, reserve
earmarks and evidence export. At 390px the income-workspace screenshot was inspected:
the current stream list makes users scroll through configuration/work/payment
controls before reaching account money, supporting the planned workspace split.
These are synthetic regression journeys, not observed usability sessions with
independent users.

## Reliability implementation verification — 2026-10-11

- Full PostgreSQL backend run: 486 tests, 482 passed and four optional backup
  integration cases skipped. New regressions first reproduced stale stream
  overwrites and polling renewal, then passed after fixes. Coverage includes
  concurrent stream edits, archive/restore conflicts, changed mileage choices,
  actual activity, expiry/revocation, absolute expiry, long-lived CSRF refresh,
  private error responses and authentication-counter isolation.
- All 16 browser scripts passed, including the new reliability and first-owner
  setup scripts. Password workflows also passed three consecutive invocations
  after correcting throttle schema routing. Narrow/desktop layouts, preserved
  unsaved entries, token refresh, keyboard activity, rejected synthetic events,
  expiry/invalid-CSRF guidance and conflict reloads were exercised.
- Ruff F, application Bandit and git diff --check passed. Setup/verification
  subprocesses were reviewed separately: they execute fixed tools via argument
  lists without shell interpolation; no server request can invoke them.
- No live Starling calls, schema migrations, publication or deployment occurred.
  A private backup was created, but restore verification was blocked by missing
  dedicated CREATEDB test credentials. Optional backup tests remain skipped in
  this run; the 2026-10-10 separate integration evidence remains historical.

LoginAttempt maps a table already created by migration 006; no applied migration
was edited. The PostgreSQL upsert keeps the twenty-attempt/fifteen-minute window
atomic while following isolated authentication-schema routing. Previously, raw
SQL bypassed browser-fixture isolation and incremented the working throttle during
repeated login checks. No working counter reset was performed; current fixture
checks prove future runs leave it unchanged.

## Historical verification

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
The complete PostgreSQL suite passed after these additions. A live scheduled bank import has not been exercised by these tests.

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

Chromium also needs its native libraries. Install the dependencies required by Playwright before running browser checks.

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

Runtime credentials cannot access migration records or modify table definitions.
Migration credentials are read only by the maintenance command; they are not
loaded into the Flask environment. See the database permission tests and README
for provisioning and the shared-OS-user limitation.

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

The full PostgreSQL-enabled suite passes **243 tests** under the runtime role.
Permission probes verify SQLSTATE 42501 for reading migration history, creating
schemas, altering tables, truncating records and assuming either privileged
role. Probes are rolled back. A maintenance transaction verifies future table
DML grants without leaving a table behind. Missing maintenance credentials fail
closed. `flask db-upgrade` passes with the separate login; Ruff F checks,
Bandit and `git diff --check` pass.

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
applied through the maintenance role; repeat upgrade is a no-op. This review covers application code and
local settings; it does not audit tailnet ACLs, firewall rules or a deployed proxy.

The user workflow is now near the top of README.md. Clear overtime wording and
Edit stream settings help, but a guided checklist would further reduce the
learning curve around forecasts versus receipts and credits versus deductions.

## Mileage groups removed — 6 October 2026

Mileage now uses one income stream per business or employer; grouping across
streams is no longer supported. The form, validator, calculator and model no
longer accept the group field. Migration 019 refuses to remove nonempty groups
on other installations, protecting their existing calculations. A rollback-only migration probe verifies that grouped
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

## Sample-data checks after stream mileage relationships

Four additional PostgreSQL tests verify that sample payroll net amounts reconcile
with recorded gross, tax and adjustments; seeded overtime is counted once and
survives repeated mode changes; overtime writes and business relationships remain
in the sample schema even when real records use the same IDs; and stale forms
cannot write into real data after switching mode. Sample mileage entered through
the owner forms crosses the shared 10,000-mile band across freelance and CIS
streams: 8,000 miles receives £4,400 and the next 4,000 receives £1,600 under the
reviewed 2026–27 rules. Bank requests remain forbidden throughout these tests.

Fresh sample datasets include an overtime example without changing the £70,000
combined forecast. Existing sample edits are preserved rather than overwritten.

Verification: all **333 backend tests** pass with PostgreSQL enabled. All five
Chromium checks pass, covering live sample workflows, income/rules pages, tax
estimate states, transaction review and dashboard layouts. Ruff and Bandit pass;
the installed-dependency audit reports no known vulnerabilities in 20 packages.
These checks cover the application; they do not verify deployment firewall or
tailnet access policies.

## Tax readiness and National Insurance (2026-10-08)

The PostgreSQL suite now passes **352 tests**, including 19 new cases covering
standard Class 4 thresholds and penny rounding, aggregation across business
streams, payroll NI reconciliation, rejected malformed/excessive NI, actual CIS
credits applied once, received-income readiness, pending/future payment exclusion,
non-taxable receipts, stale expense blocking and records outside the selected
year. Missing income confirmation blocks the received target without treating
ordinary unconfirmed spending as a calculation blocker.

The owner-only estimate hides savings targets when NI publication checks report
changed rules. NI source tests include changes from 6% to 16% and 2% to 12%, so
substring matches cannot falsely approve altered rates. A live GOV.UK check
matched the reviewed 2026–27 NI figures. New sample-mode tests confirm payroll NI
writes and stale forms remain isolated from real records.

Chromium checks pass for six tax-estimate states at five widths, the full live
sample workflow at 320/390/1280px, and income/rules page layouts. The workflow
checks readiness expansion, cumulative-target labels, sample payroll NI and
revised annual targets. Ruff and Bandit pass; the application environment audit
reports no known vulnerabilities in 25 installed distributions. Migration 021
applied successfully and a second upgrade was a no-op.

Calculation scope remains explicit: standard Class 4 estimates; actual Class 1
records rather than future payroll estimates; no automatic Class 2 charge.
Mixed Class 1/4 annual maximum adjustments, age exemptions and special NI cases
are not calculated. Deployment firewall and tailnet policies were not audited.

## Checkpoint review (2026-10-08)

The final checkpoint suite passes **359 backend tests**. Review fixes cover:

- Employee NI is displayed in the transaction confirmation popup and participates
  in its stale-version check. API changes cannot assign payroll NI to an existing
  self-employed/CIS stream by changing only the historical income type.
- Claims whose bank transaction dates move out of the tax year remain visible as
  stale deductions. They block a complete target instead of disappearing silently.
- Mileage deletion requires the version currently displayed, protecting later
  changes to its details or shift link. Completed-journey validation uses the
  Europe/London date, including near midnight during British Summer Time.
- A known changed official publication stays blocked through later network outages
  for income tax, NI and mileage. Successful matching verification clears it.
  Visiting Tax estimate also starts the existing nonblocking daily checks.
- Payroll NI fields follow employment selection, retain typed values on switching
  back, and remain subject to server validation. Sync-status failures show the
  correct real/sample footer label and leave the controls usable.

All six Chromium suites pass: dashboard, income/rules, transaction review, tax
estimate, live sample workflows, and the new real owner login/password/logout
workflow. The latter checks wrong credentials, password mismatch and correction,
revocation of another session, old-password rejection and logout at 320, 390 and
1280px, entirely within disposable database schemas. Sample workflow checks include
employment-only NI fields and sample/real isolation. Security scans and dependency
checks pass; 25 installed application distributions have no known vulnerabilities.
Live official checks match all three reviewed rule sets. The Git integrity check
also passes after preserving and quarantining two empty tool checkpoint pointers.

These tests validate the implemented planning scope and application behavior;
NI exemptions/annual maximum adjustments and deployment access policies remain
outside the implemented calculation and this local review respectively.

## Database backup and restore verification — 2026-10-08

- Full PostgreSQL-backed regression suite: 372 tests, successful (four optional
  backup integration tests skipped in that invocation).
- All 13 backup tests separately passed against an isolated PostgreSQL 18 server,
  including all four integration tests: actual archive/restore, concurrent source
  changes during an exported snapshot, nonempty-target protection and cleanup
  after manifest mismatch. Safety tests cover corrupted archives, missing
  maintenance credentials, source/system database refusal, failed backup cleanup,
  private directory permissions, safe process errors and verified-only retention.
- A real application backup was restored into the isolated server. Every table's
  count and row hash matched; all SQLAlchemy-mapped application tables could be
  read. Temporary databases were removed. The source database was not restored
  over or modified by backup verification. No bank requests were made.
- Ruff F checks and Bandit checks passed for the new maintenance code. PostgreSQL
  tools and systemctl use argument lists without a shell; those intended process
  calls have narrowly documented Bandit suppressions.
- The optional daily timer is provided but not enabled: dedicated restore-test
  credentials require administrator provisioning. Off-device storage, encryption,
  monitoring/alerts and configuration recovery remain deployment responsibilities.
  See DATA_STORAGE.md for the boundaries and recovery instructions.

## System management and owner activity — 2026-10-08

- Owner-only read-only `/dashboard/system` rejects anonymous/API access before
  reading backup metadata or database statistics. Sensitive responses retain
  no-store and existing CSP/security headers. No maintenance credentials or bank
  calls are used by the page.
- Backup inventory tests cover missing directories, malformed metadata, symlinks,
  mismatched verification markers and incomplete archives. The web page reads
  metadata only and explicitly does not claim fresh archive integrity checking.
- Action logging uses fixed action codes and record IDs, not submitted form
  values. Successful edits and their events share a transaction in real and
  sample mode. Regression checks cover rollback, failed validation, duplicate
  confirmations, UTC display and both engine/connection schema mappings. Disposable-schema
  tests leave no activity events in the live installation.
- Migration 022 grants the runtime role SELECT/INSERT only on user_actions;
  real PostgreSQL tests reject UPDATE, DELETE and TRUNCATE.
- Full backend suite: 386 tests successful, four optional backup integration tests
  skipped (those were separately exercised during backup implementation).
- Chromium System-page checks pass at 320, 390, 640, 768 and 1280 pixels for
  populated, empty and error states, escaped record IDs, filters and mobile nav.
  Existing dashboard, real login/password and sample-mode end-to-end browser
  suites also pass. Ruff F and Bandit checks pass.

## Invoice workflow — 2026-10-08

Full PostgreSQL suite: 406 tests passed, with four optional backup integration
checks skipped. Invoice coverage includes owner-only access, API credential
rejection, CSRF, stale revisions, escaped client text, dates/currencies, shift
reservations, issued snapshots, cancellation and allocation ownership.
Concurrent requests cannot reserve one shift or spend one receipt twice.
Budgets independently enforce cash, actual tax and employee NI; changed receipts
invalidate settlement. Sample invoices and allocations remain isolated.

Browser coverage at 320, 390, 768 and 1280 pixels exercises draft creation/editing,
issue/cancel, partial payments, allocation removal and multi-shift CIS settlement
through real Flask forms. Checks include overflow, JavaScript errors and unchanged
forecast totals. Ruff F, Bandit and whitespace checks passed. These checks are
not an external penetration test.

## Income workspace and money reserves — 2026-10-08

The backend suite now contains 426 tests, with four optional backup integration
checks skipped. New coverage includes tracking-only expenses, explicit tax
eligibility, foreign-currency tracking, vehicle/mileage compatibility, stream
popup filtering, real bank-balance failures, underfunding and earmark revisions.
Competing first-time earmark writes save once and reject the stale submission.

Income notices require owner sessions, use CSRF for acknowledgement, exclude
pending/transferred/future payments, flag unassigned or incomplete income, and
reopen after corrections or confirmation. Tests cover actual CIS withholding,
employment affecting business tax bands, separate Class 4 provision, and matching
per-payment reserve changes to the cumulative received-income target across
streams. Read notices still contribute to that calculation. No raw bank payload
or credentials are returned. Rule-check startup is mocked in general UI fixtures
so background network work cannot interfere with rule-check unit tests.

Real PostgreSQL/browser workflows passed at 320, 390, 768 and 1280 pixels:
configure/income/expense popups, keyboard closing and focus restoration, tracking
and deductible expense saves, earmarks, confirmation, durable notice reads and
sample/real isolation. A simulated import while the page remains open verifies
the one-minute polling path, unassigned-payment advice and bank-text escaping.
Opening account money from a notice performs a fresh page read. Existing demo,
income-form and invoice browser regressions also passed. Ruff F, Bandit and
whitespace checks passed. No external penetration test was performed.

## Stable checkpoint verification — 2026-10-10

- Reviewed the invoice and income-workspace changes, including migrations,
  owner access, revision/locking behaviour, sample isolation and browser controls.
- `RUN_POSTGRES_TESTS=1 .venv/bin/python -m unittest discover -s tests -q`:
  426 tests ran successfully; four optional disposable-server backup integration
  tests were skipped (422 passed).
- All nine Chromium scripts passed: income workspace, invoices, demo workflow,
  income forms/rules, transaction review, tax estimate, System, login and dashboard.
  The new workflows cover 320, 390, 768 and 1280px; dashboard, tax estimate and
  System checks include five widths. Bank responses were mocked.
- Ruff F checks, Bandit application scan, staged whitespace checks and a scan
  for configured secrets passed. All 24 applied migration checksums matched the
  repository, including migrations 023 and 024.
- Corrected the older README statement that the tax calculator and reserve
  recommendations had not yet been implemented.

Temporary browser tools were installed in `/tmp/toms-checkpoint-tools`; missing
Chromium libraries were extracted into `/tmp/toms-checkpoint-libs/root`.
For example, rerun the new owner workflow with:

```bash
LD_LIBRARY_PATH=/tmp/toms-checkpoint-libs/root/usr/lib/x86_64-linux-gnu PYTHONPATH=/tmp/toms-checkpoint-tools RUN_POSTGRES_TESTS=1 .venv/bin/python tests/income_workspace_browser_checks.py
```

## Client invoice documents and sending history — 2026-10-10

- Full PostgreSQL suite: 431 tests ran successfully (427 passed, four optional
  disposable-server backup checks skipped). Added coverage for required business
  details before issue, draft/cancelled document markers, escaped text, owner-only
  print access, private data exclusion, durable sending/resends, stale duplicate
  revisions, CSRF, invalid/future sending times and ambiguous local clock times.
- Real sending history grants allow runtime SELECT/INSERT and reject UPDATE,
  DELETE and TRUNCATE. Legacy issued invoices retain empty seller fields without
  backfilling or changing the original document.
- Invoice Chromium workflows passed at 320, 390, 768 and 1280px, including
  entering business details, recording a send, preserving unpaid settlement,
  print layout and actual PDF generation with print controls hidden. Internal
  sending notes and destinations remain absent from the client document. Sample
  sending records leave real tables untouched. A print screenshot was inspected.
- Income-workspace, complete demo and System browser regressions passed. Ruff F,
  Bandit and whitespace checks passed; all 25 applied migrations matched their
  repository checksums. Migration 025 was applied locally.

PDF generation uses the browser's print dialog; no automatic email or delivery
receipt integration is present. Sending records document owner-entered delivery.

## Guided checklist — 2026-10-10

- Owner-only `/dashboard/checklist` derives five ordered actions from the shared
  planning estimate and saved records. Opening it does not start Starling calls,
  automatic sync or official rule checks. API credentials cannot access it.
- Full PostgreSQL suite: 441 tests ran successfully (437 passed; four optional
  disposable-server backup checks skipped). New checks cover actual withholding,
  missing/changed income, unknown tax treatment, non-taxable receipts, pending and
  future exclusions, transfers, unconfirmed spending, changed claims moved out of
  year, bounded lists, privacy, owner access and errors without false completion.
- Chromium checklist workflow passed at 320, 390, 768 and 1280px: follow a
  forecast link, complete it, enter actual CIS withholding, confirm payments and
  return to see all five steps clear. Escaped bank text, no overflow/JavaScript
  errors, and sample edits leaving real records untouched were verified. The
  mobile screenshot was inspected.
- Dashboard and tax-estimate regression checks passed at five widths; the
  income-workspace regression passed at four widths. Ruff F, Bandit and whitespace
  checks passed. No schema migration is required.

Counts describe actions; one receipt may appear in multiple steps. The first 20
items per step are displayed, while counts include every relevant record. Clear
checks do not guarantee all income sources, work or supporting evidence exist.

## New-feature bug and functionality audit — 2026-10-10

Reviewed and exercised invoices, print/PDF documents, sending history, expense
treatment, income dialogs/notices, earmarks and the guided checklist together.
Four reproducible functionality issues were fixed:

- Non-taxable income notices incorrectly requested stream assignment. Confirmed
  receipts now show no business reserve; unconfirmed receipts request review and
  confirmation without requiring a stream or displaying a guessed zero.
- Successful notice polling did not clear earlier fetch errors. Recovery now
  clears the error and rebuilds the displayed batch, retaining keyboard focus.
- An acknowledgement followed by a failed refresh stranded its disabled button.
  The acknowledged state is explained and the action becomes usable again;
  subsequent recovery replaces the stale batch.
- Chromium repeated the invoice grand total on successive table pages. Print
  styling now places the total once after the work rows.

The added backend/browser regressions failed before these fixes. Final full
PostgreSQL verification ran 443 tests: 439 passed, four optional disposable-server
backup integration checks skipped. A new concurrent sending test verifies one
submission saves and the stale competitor receives 409, leaving one history row.

All 12 browser scripts passed: checklist, invoices, income workspace, demo,
income forms/rules, review, tax estimate, System, login, dashboard, notice failure
recovery, and large invoice documents. Affected invoice/workspace workflows were
rerun after fixes. The large-document check verifies all 100 work items appear
once across 11 PDF pages, with long fields, one total and no print controls at
320, 390 and 1280px. Notice recovery/retry checks pass at 320 and 1280px.

Ruff F, Bandit, whitespace and configured-secret scans passed; all 25 applied
migration checksums match the repository. No live Starling calls were made;
bank responses were mocked and disposable/rollback-only data was used.

The optional large-document test needs `pypdf` alongside Playwright in the
temporary browser tools directory:

```bash
.venv/bin/pip install --target /tmp/toms-checkpoint-tools pypdf
LD_LIBRARY_PATH=/tmp/toms-checkpoint-libs/root/usr/lib/x86_64-linux-gnu PYTHONPATH=/tmp/toms-checkpoint-tools .venv/bin/python tests/invoice_document_browser_checks.py
```

## Tax-year evidence export (2026-10-10)

The owner-only preview/download has twelve new regression checks covering
unauthenticated/API denial, schema isolation, no-store downloads, CSV formula
protection, exact plan totals, unknown amounts, foreign-currency labels, escaped
HTML, UK local-midnight boundaries, pending records, changed expense claims,
complete invoice sending/removed allocation history and cross-year receipt
support. ZIP row counts match CSVs. Raw bank payloads are excluded. Database
failures return a safe 503 page without exception content.

An independent PostgreSQL snapshot test verifies repeatable reads while another
connection changes a stream, and verifies `transaction_read_only` is on. Export
calculation queries use the same snapshot as evidence. No live Starling or
remote rule checks run. Existing tax calculation callers retain their normal
session by default.

Browser checks download and inspect the real ZIP at 320, 390, 768 and 1280px,
follow checklist/estimate links, verify sample labelling/isolation and render the
standalone HTML report without overflow or JavaScript errors.

Validation: full PostgreSQL suite, 455 tests (451 passed, four optional backup
tests skipped); export browser checks, Ruff F, Bandit, whitespace and configured
secret checks passed. No migration is needed.

```bash
RUN_POSTGRES_TESTS=1 .venv/bin/python -m unittest discover -s tests -q
LD_LIBRARY_PATH=/tmp/toms-checkpoint-libs/root/usr/lib/x86_64-linux-gnu PYTHONPATH=/tmp/toms-checkpoint-tools RUN_POSTGRES_TESTS=1 .venv/bin/python tests/tax_export_browser_checks.py
```

## Work and payment reconciliation (2026-10-10)

Nineteen regression checks cover owner/API access separation, actual gross and
withholding, unchanged tax totals, overtime-only forecast sources, positive and
negative work/receipt differences, partial/full/removed/stale allocations,
older invoice payment budgets, draft reservations, cancellation, changed frozen
invoice work, incomplete/unconfirmed/gross-to-net inconsistent receipts,
unassigned receipts under filtering, pending/future/out-of-year records,
automatic/manual transfer classification, current and changed non-taxable
receipts, separate currencies, archived sources, full counts with capped display,
query validation, safe database errors and real/sample isolation with escaped
text. The page uses the existing read-only repeatable-read business snapshot.

Chromium checks at 320, 390, 768 and 1280px use disposable sample schemas and
exercise source filtering, long names, navigation to an invoice, explicit payment
allocation and the refreshed reconciliation balance. Existing invoice and
export browser suites also pass at those widths. No bank calls occur.

Full PostgreSQL verification: 474 tests run, 470 passed and four optional backup
tests skipped. Ruff F, Bandit, whitespace and configured-secret checks passed.
No database migration is required.

```bash
RUN_POSTGRES_TESTS=1 .venv/bin/python -m unittest discover -s tests -q
LD_LIBRARY_PATH=/tmp/toms-checkpoint-libs/root/usr/lib/x86_64-linux-gnu PYTHONPATH=/tmp/toms-checkpoint-tools RUN_POSTGRES_TESTS=1 .venv/bin/python tests/reconciliation_browser_checks.py
```

## Complete system and feature verification — 2026-10-10

Retested the final invoice, checklist, export and reconciliation implementation
before publication. The full PostgreSQL-backed suite ran 474 tests: 470 passed
and four optional backup integration cases were skipped in that invocation.
All 13 backup tests then passed separately, including those four integration
cases, on a disposable local PostgreSQL 16 server with matching client tools.
The temporary test runner selected those clients instead of the application's
pinned PostgreSQL 18 clients; application code and client configuration were
unchanged. The temporary server was stopped after verification.

All 14 browser scripts passed: dashboard, checklist, demo, income forms/rules,
income notice recovery, income workspace, large invoice PDFs, invoices, login,
reconciliation, transaction review, System, tax estimate and tax-year export.
Coverage includes mobile/desktop layouts, source filters, explicit allocation,
partial/CIS payments, stale revisions and recovery, sample isolation, ZIP
contents, authentication/session flows and all 100 invoice lines exactly once
across multiple PDF pages.

Ruff F, Bandit, whitespace checks and Git object integrity passed. All 25 applied
migration checksums match the repository. The outgoing six feature/fix commits
were scanned across 258 unique blobs for configured application/bank/database
secrets and excluded private files; no matches were found. Bank calls were mocked
throughout. No application bugs were found in this final verification run.
# Overview and navigation verification — 2026-10-11

On `codex/overview-navigation`, the full PostgreSQL suite ran 496 tests: 492
passed and four optional backup integration cases were skipped. Overview tests
cover owner-only access, unknown and available targets, agreement with existing
planning, GBP/EUR separation, all-year issued invoice totals, stale allocation
exclusion, escaped record labels, safe errors and real/sample snapshot routing.
The six overview tests passed again after extending the stale allocation check.
Presentation checks cover integer precision, unknown money, unsupported currency
scales, summer midnight, DST, winter day labels and rejecting naive timestamps.

All 17 browser scripts passed. The new overview script covers 320, 390, 768 and
1280px, initial/incomplete/sample states, the signed-in landing URL, skip-link
focus, grouped navigation, Escape focus restoration and saved-only overview
requests. Existing sample switching still redirects to the ledger and loads
mock balances; that transition is outside the overview-only network assertion.
Screenshots at mobile and desktop widths were inspected. The initial broad run
stopped at that overbroad assertion; it was corrected and all remaining scripts
passed. No application change was needed for it.

Ruff F, application Bandit and git diff --check passed. Bank requests and rule
checks were mocked. No schema migration, deployment or live Starling verification
was performed. Optional backup integration remains unrun because isolated CREATEDB
test credentials are not configured; prior historical backup results above do
not replace current restore verification before future schema changes.

## Transaction review verification — 2026-10-11

On `codex/transaction-review`, PostgreSQL discovery ran 512 tests: 508 passed,
four optional backup integrations skipped. All 18 browser scripts passed through
`scripts/verify.py --postgres --browser` and the remaining browser rerun after
updating a legacy test's manual quick-review caption. The guided script covers
320/390/768/1280px, filtered navigation, retained invalid entries, stale evidence,
expense eligibility, escaping and a JavaScript-disabled review journey. Mobile
ledger/check screenshots were inspected. Ruff F, application Bandit and
`git diff --check` passed. Bank traffic was mocked; no migration was applied.

`scripts/benchmark.py --ledger` uses disposable schemas. At 10,000 synthetic
records, matching ledger filtering measured 0.067s/7 queries, empty attention
filtering 0.034s/5 queries and guided selection 0.019s/5 queries. These local
measurements do not justify an index migration or establish production latency.

No live Starling requests were made. Optional isolated backup verification remains skipped.
