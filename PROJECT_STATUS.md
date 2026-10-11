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

## Outstanding publication decision

The user requested testing, committing and pushing. Testing and local commits
were completed; the push remains pending clarification of repository visibility.

Last checked on 2026-10-10: `origin` was `git@github.com:Thomas12356/TOMS.git`,
SSH authenticated as `Thomas12356`, and GitHub reported the repository public.
Earlier project notes specified private publication. The user has not yet chosen
between keeping it public and making it private before publishing new commits.
Verify current visibility and remote state, resolve that choice, then push the
authorized commits without rewriting remote history. Check the remote branch
matches the local commit before reporting success.

## Current handoff

Repository instructions and this status file were added on 2026-10-11. Continue
with the user's next task; no further feature work has been selected. Changes to
these documentation files do not imply that the application was redeployed.
