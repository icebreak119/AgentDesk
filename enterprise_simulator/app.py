"""FastAPI application exposing the local enterprise refund system."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from .store import EnterpriseBusinessStore


def create_app(evidence_path: str | Path) -> FastAPI:
    store = EnterpriseBusinessStore(Path(evidence_path))
    app = FastAPI(
        title="AgentDesk Enterprise Business Simulator",
        description="可替换企业订单与退款系统的本地 HTTP 模拟服务。",
        version="0.1.0",
        docs_url="/docs",
        redoc_url=None,
    )
    app.state.store = store

    @app.exception_handler(HTTPException)
    async def business_http_error(_request: Request, exc: HTTPException) -> JSONResponse:
        detail = exc.detail if isinstance(exc.detail, dict) else {"code": str(exc.detail)}
        return JSONResponse(
            {"ok": False, "error": {"code": str(detail.get("code") or "enterprise_request_failed")}},
            status_code=exc.status_code,
        )

    def ok(data: Any) -> dict[str, Any]:
        return {"ok": True, "data": data}

    def fail(exc: Exception) -> None:
        code = str(exc.args[0] if isinstance(exc, KeyError) and exc.args else exc) or type(exc).__name__
        status = 404 if code in {"order_not_found", "operation_not_found"} else 409 if code == "idempotency_conflict" else 400
        raise HTTPException(status_code=status, detail={"code": code}) from exc

    @app.get("/enterprise/ping", tags=["system"])
    def ping() -> dict[str, Any]:
        return ok({"service": "enterprise-business-simulator", "status": "ok"})

    @app.get("/enterprise/orders/{order_id}", tags=["orders"])
    def order(order_id: str, profile_id: str = Query("d6a26b9e-demo")) -> dict[str, Any]:
        try:
            return ok(store.query_order(profile_id, order_id))
        except (KeyError, ValueError) as exc:
            fail(exc)
        raise AssertionError("unreachable")

    @app.post("/enterprise/refunds", tags=["refunds"])
    async def request_refund(request: Request) -> dict[str, Any]:
        try:
            return ok(store.apply_refund(await request.json()))
        except (KeyError, TypeError, ValueError) as exc:
            fail(exc)
        raise AssertionError("unreachable")

    @app.post("/enterprise/refunds/{operation_id}/execute", tags=["refunds"])
    async def execute_refund(operation_id: str, request: Request) -> dict[str, Any]:
        try:
            body = await request.json()
            return ok(store.execute_refund(
                operation_id,
                profile_id=str(body.get("profile_id") or ""),
                idempotency_key=str(body.get("idempotency_key") or ""),
                approval_token=str(body.get("approval_token") or ""),
            ))
        except (KeyError, TypeError, ValueError) as exc:
            fail(exc)
        raise AssertionError("unreachable")

    @app.get("/enterprise/operations/{operation_id}", tags=["operations"])
    def operation(operation_id: str, profile_id: str = Query(...)) -> dict[str, Any]:
        try:
            return ok(store.get_operation(operation_id, profile_id))
        except (KeyError, TypeError, ValueError) as exc:
            fail(exc)
        raise AssertionError("unreachable")

    @app.post("/enterprise/refunds/{operation_id}/rollback", tags=["refunds"])
    async def rollback_refund(operation_id: str, request: Request) -> dict[str, Any]:
        try:
            body = await request.json()
            return ok(store.rollback_refund(
                operation_id,
                profile_id=str(body.get("profile_id") or ""),
                idempotency_key=str(body.get("idempotency_key") or ""),
                approval_token=str(body.get("approval_token") or ""),
            ))
        except (KeyError, TypeError, ValueError) as exc:
            fail(exc)
        raise AssertionError("unreachable")

    @app.get("/enterprise/evidence", tags=["operations"])
    def evidence(limit: int = Query(100, ge=1, le=500)) -> dict[str, Any]:
        records = []
        if store.evidence_path.is_file():
            for raw in store.evidence_path.read_text(encoding="utf-8").splitlines()[-limit:]:
                if raw.strip():
                    records.append(__import__("json").loads(raw))
        return ok(records)

    return app
