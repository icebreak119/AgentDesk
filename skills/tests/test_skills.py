from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = ROOT.parent


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_registry_has_seven_skills():
    data = yaml.safe_load((ROOT / "registry.yaml").read_text(encoding="utf-8"))
    skills = data["skills"]
    assert len(skills) == 7
    for name in (
        "session_normalize",
        "intent_triage",
        "reply_plan",
        "channel_send",
        "outcome_verify",
        "customer_confirm",
        "case_digest",
    ):
        assert name in skills
        meta = skills[name]
        assert meta.get("version")
        assert meta.get("schema")
        assert meta.get("examples")


def test_registry_runnable_skills_have_entrypoint():
    data = yaml.safe_load((ROOT / "registry.yaml").read_text(encoding="utf-8"))
    for name, meta in data["skills"].items():
        if meta.get("runnable"):
            assert meta.get("entrypoint")
            assert (ROOT / meta["entrypoint"]).is_file()


def test_intent_triage_consult():
    from importlib.util import module_from_spec, spec_from_file_location

    path = ROOT / "intent_triage" / "v0.1" / "skill.py"
    spec = spec_from_file_location("intent_triage", path)
    module = module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)

    payload = _load_json(ROOT / "intent_triage" / "v0.1" / "examples" / "consult.json")
    result = module.run(payload)
    assert result["intent"] == "consult"
    assert result["priority"] == "low"
    assert result["need_approval"] is False
    assert result["confidence"] >= 0.5


def test_intent_triage_refund():
    from importlib.util import module_from_spec, spec_from_file_location

    path = ROOT / "intent_triage" / "v0.1" / "skill.py"
    spec = spec_from_file_location("intent_triage_refund", path)
    module = module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)

    payload = _load_json(ROOT / "intent_triage" / "v0.1" / "examples" / "refund.json")
    result = module.run(payload)
    assert result["need_approval"] is True
    assert result["risk_tag"] == "high"
    assert result["intent"] == "refund_or_account_change"


def test_reply_plan_high_risk_no_token():
    from importlib.util import module_from_spec, spec_from_file_location

    path = ROOT / "reply_plan" / "v0.1" / "skill.py"
    spec = spec_from_file_location("reply_plan", path)
    module = module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)

    payload = _load_json(ROOT / "reply_plan" / "v0.1" / "examples" / "high_risk.json")
    result = module.run(payload)
    assert result["action_type"] == "escalate"
    assert result.get("approval_token") is None
    assert result["draft_text"]


def test_session_normalize_dedupes_same_customer_across_channels():
    from orchestrator.models.session_event import normalize_session_event

    shared = {
        "customer_id": "customer-shared-001",
        "text": "在吗，想了解价格",
        "ts": "2026-08-02T10:00:00+08:00",
    }
    douyin = normalize_session_event("douyin", "dy-001", {**shared, "conversation_id": "dy-1"})
    wecom = normalize_session_event("qywx", "wx-001", {**shared, "conversation_id": "wx-1"})
    assert douyin["dedupe_key"] == wecom["dedupe_key"]
    assert douyin["canonical_customer_id"] == wecom["canonical_customer_id"]


def test_customer_confirm_and_case_digest_are_privacy_safe():
    from importlib.util import module_from_spec, spec_from_file_location

    confirm_path = ROOT / "customer_confirm" / "v0.1" / "skill.py"
    confirm_spec = spec_from_file_location("customer_confirm", confirm_path)
    confirm = module_from_spec(confirm_spec)
    assert confirm_spec.loader is not None
    confirm_spec.loader.exec_module(confirm)
    confirmed = confirm.run({"task_id": "task_x", "customer_feedback": "收到，谢谢，已解决"})
    assert confirmed["confirmation_state"] == "confirmed"

    digest_path = ROOT / "case_digest" / "v0.1" / "skill.py"
    digest_spec = spec_from_file_location("case_digest", digest_path)
    digest = module_from_spec(digest_spec)
    assert digest_spec.loader is not None
    digest_spec.loader.exec_module(digest)
    record = digest.run(
        {
            "task_id": "task_x",
            "channel": "douyin",
            "triage_result": {"intent": "consult", "risk_tag": "low"},
            "verify_result": {"pass": True},
            "customer_confirm_result": confirmed,
            "resolution": "done",
        }
    )
    assert record["privacy"]["contains_customer_identity"] is False
    assert record["privacy"]["contains_customer_content"] is False
    assert "收到" not in record["knowledge_snippet"]


@pytest.mark.parametrize(
    ("skill", "example"),
    [
        ("intent_triage", "intent_triage/v0.1/examples/consult.json"),
        ("intent_triage", "intent_triage/v0.1/examples/refund.json"),
        ("reply_plan", "reply_plan/v0.1/examples/consult.json"),
        ("reply_plan", "reply_plan/v0.1/examples/high_risk.json"),
        ("session_normalize", "session_normalize/v0.1/examples/douyin_inbound.json"),
        ("outcome_verify", "outcome_verify/v0.1/examples/verify_ok.json"),
        ("customer_confirm", "customer_confirm/v0.1/examples/confirmed.json"),
        ("case_digest", "case_digest/v0.1/examples/confirmed_consult.json"),
    ],
)
def test_run_skill_cli(skill: str, example: str):
    proc = subprocess.run(
        [
            sys.executable,
            str(ROOT / "run_skill.py"),
            skill,
            "-i",
            str(ROOT / example),
            "--pretty",
        ],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env={**dict(**__import__("os").environ), "PYTHONIOENCODING": "utf-8"},
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    data = json.loads(proc.stdout)
    assert isinstance(data, dict)
