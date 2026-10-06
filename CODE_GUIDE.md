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
