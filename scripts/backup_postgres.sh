#!/bin/sh
set -eu

: "${CIVITAS_BACKUP_DIR:?set CIVITAS_BACKUP_DIR to an existing protected directory}"
: "${PGHOST:?set PGHOST}"
: "${PGDATABASE:?set PGDATABASE}"
: "${PGUSER:?set PGUSER}"

if [ ! -d "$CIVITAS_BACKUP_DIR" ]; then
  echo "backup directory does not exist" >&2
  exit 2
fi

umask 077
stamp=$(date -u +%Y%m%dT%H%M%SZ)
base="${CIVITAS_BACKUP_DIR%/}/civitas-${stamp}"
temporary="${base}.dump.partial"

pg_dump --format=custom --compress=9 --file="$temporary" "$PGDATABASE"
revision=$(psql --no-align --tuples-only --command='SELECT version_num FROM alembic_version')
mv "$temporary" "${base}.dump"
printf '%s\n' "$revision" >"${base}.alembic-revision"
sha256sum "${base}.dump" >"${base}.dump.sha256"
printf '%s\n' "${base}.dump"
