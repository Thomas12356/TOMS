# Bringing another bank into TOMS

Stages 1–2 implemented on `codex/other-bank-csv-onboarding`, 12 October 2026.
Owner setup and mapped CSV previews now bring in records held before moving
money into Starling. The user does not need to move bank first.
The user selected **any bank through CSV statements**. Column mapping is the
first import interface; bank-specific presets are optional later conveniences.

## User journey

1. Add the existing account by name, bank and currency. Choose statement/manual
   records rather than a Starling connection. Register a Starling account later,
   alongside it. Neither setup choice transfers money.
2. Import CSV statements through a preview: choose the account, date/amount/
   direction/reference columns and currency; check totals and rejected or possible
   duplicate rows before confirming. Provide manual entry for missing records.
3. Bring in the supported tax year's earlier income and deductions, using
   payslips/CIS evidence for gross and withholding. A net bank receipt cannot
   establish those amounts. Bring outstanding invoices, HMRC charges and bills
   separately through their existing workflows. Do not add work/invoice totals
   again as received income.
4. Record the old account's current available balance for cash planning. This is
   an observation of existing money, not income or a taxable opening transaction.
   Confirm history coverage independently of the current cash balance.
5. When money is moved, review both sides as transfers between owned accounts.
   Preserve their bank records but exclude them from income/expense tax totals.
   Retain old-account history after closing it; separately remove a confirmed
   closed account from current-cash coverage once its remaining funds are dealt
   with. Do not delete history or require daily observations of a closed account.

For example, £5,000 already held at the old bank remains £5,000 across accounts
when moved into Starling. The move creates no additional earnings. Any earnings
received before switching still need their own records and tax evidence.

## Delivery order

**1. Delivered: account origins and coverage.** Add an explicit account provider/origin
(`starling` or `statement`), bank label and current-cash inclusion/closure state
with a migration. Existing accounts are Starling records in the current system;
preserve their IDs and importer ownership. Add owner-only manual account setup
and balance entry without Starling credentials. Keep historical income queries
independent of whether an account is included in today's cash view. Track tax-year
history coverage, including known missing pre-TOMS records; incomplete evidence
must not quietly produce a complete tax target or available-cash figure.

**2. Delivered: CSV preview and controlled import.** Reuse Transaction/classification/income
workflows through ORM/DataSession, with import batch provenance and stable row
identity independent of Starling feed IDs. Require account/currency and column
mapping; preserve signed integer money, date-only London calendar semantics and
unknown values. Bound upload size, row count and preview retention. Escape bank
text and CSV exports. Keep source files private and out of Git, logs and evidence
exports. Mark imported rows for review rather than assuming every incoming row
is earnings. Retain provenance without exposing complete source files.

Exact repeated files/rows must be idempotent. Overlapping statements need explicit
duplicate review: date/amount/reference equality alone cannot distinguish two
legitimate identical payments. Do not overwrite confirmed records or silently
discard possible duplicates. Confirmed imports must be atomic or have a precise,
recoverable batch state. Corrections must preserve audit/history and protect
existing invoice allocations and deductions.

**3. Cutover and transfer review.** Suggest possible counterpart transfers using
owned accounts, currency, direction, amount and dates. Require confirmation;
equal amounts alone are insufficient. Show the imported period and remaining
history gaps, then let Starling continue its own account's feed. Avoid treating
overlapping historical imports, aggregate opening earnings and individual
receipts as separate additional income. Do not infer tax credits from balances.

**4. Bank-specific presets after a real sample format.** Start with column mapping
for ordinary CSVs, then add tested bank presets. A provider/API connection can
follow later when its permissions and data contract are established. Do not
promise universal CSV compatibility or an unspecified bank API integration.

## Implemented boundaries

- Account has an explicit provider; the balance endpoint in `routes/dashboard.py`
  excludes statement accounts from live and synthetic Starling balance responses.
  The selected statement ledger has no automatic bank balance or sync controls.
- Starling sync discovers accounts from Starling's own account list in
  `services/transactions/sync.py`. Preserve that ownership; manual accounts must
  not acquire Starling checkpoints or be changed by sync.
- `services/transactions/classification.py` already supports internal transfers,
  but a cross-bank move may look like an ordinary incoming faster payment. Make
  transfer review visible at cutover so it does not default into new income.
- Cash planning requires observations only for open/current-cash accounts.
  Closure requires a fresh zero observation and cleared earmarks; reopening is
  available for corrections. History and evidence exports retain closed accounts.
- Existing tax exports and activity logs have deliberate field allowlists.
  Extend them deliberately for import provenance; no raw statement/auth payloads.

## Acceptance checks

- A user can set up and enter old-bank records before obtaining a Starling token.
- Statement accounts never generate Starling requests, including balance refresh.
- Old-bank receipts and deductions in 2026–27 affect planning exactly once after
  review; unsupported years stay outside the current calculation scope.
- Opening cash and a move to Starling generate no additional earnings or deductions.
- Exact reimports are harmless; overlapping and legitimately identical rows remain
  distinguishable, with explicit duplicate decisions.
- Incomplete prior income or withholding remains visible and blocks complete
  figures; a current balance does not establish historical evidence.
- Closing the old account preserves history and issued/payment snapshots while
  removing only its future cash-observation obligation.
- Owner authentication, CSRF, stale versions, sample isolation, bounded uploads,
  currency/date validation and private exports pass backend and browser checks.

Next is stage 3: suggested counterpart transfers and a focused cutover view.
Existing transfer classification already works across imported accounts; matching
suggestions and bank-specific presets remain future work.

## Supported files and retention

512 KiB / 1,000 rows / 30 columns; a non-empty header and equal-width rows are
required. UTF-8/BOM or Windows-1252; comma, semicolon or tab separators. Supported
date-only formats are ISO, day/month/year, month/day/year, day-month-year and
day.month.year. Choose signed amounts or separate credit/debit columns and an
explicit decimal separator; no currency symbols, FX conversion or inferred gross
pay/withholding. GBP/EUR/USD use two decimal places; other currencies use whole
minor units. Dates must be from 1900 through today and within a known lifetime.

Preview staging expires after 30 minutes, with at most 20 active uploads. Raw
headers/cells are kept privately only before successful mapping; mapping replaces
them with allowlisted normalized payment fields. Discard clears staged content
immediately; the next upload removes expired, unimported batches. Backups include
private staging. Evidence exports contain accounts, coverage and imported batch/
row provenance, excluding source grids, mappings, hashes and filenames. Unfinished
previews are resumable/discardable from the account. A valid mapped preview must
be discarded/reuploaded to change mapping, since original columns were removed.

Exact same-file/same-mapping imports are idempotent per account. Overlapping or
repeated rows require explicit decisions; identical legitimate payments can be
kept. Manual entry uses the same preview with a per-entry identity, preserving
repeat submission safety without preventing distinct identical payments.
