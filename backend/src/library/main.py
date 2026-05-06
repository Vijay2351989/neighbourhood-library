"""Entry point for the Neighborhood Library gRPC server.

Phase 1 scope: bring up an ``aio.Server`` bound to ``0.0.0.0:GRPC_PORT`` with
only the standard gRPC health service registered, so that the ``api`` container's
``grpc_health_probe`` healthcheck has a real endpoint to call. Business
servicers are registered in Phase 4 once the proto contract exists.

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

logger = logging.getLogger("library.main")

# Empty string is the canonical "overall server" service name in the gRPC
# health-checking protocol. ``grpc_health_probe`` defaults to this.
_OVERALL_HEALTH_SERVICE: Final[str] = ""

# Seconds to let in-flight RPCs finish during shutdown before forcing close.
_SHUTDOWN_GRACE_SECONDS: Final[float] = 5.0


def _build_server() -> tuple[aio.Server, health.HealthServicer]:
    """Construct the asyncio gRPC server with the standard health service attached.

    Returns the server and the health servicer so the caller can flip status
    during shutdown.
    """

    server = aio.server()
    health_servicer = health.HealthServicer()
    health_pb2_grpc.add_HealthServicer_to_server(health_servicer, server)

    # Mark the overall service as SERVING. Per-service entries will be added in
    # Phase 4 alongside the LibraryService registration.
    health_servicer.set(_OVERALL_HEALTH_SERVICE, health_pb2.HealthCheckResponse.SERVING)

    # Server reflection lets tools like grpcurl discover services without a
    # local copy of the .proto. Useful for dev / debugging; cheap to ship.
    reflection.enable_server_reflection(
        (
            health_pb2.DESCRIPTOR.services_by_name["Health"].full_name,
            reflection.SERVICE_NAME,
        ),
        server,
    )

    return server, health_servicer


async def _serve() -> None:
    """Bind, start, and block until a shutdown signal arrives."""

    settings = get_settings()
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
        await server.stop(_SHUTDOWN_GRACE_SECONDS)
        logger.info("library api: stopped")


def main() -> None:
    """Module entry point — wires logging then runs the asyncio server."""

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    try:
        asyncio.run(_serve())
    except KeyboardInterrupt:
        # asyncio.run already drained _serve via the signal handler; this just
        # keeps the process exit code clean if the platform skipped the handler.
        logger.info("library api: interrupted")


if __name__ == "__main__":
    main()
