# backend

Python 3.12 gRPC service for the Neighborhood Library. See the root [`README.md`](../README.md) for how to run the whole stack via Docker Compose, and [`docs/design/03-backend.md`](../docs/design/03-backend.md) for the target module layout.

Phase 1 ships only the stub server (`library.main`) plus health check wiring; persistence, the proto contract, and business RPCs land in subsequent phases.
