# Phase 3 — Protobuf Contract & Codegen

**Status:** Approved, not yet started
**Last Updated:** 2026-05-05
**Effort:** M (~3 hrs — `buf` tooling has gotchas)
**Prerequisites:** [Phase 1](phase-1-scaffolding.md)
**Blocks:** [Phase 4](phase-4-backend-crud.md), [Phase 5](phase-5-borrow-return-fines.md), [Phase 6](phase-6-frontend-mvp.md)

---

## Goal

The `.proto` is finalized and both backend and frontend can generate working stubs from it.

---

## Related design docs

- [design/02-api-contract.md](../design/02-api-contract.md) — the full `.proto` content
- [design/03-backend.md](../design/03-backend.md) — backend codegen target (`src/library/generated/`)
- [design/04-frontend.md](../design/04-frontend.md) — frontend codegen choice (Connect)

---

## Scope

### In
- `proto/library/v1/library.proto` with the full content from [design/02-api-contract.md](../design/02-api-contract.md).
- `backend/scripts/gen_proto.sh` invoking `python -m grpc_tools.protoc`.
- `frontend/buf.gen.yaml` configuring `@bufbuild/protoc-gen-es` and `@connectrpc/protoc-gen-connect-es`.
- Both codegen steps wired into the respective Docker builds.
- A trivial smoke check: backend imports `library.v1.library_pb2`; frontend imports the generated TS module without error.

### Out
- Any service or RPC implementation (Phases 4 and 5).

---

## Deliverables

- `proto/library/v1/library.proto`.
- `backend/scripts/gen_proto.sh`.
- `frontend/buf.gen.yaml`.
- Updated Dockerfiles to run codegen during build.
- `.gitignore` updated to exclude `backend/src/library/generated/` and `frontend/src/generated/`.

---

## Acceptance criteria

- `bash backend/scripts/gen_proto.sh` produces non-empty `_pb2.py` and `_pb2_grpc.py` files.
- `cd frontend && npx buf generate` produces non-empty `library_pb.ts` and `library_connect.ts`.
- Backend container build succeeds and the import works inside the container.
- Frontend container build succeeds.
- A trivial backend script that creates an empty `BorrowBookRequest()` runs without error.
- A trivial frontend TypeScript snippet that imports `LibraryService` from the generated module type-checks.

---

## Notes & risks

- **Proto path resolution.** `grpc_tools.protoc` is picky about `--proto_path` and import statements. Test the codegen command both inside the container and locally.
- **Connect vs grpc-web.** We're using Connect-generated stubs but speaking gRPC-Web on the wire (Envoy expects gRPC-Web). Connect supports both protocols; the choice is made when constructing the transport (`createGrpcWebTransport` in [design/04-frontend.md §3](../design/04-frontend.md#3-data-fetching-pattern)).
- **`buf` version churn.** Pin `@bufbuild/protoc-gen-es` and `@connectrpc/protoc-gen-connect-es` to specific versions. Document them in `buf.gen.yaml`.
- **Repo-root `proto/` consumed by both.** Backend Dockerfile copies `proto/` from the build context; frontend Dockerfile likewise. Don't duplicate the `.proto`.
