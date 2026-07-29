from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = ROOT.parents[1]
COMMON = ROOT / "common"
CHANNELS = [
    ROOT / "channel.send_message.json",
    ROOT / "channel.query_session.json",
    ROOT / "channel.fetch_history.json",
]

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _load(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    assert isinstance(data, dict)
    return data


def test_common_json_files_exist():
    for name in ("errors.json", "audit.json", "idempotency.json"):
        assert (COMMON / name).is_file()


def test_error_codes_unique_and_complete():
    spec = _load(COMMON / "errors.json")
    codes = [item["code"] for item in spec["错误码"]]
    assert len(codes) == len(set(codes))
    for item in spec["错误码"]:
        assert item["http_status"] in {400, 403, 404, 500}
        assert isinstance(item["可重试"], bool)


def test_runtime_error_codes_covered_by_contract():
    runtime_errors = (
        REPO_ROOT / "runtime" / "douyin" / "channels" / "douyin_reverse_ipc" / "errors.py"
    )
    if not runtime_errors.is_file():
        pytest.skip("runtime errors.py not present")
    text = runtime_errors.read_text(encoding="utf-8")
    match = re.search(r"ERROR_CODES\s*=\s*frozenset\(\s*\{([^}]+)\}", text, re.S)
    assert match
    runtime_codes = {
        line.strip().strip(",").strip('"').strip("'")
        for line in match.group(1).splitlines()
        if line.strip()
    }
    contract_codes = {item["code"] for item in _load(COMMON / "errors.json")["错误码"]}
    missing = runtime_codes - contract_codes
    assert not missing, f"contract missing runtime codes: {missing}"


def test_audit_samples_have_required_fields():
    from docs.contracts.validate import validate_audit_record

    spec = _load(COMMON / "audit.json")
    for sample in spec["样例"]:
        missing = validate_audit_record(sample, spec)
        assert not missing, f"audit sample missing: {missing}"


def test_idempotency_pattern_and_examples():
    from docs.contracts.validate import validate_idempotency_key

    spec = _load(COMMON / "idempotency.json")
    for example in spec["键格式"]["示例"]:
        assert validate_idempotency_key(example, spec), example
    assert not validate_idempotency_key("short", spec)
    assert not validate_idempotency_key("", spec)


@pytest.mark.parametrize("path", CHANNELS)
def test_channel_contracts_reference_common(path: Path):
    spec = _load(path)
    refs = spec.get("规范引用") or {}
    for rel in refs.values():
        assert (ROOT / rel).is_file(), f"{path.name} missing {rel}"
    listed = set(spec.get("常见错误码") or [])
    contract_codes = {item["code"] for item in _load(COMMON / "errors.json")["错误码"]}
    unknown = listed - contract_codes
    assert not unknown, f"{path.name} unknown error codes: {unknown}"


def test_send_message_requires_idempotency_in_audit():
    spec = _load(ROOT / "channel.send_message.json")
    audit_required = set(spec["审计"]["必填"])
    assert "idempotency_key" in audit_required
    assert "idempotency_key" in spec["输入"]


def test_validate_module_audit_helper():
    from docs.contracts.validate import validate_audit_record

    spec = _load(COMMON / "audit.json")
    sample = spec["样例"][0]
    assert validate_audit_record(sample, spec) == []
