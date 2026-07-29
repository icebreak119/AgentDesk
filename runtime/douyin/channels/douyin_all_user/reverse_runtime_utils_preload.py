"""主工程已占用 ``utils`` 包名时，将 reverse_runtime/utils 子模块注入 ``sys.modules``。"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_DEFAULT_RUNTIME_ROOT = (Path(__file__).resolve().parent / "reverse_runtime").resolve()

# 关键导出缺失时视为 poisoned，需强制重载（否则 IPC 启动报 enrich_peer_profile 缺失）
_REQUIRED_EXPORTS: dict[str, tuple[str, ...]] = {
    "utils.im_profile_enricher": ("enrich_peer_profile", "enrich_self_profile"),
}


def _reverse_utils_module_loaded(full_name: str, module: object, *, source_path: Path) -> bool:
    mod_file = getattr(module, "__file__", None)
    if not mod_file:
        return False
    try:
        if Path(str(mod_file)).resolve() != source_path.resolve():
            return False
    except OSError:
        return False
    required = _REQUIRED_EXPORTS.get(full_name)
    if required:
        return all(callable(getattr(module, name, None)) for name in required)
    return any(not name.startswith("_") for name in dir(module))


def list_reverse_runtime_utils_modules(runtime_root: Path | None = None) -> list[str]:
    root = (runtime_root or _DEFAULT_RUNTIME_ROOT).resolve()
    utils_dir = root / "utils"
    if not utils_dir.is_dir():
        return []
    return sorted(
        path.stem
        for path in utils_dir.glob("*.py")
        if path.name != "__init__.py"
    )


def preload_reverse_runtime_utils(runtime_root: Path | None = None) -> None:
    """按依赖多轮尝试加载 reverse_runtime/utils 下全部 .py 模块。"""
    root = (runtime_root or _DEFAULT_RUNTIME_ROOT).resolve()
    utils_dir = root / "utils"
    if not utils_dir.is_dir():
        return

    pending = [
        path
        for path in sorted(utils_dir.glob("*.py"))
        if path.name != "__init__.py"
    ]
    while pending:
        progressed = False
        still_pending: list[Path] = []
        for path in pending:
            full_name = f"utils.{path.stem}"
            existing = sys.modules.get(full_name)
            if existing is not None and _reverse_utils_module_loaded(
                full_name, existing, source_path=path
            ):
                progressed = True
                continue
            if existing is not None:
                sys.modules.pop(full_name, None)
            spec = importlib.util.spec_from_file_location(full_name, path)
            if spec is None or spec.loader is None:
                still_pending.append(path)
                continue
            module = importlib.util.module_from_spec(spec)
            sys.modules[full_name] = module
            try:
                spec.loader.exec_module(module)
            except ModuleNotFoundError:
                sys.modules.pop(full_name, None)
                still_pending.append(path)
                continue
            except Exception:
                sys.modules.pop(full_name, None)
                still_pending.append(path)
                continue
            progressed = True
        if not progressed:
            break
        pending = still_pending
