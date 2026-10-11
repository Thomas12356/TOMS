# TOMS project status

Updated: 2026-10-11. This is a handoff snapshot, not a live system report.
Stable working instructions are in [AGENTS.md](AGENTS.md).

Latest usability work: [USABILITY_ROADMAP.md](USABILITY_ROADMAP.md) incorporates
the user's supplied review and complaint about crowded navigation. Compact primary
navigation and guided-review deployment are the first delivery. A unified task
home is next; budgeting/AI remain later scoped milestones. See the latest handoff
below for deployment revision and verification evidence.

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
