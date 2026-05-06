# Infrastructure — Envoy & Docker Compose

**Status:** Complete
**Last Updated:** 2026-05-05
**Parent:** [README.md](../README.md)
**Implemented in:** [Phase 1](../phases/phase-1-scaffolding.md)

How the four services come up together: Envoy proxy config, Docker Compose topology, healthchecks, env vars, and the seed profile.

---

## 1. Envoy configuration

Envoy translates gRPC-Web (browser) into native gRPC (server). Without it the browser can't call our Python server. Envoy also handles CORS for the dev environment where Next.js runs on a different port from Envoy.

The config registers the `envoy.filters.http.grpc_web` filter and the `envoy.filters.http.cors` filter, then routes all paths to a single upstream cluster pointing at the Python server's gRPC port.

```yaml
# deploy/envoy/envoy.yaml
admin:
  address:
    socket_address: { address: 0.0.0.0, port_value: 9901 }

static_resources:
  listeners:
    - name: listener_0
      address:
        socket_address: { address: 0.0.0.0, port_value: 8080 }
      filter_chains:
        - filters:
            - name: envoy.filters.network.http_connection_manager
              typed_config:
                "@type": type.googleapis.com/envoy.extensions.filters.network.http_connection_manager.v3.HttpConnectionManager
                stat_prefix: ingress_http
                codec_type: AUTO
                route_config:
                  name: local_route
                  virtual_hosts:
                    - name: library_service
                      domains: ["*"]
                      cors:
                        allow_origin_string_match:
                          - prefix: "*"
                        allow_methods: GET, PUT, DELETE, POST, OPTIONS
                        allow_headers: keep-alive,user-agent,cache-control,content-type,content-transfer-encoding,x-accept-content-transfer-encoding,x-accept-response-streaming,x-user-agent,x-grpc-web,grpc-timeout,connect-protocol-version,connect-timeout-ms
                        max_age: "1728000"
                        expose_headers: grpc-status,grpc-message
                      routes:
                        - match: { prefix: "/" }
                          route:
                            cluster: library_grpc
                            timeout: 0s
                http_filters:
                  - name: envoy.filters.http.grpc_web
                    typed_config:
                      "@type": type.googleapis.com/envoy.extensions.filters.http.grpc_web.v3.GrpcWeb
                  - name: envoy.filters.http.cors
                    typed_config:
                      "@type": type.googleapis.com/envoy.extensions.filters.http.cors.v3.Cors
                  - name: envoy.filters.http.router
                    typed_config:
                      "@type": type.googleapis.com/envoy.extensions.filters.http.router.v3.Router

  clusters:
    - name: library_grpc
      type: STRICT_DNS
      connect_timeout: 5s
      http2_protocol_options: {}
      load_assignment:
        cluster_name: library_grpc
        endpoints:
          - lb_endpoints:
              - endpoint:
                  address:
                    socket_address: { address: api, port_value: 50051 }
```

In Compose, `address: api` resolves to the Python server container by service name.

---

## 2. Docker Compose topology

`docker-compose.yml` at repo root:

| Service | Image / build | Port (host:container) | Depends on | Notes |
|---|---|---|---|---|
| `postgres` | `postgres:16-alpine` | `5432:5432` | — | Healthcheck: `pg_isready`. Volume: `pgdata:/var/lib/postgresql/data`. Env: `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB=library`. |
| `api` | `./backend` (Dockerfile) | `50051:50051` | postgres (healthy) | Entrypoint: `alembic upgrade head && python -m library.main`. Env: `DATABASE_URL`, `GRPC_PORT=50051`, `DEFAULT_LOAN_DAYS=14`, `FINE_GRACE_DAYS=14`, `FINE_PER_DAY_CENTS=25`, `FINE_CAP_CENTS=2000`. |
| `envoy` | `envoyproxy/envoy:v1.31-latest` | `8080:8080`, `9901:9901` | api | Mounts `deploy/envoy/envoy.yaml:/etc/envoy/envoy.yaml`. |
| `web` | `./frontend` (Dockerfile) | `3000:3000` | envoy | Env: `NEXT_PUBLIC_API_BASE_URL=http://localhost:8080`. Runs `next dev` in dev profile, `next start` in prod profile. |
| `seed` | `./backend` (same image as `api`) | — | api (healthy) | Optional one-shot service in a `seed` profile: runs `python scripts/seed.py` then exits. Activate with `docker compose --profile seed up`. |

---

## 3. Migrations on startup

The `api` container's entrypoint script:

```sh
#!/bin/sh
set -e
alembic upgrade head
exec python -m library.main
```

Migrations are idempotent — re-running on every container start is safe and removes a class of "did you remember to migrate" errors.

---

## 4. Healthchecks

- `postgres`: `pg_isready -U $POSTGRES_USER`
- `api`: a tiny gRPC health-check probe (we'll use `grpc_health_probe` binary baked into the image, hitting the standard `grpc.health.v1.Health/Check`).
- `envoy`: HTTP GET to `:9901/ready`.

`web` doesn't expose a healthcheck; if Next.js fails to start, that's visible in logs and the user can't load the page.

---

## 5. Seed data

`scripts/seed.py` uses the gRPC client to (1) create ~20 books with varying copy counts, (2) create ~10 members, (3) borrow a handful of books to give the loans table some content. Running through the public API rather than direct SQL has two benefits: it's a free smoke test of the whole stack, and the seed script doubles as an executable example of how to use the API.

For loans with historic dates (overdue, returned-late) the seed script may fall back to direct DB writes for `borrowed_at`/`due_at` overrides — documented as a caveat in the script header.

---

## Cross-references

- Backend entrypoint that the `api` service runs: [design/03-backend.md](03-backend.md)
- Frontend that the `web` service runs: [design/04-frontend.md](04-frontend.md)
- Schema that migrations create: [design/01-database.md](01-database.md)
- Phase that wires this all together: [phases/phase-1-scaffolding.md](../phases/phase-1-scaffolding.md)
- Seed script details: [phases/phase-7-polish.md](../phases/phase-7-polish.md)
