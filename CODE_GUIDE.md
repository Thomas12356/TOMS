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
        income.py           Income choices and amount validation
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

Read these functions in order:

1. `routes/transactions.py` → `put_income`: receives the JSON edit.
2. `services/transactions/income.py` → `income_body`: checks the fields and allowed values.
3. `routes/transactions.py` → `saved_transaction`: loads the bank payment.
4. `services/transactions/income.py` → `validate_reconciliation`: checks entered amounts
   against the deposit.
5. Back in `put_income`: updates `TransactionIncome`, commits, and returns HTML or JSON.

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
