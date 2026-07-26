"""Generate AgentDesk architecture diagram with clear AgentTeams hierarchy."""

from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "06_架构图.png"

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "Arial Unicode MS", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False


def box(ax, x, y, w, h, text, face, edge="#1F2937", fontsize=10, bold=False):
    patch = FancyBboxPatch(
        (x, y),
        w,
        h,
        boxstyle="round,pad=0.02,rounding_size=0.03",
        linewidth=1.2,
        edgecolor=edge,
        facecolor=face,
    )
    ax.add_patch(patch)
    weight = "bold" if bold else "normal"
    ax.text(x + w / 2, y + h / 2, text, ha="center", va="center", fontsize=fontsize, weight=weight)


def band(ax, y, h, label, color):
    ax.add_patch(
        FancyBboxPatch(
            (0.03, y),
            0.94,
            h,
            boxstyle="round,pad=0.01,rounding_size=0.02",
            linewidth=0,
            facecolor=color,
            alpha=0.35,
        )
    )
    ax.text(0.02, y + h / 2, label, ha="left", va="center", fontsize=11, weight="bold", color="#0F172A")


def arrow(ax, x1, y1, x2, y2):
    ax.annotate("", xy=(x2, y2), xytext=(x1, y1), arrowprops=dict(arrowstyle="->", color="#334155", lw=1.4))


def build() -> None:
    fig, ax = plt.subplots(figsize=(16, 10), dpi=160)
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")

    ax.text(0.5, 0.97, "AgentDesk 架构图 · AgentTeams 分层协同", ha="center", va="center", fontsize=18, weight="bold", color="#0B5CFF")

    band(ax, 0.86, 0.08, "任务输入", "#DBEAFE")
    box(ax, 0.18, 0.875, 0.24, 0.05, "抖音私信", "#EFF6FF")
    box(ax, 0.58, 0.875, 0.24, 0.05, "企业微信", "#EFF6FF")
    arrow(ax, 0.30, 0.875, 0.50, 0.84)
    arrow(ax, 0.70, 0.875, 0.50, 0.84)

    band(ax, 0.74, 0.07, "Manager", "#1E3A8A")
    box(ax, 0.30, 0.755, 0.40, 0.06, "DutyManager 值班长\n任务拆解 / 审批升级", "#DBEAFE", fontsize=11, bold=True)
    arrow(ax, 0.50, 0.755, 0.50, 0.73)

    band(ax, 0.62, 0.07, "Team Leader", "#2563EB")
    box(ax, 0.30, 0.635, 0.40, 0.06, "SessionTL 会话编排\n调度 / 上下文 / 状态机", "#BFDBFE", fontsize=11, bold=True)
    for x in (0.20, 0.50, 0.80):
        arrow(ax, 0.50, 0.635, x, 0.60)

    band(ax, 0.50, 0.08, "Workers", "#38BDF8")
    box(ax, 0.08, 0.515, 0.24, 0.06, "ChannelIngress\n渠道接入", "#E0F2FE", fontsize=10)
    box(ax, 0.38, 0.515, 0.24, 0.06, "TriageGuard\n意图风控", "#E0F2FE", fontsize=10)
    box(ax, 0.68, 0.515, 0.24, 0.06, "ActVerify\n执行核验", "#E0F2FE", fontsize=10)
    arrow(ax, 0.20, 0.515, 0.50, 0.47)
    arrow(ax, 0.50, 0.515, 0.50, 0.47)
    arrow(ax, 0.80, 0.515, 0.50, 0.47)

    band(ax, 0.36, 0.08, "Skill 能力层", "#14B8A6")
    skills = ["SessionNormalize", "IntentTriage", "ReplyPlan", "ChannelSend", "OutcomeVerify"]
    for i, name in enumerate(skills):
        box(ax, 0.06 + i * 0.18, 0.375, 0.15, 0.05, name, "#CCFBF1", fontsize=8.5)
    arrow(ax, 0.50, 0.375, 0.50, 0.33)

    band(ax, 0.22, 0.08, "工具层 / MCP 等价契约", "#94A3B8")
    box(ax, 0.10, 0.235, 0.22, 0.05, "抖音 Runtime", "#F1F5F9", fontsize=10)
    box(ax, 0.39, 0.235, 0.22, 0.05, "企微 Hook API", "#F1F5F9", fontsize=10)
    box(ax, 0.68, 0.235, 0.22, 0.05, "知识检索", "#F1F5F9", fontsize=10)
    arrow(ax, 0.50, 0.235, 0.50, 0.19)

    band(ax, 0.08, 0.08, "证据与审计", "#F59E0B")
    box(ax, 0.10, 0.095, 0.22, 0.05, "Trace / Log", "#FEF3C7", fontsize=10)
    box(ax, 0.39, 0.095, 0.22, 0.05, "审批闸门", "#FEF3C7", fontsize=10)
    box(ax, 0.68, 0.095, 0.22, 0.05, "案例沉淀", "#FEF3C7", fontsize=10)

    fig.tight_layout()
    fig.savefig(OUT, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    print(f"Wrote {OUT}")


if __name__ == "__main__":
    build()
