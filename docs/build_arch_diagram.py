"""Generate AgentDesk architecture diagram (independent Runtime + AgentTeams)."""

from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "06_架构图.png"

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "Arial Unicode MS", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False


def box(ax, x, y, w, h, text, face, edge="#1F2937", fontsize=10, bold=False, text_color="#0F172A"):
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
    ax.text(x + w / 2, y + h / 2, text, ha="center", va="center", fontsize=fontsize, weight=weight, color=text_color)


def band(ax, y, h, label, color, *, alpha=0.32):
    ax.add_patch(
        FancyBboxPatch(
            (0.03, y),
            0.94,
            h,
            boxstyle="round,pad=0.01,rounding_size=0.02",
            linewidth=0,
            facecolor=color,
            alpha=alpha,
        )
    )
    ax.text(0.02, y + h / 2, label, ha="left", va="center", fontsize=11, weight="bold", color="#0F172A")


def badge(ax, x, y, text, face="#DCFCE7", edge="#16A34A", fontsize=8, text_color="#166534"):
    box(ax, x, y, 0.11, 0.028, text, face, edge=edge, fontsize=fontsize, bold=True, text_color=text_color)


def arrow(ax, x1, y1, x2, y2):
    ax.annotate("", xy=(x2, y2), xytext=(x1, y1), arrowprops=dict(arrowstyle="->", color="#334155", lw=1.4))


def build() -> None:
    fig, ax = plt.subplots(figsize=(16, 10.5), dpi=180)
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")

    ax.text(
        0.5,
        0.975,
        "AgentDesk 架构图 · 独立 Channel Runtime + AgentTeams",
        ha="center",
        va="center",
        fontsize=18,
        weight="bold",
        color="#0B5CFF",
    )
    ax.text(
        0.5,
        0.945,
        "初赛：Runtime + 参考编排器 + Skill 注册  |  复赛：AgentTeams 官方运行时 + Trace UI",
        ha="center",
        va="center",
        fontsize=10,
        color="#64748B",
    )

    band(ax, 0.865, 0.075, "任务输入", "#DBEAFE")
    box(ax, 0.16, 0.878, 0.28, 0.055, "抖音私信\nChannelIngress 入站", "#EFF6FF", fontsize=10, bold=True)
    box(ax, 0.56, 0.878, 0.28, 0.055, "企业微信\n复赛扩展渠道", "#F8FAFC", edge="#CBD5E1", fontsize=10)
    badge(ax, 0.395, 0.888, "初赛已接入")
    badge(ax, 0.795, 0.888, "复赛计划", face="#FEF3C7", edge="#D97706", text_color="#92400E")
    arrow(ax, 0.30, 0.878, 0.50, 0.845)
    arrow(ax, 0.70, 0.878, 0.50, 0.845)

    band(ax, 0.735, 0.075, "AgentTeams · Manager", "#1E3A8A", alpha=0.18)
    box(ax, 0.28, 0.752, 0.44, 0.065, "DutyManager 值班长\n任务拆解 / 审批升级 / 人工闸门", "#DBEAFE", fontsize=10, bold=True)
    badge(ax, 0.685, 0.762, "参考实现", face="#DCFCE7", edge="#16A34A")
    arrow(ax, 0.50, 0.752, 0.50, 0.725)

    band(ax, 0.615, 0.075, "AgentTeams · Team Leader", "#2563EB", alpha=0.16)
    box(ax, 0.28, 0.632, 0.44, 0.065, "SessionTL 会话编排\n调度 / TaskContext / 状态机", "#BFDBFE", fontsize=10, bold=True)
    badge(ax, 0.685, 0.642, "参考实现", face="#DCFCE7", edge="#16A34A")
    for x in (0.20, 0.50, 0.80):
        arrow(ax, 0.50, 0.632, x, 0.598)

    band(ax, 0.495, 0.085, "AgentTeams · Workers", "#38BDF8", alpha=0.16)
    box(ax, 0.07, 0.512, 0.25, 0.065, "ChannelIngress\n渠道接入 Worker", "#E0F2FE", fontsize=9.5)
    box(ax, 0.375, 0.512, 0.25, 0.065, "TriageGuard\n意图风控 Worker", "#E0F2FE", fontsize=9.5)
    box(ax, 0.68, 0.512, 0.25, 0.065, "ActVerify\n执行核验 Worker", "#E0F2FE", fontsize=9.5)
    arrow(ax, 0.20, 0.512, 0.50, 0.468)
    arrow(ax, 0.50, 0.512, 0.50, 0.468)
    arrow(ax, 0.80, 0.512, 0.50, 0.468)

    band(ax, 0.345, 0.085, "Skill 能力层", "#14B8A6", alpha=0.18)
    skills = ["SessionNormalize", "IntentTriage", "ReplyPlan", "ChannelSend", "OutcomeVerify"]
    for i, name in enumerate(skills):
        box(ax, 0.05 + i * 0.19, 0.372, 0.16, 0.05, name, "#CCFBF1", fontsize=8.3)
    badge(ax, 0.02, 0.382, "v0.1 可运行", face="#DCFCE7", edge="#16A34A", fontsize=7)
    arrow(ax, 0.50, 0.372, 0.50, 0.328)

    band(ax, 0.215, 0.095, "工具层 / MCP 等价契约", "#94A3B8", alpha=0.22)
    box(
        ax,
        0.04,
        0.228,
        0.22,
        0.06,
        "抖音 Runtime\n8765 /console",
        "#E0F2FE",
        edge="#0B5CFF",
        fontsize=9,
        bold=True,
    )
    box(ax, 0.28, 0.228, 0.22, 0.06, "orchestrator/\ndemo + trace.jsonl", "#DBEAFE", edge="#0B5CFF", fontsize=9, bold=True)
    box(ax, 0.52, 0.228, 0.22, 0.06, "contracts/*.json\nerrors/audit/idempotent", "#F1F5F9", fontsize=8.5)
    box(ax, 0.76, 0.228, 0.20, 0.06, "企微 / RAG\n复赛", "#F8FAFC", edge="#CBD5E1", fontsize=9)
    badge(ax, 0.195, 0.238, "已实现")
    badge(ax, 0.435, 0.238, "参考实现", face="#DCFCE7", edge="#16A34A")
    arrow(ax, 0.50, 0.228, 0.50, 0.185)

    band(ax, 0.075, 0.085, "证据与审计", "#F59E0B", alpha=0.18)
    box(ax, 0.08, 0.092, 0.25, 0.055, "trace.jsonl\n跨 Agent 链路", "#FEF3C7", fontsize=9)
    box(ax, 0.375, 0.092, 0.25, 0.055, "审批闸门\nApprovalToken", "#FEF3C7", fontsize=9)
    box(ax, 0.67, 0.092, 0.25, 0.055, "Trace UI\n复赛", "#FEF3C7", fontsize=9)
    badge(ax, 0.285, 0.098, "剧本B", face="#DCFCE7", edge="#16A34A", fontsize=7)

    fig.tight_layout()
    fig.savefig(OUT, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    print(f"Wrote {OUT}")


if __name__ == "__main__":
    build()
