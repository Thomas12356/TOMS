# Working in TOMS

TOMS (The Organised Money System) is a Flask/SQLAlchemy/PostgreSQL personal
finance app with Starling imports. It tracks income, work, deductions, invoices
and payments, with planning estimates for England, Wales and Northern Ireland
in 2026–27. It does not submit tax returns or move money through its planning
features. Keep changes within the supported scope unless the user expands it.

## Find the relevant code

- `app.py`: application setup and blueprint registration.
- `routes/`: request handling, authentication, validation and responses.
- `services/`: business rules, bank integration and database operations.
- `models.py`, `migrations/`: stored records and versioned schema changes.
- `templates/`, `static/`: browser interface; `tests/`: regression/browser checks.
- Use [CODE_GUIDE.md](CODE_GUIDE.md) for code boundaries and feature walkthroughs,
  [DATA_STORAGE.md](DATA_STORAGE.md) for database/backup changes, and
  [SECURITY_TESTS.md](SECURITY_TESTS.md) for test setup and verification evidence.
- Read [PROJECT_STATUS.md](PROJECT_STATUS.md) for the latest recorded handoff.
  Verify changing facts against Git, configuration and the database before acting.

## Preserve these behaviours

- Store money in integer minor units; use Decimal for calculations. Unknown
  amounts remain `None`, not zero. Keep currencies separate. Tax-year boundaries
  use Europe/London; stored timestamps are timezone-aware.
- Forecasts, shifts, invoices and receipts are separate evidence for earnings.
  Never add them together as extra income. Credit actual logged withholding once.
  Regular forecasts add explicit overtime; individual-shift forecasts use entered
  work. Equal reconciliation totals do not prove every shift was paid.
- Preserve issued invoice snapshots, actual allocation budgets, stale-version
  checks and sending history. Recording sending does not send a message.
- Retain owner login for owner-only browser features; API keys are not an
  alternative there. Protect browser writes with CSRF and retain private caching
  headers, bounded requests and safe error logging. Pass `current_user` explicitly
  when rendering templates that need it; Flask-Login's context processor is disabled.

## Bank, database and secrets

- Mock bank traffic in tests. Live Starling verification needs explicit user
  authorization and must stay within the specified permission allowlist. Keep
  diagnostic GETs read-only and bank writes behind explicit POST opt-in.
- Never print or commit credentials or `.env*` secrets. Evidence exports must
  exclude raw bank payloads and authentication records. They intentionally contain
  selected financial/client records; preserve their field allowlists and CSV
  formula protection. Follow DATA_STORAGE.md for private database backups.
- Use ORM/DataSession routing for sample business records. Authentication stays
  in the real schema; do not mix it with sample data in a query. Raw SQL through
  the sample session is unsupported. Preserve snapshot consistency for exports
  and reconciliation.
- Add migrations instead of editing applied SQL files: their checksums are pinned.
  Use `.venv/bin/flask --app app db-upgrade` when applying required migrations,
  with the separate maintenance login in `.env.migrations`.
- Use rollback-only fixtures or disposable schemas/databases for tests. Never
  restore over the working database. Match PostgreSQL client/server versions.

## Validate and hand off

- Use the repository's `.venv` and run checks appropriate to the change.
  Full backend verification:
  `RUN_POSTGRES_TESTS=1 .venv/bin/python -m unittest discover -s tests -q`.
- Browser changes need the relevant `tests/*browser_checks.py` scripts with
  Playwright/Chromium. See SECURITY_TESTS.md for dependencies, environment setup
  and commands; temporary `/tmp` tooling paths may need rebuilding.
- Backup integration tests additionally need `RUN_BACKUP_POSTGRES_TESTS=1`,
  isolated CREATEDB credentials and matching `pg_dump`/`pg_restore` tools.
  Report skipped checks separately from passed checks.
- Run `git diff --check`. For Python changes, run Ruff F and Bandit when available
  using the documented tooling. Review outgoing content/history for secrets before
  publishing; verify the remote, owner, branch and repository visibility against
  the user's publication preference. Do not force-push unless explicitly requested.
- Follow existing user authorization without repeated approval questions. Explain
  material blockers. Report changes, validation and limitations; update
  PROJECT_STATUS.md when completed work or outstanding decisions change.
