# syntax=docker/dockerfile:1.7
#
# Backend image for the Neighborhood Library gRPC service.
#
# Layout:
#   - Stage 1 (builder): pulls in `uv`, installs deps from pyproject.toml into a
#     project-local `.venv`, downloads the `grpc_health_probe` binary used by
#     the compose healthcheck.
#   - Stage 2 (runtime): minimal slim image; copies the venv, the source, and
#     the health probe binary; runs the entrypoint script.
#
# Phase 1 caveat: the Phase 1 stub server registers the standard
# `grpc.health.v1.Health` service so the healthcheck succeeds as soon as the
# process is bound. No DB connection or business logic yet.

# ---------- Stage 1: builder ----------
FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv

# `curl` is needed to fetch the grpc_health_probe binary.
# `ca-certificates` for HTTPS cert verification.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Install uv from its official standalone installer. Pinning to a known-good
# release keeps builds reproducible.
COPY --from=ghcr.io/astral-sh/uv:0.4.27 /uv /usr/local/bin/uv

WORKDIR /app

# Resolve & install deps first, before source, so `uv sync` is cached on
# pyproject changes only.
COPY pyproject.toml ./
RUN uv sync --no-install-project --no-dev

# Fetch the gRPC health probe. Pinned to a recent release.
ARG GRPC_HEALTH_PROBE_VERSION=v0.4.28
RUN set -eux; \
    arch="$(dpkg --print-architecture)"; \
    case "$arch" in \
        amd64) probe_arch="amd64" ;; \
        arm64) probe_arch="arm64" ;; \
        *) echo "unsupported arch: $arch" >&2; exit 1 ;; \
    esac; \
    curl -fsSL -o /usr/local/bin/grpc_health_probe \
        "https://github.com/grpc-ecosystem/grpc-health-probe/releases/download/${GRPC_HEALTH_PROBE_VERSION}/grpc_health_probe-linux-${probe_arch}"; \
    chmod +x /usr/local/bin/grpc_health_probe

# Now copy source and install the project itself into the venv so
# `python -m library.main` resolves. README is copied because pyproject.toml
# declares `readme = "README.md"` and hatchling validates its existence when
# building the editable install. Alembic config is copied alongside source
# so `alembic upgrade head` (run from /app at container start) finds both
# alembic.ini and the alembic/ scripts directory.
COPY README.md ./README.md
COPY src ./src
COPY alembic.ini ./alembic.ini
COPY alembic ./alembic
COPY entrypoint.sh ./entrypoint.sh
COPY scripts ./scripts

# Bring in the .proto via the named build context wired up in
# docker-compose.yml (additional_contexts.proto -> repo-root proto/). Keeping
# the .proto out of the per-service tree avoids duplication; both backend and
# frontend builds consume the same source of truth.
COPY --from=proto . /app/proto

# Generate Python gRPC stubs into src/library/generated/ before the project is
# installed into the venv — hatchling's wheel build picks up the generated
# package alongside the rest of src/library/. PATH is prepended so the
# grpcio-tools `python -m grpc_tools.protoc` invocation in gen_proto.sh
# resolves to the venv interpreter. PROTO_DIR is overridden because the
# script's default repo-relative resolution doesn't match the in-container
# layout (the proto context lands at /app/proto, not <repo>/proto).
RUN PATH="/app/.venv/bin:${PATH}" PROTO_DIR=/app/proto bash /app/scripts/gen_proto.sh

RUN uv sync --no-dev \
    && chmod +x /app/entrypoint.sh


# ---------- Stage 2: runtime ----------
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:${PATH}"

# Create an unprivileged runtime user. Owning /app keeps file perms tidy.
RUN groupadd --system --gid 1001 library \
    && useradd --system --uid 1001 --gid library --home-dir /app library

WORKDIR /app

# Copy the prebuilt venv, source, alembic config + scripts, entrypoint, and
# the health probe. Alembic needs both alembic.ini and the alembic/ directory
# at runtime to execute `alembic upgrade head` from the entrypoint. The
# scripts/ directory ships at runtime too because the entrypoint invokes
# `reset_and_seed.py` when DEMO_MODE=true and the rubric's sample-client
# walkthrough invokes `sample_client.py` via `docker compose exec`.
COPY --from=builder --chown=library:library /app/.venv /app/.venv
COPY --from=builder --chown=library:library /app/src /app/src
COPY --from=builder --chown=library:library /app/alembic.ini /app/alembic.ini
COPY --from=builder --chown=library:library /app/alembic /app/alembic
COPY --from=builder --chown=library:library /app/scripts /app/scripts
COPY --from=builder --chown=library:library /app/entrypoint.sh /app/entrypoint.sh
COPY --from=builder /usr/local/bin/grpc_health_probe /usr/local/bin/grpc_health_probe

USER library

EXPOSE 50051

ENTRYPOINT ["/app/entrypoint.sh"]
