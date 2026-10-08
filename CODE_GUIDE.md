# Getting into the code

Start with the feature you want to change. You do not need to understand the
Starling importer before working on categorisation or income forms.

## The four parts of the app

| Location | Responsibility |
| --- | --- |
| `app.py` | Creates Flask, registers route groups, configures the database, and exposes health checks. |
| `routes/` | Receives HTTP requests, validates their inputs, calls the relevant logic, and returns HTML or JSON. |
| `services/` | Business rules, bank requests, and database operations used by routes. |
| `models.py` | Describes the database tables and their relationships. |

Each route module starts with its own blueprint, authentication hook, and error
handlers. `@transactions.put(...)`, for example, connects a URL and HTTP method
to the function immediately underneath it. `before_request(require_api_key)`
checks authentication before those functions run.

## Services by purpose

```text
services/
    banking/                Starling integration
        client.py           Sends bank requests
        feed.py             Validates feed items and follows pagination
        diagnostics.py      Runs the live endpoint checks
        rate_limit.py       Coordinates bank request limits
    transactions/           The income and categorisation work starts here
        classification.py   Manual transfer types and automatic defaults
        income.py           Income choices, validation and shared saving
        income_form.py      Converts browser amounts into exact minor units
        sync.py             Coordinates transaction imports
        store.py            Saves imports and their progress
    database/
        connection.py       Creates the shared db object and configures PostgreSQL
        migrations.py       Applies the SQL files in the root migrations/ folder
    web/
        auth.py             Checks API keys
        sessions.py         Browser sessions, CSRF and login attempt limits
        request_limits.py   Bounds incoming request bodies
    validation.py           Shared UUID, date, money, and text validation
    error_logging.py        Logs safe diagnostic details
```

Imports name the actual file, for example
`from services.transactions.income import income_body`. The `__init__.py` files
just describe each package. For manual categorisation, begin in
`services/transactions/`; the bank integration has its own folder.

## Follow one income edit

For browser edits, read these functions in order:

1. `routes/dashboard.py` → `edit_income`: loads the payment and preserves ledger navigation.
2. `templates/income.html`: renders the inputs and validation messages.
3. `services/transactions/income_form.py` → `form_values`: converts decimal amounts into integer minor units.
4. `services/transactions/income.py` → `income_body` and `save_income`: validate the details, reconcile against the deposit and update the record.
5. Back in `edit_income`: commits and returns to the ledger.

The JSON API starts at `routes/transactions.py` → `put_income` and uses the same
validation and saving functions. Browser regression tests are in
`tests/test_income_form.py`; optional Chromium layout checks are in
`tests/income_browser_checks.py`.

`Transaction` stores what the bank reported. `TransactionClassification` stores
your choice of income, expense, transfer, refund, or other.
`TransactionIncome` stores the income source and entered deduction details.
Keeping these separate lets bank updates preserve your manual choices.

Amounts are integer minor units: `1250` means £12.50 for GBP. `None` means unknown;
it is different from zero. Dates are converted to UTC. The app validates entered
income amounts but does not yet calculate annual tax.

## Where to make a change

| Change | Start here | Relevant tests |
| --- | --- | --- |
| Change an income source's display label | `services/transactions/income.py`: `INCOME_TYPES` | `tests/test_income.py` |
| Edit the classification form | `templates/classification.html` and `routes/dashboard.py`: `edit_classification` | `tests/test_dashboard_classification.py` |
| Change classification rules | `services/transactions/classification.py` and `routes/transactions.py` | `tests/test_classification.py` |
| Change transaction filters or returned fields | `routes/transactions.py`: `filters`, `FIELDS`, `list_transactions` | `tests/test_transactions.py` |
| Change monthly totals | `routes/reports.py`: `monthly_report` | `tests/test_reports.py` |
| Understand date, UUID, or text validation | `services/validation.py` | `tests/test_validation_and_errors.py` |
| Understand the import flow | `services/transactions/sync.py`: `run_sync` | `tests/test_transaction_sync.py` |

Changing a label is a small first exercise. Adding a new stored income type also
requires a new SQL migration because the database restricts allowed values.
Applied migrations have checksums: add a new migration instead of editing an old one.

## Your first frontend edits

The transaction page is available at `/dashboard`. Sign in with your owner account
at `/login` (see the README setup). It reads saved transactions;
classification editing is available beside each payment.

Read its three files in this order:

1. `routes/dashboard.py`: validates the page number, reads PostgreSQL, and
   prepares plain display values. The numbered comments explain the flow.
2. `templates/dashboard.html`: the HTML layout. `{{ ... }}` displays a value;
   `{% for row in rows %}` repeats the table row for each payment.
3. `static/css/dashboard.css`: colours, spacing, table styles, and mobile layout.

Try changing “Transactions” in the template or `--accent` in the CSS.
Reload the page to see your change. The stylesheet uses your system fonts and
needs no build command. Restart Flask after Python edits when debug mode is off.

`app.py` registers the dashboard blueprint and applies the same private-response
headers as the API. `tests/test_dashboard.py` checks authentication, rendering,
pagination, and safe failures. `static/js/balances.js` loads live account balances
from `/dashboard/balances` after the table renders, then fills in the balance cards.
That endpoint uses Starling's `balance:read` permission for each saved account.

## Check your changes

Run the normal tests; bank calls are mocked and PostgreSQL tests are skipped:

```bash
.venv/bin/python -m unittest discover -s tests
```

To run just the income tests, use `-p test_income.py`. To include the database
tests after applying migrations, prefix the command with `RUN_POSTGRES_TESTS=1`.
Their shared setup is explained at the top of `tests/support.py`. Database
fixtures roll back changes; concurrency tests create and remove temporary schemas.

### Dashboard account selection

The account dropdown automatically submits its form when changed, using
`static/js/account-selector.js`. Without JavaScript, a View account button is
available. The form submits a GET request with `account=<account UUID>`.
`routes/dashboard.py` checks that the account exists and filters the transaction
query. Pagination and the balance request retain that account ID. Switching via
the dropdown omits `page`, so the new ledger starts on page one.

This is a view preference stored in the URL, not a persistent setup setting.
All accounts remains available. No transaction, classification or income data is
changed. Importing accounts still uses the existing sync flow; the dropdown does
not import or delete anything. First-run setup and remembered user preferences
can build on the owner session next.

## Follow the browser login flow

1. `routes/login.py` checks CSRF, counts attempts, verifies the password and
   creates a browser session. Its numbered comments explain the sequence.
2. `services/web/sessions.py` loads sessions, enforces expiry and protects the
   dashboard. Flask-Login handles the current user; Flask-WTF validates CSRF.
3. `templates/login.html` contains the plain HTML form, and the dashboard header
   contains a POST logout form. Both send hidden CSRF tokens.
4. `models.py` and migration `006_owner_login.sql` store the owner hash, revocable
   browser sessions and the shared login-attempt counter.
5. `app.py` configures cookies and registers the `flask owner-password` command.

This application has one owner. API authentication stays in `services/web/auth.py`
and does not accept browser cookies. Do not add session acceptance to write APIs
without CSRF protection. Future dashboard POST forms are already checked by the
blueprint guard and need `{{ csrf_token() }}` in a hidden `csrf_token` field.

Use `tests/test_login.py` to see wrong-password, CSRF, throttling, cookie, expiry,
logout-replay and password-reset checks. Database tests roll back their changes;
they never create or replace your real owner account.

## Security regression tests

`tests/test_security.py` covers hostile credentials, CSRF, cookie replay,
password-reset races and concurrent login throttling. See
[SECURITY_TESTS.md](SECURITY_TESTS.md) for the findings, commands and scope.
The concurrent tests use a temporary PostgreSQL schema and separate connections;
they do not change your real owner account.

## Follow a classification edit

1. `routes/dashboard.py` builds the row's edit link with its three transaction
   IDs, account filter and page number.
2. `templates/classification.html` displays the payment, valid classification
   choices, notes and a hidden CSRF token. Start here to change form wording.
3. `edit_classification()` in `routes/dashboard.py` loads the payment, locks it
   on POST, validates the form, commits and returns to the original ledger page.
4. `services/transactions/classification.py` contains `save_classification()` and
   `clear_classification()`. The dashboard and API share these rules, including
   preserving income details and rejecting incompatible changes.

`tests/test_dashboard_classification.py` checks real PostgreSQL saves, reset,
CSRF rejection, escaped notes, invalid input and database failures. No bank API
calls are made when editing classifications.

## First-run setup and password settings

Server startup calls `services/web/setup.py` to print a one-hour setup token
and store its hash in `OwnerSetup` (migration `007_owner_setup.sql`). Flask run
and `python app.py` announce it once; `gunicorn.conf.py` does this in the master
process before its workers start. CLI imports for migrations and tests do not
generate tokens. `flask owner-setup-token` is still available for manual rotation. The browser's
`/setup` page in `routes/login.py` requires that token, creates the single owner
and consumes the token. A PostgreSQL advisory lock serializes setup, token
rotation and CLI recovery so two requests cannot claim different owners.

`/settings/password` requires an authenticated browser session and the current
password. It locks the owner row, updates the hash and revokes every session.
Both forms share `templates/owner_settings.html`, CSRF protection and the existing
login attempt limit. `tests/test_owner_setup.py` covers setup and password changes;
`tests/test_security.py` includes a simultaneous first-owner creation test.

Dashboard syncing lives in `services/transactions/automatic_sync.py`; the browser
controls are in `static/js/transaction-sync.js`. The `sync-transactions` Flask
command reuses the importer for the systemd timer installed by
`deployment/install_sync_timer.py`. The one-minute freshness check runs inside
the existing PostgreSQL import lock in `run_sync()`.

Transaction confirmation is separate from bank status: `confirmed_at` lives on
`Transaction`. `routes/review.py` lists unconfirmed records and saves a browser
owner's confirmation. `services/transactions/review.py` fingerprints the reviewed
details so stale popups cannot confirm changed records. The popup behaviour is
in `static/js/transaction-review.js`, and its markup/styles share the dashboard.
The importer and classification/income edits clear confirmation when details change.

## Income streams page

`routes/dashboard.py` → `income_streams` handles listing, creation, renaming and
archiving. `services/transactions/income_streams.py` contains the three stream
types and form validation. `templates/income_streams.html` renders the page.

`IncomeStream` stores the owner's name and chosen type. `TransactionIncome`
links a payment to it through `income_stream_id`. Migration
`009_income_streams.sql` creates the table and optional link, preserving older
records without creating streams. Browser edits require a chosen stream, while
the existing API's historical income types remain compatible.

The stream type and tax treatment are separate: no tax amount is inferred.
Renaming preserves payment links. Archiving blocks new assignments while
retaining existing records. Income saves return to the selected stream using
constrained local navigation. Tests are in `tests/test_income_form.py`.

Stream forecasts use `expected_gross_minor`, `expected_gross_period` and
`expected_gross_currency` on `IncomeStream`. Migration 010 leaves all three
unknown on existing records. `stream_fields` validates new or updated forecasts
using the same exact decimal parser as income payments. `annual_gross` multiplies
weekly forecasts by 52, monthly by 12 and yearly by 1; it never changes actual
payment amounts. `templates/income_forecast_fields.html` shares the create/edit
fields, and invalid edits retain the entered values.

Unpaid holiday is stored as exact decimal weeks in `unpaid_holiday_weeks`.
Migration 011 defaults it to zero and constrains it to 0–52. `stream_fields`
accepts at most two decimal places. `annual_gross` reduces the base annual amount
by `(52 - unpaid weeks) / 52`, then rounds half up to a minor unit using integer
arithmetic. Actual income records are unaffected. The shared forecast-field
macro adds the control to both create and manage forms.

Holiday entry now uses `unpaid_holiday` and `unpaid_holiday_unit` in forms.
Migration 012 remembers days/weeks and increases stored week precision to four
decimal places. Days convert to weeks using five working days per week.
`holiday_amount` converts back to the saved display unit; `annual_gross` keeps
integer arithmetic with the increased precision. Existing records stay in weeks.

## Tax-rule data and official checks

Start at `data/tax_rules/uk-ewni-2026-27.json` for reviewed values.
`services/tax/rules.py` reads this file, fetches only the fixed GOV.UK content URL,
extracts table cells and allowance text without rendering remote HTML, and compares
them with the reviewed dataset. Check metadata is written atomically in `instance/`.
Failures and changed values retain the reviewed data.

`routes/dashboard.py` → `tax_rules` renders `templates/tax_rules.html` and protects
manual checks with browser sessions and CSRF. Background page checks run at most
daily. `app.py` exposes `refresh-tax-rules`; the optional personal systemd timer
is installed by `deployment/install_tax_rules_timer.py`. Tests in
`tests/test_tax_rules.py` mock GOV.UK and never contact Starling. The financial
calculation engine is a separate next step; no estimates use these values yet.

Income editing also sends a hidden transaction `version`, produced by
`services/transactions/review.py`. On POST the parent payment is locked and the
version is checked before saving. A stale form returns 409 and restores current
saved values. This prevents amounts entered for an old bank currency from being
saved in a newly changed currency. Concurrency coverage is in
`tests/test_review_concurrency.py`.

## Tax year and part-year dates

Migration 013 adds `forecast_tax_year`, `forecast_starts_on` and
`forecast_ends_on` to `IncomeStream`. Existing records use the supported 2026–27
year and blank dates. Database checks reject unsupported years and reversed dates.

In `services/transactions/income_streams.py`, `forecast_date` parses optional
ISO dates and `active_days` clips their inclusive range to `TAX_YEARS`.
`annual_gross` now means the forecast for that tax year: it prorates the normal
annual rate by active days, subtracts the entered unpaid absence, and rounds
once to a minor unit. Form validation rejects absence exceeding the active time.
The shared macro includes the year/date fields in both create and edit forms;
invalid edits retain entered values. Financial records and confirmations are
not changed by forecast edits.


Database logins are separated in `services/database/connection.py` (runtime)
and `services/database/migration_connection.py` (maintenance only). The owner-run
`deployment/restrict_database.py` provisions roles once. Normal routes and sync
must continue using `db.session`; never load migration credentials in a route.
PostgreSQL concurrency fixtures use the migration login to create disposable
schemas, then run their actual requests with the restricted runtime login.

## Tax planning: where to change it

- `services/tax/estimate.py` contains the calculation without database or network
  calls. `income_tax` applies the allowance taper and progressive bands;
  `estimate_streams` combines forecasts and subtracts withholding credits.
- `routes/dashboard.py → tax_estimate` loads this year's streams for the signed-in
  owner. It is a read-only page and does not trigger a bank sync.
- `templates/tax_estimate.html` displays the annual target, percentage, individual
  stream forecasts, incomplete inputs and scope. Change layout here.
- `services/transactions/income_streams.py → stream_fields` validates the forecast
  gross income, active dates and absence. Actual credits are read from logged
  payment records by `services/tax/records.py`. Migration 016 retires forecasts
  of tax already deducted.
- `static/js/income-streams.js` opens and closes the stream creation dialog.
  Server validation preserves invalid entries and reopens the dialog.
- `tests/test_tax_estimate.py` has plain income examples and real PostgreSQL/auth
  checks. `tests/tax_estimate_browser_checks.py` verifies Chromium/mobile layout.

All money stays in integer pence, except temporary Decimal values needed for
half-penny allowance reductions. Annual tax rounds once, half-up. The reviewed
JSON contains the bands; `validate_publication` checks the calculator's band
values as well as the visible published table. Withholding forecasts do not
change the actual amounts entered on bank payments. Before adding NI or a new
jurisdiction/year, extend the reviewed rules and tests explicitly.

## Test mode: where the separation happens

`services/web/test_data.py` owns the browser selection and small sample fixtures.
`activate_test_data` runs after dashboard/review authentication; a signed browser
cookie alone cannot bypass login. `ensure_sample_data` uses a PostgreSQL lock to
seed once even if two browsers enable it at the same time. Add sample examples
here without reading live bank data.

`services/database/session.py → DataSession.get_bind` routes the eight business
models to `toms_demo` only for an authenticated browser test-mode request. Login,
owner and browser-session models continue using `toms`. Raw SQL and queries
mixing authentication with sample records are rejected in test mode. The demo
engine is cached per request so all demo edits and the seed lock share one
transaction. API and background/CLI contexts keep the normal binding.

`services/database/demo_schema.py` is maintenance-only. After migrations, it
creates the demo business tables from the same model definitions and grants DML
to the restricted runtime role. When migrations change the models, the sample
tables are rebuilt; real tables are never copied or dropped by this helper.
This keeps the same form/validation behavior without maintaining duplicate models.

The nav switch is a normal POST form with CSRF protection; it works without
JavaScript. A switch invalidates previously opened forms and always returns to
an unfiltered ledger. `test_data_notice.html` labels demo screens. The balance
and sync routes return examples/simulated runs in test mode, with guards at the
bank client and background-sync entry point as a second check.

`tests/test_test_data.py` exercises two disposable PostgreSQL schemas with
identical live/demo UUIDs, actual routed sessions, concurrent seeding and stale
forms. `tests/demo_browser_checks.py` runs a temporary loopback Flask server and
Chromium against those disposable schemas for complete browser workflows.

## Logged tax, expenses and mileage

Start with `services/tax/estimate.py`: this is the pure annual calculation. `services/tax/records.py` selects current payment credits and eligible linked expenses. `services/tax/mileage.py` handles annual groups, bands and reimbursements; `mileage_rules.py` validates both public HMRC sources against `data/tax_rules/uk-mileage-2026-27.json`. Remote pages never become executable code or rendered HTML.

`routes/deductions.py` handles owner forms; `services/tax/deduction_forms.py` keeps validation readable and separate. `templates/deductions.html` holds the page. Stream creation uses a native dialog in `templates/income_streams.html`, controlled by `static/js/income-streams.js`. No frontend framework is needed. `models.py` and migration 016 define the two record tables; `DATA_TABLES` includes both so demo writes remain isolated.

Use `tests/test_deductions.py` for calculation and authenticated form checks. The existing demo browser script exercises real Flask requests in disposable database schemas.

## Shifts: where to start

- `services/transactions/shifts.py` validates UK local times, calculates gross
  shift pay and loads per-stream totals in one query. `annual_gross` switches
  between the regular forecast and these loaded totals according to income mode.
- `routes/shifts.py` implements owner-only create/edit/delete forms.
  `templates/shifts.html` and `static/js/shifts.js` provide the page and pay-mode
  selector. Individual-shift streams link here with **Manage shifts**; regular
  forecasts use **Add overtime**.
- `IncomeShift` in `models.py` and migration 017 define stored shifts. Composite
  foreign keys on expenses and mileage prevent links to another stream's shift.
- `routes/deductions.py → linked_shift_id` validates optional links. The shared
  write lock coordinates deduction changes and shift deletion. Mileage link
  editing changes only the association, not the journey or allowance.
- `tests/test_shifts.py` covers money, overnight/DST times, stream isolation,
  forecast selection, stale versions and deduction links. The shared rollback
  fixture is `tests/deduction_support.py`; browser requests use disposable schemas
  through `tests/demo_browser_checks.py`.

## Overtime above regular pay

`IncomeShift.is_overtime` distinguishes extra pay from ordinary work records.
`routes/shifts.py` sets it when creating work for a regular-forecast stream; it
is not a user-editable flag and editing the payment never changes its meaning.
The same page shows Overtime or Shifts according to the stream's income pattern.
A hidden income-pattern value rejects creation forms opened before a mode change.

`attach_shift_totals()` loads both totals in one query. `annual_gross()` adds
only overtime to regular pay after planned absence; Individual shifts uses all
entered work instead. Bank receipts remain separate. Migration 018 preserves
existing records as ordinary shifts. Tests in `test_shifts.py` and
`demo_browser_checks.py` cover both patterns and their tax effects.

`app.py` sets the browser Content Security Policy. Keep JavaScript in local
`static/js/` files and styles in `static/css/`; inline scripts and handlers are
blocked. Chromium wait predicates use arrow functions to work under this policy.

## Relating streams for mileage

`services/transactions/mileage_relationships.py` sets a shared opaque
`IncomeStream.mileage_pool_id` after the owner selects an existing related
stream. Independent streams get distinct IDs. A stream copies the related
stream's current pool rather than pointing at a root stream: changing one
member never silently moves other members. Creation and settings changes use
the same advisory lock as mileage writes, plus fresh row locks. The UUID itself
is never accepted from the browser; only an existing stream selection is valid.
Employment cannot join a self-employed/CIS business.

`templates/mileage_relationship_fields.html` supplies the one-time dropdown;
`static/js/income-streams.js` filters its choices and the backend enforces the
same boundary. `mileage_partners()` supplies readable card summaries without
additional queries. `services/tax/mileage.py` counts related journeys together
and allocates their combined relief back to their original streams. Journeys
still belong to exactly one stream/shift; linked expenses are not grouped.

Migrations 019 and 020 retire per-journey groups and introduce stream
relationships. `tests/test_mileage_relationships.py` covers shared historical
thresholds, reimbursement offsets, creation, independent jobs, moving and
archiving members, incompatible/forged IDs and form authentication. The demo
browser script creates a related stream, checks the threshold and then
separates it to check recalculation.

### Tax readiness and National Insurance

- `services/tax/readiness.py` builds the short list of records needing attention.
- `services/tax/records.py` selects actual tax credits, payroll NI, deductions and
  confirmed gross receipts. It keeps incomplete receipt details separate from
  blockers that invalidate an annual savings target.
- `services/tax/national_insurance.py` calculates standard Class 4 once on combined
  business profits. Reviewed figures live in `data/tax_rules/uk-ni-2026-27.json`;
  `services/tax/ni_rules.py` checks them against the official source.
- `services/tax/estimate.py` combines forecasts with those records. The received
  target allocates the business charge before applying actual credits, so CIS
  deductions cannot be counted twice.
- `templates/tax_estimate.html` presents readiness and both savings targets.
  `templates/income.html` collects actual employee NI separately from income tax.
