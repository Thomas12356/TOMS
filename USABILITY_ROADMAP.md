# TOMS usability roadmap

Prepared 11 October 2026 from the user's supplied usability review dated
10 October, checked against the implementation and current deployment.
This updates delivery priorities; IMPLEMENTATION_PLAN.md retains detailed
engineering safeguards. Review scores and week estimates are qualitative,
not measured results or delivery commitments.

## Starting point

The owner overview and grouped navigation are live locally. Filtered ledger and
guided transaction review are implemented and published on
`codex/transaction-review`, but are not merged or deployed. Reconciliation,
tax-year evidence exports, reserve targets and earmarks already exist. Improve
their discoverability rather than rebuild them. The latest recorded verification
is 508 backend passes, four optional backup skips and 18 browser scripts.

The overview does not yet establish available-to-spend cash. Annual tax targets,
recorded earmarks and bank balances have different meanings. Never use the
review's fictional figures or imply that a tax earmark proves funded cash.

## 1. Reduce navigation clutter and finish the existing review journey

First change: replace the fully exposed header links with a small primary set:
Home, Transactions, Work & income, Planning, and Settings. Home remains the
overview URL. Work & income expands to income sources, expenses & mileage,
invoices and payment matching. Planning expands to tax estimate and tasks.
Settings expands to tax rules, system status, password and sign out.

Use accessible expandable menus with clear current-section highlighting,
keyboard navigation, Escape/focus behaviour and a usable HTML fallback. Mobile
gets one menu button and stacked expandable groups. Keep sample mode clearly
visible in a compact status control; the sample notice must remain visible when
enabled. Preserve its CSRF-protected switch and existing URLs. Do not place all
secondary links in the header or add another permanent notification bar.

Implement this on a named navigation branch, check screenshots at 320, 390,
768, 1024 and 1280px and check zoom, keyboard and JavaScript-disabled access.
Verify owner/API visibility, logout, sample controls and session activity.
Then merge and deploy the already-tested transaction review work with the
navigation improvement, stopping the service before updating its checkout.

Acceptance: the desktop header stays compact at normal widths; mobile has no
horizontal overflow; every existing destination remains reachable; a user can
find and complete one transaction review without documentation.

## 2. Turn the overview into a practical task home

Reuse readiness, reconciliation and import freshness to create one ordered task
list. Each task explains the issue, links to the exact record and disappears
when its underlying condition is resolved. Prioritize missing/changed evidence
and failed imports over informational notices. Do not equate confirmation with
tax completeness or invent missing-mileage obligations from absent records.

Add saved cash balances with account/currency separation and timestamps. Show
unavailable/stale values plainly; opening Home must not trigger bank traffic.
Keep income, outgoing cash, tax targets and outstanding invoices distinct.
Stage setup around owner access, supported location/year, saved/imported records
and the first income source. Incomplete financial values stay unknown.

Acceptance: empty, incomplete, stale, sample and normal states explain the next
action; totals agree with their source pages; completing a task clears its flag.

## 3. Reduce repeated entry with explicit suggestions

First simplify income-source setup and give each source a workspace for forecast,
work, payments and expenses. Keep forecasts, logged work and receipts separate.
Move account money/earmarks out of the source index into a clear account view.

Then add owner-managed classification rules and suggested source matches, using
existing saved records. Show why a suggestion was made and require acceptance
before changing financial evidence. Treat recurring-payment detection as a
suggestion. Add reusable mileage/work defaults and business/client defaults;
editing defaults must not alter historical entries or issued invoice snapshots.

Acceptance: recurring tasks take fewer entries/navigation steps than the recorded
baseline; ambiguous matches remain unassigned; stale versions cannot overwrite
edits; all changes are auditable and reversible where appropriate.

## 4. Add budgeting only after defining the money model

The supplied review requests an expansion into household budgeting. Before schema
work, agree the first scope: monthly bills and category budgets, savings goals,
and which accounts/currencies participate. Defer investments and net worth until
that workflow is useful. Specify carry-over, pending payments, internal transfers,
recurring-payment matching and actual-versus-planned spending explicitly.

Model cash balances, bill commitments, budget allocations, goals, tax targets and
funded reserves separately. Define available-to-spend only after resolving their
overlap and freshness. Unknown obligations produce an incomplete estimate, not
a reassuring zero. Build monthly cash-flow charts from actual cash movement with
transfers excluded; do not add forecasts, invoices or logged shifts as receipts.

Require an isolated, successful backup/restore check before changing the working
schema. Add versioned migrations and disposable-schema invariants, including
double-counting, currency separation and boundary dates. Deliver one complete
monthly bills/budget workflow before broadening planning features.

## 5. Strengthen operations, then consider integrations

Improve durable import jobs, last-success/failure visibility, safe retries and
operational recovery. Optimize notice/checklist/invoice queries using measured
costs rather than speculative rewrites. Preserve the existing Flask architecture.

ChatGPT integration is a later optional project: establish authentication,
data minimization and a read-only structured summary first. Any writes require
validated proposals, explicit confirmation, stale checks and audit records.
Financial data must not be sent to an external AI provider as an incidental
side effect of ordinary browsing. Define authorization and retention before use.

Amazon Flex and other integrations need a concrete import format/permission
scope first. Evidence exports already exist. Additional tax years, tax submission,
money movement and investment advice are outside the current supported scope.

## Delivery and verification

Use small named `codex/` branches. Record the starting task steps and compare
after each usability change; responsive screenshots alone do not prove usability.
Run relevant browser workflows, PostgreSQL regression tests for business changes,
Ruff F, Bandit and whitespace checks. Mock bank traffic and use disposable data.
Report skipped checks separately. Update PROJECT_STATUS.md and this roadmap after
each delivered milestone. Publish only after reviewing outgoing content for secrets.

Immediate sequence: compact navigation, deploy the existing guided review,
unified task home, staged income/workspaces, then safe reusable entry defaults.
Budgeting and AI remain explicitly scoped later milestones.
