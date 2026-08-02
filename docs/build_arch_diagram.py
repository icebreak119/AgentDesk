"""Generate the submission architecture diagram for AgentDesk."""

from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "06_架构图.png"

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "Arial Unicode MS", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False

INK = "#173B3A"
MUTED = "#52606D"
TEAL = "#2F8F83"
BLUE = "#2A6F97"
CORAL = "#E76F51"
YELLOW = "#E9C46A"
MINT = "#DDF3ED"
SKY = "#DCECF7"
PEACH = "#FCE9E2"
PAPER = "#F7FAF9"


def rounded_box(ax, x, y, width, height, text, *, face, edge="none", size=10, weight="normal", color=INK):
    patch = FancyBboxPatch(
        (x, y),
        width,
        height,
        boxstyle="round,pad=0.02,rounding_size=0.10",
        linewidth=1.1 if edge != "none" else 0,
        edgecolor=edge,
        facecolor=face,
    )
    ax.add_patch(patch)
    ax.text(
        x + width / 2,
        y + height / 2,
        text,
        ha="center",
        va="center",
        fontsize=size,
        weight=weight,
        color=color,
        linespacing=1.25,
    )


def pill(ax, x, y, text, *, face, color=INK):
    width = max(0.95, 0.16 * len(text) + 0.35)
    rounded_box(ax, x, y, width, 0.28, text, face=face, edge="none", size=7.7, weight="bold", color=color)


def arrow(ax, start, end, *, label=""):
    ax.annotate(
        "",
        xy=end,
        xytext=start,
        arrowprops={"arrowstyle": "-|>", "lw": 1.45, "color": INK, "shrinkA": 3, "shrinkB": 3},
    )
    if label:
        ax.text(
            (start[0] + end[0]) / 2,
            (start[1] + end[1]) / 2 + 0.08,
            label,
            ha="center",
            va="bottom",
            fontsize=7.2,
            color=MUTED,
            bbox={"boxstyle": "round,pad=0.16", "facecolor": PAPER, "edgecolor": "none"},
        )


def band(ax, y, label, *, color):
    rounded_box(ax, 0.30, y, 1.32, 0.58, label, face=color, edge="none", size=9.5, weight="bold")


def build() -> None:
    fig, ax = plt.subplots(figsize=(16, 9), dpi=180)
    fig.patch.set_facecolor("white")
    ax.set_xlim(0, 16)
    ax.set_ylim(0, 9)
    ax.axis("off")

    ax.text(8, 8.62, "AgentDesk: 智能客服自主闭环架构", ha="center", va="center", fontsize=22, weight="bold", color=INK)
    ax.text(
        8,
        8.26,
        "初赛可验证: 抖音 Runtime + 参考编排器 + 7 Skill + Trace + 匿名 CaseDigest | 企微: 统一契约 / 离线剧本 | 复赛: 官方 AgentTeams、完整 RAG、Trace UI",
        ha="center",
        va="center",
        fontsize=9.5,
        color=MUTED,
    )

    band(ax, 7.32, "任务输入", color=SKY)
    rounded_box(ax, 2.05, 7.20, 4.35, 0.74, "抖音私信\n入站消息 -> SessionEvent\n初赛已接入", face="#EFF7FB", edge=BLUE, size=9.2, weight="bold")
    rounded_box(ax, 7.05, 7.20, 3.60, 0.74, "企业微信\n统一契约 / 离线剧本\n非真实渠道接入", face="#F3F6F8", edge="#B9C5CC", size=8.8, color=MUTED)
    rounded_box(ax, 11.30, 7.20, 2.95, 0.74, "跨渠道去重键\n匿名客户 + 归一内容\n5 分钟窗口", face="#F8FBFA", edge="#C9D8D5", size=8.2, color=MUTED)

    band(ax, 6.18, "Manager", color=MINT)
    rounded_box(ax, 3.00, 6.10, 9.80, 0.75, "DutyManager (Manager)\n任务拆解 | 风险升级 | 审批闸门", face="#ECF7F4", edge=TEAL, size=11.2, weight="bold")
    pill(ax, 11.40, 6.34, "参考实现", face="#DFF1E8", color=TEAL)
    arrow(ax, (4.20, 7.20), (6.15, 6.86), label="归一任务")
    arrow(ax, (8.85, 7.20), (8.00, 6.86), label="统一契约")

    band(ax, 5.02, "Team Leader", color="#E7EEF7")
    rounded_box(ax, 3.00, 4.94, 9.80, 0.75, "SessionTL (Team Leader)\nTaskContext 共享 | 去重拦截 | 状态机 | 4 Worker 调度", face="#EEF5FB", edge=BLUE, size=11.2, weight="bold")
    pill(ax, 11.40, 5.18, "参考实现", face="#DFF1E8", color=TEAL)
    arrow(ax, (8.00, 6.10), (8.00, 5.69))

    band(ax, 3.62, "Workers", color="#E8F4F4")
    worker_y = 3.72
    workers = (
        (1.30, "ChannelIngress\n归一、跨渠道去重", "#F0FAF9", TEAL),
        (4.55, "TriageGuard\n意图分级、方案", "#FFF9E9", YELLOW),
        (7.80, "ActVerify\n执行、核验、客户确认", "#FFF4EF", CORAL),
        (11.05, "CaseLearning\n标签检索、匿名沉淀", "#EEF5FB", BLUE),
    )
    for x, label, face, edge in workers:
        rounded_box(ax, x, worker_y, 2.65, 0.74, label, face=face, edge=edge, size=8.6, weight="bold")
    for start_x, end_x in ((5.80, 2.63), (7.15, 5.88), (8.85, 9.13), (10.20, 12.38)):
        arrow(ax, (start_x, 4.94), (end_x, 4.46))

    band(ax, 2.42, "Skill", color="#F9F3DF")
    skill_y = 2.50
    skill_x = [0.95, 3.00, 5.05, 7.10, 9.15, 11.20, 13.25]
    skill_names = [
        "Session\nNormalize",
        "Intent\nTriage",
        "Reply\nPlan",
        "Channel\nSend",
        "Outcome\nVerify",
        "Customer\nConfirm",
        "Case\nDigest",
    ]
    for x, name in zip(skill_x, skill_names):
        rounded_box(ax, x, skill_y, 1.82, 0.62, name, face="#FFF9E9", edge=YELLOW, size=7.8, weight="bold")
    for start_x, end_x in (
        (2.63, 1.86),
        (5.88, 3.91),
        (5.88, 5.96),
        (9.13, 8.01),
        (9.13, 10.06),
        (9.13, 12.11),
        (12.38, 14.16),
    ):
        arrow(ax, (start_x, 3.72), (end_x, 3.12))

    band(ax, 1.26, "工具 / 契约", color=PEACH)
    rounded_box(ax, 1.25, 1.30, 3.05, 0.62, "抖音 Channel Runtime\nHTTP 8765 / Web Console", face="#FFF4EF", edge=CORAL, size=9.0, weight="bold")
    rounded_box(ax, 5.15, 1.30, 3.20, 0.62, "MCP 等价契约\nSchema | 幂等 | 审计 | 错误码", face="#FFF4EF", edge=CORAL, size=8.8, weight="bold")
    rounded_box(ax, 10.20, 1.30, 4.10, 0.62, "企业微信统一契约 / 离线剧本\n渠道 Adapter 待真实接入", face="#F5F7F8", edge="#B9C5CC", size=8.5, color=MUTED)
    arrow(ax, (8.01, 2.50), (2.78, 1.92))
    arrow(ax, (8.01, 2.50), (6.75, 1.92))

    band(ax, 0.28, "证据 / 安全", color="#E8ECEB")
    rounded_box(ax, 1.10, 0.31, 2.90, 0.56, "trace.jsonl\ntask / agent / skill / status", face="#F7FAF9", edge="#AFC4BF", size=8.2, weight="bold")
    rounded_box(ax, 4.70, 0.31, 2.90, 0.56, "审批与隔离\nApprovalToken | profile scope", face="#F7FAF9", edge="#AFC4BF", size=8.2, weight="bold")
    rounded_box(ax, 8.30, 0.31, 2.90, 0.56, "执行证据\nreceipt | verify | confirm", face="#F7FAF9", edge="#AFC4BF", size=8.2, weight="bold")
    rounded_box(ax, 11.90, 0.31, 2.90, 0.56, "案例档案 JSONL\n标签 | case:// | 隐私校验", face="#EEF5FB", edge=BLUE, size=8.0, weight="bold")
    arrow(ax, (6.75, 1.30), (6.15, 0.87))
    arrow(ax, (14.16, 2.50), (13.35, 0.87))

    fig.savefig(OUT, dpi=180, bbox_inches="tight", pad_inches=0.12, facecolor="white")
    plt.close(fig)
    print(f"Wrote {OUT}")


if __name__ == "__main__":
    build()
