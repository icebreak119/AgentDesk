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


def test_registry_has_five_skills():
    data = yaml.safe_load((ROOT / "registry.yaml").read_text(encoding="utf-8"))
    skills = data["skills"]
    assert len(skills) == 5
    for name in (
        "session_normalize",
        "intent_triage",
        "reply_plan",
        "channel_send",
        "outcome_verify",
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


@pytest.mark.parametrize(
    ("skill", "example"),
    [
        ("intent_triage", "intent_triage/v0.1/examples/consult.json"),
        ("intent_triage", "intent_triage/v0.1/examples/refund.json"),
        ("reply_plan", "reply_plan/v0.1/examples/consult.json"),
        ("reply_plan", "reply_plan/v0.1/examples/high_risk.json"),
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
