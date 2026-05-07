# Phase 5.5 — Observability Instrumentation

**Status:** Approved, not yet started
**Last Updated:** 2026-05-07
**Effort:** M (~5–6 hrs)
**Prerequisites:** [Phase 5](phase-5-borrow-return-fines.md)
**Blocks:** [Phase 5.5b](phase-5-5b-observability-backend.md), [Phase 6](phase-6-frontend-mvp.md), [Phase 7](phase-7-polish.md)
**Pairs with:** [Phase 5.5b](phase-5-5b-observability-backend.md) — adds the SigNoz local overlay

This phase is an addition to the original take-home plan, scoped between Phases 5 and 6 to land the production-grade telemetry posture before frontend work begins (the request-id and trace propagation make Phase 6 debugging materially easier).

---

## Goal

Instrument the backend with OpenTelemetry so every RPC produces a structured trace, every log line carries trace context, and key business events are queryable. The instrumentation is finalized in code; the destination is the OTel console exporter for now (real backends — Tempo, Loki, Grafana — are deferred to a follow-up phase).

---

## Related design docs

- [design/06-observability.md](../design/06-observability.md) — the architectural spec that this phase implements

---

## Scope

### In

- **OpenTelemetry SDK init** at server startup with `TracerProvider` + `LoggerProvider` configured.
- **Auto-instrumentation** for `grpc.aio` (server side), `sqlalchemy`, `asyncpg`. Spans for every RPC and SQL query land for free.
- **Manual spans + events** at the seven business hotspots from `design/06-observability.md` §5:
  - `borrow.validate`, `borrow.transaction`, `borrow.pick_copy`, `borrow.build_response`
  - `return.transaction`, `return.build_response`
  - `list_loans`, `list_loans.build_response`
  - `fines.aggregate`
  - `books.reconcile_copies`
  - Closing events: `loan.created`, `loan.contention`, `loan.returned`, `loan.return_rejected`, `copy_picked`, `copies.reconciled`, `copies.reconciliation_rejected`, `fines.computed`, `member.fetched`, `book.created`, `member.created`, `member.updated`, `member.email_collision`, `list.returned`, `member_loans.returned`.
- **gRPC server interceptor** (`library/observability/interceptors.py`) that:
  - Generates `request.id = uuid4()` per RPC, stamps it on the root span and a `contextvars.ContextVar`.
  - Emits one INFO access-log line per RPC at end-of-call (`method`, `status`, `duration_ms`, `peer`, `request.id`).
  - Extracts incoming `traceparent` metadata so frontend traces (Phase 6) can chain into backend traces.
- **Structured JSON logging** via OTel's `LoggingHandler` attached to the root logger. Existing `logger.X(...)` calls continue to work and now emit JSON with `trace_id` / `span_id` / `request.id` populated automatically.
- **Error span hooks in `errors.map_domain_errors`** — `set_status(ERROR)` + `record_exception(...)` so trace UIs show errored spans red.
- **Console exporter** as the default OTLP destination, configurable via `OTEL_EXPORTER_OTLP_ENDPOINT` env var. Switching to a real collector is one env-var change.
- **PII review** — code review pass to confirm no member emails / names / addresses appear in any new span attribute or log field.

### Out

- **OTel Collector + Tempo + Loki + Grafana in compose.** Deferred. Spec calls for it as a follow-up; this phase ships only the instrumentation.
- **Metrics** (Prometheus exporter, request/latency histograms, DB pool gauges). Deferred. The SDK init is structured so a `MeterProvider` can be added without re-instrumentation.
- **Frontend instrumentation.** Phase 6 owns its client-side OTel setup; this phase makes the backend ready to receive a propagated trace context.
- **Alerting / SLO definitions.** Depend on a metrics backend.

---

## Deliverables

### New files

- `backend/src/library/observability/__init__.py` — empty package init.
- `backend/src/library/observability/setup.py` — `init_telemetry(settings)`: builds the `TracerProvider`, attaches the OTLP/console span exporter, configures the `LoggerProvider`, sets the global tracer/logger providers, returns shutdown hooks for the server's drain path.
- `backend/src/library/observability/interceptors.py` — `RequestContextInterceptor(grpc.aio.ServerInterceptor)`: per-RPC `request.id`, contextvar stamping, access-log emission, optional incoming-traceparent extraction.
- `backend/src/library/observability/logging_config.py` — JSON formatter, contextvar-reading filter, redaction utility, `configure_logging(level)` helper.

### Modified files

- `backend/src/library/main.py` — call `init_telemetry()` before `aio.server()`, register the interceptor, call shutdown hooks in the drain path.
- `backend/src/library/errors.py` — `set_status` / `record_exception` on the active span in both the `DomainError` and `Exception` branches of `map_domain_errors`.
- `backend/src/library/services/loan_service.py` — manual spans + events listed in §5.1, §5.2, §5.3, §5.6 of the design doc.
- `backend/src/library/services/book_service.py` — manual span/event in `update_book` for `books.reconcile_copies`; closing `book.created` on Create.
- `backend/src/library/services/member_service.py` — `fines.aggregate` span around the `sum_member_fines` call; closing events.
- `backend/src/library/repositories/loans.py` — `borrow.pick_copy` span around the `FOR UPDATE SKIP LOCKED` query; `loan.contention` event when no row is locked.
- `backend/pyproject.toml` — new deps: `opentelemetry-sdk`, `opentelemetry-exporter-otlp-proto-grpc`, `opentelemetry-instrumentation-grpc`, `opentelemetry-instrumentation-sqlalchemy`, `opentelemetry-instrumentation-asyncpg`. Pinned to a single minor version family to avoid coordination breakage.
- `docker-compose.yml` — add the full standard OTel env-var set on the `api` service so behavior is tunable without rebuilds. Phase 5.5 defaults are below; Phase 5.5b switches `OTEL_*_EXPORTER` from `console` to `otlp` and points `OTEL_EXPORTER_OTLP_ENDPOINT` at the SigNoz collector.

  | Variable | Phase 5.5 value |
  |---|---|
  | `OTEL_SERVICE_NAME` | `library-api` |
  | `OTEL_RESOURCE_ATTRIBUTES` | `service.namespace=library,deployment.environment=local` |
  | `OTEL_EXPORTER_OTLP_ENDPOINT` | unset (console fallback) |
  | `OTEL_EXPORTER_OTLP_PROTOCOL` | `grpc` |
  | `OTEL_TRACES_EXPORTER` | `console` |
  | `OTEL_LOGS_EXPORTER` | `console` |
  | `OTEL_METRICS_EXPORTER` | `none` (metrics deferred) |
  | `OTEL_PROPAGATORS` | `tracecontext,baggage` |
  | `OTEL_LOG_LEVEL` | `info` |

  The dual-mode setup (5.5 console-default vs 5.5b SigNoz) is achieved without code branches — only env-var values change.

### New tests

- `backend/tests/integration/test_observability.py` — wires a custom `InMemorySpanExporter` into the test setup and asserts:
  - Borrow happy path produces a trace with the expected span tree (root + 4 manual children + ≥3 SQL children) and emits a `loan.created` event with `loan_id`, `copy_id`, `due_at`.
  - Borrow no-copies-available emits `loan.contention` and the root span is `StatusCode.ERROR`.
  - Return happy path emits `loan.returned` with the right `fine_cents`/`was_overdue`/`days_late` attributes.
  - Every RPC-rooted span carries `request.id` matching the access-log line emitted for that call.
  - PII smoke: pull all attributes from all spans; assert none of them contain a member email, name, address, or full-text book title (substring scan against the test fixtures' values).

---

## Acceptance criteria

- All Phase 4 + Phase 5 tests still pass (`pytest backend/tests/`).
- New Phase 5.5 tests pass (`pytest backend/tests/integration/test_observability.py`).
- Running `docker compose up -d postgres api` and exercising one borrow + return through a Python stub produces console-exporter output containing:
  - One root span per RPC with `rpc.method` and `request.id` attributes
  - The seven manual span names from the design doc
  - Events `loan.created` and `loan.returned` with no PII in attributes
- Setting `OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317` (with no collector running) does not crash the app — it logs an exporter warning and the app remains healthy. (Connection failures are non-fatal.)
- Test suite runtime regresses by less than 10% relative to Phase 5's `pytest backend/tests/` baseline (~7s).

---

## Notes & risks

- **Logger handler ordering.** OTel's `LoggingHandler` and the JSON formatter both attach to the root logger. Verify the formatter still applies when records flow through the OTel handler; if not, build a small `OTelJsonHandler` that does both.
- **`grpcio-reflection` traffic creates noise.** Reflection requests are RPCs too — they'll get traced. Cheap noise locally, easily filtered by `rpc.method` once a collector is in place. Option for now: skip the interceptor's access-log emission for `grpc.reflection.v1alpha.ServerReflection` calls.
- **Span text bloat.** Long SQL statements can balloon `db.statement`. Truncate to 500 chars in the auto-instrumentation config.
- **Per-call overhead.** OTel adds sub-millisecond instrumentation cost per RPC. Watch the test-suite runtime — if it regresses past 10%, profile and consider increasing the BatchSpanProcessor's queue size.
- **Backwards compat.** The `LibraryServicer.__init__` signature gains no new required arg (telemetry is global state initialized once at server boot); existing test fixtures continue to work without changes.
- **Deferred backend stack.** Adding Tempo / Loki / Grafana to compose is a clean, additive next phase (likely Phase 5.5b or part of Phase 7 polish). The OTLP envelope means *no application code changes* when that lands.

---

## Cross-references

- Architectural spec: [design/06-observability.md](../design/06-observability.md)
- Error decorator that gains span hooks: [design/03-backend.md](../design/03-backend.md)
- Per-RPC contracts referenced by manual spans: [design/02-api-contract.md](../design/02-api-contract.md)
- Phase that benefits next from this work: [phases/phase-6-frontend-mvp.md](phase-6-frontend-mvp.md)
