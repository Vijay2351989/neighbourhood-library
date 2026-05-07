"""Entry point for the Neighborhood Library gRPC server.

Phase 4 scope: register the :class:`LibraryServicer` (the eight book/member
RPCs) alongside the standard ``grpc.health.v1.Health`` service. The health
service's per-service entry for ``library.v1.LibraryService`` is set to
``SERVING`` once the servicer is wired in, satisfying the spec's requirement
that the api healthcheck pass once the business RPCs are live.

Graceful shutdown: SIGINT / SIGTERM trigger ``server.stop(grace)`` so in-flight
RPCs get a chance to finish. The health service is flipped to ``NOT_SERVING``
before stopping so load balancers see us drain cleanly.
"""

from __future__ import annotations

import asyncio
import logging
import signal
from typing import Final

from grpc import aio
from grpc_health.v1 import health, health_pb2, health_pb2_grpc
from grpc_reflection.v1alpha import reflection

from library.config import get_settings
from library.db.engine import AsyncSessionLocal
from library.generated.library.v1 import library_pb2, library_pb2_grpc
from library.observability.interceptors import RequestContextInterceptor
from library.observability.setup import (
    TelemetryHandles,
    grpc_otel_server_interceptor,
    init_telemetry,
)
from library.servicer import LibraryServicer

logger = logging.getLogger("library.main")

# Empty string is the canonical "overall server" service name in the gRPC
# health-checking protocol. ``grpc_health_probe`` defaults to this.
_OVERALL_HEALTH_SERVICE: Final[str] = ""

# Per-service health entry name. Frontend / Envoy can probe this specifically
# to know whether the business surface is up, distinct from the overall
# server-up signal above.
_LIBRARY_SERVICE_NAME: Final[str] = (
    library_pb2.DESCRIPTOR.services_by_name["LibraryService"].full_name
)

# Seconds to let in-flight RPCs finish during shutdown before forcing close.
_SHUTDOWN_GRACE_SECONDS: Final[float] = 5.0


def _build_server() -> tuple[aio.Server, health.HealthServicer]:
    """Construct the asyncio gRPC server with health, reflection, and LibraryService.

    Returns the server and the health servicer so the caller can flip status
    during shutdown. The interceptor order matters: the OTel server
    interceptor must come first so it creates the root span before our
    request-context interceptor stamps attributes onto it.
    """

    server = aio.server(
        interceptors=[
            grpc_otel_server_interceptor(),
            RequestContextInterceptor(),
        ]
    )
    health_servicer = health.HealthServicer()
    health_pb2_grpc.add_HealthServicer_to_server(health_servicer, server)

    # Register the business servicer. AsyncSessionLocal is the lazy proxy
    # built in db/engine.py; the engine isn't actually opened until the first
    # session is taken, which lets ``alembic upgrade head`` finish first via
    # entrypoint.sh.
    library_servicer = LibraryServicer(AsyncSessionLocal)
    library_pb2_grpc.add_LibraryServiceServicer_to_server(library_servicer, server)

    # Mark both health entries as SERVING.
    health_servicer.set(_OVERALL_HEALTH_SERVICE, health_pb2.HealthCheckResponse.SERVING)
    health_servicer.set(_LIBRARY_SERVICE_NAME, health_pb2.HealthCheckResponse.SERVING)

    # Server reflection lets tools like grpcurl discover services without a
    # local copy of the .proto. Useful for dev / debugging; cheap to ship.
    reflection.enable_server_reflection(
        (
            health_pb2.DESCRIPTOR.services_by_name["Health"].full_name,
            _LIBRARY_SERVICE_NAME,
            reflection.SERVICE_NAME,
        ),
        server,
    )

    return server, health_servicer


async def _serve() -> None:
    """Bind, start, and block until a shutdown signal arrives."""

    settings = get_settings()

    # Initialize telemetry before constructing the server so the gRPC
    # auto-instrumentation hooks are active when the server is built.
    telemetry = init_telemetry()

    server, health_servicer = _build_server()

    bind_address = f"0.0.0.0:{settings.grpc_port}"
    server.add_insecure_port(bind_address)

    await server.start()
    logger.info("library api: listening on :%d", settings.grpc_port)

    shutdown_event = asyncio.Event()
    loop = asyncio.get_running_loop()

    def _request_shutdown(signum: int) -> None:
        logger.info("library api: received signal %d, beginning graceful shutdown", signum)
        shutdown_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _request_shutdown, sig)
        except NotImplementedError:
            # add_signal_handler is unavailable on Windows; fall back to default
            # signal behavior (immediate KeyboardInterrupt / process exit).
            logger.debug("signal handler for %s not installable on this platform", sig)

    try:
        await shutdown_event.wait()
    finally:
        # Drain: tell health probes we're going away, then stop the server.
        health_servicer.set(
            _OVERALL_HEALTH_SERVICE,
            health_pb2.HealthCheckResponse.NOT_SERVING,
        )
        health_servicer.set(
            _LIBRARY_SERVICE_NAME,
            health_pb2.HealthCheckResponse.NOT_SERVING,
        )
        await server.stop(_SHUTDOWN_GRACE_SECONDS)
        # Flush any pending OTLP batches (no-op when the console exporter is
        # active; matters when traces/logs are shipped out-of-process).
        telemetry.shutdown()
        logger.info("library api: stopped")


def main() -> None:
    """Module entry point — runs the asyncio server.

    Logging is configured by :func:`init_telemetry` (called inside ``_serve``)
    so that the JSON formatter is wired up consistently with the OTel logs
    pipeline. We don't call ``logging.basicConfig`` here.
    """

    try:
        asyncio.run(_serve())
    except KeyboardInterrupt:
        # asyncio.run already drained _serve via the signal handler; this just
        # keeps the process exit code clean if the platform skipped the handler.
        logger.info("library api: interrupted")


if __name__ == "__main__":
    main()
