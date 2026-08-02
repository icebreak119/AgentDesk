"""Build a self-contained AgentDesk preliminary-submission zip."""

from __future__ import annotations

import zipfile
import argparse
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DOCS = REPO_ROOT / "docs"
RUNTIME = REPO_ROOT / "runtime" / "douyin"
OUT_DIR = REPO_ROOT.parent
ZIP_NAME = f"AgentDesk_初赛提交_{datetime.now().strftime('%Y%m%d')}.zip"

ROOT_FILES = ["README.md", "LICENSE", "pytest.ini"]

# 文档根目录文件（作品简介、PDF、技术说明、运行证据和提交口径）
DOC_FILES = [
    "README.md",
    "01_作品简介.txt",
    "02_方案PPT.pdf",
    "02_方案PPT大纲.md",
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
    "14_独立化与Web登录方案.md",
    "15_初赛提交表单内容.md",
    "16_赛题核验清单.md",
    "17_PPT视觉设计说明.md",
    "build_arch_diagram.py",
    "build_ppt_assets.py",
    "build_ppt_pdf.py",
    "build_submission_screenshots.py",
    "build_submission_zip.py",
]

SKIP_PARTS = {
    "__pycache__",
    ".pytest_cache",
    ".git",
    "node_modules",
    "profiles",
    "logs",
    "avatars",
    "message_images",
    "message_emojis",
    "message_videos",
    ".env",
}

EXTRA_DIRS = [
    REPO_ROOT / "docs" / "contracts",
    REPO_ROOT / "docs" / "07_系统截图",
    REPO_ROOT / "skills",
    REPO_ROOT / "orchestrator",
    RUNTIME,
]


def _should_skip(path: Path) -> bool:
    parts = {part.lower() for part in path.parts}
    if any(part in SKIP_PARTS for part in parts):
        return True
    if path.name.startswith("test_trace_"):
        return True
    suffix = path.suffix.lower()
    if suffix in {".pyc", ".db", ".mp4", ".gif", ".webp", ".jpeg"}:
        return True
    if path.name.endswith(".db-wal") or path.name.endswith(".db-shm"):
        return True
    return False


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


def build(output: Path | None = None) -> Path:
    out_path = (output or (OUT_DIR / ZIP_NAME)).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    total = 0
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name in ROOT_FILES:
            path = REPO_ROOT / name
            if path.is_file():
                zf.write(path, arcname=name)
                total += 1
        for name in DOC_FILES:
            path = DOCS / name
            if path.is_file():
                zf.write(path, arcname=f"docs/{name}")
                total += 1
        for src in EXTRA_DIRS:
            if not src.exists():
                continue
            prefix = str(src.relative_to(REPO_ROOT)).replace("\\", "/")
            total += _add_path(zf, src, prefix)
    print(f"Wrote {out_path} ({total} files)")
    return out_path


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="打包 AgentDesk 初赛提交材料")
    parser.add_argument("--output", type=Path, help="指定 zip 输出路径")
    args = parser.parse_args()
    build(args.output)
