"""Contract validation helpers (stdlib only)."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(f"expected object: {path}")
    return data


def idempotency_pattern(spec: dict[str, Any] | None = None) -> re.Pattern[str]:
    spec = spec or load_json(ROOT / "common" / "idempotency.json")
    raw = spec["键格式"]["pattern"]
    return re.compile(raw)


def validate_idempotency_key(key: str, spec: dict[str, Any] | None = None) -> bool:
    text = str(key or "").strip()
    spec = spec or load_json(ROOT / "common" / "idempotency.json")
    fmt = spec["键格式"]
    if len(text) < int(fmt["最小长度"]) or len(text) > int(fmt["最大长度"]):
        return False
    return bool(idempotency_pattern(spec).match(text))


def error_codes(spec: dict[str, Any] | None = None) -> set[str]:
    spec = spec or load_json(ROOT / "common" / "errors.json")
    return {str(item["code"]) for item in spec["错误码"]}


def audit_required_fields(spec: dict[str, Any] | None = None) -> set[str]:
    spec = spec or load_json(ROOT / "common" / "audit.json")
    return set(spec["必填字段"].keys())


def validate_audit_record(record: dict[str, Any], spec: dict[str, Any] | None = None) -> list[str]:
    required = audit_required_fields(spec)
    missing: list[str] = []
    for name in sorted(required):
        value = str(record.get(name) or "").strip()
        if name == "error_code":
            if str(record.get("outcome") or "").strip() == "failed" and not value:
                missing.append(name)
            continue
        if not value:
            missing.append(name)
    return missing
