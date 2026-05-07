# Observability Design

**Status:** Approved, not yet implemented
**Last Updated:** 2026-05-07
**Parent:** [README.md](../README.md)
**Implemented in:** [Phase 5.5](../phases/phase-5-5-observability.md)
**Used by:** [Phase 6](../phases/phase-6-frontend-mvp.md), [Phase 7](../phases/phase-7-polish.md)

How the backend exposes its internal behavior for operators and developers: structured logs, distributed traces, and (deferred) metrics. The unifying framework is OpenTelemetry. The application emits via the OTel SDK and is decoupled from any specific backend by exporting OTLP — so the local dev viewer (or none at all) and a production aggregator are interchangeable without app changes.

---

## 1. Goals

1. **Debug a production issue in under five minutes.** Filter by request id; pivot from a log line to its trace; find the slow span; see the SQL parameters; read the error stack — without grep-stitching plaintext logs from multiple machines.
2. **Make business events queryable.** "How many borrows succeeded yesterday?" "What's the p99 latency of `BorrowBook` when contention is high?" answered without ad-hoc SQL or log scraping.
3. **Treat the app as backend-agnostic.** Same code runs against console output (dev), an OTel Collector (production), or any future destination, with config-only changes.
4. **Survive container rebuilds and host failure.** Telemetry that gets reset every `docker compose build` is observability theater. The pipeline must outlive the api container's lifecycle.

---

## 2. Non-goals

- **No metrics for now.** The spec focuses on logs + traces. Metrics (Prometheus exporter, Grafana dashboards) are deferred to a follow-up; the OTel SDK is being installed in a way that lets metrics be added without re-instrumentation.
- **No local viewer stack** (Tempo/Loki/Grafana in compose) — yet. This phase ships the instrumentation; backends are config. The "console exporter" will be the dev default; switching to OTLP→Collector→{Tempo,Loki} is a future, additive change.
- **No alerting / SLO definitions.** Out of scope; depends on a metrics backend.
- **No frontend telemetry.** The browser-side trace context propagation is set up so Phase 6 can adopt it cleanly, but Phase 6 is responsible for client-side instrumentation.
- **No PII in telemetry.** Member emails, names, addresses, and full request/response payloads are excluded. IDs only.

---

## 3. Architecture

```
                              (Phase 5.5 default)           (Phase 5.5b — SigNoz overlay)
                                      │                                    │
   library_servicer / loan_service    ▼                                    ▼
                  │                console                       SigNoz OTel Collector
                  │              exporter                                  │
   logger.info()──┴──► OTel SDK ──┤                                        ▼
                                  │                                  ClickHouse
                                  │                                        │
                                  └──► OTLP/gRPC ──────────────────────────┘
                                                                           │
                                                                           ▼
                                                          SigNoz Query Service + UI (:3301)
```

Three key decisions in this picture:

**Decision A — OpenTelemetry as the unifying SDK.** Logs, traces, and metrics flow through one SDK with one config. The signals share context — every log line in a request can carry the active `trace_id` / `span_id` so that a log-line click in any modern viewer pivots into the matching trace. The alternative (separate `python-json-logger` + custom tracing + Prometheus client) means three configs, three deps, three places that have to agree on context propagation. Not worth it.

**Decision B — SigNoz as the self-hosted backend.** SigNoz bundles the OTel collector + ClickHouse storage + query API + UI into one project. All three signals (traces, logs, metrics-when-added) land in the same store and are queryable from the same UI. The alternative — Tempo + Loki + Prometheus + Grafana + a separate collector — is more idiomatic for cloud-native shops but is four projects to manage and three Grafana datasources to glue. We pick SigNoz for the "one platform, one UI" simplicity. Trade-off: ClickHouse at idle is ~1.5 GB RAM (real cost), and we're tied to SigNoz's roadmap.

**Decision C — Console exporter for Phase 5.5; SigNoz overlay for Phase 5.5b.** The instrumentation is the engineering decision; the destination is operations. By exporting OTLP from day one and gating SigNoz behind a Compose profile, the default `docker compose up` stays lean (postgres + api + envoy + web). Developers opt into `docker compose --profile observability up` when they want to view traces/logs in the SigNoz UI. Production deployments swap `OTEL_EXPORTER_OTLP_ENDPOINT` to point at the platform's collector — no application code changes either way.

---

## 4. The three signals

### 4.1 Traces

A **trace** is a tree of **spans** describing one logical operation as it flows through the system. The root span is the inbound RPC; child spans are the work done to service it. Each span has a name, start/end time, status, and attributes.

Three sources of spans in this codebase:

| Source | Origin | Coverage |
|---|---|---|
| **Auto from gRPC instrumentation** | `opentelemetry-instrumentation-grpc` | One root span per RPC, with `rpc.system`, `rpc.service`, `rpc.method`, `rpc.grpc.status_code`, peer address. |
| **Auto from SQLAlchemy + asyncpg** | `opentelemetry-instrumentation-sqlalchemy`, `opentelemetry-instrumentation-asyncpg` | One span per SQL statement, with `db.system`, `db.operation`, `db.statement` (SQL text, parameters redacted). |
| **Manual** | Hand-added in service / repo code | Domain-aware spans wrapping business decision points; named events at moments worth marking. |

Auto-instrumentation gets us a usable trace tree with zero code changes. Manual instrumentation adds the **meaning** auto-instrumentation can't infer. The combination is the deliverable — auto tells you "the SELECT FOR UPDATE took 8ms," manual tells you "that was the contention check, and it happened in the borrow flow for book_id=42, member_id=7."

Detailed per-RPC trace specifications live in §5.

### 4.2 Logs

Python's stdlib `logging` is retained as the developer-facing API — `logger.info(...)`, `logger.exception(...)`, etc. — so the existing call sites in `errors.py`, `main.py`, and `db/engine.py` continue to work. What changes:

- **Format becomes JSON.** One JSON object per line. Standard fields: `ts`, `level`, `logger`, `msg`, `service.name`. Plus context fields populated from `contextvars` (see §6.1) and OTel context (`trace_id`, `span_id`, `request.id`).
- **Records also flow through the OTel logs SDK** so they ride the same OTLP pipeline as traces. The OTel `LoggingHandler` is attached to the root logger; existing `logger.X(...)` calls auto-pick up the active span context.
- **Discipline on levels** (see §7.1).
- **PII redaction** (see §6.4).

Output goes to **stderr** (the runtime captures it; 12-factor) AND simultaneously exports via OTLP for trace correlation. The stderr fallback ensures logs are observable even if the OTLP destination is unreachable.

### 4.3 Metrics (deferred)

Out of scope for Phase 5.5 to keep the change set tight. The OTel SDK init in §3 is configured with `MeterProvider` registered, so metrics can be added later without re-init. When added, the natural set will be:

- `library.grpc.requests_total{method,status}` — counter
- `library.grpc.request_duration_seconds{method}` — histogram
- `library.db.pool.size` — gauge from SQLAlchemy
- `library.db.pool.checked_out` — gauge
- `library.loans.borrow_contention_total` — counter (driven by the `loan.contention` event)

Tracked as a follow-up for whichever phase opts in.

---

## 5. Instrumentation plan (per RPC)

The plan is organized by RPC. Each entry gives:

- The auto spans you'll see for free
- The **manual spans** to add (italicized) and where in the code they go
- The **events** to emit at decision points
- Notable attributes
- Questions the trace can answer

OTel attribute naming follows the [semantic conventions](https://opentelemetry.io/docs/specs/semconv/) where applicable; domain attributes use the `library.*` prefix.

### 5.1 BorrowBook (the most informative trace)

```
gRPC: BorrowBook                                                      [auto]
  attrs: rpc.method, rpc.grpc.status_code, peer.*, library.book_id,
         library.member_id, request.id
│
├─ borrow.validate                                                    [manual]
│
├─ borrow.transaction                                                 [manual]
│  ├─ db: SELECT FROM members WHERE id = $1                           [auto]
│  ├─ db: SELECT FROM books WHERE id = $1                             [auto]
│  ├─ borrow.pick_copy                                                [manual]
│  │   attrs: library.book_id
│  │   └─ db: SELECT FROM book_copies … FOR UPDATE SKIP LOCKED        [auto]
│  │   event: copy_picked (copy_id=…)
│  │
│  ├─ db: INSERT INTO loans                                           [auto]
│  ├─ db: UPDATE book_copies SET status = 'BORROWED'                  [auto]
│  └─ event: loan.created (loan_id, copy_id, due_at)
│
└─ borrow.build_response                                              [manual]
```

**Manual span locations:** `services/loan_service.py:borrow_book` (the outer three) and `repositories/loans.py:borrow` (the `pick_copy` inner span).

**Notable events:**
- `copy_picked` — a copy was successfully locked.
- `loan.created` — emitted on success. The dashboard event.
- `loan.contention` — emitted when no copy could be locked. Powers a "contention rate" panel.

**Questions answered:** Where did the 47ms go? Was the `FOR UPDATE` slow? Did concurrent borrows serialize on the lock or proceed in parallel (compare span overlap across traces)?

### 5.2 ReturnBook (captures the snapshot moment)

```
gRPC: ReturnBook                                                      [auto]
  attrs: library.loan_id, request.id
│
├─ return.transaction                                                 [manual]
│  ├─ db: SELECT FROM loans WHERE id = $1 FOR UPDATE                  [auto]
│  │   (if returned_at IS NOT NULL)
│  │   event: loan.return_rejected (reason=already_returned)
│  │
│  ├─ db: SELECT FROM book_copies WHERE id = $1                       [auto]
│  ├─ db: UPDATE loans SET returned_at = NOW()                        [auto]
│  ├─ db: UPDATE book_copies SET status = 'AVAILABLE'                 [auto]
│  ├─ event: loan.returned (loan_id, fine_cents, was_overdue,
│  │                        days_late)
│  └─ db: SELECT loans + joins (the response re-fetch)                [auto]
│
└─ return.build_response                                              [manual]
   attrs: library.fine_cents
```

**Why `loan.returned` matters:** the moment `returned_at` is set is the moment the fine "snapshots" — every future read computes the same `fine_cents` because `returned_at` is now fixed. A query for `loan.returned` events with `fine_cents > 0` is your "fines collected today" panel without writing any SQL.

### 5.3 ListLoans (the filtered query)

```
gRPC: ListLoans                                                       [auto]
  attrs: library.list.page_size, library.list.offset,
         library.list.filter
│
├─ list_loans                                                         [manual]
│  ├─ db: SELECT COUNT(*) … WHERE filter                              [auto]
│  ├─ db: SELECT loans + joins WHERE filter LIMIT … OFFSET …          [auto]
│  └─ event: list.returned (returned_count, total_count)
│
└─ list_loans.build_response                                          [manual]
   attrs: library.list.returned_count
```

`library.list.filter` lets you partition latency by filter value. The `HAS_FINE` filter has the most expensive predicate (the `LEAST/GREATEST` arithmetic); knowing how often it's used vs the cheap `ACTIVE` filter informs whether to add a partial index.

### 5.4 GetMember (the fines aggregate path)

```
gRPC: GetMember                                                       [auto]
  attrs: library.member_id
│
├─ db: SELECT FROM members WHERE id = $1                              [auto]
│
├─ fines.aggregate                                                    [manual]
│  attrs: library.member_id
│  └─ db: SELECT COALESCE(SUM(LEAST(...))) FROM loans …               [auto]
│  event: fines.computed (member_id, total_cents)
│
└─ event: member.fetched (id, has_outstanding_fines)
```

**Why wrap the aggregate in a manual span:** it's the query worth watching as data grows. Auto-instrumentation gives SQL duration; the manual span lets us pin `library.member_id` directly so you can answer "who has the slowest fine aggregate" without parsing SQL.

### 5.5 UpdateBook (the copy-reconciliation safeguard)

```
gRPC: UpdateBook                                                      [auto]
│
├─ books.update                                                       [manual]
│
├─ books.reconcile_copies                                             [manual]
│  attrs: library.book_id, library.target_copies, library.current_total,
│         library.current_available, library.delta
│  │   (on rejection)
│  │   event: copies.reconciliation_rejected
│  │           (untouchable=count, reason="borrowed_or_lost")
│  │   (on success, if delta != 0)
│  │   event: copies.reconciled (delta)
│  │
│  └─ db: SELECT counts; INSERT/DELETE rows                           [auto]
│
└─ books.build_response                                               [manual]
```

Lets you see how often librarians shrink vs grow copy counts and how often the safeguard fires.

### 5.6 GetMemberLoans, ListBooks, ListMembers, GetBook, CreateBook, CreateMember, UpdateMember

Lighter instrumentation — auto spans + a single closing event:

| RPC | Closing event |
|---|---|
| `GetMemberLoans` | `member_loans.returned (member_id, count)` |
| `ListBooks` | `list.returned (returned_count, total_count, has_search)` |
| `ListMembers` | `list.returned (returned_count, total_count, has_search)` |
| `GetBook` | (none — auto spans suffice) |
| `CreateBook` | `book.created (book_id, copies_count)` |
| `CreateMember` | `member.created (member_id)` |
| `UpdateMember` | `member.updated (member_id)` (on email-collision rejection: `member.email_collision`) |

---

## 6. Cross-cutting concerns

### 6.1 Request context propagation

A single gRPC server interceptor at boot:

1. **Generates `request.id = uuid4()`** at the start of every RPC.
2. **Stamps it on the root span** as a span attribute.
3. **Sets a `contextvars.ContextVar`** so logs and downstream code can read it without threading it through signatures.
4. **Extracts incoming `traceparent` metadata** if present (for Phase 6 — frontend traces will chain into backend traces over the same trace_id).
5. **Emits one access log line per RPC** at end-of-call: `method`, `status_code`, `duration_ms`, `peer`, `request.id`. INFO level. The single most useful log entry for ops.

The interceptor lives in `library/observability/interceptors.py`. Logs and spans automatically get the request id once it's on the contextvar; no individual call site has to remember to pass it.

### 6.2 Error semantics on spans

Where errors flow through `errors.map_domain_errors`:

```python
except DomainError as exc:
    span = trace.get_current_span()
    span.set_status(StatusCode.ERROR, str(exc))
    span.record_exception(exc)
    ...
except Exception:
    span = trace.get_current_span()
    span.set_status(StatusCode.ERROR, "internal error")
    span.record_exception(exc)
    ...
```

Effect: trace UIs render the span red; `record_exception` adds a span event with class name + stack trace; you can filter "show all errored traces" without log parsing.

### 6.3 Sampling policy

| Environment | Trace sampling | Why |
|---|---|---|
| **Local dev** | 100% (always-on) | Free; we want every trace |
| **Production (future)** | Tail-based: 100% of errors, 5–10% of successes | Cheap on storage, full visibility on the failures that matter |

The sampling decision lives in the OTel Collector config, not the SDK. App always emits all spans; collector drops sampled-out traces. (Trade-off: more network bytes than head-based sampling. Acceptable for the scale we're sizing for.)

For Phase 5.5 specifically: the console exporter prints every span. There's no sampler. When the collector is added, this section becomes the source-of-truth for the production rule.

### 6.4 PII handling

**Excluded** from spans, span events, and log records:

- Member names
- Member email addresses
- Member phone / address
- Book titles in mass-collected data (see note below)
- SQL parameter values (use `db.statement` with `?` placeholders, not literal values)
- Full request / response protobuf payloads

**Included** is fine:

- Resource IDs (`book_id`, `member_id`, `loan_id`, `copy_id`)
- Counts (`total_copies`, `available_copies`, `fine_cents`)
- Status / state values (`OK`, `ALREADY_EXISTS`, `BORROWED`, `LOAN_FILTER_ACTIVE`)
- Timestamps

**Note on book titles.** Borderline. Public information on its own, but logging every borrow's title creates an inadvertent reading-history dataset. Default to *omitting* book titles from `loan.created` / `loan.returned` events; add temporarily in a debugging session if needed.

**Implementation:** PII redaction lives in the OTel Collector config (a span processor that drops or scrubs disallowed attributes), **not** the application. The app emits whatever is convenient; the collector enforces policy. This matches industry practice — keeps the app simple, lets a single place define what's allowed, makes audits easier.

For Phase 5.5 (no collector), the rule is enforced by code review: don't put names/emails on spans in the first place. The instrumentation plan in §5 already follows this rule.

---

## 7. Logging policy

### 7.1 Log levels

| Level | What goes there | Example |
|---|---|---|
| `DEBUG` | SQL queries, retry attempts, internal state transitions, anything verbose | Default off in production |
| `INFO` | RPC access lines, lifecycle events (server start/stop, migrations), business events worth recording | Per-RPC access log; `library api: listening on :50051` |
| `WARNING` | Recoverable degradation, retryable conditions, deprecated input | "Slow query took 1.3s"; "client used deprecated field X" |
| `ERROR` | Operations that returned `INTERNAL` to the client; the catch-all path in `map_domain_errors` | The existing `logger.exception("uncaught error in %s", ...)` |
| `CRITICAL` | Paging-worthy: DB pool exhausted, config invalid at startup | Reserved; rare |

Default level in production is **INFO**. Local dev defaults to **DEBUG** when developing, INFO when running smoke tests.

### 7.2 Structured fields

Every log line is a JSON object. Required fields:

```json
{
  "ts": "2026-05-07T14:23:11.192Z",
  "level": "INFO",
  "logger": "library.servicer",
  "msg": "human-readable message",
  "service.name": "library-api"
}
```

Plus, when present in context:

```json
{
  "trace_id": "4bf92f3577b34da6a3ce929d0e0e4736",
  "span_id": "00f067aa0ba902b7",
  "request.id": "abc-123",
  "rpc.method": "BorrowBook"
}
```

The `trace_id` / `span_id` are populated automatically by OTel's logging instrumentation; the rest come from the contextvar set by the gRPC interceptor.

### 7.3 PII redaction

Never log raw email addresses, names, or addresses. If a log message *needs* to reference a member, log the `member_id`. For debugging cases where you genuinely need an email, redact: `j***@example.com` (a small `redact_email(s)` utility lives next to the logging config).

This is policy, enforced by code review. There's no automatic scrubber on the logging path; the OTel Collector (when added) will strip any PII that leaks through.

---

## 8. Backend strategy

The application is permanently OTLP-aware. The destination behind the OTLP endpoint is a deployment-time decision, configured via environment variables (see §8.4) — no code changes are needed to switch between the console exporter, SigNoz, or any future cloud aggregator.

### 8.1 Phase 5.5 default: console exporter

When `OTEL_TRACES_EXPORTER=console` and `OTEL_LOGS_EXPORTER=console` (the Phase 5.5 default), the SDK serializes every span and log record to stdout. Output looks like:

```
{"name": "BorrowBook", "context": {"trace_id": "0x4bf...", "span_id": "0x00f..."},
 "kind": "SpanKind.SERVER", "attributes": {...}, "events": [...], "status": ...}
```

Useful for verifying instrumentation; not useful for browsing traces over time. Intentional — Phase 5.5 ships the *shape* of telemetry, not a viewer.

### 8.2 Phase 5.5b: SigNoz local overlay

A separate phase ([phases/phase-5-5b-observability-backend.md](../phases/phase-5-5b-observability-backend.md)) brings up SigNoz as a Compose profile so developers can opt into a local viewer without bloating the default stack.

**Why SigNoz specifically:** single project, single ClickHouse, single UI for traces + logs + (future) metrics. See Decision B in §3 for the trade-off.

```yaml
# docker-compose.yml — under profile "observability"

services:
  signoz-clickhouse:
    image: clickhouse/clickhouse-server:24.1.2-alpine
    profiles: ["observability"]
    volumes: [signoz-clickhouse:/var/lib/clickhouse]

  signoz-query-service:
    image: signoz/query-service:0.46.0
    profiles: ["observability"]
    depends_on: [signoz-clickhouse]

  signoz-frontend:
    image: signoz/frontend:0.46.0
    profiles: ["observability"]
    ports: ["3301:3301"]      # SigNoz UI
    depends_on: [signoz-query-service]

  signoz-otel-collector:
    image: signoz/signoz-otel-collector:0.92.0
    profiles: ["observability"]
    ports: ["4317:4317"]      # OTLP gRPC
    volumes:
      - ./deploy/signoz/collector.yaml:/etc/otel-collector-config.yaml:ro
    depends_on: [signoz-clickhouse]

volumes:
  signoz-clickhouse:
```

Default usage stays unchanged:

```bash
docker compose up                            # postgres + api + envoy + web only
```

When traces/logs are wanted:

```bash
docker compose --profile observability up    # adds the four SigNoz services
# UI at http://localhost:3301
```

The `api` service has its `OTEL_EXPORTER_OTLP_ENDPOINT` set to `http://signoz-otel-collector:4317`. With the profile inactive, that name doesn't resolve and the OTLP exporter fails silently while the console fallback keeps logs visible. With the profile active, telemetry flows to SigNoz.

### 8.3 Production

In a real cloud deployment the cluster typically runs its own collector (or a SaaS like SigNoz Cloud, Datadog, Honeycomb). The app's config changes one env var:

```
OTEL_EXPORTER_OTLP_ENDPOINT=http://<cluster-collector>:4317
OTEL_RESOURCE_ATTRIBUTES=service.namespace=library,deployment.environment=prod
```

No application code changes. That's the payoff for separating instrumentation from destinations.

### 8.4 Standard OTel environment variables

The SDK reads these directly at init — no application code touches them. Exposing them in compose lets you tune behavior per environment without rebuilds.

| Variable | Purpose | Phase 5.5 default | Phase 5.5b (`--profile observability`) |
|---|---|---|---|
| `OTEL_SERVICE_NAME` | Identifier in trace/log views | `library-api` | `library-api` |
| `OTEL_RESOURCE_ATTRIBUTES` | Static labels on every span/log | `service.namespace=library,deployment.environment=local` | same |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | Where to send OTLP data | unset (falls back to console) | `http://signoz-otel-collector:4317` |
| `OTEL_EXPORTER_OTLP_PROTOCOL` | `grpc` or `http/protobuf` | `grpc` | `grpc` |
| `OTEL_TRACES_EXPORTER` | `otlp`, `console`, or `none` | `console` | `otlp` |
| `OTEL_LOGS_EXPORTER` | `otlp`, `console`, or `none` | `console` | `otlp` |
| `OTEL_METRICS_EXPORTER` | `otlp`, `console`, or `none` | `none` (deferred) | `none` (deferred) |
| `OTEL_PROPAGATORS` | Trace context formats accepted on incoming requests | `tracecontext,baggage` | same |
| `OTEL_LOG_LEVEL` | OTel SDK's own debug logging | `info` | `info` |

The dual-mode setup (Phase 5.5 vs 5.5b) is achieved without code branches: the SDK is *always* configured the same way. Phase 5.5 and 5.5b differ only in environment-variable values, which is exactly the layering OTel was designed for.

---

## 9. Open questions (to confirm at implementation)

- **Logger handler ordering.** OTel's `LoggingHandler` and the JSON formatter both attach to the root logger. Need to verify the formatter still applies when records flow through the OTel handler. Spike during implementation; fall back to a custom OTel handler if needed.
- **Span text size.** Long SQL statements (the fine aggregate is ~200 chars) can bloat span attributes. Consider truncating `db.statement` to 500 chars in the OTel config.
- **`grpcio-reflection` interaction.** Reflection requests are gRPC RPCs too — they'll get traced. Cheap noise; option to filter by `rpc.method` in the collector.
- **Cost in dev.** OTel auto-instrumentation does add per-call overhead. Should be sub-millisecond per RPC, but worth measuring on the existing test suite once instrumented.

---

## Cross-references

- The phase that implements this: [phases/phase-5-5-observability.md](../phases/phase-5-5-observability.md)
- gRPC framework wiring: [design/03-backend.md](03-backend.md)
- Per-RPC contracts referenced by §5: [design/02-api-contract.md](02-api-contract.md)
- Compose changes (deferred backend stack): [design/05-infrastructure.md](05-infrastructure.md)
