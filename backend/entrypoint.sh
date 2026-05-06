#!/bin/sh
set -e

# Bring the schema up to date before serving. Migrations are idempotent —
# re-running on every container start is safe and removes a class of "did
# you remember to migrate" errors. See docs/design/05-infrastructure.md §3.
echo "library api: running alembic upgrade head"
alembic upgrade head

echo "library api: starting gRPC server"
exec python -m library.main
