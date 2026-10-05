# Getting into the code

Start with the feature you want to change. You do not need to understand the
Starling importer before working on categorisation or income forms.

## The four parts of the app

| Location | Responsibility |
| --- | --- |
| `app.py` | Creates Flask, registers route groups, configures the database, and exposes health checks. |
| `routes/` | Receives HTTP requests, validates their inputs, calls the relevant logic, and returns JSON. |
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
        auth.py             Checks the API key
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
5. Back in `put_income`: updates `TransactionIncome`, commits, and returns JSON.

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
| Change classification rules | `services/transactions/classification.py` and `routes/transactions.py` | `tests/test_classification.py` |
| Change transaction filters or returned fields | `routes/transactions.py`: `filters`, `FIELDS`, `list_transactions` | `tests/test_transactions.py` |
| Change monthly totals | `routes/reports.py`: `monthly_report` | `tests/test_reports.py` |
| Understand date, UUID, or text validation | `services/validation.py` | `tests/test_validation_and_errors.py` |
| Understand the import flow | `services/transactions/sync.py`: `run_sync` | `tests/test_transaction_sync.py` |

Changing a label is a small first exercise. Adding a new stored income type also
requires a new SQL migration because the database restricts allowed values.
Applied migrations have checksums: add a new migration instead of editing an old one.

For the first dashboard page, add `routes/dashboard.py`, a template under
`templates/`, and CSS under `static/`, then register the blueprint in `app.py`.
Those files are a next step; the dashboard has not been implemented yet.

## Check your changes

Run the normal tests; bank calls are mocked and PostgreSQL tests are skipped:

```bash
.venv/bin/python -m unittest discover -s tests
```

To run just the income tests, use `-p test_income.py`. To include the database
tests after applying migrations, prefix the command with `RUN_POSTGRES_TESTS=1`.
Their shared setup is explained at the top of `tests/support.py`; database
changes made by those tests are rolled back.
