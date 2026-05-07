# Phase 5.5b — Observability Backend (SigNoz Local Overlay)

**Status:** Approved, not yet started
**Last Updated:** 2026-05-07
**Effort:** S (~1.5–2 hrs)
**Prerequisites:** [Phase 5.5](phase-5-5-observability.md) — instrumentation must already be in place
**Blocks:** none (purely additive)

This phase plugs a **self-hosted SigNoz** instance into the existing app via Compose. After Phase 5.5, the application emits structured spans and logs through the OTel SDK; this phase gives you a local UI to view them in. The default `docker compose up` flow stays unchanged — SigNoz is gated behind a Compose **profile**, opt-in only.

---

## Goal

`docker compose --profile observability up` brings up the full app stack plus a local SigNoz instance. Every gRPC RPC fired against the api becomes a queryable trace in SigNoz UI within a few seconds; every log line shows up alongside, correlated by `trace_id` / `request.id`.

---

## Related design docs

- [design/06-observability.md §8.2](../design/06-observability.md) — SigNoz architecture decision and the env-var dual-mode setup
- [phases/phase-5-5-observability.md](phase-5-5-observability.md) — instrumentation work this phase consumes

---

## Why SigNoz, why a profile

**SigNoz** is a single self-hosted observability platform that bundles an OTel collector, ClickHouse storage, a query service, and a unified UI. Picking SigNoz means:

- One project to deploy (vs Tempo + Loki + Prometheus + Grafana + a separate collector).
- One UI for traces, logs, and (future) metrics.
- Native OTel ingestion — the collector understands our OTLP exporter without translation layers.

**Compose profile** keeps the four SigNoz services out of the default stack. Default `docker compose up` brings up postgres + api + envoy + web (and the seed profile when needed). `docker compose --profile observability up` adds the four SigNoz services on top. ~2 GB RAM on the SigNoz side; opt-in only so dev iteration isn't slowed.

---

## Scope

### In

- Four SigNoz services declared in `docker-compose.yml` under `profiles: ["observability"]`:
  - `signoz-clickhouse` — single ClickHouse for all signals; named volume for persistence across `compose down`.
  - `signoz-query-service` — query API the frontend talks to.
  - `signoz-frontend` — UI on host port `3301`.
  - `signoz-otel-collector` — OTLP receiver on `4317` (gRPC).
- SigNoz collector config at `deploy/signoz/collector.yaml`. Receives OTLP, exports to ClickHouse, applies basic span/log batching.
- Updated `OTEL_*` env vars on the `api` service so they take effect when the profile is active. Achieved with Compose's `${VAR:-default}` interpolation:
  - `OTEL_TRACES_EXPORTER=${OTEL_TRACES_EXPORTER:-console}` → defaults to `console`, set to `otlp` via `.env.observability` or shell when the profile is active.
  - Same pattern for `OTEL_LOGS_EXPORTER` and `OTEL_EXPORTER_OTLP_ENDPOINT`.
- A `.env.observability` template at repo root that sets the override values and is sourced explicitly: `docker compose --env-file .env.observability --profile observability up`. Documented in `README.md` polish (Phase 7).
- One-page section in `README.md` (or a child file under `docs/reference/`) explaining: what SigNoz is, how to bring it up, where the UI is, what to do if a trace doesn't appear.

### Out

- **Frontend instrumentation.** Phase 6 owns its client-side OTel setup. SigNoz ingests browser-side traces fine; this phase just makes sure the backend is wired.
- **Metrics.** Deferred from Phase 5.5 and from this phase. The SDK init and SigNoz both support metrics; turning them on is a one-line env-var flip plus the `MeterProvider` config.
- **Alerting / dashboards.** SigNoz supports both. Out of scope here; can be a separate polish task or part of Phase 7.
- **Production deployment of SigNoz.** This phase is local-only. A real cluster would run SigNoz Cloud or a separately provisioned SigNoz install.
- **PII redaction at the collector.** Phase 5.5 enforces no-PII in instrumentation by code review. A collector-side redaction processor is a future hardening; not blocking.

---

## Deliverables

### New files

- `deploy/signoz/collector.yaml` — SigNoz collector config: OTLP receivers (gRPC + HTTP), ClickHouse exporter pointing at `signoz-clickhouse`, batch + memory-limiter processors, modest sampling defaults.
- `.env.observability` — template env file with:
  ```
  OTEL_TRACES_EXPORTER=otlp
  OTEL_LOGS_EXPORTER=otlp
  OTEL_EXPORTER_OTLP_ENDPOINT=http://signoz-otel-collector:4317
  ```
- (Optional) `docs/reference/observability-runbook.md` — short ops guide: start, stop, reset state, common pitfalls. Lower priority.

### Modified files

- `docker-compose.yml` — four new services under `profiles: ["observability"]`, one named volume (`signoz-clickhouse`), updated env-var stanza on the `api` service to use Compose interpolation defaults.
- `README.md` — short "Local observability" subsection pointing at the run command and `localhost:3301`.

---

## Acceptance criteria

- `docker compose up` (no profile) brings up only the original four services. SigNoz containers do not start. `OTEL_TRACES_EXPORTER` resolves to `console` and the api logs span JSON to stdout, exactly as in Phase 5.5.
- `docker compose --env-file .env.observability --profile observability up` brings up postgres + api + envoy + web + the four SigNoz services. All reach `running` state; no restart loops.
- `localhost:3301` returns the SigNoz UI within ~30 seconds of compose-up.
- After firing one `BorrowBook` RPC against the running api, the trace shows up in SigNoz under the `library-api` service within a few seconds, with all expected manual span names (`borrow.transaction`, `borrow.pick_copy`, etc.) and at least one `loan.created` event visible on the trace.
- Log lines from the same RPC appear in SigNoz's Logs view, filterable by `trace_id` from the trace detail page (the cross-signal pivot works).
- Stopping with `docker compose down` cleanly shuts down everything; SigNoz data persists in the named volume across restarts; `docker compose down -v` discards it.
- All Phase 5 + Phase 5.5 tests still pass; this phase doesn't touch app code.

---

## Notes & risks

- **Image versions.** SigNoz versions evolve quickly and the four images need to be compatible with each other. Pin to a known-good release set in `docker-compose.yml` (e.g., `signoz/query-service:0.46.0`, `signoz/frontend:0.46.0`, `signoz/signoz-otel-collector:0.92.0`, `clickhouse/clickhouse-server:24.1.2-alpine`). Bumping is its own task; don't track `:latest`.
- **ClickHouse memory.** ClickHouse at idle is ~1.5 GB resident. On constrained dev machines, leaving the observability profile running can affect other workloads. Documented in the runbook.
- **First-run schema migration.** SigNoz's query service initializes its ClickHouse schema on first boot. The `signoz-query-service` container takes ~30s before serving; the frontend will show a temporary "service starting" screen during that window. Not a bug.
- **Collector config drift.** SigNoz's bundled collector config evolves between versions. Keeping our `deploy/signoz/collector.yaml` minimal (receivers + ClickHouse exporter + standard processors) reduces upgrade pain.
- **Cross-stack networking.** The api container resolves `signoz-otel-collector` via Compose's default network DNS. Both services must be on the same Compose network (default behavior; flag if any future override breaks it).
- **Failure mode.** If SigNoz isn't running but `OTEL_TRACES_EXPORTER=otlp` is set, the OTLP gRPC export fails. The OTel SDK logs warnings but the api remains healthy — the failure is non-fatal by design. Verifies the loose-coupling decision from Phase 5.5.
- **Reset.** `docker compose --profile observability down -v` wipes ClickHouse storage. Useful when collector schema changes between SigNoz versions.

---

## Cross-references

- Architectural spec: [design/06-observability.md](../design/06-observability.md)
- Sister phase that owns the instrumentation: [phases/phase-5-5-observability.md](phase-5-5-observability.md)
- Compose patterns referenced: [design/05-infrastructure.md](../design/05-infrastructure.md)
