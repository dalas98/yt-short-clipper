"""Shared API backend and HTTP server for YT Short Clipper."""

from api.backend import ClipperBackend
from api.http_server import ClipperApiServer, ClipperJobManager, serve_api
from api.openapi import build_openapi_spec, build_swagger_ui_html

__all__ = [
    "ClipperBackend",
    "ClipperApiServer",
    "ClipperJobManager",
    "build_openapi_spec",
    "build_swagger_ui_html",
    "serve_api",
]
