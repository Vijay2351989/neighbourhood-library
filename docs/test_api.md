# Manual API Test Commands

**Status:** Living document — append commands as new phases land.
**Last Updated:** 2026-05-05
**Parent:** [README.md](README.md)

A runbook for manually verifying the running stack at every layer. Three paths reach the same gRPC server; each proves a different part of the system is working.

---

## The three paths

```
Path A: grpcurl  ──native gRPC──▶  API :50051
        (bypasses Envoy entirely; tests the Python server in isolation)

Path B: grpcurl  ──native gRPC──▶  Envoy :8080  ──native gRPC──▶  API :50051
        (Envoy passthrough mode; grpc_web filter is a no-op for application/grpc+proto)

Path C: buf curl ──gRPC-Web────▶  Envoy :8080  ──native gRPC──▶  API :50051
        (real gRPC-Web translation; the path the React app uses)
```

If A works but B fails → Envoy config issue.
If B works but C fails → `grpc_web` filter or CORS issue.
If A fails → Python server issue (Envoy is innocent).

---

## Prerequisites

Install the test tools once:

```bash
brew install grpcurl
brew install bufbuild/buf/buf
```

Bring the stack up:

```bash
cd /Users/jai/projects/neighborhood-library
docker compose up -d
docker compose ps    # all four services should be "healthy"
```

---

## Path A — direct to API (native gRPC, bypassing Envoy)

```bash
# List available services (proves reflection works)
grpcurl -plaintext localhost:50051 list

# Expected output:
#   grpc.health.v1.Health
#   grpc.reflection.v1alpha.ServerReflection

# Call the health check
grpcurl -plaintext localhost:50051 grpc.health.v1.Health/Check

# Expected output:
#   { "status": "SERVING" }
```

**What this proves:**
- Python gRPC server is running on `:50051`
- Server reflection is enabled (`grpcio-reflection` dep installed)
- Standard `grpc.health.v1.Health` service is registered

**Useful when:** debugging the backend in isolation, integration tests, sanity check before involving Envoy.

---

## Path B — through Envoy as native gRPC (passthrough)

```bash
# List services through Envoy
grpcurl -plaintext localhost:8080 list

# Expected output (same as Path A — Envoy proxies reflection too):
#   grpc.health.v1.Health
#   grpc.reflection.v1alpha.ServerReflection

# Health check through Envoy
grpcurl -plaintext localhost:8080 grpc.health.v1.Health/Check

# Expected output:
#   { "status": "SERVING" }
```

**What this proves (in addition to Path A):**
- Envoy listener accepts connections on `:8080`
- Routing matches (`prefix: "/"` → cluster `library_grpc`)
- `STRICT_DNS` cluster resolves the `api` service name
- `http2_protocol_options: {}` correctly forces HTTP/2 to the upstream

**Note:** Envoy's `grpc_web` filter is a **no-op** for this request because the content-type is `application/grpc+proto` (native gRPC, not gRPC-Web). The filter chain runs but doesn't transform anything — the request flows through `grpc_web → cors → router → upstream`.

---

## Path C — through Envoy as gRPC-Web (the real browser path)

This is the one that actually exercises the `grpc_web` filter's translation work.

### C.1 — `buf curl` with reflection (recommended)

```bash
buf curl --protocol=grpcweb --http2-prior-knowledge \
  --reflect --reflect-protocol=grpc-v1alpha \
  http://localhost:8080/grpc.health.v1.Health/Check

# Expected output:
#   {"status": "SERVING"}
```

The `--http2-prior-knowledge` flag is required because `buf curl --reflect` refuses to use reflection over plain HTTP unless you explicitly opt in. Envoy's listener (`codec_type: AUTO`) accepts both HTTP/1.1 and HTTP/2, so HTTP/2-prior-knowledge works fine.

### C.2 — raw `curl` (no extra tools, exercises HTTP/1.1 path)

For `Health/Check` with empty service field, the gRPC-Web request body is exactly **5 bytes of zeros** — frame flag 0, length 0, no protobuf payload.

```bash
printf '\x00\x00\x00\x00\x00' | curl -i -X POST \
  -H "Content-Type: application/grpc-web+proto" \
  -H "X-Grpc-Web: 1" \
  -H "Accept: application/grpc-web+proto" \
  --data-binary @- \
  http://localhost:8080/grpc.health.v1.Health/Check \
  --output /tmp/health.raw

# View the response body bytes
xxd /tmp/health.raw | tail -5
```

**Decoded response** (you should see something like this in `xxd`):

| Bytes | Meaning |
|---|---|
| `00 00 00 00 02` | Message frame: flag `0x00`, length `2` |
| `08 01` | Protobuf: field 1 (varint) = `1` → `HealthStatus.SERVING` |
| `80 00 00 00 0F` | Trailer frame: flag `0x80`, length `15` |
| `67 72 70 63 2d 73 74 61 74 75 73 3a 30 0d 0a` | ASCII: `"grpc-status:0\r\n"` |

**What this proves (the full filter chain):**
- `grpc_web` filter rewrote `Content-Type: application/grpc-web+proto` → `application/grpc+proto`
- `grpc_web` filter added `te: trailers` on the way to upstream
- Python server processed the native gRPC `Health/Check`
- `grpc_web` filter took the HTTP/2 trailers from the upstream response and synthesized the `0x80`-flagged trailer frame in the response body
- `cors` filter added `Access-Control-Allow-Origin` and `Access-Control-Expose-Headers`

If the body has the `0x00`-prefixed message frame followed by a `0x80`-prefixed trailer frame containing `grpc-status:0` — your gRPC-Web translation is provably correct end-to-end.

---

## Bonus — CORS preflight verification

The browser sends an `OPTIONS` preflight before any non-simple gRPC-Web call. This proves the `cors` filter short-circuits it correctly:

```bash
curl -i -X OPTIONS http://localhost:8080/grpc.health.v1.Health/Check \
  -H "Origin: http://localhost:3000" \
  -H "Access-Control-Request-Method: POST" \
  -H "Access-Control-Request-Headers: content-type, x-grpc-web"

# Expected response headers:
#   HTTP/1.1 200 OK
#   access-control-allow-origin: *
#   access-control-allow-methods: GET, PUT, DELETE, POST, OPTIONS
#   access-control-allow-headers: ...,x-grpc-web,...
#   access-control-max-age: 1728000
```

**What this proves:**
- `cors` filter short-circuits OPTIONS preflights without forwarding upstream
- Allow-headers list includes the gRPC-Web specific headers (`x-grpc-web`, `grpc-timeout`, etc.)
- Browser will be permitted to send the actual gRPC-Web POST after preflight succeeds

---

## Bonus — Envoy admin endpoints

```bash
# Liveness check (used by docker-compose healthcheck)
curl -s http://localhost:9901/ready
# Expected: LIVE

# Stats (filter for our cluster)
curl -s "http://localhost:9901/stats?filter=library_grpc"

# Live config dump
curl -s http://localhost:9901/config_dump | jq .

# Cluster membership
curl -s http://localhost:9901/clusters
```

Useful for debugging when the proxy paths fail and you need to see what Envoy thinks is happening.

---

## Quick sanity check — single command

If you only run one verification, run this:

```bash
grpcurl -plaintext localhost:8080 grpc.health.v1.Health/Check
```

Returns `{ "status": "SERVING" }` → routing through Envoy works. Most useful single test for Phase 1.

---

## Common failure modes

| Symptom | Likely cause | Fix |
|---|---|---|
| `grpcurl ... server does not support the reflection API` | Reflection not enabled on the server | Verify `grpcio-reflection` is in `pyproject.toml` and `reflection.enable_server_reflection(...)` is called in `main.py` |
| `curl: (52) Empty reply from server` | Envoy crashed or not listening | `docker compose logs envoy` |
| `HTTP/1.1 503 Service Unavailable` with `no_healthy_upstream` | Envoy can't reach `api:50051` | Check `docker compose ps`; api healthcheck failing |
| `HTTP/1.1 415 Unsupported Media Type` | Wrong Content-Type | Must be exactly `application/grpc-web+proto` |
| `grpc-status: 12` (UNIMPLEMENTED) | Method not registered on server | Server may be missing the service; rebuild api |
| `grpc-status: 14` (UNAVAILABLE) | Network/connectivity to upstream | Check container network and api healthcheck |
| `buf curl --reflect` complains about plain HTTP | buf safety guard | Add `--http2-prior-knowledge` flag |
| Browser console: "CORS error" | `cors` filter misconfigured | Verify `allow_origin`, `allow_headers`, `expose_headers` in `envoy.yaml` |

---

## Future phases — append commands here as they land

### Phase 2 — Schema & Migrations
Coming: `psql` commands to verify schema is created.

### Phase 3 — Protobuf Contract
Coming: `grpcurl localhost:50051 list` will start showing `library.v1.LibraryService`.

### Phase 4 — Backend CRUD
Coming: `grpcurl ... library.v1.LibraryService/CreateBook` examples.

### Phase 5 — Borrow & Return
Coming: end-to-end borrow → list → return scenario via `grpcurl`.

### Phase 6 — Frontend MVP
Coming: browser DevTools console snippets for calling the Connect client directly.

### Phase 7 — Polish
Coming: `python backend/scripts/sample_client.py` walk-through.
