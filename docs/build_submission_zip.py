"""打包 AgentDesk 初赛提交 zip v4.5。"""

from __future__ import annotations

import zipfile
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DOCS = REPO_ROOT / "docs"
OUT_DIR = REPO_ROOT.parent
ZIP_NAME = f"AgentDesk_初赛提交_v4.5_{datetime.now().strftime('%Y%m%d')}.zip"

# 文档根目录文件（01~13 + 架构图 + 截图）
DOC_FILES = [
    "01_作品简介.txt",
    "02_方案PPT.pdf",
    "03_Agent_Identity清单.md",
    "04_Skill清单.md",
    "05_多Agent闭环说明.md",
    "06_架构图.png",
    "08_Demo演示脚本.md",
    "09_代码仓库说明.md",
    "10_运行说明.md",
    "11_当前完成度与复赛计划.md",
    "12_附件索引.md",
    "13_初赛补强计划.md",
]

SKIP_PARTS = {
    "__pycache__",
    ".pytest_cache",
    ".git",
    ".pyc",
}

EXTRA_DIRS = [
    REPO_ROOT / "docs" / "contracts",
    REPO_ROOT / "docs" / "07_系统截图",
    REPO_ROOT / "skills",
    REPO_ROOT / "orchestrator",
]


def _should_skip(path: Path) -> bool:
    return any(part in SKIP_PARTS for part in path.parts)


def _add_path(zf: zipfile.ZipFile, src: Path, arc_prefix: str) -> int:
    count = 0
    if src.is_file():
        if not _should_skip(src):
            zf.write(src, arcname=f"{arc_prefix}/{src.name}")
            count += 1
        return count
    for file in sorted(src.rglob("*")):
        if not file.is_file() or _should_skip(file):
            continue
        rel = file.relative_to(src)
        zf.write(file, arcname=f"{arc_prefix}/{rel.as_posix()}")
        count += 1
    return count


def build() -> Path:
    out_path = OUT_DIR / ZIP_NAME
    total = 0
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name in DOC_FILES:
            path = DOCS / name
            if path.is_file():
                zf.write(path, arcname=name)
                total += 1
        for src in EXTRA_DIRS:
            if not src.exists():
                continue
            prefix = src.name if src.parent == DOCS else str(src.relative_to(REPO_ROOT)).replace("\\", "/")
            total += _add_path(zf, src, prefix)
    print(f"Wrote {out_path} ({total} files)")
    return out_path


if __name__ == "__main__":
    build()
