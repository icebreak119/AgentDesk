"""Generate AgentDesk architecture diagram with clear AgentTeams hierarchy."""

from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "06_架构图.png"

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "Arial Unicode MS", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False


def box(ax, x, y, w, h, text, face, edge="#1F2937", fontsize=10, bold=False):
    patch = FancyBboxPatch(
        (x, y),
        w,
        h,
        boxstyle="round,pad=0.02,rounding_size=0.08",
        linewidth=1.2,
        edgecolor=edge,
        facecolor=face,
    )
    ax.add_patch(patch)
    weight = "bold" if bold else "normal"
    ax.text(x + w / 2, y + h / 2, text, ha="center", va="center", fontsize=fontsize, weight=weight)


def band(ax, y, h, label, color):
    patch = FancyBboxPatch(
        (0.4, y),
        15.2,
        h,
        boxstyle="round,pad=0.01,rounding_size=0.05",
        linewidth=0,
        facecolor=color,
        alpha=0.35,
    )
    ax.add_patch(patch)
    ax.text(0.55, y + h - 0.18, label, fontsize=11, weight="bold", color="#0F172A")


def arrow(ax, x1, y1, x2, y2):
    ax.add_patch(
        FancyArrowPatch(
            (x1, y1),
            (x2, y2),
            arrowstyle="-|>",
            mutation_scale=12,
            linewidth=1.2,
            color="#334155",
        )
    )


def build() -> None:
    fig, ax = plt.subplots(figsize=(16, 10), dpi=180)
    ax.set_xlim(0, 16)
    ax.set_ylim(0, 10)
    ax.axis("off")

    ax.text(8, 9.55, "AgentDesk 架构图 · AgentTeams 分层协同", ha="center", va="center", fontsize=18, weight="bold", color="#0B5CFF")

    band(ax, 8.55, 0.75, "任务输入", "#DBEAFE")
    box(ax, 3.0, 8.72, 2.4, 0.45, "抖音私信", "#EFF6FF")
    box(ax, 10.6, 8.72, 2.4, 0.45, "企业微信", "#EFF6FF")

    band(ax, 7.45, 0.95, "Manager", "#1E3A8A")
    box(ax, 5.2, 7.62, 5.6, 0.65, "DutyManager 值班长\n任务拆解 / 审批升级", "#1D4ED8", edge="#1E3A8A", fontsize=11, bold=True)

    band(ax, 6.25, 0.95, "Team Leader", "#2563EB")
    box(ax, 5.0, 6.42, 6.0, 0.65, "SessionTL 会话编排\n调度 / 上下文 / 状态机", "#3B82F6", edge="#1D4ED8", fontsize=11, bold=True)

    band(ax, 4.85, 1.15, "Workers", "#38BDF8")
    box(ax, 1.0, 5.05, 3.8, 0.65, "ChannelIngress\n渠道接入", "#E0F2FE")
    box(ax, 6.1, 5.05, 3.8, 0.65, "TriageGuard\n意图风控", "#E0F2FE")
    box(ax, 11.2, 5.05, 3.8, 0.65, "ActVerify\n执行核验", "#E0F2FE")

    band(ax, 3.35, 0.95, "Skill 能力层", "#14B8A6")
    for i, name in enumerate(["SessionNormalize", "IntentTriage", "ReplyPlan", "ChannelSend", "OutcomeVerify"]):
        box(ax, 0.8 + i * 3.0, 3.95, 2.5, 0.45, name, "#CCFBF1", fontsize=9)

    band(ax, 2.55, 0.95, "工具层 / MCP 等价契约", "#94A3B8")
    box(ax, 1.5, 2.95, 3.8, 0.45, "抖音 Runtime", "#F1F5F9")
    box(ax, 6.1, 2.95, 3.8, 0.45, "企微 Hook API", "#F1F5F9")
    box(ax, 10.7, 2.95, 3.8, 0.45, "知识检索", "#F1F5F9")

    band(ax, 1.45, 0.95, "证据与审计", "#F59E0B")
    box(ax, 1.5, 1.05, 3.8, 0.45, "Trace / Log", "#FEF3C7")
    box(ax, 6.1, 1.05, 3.8, 0.45, "审批闸门", "#FEF3C7")
    box(ax, 10.7, 1.05, 3.8, 0.45, "案例沉淀", "#FEF3C7")

    arrow(ax, 4.2, 8.72, 7.2, 8.27)
    arrow(ax, 11.8, 8.72, 8.8, 8.27)
    arrow(ax, 8.0, 7.62, 8.0, 7.07)
    arrow(ax, 8.0, 6.42, 2.9, 5.7)
    arrow(ax, 8.0, 6.42, 8.0, 5.7)
    arrow(ax, 8.0, 6.42, 13.1, 5.7)
    arrow(ax, 8.0, 5.05, 8.0, 4.45)
    arrow(ax, 8.0, 3.95, 8.0, 3.45)
    arrow(ax, 8.0, 2.95, 8.0, 1.55)

    fig.savefig(OUT, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    print(f"Wrote {OUT}")


if __name__ == "__main__":
    build()
