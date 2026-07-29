"""确保 PyExecJS 使用 Node.js 而非 Windows JScript（避免 SyntaxError）。"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path


def _node_candidates() -> list[Path]:
    candidates: list[Path] = []
    override = os.environ.get("DY_NODE_EXECUTABLE", "").strip()
    if override:
        candidates.append(Path(override))

    roots: list[Path] = []
    client_root = os.environ.get("YUNDUO_CLIENT_ROOT", "").strip()
    if client_root:
        roots.append(Path(client_root))
    if getattr(sys, "frozen", False):
        roots.append(Path(sys.executable).resolve().parent)
        meipass = getattr(sys, "_MEIPASS", "").strip()
        if meipass:
            roots.append(Path(meipass))

    deduped_roots: list[Path] = []
    for root in roots:
        resolved = root.resolve()
        if resolved not in deduped_roots:
            deduped_roots.append(resolved)

    for root in deduped_roots:
        candidates.append(root / "runtime" / "node" / "node.exe")

    which = shutil.which("node")
    if which:
        candidates.append(Path(which))

    if sys.platform == "win32":
        for path in (
            os.path.expandvars(r"%ProgramFiles%\nodejs\node.exe"),
            os.path.expandvars(r"%ProgramFiles(x86)%\nodejs\node.exe"),
            os.path.expandvars(r"%LocalAppData%\Programs\nodejs\node.exe"),
        ):
            if path:
                candidates.append(Path(path))

    deduped: list[Path] = []
    for item in candidates:
        try:
            resolved = item.resolve()
        except OSError:
            continue
        if resolved not in deduped:
            deduped.append(resolved)
    return deduped


def ensure_node_for_execjs() -> str:
    """把 Node 写入环境变量，强制 execjs 使用 Node 运行时。"""
    node_exe = ""
    for candidate in _node_candidates():
        if candidate.is_file():
            node_exe = str(candidate)
            break
    if not node_exe:
        return ""

    node_dir = str(Path(node_exe).resolve().parent)
    os.environ["DY_NODE_EXECUTABLE"] = node_exe
    os.environ["EXECJS_RUNTIME"] = "Node"
    path_entries = os.environ.get("PATH", "").split(os.pathsep)
    if node_dir not in path_entries:
        os.environ["PATH"] = node_dir + os.pathsep + os.environ.get("PATH", "")
    return node_exe
