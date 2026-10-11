# TOMS project status

Updated: 2026-10-11. This is a handoff snapshot, not a live system report.
Stable working instructions are in [AGENTS.md](AGENTS.md).

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
