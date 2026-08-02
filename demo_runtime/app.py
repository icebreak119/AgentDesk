"""Real-time browser demo for the Douyin + WeCom refund workflow."""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse

from orchestrator.agents.duty_manager import DutyManager
from orchestrator.agents.session_tl import SessionTL
from orchestrator.models.conversation_ledger import ConversationLedger
from orchestrator.models.session_event import normalize_session_event
from orchestrator.models.trace import TraceWriter, input_hash

_TZ = timezone(timedelta(hours=8))


def _now() -> str:
    return datetime.now(_TZ).strftime("%H:%M:%S")


def _http_json(method: str, url: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    body = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            value = json.loads(response.read().decode("utf-8"))
    except (OSError, urllib.error.HTTPError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"HTTP request failed: {type(exc).__name__}") from exc
    if value.get("ok") is False:
        error = value.get("error") or {}
        raise RuntimeError(str(error.get("code") or "request_failed"))
    return value.get("data") if isinstance(value.get("data"), dict) else value


class DemoRun:
    def __init__(self, run_id: str, trace_path: Path, scenario: str) -> None:
        self.run_id = run_id
        self.trace_path = trace_path
        self.scenario = scenario
        self.events: list[dict[str, Any]] = []
        self.status = "starting"
        self.error = ""
        self.started_at = _now()
        self.finished_at = ""
        self.final_state = ""
        self._lock = threading.RLock()

    def emit(self, kind: str, title: str, body: str, **fields: Any) -> None:
        item = {
            "seq": len(self.events) + 1,
            "time": _now(),
            "kind": kind,
            "title": title,
            "body": body,
            **fields,
        }
        with self._lock:
            self.events.append(item)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "run_id": self.run_id,
                "status": self.status,
                "error": self.error,
                "started_at": self.started_at,
                "finished_at": self.finished_at,
                "trace_path": str(self.trace_path),
                "scenario": self.scenario,
                "final_state": self.final_state,
                "events": list(self.events),
            }


def _trace_view(event: dict[str, Any]) -> tuple[str, str, str]:
    agent = str(event.get("agent") or "Agent")
    skill = str(event.get("skill") or event.get("event") or "step")
    event_name = str(event.get("event") or "")
    if event_name == "state_transition":
        body = f"{event.get('from', '')}  ->  {event.get('to', '')}"
    else:
        output = event.get("output") or {}
        details = []
        for key in ("operation_id", "api", "order_id", "status", "error_code", "rollback_of", "evidence_ref"):
            if output.get(key):
                details.append(f"{key}={output[key]}")
        body = " · ".join(details) or str(event.get("status") or "emitted")
    title = f"{agent}  /  {skill}"
    step = "approval" if "approval" in event_name else "business" if "business_" in event_name else "notify" if "notification" in event_name or skill == "ChannelSend" else "agent"
    return title, body, step


def _run_workflow(
    demo: DemoRun,
    *,
    enterprise_url: str,
    wecom_url: str,
    gateway_url: str,
    repo_root: Path,
    scenario: str,
) -> None:
    try:
        output_dir = repo_root / "tmp" / f"live_demo_{demo.run_id}"
        output_dir.mkdir(parents=True, exist_ok=True)
        knowledge_path = output_dir / "case_knowledge.jsonl"
        action_path = output_dir / "business_actions.jsonl"
        ledger = ConversationLedger()
        duty_manager = DutyManager()
        session_tl = SessionTL(
            conversation_ledger=ledger,
            knowledge_path=knowledge_path,
            business_action_path=action_path,
        )
        inject_verify_failure = scenario in {"verify_failure", "rollback_failure"}
        inject_rollback_failure = scenario == "rollback_failure"
        shared_ts = "2026-08-02T09:05:00+08:00"
        douyin_payload = {
            "conversation_id": "dy-demo-conversation-001",
            "event_id": f"dy-live-{demo.run_id}",
            "customer_id": "customer-demo-002",
            "ts": shared_ts,
            "text": "我要退款，改一下账户",
            "sender_name": "演示客户",
            "order_id": "order-demo-001",
            "amount": "199.00",
            "currency": "CNY",
            "refund_reason": "商品售后退款",
            "customer_feedback": "谢谢，已经收到处理进度。",
        }

        demo.emit(
            "system",
            "LIVE RUN STARTED",
            f"场景={scenario} · 演示运行在本机隔离目录中，退款动作将调用 HTTP 企业业务模拟器。",
            step="start",
            backend="HTTP / 8770",
        )
        demo.emit(
            "inbound",
            "抖音 Runtime · inbound callback",
            "高风险退款请求进入统一会话契约。",
            step="inbound",
            channel="douyin",
            contract="POST /webhooks/douyin/messages",
        )
        douyin_event = _http_json(
            "POST",
            f"{gateway_url.rstrip('/')}/webhooks/douyin/messages",
            {**douyin_payload, "account_code": "d6a26b9e-demo"},
        )
        normalized_douyin = douyin_event.get("session_event") or {}
        demo.emit(
            "channel",
            "抖音 → SessionEvent",
            f"channel={normalized_douyin.get('channel')} · dedupe_key={normalized_douyin.get('dedupe_key')} · content_hash={input_hash(normalized_douyin.get('content', ''))}",
            step="inbound",
            channel="douyin",
            privacy="content omitted from live telemetry",
        )
        ctx = duty_manager.create_task(
            task_id=f"live_{demo.run_id}",
            profile_id="d6a26b9e-demo",
            channel="douyin",
            raw_event=douyin_payload,
            mode="demo",
        )

        def on_trace(event: dict[str, Any]) -> None:
            title, body, step = _trace_view(event)
            demo.emit("trace", title, body, step=step, trace=event)
            time.sleep(0.38)

        with TraceWriter(demo.trace_path, on_event=on_trace) as trace:
            ctx = session_tl.run_until_gate(ctx, trace, duty_manager)
            if ctx.state != "suspended":
                raise RuntimeError(f"approval_gate_not_reached:{ctx.state}")
            demo.emit(
                "approval",
                "审批闸门 · suspended",
                "退款动作已挂起，当前没有创建企业退款操作。",
                step="approval",
                status="suspended",
            )
            time.sleep(0.9)
            duty_manager.grant_approval(ctx, "appr_token_live_demo")
            demo.emit(
                "approval",
                "人工审批 · granted",
                "审批通过，向 BusinessAction 传递一次性审批范围。",
                step="approval",
                status="approved",
            )
            ctx = session_tl.resume_after_approval(
                ctx,
                trace,
                duty_manager,
                business_action_backend="http",
                enterprise_base_url=enterprise_url,
                inject_verify_failure=inject_verify_failure,
                inject_rollback_failure=inject_rollback_failure,
            )

            demo.emit(
                "inbound",
                "企业微信 · webhook callback",
                "同一客户、同一问题从企微进入，先经过本地 Webhook 适配器。",
                step="channel",
                channel="wecom",
                contract="POST /webhooks/wecom/messages",
            )
            wecom_payload = {
                "profile_id": "d6a26b9e-demo",
                "event_id": f"wx-live-{demo.run_id}",
                "external_userid": "customer-demo-002",
                "chat_id": "wecom-demo-chat-001",
                "create_time": shared_ts,
                "text": "我要退款，改一下账户",
                "sender_name": "演示客户",
            }
            wecom_event = _http_json("POST", f"{wecom_url.rstrip('/')}/webhooks/wecom/messages", wecom_payload)
            normalized = wecom_event.get("session_event") or {}
            demo.emit(
                "channel",
                "企微 → SessionEvent",
                f"channel={normalized.get('channel')} · dedupe_key={normalized.get('dedupe_key')}",
                step="channel",
                channel="wecom",
                privacy="content omitted from live telemetry",
            )
            wecom_ctx = duty_manager.create_task(
                task_id=f"live_{demo.run_id}_wecom",
                profile_id="d6a26b9e-demo",
                channel="wecom",
                raw_event={**wecom_payload, "ts": shared_ts},
                mode="demo",
            )
            wecom_ctx = session_tl.run_until_gate(wecom_ctx, trace, duty_manager)
            demo.emit(
                "channel",
                "ConversationLedger · duplicate linked",
                f"企微消息与抖音任务合并，state={wecom_ctx.state}，没有再次发送或退款。",
                step="channel",
                status=wecom_ctx.state,
            )

        demo.final_state = ctx.state
        if ctx.state == "escalated":
            demo.emit(
                "approval",
                "补偿回滚失败 · escalated",
                "退款操作未核验成功且补偿回滚失败，已进入人工复核，不发送客户成功通知。",
                step="approval",
                status="human_review",
            )
        demo.emit(
            "complete",
            "TRACE COMPLETE",
            f"主任务 state={ctx.state} · trace={demo.trace_path.name}",
            step="complete",
            status=ctx.state,
            trace_path=str(demo.trace_path),
        )
        demo.status = "completed"
    except Exception as exc:
        demo.error = str(exc)
        demo.emit("error", "DEMO FAILED", str(exc), step="error")
        demo.status = "failed"
    finally:
        demo.finished_at = _now()


HTML = r"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>AgentDesk Live Orchestration</title>
  <style>
    :root { --ink:#e8eef5; --muted:#8e9dad; --line:#263442; --panel:#111b24; --panel2:#0d151d; --cyan:#54d6d0; --blue:#70a7ff; --amber:#f4bd63; --red:#ff7c87; --green:#65d39b; }
    * { box-sizing:border-box; }
    body { margin:0; color:var(--ink); background:#081017; font:14px/1.5 "Segoe UI","Microsoft YaHei",sans-serif; }
    .shell { max-width:1480px; margin:0 auto; padding:28px 34px 36px; }
    header { display:flex; justify-content:space-between; align-items:flex-start; gap:24px; border-bottom:1px solid var(--line); padding-bottom:22px; }
    .eyebrow { color:var(--cyan); font:700 11px/1.2 ui-monospace,SFMono-Regular,Consolas,monospace; letter-spacing:1.8px; }
    h1 { margin:8px 0 4px; font-size:28px; letter-spacing:0; }
    .subtitle { color:var(--muted); margin:0; }
    .actions { display:flex; gap:10px; align-items:center; }
    select { border:1px solid #2f6b70; background:#0e262d; color:#dffcfb; border-radius:6px; padding:10px 12px; font-weight:700; }
    button { border:1px solid #2f6b70; background:#12363b; color:#dffcfb; border-radius:6px; padding:10px 16px; font-weight:700; cursor:pointer; }
    button:disabled { opacity:.45; cursor:default; }
    .status { border:1px solid var(--line); border-radius:5px; padding:9px 12px; color:var(--muted); font:12px ui-monospace,SFMono-Regular,Consolas,monospace; }
    .flow { display:grid; grid-template-columns:repeat(7,1fr); gap:8px; margin:22px 0; }
    .step { min-height:70px; border:1px solid var(--line); background:var(--panel2); padding:11px 12px; border-radius:5px; color:var(--muted); }
    .step small { display:block; color:#607284; font:11px ui-monospace,SFMono-Regular,Consolas,monospace; margin-bottom:8px; }
    .step strong { font-size:13px; color:var(--ink); }
    .step.active { border-color:var(--cyan); box-shadow:inset 3px 0 var(--cyan); }
    .step.done { border-color:#316251; }
    .layout { display:grid; grid-template-columns:280px minmax(480px,1fr) 300px; gap:16px; align-items:start; }
    .panel { border:1px solid var(--line); background:var(--panel); border-radius:6px; overflow:hidden; }
    .panel-head { padding:14px 16px; border-bottom:1px solid var(--line); display:flex; justify-content:space-between; align-items:center; }
    .panel-head h2 { margin:0; font-size:14px; }
    .panel-head span { color:var(--muted); font:11px ui-monospace,SFMono-Regular,Consolas,monospace; }
    .panel-body { padding:14px 16px; }
    .channel { border-left:3px solid var(--blue); padding:10px 12px; background:#101e2c; margin-bottom:10px; }
    .channel.wecom { border-left-color:var(--green); background:#10251f; }
    .channel b { display:block; margin-bottom:4px; }
    .channel p { color:var(--muted); margin:0; font-size:12px; }
    .metric { display:flex; justify-content:space-between; padding:9px 0; border-bottom:1px solid #1c2934; color:var(--muted); }
    .metric strong { color:var(--ink); font-family:ui-monospace,SFMono-Regular,Consolas,monospace; }
    .timeline { min-height:500px; max-height:660px; overflow:auto; padding:8px 16px 16px; }
    .event { display:grid; grid-template-columns:68px 12px 1fr; gap:10px; padding:12px 0; border-bottom:1px solid #1c2934; }
    .event time { color:#627486; font:11px ui-monospace,SFMono-Regular,Consolas,monospace; padding-top:2px; }
    .dot { width:9px; height:9px; margin-top:5px; border-radius:50%; background:#627486; box-shadow:0 0 0 3px #1b2a35; }
    .event.trace .dot { background:var(--cyan); }.event.approval .dot { background:var(--amber); }.event.inbound .dot { background:var(--blue); }.event.channel .dot { background:var(--green); }.event.error .dot { background:var(--red); }
    .event h3 { margin:0 0 3px; font-size:13px; }.event p { margin:0; color:var(--muted); font-size:12px; }
    code { color:#b5d7ff; font:11px ui-monospace,SFMono-Regular,Consolas,monospace; }
    .endpoint { padding:10px 0; border-bottom:1px solid #1c2934; }.endpoint code { display:block; }.endpoint span { color:var(--muted); font-size:12px; }
    .foot { margin-top:18px; color:#607284; font-size:12px; display:flex; justify-content:space-between; }
    @media (max-width:1000px) { .layout { grid-template-columns:1fr; }.flow { grid-template-columns:repeat(4,1fr); } header { flex-direction:column; }.timeline { max-height:none; } }
    @media (max-width:560px) { .shell { padding:18px 14px; }.flow { grid-template-columns:repeat(2,1fr); }.actions { width:100%; }.actions button { flex:1; } h1 { font-size:23px; } }
  </style>
</head>
<body>
  <main class="shell">
    <header>
      <div><div class="eyebrow">AGENTDESK / LIVE ORCHESTRATION</div><h1>高风险客服动作闭环</h1><p class="subtitle">抖音 + 企业微信 · Agent delegation · HTTP enterprise evidence</p></div>
      <div class="actions"><div id="runStatus" class="status">READY · 等待演示</div><select id="scenarioSelect" aria-label="演示场景"><option value="success">成功闭环</option><option value="verify_failure">核验失败并回滚</option><option value="rollback_failure">回滚失败升级人工</option></select><button id="runButton">启动实时演示</button></div>
    </header>
    <section class="flow" id="flow"></section>
    <section class="layout">
      <aside class="panel"><div class="panel-head"><h2>Channel ingress</h2><span>2 adapters</span></div><div class="panel-body"><div class="channel"><b>抖音私信</b><p>Inbound message → SessionEvent</p><code>channel=douyin</code></div><div class="channel wecom"><b>企业微信 Webhook</b><p>POST /webhooks/wecom/messages</p><code>channel=wecom</code></div><div class="metric"><span>统一契约</span><strong>SessionEvent</strong></div><div class="metric"><span>去重策略</span><strong>5m window</strong></div><div class="metric"><span>任务模式</span><strong id="modeMetric">HTTP demo</strong></div></div></aside>
      <section class="panel"><div class="panel-head"><h2>Agent trace / live feed</h2><span id="eventCount">0 events</span></div><div id="timeline" class="timeline"><div style="color:#718394;padding:20px 0">点击“启动实时演示”，观察事件逐条进入。</div></div></section>
      <aside class="panel"><div class="panel-head"><h2>Enterprise API</h2><span>127.0.0.1:8770</span></div><div class="panel-body"><div class="endpoint"><code>GET /enterprise/orders/{id}</code><span>订单查询</span></div><div class="endpoint"><code>POST /enterprise/refunds</code><span>退款申请 + 幂等</span></div><div class="endpoint"><code>POST /enterprise/refunds/{id}/execute</code><span>执行回执</span></div><div class="endpoint"><code>GET /enterprise/operations/{id}</code><span>结果核验</span></div><div class="endpoint"><code>POST /enterprise/refunds/{id}/rollback</code><span>补偿回滚</span></div><div class="metric"><span>证据状态</span><strong id="evidenceMetric">pending</strong></div><div class="metric"><span>Trace file</span><strong id="traceMetric">-</strong></div></div></aside>
    </section>
    <div class="foot"><span>初赛演示环境 · 本地服务 · 不接触真实支付系统</span><span id="runId">run=-</span></div>
  </main>
  <script>
    const steps = [['inbound','01','Inbound'],['agent','02','Agent delegation'],['approval','03','Approval gate'],['business','04','Refund API'],['business','05','Verify'],['notify','06','Notify'],['complete','07','Trace']];
    const flow = document.getElementById('flow');
    flow.innerHTML = steps.map(([key,n,label]) => `<div class="step" data-step="${key}"><small>${n}</small><strong>${label}</strong></div>`).join('');
    let runId = null, rendered = 0, polling = null;
    const status = document.getElementById('runStatus'), timeline = document.getElementById('timeline');
    const esc = (value) => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
    function renderEvent(e) { const div=document.createElement('div'); div.className=`event ${esc(e.kind)}`; div.innerHTML=`<time>${esc(e.time)}</time><div class="dot"></div><div><h3>${esc(e.title)}</h3><p>${esc(e.body)}</p></div>`; timeline.appendChild(div); timeline.scrollTop=timeline.scrollHeight; }
    function activate(step) { document.querySelectorAll('.step').forEach(x=>x.classList.toggle('active', x.dataset.step===step)); }
    async function poll() { if(!runId) return; const data=await (await fetch(`/api/demo/state/${runId}`)).json(); const events=data.events||[]; if(rendered===0) timeline.innerHTML=''; events.slice(rendered).forEach(renderEvent); rendered=events.length; document.getElementById('eventCount').textContent=`${rendered} events`; const last=events[events.length-1]; if(last) { activate(last.step); if(last.trace_path) document.getElementById('traceMetric').textContent=last.trace_path.split(/[\\/]/).pop(); } status.textContent=`${data.status.toUpperCase()} · ${data.final_state||data.finished_at||data.started_at}`; if(data.status==='completed'||data.status==='failed'){ clearInterval(polling); document.getElementById('runButton').disabled=false; document.getElementById('scenarioSelect').disabled=false; document.getElementById('evidenceMetric').textContent=data.final_state||'inspect'; } }
    document.getElementById('runButton').onclick=async()=>{ document.getElementById('runButton').disabled=true; document.getElementById('scenarioSelect').disabled=true; rendered=0; const scenario=document.getElementById('scenarioSelect').value; const res=await (await fetch('/api/demo/run',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({scenario})})).json(); runId=res.run_id; document.getElementById('runId').textContent=`run=${runId} · ${scenario}`; status.textContent='RUNNING · starting'; polling=setInterval(poll,250); poll(); };
  </script>
</body>
</html>"""


def create_app(
    *,
    repo_root: str | Path,
    enterprise_url: str = "http://127.0.0.1:8770",
    wecom_url: str = "http://127.0.0.1:8771",
    gateway_url: str = "http://127.0.0.1:8780",
) -> FastAPI:
    root = Path(repo_root).resolve()
    app = FastAPI(title="AgentDesk Live Demo", docs_url=None, redoc_url=None)
    app.state.runs: dict[str, DemoRun] = {}
    app.state.root = root

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return HTML

    @app.get("/api/ping")
    def ping() -> dict[str, Any]:
        return {"ok": True, "data": {"service": "live-demo", "enterprise_url": enterprise_url, "wecom_url": wecom_url, "gateway_url": gateway_url}}

    @app.get("/webhooks/douyin/ping")
    def douyin_ping() -> dict[str, Any]:
        return {"ok": True, "data": {"channel": "douyin", "status": "ok"}}

    @app.post("/webhooks/douyin/messages")
    async def douyin_message(request: Request) -> dict[str, Any]:
        payload = await request.json()
        if not isinstance(payload, dict):
            raise HTTPException(status_code=400, detail="webhook_payload_must_be_object")
        profile_id = str(payload.get("profile_id") or payload.get("account_code") or "douyin-demo")
        event = normalize_session_event("douyin", profile_id, payload)
        return {
            "ok": True,
            "data": {
                "session_event": event,
                "evidence_ref": f"channel://douyin/{event['source_event_id']}",
            },
        }

    @app.post("/api/demo/run")
    async def start_run(request: Request) -> dict[str, Any]:
        active = next((run for run in app.state.runs.values() if run.status in {"starting", "running"}), None)
        if active:
            return {"ok": True, "run_id": active.run_id, "status": active.status}
        try:
            payload = await request.json()
        except Exception:
            payload = {}
        scenario = str(payload.get("scenario") or "success") if isinstance(payload, dict) else "success"
        if scenario not in {"success", "verify_failure", "rollback_failure"}:
            raise HTTPException(status_code=400, detail="unsupported_demo_scenario")
        run_id = uuid.uuid4().hex[:8]
        trace_path = root / "tmp" / f"live_demo_{run_id}" / "trace.jsonl"
        run = DemoRun(run_id, trace_path, scenario)
        run.status = "running"
        app.state.runs[run_id] = run
        thread = threading.Thread(
            target=_run_workflow,
            kwargs={
                "demo": run,
                "enterprise_url": enterprise_url,
                "wecom_url": wecom_url,
                "gateway_url": gateway_url,
                "repo_root": root,
                "scenario": scenario,
            },
            name=f"agentdesk-live-demo-{run_id}",
            daemon=True,
        )
        thread.start()
        return {"ok": True, "run_id": run_id, "status": run.status}

    @app.get("/api/demo/state/{run_id}")
    def run_state(run_id: str) -> dict[str, Any]:
        run = app.state.runs.get(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="run_not_found")
        return run.snapshot()

    return app
