# TOMS project status

Updated: 2026-10-11. This is a handoff snapshot, not a live system report.
Stable working instructions are in [AGENTS.md](AGENTS.md).

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
, owned by , with default branch
. Push the completed work without rewriting remote history and verify the
remote commit. All subsequent implementation belongs on a named  branch.

## Current handoff

The user authorized starting [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md).
Phases 0 and 1 are implemented locally:

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

No schema migration, deployment, publication or live Starling calls were performed.
A private backup was created; restore verification could not run because dedicated
CREATEDB test credentials are absent. Arrange isolated restore verification before
a later phase changes the working schema. Do not restore over the working database.

Next implementation: Phase 2, shared presentation/base templates, grouped
navigation and the owner overview. The user authorized public publication as recorded above.
