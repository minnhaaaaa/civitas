# PostgreSQL backup and restore runbook

PostgreSQL is the canonical record for planning inputs, evidence lineage, Jury decisions,
approval receipts, workflow checkpoints, and execution ledgers. A backup is incomplete if its
database revision and provider-side execution references are not retained with it.

## Backup

Run backups from a controlled operations host with PostgreSQL 17 client tools. Supply connection
secrets through the process environment or a platform secret file, never command-line arguments.

```bash
export PGHOST=db.internal PGPORT=5432 PGDATABASE=civitas PGUSER=backup_role
export PGPASSFILE=/run/secrets/civitas_backup_pgpass
export CIVITAS_BACKUP_DIR=/srv/encrypted-backups/civitas
scripts/backup_postgres.sh
```

The script creates a custom-format dump, SHA-256 checksum, and Alembic revision sidecar under
umask `077`. Encrypt and replicate all three files. Use a database role with only the permissions
needed for backup.

Recommended policy:

- managed PostgreSQL point-in-time recovery plus daily logical backups;
- encrypted copies in a separate failure domain;
- retention matching organization audit policy;
- quarterly restore exercises and checksummed backup verification;
- alerts on missing backup, checksum failure, or retention-policy drift.

## Restore rehearsal

Restore only into a new, isolated, empty database. The provided script refuses to clean or replace
an existing schema.

```bash
export PGHOST=restore-db.internal PGPORT=5432 PGDATABASE=civitas_restore PGUSER=restore_role
export CIVITAS_RESTORE_FILE=/srv/encrypted-backups/civitas/civitas-YYYYMMDDTHHMMSSZ.dump
export CIVITAS_RESTORE_CONFIRM=civitas_restore
scripts/restore_postgres.sh
DATABASE_URL='postgresql+psycopg://...' uv run alembic upgrade head
```

Then verify:

1. `alembic current` reports the deployed head.
2. Evidence, lineage edges, approval receipts, execution audit events, and provider-write ledgers
   have expected counts and foreign-key integrity.
3. An unfinished planning run resumes from its checkpoint.
4. Repeating an already completed execution idempotency key returns the stored receipt and does
   not create another provider write.
5. Every external provider reference in the execution ledger is reconciled with the provider.

Do not replay a failed or ambiguous provider write solely because the database was restored.
Escalate it for reconciliation; the database cannot roll back an external supplier side effect.

## Disaster recovery

During a real incident, stop MCP write traffic and workers before database promotion. Restore or
promote PostgreSQL, apply reviewed migrations, reconcile provider references, then start workers.
Only mark MCP ready after the database revision and worker heartbeat gates pass. Rotate any secret
that may have been exposed during the incident and retain immutable incident/audit references.
