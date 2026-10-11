# Whole-system usability review

Reviewed 11 October 2026 after publishing implementation `aeedef9` to main
and deploying it locally. This is an expert walkthrough with synthetic records,
not user research or live bank verification.

## Delivery follow-up — 11 October 2026

The recommendations below informed `codex/focused-system-usability`. Completed:
compact income-notice counts with retained details/recovery, visible sample
identity with secondary help disclosed, consistent source/matching labels,
focused short source setup, grouped mileage fields with visible eligibility,
validated source returns, source/date-filtered history pages, prominent guided
review, planning next action/received target first, and unresolved matching groups
before source summaries. Advanced editing and calculations remain accessible.

Full work totals and mileage thresholds/reimbursement still use all evidence,
independent of history filters. Display pages contain at most 20 work records,
20 expenses and 20 journeys. Shift choices are the latest 200 plus older linked
records, preserving historical links. Annual mileage calculation still processes
complete evidence; bounded display is not a claim that this calculation is constant
time. No persistence migration, preset manager or broader automatic rule was added.

The repeat 14-page mobile/desktop expert audit had 28 successful visits without
overflow or JavaScript errors. At 390px, main-content height in the same synthetic
fixture changed: source setup 2,312 → 890px; work entry 2,065 → 1,604px; mileage
2,940 → 2,650px; planning 5,488 → 4,196px; matching 2,900 → 2,043px. These figures
describe layout, not task completion speed. The corrected mileage textarea keeps
a readable editing area rather than shrinking it just to reduce page height.

The initial user-observation step remains pending. Follow
[USABILITY_WALKTHROUGH.md](USABILITY_WALKTHROUGH.md) to capture uncertainty during
the five owner tasks. Long-form explanation still warrants observation; further
automation, budgeting, named presets, balance persistence and operational recovery
need their separately scoped design/evidence. The original findings below are the
pre-delivery record, rather than a claim that these changes remain unimplemented.

## Assessment

TOMS now has a coherent core journey: Home identifies a saved-record task,
guided review explains the payment, source workspaces separate expected income
from work and receipts, and Account money separates earmarks from cash. The
primary navigation is compact. The next improvement should reduce the amount
of material competing with the current task, especially on mobile.

The system remains more comfortable for an owner who already understands its
financial model than for a first-time user. Long explanatory pages, several names
for the same concept and multiple ways to review a payment add learning effort.
There is no evidence from this review that adding more top-level navigation or
automatic financial decisions would solve that problem.

## Evidence and limits

- Full regression run: 544 backend tests, 540 passed and four optional backup
  integration skips; all 20 browser scripts passed. Ruff F and Bandit passed.
- Additional post-publication walkthrough: 14 pages at 390px and 1280px, covering
  Home, transactions, sources, staged setup, source workspace, Account money,
  work, mileage, expenses, invoices, matching, checklist, planning and export.
  All 28 visits returned 200, with no horizontal overflow or JavaScript errors.
  Screenshots were inspected alongside the templates and request handlers.
- Login, setup, settings/session recovery, system status, guided review and invoice
  document behaviour were also assessed through their regression journeys,
  templates and selected screenshots. The walkthrough alone did not exercise
  every setting, invoice state or bank/provider failure.
- Sample-data screens include an intentionally prominent safety notice. Their
  total height overstates the equivalent real-data page; the income notice bar
  can also appear with real data. Page heights are observations from this fixture,
  not timings, accessibility certification or measured user completion rates.
- Bank calls were mocked. No working financial records or owner settings changed.
  Optional backup restore integration remains unverified in this run.

## What works well

- Five primary navigation destinations, expandable groups and a mobile menu
  provide access without exposing every feature in the header. Keyboard and
  JavaScript-disabled navigation are covered by browser checks.
- Home's next action and exact record links make incomplete evidence actionable.
  Confirmation and tax readiness are distinguished instead of conflated.
- Name/type-first source creation keeps unknown forecasts unknown. Source
  workspaces explain why receipts, forecasts and work are different evidence.
- Account money has an explicit refresh, response freshness, separate currencies
  and clear earmark meaning. Failed refreshes clear stale cash rather than leaving
  an apparently current number. Saved earmarks remain usable without JavaScript.
- Reusing saved work/journey fields saves repeated entry while requiring fresh
  actuals and eligibility. Suggestions explain their basis and leave saving to
  the owner. Conflicting history and stale versions do not silently overwrite data.
- Invoice snapshots, allocation limits, retained stale forms and private exports
  support trustworthy correction and an auditable history.

## Prioritized changes I would make next

| Priority | Finding and evidence | Recommended change and acceptance |
| --- | --- | --- |
| High | Attention is repeated across the global income notice, Home, checklist and planning warnings. On mobile the income banner plus sample notice occupies much of the first screen before the task begins (`templates/navigation.html`, `overview.html`, `checklist.html`). | Keep the mode warning visible, shorten its secondary explanation behind an accessible disclosure, and make Home the primary task destination. Use a compact income-notice count on other pages. The first task should remain visible sooner at 390px, without hiding unresolved warnings or losing acknowledgement history. |
| High | Mileage combines rules, a long form and both expense/journey history. Its fixture content is about 2,940px at 390px; the backend loads all saved journeys and expenses (`routes/deductions.py`). | Separate entry from history, group route/vehicle details and show help beside the relevant fields. Add source/date filters and bounded history pagination while keeping complete annual calculations. Eligibility remains explicit and visible before saving; reused values and validation errors survive navigation. |
| High | Planning is about 5,488px at 390px. Annual forecast, logged credits, received-income target and explanatory calculation all compete for attention (`templates/tax_estimate.html`). | Start with readiness, the applicable target and one next action. Put calculation/source breakdowns in accessible disclosures or a dedicated detail view. Keep unknown amounts and unsupported scope prominent; prove rendered totals unchanged with the existing six-state browser fixtures. |
| Medium | Labels vary: navigation says “Income streams”, staged creation says “income source”, the form also has an optional “Income source” description, and matching is called “Reconciliation”. | Use “Income sources” consistently for jobs/businesses; rename the optional description to distinguish it. Use “Work and payment matching” in navigation. Keep technical model/URL names internal. Check headings, button labels and back links together. |
| Medium | Source setup still shows competing “Add income source”, “Create with full details” and management actions, plus the full source list beneath the small new-source form. The fixture setup content is 2,312px at 390px (`templates/income_streams.html`). | Give the short creation form a dedicated focused view with one continuation. Retain advanced setup as a secondary link. Returning after creation or validation failure must preserve the intended source and entered fields. |
| Medium | Ledger rows expose classification, income/expense editing and guided review together; “Quick confirmation” also competes with guided review. Seven sample rows create about 4,770px of mobile content (`templates/dashboard.html`). | Promote guided review as the main action for records needing attention, group advanced editing under a disclosure and compact secondary metadata. Preserve expert direct editing and the separate meaning of confirmation. Test a mixed pending, future, settled and incomplete list. |
| Medium | Matching shows every source total before unresolved receipts/invoices; the fixture is about 2,900px at 390px. Work forms return to a record list and deductions return to the global page, making source-oriented entry feel fragmented (`routes/shifts.py`, `routes/deductions.py`). | Show unresolved matching tasks first and collapse clear sections. Add validated source return context to work/journey actions. A user should enter work, reuse a journey and return to the same source without losing context; matching must still be explicit and amount equality must not imply payment proof. |
| Later | Defaults currently reuse records rather than named owner-managed presets. Suggestions need at least two consistent confirmed predecessors with stable payer/reference; variable references will often produce no proposal. Account balances are live-only. | Observe actual repeated entry before introducing preset management or more flexible rules. Any wider matching needs explained criteria, conflict detection and preview. Persisting balances needs a schema/backup/isolated-restore plan before promising freshness offline or available spending cash. |
| Later | The System page explains backups with a reference to DATA_STORAGE.md; operational recovery still requires command-line knowledge (`templates/system.html`). | Provide readable backup status, instructions and failure recovery in the interface. Keep restore verification distinct from an archive merely existing. Do not add a destructive restore button without a separately designed recovery workflow. |

## Next release proposal

1. Reduce duplicate attention and standardize visible terminology, including
   focused source creation. Keep the existing five primary navigation groups.
2. Shorten daily mileage/work entry and keep source return context, then paginate
   history without changing complete calculation evidence.
3. Restructure tax planning and matching around unresolved actions, retaining
   auditable details and the existing money semantics.

Validate each on a separately named `codex/` branch with mobile/desktop,
keyboard, JavaScript-disabled, retained-error and stale-record journeys. After
these changes, observe an owner completing source creation, payment review,
work/journey entry, earmarking and invoice matching; that would provide evidence
about confusion and completion effort that automated checks cannot supply.
