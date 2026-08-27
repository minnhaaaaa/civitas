#!/bin/sh
set -eu

shutdown() {
  if [ -n "${child_pid:-}" ]; then
    kill -TERM "$child_pid" 2>/dev/null || true
    wait "$child_pid" || true
  fi
  exit 0
}

trap shutdown TERM INT

if [ "${CIVITAS_RUN_MIGRATIONS:-false}" = "true" ]; then
  alembic upgrade head
fi

"$@" &
child_pid=$!
wait "$child_pid"
