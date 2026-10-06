# TOMS - The Organised Money System

TOMS is a personal finance project for importing Starling transactions,
manually categorising incoming money, and keeping income records ready for
an annual tax calculation. The goal is to make it clear where income came from,
which records need attention, and how a tax estimate was calculated.

**Current stage:** a working Flask JSON API with PostgreSQL storage and automated
tests and an initial transaction dashboard at `/dashboard`. Manual categorisation
and income details are available through the API and dashboard. The annual tax
calculator is planned and has not been implemented yet.

For a short map of the code and a walkthrough of an income edit, start with
[Getting into the code](CODE_GUIDE.md).

## Project scope and MVP

The MVP should let you import transactions, review incoming payments, enter
income details, and see an income-tax estimate for a supported jurisdiction and
tax year. It will focus on income, with no claimed business expenses or complex
reliefs in the initial calculation. That assumption should be visible alongside
the result.

The first supported jurisdiction and tax years still need to be chosen. The
existing income fields include UK-oriented concepts such as CIS and PAYE, but
there are currently no country-specific tax rules or calculations. The eventual
tax profile must capture any region or other inputs required by the supported
rules. Multi-country support is a later extension.

The proposed initial tax feature calculates income tax within its documented
scope. National Insurance, other social contributions, tax-return submission,
and payments to tax authorities would be separate future features. Recorded
withholding will be shown separately from calculated liability.

## What works today

| Feature | Current behaviour |
| --- | --- |
| Starling imports | Manual history imports and incremental updates, including active account spaces. |
| Saved transactions | Filtered, paginated reads from PostgreSQL without contacting Starling. |
| Transaction dashboard | Owner login, account selection, live balances, and a transaction table with classification editing. |
| Browser security | Hashed owner password, revocable sessions, login throttling, and CSRF-protected forms. |
| Manual categorisation | Edit classification and notes through the dashboard or API; restore automatic inference. |
| Income records | Record source, gross amount, deductions, adjustments, and confirmed tax treatment. |
| Review flags | Relevant bank corrections flag income details for review while preserving manual entries. |
| Monthly reports | Summarise settled cash flow by currency, spending category, and income source. |
| Sync reports | Inspect committed progress, completed runs, and failures. |
| Private API access | API-key authentication, private response headers, bounded requests, and safe error logging. |

Monthly reports describe cash flow from bank deposits. They do not calculate
annual tax. Unknown income amounts stay unknown, and choosing an income source
does not automatically establish its tax treatment.

## Next milestones

1. **Income review:** add dashboard forms for income details and a queue for
   incomplete or flagged incoming payments.
2. **First-run setup:** choose and remember the working account.
3. **Complete income inputs:** record income outside the connected account and
   prevent it from being counted twice.
4. **Annual tax calculation:** select a supported jurisdiction and tax year,
   apply verified, versioned rules, and test allowances and band boundaries.
5. **Tax summary:** show the calculation breakdown, recorded deductions,
   incomplete records, and an export of the underlying income data.

Deliver each milestone as a small change with focused commits and an explanation
of the files involved.

## Open the dashboard

With the server running, visit `/` (the root URL) or `/dashboard`. It redirects to `/login`, where
you use your owner username and password. Follow the setup below first.

The page shows 50 saved transactions at a time across all accounts, newest
first, including manual or automatic classifications. The account selector
filters both the saved transactions and live balances. Dates display in UTC.
The transaction table reads PostgreSQL. Live main-account balances load
separately from Starling above the table, using `balance:read` permission.
Each saved account gets its own balance card; a failed balance request leaves
the table usable. Reloading the page requests fresh balances.
If no transactions are saved,
use the manual sync endpoint described below, then reload the page.

The dashboard's **Account** dropdown filters transactions and live balances to
one saved account. Switching returns to page one and preserves all saved records.
Choose **All accounts** to view the combined ledger. Selection stays in the URL;
it is not yet a saved first-run preference or a tax-report inclusion setting.

Use **Edit classification** beside a payment to change its classification and
notes. **Use automatic classification** removes the manual override. Saves
return to the same account and page. Existing income details are preserved;
incompatible classification changes are blocked. These are local edits and
do not change bank records or establish tax treatment.

Layout lives in `templates/dashboard.html`; styling lives in
`static/css/dashboard.css`. See the frontend walkthrough in
[CODE_GUIDE.md](CODE_GUIDE.md) for small edits you can make yourself.

## Local setup

You need Python, PostgreSQL, and a Starling access token for live bank operations.
The app uses Flask, Flask-SQLAlchemy, SQLAlchemy, Psycopg, and HTTPX. Normal tests
mock bank requests and do not require a live bank token.

From the repository root, create the environment and install dependencies:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

For a new checkout, copy `.env.example` to `.env`. If `.env` already exists,
edit that file directly to preserve your existing settings.

Set the following values privately in `.env`:

- `STARLING_ACCESS_TOKEN`: the bank credential used by the server.
- `APP_API_KEY`: a separate random secret for callers of this app.
- `PGHOST`, `PGPORT`, `PGDATABASE`, `PGUSER`, and `PGPASSWORD`: your PostgreSQL
  connection settings. The default database name is `TOMS`.

Create the database if it does not already exist, using your normal PostgreSQL
administration tools. Then apply the table migrations and start Flask:

```bash
.venv/bin/flask --app app db-upgrade
.venv/bin/flask --app app run --host 127.0.0.1 --port 5000
```

Visit `http://127.0.0.1:5000/health` to check the app. Authenticate to
`/health/db` to check PostgreSQL. Running migrations creates the storage tables;
starting Flask does not import bank transactions. Call the manual sync endpoint
when you are ready to import.

Live bank operations use the **production Starling API** at
`https://api.starlingbank.com`, with the corresponding permissions on your token.
The example configuration disables Flask debug mode. Use HTTPS and a production
WSGI server if you deploy beyond your local machine.

## Code layout

```text
app.py                      Flask setup, route registration, health checks, CLI
models.py                   Database tables and relationships
routes/                     HTTP inputs, browser pages and JSON endpoints
templates/                  Dashboard, login and classification forms
static/                     Shared CSS and small browser scripts
services/                   Business rules, bank client, sync and database logic
    banking/                Starling client, feed parsing, diagnostics, rate limits
    transactions/           Classification, income rules, sync orchestration, storage
    database/               PostgreSQL connection and migration runner
    web/                    API keys, browser sessions, CSRF and request limits
    validation.py           Shared validation for IDs, dates, money, and text
    error_logging.py        Safe error diagnostics
migrations/                 Versioned PostgreSQL schema changes
tests/                      API, browser, security and opt-in database tests
CODE_GUIDE.md               Reading order and places to make your own changes
SECURITY_TESTS.md           Security findings, verification and remaining scope
.env.example                Configuration template without credentials
```

Start with `templates/dashboard.html` for the UI, `routes/dashboard.py` for
browser edits, and `services/transactions/` for classification and income rules. `routes/reports.py` contains the monthly report.
`services/transactions/sync.py` coordinates bank imports, while
`services/transactions/store.py` handles their database writes.

To make a small first edit, change a display label in `INCOME_TYPES` in
`services/transactions/income.py`, then run the income tests. Adding a new stored type also
requires a new migration because the database restricts allowed values.

```bash
.venv/bin/python -m unittest discover -s tests -p test_income.py
```

## API and operation reference

- [Caller authentication](#caller-authentication)
- [PostgreSQL](#postgresql)
- [Import and update transactions](#import-and-update-transactions)
- [Browse saved transactions](#browse-saved-transactions)
- [Classify transfer types](#classify-transfer-types)
- [Income sources and tax deductions](#income-sources-and-tax-deductions)
- [Monthly income and spending](#monthly-income-and-spending)
- [Routes](#routes)
- [Test all endpoints](#test-all-endpoints)
- [Tests and references](#tests-and-references)

## Caller authentication

The API endpoints (`/starling`, `/sync`, `/transactions`, `/reports` and
`/health/db`) require `APP_API_KEY`. This is separate from `STARLING_ACCESS_TOKEN`,
which stays on the server. Read-only curl calls using `--user api` still work. State-changing calls
(POST, PUT, PATCH, DELETE) require `Authorization: Bearer <APP_API_KEY>`;
automatically attached browser Basic credentials cannot authorize writes.
For the bearer examples below, set `APP_API_KEY` in your shell securely first
(for example `read -rs -p "API key: " APP_API_KEY`, then press Enter).

The browser dashboard uses its own owner login and session cookie. Browser
sessions do not grant API access. For scripts, `/dashboard` and its balances
endpoint also accept an explicit bearer key; they no longer accept Basic auth.
Missing credentials fail closed. `/` redirects into the protected dashboard
flow; `/health` remains public.
Banking and login responses use `Cache-Control: no-store`.

### Set up the owner login

Install requirements, generate an independent signing key, and put it in `.env`
as `SECRET_KEY` (keep it private; do not use your API key or bank token):

```bash
.venv/bin/pip install -r requirements.txt
.venv/bin/python -c 'import secrets; print(secrets.token_hex(32))'
.venv/bin/python -m flask db-upgrade
```

Starting Flask or Gunicorn from the project directory prints a private one-time
setup token in the server terminal when no owner exists. It is valid for one hour. Open
`/`, `/dashboard` or `/login`: if no owner exists, you are directed to `/setup`.
Enter the token there and choose your username and password (at least 8
characters). Only the token hash is stored. Restarting the server generates a replacement token and invalidates the previous
one. `flask owner-setup-token` remains available if you need a fresh token without
restarting. Setup closes permanently once an owner exists.

While signed in, use **Password settings** in the dashboard header to change
your password. It requires the current password and revokes all browser sessions,
including yours, so sign in again afterward. Forgotten passwords can still be
reset locally with `.venv/bin/python -m flask owner-password`; the command uses
hidden password entry and confirmation. There is no public registration or
email recovery. Existing owners go straight to the normal login page.

For **localhost HTTP development only**, launch with:

```bash
SESSION_COOKIE_SECURE=0 .venv/bin/python -m flask run --host 127.0.0.1
```

Visit `http://127.0.0.1:5000/dashboard`. The default `SESSION_COOKIE_SECURE=1`
requires HTTPS; leaving it enabled on plain HTTP can prevent cookies from being
sent and cause login to appear unsuccessful. Keep it enabled for tailnet hosting.

Sessions expire after 30 minutes without an authenticated dashboard request or
eight hours from sign-in. Logout revokes the current session in PostgreSQL, even
if its old cookie is replayed. Only hashed session tokens are stored in the
database. Login attempts are capped globally at 20 per fifteen-minute window;
this deliberately works without trusting client IP headers from a proxy.
The limit survives restarts and is shared by workers. A 429 response means wait
fifteen minutes before retrying. Login/logout forms use CSRF tokens; future
browser editing forms must include them too.

### Host privately on your tailnet

Run a production server bound to localhost, keeping `FLASK_DEBUG=0` and
`SESSION_COOKIE_SECURE=1`:

```bash
.venv/bin/gunicorn --bind 127.0.0.1:5000 --workers 2 app:app
```

In another terminal on that host:

```bash
tailscale serve 5000
```

Open the HTTPS URL printed by Serve and append `/dashboard`. Serve handles TLS;
the app keeps its own login and does not authenticate from proxy identity
headers. No proxy middleware is needed for this setup. Gunicorn automatically loads
`gunicorn.conf.py` from the project directory to announce setup once before its
workers start. Limit dashboard access
to your owner identity using tailnet policy rules, keep Funnel off, and keep
PostgreSQL private. Use a service manager to keep Gunicorn and Serve running
when deploying permanently. This repository does not change your tailnet policy
or enable Serve for you. [Tailscale Serve documentation](https://tailscale.com/docs/features/tailscale-serve).


## PostgreSQL

The app connects to your existing local `TOMS` database using Flask-SQLAlchemy.
Psycopg remains installed as SQLAlchemy's PostgreSQL driver.
Configure it in `.env`:

```dotenv
PGHOST=localhost
PGPORT=5432
PGDATABASE=TOMS
PGUSER=toms_app
PGPASSWORD=
```

If your server requires a password, set `PGPASSWORD` privately in `.env`.
Restart Flask after changing connection settings. Check the connection with:

```bash
curl --user api http://127.0.0.1:5000/health/db
```

Use your `APP_API_KEY` at the password prompt. A working database returns
`{"status":"ok","database":"TOMS"}`; connection failures return a safe 503.
The public `/health` route still checks only whether the Flask app is running.
Flask-SQLAlchemy manages a session for each application context and returns its
connections to the pool at teardown. Connection and statement timeouts are five
seconds, and pooled connections are checked before reuse.
Tables are created only by the explicit migration command below. Banking
information is stored only when you call the manual sync endpoint.

## Import and update transactions

Apply migrations once, and again when new migration files are added:

```bash
.venv/bin/flask db-upgrade
```

The migrations create `toms.accounts`, `toms.categories`, `toms.transactions`,
`toms.transaction_classifications`, `toms.transaction_income`, `toms.sync_runs`,
and `toms.sync_targets`, with migration checksums recorded in
`toms.schema_migrations`. Repeating the command is safe. Existing banking data
is not deleted.

Restart Flask, then start an import:

```bash
curl -H "Authorization: Bearer $APP_API_KEY" -X POST http://127.0.0.1:5000/sync/transactions
```

The first run discovers all accounts
accessible to the token, their default categories, and active savings/spending
spaces. It requests history from each account's `createdAt` to a fixed timestamp
captured at the start of the run, following Starling's pagination cursors until
there are no more pages. Required permissions are `account-list:read`,
`space:read`, and `transaction:read`.

The request runs synchronously: keep the connection open while it imports.
While it runs, you can inspect progress from another terminal or browser:

```bash
curl --user api http://127.0.0.1:5000/sync/runs
curl --user api http://127.0.0.1:5000/sync/runs/RUN_UUID
```

Use the `run_uid` returned by the POST, or the newest entry in `/sync/runs`.
Reports include status, committed page count, received item count, changed row
count, and each category's requested dates and earliest/latest received
transaction timestamps. A requested opening date does not prove Starling
retained every older transaction; verify the earliest result against statements.

Run the same POST again for incremental updates. Each category uses its last
successful checkpoint with a five-minute overlap. Starling's changed-items API
includes updates to older transactions, such as a pending payment becoming
settled. If the checkpoint is almost a year old, or a change response has at least
1000 items, the importer safely rescans paginated history instead.

### Date and account options

To force a historical import from a chosen date:

```bash
curl -H "Authorization: Bearer $APP_API_KEY" -X POST http://127.0.0.1:5000/sync/transactions \
  -H 'Content-Type: application/json' \
  -d '{"mode":"history","start":"2020-01-01"}'
```

Optional JSON fields:

| Field | Meaning |
| --- | --- |
| `mode` | `auto` (default), `history`, or `incremental`; a new category always needs an initial historical import. |
| `start` | Historical lower bound: `YYYY-MM-DD` or a timestamp with timezone. Defaults to account opening or existing history start. |
| `end` | Historical upper bound; cannot be in the future. Defaults to the fixed run start timestamp. A date means midnight at its start in UTC. |
| `accountUid` | Import only this accessible account UUID. |
| `categoryUid` | Import only this category UUID; requires `accountUid`. Can specify a known archived space omitted by active-space discovery. |

JSON options are read even for streamed requests without `Content-Length`.
An empty body uses defaults; a nonempty body requires valid JSON options.

Dates force history fetching and cannot be combined with `mode: incremental`.
Automatic imports discover active spaces; unknown archived spaces cannot be
discovered by that endpoint. Explicit account/category selection avoids the
space-discovery request. A manual old-date query does not advance the checkpoint
for updates to the present, and disjoint date ranges are not marked as continuous
coverage.

### Storage and failure handling

Money is stored as nonnegative integer minor units plus currency and `IN`/`OUT`
direction. All statuses are retained, including pending, reversed, and declined.
Rows are keyed by account UUID, category UUID, and feed item UUID. Repeated
imports do not duplicate transactions. Newer `updatedAt` values replace earlier
versions; stale responses do not overwrite newer stored versions. Full raw feed
JSON is also stored, including private transaction details, so treat PostgreSQL
and its backups as private data.

Each page and its progress counts commit together. If a later page fails, earlier
pages remain stored, the target/run is marked failed, and that category's
checkpoint is not advanced. Other categories completed before the failure retain
their successful checkpoints. A rerun safely re-fetches incomplete history; it
does not resume from a stored cursor. No rows are deleted because they were
absent from a response. If a worker exits abruptly, the next sync marks its
unfinished run interrupted. A database advisory lock allows one sync at a time;
a concurrent request returns 409. Rate-limit failures return 429 with a run ID
when available; check its report before rerunning after the limit resets.

Imports do not create bank payments or change transaction metadata at Starling.

## Browse saved transactions

`GET /transactions` reads your imported transactions from PostgreSQL. It requires
`APP_API_KEY` and does not contact Starling or change stored data.

```bash
curl --user api 'http://127.0.0.1:5000/transactions?start=2026-06-01&end=2026-06-30&direction=OUT&page=1&per_page=50'
```

Enter your app key at the password prompt. Optional query parameters:

| Parameter | Meaning |
| --- | --- |
| `start` | First included UTC calendar day, `YYYY-MM-DD`. |
| `end` | Last included UTC calendar day, `YYYY-MM-DD`; includes the whole day. |
| `accountUid` | Filter by one account UUID. Omit to include all saved accounts/spaces. |
| `direction` | `IN` or `OUT`. |
| `status` | Exact stored status, such as `SETTLED` or `PENDING`; all statuses are supported. |
| `income_type` | A supported income source from `/transactions/income-types`; matches explicitly labelled income. |
| `tax_treatment` | `unknown`, `no_tax_deducted`, `cis`, `paye`, `other_deduction` or `non_taxable`; matches saved income details. |
| `classification` | Effective transfer type: `income`, `expense`, `internal_transfer`, `refund` or `other`. |
| `page` | Page number, default 1. |
| `per_page` | Items per page, default 50, maximum 100. |

Results are ordered by transaction time, newest first, with transaction IDs used
to break ties. Each item includes IDs, `amount_minor`, currency, direction,
status, transaction/update/settlement timestamps, counterparty name, reference,
spending category and source information. Amounts stay in integer minor units:
`1250` with `GBP` means £12.50. Timestamps use ISO 8601 in UTC. Each item also includes `classification` with `type`, `origin`, `notes`, and
`updated_at`. Raw bank JSON is not loaded or returned. Example response structure:

```json
{
  "transactions": [],
  "pagination": {
    "page": 1,
    "per_page": 50,
    "total": 0,
    "pages": 0,
    "has_next": false,
    "has_prev": false
  }
}
```

`total` counts transactions matching the filters across all pages. Filters matching no records
or a page beyond the results return an empty array with HTTP 200. Malformed,
duplicate or unknown parameters return JSON with HTTP 400. Database failures
return a safe 503. Banking cache protections also apply to this route.
No new migration is needed. Like other page-number APIs, results can move between
pages if transactions are synced while you browse.

## Classify transfer types

Transfer classifications are stored locally in `toms.transaction_classifications`,
separately from bank fields. Manual choices survive repeated imports and bank
updates. The migration has been applied to the local `TOMS` database; other
installations should run `flask db-upgrade`. Restart Flask to load the new routes.

All these endpoints require `APP_API_KEY` and make no bank requests:

| Method and path | Purpose |
| --- | --- |
| `GET /transactions/classification-types` | List supported types and permitted directions. |
| `GET /transactions/{accountUid}/{categoryUid}/{feedItemUid}/classification` | Read the effective classification. |
| `PUT /transactions/{accountUid}/{categoryUid}/{feedItemUid}/classification` | Create or replace a manual classification. |
| `DELETE /transactions/{accountUid}/{categoryUid}/{feedItemUid}/classification` | Clear the manual choice and restore inference. |

Use all three IDs from a `/transactions` result. `income` requires an incoming
transaction, `expense` requires an outgoing transaction, and `internal_transfer`,
`refund` and `other` support either direction. Unedited transactions are inferred
as `internal_transfer` when Starling's source is `INTERNAL_TRANSFER`, otherwise
`income` for `IN` and `expense` for `OUT`. Inference labels incoming/outgoing
cashflow; it does not determine taxable income or deductible expenses.

Example (replace the three UUID placeholders):

```bash
curl -H "Authorization: Bearer $APP_API_KEY" -X PUT \
  'http://127.0.0.1:5000/transactions/ACCOUNT_UUID/CATEGORY_UUID/FEED_ITEM_UUID/classification' \
  -H 'Content-Type: application/json' \
  -d '{"type":"internal_transfer","notes":"Transfer to my other bank account"}'
```

The response contains a `classification` object with the chosen `type`,
`origin: "manual"`, optional `notes`, and UTC `updated_at`. Notes may be null or
up to 2000 characters. A PUT replaces both type and notes, so omitting notes
clears them. Invalid types/bodies or incompatible directions return 400; an
unknown saved transaction returns 404. Clearing an already automatic
classification is safe and returns the inferred result with `origin: "automatic"`.
Clearing a classification preserves the underlying bank transaction.

Filter the transaction browser with `classification=internal_transfer` to find
both inferred and manually marked own-account transfers. Manual types take
precedence over bank inference, including in monthly reports. A classification
applies to exactly one transaction record; if both sides of your own transfer
are imported, mark both sides. No automatic pairing is performed.

`refund` and `other` are labels for now: monthly reports still count their amounts
according to `IN`/`OUT`. Business expense links,
and mileage can be added separately later.

## Income sources and tax deductions

Income details are stored separately in `toms.transaction_income` using
Flask-SQLAlchemy. They survive syncs and do not change the bank transaction.
There is no evidence storage or automatic tax calculation.

| Endpoint | Purpose |
| --- | --- |
| `GET /transactions/income-types` | List income sources and tax treatments. |
| `GET /transactions/{accountUid}/{categoryUid}/{feedItemUid}/income` | Read income details, or `income: null` when not entered. |
| `PUT /transactions/{accountUid}/{categoryUid}/{feedItemUid}/income` | Create or replace income details. |
| `DELETE /transactions/{accountUid}/{categoryUid}/{feedItemUid}/income` | Clear details, preserving the bank transaction and transfer classification. |

Use the three IDs returned by `/transactions`. The transaction must be incoming
and its effective transfer classification must be `income`. If it is currently
labelled as an own-account transfer, refund or other, first correct its
`/classification` explicitly. Income details must be deleted before changing its
classification away from income, or clearing a manual classification that would
restore a non-income type.

For roofing where deductions have not been confirmed:

```bash
curl -H "Authorization: Bearer $APP_API_KEY" --request PUT \
  'http://127.0.0.1:5000/transactions/ACCOUNT_UUID/CATEGORY_UUID/FEED_ITEM_UUID/income' \
  --header 'Content-Type: application/json' \
  --data '{"income_type":"roofing","tax_treatment":"unknown","source_name":"Roofing contractor"}'
```

For Amazon Flex, choose `income_type: "amazon_flex"` and select the tax treatment
you have confirmed. Source and tax treatment are independent: choosing roofing
never automatically selects CIS, and choosing employment never selects PAYE.

The additional incoming-money categories are:

| `income_type` | Use |
| --- | --- |
| `personal_gift` | A personal cash gift. |
| `inheritance` | Inherited money. |
| `loan_received` | Money borrowed. |
| `loan_repayment` | Principal repaid on money you lent; interest is separate. |
| `tax_refund` | A repayment of tax. |
| `personal_item_sale` | Proceeds from selling your own belongings. |
| `tax_free_benefit` | A benefit you have confirmed is tax-free. |

Selecting a category defaults to `tax_treatment: "unknown"`, not an automatic
exemption. Set `tax_treatment: "non_taxable"` explicitly when confirmed. A
`non_taxable` entry allows a zero or unknown deduction amount and is still
included in cash flow and in the monthly source breakdown.
`no_tax_deducted` records absence of withholding, not an exemption from tax.

Example body for a confirmed non-taxable personal gift:

```json
{"income_type":"personal_gift","tax_treatment":"non_taxable","tax_deducted_minor":0}
```

Categories describe incoming money; loans are not business earnings.
Interest on money lent must be recorded separately from principal. Personal
sales can involve trading or capital gains, and income generated by an
inheritance is separate from inherited capital. Use `tax_free_benefit` only
for a confirmed tax-free benefit, not all benefit payments. See
[HMRC's personal-sale guidance](https://www.gov.uk/guidance/selling-goods-or-services-on-a-digital-platform),
[personal possessions and Capital Gains Tax](https://www.gov.uk/capital-gains-tax-personal-possessions),
[inheritance guidance](https://www.gov.uk/tax-property-money-shares-you-inherit),
and [tax-free and taxable benefits](https://www.gov.uk/income-tax).

`income_type` is required. `tax_treatment` defaults to `unknown`.
`source_name` is optional (maximum 200 characters). `gross_minor` and
`tax_deducted_minor` are optional nonnegative integer amounts. For GBP, 100 means
£1. Unknown amounts stay `null`, not zero. All writes replace the full income
details; omitted optional fields are cleared. `updated_at` records the last
change to the income record, including a review flag set during sync.

When gross and withholding are known, amounts must reconcile exactly:

```text
net_received_minor = gross_minor - tax_deducted_minor + adjustment_minor
```

`adjustment_minor` is an optional signed integer, defaulting to zero. Positive
adjustments add to the deposit (for example a separate reimbursed amount);
negative adjustments subtract from it (for example another deduction).
A nonzero adjustment requires `adjustment_notes`, a nonempty explanation of at
most 2000 characters. Example for a £25 deposit with £30 gross, £10 withholding,
and a separately recorded £5 addition:

```json
{
  "income_type": "roofing",
  "tax_treatment": "cis",
  "gross_minor": 3000,
  "tax_deducted_minor": 1000,
  "adjustment_minor": 500,
  "adjustment_notes": "Additional reimbursed cost"
}
```

If withholding is unknown, gross plus adjustment cannot be below the deposit,
but the app does not infer the missing tax amount. If gross is unknown, full
reconciliation is deferred. `no_tax_deducted` and `non_taxable` establish zero
withholding for validation even if the recorded tax amount is null. A known tax
deduction cannot exceed gross. Adjustments describe the payment composition;
they do not establish the tax treatment of a reimbursement or deduction.

Income responses include `net_received_minor` and `currency` from the current
bank transaction, `recorded_currency` identifying the currency used for the
entered amounts, and `needs_review`. A sync correction to amount, currency,
direction, transaction time, bank source, counterparty name or reference sets
`needs_review: true`, preserving all entered amounts and their recorded
currency. Changes only to status, settlement time or update time do not set it.
The flag remains until you PUT valid income details again against the current
bank record. Rejected writes and repeated/older bank data do not clear it.
Sync and income edits lock the same transaction row so the correction and
review flag are committed together.

Apply `flask db-upgrade` for migration 005. Existing income entries are preserved
and flagged for a one-time review because they predate reconciliation checks
and currency tracking. New validated entries start with `needs_review: false`.

Filter `/transactions?income_type=roofing&tax_treatment=unknown` to find saved
roofing income whose deductions still need confirming. Unlabelled income has
`income: null` and does not match income-detail filters.

## Monthly income and spending

`GET /reports/monthly?month=2026-06` summarises saved, settled transactions for a
UTC calendar month. It requires `APP_API_KEY`, reads PostgreSQL only and requires
`flask db-upgrade` to have applied the income-details migration.

```bash
curl --user api 'http://127.0.0.1:5000/reports/monthly?month=2026-06'
```

Optionally add `accountUid=YOUR_ACCOUNT_UUID` to report on one account and all
its imported spaces. Without it, all saved accounts and spaces are included.
`month` is required in `YYYY-MM` format. Duplicate/unknown parameters and invalid
months or account UUIDs return JSON with HTTP 400; database failures return 503.

The response includes `month`, `timezone`, `account_uid` (null for all accounts),
`status`, the inclusive `period_start`, exclusive `period_end`, and a `currencies`
array. Each currency has:

| Field | Meaning |
| --- | --- |
| `income_by_type` | Incoming transactions classified as income, grouped by income source and tax treatment, with bank deposit totals in `net_received_minor` and counts. Unlabelled income uses `unclassified` / `unknown`. |
| `income_needing_review` | Count of saved income entries needing review among settled transactions in this currency/month, including entries on excluded internal transfers. |
| `income_minor` | Sum of included incoming amounts. |
| `spending_minor` | Sum of included outgoing amounts. |
| `net_minor` | Income minus spending, which can be negative. |
| `transaction_count` | Number of included settled transaction records. |
| `spending_by_category` | Category, `amount_minor` and `transaction_count`, sorted by spending descending. |
| `excluded_internal_transfers` | Separate transaction count, incoming and outgoing minor units for marked internal transfers. |

All monetary totals are integers in the currency's minor units. Currencies are
reported separately without conversion or a combined total. Missing, empty and
`NONE` spending categories appear as `UNCATEGORISED`. Inbound refunds contribute
to incoming cashflow when settled. Refunds are not deducted from the outgoing
category totals.

The report uses `transaction_time` to choose the month and includes only records
whose current stored status is `SETTLED`. Pending, reversed, declined and other
statuses are excluded. Net measures the included monthly cashflow. It reflects
the history currently imported, so sync before requesting an up-to-date report.
Empty months or unmatched account filters return `currencies: []` with HTTP 200.
A currency with only marked internal transfers still appears with zero included
totals and its excluded transfer amounts.

Transactions whose effective classification is `internal_transfer` are excluded
from both income and spending. A manual choice overrides bank inference; without
a manual choice, Starling source `INTERNAL_TRANSFER` supplies the inference. This source is present in
[Starling's feed schema](https://developer.starlingbank.com/api/openapi.json).
Transfers lacking that marker, such as transfers involving another bank, count
as incoming/outgoing cashflow until you manually mark them `internal_transfer`. Spending labels such as
`PERSONAL_TRANSFERS` alone are not treated as evidence of an internal transfer.
This prevents legitimate payments to other people being silently excluded.
Raw bank JSON and individual transaction details are not returned by the report.

## Routes

All paths below start with `/starling`. IDs in braces must be UUIDs.
GET requests return Starling's JSON unless a download format is listed.

| Permission | Method and path | Required query parameters |
| --- | --- | --- |
| `account-holder-name:read` | `GET /account-holder/name` | — |
| `account-list:read` | `GET /accounts` | — |
| `balance:read` | `GET /accounts/{accountUid}/balance` | — |
| `confirmation-of-funds:read` | `GET /accounts/{accountUid}/confirmation-of-funds` | `targetAmountInMinorUnits` (nonnegative integer; 1250 means £12.50 for GBP) |
| `mandate:read` | `GET /direct-debit/mandates` | — |
| `metadata:create`, `metadata:edit` | `PUT /feed/account/{accountUid}/category/{categoryUid}/{feedItemUid}/receipt` | JSON receipt body; see below |
| `payee:read` | `GET /payees` | — |
| `payee-image:read` | `GET /payees/{payeeUid}/image` | — (PNG download) |
| `payee-transaction:read` | `GET /payees/{payeeUid}/account/{accountUid}/payments` | `since` (`YYYY-MM-DD`) |
| `pay-local:read` | `GET /payments/local/payment-order/{paymentOrderUid}` | — |
| `receipts:read` | `GET /feed/account/{accountUid}/category/{categoryUid}/{feedItemUid}/receipts` | — |
| `savings-goal:read` | `GET /account/{accountUid}/savings-goals` | — |
| `savings-goal-transfer:read` | `GET /account/{accountUid}/savings-goals/{savingsGoalUid}/recurring-transfer` | — |
| `scheduled-payment:read` | `GET /payees/{payeeUid}/account/{accountUid}/scheduled-payments` | — |
| `space:read` | `GET /account/{accountUid}/spaces` | — |
| `standing-order:read` | `GET /payments/local/account/{accountUid}/category/{categoryUid}/standing-orders` | — |
| `statement-pdf:read` | `GET /accounts/{accountUid}/statement/pdf` | `yearMonth` (`YYYY-MM`; PDF download) |
| `statement-csv:read` | `GET /accounts/{accountUid}/statement/csv` | `yearMonth` (`YYYY-MM`; CSV download) |
| `feed-export-csv:read` | `GET /accounts/{accountUid}/feed-export` | `start` (`YYYY-MM-DD`); optional `end` |
| `transaction:read` | `GET /feed/account/{accountUid}/category/{categoryUid}` | `changesSince` (timestamp with timezone, e.g. `2026-01-01T00:00:00Z`) |

Use `/starling/accounts` to find your account UID and default category UID, or
let the diagnostic discover them automatically. In the
`/payees/.../account/...` routes, `accountUid`
is the **payee's account UID**, obtained from `/starling/payees`.

Additional GET routes retrieve an individual mandate or payee by appending its
UUID to the list route, an individual savings goal or standing order by appending
its UUID to its list route, and payment order payments by appending `/payments`
to the payment order route.

For one transaction, append `/{feedItemUid}` to the feed route. To fetch a date
range, append `/transactions-between` and supply `minTransactionTimestamp` and
`maxTransactionTimestamp`, both with timezones.

Example balance request (replace the UUID):

```bash
curl --user api http://127.0.0.1:5000/starling/accounts/aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa/balance
```

## Create or edit receipt metadata

Starling maps `metadata:create` and `metadata:edit` to its receipt submission
operation. Call the `PUT .../receipt` route with `Content-Type: application/json`:

```json
{
  "metadataSource": "CUSTOMER",
  "receiptIdentifier": "receipt-001",
  "totalAmount": 12.5,
  "currencyCode": "GBP",
  "receiptMerchant": {"identifier": "Example shop"},
  "items": [{"description": "Lunch", "amount": 12.5}],
  "paymentMethods": [{"description": "Card", "amount": 12.5}]
}
```

Amounts in this receipt are decimal currency amounts. To update a receipt,
include the `receiptUid` returned when it was created. Calling this PUT route
writes receipt metadata to Starling. Read endpoints do not create payments or
transfer money. No bank requests are made when starting the app or running tests.

Invalid filters and receipt bodies return HTTP 400. Missing server credentials
return 503. Starling authentication failures and network/server failures return
502, with safe messages. Selected upstream errors (including 404 and 429) retain
their status codes. Tokens and upstream error bodies are not returned.

## Test all endpoints

Open `http://127.0.0.1:5000/starling/test` in a browser to run live read checks
with `GET`. `POST /starling/test` runs the same checks and supports JSON options:

```bash
curl -H "Authorization: Bearer $APP_API_KEY" -X POST http://127.0.0.1:5000/starling/test
```

The report includes `summary` counts and one result per endpoint with `passed`,
`failed`, or `skipped`, its HTTP status when called, and any safe error message.
HTTP 200 means the report was produced; inspect the individual results for bank
request failures. `allPassed` is true only when every check passed, with no skips.
Downloads report their size and type; account data and download contents are not
included in the report.

The diagnostic uses an explicit allowlist of the original 20 permissions plus
`account-list:read` for automatic account discovery. It makes 20 checks because
metadata create and edit share one operation. Each result lists its applicable
`scopes`.

Only the bank token is needed for account discovery when it includes
`account-list:read`; callers must still authenticate with `APP_API_KEY`.
Discovery prefers your GBP account and uses its `defaultCategory` for feed checks.
To override automatic account selection, optionally add these IDs to `.env`:

```dotenv
STARLING_ACCOUNT_UID=your-account-uuid
STARLING_CATEGORY_UID=your-default-category-uuid
```

Use actual UUIDs in place of those placeholders. For POST checks, you can instead
supply `accountUid` and `categoryUid` in the JSON body. JSON values override `.env`.
If discovery fails and no IDs are configured, account checks are skipped and the
discovery failure appears in the report.

The diagnostic discovers other IDs from the first payee, mandate, savings
goal, recent transaction, and standing order. Resources that don't exist are
skipped. Override these choices by sending any of `accountUid`, `categoryUid`,
`feedItemUid`, `payeeUid`, `payeeAccountUid`, `savingsGoalUid`, `paymentOrderUid`,
and `mandateUid` as UUID strings in a JSON body.

Checks use the last 30 days, the previous calendar month's statement, and zero
minor units for confirmation of funds. All Starling requests, including ordinary
endpoints and concurrent diagnostics, use a shared SQLite limiter. It spaces
admission by 250ms and limits each bank token to 1000 attempts per rolling 24h.
It stores a token hash and request timestamps in ignored
`instance/starling-rate-limit.sqlite3`. Worker processes on the same machine
must share this database; multiple hosts would need a shared rate-limit service.
When no slot is available within two seconds, the API returns 429 with
`Retry-After`. A run can use up to 20 bank requests and take several
seconds; a 429 response stops further checks. Other failed checks do not stop
independent endpoints.

Metadata is skipped by default. To test a metadata write, send
`includeMetadataWrite: true`, a `receipt` object using the shape above, and explicit
`accountUid`, `categoryUid`, and `feedItemUid`. This performs **one real receipt
write** to your chosen transaction: omit `receiptUid` to create or supply it to
update. The create and edit permissions share this route; a single diagnostic
run tests the supplied operation, not both permissions independently.

The bank HTTP client rejects redirects so the bank token cannot be forwarded to
another host or an HTTP URL. Interrupted upstream responses return safe 502
errors, and invalid receipt amounts return 400.

## Implementation choices

Bank requests use one synchronous HTTPX client with connection reuse, explicit
10-second network timeouts and redirects disabled. Local rate limits and safe
upstream error messages are handled by `services/banking/client.py` and
`services/banking/rate_limit.py`. Caller authentication lives in `services/web/auth.py`;
each route blueprint explicitly registers it. Shared query-string validation
lives in `routes/helpers.py`.

Diagnostics validate options, discover resources and execute/report checks in
separate helpers. Flask's `url_for` builds diagnostic URLs; internal dispatch
still exercises the actual authenticated routes. The permission allowlist and
explicit receipt-write opt-in remain in place.

Sync planning uses a small immutable `SyncPlan` and a separate category importer.
Shared ID, timestamp and money validation lives in `services/validation.py`.

Database models live in `models.py`: `Account`, `Category`, `Transaction`,
`TransactionClassification`, `TransactionIncome`, `SyncRun`, and `SyncTarget`.
They map to the `toms` tables. `services/database/connection.py` configures the `db`
extension using the `PG*` settings; `services/transactions/store.py` uses model lookups,
attribute updates and session commits.

Each imported page commits its transactions and progress together. A separate
SQLAlchemy connection holds a PostgreSQL transaction-level advisory lock for the
whole import, so page commits cannot release the lock. Closing that connection
releases the lock before it returns to the pool. Transaction insert/update
comparisons are made while this lock prevents concurrent imports.

The existing `flask db-upgrade` command applies the versioned SQL migrations
through SQLAlchemy and preserves the existing migration history and checksums.
Use migrations for future schema changes; changing a model alone does not alter
an existing table.

For interactive queries, start the Flask shell:

```bash
.venv/bin/flask shell
```

The shell already includes `db` and the banking models listed above. For example:

```python
transactions = db.session.scalars(
    db.select(Transaction).order_by(Transaction.transaction_time.desc()).limit(20)
).all()

for transaction in transactions:
    print(transaction.transaction_time, transaction.amount_minor, transaction.currency)
```

In other modules, import `db` from `services.database.connection` and models from `models`.
Database operations require a Flask request or application context. Normal model
changes use `db.session.add(...)` and `db.session.commit()`.

## Request limits and diagnostics

Request bodies have a 1 MiB limit (`MAX_CONTENT_LENGTH = 1048576` in `app.py`).
Oversized JSON bodies return HTTP 413 with a JSON `error` and `max_bytes`,
including streamed requests without `Content-Length`. Bodies at the limit are
accepted and validated normally. Authentication runs before private routes read
the body, and error responses retain the private response cache headers.

Timestamps that cannot be converted into the supported UTC range return HTTP
400. Income source names, adjustment notes, classification notes and status
filters reject null characters and invalid Unicode text before database access.

Handled database failures and unexpected sync failures emit ERROR records using
the standard Python logger `toms.errors`. These appear in server stderr by
default and can be routed through your hosting server's logging configuration.
Each record includes the operation, exception class, a database SQLSTATE when
available, up to eight code locations, and the sync run UID when available.
Exception messages, SQL statements/parameters, request bodies, headers,
credentials and stack-frame local variables are excluded. Failures when recording
a failed sync or releasing its lock are also logged. Client responses continue
to use safe error messages; inspect the server logs to identify the failing code.

## Tests and references

```bash
.venv/bin/python -m unittest discover -s tests -v
```

Requests in tests use fake credentials and mocked HTTP responses.
Real PostgreSQL tests are opt-in and roll back every synthetic data change:

```bash
RUN_POSTGRES_TESTS=1 .venv/bin/python -m unittest discover -s tests -v
```

Apply migrations before running those tests. They never contact Starling.
The routes are based on [Starling's public OpenAPI schema](https://developer.starlingbank.com/api/openapi.json).
Statement download paths and content negotiation follow
[Starling's official SDK account client](https://github.com/starlingbank/starling-developer-sdk/blob/master/src/entities/account.js).

Income source breakdowns use the same settled status, month, account filter and
currency separation as the monthly cash-flow report. Refunds and other incoming
transfer types still contribute to cash flow but are excluded from
`income_by_type`. The breakdown is based on net deposits; it is not a gross
income or tax-liability calculation.

## Automatic transaction syncing

The dashboard checks for a completed sync when it loads. If the last successful
full sync is at least one minute old, it imports transactions in the background
and reloads the current account/page when finished. **Sync now** forces an import.
Both require browser login and CSRF protection; API keys are never sent to JavaScript.
Syncing covers all accessible accounts and retains manual classifications.

For twice-daily background imports on Linux, run:

```bash
.venv/bin/python deployment/install_sync_timer.py
systemctl --user list-timers toms-sync.timer
journalctl --user -u toms-sync.service
```

The timer runs at **08:00 and 20:00 Europe/London**, including daylight-saving
changes. It uses the repository's `.env` and virtual environment. The host must
be running and the user systemd manager available. `Persistent=true` runs one
catch-up import when the timer starts after a missed slot. For unattended use
after logout, enable lingering for the hosting user with `loginctl enable-linger`.
Install the timer on the actual host too if deploying to another machine.
To disable it: `systemctl --user disable --now toms-sync.timer`.

### Access over the local network

Set `FLASK_RUN_HOST=0.0.0.0` in `.env` and restart `flask run` or `python app.py`.
This listens on localhost and all network interfaces. Use the host's LAN IP and
port 5000 from another device. For a plain HTTP development session, run
`SESSION_COOKIE_SECURE=0 .venv/bin/python -m flask run`; secure cookies require
HTTPS for login over a LAN address. Keep secure cookies enabled with Tailscale
Serve HTTPS. An explicit `--host` or Gunicorn `--bind` overrides this setting.

## Confirming imported transactions

Opening the ledger shows a dismissible review popup when the selected account
view has unconfirmed transactions, including those on other pages. Review each
transaction and choose **Confirm details**, or edit its classification first.
Closing the popup or pressing Escape leaves everything unconfirmed; use
**Review transactions** to reopen it. The table separately labels owner review
as **Confirmed** or **Unconfirmed**, alongside the bank's payment status.

Existing transactions also begin unconfirmed when migration 008 is applied.
Confirmation is saved in PostgreSQL. Changes to important bank details, including
pending/settled status, and edits to classification or income details require
confirmation again. Identical syncs preserve it. Confirmation does not establish
tax treatment. If details change while the popup is open, it refreshes them and
requires another review before accepting confirmation.

## Entering income details

Classify an incoming transaction as **Income**, then select **Edit income details**
in its ledger row. Choose its income stream and tax treatment, and enter the source,
gross income and tax already deducted when known. GBP, EUR and USD amounts use
ordinary decimals (for example `30.00`); other currencies explicitly use minor units.
Leave unknown amounts blank. When known, gross minus tax plus any explained
adjustment must match the bank deposit. Saving returns to the same account and
ledger page and requires you to confirm the updated transaction again. If the
payment or saved income changed while the form was open, the stale edit is
rejected and the current values are shown for review.

Bank corrections flag saved income details for review. A currency change asks you
to re-enter amounts in the new currency rather than silently converting them.

## Income streams

Open **Income streams** in the navigation, or visit `/dashboard/income-streams`.
There are no default streams. Create one with a name and one of three types:
**Self employed**, **Employed** or **CIS**. Enter expected gross income before
deductions, choose **Weekly**, **Monthly** or **Yearly**, and select GBP, EUR or USD.
Then select the stream when editing an income payment. Tax treatment and any amounts already deducted are entered separately
for each payment; choosing CIS does not fill them in automatically.

Use **Manage** to update the name or income forecast, archive or restore a stream. Archiving preserves its
payments and prevents new assignments; existing assignments remain editable.
Create a new stream if its type changes. Older income records remain available
under All income with **No stream assigned** until you choose a stream for them.

Apply migration `009_income_streams.sql` with `.venv/bin/flask db-upgrade` when
updating another installation. It creates no default streams.

Weekly forecasts are multiplied by 52 and monthly forecasts by 12 to display a
full-year planning estimate, then reduced proportionally for **Unpaid holiday per year**. Choose **Days** or **Weeks**; partial values are supported. Days are working
days using a five-day week, with a maximum of 260 days or 52 weeks. For example,
£500 weekly with four unpaid weeks estimates £24,000 for the year. Paid holiday
does not reduce this figure. Enter zero if your income estimate already includes
unpaid time off, to avoid subtracting it twice. These forecasts do not create
payments or calculate tax and assume the same income rate throughout the year.
Existing streams have no forecast until you enter one. Migration
`010_income_stream_forecasts.sql` adds these fields; run `.venv/bin/flask db-upgrade`
on other installations after updating.

Migration `011_income_stream_unpaid_holiday.sql` adds unpaid holiday with a zero
baseline, preserving existing estimates. Apply it with `.venv/bin/flask db-upgrade`
on other installations. The holiday setting is editable under **Manage**.

Migration `012_income_stream_holiday_units.sql` preserves existing entries as
weeks and remembers the selected unit when editing. It retains enough precision
for partial days without changing the forecast through rounding during storage.

## UK tax rules and automatic official checks

Open **Tax rules** in the navigation. The first reviewed dataset covers England,
Wales and Northern Ireland for **2026–27 (6 April 2026 to 5 April 2027)**.
`data/tax_rules/uk-ewni-2026-27.json` stores the published bands, personal allowance
and taper, scope, source links and version. This is the data foundation; the annual
income-tax calculator and reserve recommendations are not implemented yet.

The app fetches GOV.UK's public content API with a separate unauthenticated client.
It checks the year, published bands and allowance wording against reviewed rules.
Matching values update verification metadata; different values, changed formatting
or a new year are flagged for review. The data file is never replaced by the fetch.
Timeouts retain reviewed rules and the last successful match. No bank credentials,
income records or personal information are sent to GOV.UK.

Checks run when visiting Tax rules, at most daily, or via **Check GOV.UK now**.
For a daily background check even when the site is closed:

```bash
.venv/bin/python deployment/install_tax_rules_timer.py
systemctl --user list-timers toms-tax-rules.timer
```

The personal Linux timer runs at 07:55 Europe/London with persistent catch-up.
It is enabled on the current development machine. Run a check manually with
`.venv/bin/flask refresh-tax-rules`. Cached check metadata is stored in
`instance/tax-rule-check.json`; it contains no banking data.

Official sources: [HMRC published rates](https://www.gov.uk/income-tax-rates) and
[rates after allowances](https://www.gov.uk/government/publications/rates-and-allowances-income-tax/income-tax-rates-and-allowances-current-and-past).

## Part-year income forecasts

Stream creation and **Manage** now include a **Tax year**, **Start date** and
**End date**. The first supported year is **2026–27**, from 6 April 2026 to
5 April 2027. Blank dates mean the boundaries of that tax year. Dates can extend
outside the year: only the overlap contributes to its forecast. Start and end
are inclusive, and an end before a start is rejected.

Enter normal weekly/monthly pay or a full-year salary. The forecast keeps the
52-week/12-month annual rate and prorates it by active calendar days divided by
the 365 days in this tax year. Unpaid holiday/absence is then subtracted for the
active dates, rather than prorated again. Absence cannot exceed the active period.
For example, £500 weekly from 6 October 2026 to 5 April 2027 with four unpaid
weeks estimates £10,964.38 for 2026–27. This assumes a steady income rate and is
an estimate, not a payroll calculation. Recorded payments are unchanged.

Migration `013_income_stream_tax_year_dates.sql` places existing forecasts in
2026–27 with blank dates, preserving their previous full-year estimates. Apply
it with `.venv/bin/flask db-upgrade` when updating another installation. Only
this supported year is selectable; retaining separate forecasts for multiple
years will be needed when support for another tax year is added.


## Separate database permissions

Normal web requests, bank sync and background timers use `toms_app` from `.env`.
It can select, insert, update and delete application records, but cannot alter
application tables, create schemas or read migration records. It has no
superuser, role-management, database-creation, replication or RLS-bypass powers.

For a new installation or an existing installation still using its administrator
login, first put that administrator's PostgreSQL connection details in `.env`,
then run these commands from the repository root:

```sh
.venv/bin/python deployment/restrict_database.py
.venv/bin/flask db-upgrade
```

The one-time provisioning script creates `toms_app` and `toms_migrator`, transfers
only the `toms` schema and its tables to the migrator, switches `.env` to the
restricted app login, and writes migration credentials to `.env.migrations`.
Both credential files are private (0600) and ignored by Git. The script refuses
to overwrite existing roles or an existing migration credentials file. It also
removes PUBLIC's ability to create objects in this database's `public` schema.
Run it only against the database dedicated to this application.

Restart the running Flask server after provisioning so existing connections are
replaced. Scheduled sync commands automatically use `.env` on their next run.
For subsequent updates, run `.venv/bin/flask db-upgrade`: only that maintenance
command reads `.env.migrations`, opens a separate connection and closes it
when finished. Missing migration credentials cause a clear error; the command
never falls back to the runtime login. The migrator is also not a superuser,
but owns the application schema and can create schemas in the app database.
Future tables created by that role inherit runtime DML grants; migration records
are explicitly kept private.

Keep `.env.migrations` out of a web-server deployment where migrations are run
on a separate maintenance host. On a single personal host, both logins belong
to the same OS user: PostgreSQL privileges contain SQL-level compromise, while
full code execution as that OS user could still read the maintenance file.

## Annual income-tax estimate

Open **Tax estimate** in the navigation, or `/dashboard/tax-estimate` after
signing in. This first estimate supports **England, Wales and Northern Ireland,
2026–27**, in GBP. It combines every stream's gross forecast for that year,
including archived streams, applies one personal allowance and the reviewed
20%, 40% and 45% bands, and handles the allowance reduction above £100,000.
Ending a job should use its end date: archiving it does not remove that year's
income. Forecasts already account for active dates and unpaid absence.

In **Income streams → Manage**, choose how each stream's tax is deducted:

- **No tax taken automatically**: its automatic deduction credit is zero.
- **Estimate standard PAYE**: employed streams share the employment-only annual
  tax calculation and one allowance. This is a planning assumption, not an
  implementation of payroll tax codes. When there are multiple jobs, the
  estimated deduction pool is allocated by gross income, preserving pennies.
- **Enter expected annual PAYE / CIS deductions**: enter tax already withheld
  plus expected deductions for the rest of the stream's active dates. Use only
  income-tax/PAYE or CIS deduction amounts, not NI, pensions or student loans.
  This is a whole-year amount in the stream's currency: it is not multiplied by
  weekly/monthly periods or reduced again for absence.
- **Not set yet**: the tax liability can be shown if gross income is complete,
  but the set-aside target remains unavailable until deductions are specified.

CIS credits are taken from your entered annual deduction forecast rather than
assuming every receipt has a 20% deduction. Gross payment status, contractor
verification, VAT and materials can change the deductions. This MVP does not
subtract expenses or materials from taxable gross earnings.

The annual set-aside target is **combined estimated income tax minus all
expected automatic deductions**, floored at zero. An excess is labelled as a
forecast, not a confirmed refund. A planning percentage spreads unpaid income
tax over the forecast self-employed/CIS receipts after automatic tax deductions;
it also covers any PAYE shortfall. No self-employed/CIS receipts means the page
shows the amount without inventing a percentage. This is a whole-year target,
not a remaining balance: money already saved or paid directly to HMRC is not
subtracted, and the rate is not recalculated over remaining months.

For example, £40,000 employment and £20,000 self-employment produce £11,432
estimated income tax. Standard PAYE is £5,486, leaving £5,946 to fund yourself:
29.73% of that self-employed forecast. If employment alone is £100,000, another
£10,000 adds £6,000 income tax because the personal allowance also falls.

Missing gross forecasts and non-GBP streams block partial totals; foreign
currencies need a separately reviewed conversion policy. Bank payments are not
added again to stream forecasts. Existing recorded payment deductions and
transaction confirmations remain independent of these planning fields.

**Scope:** income tax only. Excludes National Insurance, expenses/write-offs,
trading allowance, pension/other reliefs, savings/dividends, student loans,
payments on account and tax already paid directly. The target is therefore not
the complete Self Assessment bill. The page shows the source-check status and
links to the reviewed rules.

Migration `014_income_stream_withholding.sql` adds the forecast deduction fields.
Existing streams start as **Not set yet**, rather than guessing their deductions.
Run `.venv/bin/flask db-upgrade` and restart Flask when updating another
installation.

Sources: [HMRC rates and personal allowance](https://www.gov.uk/income-tax-rates),
[rates after allowances](https://www.gov.uk/government/publications/rates-and-allowances-income-tax/income-tax-rates-and-allowances-current-and-past),
[PAYE](https://www.gov.uk/income-tax/how-you-pay-income-tax) and
[CIS deduction statements](https://www.gov.uk/what-you-must-do-as-a-cis-subcontractor/get-paid).
