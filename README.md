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
