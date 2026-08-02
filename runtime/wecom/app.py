"""FastAPI app for the local Enterprise WeChat webhook."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .adapter import WecomWebhookAdapter


def create_app(evidence_path: str | Path) -> FastAPI:
    adapter = WecomWebhookAdapter(Path(evidence_path))
    app = FastAPI(
        title="AgentDesk Enterprise WeChat Webhook",
        description="本地企微消息回调适配器：WeCom -> SessionEvent。",
        version="0.1.0",
        docs_url="/docs",
        redoc_url=None,
    )
    app.state.adapter = adapter

    @app.get("/webhooks/wecom/ping", tags=["system"])
    def ping() -> dict[str, Any]:
        return {"ok": True, "data": {"channel": "wecom", "status": "ok"}}

    @app.post("/webhooks/wecom/messages", tags=["webhook"])
    async def message(request: Request) -> JSONResponse:
        try:
            payload = await request.json()
            result = adapter.handle(payload)
            return JSONResponse({"ok": True, "data": result})
        except (TypeError, ValueError) as exc:
            return JSONResponse(
                {"ok": False, "error": {"code": str(exc) or type(exc).__name__}},
                status_code=400,
            )

    return app
