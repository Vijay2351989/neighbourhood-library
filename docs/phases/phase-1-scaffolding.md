# Phase 1 — Repo & Infra Scaffolding

**Status:** Approved, not yet started
**Last Updated:** 2026-05-05
**Effort:** M (~4 hrs)
**Prerequisites:** none
**Blocks:** all subsequent phases

---

## Goal

Have a repo skeleton where `docker compose up` brings up *something* (even if it's just Postgres + a hello-world API) and developer tooling is wired up.

---

## Related design docs

- [design/05-infrastructure.md](../design/05-infrastructure.md) — Envoy config and Compose topology
- [design/03-backend.md](../design/03-backend.md) — backend layout (the empty version of which we scaffold here)
- [design/04-frontend.md](../design/04-frontend.md) — frontend layout (likewise)

---

## Scope

### In
- Repo layout: `backend/`, `frontend/`, `proto/`, `deploy/envoy/`, `docs/` (already exists), `docker-compose.yml`, `.gitignore`, root `README.md` (skeleton).
- Backend: `pyproject.toml` with `uv`, a stub `library.main` that starts a gRPC server on 50051 with no services registered, Dockerfile.
- Frontend: `npx create-next-app@latest` baseline with Tailwind, TypeScript, App Router. Dockerfile.
- Envoy: `envoy.yaml` from [design/05-infrastructure.md §1](../design/05-infrastructure.md#1-envoy-configuration).
- Compose: all four services with healthchecks. Postgres uses the `library` DB.
- `.gitignore` covers `generated/`, `node_modules/`, `__pycache__/`, `.venv/`, etc.

### Out
- Any business logic.
- Any database tables (Phase 2).
- Any proto-generated code (Phase 3).

---

## Deliverables

- Working `docker-compose.yml`.
- Two Dockerfiles (`backend/Dockerfile`, `frontend/Dockerfile`).
- Bare-bones `deploy/envoy/envoy.yaml`.
- Stub Python `src/library/main.py` that logs "listening on :50051".
- Stub Next.js home page that says "Neighborhood Library".
- Root `README.md` with a 3-line "how to run" placeholder (the full README ships in [Phase 7](phase-7-polish.md)).

---

## Acceptance criteria

- `docker compose up` exits non-error and stays running.
- `curl http://localhost:3000` returns the Next.js stub page.
- `curl http://localhost:8080` returns an Envoy 404 (proves Envoy is reachable; no upstream routes registered yet).
- `psql -h localhost -U postgres library -c '\dt'` returns "no relations" without error.
- `docker compose down -v && docker compose up` reproduces the same clean state.

---

## Notes & risks

- **Docker Desktop memory.** Compose with four services is fine on default settings, but flag if developers see slow startup.
- **Port conflicts.** 5432, 8080, 50051, 3000 — document in the README which ports the project owns.
- **gRPC health probe binary.** We add `grpc_health_probe` to the backend image so the `api` healthcheck has something to call. The actual `grpc.health.v1.Health` service won't be implemented until Phase 4, so the healthcheck will fail until then — acceptable for Phase 1; document this clearly in compose comments.
