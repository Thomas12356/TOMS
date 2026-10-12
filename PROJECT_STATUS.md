# TOMS project status

Updated: 2026-10-12. This is a handoff snapshot, not a live system report.
Stable working instructions are in [AGENTS.md](AGENTS.md).

## Latest delivery — reusable imports and guided statement review

Implemented on `codex/statement-review-and-transfer-matching`, following approval
of transfer matching, import corrections and saved CSV mappings.

An exact account/header-order/encoding/separator match can reuse a format saved
after import confirmation, proceeding to the mandatory validated duplicate preview.
Header suggestions are visible defaults only; ambiguous headers and date formats
are never inferred into saved records. Formats can be cleared in account settings.
Failed whole-file validation returns to mapping without partial transactions.

Own-account transfer suggestions require opposite settled payments with the same
nonzero amount/currency within three days. Confirmation locks and versions both
records, applies transfer classification and reviews them together without creating
income. Already confirmed records, incompatible manual classifications and any
income/expense/invoice links are excluded. Ambiguous pairs stay separate suggestions;
split, FX and longer-delay transfers use existing review. No bank calls occur.

Saved imports now have paginated correction screens (50 rows). Original normalized
rows and provenance stay intact. Edits, exclusions, restorations and atomic selected
exclusions retain explicit reasons and before/after snapshots. Excluded records use
REVERSED status and leave settled totals; editing excluded details does not restore
them implicitly. Linked income/expenses and all invoice allocations, including
inactive history, block direct correction. Changed/restored settled payments need
review again; explicitly excluded records stay reviewed and do not reappear on Home
or prevent closure solely because of their mistaken dates. All corrections invalidate
history readiness. Editing an excluded record never implicitly restores it.
New evidence exports deliberately include allowlisted correction history, excluding
raw statements, format signatures, mappings and authentication data.

Account pages show a concrete next task. Repeat mapping and two separate transfer
reviews are removed from common journeys; explicit financial confirmations remain.
Existing navigation gains no top-level item. Bulk corrections validate every selected
record before mutation, use deterministic transaction locks and reject stale pages.
Migration 032 adds mappings/audit tables while preserving edited sample records.
A private pre-change backup was taken; restore verification remains excluded.

Final backend verification: 641 tests / 637 passes / four optional restore skips.
All 26 browser scripts passed; both statement scripts were rerun after the final
exclusion/closure/read-model refinements at 320/390/768/1280px, including JavaScript
disabled at 390px. The mobile correction history screenshot was inspected.
Application and changed-test Ruff F, application Bandit, whitespace and outgoing
secret-value checks passed. Migration rerun reports the database up to date.
No live Starling traffic, credential setup or database restore was performed.
Implementation `5218e977138cb1eaeca11557e867b85af8833fdb` was committed and pushed
to `codex/statement-review-and-transfer-matching`; the remote revision matched.
GitHub owner `Thomas12356`, SSH remote and public visibility matched the user's
publication authorization. Main was not changed.

Next: the user's real-statement/five-task walkthrough. Remaining format differences,
split/FX transfers and linked-evidence correction effort should guide subsequent work;
no universal CSV compatibility or complete live-bank verification is claimed.

## Previous delivery — other-bank accounts and CSV onboarding

Implemented on `codex/other-bank-csv-onboarding` from `e80f592`, following the
user's approval of stages 1–2 in `BANK_MIGRATION_PLAN.md`. Owner-only Accounts
and statements is linked from Home, Account money and cash planning without a
new top-level navigation item. Accounts have explicit Starling/statement origins,
bank names, known opening dates, currency and current-cash inclusion. Statement
accounts never enter Starling balance requests; their selected ledger has no
automatic provider balance/sync controls. Sync rejects an account-origin collision.

Bounded CSV uploads use explicit columns/date/amount/decimal formats, whole-file
validation, totals and explicit duplicate choices. Exact same-file/same-mapping
reimports and replayed confirmations are harmless. Overlapping/repeated rows can
be skipped or kept as distinct payments. Confirmation is atomic, with account and
ledger stale checks. Rows start unreviewed as Other and reuse existing guided
classification, income/withholding and deduction workflows. Missing individual
transactions use the same preview, with independent entry identity for legitimate
identical payments. Unfinished uploads can be resumed or discarded from an account.

Opening cash remains a balance observation, never a receipt. Separate supported-
year history coverage must include the required account lifetime and reviewed
settled transactions; incompleteness blocks annual/received-income planning.
Closed accounts retain tax/history/export evidence but leave current cash. Closure
requires a fresh zero observation, cleared earmarks and no later transactions;
accidental closure can be corrected by reopening. Evidence exports explicitly
allowlist account/history/confirmed batch and row provenance; raw CSV grids,
mapping, hashes, filenames and authentication remain excluded.

Migration 030 adds this data model without resetting edited sample records.
Review also exposed reliance on wall-clock ordering for post-payment cash checks.
Migration 031 records the payment-state fingerprint with balance observations:
recording or voiding a payment requires a new observation even after a backward
clock adjustment. Legacy observations with payments need a fresh check. A private
pre-change backup was taken before both additive migrations were applied through
the maintenance login. No working financial amounts were changed or restored.

The unmocked walkthrough also caught cash planning requesting `tax_year` from a
planning result that did not provide it. `current_plan` now supplies the reviewed
rules' year explicitly. A real-calculator cash-page regression and the browser
journey protect this boundary; older stubbed cash checks alone had missed it.

Backend verification: 630 tests, 626 passed / four optional restore skips. New
cases cover exact money/date/currency validation, replay, overlap decisions,
whole-import rollback on failure, stale records, history versus cash, transfers,
closure, owner/API/CSRF/sample isolation, private provenance exports, Windows
encoding, discarded/expired staging and manual identical-entry identity. Cash
checks include a correction whose timestamp precedes its balance observation.
All 25 browser scripts passed in one final sequential run, including the new
account/import/transfer/history/balance/closure/reopen journey at 320/390/768/1280px
with JavaScript disabled at 390px. The mobile screenshot was inspected. Application
and changed-test Ruff F, application Bandit, whitespace and outgoing secret-value
checks passed. Migration rerun reports the database up to date. Earlier run
failures and the stubbed-calculator gap are recorded in `SECURITY_TESTS.md`.
No live bank verification, credential setup or backup restore was performed.
Implementation `0916da648794d4478d1a2a8e9a28cc4926e82041` was committed and pushed
to `codex/other-bank-csv-onboarding`; the remote revision matched. GitHub owner,
SSH remote and public visibility matched the user's publication authorization.

Next: real-format/user walkthrough feedback, then explicit cross-account transfer
suggestions and a focused cutover view. Mapping presets and safer bulk correction
tools remain future work. Current imports retain committed evidence; existing
classification/income/deduction correction workflows remain available. No universal
CSV compatibility, bank-specific API support or live Starling verification is claimed.

## Previous delivery — reliable forms and simpler cash review

Work on `codex/reliable-forms-cash-workflow` follows the user's request for
reliability and reduced cash-planning effort. Diagnostics reproduced an invoice
form and background activity rejection when newly signed CSRF tokens were about
0.3 seconds ahead of the current wall clock. The library rejects negative ages
as expired. This establishes a clock-related failure, not proof that every older
intermittent assertion had the same cause.

`services/web/signing.py` tolerates at most two seconds ahead for verified session,
CSRF and matching-preview signatures. Past-age limits and farther-future rejection
remain enforced; formats are compatible with existing signatures. CSRF session
matching/HTTPS referrer checks, owner login, server-side session revocation,
inactivity/absolute expiry and stale business versions remain. Independent tests
reproduce the original negative-age rejection and check the tolerance boundary,
expiry, tampering, missing/wrong-session tokens and HTTPS referrers. A browser test
deliberately regresses the signing clock before dataset switching and saving.

Cash planning names accounts requiring updated observations and combines balance
updates/coverage into one atomic save. Blank values retain the original observation
and age; zero is a real entered balance. Opened-record fingerprints and revisions
guard changes, errors roll back the whole review and retain entries. Coverage is
optional when saving balances, never silently assumed. Unknown/stale/incomplete
figures still remain unavailable. Payment history now links directly to finishing
the cash review and reviewing manual earmarks. Tax/money calculation rules and
Starling traffic are unchanged; no migration or new credential setup is required.

The user chose bank-independent CSV statements for future other-bank onboarding.
[BANK_MIGRATION_PLAN.md](BANK_MIGRATION_PLAN.md) specifies account origins/history
coverage, CSV mapping/preview/deduplication, then transfer review at Starling
cutover. Preserve prior-bank tax-year evidence, record opening cash separately
and exclude transfers between owned accounts from new earnings. No other-bank
account/import support is claimed as delivered in this change.

Backend verification: 613 tests, 609 passed / four optional backup restore skips.
The updated cash browser workflow passed four widths, including no JavaScript at
390px; the deterministic clock-regression journey passed 320/390/1280px.
All 24 browser scripts passed in one complete verification run. Income workspace,
invoices, overview/dataset switching, actual login/password and usability/work
saves then passed two additional complete rounds (42 width-specific passes).
The final backend rerun also passed. Application and changed-test Ruff F,
application Bandit and whitespace checks passed. No live bank verification,
working financial edits or backup restore checks were performed.

Published implementation `071dabf` on `codex/reliable-forms-cash-workflow` after
verifying the public Thomas12356/TOMS remote/owner/branch and scanning outgoing
files/history for secret values.
Main remains at `577ac03`. The handoff documentation commit follows the implementation.

## Latest delivery — actual HMRC charges, bills and cash planning

Implemented on `codex/tax-bills-cash-planning`, following guided receipt matching.
Planning now has one Bills and cash planning page for manual statement charges,
one-off/weekly/monthly/yearly bills, dated available balance observations and a
30-day cash view. Overdue occurrences remain included; month-end and leap-year
anchors are preserved. Payments consume an occurrence's actual outstanding
budget; corrections void records instead of deleting history. Revision checks,
idempotent creation/payment IDs, owner login and CSRF protect writes.

Actual statement charges retain explicit tax year/component and due date. Start
from the remaining statement charge after existing credits, enter each once,
and record only subsequent direct payments. Mixed/unknown charges use Other;
balancing/on-account entries require confirmation of income-tax/Class 4 scope.
Only an explicitly entered supported 2026–27 portion of a direct payment reduces
the cash provision. Blank claims no credit. This never changes the tax estimate,
deductible expenses, logged withholding or bank records and sends no payment.

Cash uses observed available balances, unpaid bills and protected tax provision.
The current-year provision is the larger of the received-income target after
explicit direct credits and current-year supported HMRC charges due; other-year
and Other charges due are added separately. Protect at least the saved aggregate
tax/NI earmarks. This avoids overlapping provisions and preserves distinct-year
liabilities. Future income and unused overdraft facilities are excluded. Negative
results remain shortfalls. Unknown due amounts, missing/stale/future or changed-
currency observations, unsupported/incomplete tax targets, payment corrections
without updated balances, or unconfirmed coverage withhold the figure. Coverage
is tied to the actual records and day, and observations expire after 24 hours.

Migrations 027–029 add independent business tables/fields in real/sample schemas
without rebuilding sample edits. A private pre-migration database backup was
created; restore verification remains excluded. Existing applied SQL was not
edited. New records are included by backup table discovery and have a separate
snapshot-consistent private CSV ZIP export with explicit field allowlists,
currency labels and spreadsheet formula protection. Raw bank/auth data is excluded.

Final backend: 603 tests, 599 passed / four optional backup restore skips. The
new browser flow passed 320/390/768/1280px, including no JavaScript at 390px.
All 23 browser scripts completed successfully across focused runs, including
responsive layouts, actual login/password revocation, invoices/PDFs, navigation,
review, tax/export and cash planning. Ruff F (application and browser tests),
application Bandit and whitespace checks passed. The first full browser run
stopped at an intermittent expense assertion; later runs also exposed intermittent
invoice, dataset-switch, login/password and work-save assertions. Final reruns
passed. Their underlying cause is not established; successful reruns do not prove
they are permanently resolved. Synthetic failure captures and rejected-response
checks were retained for diagnosis. No working financial records were changed.
Browser fixture cookies now use the browser's real Flask-Login identifier rather
than an incomplete copied session; authentication is not weakened in production.
An independent regression covers identifier binding and background cookie writes.

The statement/component guidance was checked against GOV.UK's
[statement explanation](https://www.gov.uk/understand-self-assessment-bill/self-assessment-statement)
and [payments-on-account guidance](https://www.gov.uk/understand-self-assessment-bill/payments-on-account)
on 12 October 2026. TOMS records supplied charges rather than recalculating an
actual HMRC statement or inferring how an unallocated payment was credited.
Published implementation `6c4776c` to `codex/tax-bills-cash-planning` after
verifying the public Thomas12356/TOMS remote, owner and branch and scanning
outgoing content/history for secret values.
Main remains at `577ac03`; the implementation is on its named branch.

Limits: balances/statement charges/payments are manually entered, not verified
against live providers. Cash is a 30-day record-based figure, not a funding
guarantee; include everyday spending allowances and review earmarks after paying
tax. Figures in unsupported currencies remain unavailable. No tax-return
submission, bank transfer, tax-rate expansion or excluded relief calculation was
added. Owner walkthrough and optional restore checks remain outstanding.

## Latest delivery — guided receipt matching

Implemented on `codex/guided-receipt-matching`. Available receipts now lead to
one payment screen with explained same-source/currency invoice suggestions,
reference/amount/date evidence, invoice search and compact expandable choices.
The owner enters actual cash/deductions, previews the resulting outstanding
balance and confirms on the same screen. A partial match leaves separate cash,
tax and NI budgets available for the next invoice. No-invoice and decide-later
paths leave the receipt unmatched without recording an invoice payment/status.

Preview is read-only. Confirmation requires a signed 15-minute preview tied to
the payment, invoice revision and real/sample mode; it locks records and reuses
the established allocation validation. Changed receipts/invoices, stale or
excessive allocations, wrong source/currency, inconsistent gross/deductions,
tampered/expired previews and repeat confirmation are rejected. No tax rules,
income totals, issued snapshots, bank traffic or database schema are changed.

Validation: 583 backend tests, 579 passed / four optional backup restore skips.
Matching, invoices, Home tasks and reliability browser scripts passed all tested
widths; matching also passed with JavaScript disabled at 390px. Ruff F, Bandit
and whitespace checks passed. Earlier browser runs had intermittent assertions
in invoice estimates, Home navigation and matching confirmation; final reruns
passed, with sample-only diagnostic capture added. Their cause is not established.
Published implementation `c3b76ff` on `codex/guided-receipt-matching` after
verifying the public Thomas12356/TOMS remote and scanning outgoing content/history
for secrets.
The owner walkthrough remains outstanding.

## Latest delivery — bounded financial evidence loading

Completed requested steps 2–5 on `codex/bounded-financial-reads`: Home loading,
incremental tax exports, reconciliation loading/shorter invoice choices, and
review of the linear completed branch history for merging into main. See
[PERFORMANCE_REVIEW.md](PERFORMANCE_REVIEW.md) for the final baseline comparison.

Home shares compact streamed payment evidence and retains 20 priority invoice
actions with complete counts/totals. Reconciliation retains 50 details and five
recent invoice choices per receipt, with complete summaries and an all-invoices
link. Exports use anonymous private temporary files and incremental JSON/CSV/ZIP
generation; every failure and completed response closes files. Financial rules,
cross-year evidence, stale versions, snapshots and currency separation remain.

Validation: 574 backend tests, 570 passes / four optional backup restore skips;
all 22 browser scripts; Ruff F, Bandit and git whitespace checks passed. Final
10k/50k synthetic benchmark passed without working financial writes or bank
requests. No credentials, schema migration or backup restore was performed.
Published implementation `3630fde` to `codex/bounded-financial-reads` and
fast-forwarded/pushed `main`, incorporating the completed calculation audit, tax
profile/payment guidance, failure recovery review and performance review history.
Repository owner/remote and public visibility were verified against the user's
publication preference; outgoing content/history passed the secret-value scan.

Outstanding: owner five-task walkthrough and optional backup restore verification
with the previously excluded credential setup. Ledger ordering/index work should
be evaluated separately using measured plans. Full summary calculations still
read relevant evidence; this delivery bounds detail objects and archive memory.

## Previous review — performance as history grows

Measured on `codex/performance-review`; see [PERFORMANCE_REVIEW.md](PERFORMANCE_REVIEW.md)
and the opt-in `tests/performance_review.py` harness. Application behaviour is
unchanged. Disposable schemas contained 10k/50k synthetic transactions, 1k/5k
work records and 250/1,250 invoices with partial allocations and sending history.
Bank and background rule/import requests were blocked. Only index metadata was
read from the working schema; working financial data was not benchmarked.

At 50k records, warm median response generation was 49–161 ms for ledger variants,
2.0 seconds for Home, 2.9 seconds for matching and 5.4 seconds for the tax ZIP.
Export Python allocation peak was 239.6 MiB. Ledger query counts stayed at seven;
the other paths materialise complete evidence and use batched relationship loads.
Read-only query plans showed scans and disk sorts on selected large queries.

Prioritise bounded Home details with complete shared summaries, then incremental
export generation, then matching detail/candidate loading. Evaluate a global
ledger ordering index afterward. These remain proposals; no schema, calculation,
live deployment or credential change was made. Full snapshot consistency,
stale evidence, all-year allocations and currency separation must be retained.

Validation: both large benchmark datasets passed, followed by a 100-record final
harness smoke run checking schema removal. Full backend: 568 tests, 564 passes /
four optional backup integration skips. Ruff F, application/harness Bandit and
whitespace checks passed. No fixture real/demo schemas remained. Browser reruns
were unnecessary because this delivery changes only benchmark tooling and docs.
Owner walkthrough and excluded
backup restore verification remain outstanding.

## Latest review — failure recovery and evidence consistency

Reviewed on `codex/failure-recovery-review`: transaction import orchestration and
page commits, duplicate/stale owner submissions, invoice receipt budgets,
reconciliation and tax evidence snapshots. No product defect was reproduced in
the reviewed paths; this delivery adds regression evidence rather than changing
application behaviour.

Three additional checks prove abandoned running imports are recovered without
duplicate transactions, later failed pages preserve earlier saved pages without
advancing checkpoints, and a concurrent edit during export cannot mix new
evidence with old calculations. The export test uses a separate committed writer
in disposable schemas; import fixtures retain their rollback-only boundary.
Existing checks cover simultaneous confirmations/sending records, stale invoice
actions, cross-year allocation budgets, changed issued work, currencies and
display limits. Bank traffic remains mocked.

Full backend verification: 568 tests, 564 passes / four optional backup integration
skips. Reliability, reconciliation, tax-export and invoice browser checks passed
at four widths; Ruff F, application Bandit and whitespace checks passed.
Credential provisioning and restore testing were excluded at the user's
request. The interrupted provisioning attempt created no credential file or test
server. The existing migration login's privileges remain unchanged. The live app
continues to run implementation `b59f01b`; no deployment is needed for tests/docs.

The five-task owner walkthrough remains the next usability input. Equal work and
receipt totals still require individual matching evidence, allocation is explicit,
and tax exports retain their documented issue-date selection for invoices.

## Latest delivery — tax jurisdiction and HMRC payment guidance

Implemented on `codex/tax-jurisdiction-payment-guidance`:

- Owner-confirmed 2026–27 tax profile, independent of journey locations. England,
  Wales and Northern Ireland enable supported estimates; missing/unconfirmed,
  Scottish and other profiles withhold calculated totals and reserve targets.
- Owner-only, CSRF-protected saves, fresh confirmation, revision checks and safe
  stale-form recovery. First inserts and sample seeding share a dataset-specific
  lock. Real and sample settings remain separate; only initial synthetic sample
  data assumes England. The real owner's jurisdiction has not been inferred.
- Scotland journeys retain UK mileage relief with a supported profile. The same
  profile gate applies to estimates, account targets, notices, Home/checklist and
  evidence exports. Exports preserve the profile summary in the financial snapshot.
- Consistent payment guidance beside tax/account targets and in exported reports:
  payments on account are excluded and direct HMRC payments are not subtracted.
  An official statement link explains how to check actual amounts/dates due.
  Actual payment tracking remains outside this release.
- Migration 026 adds the profile table. A private pre-migration backup was made;
  fingerprints verified existing real/sample data unchanged. This additive upgrade
  preserves sample edits. No live Starling calls were performed.

Validation: 565 backend tests, 561 passes / four optional backup integration skips;
all 22 browser scripts passed, including the new five-width workflow (390px without
JavaScript). Nine new backend regressions cover scope, preserved numbers, Scottish
travel, initial/update stale saves, auth/CSRF, safe errors, migration defaults and
sample isolation. Ruff F, Bandit and whitespace checks passed. Optional backup
restore integration remains unrun because it needs separate CREATEDB credentials.

Published implementation `b59f01bfa9dd9d481d463c2a561a6ec0dadf735c` to the authorised
public `Thomas12356/TOMS` repository on `codex/tax-jurisdiction-payment-guidance`;
main remains unchanged. Outgoing content/history passed the secret scan. The owner must confirm the real tax profile once before estimates resume.

The owner walkthrough is still pending; its sample-profile guidance has been
updated. Further navigation changes, presets and matching automation await those
observations. Earlier audit/delivery sections below are historical snapshots;
the tax-jurisdiction issue described in the audit is resolved by this release.

## Latest audit — calculations, legislation and remaining friction

The requested audit is recorded in [CALCULATION_AUDIT.md](CALCULATION_AUDIT.md)
on `codex/tax-calculation-legislation-audit`. Current HMRC income-tax, NI and both
mileage publications passed the application validators in a temporary cache.
Primary legislation confirms 2026–27 main earnings rates and the 55p mileage
increase. Independent comparisons matched 20,802 income-tax inputs and 20,802
Class 4 profit inputs; three new regression tests retain these comparisons and
the official 11,000-mile example. No tested supported arithmetic discrepancy was
found; this is not a complete tax-return or personal eligibility certification.

Priorities informed by this audit: confirm the supported owner tax jurisdiction
separately from journey location; record actual HMRC payment schedules/payments;
then choose applicable excluded tax features from a confirmed owner profile.
The current Scotland-journey blocker is conservative but cannot establish tax
residence. It remains pending a versioned setting/migration design; do not change
a real journey location to bypass it. Payments on account, trading allowance,
personal reliefs and individual NI adjustments remain outside the current model.
The five-task owner walkthrough still needs actual owner observations before
broader matching automation or named preset management is prioritised.

Validation: final full backend 556 tests, 552 passes / four optional backup
integration skips; all 21 browser scripts passed; Ruff F, Bandit and whitespace
checks passed. Bank calls were mocked; no live verification, working financial
write, migration or deployment change. The prior live implementation remains
`dda2744`. Publication is to the previously authorised public Thomas12356/TOMS
repository on the named audit branch; main is unchanged by this audit.

## Latest delivery — focused system usability

Review-informed stages 1–3 are implemented on `codex/focused-system-usability`:

- Compact income notices retain their detail/acknowledgement and failure recovery;
  sample identity stays visible, with secondary explanation in a disclosure.
- Consistent Income sources / Work and payment matching labels and distinct
  payment-description fields. The short source-creation view hides competing
  management/list actions while preserving the full configuration path.
- Grouped mileage entry and separate disclosed history. Validated internal source
  context survives work/journey reuse, invalid input and successful saves, while
  the filtered-ledger expense return remains supported.
- Source/date history filters and 20-record pages. Work totals and mileage relief
  continue to use full evidence. The latest 200 shift choices include older
  referenced links; invoice badges load only for displayed work records.
- Guided ledger review is prominent; direct editing and quick confirmation remain
  under disclosures. Planning puts its next action and received-income target
  first, with calculation/source details disclosed. Matching shows unresolved
  groups before source totals; clear groups start collapsed.

Validation: 553 backend tests, 549 passes / four optional backup integration skips;
all 21 browser scripts passed. Nine new backend regressions and a five-width
browser journey cover the new behaviour, including JavaScript-disabled entry.
Ruff F, Bandit, whitespace and outgoing secret checks passed. All 28 expert audit
page visits passed without overflow or browser errors. No migration or live bank
verification was performed. Complete mileage evidence still requires a full scan.

The owner-observation step is prepared in USABILITY_WALKTHROUGH.md and remains
pending actual use. Later presets, automation, budgeting, stored balances and
recovery features are still separately scoped. Implementation `dda2744` is pushed
to the named branch and main and deployed locally; details are recorded below.

## Latest delivery — account money, defaults and suggestions

Implemented on `codex/account-money-work-defaults-suggestions`:
- Dedicated Account money page: saved earmarks, received-income tax/NI target and
  gap, separate currencies, explicit live balance refresh and response freshness.
  Opening the page makes no bank request. A failed refresh clears stale cash.
- Reuse saved work rates/breaks and journey descriptions, with fresh dates,
  mileage, eligibility and reimbursement. Existing work/history stays unchanged;
  these are record templates, not a configurable preset manager.
- Conservative classification/source suggestions from consistent confirmed
  history. Explicit preview changes only the form; the owner must save through
  existing validation. Amounts and tax treatment are not copied.

Verification: 544 backend tests, 540 passes and four optional backup skips;
all 20 browser scripts passed, including the new five-width workflow journey
and JavaScript-disabled entry. Final focused regressions, Ruff F, Bandit,
whitespace and outgoing secret-value checks passed. No migration or live bank
verification was needed.

Implementation `aeedef95d0353263545d77ff21577c8677305f75` was pushed to the named
branch and fast-forwarded to public main.

The post-publication [whole-system usability review](USABILITY_REVIEW.md) checked
14 pages at mobile/desktop widths, with all 28 visits returning 200, no overflow
or browser errors, and inspected synthetic screenshots. Remaining priorities:
reduce repeated attention, standardize visible source/matching terminology,
focus source creation, shorten mileage entry with bounded history and preserve
source return context, then put unresolved planning/matching actions first.
This is an expert review, not measured user research. No working financial data
changed. Future implementation should start on a new appropriately named branch.

Next-step decision: the user asked to use the whole-system review to guide delivery.
IMPLEMENTATION_PLAN.md now specifies scope, named branches and acceptance checks:
focus attention/source setup, simplify daily entry/history, then prioritize
unresolved review/planning/matching actions. Owner observation follows before
new feature expansion. This update changes planning only; no new interface or
financial behaviour has been implemented or deployed by it.

Latest usability work: [USABILITY_ROADMAP.md](USABILITY_ROADMAP.md) incorporates
the user's supplied review and complaint about crowded navigation. Compact primary
navigation and guided-review deployment are the first delivery. A unified task
home and staged income workspaces are implemented. Account money, reusable
saved-record defaults and explicit payment suggestions are implemented. Budgeting/AI remain later
scoped milestones. See the latest handoff
below for deployment revision and verification evidence.

Latest delivery: `codex/focused-system-usability` reduces distraction, adds bounded
entry history and prioritizes actionable review/planning/matching evidence.

## Implemented

- Income streams, regular forecasts, shifts/overtime, linked expenses and mileage.
- Income-tax/Class 4 NI planning, received-income reserve targets, earmarks and
  income notices. Scope remains EWNI 2026–27, with warnings for unsupported cases.
- Invoice drafts/issue/cancellation, frozen client documents and browser PDF
  printing, manual sending history, actual receipt allocations and stale checks.
- Guided checklist derived from saved records.
- Owner tax-year ZIP export: readable report, exact JSON calculation/rules and
  CSV evidence, with separate sample mode and consistent database snapshots.
- Work/payment reconciliation: differences for individual-shift sources,
  outstanding invoices, uninvoiced/changed work and receipt/allocation review.
  Matching remains explicit and does not create additional income.

## Last complete verification

Recorded on 2026-10-10 in [SECURITY_TESTS.md](SECURITY_TESTS.md), committed as
`7360e5a` after reconciliation (`007afc3`) and export (`4dd484c`).

- Main backend run: 474 tests, 470 passed and four optional backup cases skipped.
- Separate backup run: all 13 tests passed, including those four integration cases,
  on disposable PostgreSQL 16 with matching tools. The temporary server was stopped.
- All 14 browser scripts passed, including mobile/desktop exports, reconciliation,
  invoice allocation, authentication, sample isolation and multi-page invoice PDFs.
- Ruff F, Bandit, whitespace, Git integrity and outgoing secret checks passed.
  All 25 applied migration checksums matched. Bank traffic was mocked.

These are historical results. Recheck affected behaviour after subsequent changes;
do not infer deployment status or current database health from them.

## Publication authorization

On 2026-10-11 the user explicitly authorized pushing to the current public
repository after its visibility was checked. The verified remote is
`git@github.com:Thomas12356/TOMS.git`, owned by `Thomas12356`, with default branch
`main`. Commit `0b3c2ca` was pushed and the remote commit matched the local HEAD.
Subsequent implementation uses `codex/overview-navigation`.

## Current handoff

The user authorized starting [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md).
Phases 0 and 1 are implemented and published:

- Pinned development tools, private Chromium libraries and setup/verification
  commands replace dependence on temporary tooling. Single-year and multi-year
  synthetic performance baselines are in SECURITY_TESTS.md.
- Stream configure/archive/restore forms compare settings and mileage-choice
  snapshots under the existing write lock. Conflicts retain submitted values,
  display the current saved summary and require a deliberate reload.
- Background requests no longer renew session inactivity. Trusted interaction
  and user-activated document navigation renew a valid session within the fixed
  eight-hour expiry. Activity refreshes CSRF tokens without discarding form data.
- LoginAttempt maps the existing throttle table; its atomic PostgreSQL upsert
  respects isolated authentication schemas. Repeated browser tests previously
  bypassed this routing via raw SQL and touched the working attempt counter.
  No working credentials or owner records were changed; no counter reset was
  performed. Future browser runs use disposable counters.

Latest backend verification: 486 tests ran, 482 passed and four optional backup
integration cases were skipped. All affected workflows passed, including three
consecutive password-browser runs, stale-edit recovery, activity/expiry, sample
isolation and concurrent stream edits. Ruff F, application Bandit and whitespace
checks passed. All 16 browser scripts passed on the completed reliability changes;
the two new scripts cover stale-edit/session recovery and first-owner setup.

No schema migration, deployment or live Starling calls were performed.
A private backup was created; restore verification could not run because dedicated
CREATEDB test credentials are absent. Arrange isolated restore verification before
a later phase changes the working schema. Do not restore over the working database.

## Overview and navigation phase

Phase 2 is implemented on `codex/overview-navigation`, based on the published
reliability work. Successful login and the signed-in root URL lead to the owner
overview; the existing ledger URL and API-key ledger access remain compatible.

- Shared integer money formatting and Europe/London browser dates replace
  route-to-route formatter imports and UTC ledger day labels.
- Overview and checklist use a shared base template with a keyboard skip link.
  Navigation groups income/work, planning and settings while retaining existing
  URLs, sample controls, session activity and keyboard/mobile behaviour.
- Overview reads one repeatable-read, sample-routed saved-data snapshot. It shows
  the next record, planning readiness, received-income target, per-currency saved
  earmarks/gap, all-year outstanding issued invoices and saved import freshness.
  Unknown targets remain unavailable; stale invoice allocations do not reduce
  outstanding totals. Earmarks do not assert available bank funds. Opening the
  overview starts neither a bank call nor a rules check.

Verification: 496 backend tests ran, 492 passed and four optional backup integration
cases were skipped. The six overview regressions were rerun after adding the stale
allocation assertion. All 17 browser scripts passed, including the new overview
journey at 320, 390, 768 and 1280px. Mobile and desktop screenshots were inspected.
Ruff F, application Bandit and whitespace checks passed. No schema migration,
deployment or live bank calls were performed. Existing backup-test credential
limitations remain unchanged.

## Transaction review and deployment handoff

Overview commit `bf012d6` was fast-forwarded into main and published to the
user-authorized public repository.

Phase 3 is implemented on `codex/transaction-review`: bounded payer/reference,
London-date, classification and attention filters survive pagination and edits.
Review is an explicit action. The guided owner journey covers classification,
income or expense details, then checking and confirming the saved evidence;
quick confirmation remains optional. Validation retains entries, concurrent
changes reject stale confirmation, and the full journey works without JavaScript.
Unknown amounts, separate currencies, claim eligibility and existing allocations
retain their existing treatment.

Verification: 512 backend tests ran, 508 passed and four optional backup tests
were skipped. All 18 browser scripts passed; mobile screenshots were inspected.
Ruff F, application Bandit and whitespace checks passed. Disposable 10,000-record
ledger benchmarks measured approximately 0.067s for a matching filter, 0.034s
for an empty attention filter and 0.019s for guided queue selection. These are
local synthetic measurements, not production latency guarantees. No schema
change or live Starling verification was performed. Transaction review was
subsequently merged and deployed with compact navigation (see below). Next implementation follows
the revised usability roadmap; measured optimization
of full checklist/invoice queries remains in Phase 7.

## Compact navigation delivered locally

On 11 October, `codex/compact-navigation` was fast-forwarded into main and pushed
to the verified, user-authorized public repository. It includes guided transaction
review and the revised roadmap.

The header now exposes Home, Transactions, Work & income, Planning and Settings,
with native expandable secondary menus. Sample mode stays visible; logout lives
in Settings. Mobile collapses the primary menu below 900px when JavaScript is
available; section menus work without it. Escape restores focus, outside clicks
close menus, and current sections are highlighted. Guided review and persistent
ledger filters are now available in the running app.

Verification: 508 backend passes, four optional backup skips, all 18 browser
scripts passed across focused runs, and affected menu/sample/logout journeys
rerun after the final activation change. Ten checklist tests passed after copy
updates. Ruff F, application Bandit and whitespace checks passed. Open/closed
menu screenshots were inspected. No schema or financial-record changes were
needed for deployment. Next: unified task home, then staged income workspaces.

## Unified Home actions

Implemented on `codex/task-home`: failed or stale full-import status, deduplicated
payment actions across saved years, changed/overdue invoice evidence, income
setup and one planning-warning summary. Home previews six actions and a category
shows twenty, with exact counts and links to complete source records. Financial
summaries appear before the detailed action list. Review started from Home returns
to its same category instead of entering the wider ledger queue. Corrections clear
derived actions; confirmation alone does not clear stale or incomplete evidence.

Fixed two less obvious issues: stale-allocation totals now count allocations,
rather than invoices; and partial-import success cannot hide a failed full import.
Changed invoice work uses the same comparison as reconciliation and preserves
issued snapshots. No missing-pay or missing-mileage requirements are inferred from
uninvoiced work. Home remains a saved, repeatable-read view that makes no bank call.

Backend verification: 524 tests, 520 passed and four optional backup tests skipped.
All 19 browser scripts passed; latest evidence is in SECURITY_TESTS.md.
At 10,000 disposable transactions, Home measured 2.294s/122 queries and a filtered
category 2.330s/122 queries. Shared planning/checklist scanning remains a measured
performance limitation. Next work is staged income setup/workspaces, with this
read cost to be addressed before broader financial-summary expansion. Live-only
balances are not presented as saved cash or available spending money. No migration
or live Starling verification was required.

Publication completed on 11 October: `codex/task-home` was fast-forwarded into main
and pushed to the verified public repository under the user's existing publication
authorization.

## Home performance and staged income workspaces

Implemented on `codex/home-performance-income-workspaces`: Home reuses one ordered
payment snapshot for planning and checklist reads, with joined one-to-one evidence
and one reference time. Original-date claims moved into another year remain
reviewable; future/unsettled payments retain their exclusions. At 10,000 disposable
payments, Home measured 0.583s/21 queries (previously 2.294s/122 queries), and the
filtered category 0.628s/21 queries. These are local synthetic measurements.

Income sources can start with only name and type, preserving unknown forecast
amount/period/currency rather than zero. The next screen separates forecast,
entered work, receipts, expenses and mileage, with five-record previews and links
to existing full lists/forms. Settings opened from that workspace return there
after saving. The index renders only the requested settings dialog, with normal
GET navigation and JavaScript-disabled setup supported. Complete creation remains
available; locks, stale versions, audit actions and sample routing are retained.

No migration or live bank verification is required for these changes. Account
money still lives in the existing source-index section; a dedicated account view
and reusable work/mileage defaults are next. Saved bank-balance persistence still
needs a scoped migration and isolated backup restore verification first.

Verification: 530 backend tests ran, 526 passed and four optional backup cases
skipped. All 19 browser scripts passed across the full run and remaining-script
rerun after correcting the synthetic settings-navigation fixture. New regressions
cover unknown source creation, validation retention, bounded/source-isolated
previews, owner access, stale settings return context and moved-claim/future-payment
equivalence for shared reads. Affected backend forms also passed a focused
67-test run. Mobile/desktop workspace screenshots were inspected. Ruff F, Bandit,
whitespace and outgoing secret checks passed. Bank traffic was mocked; no working
schema change was needed.

Published on 11 October: the named branch was fast-forwarded into
main and both remote revisions matched `7e98eb3`. Repository owner/remote/public
visibility matched the user's existing publication authorization.

## Focused system usability publication and deployment — 2026-10-11

Implementation `dda27443875516baa5283dda6193a54d27b7c26f` was committed on
`codex/focused-system-usability`, pushed and fast-forwarded into main. Both remote
implementation revisions were verified. GitHub owner `Thomas12356`, the SSH
remote and public visibility matched the user's authorized publication preference.
