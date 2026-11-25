"""Primary ASGI entrypoint for the TITAN application.

The production runtime is Starlette. The older FastAPI route module remains
available for compatibility/reference, but it is not the application entrypoint.
"""
from __future__ import annotations

from .star_app import app, create_star_app

# Keep the conventional factory name for ASGI servers and integrations.
create_app = create_star_app

__all__ = ["app", "create_app", "create_star_app"]
