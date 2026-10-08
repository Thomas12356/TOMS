# Data storage, backups and recovery

TOMS stores its records in PostgreSQL. The working database is selected by
`PGHOST`, `PGPORT` and `PGDATABASE` in `.env`. The website uses the restricted
`toms_app` role; maintenance commands use `.env.migrations` instead. Neither
backup nor restore imports the web application, starts jobs or calls Starling.

## What is stored where?

| Location | Contents | Backup approach |
| --- | --- | --- |
| PostgreSQL `toms` schema | Accounts, transactions, classifications, income details, streams, shifts, mileage, expenses, sync history, owner password hash, sessions, setup state and migration history | Database bundle below |
| PostgreSQL `toms_demo` schema | Separate sample records, including your edits in test mode | Included in the same bundle |
| `.env` | Runtime database credentials, bank/API credentials and application settings | Separate encrypted configuration copy |
| `.env.migrations` | Maintenance database credentials | Separate encrypted configuration copy |
| `.env.backup-test` | Separate restore-test login | Separate encrypted configuration copy |
| `instance/` | Local rule-check caches and other runtime files | Rule caches can be regenerated; backups live here by default |
| `data/tax_rules/`, migrations, templates and source code | Reviewed tax rules and application code | Git; retain the commit used for each deployment |

A database archive does not contain `.env`, application code, PostgreSQL server
configuration or role passwords. Any separately stored attachments would need
file backups too. Backups contain private financial data and password/session
hashes. They are compressed, **not encrypted**. Protect them like the database.

## Requirements

Install PostgreSQL client tools (`pg_dump` and `pg_restore`) on the machine
running the commands. Use the same major version as the server (currently 18),
or a compatible newer client. On a machine with the PostgreSQL package
repository configured, the package is `postgresql-client-18`.
The commands also find clients installed beside the active Python interpreter.
For this checkout, PostgreSQL 18 clients were installed privately in `.venv`;
recreating the virtual environment requires reinstalling those client tools.

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
