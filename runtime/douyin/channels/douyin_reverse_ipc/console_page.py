"""Serve the Chinese AgentDesk runtime console."""

from __future__ import annotations

from pathlib import Path

from fastapi.responses import HTMLResponse, RedirectResponse

_CONSOLE_HTML = (Path(__file__).resolve().parent / "static" / "console.html").read_text(encoding="utf-8")


def register_console_routes(app) -> None:
    @app.get("/", include_in_schema=False)
    def console_root():
        return HTMLResponse(_CONSOLE_HTML)

    @app.get("/console", include_in_schema=False)
    def console_page():
        return HTMLResponse(_CONSOLE_HTML)

    @app.get("/docs", include_in_schema=False)
    def docs_redirect():
        return RedirectResponse(url="/console", status_code=302)

    @app.get("/redoc", include_in_schema=False)
    def redoc_redirect():
        return RedirectResponse(url="/console", status_code=302)
