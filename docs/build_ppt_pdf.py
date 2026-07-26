"""Generate GOAI preliminary submission PDF deck."""

from __future__ import annotations

from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import landscape, A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.platypus import Image, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "02_方案PPT.pdf"
ARCH = ROOT / "06_架构图.png"

pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))

styles = getSampleStyleSheet()
TAG = ParagraphStyle("Tag", parent=styles["Normal"], fontName="STSong-Light", fontSize=10, textColor=colors.HexColor("#0B5CFF"))
TITLE = ParagraphStyle("Title", parent=styles["Title"], fontName="STSong-Light", fontSize=24, leading=30, spaceAfter=12)
SUB = ParagraphStyle("Sub", parent=styles["Normal"], fontName="STSong-Light", fontSize=12, leading=18, textColor=colors.HexColor("#475569"))
H1 = ParagraphStyle("H1", parent=styles["Heading1"], fontName="STSong-Light", fontSize=18, leading=24, spaceAfter=10, textColor=colors.HexColor("#0B5CFF"))
BODY = ParagraphStyle("Body", parent=styles["BodyText"], fontName="STSong-Light", fontSize=11, leading=18, spaceAfter=6)
BULLET = ParagraphStyle("Bullet", parent=BODY, leftIndent=12, spaceAfter=5)

SLIDES = [
    ("P1", "私域客服自治闭环（AgentDesk）", [
        "赛道：新智基座（Agent Infra）",
        "方向：智能客服自主闭环",
        "团队：AgentDesk",
        "仓库：https://github.com/icebreak119/AgentDesk",
    ], True),
    ("P2", "背景与痛点", [
        "私域客服会话分散在抖音、企微等渠道，人工切换成本高。",
        "自动回复常见问题是：发了但不可验证、短文本易串号、高风险无审批。",
        "单 Bot 只能聊天，无法完成拆解、协同、验证、审计的企业级闭环。",
    ], False),
    ("P3", "场景价值", [
        "目标用户：私域运营、客服团队、中小企业客服负责人。",
        "收益：提效接待、降低错发风险、支持审批审计、沉淀可复用 Skill。",
        "可复制：电商、教育、本地生活等私域场景均可迁移。",
    ], False),
    ("P4", "AgentTeams 分层架构", [
        "Manager：DutyManager 负责任务拆解与审批。",
        "Team Leader：SessionTL 负责调度、上下文与状态机。",
        "Workers：ChannelIngress / TriageGuard / ActVerify。",
        "下层 Skill 抽象能力，工具层通过 MCP 等价契约接入渠道 Runtime。",
    ], False, True),
    ("P5", "Agent 分工", [
        "DutyManager：拆解、升级、审批决策。",
        "SessionTL：状态机编排、上下文共享。",
        "ChannelIngress：多渠道入站归一。",
        "TriageGuard：意图识别、分级、风险判定。",
        "ActVerify：发送执行、结果核验、证据沉淀。",
    ], False),
    ("P6", "任务拆解与状态机", [
        "拆解链路：ingress → triage → plan/act → verify → digest。",
        "状态：pending → triaging → planning → acting → verifying → done/failed/escalated。",
        "上下文：TaskContext 传递 task_id、profile_id、中间结论与证据引用。",
    ], False),
    ("P7", "Skill 工程体系", [
        "SessionNormalize：多渠道会话归一。",
        "IntentTriage：意图识别与分级。",
        "ReplyPlan：回复/处置方案生成。",
        "ChannelSend：渠道发送，幂等、防串号。",
        "OutcomeVerify：结果核验与执行证据。",
    ], False),
    ("P8", "MCP 等价契约", [
        "channel.send_message：发送消息，带 idempotency_key。",
        "channel.query_session：查询会话状态。",
        "channel.fetch_history：拉取历史消息。",
        "迁移到 MCP 仅需协议适配，Skill 与 Agent 编排无需重写。",
    ], False),
    ("P9", "闭环演示剧本", [
        "主路径：咨询价格 → 低风险 → 自动回复 → 核验通过。",
        "高风险：退款/改账户 → 风控拦截 → 人工审批 → 执行或拒绝。",
        "扩展：企微复用同一编排，仅替换渠道适配器。",
    ], False),
    ("P10", "验证与审计", [
        "OutcomeVerify：回执 + DOM 二次校验，短文本必须一致。",
        "profile 级账号隔离，防止跨账号串号。",
        "ApprovalToken 控制高风险动作，支持审计与回滚。",
    ], False),
    ("P11", "可观测与上下文", [
        "共享状态：TaskContext + 会话状态机。",
        "轨迹可观测：Trace/Log 串联 agent → skill → tool。",
        "复赛：接入 RAG 与 AgentLoop 离线评估。",
    ], False),
    ("P12", "落地与开源计划", [
        "初赛：方案、Identity、Skill、闭环说明（已完成）。",
        "复赛：可执行 AgentTeams 代码包 + 抖音端到端 Demo。",
        "开源：Skill Schema、Identity 模板、工具契约、核验协议。",
    ], False),
]


def add_slide(story, page_no: str, title: str, bullets: list[str], cover: bool = False, with_arch: bool = False) -> None:
    if cover:
        story.append(Spacer(1, 3 * cm))
        story.append(Paragraph("GOAI 2026 · 新智基座赛道", TAG))
        story.append(Spacer(1, 0.5 * cm))
        story.append(Paragraph(title, TITLE))
        for item in bullets:
            story.append(Paragraph(item, SUB))
        return

    story.append(Paragraph(page_no, TAG))
    story.append(Paragraph(title, H1))
    story.append(Spacer(1, 0.15 * cm))
    for item in bullets:
        story.append(Paragraph(f"• {item}", BULLET))
    if with_arch and ARCH.exists():
        story.append(Spacer(1, 0.2 * cm))
        story.append(Image(str(ARCH), width=24 * cm, height=10.5 * cm))


def build() -> None:
    doc = SimpleDocTemplate(
        str(OUT),
        pagesize=landscape(A4),
        leftMargin=1.5 * cm,
        rightMargin=1.5 * cm,
        topMargin=1.2 * cm,
        bottomMargin=1.2 * cm,
        title="AgentDesk 初赛方案",
        author="AgentDesk Team",
    )
    story: list = []

    for idx, slide in enumerate(SLIDES):
        if idx > 0:
            story.append(PageBreak())
        if len(slide) == 4:
            page_no, title, bullets, cover = slide
            with_arch = False
        else:
            page_no, title, bullets, cover, with_arch = slide  # type: ignore[misc]
        add_slide(story, page_no, title, bullets, cover=cover, with_arch=with_arch)

    story.append(PageBreak())
    story.append(Paragraph("附录：评审维度对齐", H1))
    data = [
        ["评审维度", "AgentDesk 对应"],
        ["场景价值 25%", "私域客服真实痛点，抖音/企微可扩展"],
        ["多 Agent 闭环 25%", "5 Agent + 8 步闭环 + 审批回滚"],
        ["Skill 工程 25%", "5 Skill + Schema + 版本策略"],
        ["工程与安全 20%", "核验、隔离、证据、MCP 契约"],
        ["开源贡献 5%", "GitHub 仓库 + 可复用模板"],
    ]
    table = Table(data, colWidths=[5 * cm, 20 * cm])
    table.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (-1, -1), "STSong-Light"),
                ("FONTSIZE", (0, 0), (-1, -1), 10),
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#EAF2FF")),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]
        )
    )
    story.append(table)

    doc.build(story)
    print(f"Wrote {OUT}")


if __name__ == "__main__":
    build()
