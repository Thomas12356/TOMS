# Bringing another bank into TOMS

Proposed next feature, 12 October 2026. This is a design, not implemented import
support. It follows the user's request to support records held before moving
money into Starling. It does not require the user to move bank first.
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

## Implement in this order

**1. Account origins and coverage.** Add an explicit account provider/origin
(`starling` or `statement`), bank label and current-cash inclusion/closure state
with a migration. Existing accounts are Starling records in the current system;
preserve their IDs and importer ownership. Add owner-only manual account setup
and balance entry without Starling credentials. Keep historical income queries
independent of whether an account is included in today's cash view. Track tax-year
history coverage, including known missing pre-TOMS records; incomplete evidence
must not quietly produce a complete tax target or available-cash figure.

**2. CSV preview and controlled import.** Reuse Transaction/classification/income
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

## Existing code to adapt

- Account currently has no provider discriminator; the balance endpoint in
  `routes/dashboard.py` attempts Starling balance requests for every saved account.
  Filter provider-specific requests before adding statement accounts.
- Starling sync discovers accounts from Starling's own account list in
  `services/transactions/sync.py`. Preserve that ownership; manual accounts must
  not acquire Starling checkpoints or be changed by sync.
- `services/transactions/classification.py` already supports internal transfers,
  but a cross-bank move may look like an ordinary incoming faster payment. Make
  transfer review visible at cutover so it does not default into new income.
- Cash planning currently requires observations for all saved accounts. Add an
  explicit current-cash account selection with coverage invalidation; retain all
  accounts in historical records and exports.
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

The useful next delivery is stages 1–2, followed by cutover review. Current bills,
HMRC charges and cash observations cover commitments and money held, but they do
not import another bank's history yet.
