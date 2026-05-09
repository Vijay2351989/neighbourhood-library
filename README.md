# Neighborhood Library

> Phase 1 scaffold. The full README ships in Phase 7. See [`docs/README.md`](docs/README.md) for the spec and [`docs/phases/`](docs/phases/) for the implementation plan.

## How to run (placeholder)

```sh
docker compose up
```

That's the goal. Phase 1 scaffolds the four services as stubs; subsequent phases fill them in.

## Local observability (Phase 5.5b)

The api emits OpenTelemetry traces and structured logs by default ([Phase 5.5](docs/phases/phase-5-5-observability.md)). With no extra setup the data goes to the api container's stdout (the **console exporter**) — visible via `docker compose logs api`.

To view traces and logs in a real UI, bring up the SigNoz overlay:

```sh
docker compose --env-file .env.observability --profile observability up -d
```

Then open <http://localhost:3301>. The first-run schema migration takes ~30s; until it finishes the UI may briefly show "service starting".

**One-time schema fix (first run only).** At the SigNoz v0.144 / v0.76 release pairing we pin to, the schema migrator leaves the trace-tag enum without a `scope` value that the collector writes. After the stack is up, run once:

```sh
docker exec -i neighbourhood-library-signoz-clickhouse-1 \
    clickhouse-client --multiquery < deploy/signoz/post-migrate.sql
```

The script is idempotent; re-running is a no-op. Background and the four ALTER statements are documented inline in `deploy/signoz/post-migrate.sql`.

Fire any RPC against the api (the smoke client below works) and within a few seconds the trace shows up in SigNoz under the `library-api` service. Logs are searchable from the same UI and can be filtered by `trace_id` to pivot between signals.

The observability profile is opt-in. `docker compose up` (no profile) runs the lean four-service stack — the SigNoz containers add ~2 GB RAM that you don't want eating your laptop while you iterate on app code.

See [`docs/design/06-observability.md`](docs/design/06-observability.md) for the full architecture.

## Resilience knobs (Phase 5.6)

The api service ships with sane production defaults for Postgres-side timeouts, connection pool tuning, and a service-level retry decorator. Override any of them per environment via the listed env vars; the production defaults match the values in [`docs/phases/phase-5-6-resilience.md`](docs/phases/phase-5-6-resilience.md).

| Env var | Default | What it bounds |
|---|---|---|
| `DB_STATEMENT_TIMEOUT_MS` | `5000` | Postgres `statement_timeout` — server actually stops the query and releases locks |
| `DB_LOCK_TIMEOUT_MS` | `3000` | `lock_timeout` — surface non-deadlock lock waits as `lock_not_available` (must be `<` statement_timeout) |
| `DB_IDLE_TX_TIMEOUT_MS` | `15000` | `idle_in_transaction_session_timeout` — kills a forgotten BEGIN |
| `DB_POOL_SIZE` | `10` | Warm SQLAlchemy pool size per worker |
| `DB_MAX_OVERFLOW` | `10` | Burst overflow above pool_size; total cap = 20 in-flight per worker |
| `DB_POOL_TIMEOUT_S` | `5` | Wait-for-free-connection budget; surfaces as `RESOURCE_EXHAUSTED` |
| `DB_POOL_RECYCLE_S` | `1800` | Recycle pool entries every 30 min to evade firewall idle-kills |
| `DB_COMMAND_TIMEOUT_S` | `5` | asyncpg driver-side per-command timeout |

To surface failures faster in tests or local dev (e.g. force a lock-timeout error after 100ms instead of waiting 3s), set `DB_LOCK_TIMEOUT_MS=100` on the api service. The retry decorator on each service method catches transient errors per [the policy table](docs/phases/phase-5-6-resilience.md#per-rpc-policy-assignment); retries past the first emit a `retry.attempt` span event visible in SigNoz.
