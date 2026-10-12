# Data storage, backups and recovery

TOMS stores its records in PostgreSQL. The working database is selected by
`PGHOST`, `PGPORT` and `PGDATABASE` in `.env`. The website uses the restricted
`toms_app` role; maintenance commands use `.env.migrations` instead. Neither
backup nor restore imports the web application, starts jobs or calls Starling.

## What is stored where?

Migration 034 adds nullable `evidence_needed_reason` fields to expense and mileage
records in real/sample schemas. Existing records have no additional-document request.
Reasons are limited to 1,000 characters, included in private backups and allowlisted
tax exports, and can be cleared when proof is already available. A pre-change private
backup was taken. Existing samples, files and financial amounts are preserved.

Migration 033 adds owner-only supporting files in `evidence_assets` and links in
`evidence_documents`, in both real and sample schemas. Originals are stored as
PostgreSQL BYTEA, deduplicated by SHA-256 within each schema; no uploads are placed
under static URLs or in external folders. Private database bundles automatically
include originals, metadata, previews and archived links together. A pre-change
private backup was taken before the additive migration; existing sample edits remain.
Each upload is at most 10 MiB, each schema has a 256 MiB budget including previews,
and each target permits 100 links including archived history. Archiving keeps the
original and removes the link from tax exports; it does not erase files or backups.
Unlinking an expense retains its transaction evidence; removing a journey leaves
its evidence in the document vault without a journey link. Restoring that link does
not reconstruct a removed journey. No automatic tax eligibility or amount changes
occur. Originals download through owner authentication as attachments; JPEG previews
have metadata stripped. Resource-limited parser processes validate static JPEG,
PNG, WebP and unencrypted PDF content; this is not antivirus or OCR.

Tax ZIPs include only an allowlisted active document index for selected payments
and journeys. A separate explicit download choice includes original files once per
hash, with index references via `asset_sha256`; archived and orphaned journey files
remain outside those exports. User-provided filenames/notes get normal CSV formula
protection. These files may themselves contain private financial/client details:
protect the downloaded ZIP as well as the database and its backups.

Migration 032 adds `statement_mappings` (account-specific header/settings hashes
and selected mapping fields) and `statement_changes` (selected financial snapshots,
reason and time). Private backups include both; evidence exports include only the
allowlisted correction history, excluding mapping settings/signatures. The original
normalized import rows remain intact after correction. Exclusions preserve the
transaction/provenance as REVERSED, with explicit restoration available. A private
pre-change backup was taken before applying this additive migration; no restoration
over the working database or credential setup is required.

Migration 030 adds statement account origins, history coverage, private CSV
staging and import provenance in real/sample business schemas. Staged raw grids
are removed on successful mapping or explicit discard; expired unimported batches
are removed on the next upload. Private database backups therefore may contain
unfinished statement grids. Evidence ZIPs exclude those grids, source mappings,
file hashes, filenames and authentication records; they include selected account,
history and confirmed import/row metadata with CSV formula protection. Migration
031 records a payment-state fingerprint with balance observations for reliable
invalidation after payment corrections. Neither migration changes saved amounts
or restores the database. The pre-change private backup was taken before applying
both migrations; restore verification remains excluded by the user's instructions.

| Location | Contents | Backup approach |
| --- | --- | --- |
| PostgreSQL `toms` schema | Accounts, transactions, classifications, income details, streams, shifts, mileage, expenses, annual tax profile, sync history, owner password hash, sessions, setup state and migration history | Database bundle below |
| PostgreSQL `toms_demo` schema | Separate sample records, including your edits in test mode | Included in the same bundle |
| `.env` | Runtime database credentials, bank/API credentials and application settings | Separate encrypted configuration copy |
| `.env.migrations` | Maintenance database credentials | Separate encrypted configuration copy |
| `.env.backup-test` | Separate restore-test login | Separate encrypted configuration copy |
| `instance/` | Local rule-check caches and other runtime files | Rule caches can be regenerated; backups live here by default |
| `data/tax_rules/`, migrations, templates and source code | Reviewed tax rules and application code | Git; retain the commit used for each deployment |

A database archive does not contain `.env`, application code, PostgreSQL server
configuration or role passwords. Supporting documents are inside the database
archive and require no separate file copy. Backups contain private financial data and password/session
hashes. They are compressed, **not encrypted**. Protect them like the database.

The annual tax profile is a business setting: real and sample profiles live in
their respective schemas and are included in the existing table-discovery backup
and fingerprint process. Missing profiles are unconfirmed. Migration 026 adds its
table without rebuilding existing samples; other schema-changing migrations may
still rebuild sample tables. Tax evidence exports include the selected profile's
explicit jurisdiction, confirmation time and revision with the calculation,
using the same read-only snapshot as the financial evidence.

## Cash planning records (12 October 2026)

`cash_commitments`, `cash_payments`, `cash_balances` and `cash_coverage` are
business tables in both real/sample schemas. Migrations 027–029 preserve existing
sample records. Commitments retain schedule, explicit HMRC year/component,
unknown/known integer amounts and revisions. Payments preserve occurrence date,
paid date, reference, explicit supported cash-provision credit and void history.
Balances retain their observed currency/time separately from account metadata.
Coverage stores only a record fingerprint and confirmation time.

The existing backup discovers and fingerprints all four tables automatically.
A private backup was created before migration; no restore over working data or
new credential setup was performed. The separate planning export includes
allowlisted commitments, payments and current balance observations (with both
recorded/current currency), streamed to an anonymous private ZIP within the
existing consistent read-only snapshot. Coverage's internal hash, auth records
and raw bank data are excluded; CSV formula protection remains in force. The
final file closes with the response or on failure. Cancelled schedules and voided
payments are retained. Exports are evidence, not import/restore mechanisms.

## Requirements

Install PostgreSQL client tools (`pg_dump` and `pg_restore`) on the machine
running the commands. Use the same major version as the server,
or a compatible newer client. On a machine with the PostgreSQL package
repository configured, the package is `postgresql-client-18`.
The commands also find clients installed beside the active Python interpreter.

The existing `.env.migrations` login needs access to both application schemas.
Keep credential files private (`chmod 600`) and never commit them. Backups must
be written into a private directory (`chmod 700`). The default directory is
`instance/backups/`, which Git ignores. Custom bundles must also stay outside
Git and public/shared folders.

## Make a backup

Run from the repository root:

```sh
.venv/bin/python -m services.database.backups backup
```

A successful backup produces a directory such as:

```text
instance/backups/toms-20261008T020000Z-a1b2c3d4/
    database.dump
    manifest.json
    verified-<unique-id>.json    # appears only after a successful restore test
```

Keep the **whole directory**. The manifest records the creation time, source
database name, archive checksum and a row count/hash for every application table.
It does not contain the rows themselves. Verification markers record successful
restore-test times and the archive checksum they checked.

The archive and manifest come from the same repeatable-read PostgreSQL snapshot,
so normal app writes can continue during the backup. Files have mode `600` and
the bundle directory mode `700`. Incomplete backups are removed and never
published as finished bundles. A failed verification retains the backup for
investigation but creates no success marker.

## Configure restore testing once

Restore testing needs a **separate login with CREATEDB**. Neither the normal app
nor the migration role receives this permission. Ask your PostgreSQL
administrator to create a dedicated login (choose a strong unique password):

```sql
CREATE ROLE toms_backup_test LOGIN CREATEDB NOSUPERUSER NOCREATEROLE
    NOREPLICATION NOBYPASSRLS;
```

Use psql's `\password toms_backup_test` prompt to set its password without
putting it in shell history. This login can create databases and therefore needs
careful protection; it is never loaded by Flask. For stronger separation, run
restore tests against a dedicated PostgreSQL test server.

Create `.env.backup-test` with mode `600`:

```dotenv
PGUSER=toms_backup_test
PGPASSWORD=your-separate-password
```

Host, port and connection database default to the source settings. Override
`PGHOST`, `PGPORT` and `PGDATABASE` in this file to use a separate test server.
The login needs CONNECT on that connection database. Its server must support
the archive's PostgreSQL version and have enough disk space for a restored copy.

Now run:

```sh
.venv/bin/python -m services.database.backups backup --verify
```

Or test an existing bundle:

```sh
.venv/bin/python -m services.database.backups verify instance/backups/<bundle>
```

The command creates a uniquely named `toms_restore_test_<random>` database,
restores within one transaction and compares every table's row count and hash
with the snapshot manifest. PostgreSQL recreates indexes and constraints; the
checker also rejects unvalidated constraints. It drops the temporary database
before writing a success marker. The working database is never a restore target.

If the process is forcibly killed or PostgreSQL becomes unavailable during
cleanup, an administrator may need to remove the specifically named temporary
database. Do not delete your working database. A success marker proves that
archive passed that test at that time; it does not guarantee future disk health,
correct financial inputs or compatibility with arbitrary future app versions.
The SHA-256 checksum detects accidental corruption, not malicious tampering by
someone who can replace both the manifest and archive. Restore only trusted
backups: PostgreSQL archives contain executable database definitions.

## Daily scheduling and retention

After a manual `backup --verify` succeeds, install the optional Linux user timer:

```sh
.venv/bin/python deployment/install_backup_timer.py
systemctl --user list-timers toms-backup.timer
journalctl --user -u toms-backup.service
```

It runs daily at **02:00 Europe/London**, catches up after missed runs and makes
a new backup followed by a restore test. It does not run bank syncs. The timer
needs client tools on its service PATH or beside `.venv/bin/python`. As with other user timers, the user
manager must stay running; an administrator can enable lingering if necessary.
A service failure is recorded in the journal; no email/push alert is configured.

Manual backups are retained unless you request pruning with `--verify --keep-days 30`.
The timer uses this option: only intact, previously verified bundles older than
30 days are removed, and only after the new backup successfully passes restore
verification. Unverified, damaged or unrecognized bundles are kept for review.
Keep monthly copies outside this directory if you want longer retention.
Monitor disk space; verification temporarily needs another database's worth.

Check backup timestamps (UTC) without opening financial records:

```sh
.venv/bin/python -m services.database.backups status
```

This checks archive hashes and reports the newest intact backup and successful
restore-test timestamp. It is a command-line status report, not a website banner.

Copy complete bundles and configuration to another device using encrypted
storage or an encrypted backup tool. A local backup does not protect against
loss of the server. Off-device copying, encryption and remote retention are
operator setup, not performed by this tool.

## Recover after a failure

1. Stop the web app, bank-sync timers and any other writers. Preserve the old
   database and files while investigating. Select a trusted verified bundle and
   the corresponding application revision.
2. Install compatible PostgreSQL and the app. Recover configuration privately;
   recreate the separate database roles if recovering to another server.
3. Have an administrator create an **empty replacement database**, owned by
   `toms_migrator`, such as `TOMS_recovered`. Do not reuse the working database
   name yet. Ensure the migration login can connect.
4. Keep `.env` pointing at the original name during this command:

   ```sh
   .venv/bin/python -m services.database.backups restore instance/backups/<bundle> --database TOMS_recovered
   ```

   The command refuses the configured source name, the bundle's original source
   name, system databases and targets containing user schemas/tables/functions.
   It never uses `--clean` or deletes an existing database. Restoration verifies
   the records against the manifest. If comparison fails, leave the app stopped
   and investigate; the replacement database may contain restored records.
5. Archives omit ownership and access grants so they can be restored by the
   replacement database's owner. As the maintenance role on **the replacement**,
   restore runtime access and revoke old login sessions/setup tokens:

   ```sql
   GRANT CONNECT ON DATABASE "TOMS_recovered" TO toms_app;
   REVOKE CREATE ON SCHEMA public FROM PUBLIC;
   REVOKE ALL ON SCHEMA toms, toms_demo FROM PUBLIC;
   GRANT USAGE ON SCHEMA toms, toms_demo TO toms_app;
   GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA toms, toms_demo TO toms_app;
   GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA toms, toms_demo TO toms_app;
   REVOKE ALL ON TABLE toms.schema_migrations FROM PUBLIC, toms_app;
   ALTER DEFAULT PRIVILEGES IN SCHEMA toms, toms_demo
       GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO toms_app;
   ALTER DEFAULT PRIVILEGES IN SCHEMA toms, toms_demo
       GRANT USAGE, SELECT ON SEQUENCES TO toms_app;
   DELETE FROM toms.browser_sessions;
   DELETE FROM toms.owner_setup;
   ```

   Review database CONNECT and public-schema permissions as in the restricted
   database setup. The backup does not recreate server roles or global grants.
6. Point `.env` at the replacement. Run `flask db-upgrade` if moving to newer
   application code (this can rebuild sample data). Restart, sign in and check
   account balances, transaction history, streams, deductions and tax totals
   before re-enabling bank syncing. The bank import can retrieve newer bank
   transactions; manual edits since the backup may need to be re-entered.
7. Keep the old database until recovery is confirmed and take a fresh verified
   backup. Rotating credentials is appropriate if recovery followed a compromise.

## Development tests

Unit safety tests:

```sh
.venv/bin/python -m unittest tests.test_backups -v
```

The real archive tests require an isolated PostgreSQL server and a CREATEDB
login. Set `PGHOST`, `PGPORT`, `PGDATABASE`, `PGUSER`, `PGPASSWORD` for that test
server, then run with `RUN_BACKUP_POSTGRES_TESTS=1`. The tests create their own
randomly named source and restore databases and remove them afterward. Never
use a production administrator login for this test suite.

## System management page and activity data

Open **System** in the navigation (`/dashboard/system`) after signing in as the
owner. This page always describes the real installation, even when test data is
selected. It is read-only and does not read maintenance credentials or call the
bank. API keys cannot access it.

- Database storage is PostgreSQL's current database size, with separate real and
  sample schema totals including table indexes/TOAST. Database-level overhead
  means the schema figures need not add up to the total. Cluster-wide WAL,
  filesystem free space and backups are not part of this database-size figure.
- The backups list reads metadata from `instance/backups` and shows the newest
  50 bundles, their disk sizes and recorded restore-test results. Custom backup
  locations and off-device copies are not listed. Loading the page does not hash
  large archives or rerun restore tests; use the verify command for a fresh check.
- The user action log starts when migration `022_user_actions.sql` is installed.
  It records successful owner browser actions, including sign-in/out, password
  changes, classifications, income details, streams, shifts/overtime, deductions,
  confirmations, test-mode switches, manual sync requests and rule checks.
  Background jobs, API calls, failed submissions and simple page views are not
  included. Bank-sync completion remains in sync history, separate from a user
  requesting sync. Dates use UTC.
- Activity is stored in `toms.user_actions`, included in subsequent database
  backups. Sample actions are clearly flagged and use the same transaction as
  the sample edit; they remain in the real action log when samples are rebuilt.
  The log contains action codes and record IDs, not submitted form contents,
  password hashes, session tokens or raw bank payloads. It is retained without
  automatic pruning. The runtime role can select/insert but cannot update/delete
  these events. A database administrator can still change them; this is an
  activity history, not a tamper-proof audit service.

When recovering an archive that includes the activity table, restore its
append-only permissions **after** any broad table grants:

```sql
REVOKE UPDATE, DELETE, TRUNCATE ON TABLE toms.user_actions FROM toms_app;
```

Apply `flask db-upgrade` and restart the app when deploying this feature to
another installation. As with existing migrations, upgrading rebuilds the
sample business dataset; real records and the action log are preserved.

## Invoice storage and recovery

Migration 023 adds `invoices`, `invoice_items`, `invoice_payments` and
`invoice_sequences`. These store client details, work snapshots, allocation
history and sequential numbering. Existing database bundles include them
and their separate sample records automatically. No invoice PDF files or
attachments exist in this version.

Restore these tables together with bank payments, streams and shifts. Retain
cancelled invoices and removed allocations for history. Restoring an older backup
also rolls back the numbering sequence: reconcile invoices issued since that
backup before issuing more, to avoid reusing numbers already shared with clients.

## Earmarks, expense treatment and income notices

Migration 024 adds `account_reserves` (per-account tax/NI amounts and revision)
and `income_notice_reads` (acknowledged payment versions), plus
`expense_deductions.is_tax_deductible`. Existing claims keep their tax treatment;
new browser forms default to tracking only. The legacy table name remains to
preserve its existing bank/shift links and migrations.

All of this state is in PostgreSQL and included in existing database bundles.
The demo schema has its own copies. Notifications derive advice from current
records; only acknowledgement versions are stored. Restoring an older backup
can therefore make previously read notices appear again. Earmarks survive
restarts and restores but do not transfer money or represent an HMRC payment.

## Invoice documents and sending history

Migration 025 adds invoice business/contact/payment fields and
`invoice_deliveries`, containing sent time, method, recipient, notes and recorded
time. Document details are editable only on drafts; issued records are never
backfilled with new seller information. The runtime role can SELECT and INSERT
real sending history but cannot UPDATE, DELETE or TRUNCATE it. Resends append
entries. Cancelling an invoice retains its history.

Existing database backups include these fields and tables, including separate
sample copies. PDF documents are produced through browser printing; no PDF or
email attachment files are stored by the server. Sending records describe manual
delivery and are not proof of successful email delivery or receipt.

## Tax-year evidence packages

Exports require no schema change and create no stored files or export rows. ZIP
bytes are assembled in memory from an independent read-only repeatable-read
transaction. Real authentication remains in its usual session; sample business
records use the selected demo schema. The package is a dated evidence snapshot,
not a restorable database backup. Included records and selection rules are
described in its README.

## Reconciliation review

Reconciliation creates no additional income, matching records or stored state.
It derives the review from shifts, invoice snapshots, payment allocations and
classified receipts in a read-only snapshot. Removing or adding a payment uses
the existing invoice history tables. No schema change is required.

## Temporary evidence export files

Browser evidence downloads spool allowlisted JSON/CSV and the final ZIP to anonymous operating-system temporary files with restrictive file permissions. These files contain selected private financial/client evidence, never authentication records or raw bank payloads. Spools close on completion/failure; the final file closes with the response or on response preparation failure. They are ephemeral export material, not retained backups. Generation stays inside the existing read-only consistent snapshot; all financial allowlists and CSV formula protection remain in force. Disk capacity can limit large exports even though Python memory use is reduced.
