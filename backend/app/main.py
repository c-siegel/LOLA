"""
This is the main application file that creates and configures the FastAPI application.
It's the starting point for the backend server.

What is this file for?
This file:
- Creates the FastAPI application instance (create_app)
- Configures CORS (Cross-Origin Resource Sharing) and the security-headers middleware
- Registers all API route handlers (collected in app/api_router.py) under /api/v1
- Provides a health check endpoint

How it works:
1. When you run the server (e.g., `uvicorn app.main:app`), this file is executed
2. The FastAPI app instance is created
3. Middleware and routes are registered
4. The server starts listening for requests
"""

import asyncio
from contextlib import asynccontextmanager, suppress

import anyio.to_thread
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api_router import api_router
from app.core.config import settings
from app.core.errors import DomainError, domain_error_handler
from app.core.middleware import add_security_headers
from app.core.robots import robots_txt
from app.core.urls import API_PREFIX
from app.tasks.knowledge_cleanup import knowledge_cleanup_loop, mark_interrupted_evaluation_runs
from app.tasks.retention import retention_loop, run_retention_purge


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Run startup tasks: raise the thread-pool ceiling, run the data-retention cleanup once
    immediately, then keep re-running it periodically for as long as the process is up."""
    # anyio's own default (40) caps how many worker threads every sync route (almost all of
    # them) and every /messages/stream response body may use at once, across the whole
    # process — sized for a handful of simultaneous users, not a class of ~30 each holding a
    # thread for their own request. Settable only from inside a running event loop, hence here.
    anyio.to_thread.current_default_thread_limiter().total_tokens = settings.request_thread_pool_size
    run_retention_purge()
    if settings.evaluation_enabled:
        # Runs that were in progress when the backend stopped can't resume (see runner.py).
        mark_interrupted_evaluation_runs()
    retention_task = asyncio.create_task(retention_loop())
    # Only with the knowledge service: sends deletes it missed while it was unreachable.
    knowledge_task = asyncio.create_task(knowledge_cleanup_loop()) if settings.rag_enabled else None
    try:
        yield
    finally:
        for task in (retention_task, knowledge_task):
            if task is None:
                continue
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task


def health():
    """
    Health check endpoint.

    This endpoint is used to check if the server is running and responding.
    It's commonly used by:
    - Load balancers to check server health
    - Monitoring systems to detect outages
    - Deployment scripts to verify the server started successfully
    """
    return {"status": "ok"}


def create_app() -> FastAPI:
    """Build the FastAPI application: middleware, error handling, and every route."""
    app = FastAPI(title="LOLA API", lifespan=lifespan)

    # Configure CORS (Cross-Origin Resource Sharing) middleware
    # CORS allows the frontend (running on a different domain/port) to make requests to this backend
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,  # Which domains can make requests
        allow_credentials=True,  # Allow cookies to be sent with requests
        allow_methods=["*"],  # Allow all HTTP methods (GET, POST, PUT, DELETE, etc.)
        allow_headers=["*"],  # Allow all HTTP headers
    )
    app.middleware("http")(add_security_headers)

    # Services raise DomainError instead of HTTPException; rendered identically (see core/errors.py).
    app.add_exception_handler(DomainError, domain_error_handler)

    app.include_router(api_router, prefix=API_PREFIX)
    # Reachable both at the bare path (what a load balancer or uptime check tries first, and what
    # the Docker healthcheck calls) and next to every other route under the API prefix.
    app.get("/health")(health)
    app.get(f"{API_PREFIX}/health", include_in_schema=False)(health)
    # At the root, where crawlers look for it; Caddy and the Vite dev server pass it through.
    app.get("/robots.txt", include_in_schema=False)(robots_txt)
    return app


# The application instance uvicorn serves (`uvicorn app.main:app`).
app = create_app()
