#!/bin/sh
set -e

# Bring the schema up to date before serving. Migrations are idempotent —
# re-running on every container start is safe and removes a class of "did
# you remember to migrate" errors. See docs/design/05-infrastructure.md §3.
echo "library api: running alembic upgrade head"
alembic upgrade head

# DEMO_MODE=true wipes the database and reseeds it with a representative
# fixture set on every container start (see backend/scripts/reset_and_seed.py
# for the exact shape of the data). Default is "false" — production-style
# bring-up keeps whatever state already exists. The seeder runs *before*
# the gRPC server binds so the server never serves a half-seeded DB.
if [ "${DEMO_MODE:-false}" = "true" ]; then
    echo "library api: DEMO_MODE active — resetting tables and seeding"
    python /app/scripts/reset_and_seed.py
fi

echo "library api: starting gRPC server"
exec python -m library.main
