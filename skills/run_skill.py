#!/usr/bin/env python3
"""CLI 样例调用已注册 Skill。"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
REPO_ROOT = ROOT.parent


def _load_registry() -> dict[str, Any]:
    try:
        import yaml
    except ImportError as exc:
        raise SystemExit("缺少 PyYAML，请执行: pip install pyyaml") from exc
    data = yaml.safe_load((ROOT / "registry.yaml").read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("skills"), dict):
        raise SystemExit("registry.yaml 格式无效")
    return data["skills"]


def _load_skill_module(entrypoint: str):
    path = ROOT / entrypoint
    if not path.is_file():
        raise SystemExit(f"entrypoint 不存在: {path}")
    spec = importlib.util.spec_from_file_location(f"skill_{path.stem}", path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"无法加载: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if not hasattr(module, "run"):
        raise SystemExit(f"{path} 缺少 run(payload) 函数")
    return module


def _read_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise SystemExit(f"输入必须是 JSON 对象: {path}")
    return data


def main(argv: list[str] | None = None) -> int:
    registry = _load_registry()
    parser = argparse.ArgumentParser(description="AgentDesk Skill CLI")
    parser.add_argument("skill", choices=sorted(registry.keys()), help="Skill 名称")
    parser.add_argument("-i", "--input", required=True, help="输入 JSON 文件路径")
    parser.add_argument("--pretty", action="store_true", help="格式化输出")
    args = parser.parse_args(argv)

    meta = registry[args.skill]
    if not meta.get("runnable"):
        raise SystemExit(f"Skill `{args.skill}` 不可 CLI 运行: {meta.get('implementation', '见 registry')}")

    entrypoint = str(meta.get("entrypoint") or "").strip()
    if not entrypoint:
        raise SystemExit(f"Skill `{args.skill}` 未配置 entrypoint")

    payload = _read_json(Path(args.input))
    module = _load_skill_module(entrypoint)
    result = module.run(payload)
    print(json.dumps(result, ensure_ascii=False, indent=2 if args.pretty else None))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
