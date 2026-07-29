"""HTTP localhost API wrapping ReverseSupervisor (127.0.0.1 only)."""

from __future__ import annotations

from typing import Any, Optional

from fastapi import FastAPI, Query, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, ConfigDict, Field

from channels.douyin_reverse_ipc.account_slots import AccountSlotManager
from channels.douyin_reverse_ipc.console_page import register_console_routes
from channels.douyin_reverse_ipc.errors import RpcError
from channels.douyin_reverse_ipc import profile_service
from channels.douyin_reverse_ipc.supervisor import ReverseSupervisor


class SendTextBody(BaseModel):
    model_config = ConfigDict(title="发送文本请求体")

    text: str = Field("", title="消息正文")
    conversation_id: str = Field("", title="会话编号")
    peer_uid: str = Field("", title="对方用户编号")
    client_msg_id: str = Field("", title="客户端消息编号")
    is_ai_reply: bool = Field(False, title="是否 AI 回复")


class SendEmojiBody(BaseModel):
    model_config = ConfigDict(title="发送表情请求体")

    emoji_url: str = Field("", title="表情地址")
    emoji_name: str = Field("", title="表情名称")
    conversation_id: str = Field("", title="会话编号")
    peer_uid: str = Field("", title="对方用户编号")
    client_msg_id: str = Field("", title="客户端消息编号")


class SendImageBody(BaseModel):
    model_config = ConfigDict(title="发送图片请求体")

    image_path: str = Field("", title="图片路径")
    conversation_id: str = Field("", title="会话编号")
    peer_uid: str = Field("", title="对方用户编号")
    client_msg_id: str = Field("", title="客户端消息编号")


def create_app(
    db_path: str,
    *,
    slots: Optional[AccountSlotManager] = None,
) -> FastAPI:
    supervisor = ReverseSupervisor(db_path, slots=slots)
    app = FastAPI(
        title="AgentDesk 抖音渠道 Runtime",
        description=(
            "AgentDesk 渠道工具层（MCP 等价 HTTP 接口）。"
            "支持多账号托管、私信发送、会话与消息查询。"
            "中文操作台：http://127.0.0.1:8765/console"
        ),
        version="1.0.0",
        docs_url=None,
        redoc_url=None,
        openapi_tags=[
            {"name": "系统", "description": "健康检查与运行信息"},
            {"name": "账号托管", "description": "账号启停与凭证管理"},
            {"name": "消息发送", "description": "文本、表情、图片私信"},
            {"name": "会话查询", "description": "会话列表与历史消息"},
        ],
    )
    register_console_routes(app)
    app.state.supervisor = supervisor

    def _ok(data: Any) -> dict[str, Any]:
        return {"ok": True, "data": data}

    def _run(method: str, params: dict[str, Any]) -> dict[str, Any]:
        return supervisor.dispatch(method, params)

    def _avatar_response(
        account_code: str,
        *,
        conversation_id: str = "",
        self_profile: bool = False,
    ):
        kind, value = profile_service.resolve_avatar_target(
            supervisor.db_path,
            account_code,
            conversation_id=conversation_id,
            self_profile=self_profile,
        )
        if kind == "file":
            return FileResponse(value)
        return RedirectResponse(value)

    @app.exception_handler(RpcError)
    async def _rpc_error_handler(_request: Request, exc: RpcError):
        return JSONResponse(
            status_code=400,
            content={"ok": False, "error": {"code": exc.code, "message": exc.message}},
        )

    @app.exception_handler(Exception)
    async def _unhandled_error_handler(_request: Request, exc: Exception):
        return JSONResponse(
            status_code=500,
            content={
                "ok": False,
                "error": {
                    "code": "internal",
                    "message": str(exc) or type(exc).__name__,
                },
            },
        )

    @app.get("/ping", summary="健康检查", tags=["系统"])
    def ping():
        return _ok(_run("ping", {}))

    @app.get("/db_path", summary="凭证库路径", tags=["系统"])
    def db_path_route():
        return _ok(_run("get_db_path", {}))

    @app.get("/accounts", summary="账号列表", tags=["账号托管"])
    def list_accounts():
        return _ok(_run("list_accounts", {}))

    @app.get("/accounts/{account_code}/status", summary="账号状态", tags=["账号托管"])
    def account_status(account_code: str):
        return _ok(_run("get_account_status", {"account_code": account_code}))

    @app.get("/accounts/{account_code}/profile", summary="托管账号资料", tags=["账号托管"])
    def account_profile(account_code: str):
        return _ok(_run("get_account_profile", {"account_code": account_code}))

    @app.get("/accounts/{account_code}/profile/avatar", summary="托管账号头像", tags=["账号托管"])
    def account_profile_avatar(account_code: str):
        return _avatar_response(account_code, self_profile=True)

    @app.post("/accounts/{account_code}/start", summary="启动账号托管", tags=["账号托管"])
    def start_account(account_code: str):
        return _ok(_run("start_account", {"account_code": account_code}))

    @app.post("/accounts/{account_code}/stop", summary="停止账号托管", tags=["账号托管"])
    def stop_account(account_code: str):
        return _ok(_run("stop_account", {"account_code": account_code}))

    @app.post("/accounts/{account_code}/reload_credentials", summary="重新加载凭证", tags=["账号托管"])
    def reload_credentials(account_code: str):
        return _ok(_run("reload_credentials", {"account_code": account_code}))

    @app.post("/accounts/{account_code}/refresh_profiles", summary="同步账号与会话资料", tags=["账号托管"])
    def refresh_profiles(account_code: str):
        return _ok(_run("refresh_profiles", {"account_code": account_code}))

    @app.post("/accounts/stop_all", summary="停止全部账号", tags=["账号托管"])
    def stop_all():
        return _ok(_run("stop_all", {}))

    @app.post("/accounts/{account_code}/send/text", summary="发送文本私信", tags=["消息发送"])
    def send_text(account_code: str, body: SendTextBody):
        return _ok(
            _run(
                "send_text",
                {
                    "account_code": account_code,
                    "text": body.text,
                    "conversation_id": body.conversation_id,
                    "peer_uid": body.peer_uid,
                    "client_msg_id": body.client_msg_id,
                    "is_ai_reply": body.is_ai_reply,
                },
            )
        )

    @app.post("/accounts/{account_code}/send/emoji", summary="发送表情私信", tags=["消息发送"])
    def send_emoji(account_code: str, body: SendEmojiBody):
        return _ok(
            _run(
                "send_emoji",
                {
                    "account_code": account_code,
                    "emoji_url": body.emoji_url,
                    "emoji_name": body.emoji_name,
                    "conversation_id": body.conversation_id,
                    "peer_uid": body.peer_uid,
                    "client_msg_id": body.client_msg_id,
                },
            )
        )

    @app.post("/accounts/{account_code}/send/image", summary="发送图片私信", tags=["消息发送"])
    def send_image(account_code: str, body: SendImageBody):
        return _ok(
            _run(
                "send_image",
                {
                    "account_code": account_code,
                    "image_path": body.image_path,
                    "conversation_id": body.conversation_id,
                    "peer_uid": body.peer_uid,
                    "client_msg_id": body.client_msg_id,
                },
            )
        )

    @app.get("/accounts/{account_code}/conversations", summary="会话列表", tags=["会话查询"])
    def conversations(account_code: str, limit: int = Query(50, ge=1, le=500)):
        return _ok(_run("get_conversations", {"account_code": account_code, "limit": limit}))

    @app.get(
        "/accounts/{account_code}/conversations/{conversation_id:path}/profile",
        summary="对方会话资料",
        tags=["会话查询"],
    )
    def conversation_profile(account_code: str, conversation_id: str):
        return _ok(
            _run(
                "get_conversation_profile",
                {"account_code": account_code, "conversation_id": conversation_id},
            )
        )

    @app.get(
        "/accounts/{account_code}/conversations/{conversation_id:path}/avatar",
        summary="对方头像",
        tags=["会话查询"],
    )
    def conversation_avatar(account_code: str, conversation_id: str):
        return _avatar_response(account_code, conversation_id=conversation_id)

    @app.get(
        "/accounts/{account_code}/conversations/{conversation_id:path}/messages",
        summary="会话消息记录",
        tags=["会话查询"],
    )
    def messages(
        account_code: str,
        conversation_id: str,
        after_id: str = "",
        limit: int = Query(50, ge=1, le=500),
    ):
        return _ok(
            _run(
                "get_messages",
                {
                    "account_code": account_code,
                    "conversation_id": conversation_id,
                    "after_id": after_id,
                    "limit": limit,
                },
            )
        )

    return app
