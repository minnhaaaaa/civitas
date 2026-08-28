#!/bin/sh
set -eu

: "${CIVITAS_RESTORE_FILE:?set CIVITAS_RESTORE_FILE to a custom-format backup}"
: "${CIVITAS_RESTORE_CONFIRM:?set CIVITAS_RESTORE_CONFIRM to the target database name}"
: "${PGHOST:?set PGHOST}"
: "${PGDATABASE:?set PGDATABASE}"
: "${PGUSER:?set PGUSER}"

if [ "$CIVITAS_RESTORE_CONFIRM" != "$PGDATABASE" ]; then
  echo "restore confirmation does not match PGDATABASE" >&2
  exit 2
fi
if [ ! -f "$CIVITAS_RESTORE_FILE" ]; then
  echo "restore file does not exist" >&2
  exit 2
fi
if [ -f "${CIVITAS_RESTORE_FILE}.sha256" ]; then
  sha256sum --check "${CIVITAS_RESTORE_FILE}.sha256"
fi

table_count=$(psql --no-align --tuples-only --command="SELECT count(*) FROM pg_tables WHERE schemaname = 'public'")
if [ "$table_count" != "0" ]; then
  echo "target database is not empty; restore refuses destructive replacement" >&2
  exit 3
fi

pg_restore \
  --exit-on-error \
  --single-transaction \
  --no-owner \
  --no-privileges \
  --dbname="$PGDATABASE" \
  "$CIVITAS_RESTORE_FILE"
