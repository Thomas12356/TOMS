# TOMS implementation plan

Delivery priorities now follow the post-publication
[whole-system usability review](USABILITY_REVIEW.md). Navigation, guided review,
task Home, source workspaces, Account money, reusable defaults and explicit
suggestions are implemented. The next work reduces distraction and effort in
existing journeys. Existing engineering safeguards below still apply.

Created and revised: 2026-10-11. The next delivery sequence below takes precedence
over the historical phase ordering. See PROJECT_STATUS.md for verified delivery.

## Next delivery sequence

### 1. Focus attention and source setup

Branch: `codex/focused-attention-source-setup`.

Start here because the review found repeated notices and competing actions on
most screens; improving this benefits every subsequent workflow without changing
the financial model or requiring a migration.

- Keep Home as the main task destination. Replace the large global income banner
  with a compact count/control, retaining notice details, acknowledgement and
  readable failure/retry states. Unresolved financial warnings stay visible where
  they affect a decision; do not silently dismiss them.
- Keep the sample-mode identity and separation warning visible. Move secondary
  explanation into an accessible disclosure with a usable HTML fallback.
- Standardize job/business labels as “Income sources” and matching as “Work and
  payment matching”; distinguish the optional payment description from a source.
  Update related headings, buttons and back links together, retaining URLs.
- Give name/type source creation a focused view with one primary continuation.
  Keep full configuration available as a secondary path and preserve invalid
  entries, unknown forecasts and the return to the new source workspace.

Acceptance: compare real and sample screenshots at 390px and 1280px with the
review baseline. The main task starts higher on mobile, mode identity remains
obvious, and notice count/details/errors remain reachable. Creation presents one
primary continuation and retains input on failure. Verify keyboard, no-JavaScript,
owner-only access, CSRF, sample isolation and existing notice acknowledgements.

### 2. Make daily work and mileage entry shorter

Branch: `codex/work-mileage-entry-history`.

- Separate entry from history and group journey/vehicle fields. Keep eligibility
  visible before saving; show concise explanations beside the fields they affect.
- Carry validated source context through work, expense and journey entry, reuse,
  errors and saving. Return to the same source workspace rather than the global
  list. Preserve existing filtered-ledger return behaviour when that is the origin.
- Add source/date filtering and bounded history pagination. Financial calculations,
  allowances and consistency checks must still use complete relevant evidence;
  a display page is never the calculation input.
- Keep saved-record reuse. Do not introduce preset tables until repeated owner
  workflows demonstrate that record templates are insufficient.

Acceptance: create work, reuse a journey, correct an invalid field and return to
the same source on mobile and without JavaScript. Confirm dates, actual miles,
reimbursement and eligibility are checked afresh, old records unchanged, stale
writes rejected and calculated results identical across display pages/filters.
Use a large disposable history to verify bounded displayed records/query work.

### 3. Make review, planning and matching action-first

Branch: `codex/action-first-review-planning` (split further if needed).

- Promote guided review on ledger records needing attention; group expert editing
  and secondary metadata into accessible disclosures. Keep quick confirmation's
  distinct meaning and existing direct-edit capabilities clear.
- Start planning with readiness, the applicable target and one exact next action.
  Move calculation/source breakdowns into accessible details while keeping unknown
  amounts, unsupported scope and material warnings prominent.
- Put unresolved receipts/invoices first in matching; collapse clear summaries
  without concealing evidence. Retain explicit allocations and the warning that
  equal totals do not prove work was paid.

Acceptance: mixed pending, future, settled and incomplete ledger fixtures remain
correct. All six planning states retain identical totals and warnings. Matching
still rejects stale/over-budget allocation and never adds duplicate income.
Compare mobile task position and visible competing actions before/after; do not
treat lower page height alone as evidence of improved usability.

### 4. Validate with the owner before expanding features

Observe source creation, payment review, work/journey entry, earmarking and invoice
matching. Record where the owner hesitates, loses context or needs an explanation;
use that evidence to revise priorities. This is not yet completed user research.

Later: readable backup/recovery guidance in System, named presets if justified,
and a separately designed balance-persistence change. Persisted balances require
private backup and isolated restore verification before migration. Budgeting, AI,
broader automatic rules and available-to-spend claims need separate scope/design.

### Verification and publication for each delivery

Run affected backend/browser journeys while implementing, then the full PostgreSQL
and browser verifier before publishing each completed delivery. Include 320, 390,
768, 1024 and 1280px where affected, keyboard, JavaScript-disabled, retained-error,
stale-record and real/sample checks. Run Ruff F for Python changes, Bandit and
`git diff --check`; scan outgoing content/history for secrets. Report optional
backup skips separately and do not describe them as passes. Update handoff with
actual evidence, commit on the named branch and use the existing authorized
publication process. Check local deployment health after a deployed change.

## Objective and boundaries

Implement the project review recommendations as small, verifiable changes: fix
editing/session weaknesses, simplify everyday workflows, and improve operational
reliability and maintainability. Retain Flask, SQLAlchemy and server-rendered HTML.
Support remains EWNI 2026–27; prepare rollover architecture without enabling new
tax years, tax-return submission, bank transfers or automatic message delivery.

Preserve all AGENTS.md financial, security, snapshot, schema-isolation and backup
requirements. UI consolidation must not merge forecasts, work, invoices and actual
receipts into a single earnings total. Unknown figures remain unknown.

## Delivery approach

- Work sequentially through the phases below, with a separate reviewable change
  for each numbered deliverable. Split schema and UI changes where that makes
  compatibility easier to verify; do not leave a broken intermediate state.
- First reproduce suspected defects. Code inspection identifies risks, but is
  not evidence that a new regression test reproduces them.
- Reuse existing calculation, import, validation and allocation services. Extract
  shared code only when the next deliverable needs it; avoid a broad rewrite.
- Record completed work, validation, limitations and the next step in
  PROJECT_STATUS.md. Revise this plan if implementation evidence changes a choice.
- No parallel agents are required. The user authorized pushing to the existing public repository on 2026-10-11.
  Subsequent implementation uses a named codex/ branch.

## Phase 0 — Establish a reproducible baseline

1. Inspect Git changes, deployment entry points, actual migration state and test
   configuration. Read relevant existing tests before altering behaviour.
2. Restore Playwright/Chromium tooling: the documented temporary installation is
   currently absent. Make development test dependencies and a single verification
   command reproducible, separate from production dependencies. Document Linux
   library requirements rather than depending solely on transient /tmp paths.
3. Walk through synthetic first-run, employment, freelance and CIS workflows at
   mobile and desktop widths. Record current task steps, screenshots, accessibility
   issues and query counts; mock bank/rule traffic and use isolated fixtures.
4. Capture a baseline for notice polling and reconciliation on disposable data at
   ordinary and multi-year volumes. Establish explicit performance budgets from
   these measurements before optimizing.

Acceptance: fresh backend and browser checks can be repeated; known failures and
skips are recorded independently. Review baseline: 474 backend tests ran on
2026-10-11, 470 passed and four optional backup integrations skipped. Fresh browser
checks failed to start because Playwright was unavailable.

## Phase 1 — Protect edits and define real inactivity

1. Use the existing record-snapshot hash pattern for IncomeStream settings. Include
   it in configure, archive and restore submissions; check current settings and
   mileage relationship choices under the existing shared write lock before any
   mutation. Preserve submitted values and show the current saved summary with an
   actionable conflict message. This replaces the initially proposed revision
   migration: hashing existing columns and related records covers current writers
   without altering the working schema or introducing an extra revision counter.
2. Separate session validation from activity renewal. Background notices, balance
   reads and status checks must validate expiry without extending inactivity.
   Define a bounded, owner-only, CSRF-protected activity signal for actual keyboard,
   pointer and touch interaction; throttle it and do not renew merely for visibility
   or elapsed time. Keep the eight-hour absolute expiry, revocation and sample/auth
   separation. Handle expired sessions consistently in asynchronous UI controls.

Acceptance: two tabs cannot silently overwrite stream settings; concurrent archive
and edits are deterministic. Polling alone cannot keep a session alive beyond
30 minutes; actual interaction renews it within the absolute limit. Verify with
controlled clocks, concurrent database tests and browser expiry/recovery checks.

## Phase 2 — Introduce shared structure and an overview

1. Extract money/date display helpers from routes/dashboard.py into a shared
   presentation module. Use Europe/London for human-facing dates and Today labels;
   preserve UTC storage/API contracts and test local-midnight/DST boundaries.
2. Introduce a common base template and shared page/error/navigation components.
   Migrate templates incrementally, retaining script loading, CSP, explicit
   current_user handling, private headers, sample labels and keyboard behaviour.
3. Group navigation around Overview, Transactions, Income/work, Invoices and
   Planning. Put rules, system health and password controls in Settings. Keep
   existing URLs and useful cross-links compatible.
4. Add an owner-only overview and make it the signed-in landing page. Show the
   next actionable records, forecast/received-income readiness, reserve target,
   earmarked amount/gap, outstanding invoices and saved import freshness.
   Use existing snapshot/calculation services; do not start a bank call on opening
   it. Represent unavailable data explicitly and keep currencies separate.

Acceptance: users can identify their next action without opening multiple pages.
Overview totals match existing source pages and do not add evidence together.
Test empty, incomplete, stale, unavailable and sample states at 320, 390, 768 and
1280px, with keyboard and focus checks. Inspect rendered screenshots.

## Phase 3 — Make transaction review a complete task

1. Add bounded server-side filters for payer/reference search, date range,
   classification and attention state. Specify attention semantics centrally and
   reuse them in overview/checklist links. Preserve all filters through edits and
   pagination; parameterize search and select indexes using measured query plans.
2. Replace automatic review popups with a visible Review N transactions action.
3. Build a guided review that presents classification, the applicable income or
   expense details, and confirmation in one flow. Reuse existing validators and
   services; preserve separate records. Save corrections and confirm the resulting
   version only after presenting its details. Changed bank data must still reject
   stale submissions. Provide progressive HTML fallbacks and resume/navigation
   context; avoid forcing users to complete every record at once.

Acceptance: one receipt can be classified, completed and confirmed without
repeated unrelated page visits. Validate income, expenses, transfers, non-taxable,
pending and corrected receipts; ensure validation/conflicts preserve user input.
Search works for long names and hostile text without leaking private payloads.

## Phase 4 — Simplify income and account planning

1. Give each stream a dedicated workspace with forecast, work, payments and
   deductions sections. Make the stream list a concise index, avoiding a full
   configuration dialog per stream. Reuse existing shift/deduction workflows and
   preserve old links through redirects or compatible entry points.
2. Stage stream creation: name/type first, then applicable forecast or entered-work
   setup, then optional dates, absence and mileage relationships. Permit explicitly
   incomplete forecasts rather than storing invented zeroes; add a migration if
   required by actual constraints. Surface incompleteness through readiness checks.
   Preserve nonsecret first-owner fields after validation errors; the baseline
   browser journey currently requires re-entering the username as well as secrets.
3. Move account balances and earmarks into a dedicated account-money view. Show
   target, earmarked amount and remaining gap together; explain forecast dependence,
   withholding and excluded direct HMRC payments beside the figures. Preserve
   existing earmark revisions and underfunding behaviour.
4. State supported location/year during setup and planning. Document how a future
   rollover would clone eligible configuration into independent year records,
   require forecast review and retain historical rules/evidence. Do not activate
   another year without a separately reviewed rule dataset and expanded authorization.

Acceptance: users can create an incomplete source safely and finish it later;
regular forecasts and individual work remain distinct. Account-money figures agree
with the current reserve calculation. Older links and sample workflows still work.

## Phase 5 — Reuse business and client details for invoices

1. Add owner business defaults and reusable client records with versioned edits,
   field validation and appropriate restricted runtime grants. Decide their
   real/sample routing explicitly; exercise schema rebuilding and isolation.
2. Prefill new drafts from those records, retaining editable per-draft copies.
   Existing drafts remain valid. Issue freezes the same seller/client/payment
   snapshots used today; changing defaults or clients never changes issued invoices.
3. Preserve delivery history, numbering, cancellation, allocation budgets and
   print/PDF layouts. Extend evidence field allowlists deliberately if client/default
   evidence is needed; exclude internal fields from client documents.

Acceptance: a repeat invoice does not require retyping seller/client details.
Changes to reusable records leave previously issued documents byte-for-byte
equivalent in content. Run invoice allocation and multi-page document regressions.

## Phase 6 — Durable jobs and actionable operational health

1. Replace web-worker daemon imports with durable queued requests executed by a
   separate CLI worker under systemd. Prefer PostgreSQL job state and the existing
   deployment model; add Redis/Celery only if measured requirements justify them.
2. Define atomic claiming, bounded retries/backoff, leases, interrupted-job recovery,
   coalescing and idempotency. Reuse run_sync checkpoints and its database lock.
   Keep minimum-age/force semantics and scheduled imports compatible. A force
   request bypasses freshness, not locking or bank permission boundaries.
3. Report queued/running/completed/failed jobs to the browser without blocking the
   request or causing duplicate imports. Sample sync stays isolated and bank-free.
   Integration tests mock all bank requests; enabling a real worker that contacts
   Starling requires explicit live-bank authorization.
4. Surface missing/overdue verified backups, import failures and rule-check health
   in System and the overview. Read compact metadata/status, not large archives,
   during web requests. Distinguish unconfigured schedules, failed jobs and unknown
   health. Do not introduce external notifications or messages.
5. Document worker installation, monitoring, failure recovery and disabling it.
   Include rule-check lifecycle review: normal page rendering must remain usable
   when a check fails or a process restarts.

Acceptance: a web restart does not lose an accepted import request; worker restarts
recover safely without duplicated earnings. Unchanged bank requests remain within
the existing allowlist. Operational faults lead to concrete recovery instructions.

## Phase 7 — Optimize measured growth and finish code boundaries

1. Split notices into bounded detail queries and full calculation aggregates.
   Preserve chronological cumulative reserve computation, acknowledged receipts'
   contribution and version-triggered reopening. Apply year/currency rules exactly.
2. Optimize reconciliation through aggregates and bounded detail retrieval while
   retaining allocations/reservations from all relevant years. UI caps must never
   cap totals or available receipt budgets. Add caching only with complete
   invalidation and snapshot-consistency rules; prefer query improvements first.
3. Split dashboard routes by responsibility and centralize repeated owner guards,
   query validation and response handling while preserving existing auth contracts.
4. Introduce create_app with explicit configuration initialization. Preserve the
   app import compatibility needed by current tests/CLI, gunicorn setup-token
   behaviour and maintenance commands; remove reliance on bank-module imports
   loading environment configuration. Verify multiple independently configured apps.

Acceptance: recorded performance budgets are met on representative disposable
datasets with identical financial results. Old entry points, migrations, CLI,
sample routing and startup behaviours remain covered. Avoid unnecessary rewrites
of stable domain code or migration tooling.

## Validation and release gates

- Each behavioural change: meaningful targeted regressions, including stale edits,
  failures and sample isolation. UI changes: relevant Chromium scripts, screenshots,
  keyboard operation, focus restoration, mobile layout and session expiry recovery.
- Each phase: full PostgreSQL suite, git diff --check, Ruff F and Bandit for Python
  changes. Rerun affected checks after fixes; record exact passes and skips.
- Schema changes: new migration files, verified existing checksums, restricted
  grants, sample-schema routing/rebuild and backward-compatible stored records.
  Apply with the maintenance login according to DATA_STORAGE.md; retain a verified
  private backup before changing the working schema.
- Backup-related changes: optional backup integrations on disposable databases
  with matching PostgreSQL tools and isolated CREATEDB credentials. Never restore
  over the working database.
- Final gate: all browser scripts and a complete synthetic journey from source
  setup through work, invoice issue, receipt review/allocation, planning and export;
  compare financial results with the original services' fixtures.
- No deployment/publication claim without evidence. Resolve the recorded visibility
  preference and scan outgoing content/history before any authorized push.

## Order and completion tracking

Phase 0 -> 1 -> 2 -> 3 -> 4 -> 5 -> 6 -> 7 -> final verification.
Optimize earlier if baseline measurements show current query costs block the new
overview. Perform narrowly required code extractions during feature phases; the
application-factory migration waits until workflows are stable to reduce churn.

- [x] Baseline and reproducible browser tooling
- [x] Stream stale protection and interaction-based inactivity
- [x] Shared presentation, navigation and overview
- [x] Filtered ledger and guided transaction review
- [ ] Stream workspaces, staged setup and account money
- [ ] Business defaults and reusable clients
- [ ] Durable imports and operational health
- [ ] Measured query optimization and final architectural cleanup
- [ ] Complete validation and updated handoff

Implementation notes (2026-10-11): development tools now have pinned dependencies,
setup/verification scripts and a private native-library path. Single-year and
multi-year read benchmarks have been recorded in SECURITY_TESTS.md. Stream forms
now reject stale/competing edits, including changed mileage choices; browser
recovery preserves entries. Sessions validate without polling renewal, renew on
trusted interaction or user-activated navigation, preserve absolute expiry, and
refresh CSRF tokens for active long-lived forms. Expiry errors preserve unsaved
entries. No schema migration or live bank verification was performed.

Repeated browser login runs exposed the existing raw-SQL throttle bypassing the
disposable schema mapping. LoginAttempt now maps that existing table and the
atomic PostgreSQL upsert follows authentication-schema routing. Its twenty-attempt
window and concurrency behaviour are retained; a regression verifies that sample
workflows cannot create demo authentication tables or touch the working counter.

A private pre-change backup was created, but restore verification could not run
because dedicated CREATEDB credentials are not configured. Before a later phase
changes the working schema, arrange a successful isolated restore test as required
by DATA_STORAGE.md. This does not block the schema-free reliability fixes.

Phase 2 completed on `codex/overview-navigation`: shared presentation helpers,
Europe/London ledger dates, base templates for overview/checklist, grouped
navigation and the signed-in owner overview. Saved business data is read through
one repeatable-read routed session, reusing planning/checklist/settlement logic.
The overview preserves unknowns, currencies, stale allocation treatment and
separate evidence. It starts no bank or rule check. Validation: 492 backend passes,
four optional backup skips, all 17 browser scripts, Ruff F, Bandit and whitespace.
Further template migration is incremental. Next: Phase 3 on a separately named
transaction-review branch.

Phase 3 completed on `codex/transaction-review`: bounded persistent ledger
filters, explicit review entry, guided classification/details/check/confirmation,
retained validation input and stale evidence protection. All 18 browser scripts
and 508 backend cases passed; four optional backup cases skipped. Ruff F, Bandit
and whitespace passed. A disposable 10,000-record filtered ledger request took
approximately 0.067s.
Overview has been merged into main. See PROJECT_STATUS.md for publication history.

The revised roadmap's unified task home is implemented on `codex/task-home`:
deduplicated payment actions, full-import failure/freshness checks, invoice evidence
actions and income/planning follow-ups. Exact category counts accompany bounded
previews; Home review returns to its category. Financial summaries precede the
detailed list. A multi-allocation overview counting bug was corrected. Saved cash
balances remain deferred because balances are currently fetched live rather than
persisted. The measured 10,000-record Home path still scales with shared planning
reads; optimize that measured cost before expanding the summary into budgeting.
