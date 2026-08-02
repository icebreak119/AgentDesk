"""Generate the competition-facing AgentDesk proposal PDF.

The layout deliberately uses a small number of high-contrast editorial
compositions instead of repeating a generic three-card template.  Claims are
kept aligned with the locally runnable runtime, orchestration scripts, Skills,
Trace evidence, and tests in this repository.
"""

from __future__ import annotations

import math
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas
from reportlab.platypus import Paragraph

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "02_方案PPT.pdf"
ARCH = ROOT / "06_架构图.png"
HERO = ROOT / "assets" / "agentdesk_control_plane.png"
LOGIN_SCREEN = ROOT / "07_系统截图" / "01_托管账号登录控制面.png"
APPROVAL_TRACE = ROOT / "07_系统截图" / "04_审批闭环Trace.png"
VERIFY_TRACE = ROOT / "07_系统截图" / "05_执行核验Trace.png"
MULTICHANNEL_TRACE = ROOT / "07_系统截图" / "06_跨渠道去重与案例复用Trace.png"

PAGE_W, PAGE_H = landscape(A4)

# A local Chinese sans font makes the title system feel more deliberate than
# the default CID serif font. The CID fallback keeps the generator portable.
FONT_PATH = Path(r"C:\Windows\Fonts\simhei.ttf")
BOLD_FONT_PATH = Path(r"C:\Windows\Fonts\Dengb.ttf")
if FONT_PATH.exists():
    pdfmetrics.registerFont(TTFont("AgentDeskSans", str(FONT_PATH)))
    FONT = "AgentDeskSans"
else:
    pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
    FONT = "STSong-Light"
if BOLD_FONT_PATH.exists():
    pdfmetrics.registerFont(TTFont("AgentDeskBold", str(BOLD_FONT_PATH)))
    FONT_BOLD = "AgentDeskBold"
else:
    FONT_BOLD = FONT
MONO = "Courier"

INK = colors.HexColor("#10151B")
INK_SOFT = colors.HexColor("#1B242D")
PAPER = colors.HexColor("#F4F1EA")
WARM_WHITE = colors.HexColor("#FCFAF5")
MIST = colors.HexColor("#E4E1D8")
MUTED = colors.HexColor("#657078")
LINE = colors.HexColor("#CFCBC2")
MINT = colors.HexColor("#35C9AD")
MINT_LIGHT = colors.HexColor("#D9F2EC")
CYAN = colors.HexColor("#3F9EF4")
CYAN_LIGHT = colors.HexColor("#DCECFB")
CORAL = colors.HexColor("#FF6858")
CORAL_LIGHT = colors.HexColor("#FFE1DA")
GOLD = colors.HexColor("#F4B842")
GOLD_LIGHT = colors.HexColor("#FFF0CD")
INK_LINE = colors.HexColor("#31404A")
GRID = colors.HexColor("#222D35")


def text_style(
    name: str,
    size: float,
    leading: float,
    color: colors.Color,
    alignment: int = TA_LEFT,
    font: str = FONT,
) -> ParagraphStyle:
    return ParagraphStyle(
        name,
        fontName=font,
        fontSize=size,
        leading=leading,
        textColor=color,
        alignment=alignment,
        spaceAfter=0,
        spaceBefore=0,
    )


TITLE = text_style("title", 27, 35, INK, font=FONT_BOLD)
TITLE_DARK = text_style("title-dark", 27, 35, WARM_WHITE, font=FONT_BOLD)
HERO_TITLE = text_style("hero-title", 34, 43, WARM_WHITE, font=FONT_BOLD)
HERO_SUB = text_style("hero-sub", 13.2, 20, colors.HexColor("#BAC7C8"))
BODY = text_style("body", 11, 16, INK)
BODY_DARK = text_style("body-dark", 11, 16, colors.HexColor("#CFD9D8"))
BODY_SMALL = text_style("body-small", 9.2, 13, MUTED)
BODY_SMALL_DARK = text_style("body-small-dark", 9.2, 13, colors.HexColor("#AABABA"))
KICKER = text_style("kicker", 9.2, 12, MUTED, font=MONO)
KICKER_DARK = text_style("kicker-dark", 9.2, 12, colors.HexColor("#A8B9B8"), font=MONO)
CARD_TITLE = text_style("card-title", 13.5, 18, INK, font=FONT_BOLD)
CARD_BODY = text_style("card-body", 10.1, 14.5, MUTED)
CODE = text_style("code", 9.1, 13.2, WARM_WHITE, font=MONO)
CODE_LIGHT = text_style("code-light", 9.2, 13, INK, font=MONO)


def paragraph(c: canvas.Canvas, content: str, x: float, top: float, width: float, style: ParagraphStyle) -> float:
    """Draw a Platypus paragraph from a top-left anchor and return its height."""
    item = Paragraph(content, style)
    _, height = item.wrap(width, PAGE_H)
    item.drawOn(c, x, top - height)
    return height


def fill_page(c: canvas.Canvas, color: colors.Color) -> None:
    c.setFillColor(color)
    c.rect(0, 0, PAGE_W, PAGE_H, fill=1, stroke=0)


def rounded(
    c: canvas.Canvas,
    x: float,
    y: float,
    width: float,
    height: float,
    *,
    fill: colors.Color,
    stroke: colors.Color | None = None,
    radius: float = 8,
    line_width: float = 1,
) -> None:
    c.setFillColor(fill)
    c.setStrokeColor(stroke or fill)
    c.setLineWidth(line_width)
    c.roundRect(x, y, width, height, radius, fill=1, stroke=1 if stroke else 0)


def pill(
    c: canvas.Canvas,
    label: str,
    x: float,
    y: float,
    *,
    fill: colors.Color,
    text_color: colors.Color,
    min_width: float = 78,
) -> float:
    c.setFont(FONT_BOLD, 8.6)
    width = max(min_width, c.stringWidth(label, FONT_BOLD, 8.6) + 22)
    rounded(c, x, y, width, 21, fill=fill, radius=10)
    c.setFillColor(text_color)
    c.drawCentredString(x + width / 2, y + 6.7, label)
    return width


def line(c: canvas.Canvas, x1: float, y1: float, x2: float, y2: float, color: colors.Color, width: float = 1) -> None:
    c.setStrokeColor(color)
    c.setLineWidth(width)
    c.line(x1, y1, x2, y2)


def arrow(c: canvas.Canvas, x1: float, y1: float, x2: float, y2: float, color: colors.Color, width: float = 1.6) -> None:
    line(c, x1, y1, x2, y2, color, width)
    angle = math.atan2(y2 - y1, x2 - x1)
    head = 7.5
    for offset in (math.pi * 0.82, -math.pi * 0.82):
        c.line(x2, y2, x2 + head * math.cos(angle + offset), y2 + head * math.sin(angle + offset))


def soft_grid(c: canvas.Canvas, color: colors.Color, spacing: int = 34) -> None:
    c.setStrokeColor(color)
    c.setLineWidth(0.45)
    for x in range(0, int(PAGE_W) + spacing, spacing):
        c.line(x, 0, x, PAGE_H)
    for y in range(0, int(PAGE_H) + spacing, spacing):
        c.line(0, y, PAGE_W, y)


def footer(c: canvas.Canvas, page_no: int, dark: bool = False) -> None:
    color = colors.HexColor("#8F9B9B") if dark else MUTED
    rule = colors.HexColor("#34434B") if dark else LINE
    line(c, 48, 39, PAGE_W - 48, 39, rule, 0.7)
    c.setFillColor(color)
    c.setFont(MONO, 8.2)
    c.drawString(48, 23, "AGENTDESK / AGENT INFRA / PRELIMINARY")
    c.drawRightString(PAGE_W - 48, 23, f"{page_no:02d} / 12")


def light_header(c: canvas.Canvas, page_no: int, section: str, title: str, subtitle: str = "") -> None:
    fill_page(c, PAPER)
    c.setFillColor(CORAL)
    c.rect(0, PAGE_H - 8, PAGE_W, 8, fill=1, stroke=0)
    c.setFillColor(CORAL)
    c.rect(48, PAGE_H - 58, 28, 4, fill=1, stroke=0)
    paragraph(c, section.upper(), 86, PAGE_H - 48, 270, KICKER)
    c.setFillColor(MUTED)
    c.setFont(MONO, 8.4)
    c.drawRightString(PAGE_W - 48, PAGE_H - 47, f"PAGE {page_no:02d}")
    paragraph(c, title, 48, PAGE_H - 84, PAGE_W - 96, TITLE)
    if subtitle:
        paragraph(c, subtitle, 48, PAGE_H - 126, PAGE_W - 96, BODY_SMALL)
    footer(c, page_no)


def dark_header(c: canvas.Canvas, page_no: int, section: str, title: str, subtitle: str = "") -> None:
    fill_page(c, INK)
    soft_grid(c, GRID)
    c.setFillColor(MINT)
    c.rect(0, PAGE_H - 8, PAGE_W, 8, fill=1, stroke=0)
    c.setFillColor(MINT)
    c.rect(48, PAGE_H - 58, 28, 4, fill=1, stroke=0)
    paragraph(c, section.upper(), 86, PAGE_H - 48, 310, KICKER_DARK)
    c.setFillColor(colors.HexColor("#97A6A8"))
    c.setFont(MONO, 8.4)
    c.drawRightString(PAGE_W - 48, PAGE_H - 47, f"PAGE {page_no:02d}")
    paragraph(c, title, 48, PAGE_H - 84, PAGE_W - 96, TITLE_DARK)
    if subtitle:
        paragraph(c, subtitle, 48, PAGE_H - 126, PAGE_W - 96, BODY_SMALL_DARK)
    footer(c, page_no, dark=True)


def image_contain(c: canvas.Canvas, path: Path, x: float, y: float, width: float, height: float) -> None:
    image = ImageReader(str(path))
    image_w, image_h = image.getSize()
    scale = min(width / image_w, height / image_h)
    draw_w, draw_h = image_w * scale, image_h * scale
    c.drawImage(image, x + (width - draw_w) / 2, y + (height - draw_h) / 2, draw_w, draw_h, mask="auto")


def image_cover(c: canvas.Canvas, path: Path, x: float, y: float, width: float, height: float) -> None:
    image = ImageReader(str(path))
    image_w, image_h = image.getSize()
    scale = max(width / image_w, height / image_h)
    draw_w, draw_h = image_w * scale, image_h * scale
    draw_x = x + (width - draw_w) / 2
    draw_y = y + (height - draw_h) / 2
    c.saveState()
    clip = c.beginPath()
    clip.rect(x, y, width, height)
    c.clipPath(clip, stroke=0, fill=0)
    c.drawImage(image, draw_x, draw_y, draw_w, draw_h, mask="auto")
    c.restoreState()


def screen(c: canvas.Canvas, path: Path, x: float, y: float, width: float, height: float, label: str, accent: colors.Color) -> None:
    """Place an image in a quiet browser-style evidence frame."""
    rounded(c, x, y, width, height, fill=WARM_WHITE, stroke=LINE, radius=10, line_width=0.9)
    rounded(c, x + 7, y + height - 26, width - 14, 19, fill=INK_SOFT, radius=5)
    for offset, color in ((18, CORAL), (29, GOLD), (40, MINT)):
        c.setFillColor(color)
        c.circle(x + offset, y + height - 16.5, 3, fill=1, stroke=0)
    c.setFillColor(colors.HexColor("#C7D2D2"))
    c.setFont(MONO, 7.2)
    c.drawString(x + 55, y + height - 19.6, label)
    c.setFillColor(accent)
    c.rect(x + 7, y + height - 28, width - 14, 2, fill=1, stroke=0)
    c.setFillColor(colors.HexColor("#ECEBE7"))
    c.rect(x + 7, y + 7, width - 14, height - 36, fill=1, stroke=0)
    image_contain(c, path, x + 9, y + 9, width - 18, height - 40)


def metric(c: canvas.Canvas, x: float, y: float, value: str, label: str, color: colors.Color, dark: bool = False) -> None:
    c.setFillColor(color)
    c.setFont(FONT_BOLD, 26)
    c.drawString(x, y, value)
    c.setFillColor(colors.HexColor("#B3C1C0") if dark else MUTED)
    c.setFont(FONT, 9.2)
    c.drawString(x, y - 16, label)


def small_label(c: canvas.Canvas, x: float, y: float, number: str, heading: str, body: str, color: colors.Color, dark: bool = False) -> None:
    body_style = BODY_SMALL_DARK if dark else BODY_SMALL
    c.setFillColor(color)
    c.setFont(MONO, 9.5)
    c.drawString(x, y, number)
    c.setFillColor(WARM_WHITE if dark else INK)
    c.setFont(FONT_BOLD, 12)
    c.drawString(x + 33, y - 0.7, heading)
    paragraph(c, body, x + 33, y - 16, 182, body_style)


def cover(c: canvas.Canvas) -> None:
    fill_page(c, INK)
    soft_grid(c, GRID)
    c.setFillColor(CORAL)
    c.rect(0, PAGE_H - 8, PAGE_W, 8, fill=1, stroke=0)
    pill(c, "AGENT INFRA / 2026", 48, PAGE_H - 55, fill=MINT, text_color=INK, min_width=145)
    paragraph(c, "私域客服自治工作台<br/>AgentDesk", 48, 457, 365, HERO_TITLE)
    paragraph(c, "从一条私信，到一份可核验的闭环。", 48, 356, 350, text_style("cover-key", 17, 24, MINT, font=FONT_BOLD))
    paragraph(
        c,
        "面向抖音私信与企业微信扩展的多 Agent 客服基础设施。<br/>把任务拆解、风险审批、工具执行和结果证据纳入同一条生产化链路。",
        48,
        314,
        350,
        HERO_SUB,
    )
    if HERO.exists():
        rounded(c, 421, 159, 372, 250, fill=INK_SOFT, stroke=INK_LINE, radius=14, line_width=1.1)
        image_cover(c, HERO, 427, 165, 360, 238)
        c.setFillColor(MINT)
        c.rect(427, 165, 4, 238, fill=1, stroke=0)
        c.setFillColor(colors.HexColor("#C7D5D3"))
        c.setFont(MONO, 8.4)
        c.drawString(443, 180, "CONTROL PLANE / MESSAGE -> EVIDENCE")
    else:
        rounded(c, 421, 159, 372, 250, fill=INK_SOFT, stroke=INK_LINE, radius=14, line_width=1.1)
    line(c, 48, 130, PAGE_W - 48, 130, INK_LINE, 0.9)
    metric(c, 48, 87, "06", "Agent Identity", MINT, dark=True)
    metric(c, 169, 87, "07", "可复用 Skill", GOLD, dark=True)
    metric(c, 300, 87, "56", "pytest 断言", CORAL, dark=True)
    c.setFillColor(colors.HexColor("#9CAAAA"))
    c.setFont(MONO, 8.5)
    c.drawRightString(PAGE_W - 48, 92, "DOUYIN RUNTIME / REFERENCE ORCHESTRATOR / TRACE")
    c.drawRightString(PAGE_W - 48, 69, "https://github.com/icebreak119/AgentDesk")
    c.setFillColor(colors.HexColor("#6C7D80"))
    c.setFont(MONO, 8.2)
    c.drawString(48, 33, "GOAI 2026  |  新智基座  |  智能客服自主闭环")
    c.drawRightString(PAGE_W - 48, 33, "PRELIMINARY SUBMISSION")


def scene_problem(c: canvas.Canvas) -> None:
    light_header(c, 2, "01 / Problem Framing", "客服真正难的，不是聊，而是完成。", "企业级客服任务必须跨越会话接入、意图判断、风险门控、执行回执与结果核验。")
    # Three deliberately different failure modes, not another row of equal cards.
    rounded(c, 48, 230, 224, 143, fill=INK, radius=10)
    c.setFillColor(MINT)
    c.setFont(FONT_BOLD, 27)
    c.drawString(68, 326, "01")
    c.setFillColor(WARM_WHITE)
    c.setFont(FONT_BOLD, 15)
    c.drawString(68, 299, "上下文被切碎")
    c.setFillColor(colors.HexColor("#B9C9C7"))
    c.setFont(FONT, 10)
    c.drawString(68, 278, "抖音、企微等渠道各自承载会话")
    c.drawString(68, 261, "人工切换造成遗漏和重复处理。")
    for x, y, color in ((207, 315, CYAN), (230, 283, GOLD), (200, 247, CORAL)):
        c.setFillColor(color)
        c.circle(x, y, 8, fill=1, stroke=0)
        line(c, x - 29, y, x - 9, y, color, 2)

    rounded(c, 291, 230, 230, 143, fill=CORAL, radius=10)
    c.setFillColor(INK)
    c.setFont(FONT_BOLD, 48)
    c.drawString(312, 291, "!=")
    c.setFont(FONT_BOLD, 15)
    c.drawString(390, 304, "发送成功")
    c.drawString(390, 282, "服务完成")
    c.setFont(FONT, 10)
    c.drawString(312, 251, "一次回复无法证明目标会话、回执与结果一致。")

    rounded(c, 540, 230, 254, 143, fill=WARM_WHITE, stroke=LINE, radius=10, line_width=0.9)
    c.setFillColor(CORAL)
    c.setFont(MONO, 9.5)
    c.drawString(560, 329, "03 / RISK ACTION")
    c.setFillColor(INK)
    c.setFont(FONT_BOLD, 15)
    c.drawString(560, 300, "退款和账户变更")
    c.drawString(560, 279, "不能直接交给自动化")
    c.setFillColor(MUTED)
    c.setFont(FONT, 10)
    c.drawString(560, 252, "需要审批令牌、拒绝路径和审计证据。")
    c.setFillColor(CORAL_LIGHT)
    c.roundRect(731, 284, 37, 39, 7, fill=1, stroke=0)
    c.setStrokeColor(CORAL)
    c.setLineWidth(2)
    c.circle(749.5, 304, 8, fill=0, stroke=1)
    c.line(741.5, 304, 757.5, 304)
    c.line(749.5, 296, 749.5, 312)

    rounded(c, 48, 111, 746, 76, fill=INK_SOFT, radius=10)
    paragraph(c, "企业级闭环 = <font color='#35C9AD'>任务拆解</font> -> 上下文传递 -> 工具调用 -> <font color='#F4B842'>结果核验</font> -> <font color='#FF6858'>安全审计</font>", 77, 160, 688, text_style("problem-chain", 16, 22, WARM_WHITE, TA_CENTER, FONT_BOLD))
    paragraph(c, "目标用户：私域运营、客服团队和中小企业客服负责人。", 48, 82, 746, BODY_SMALL)


def phase_boundary(c: canvas.Canvas) -> None:
    dark_header(c, 3, "02 / Delivery Boundary", "先证明，再扩展。", "初赛提交只陈述已能本地核验的路径，把未来能力明确标注为下一阶段。")
    c.setFillColor(colors.HexColor("#7B8889"))
    c.setFont(MONO, 8.5)
    c.saveState()
    c.translate(29, 270)
    c.rotate(90)
    c.drawString(0, 0, "PROVE FIRST")
    c.restoreState()

    rounded(c, 65, 173, 332, 223, fill=MINT, radius=12)
    c.setFillColor(INK)
    c.setFont(MONO, 9)
    c.drawString(87, 365, "NOW / VERIFIABLE")
    c.setFont(FONT_BOLD, 21)
    c.drawString(87, 327, "可直接核验的工程证据")
    c.setFont(FONT, 11.1)
    for y, item in zip((292, 267, 242, 217, 192), (
        "抖音 Channel Runtime 可独立启动",
        "6 个 Agent Identity + 参考编排器",
        "7 个 Skill + JSON Schema",
        "JSONL Trace + A/B/C 剧本",
        "Web Console 创建并查看本机登录 Job",
    )):
        c.setFillColor(INK)
        c.circle(91, y + 3, 3.6, fill=1, stroke=0)
        c.drawString(105, y - 1, item)

    rounded(c, 432, 173, 362, 223, fill=INK_SOFT, stroke=INK_LINE, radius=12, line_width=1.1)
    c.setFillColor(CYAN)
    c.setFont(MONO, 9)
    c.drawString(455, 365, "NEXT / DELIBERATE ROADMAP")
    c.setFillColor(WARM_WHITE)
    c.setFont(FONT_BOLD, 21)
    c.drawString(455, 327, "已设计，复赛继续实现")
    for y, item in zip((292, 267, 242, 217, 192), (
        "企业微信真实渠道适配器",
        "官方 AgentTeams Runtime 接入",
        "知识库 RAG / 长期记忆",
        "生产 Task DB 与 Trace Web UI",
        "完整 RAG / 长期记忆与系统化评测",
    )):
        c.setFillColor(CYAN)
        c.rect(455, y, 7, 7, fill=1, stroke=0)
        c.setFillColor(colors.HexColor("#D2DDDD"))
        c.setFont(FONT, 11.1)
        c.drawString(475, y - 1, item)

    line(c, 65, 132, 794, 132, INK_LINE, 0.8)
    paragraph(c, "真实性不是降低愿景，而是把每一个阶段都变成可审计、可复用、可继续生长的交付。", 65, 105, 729, text_style("boundary-final", 14.5, 20, WARM_WHITE, TA_CENTER, FONT_BOLD))


def agent_team(c: canvas.Canvas) -> None:
    light_header(c, 4, "03 / Agent Orchestration", "让角色协作，不让任务失控。", "Manager -> Team Leader -> Worker 的分层用于任务拆解、上下文共享、风险升级和执行回收。")
    c.setFillColor(MUTED)
    c.setFont(MONO, 8.5)
    c.saveState()
    c.translate(28, 223)
    c.rotate(90)
    c.drawString(0, 0, "AGENTTEAMS MAPPING")
    c.restoreState()

    # Central orchestration spine.
    rounded(c, 249, 314, 345, 63, fill=INK, radius=10)
    c.setFillColor(MINT)
    c.setFont(MONO, 8.6)
    c.drawCentredString(421.5, 354, "MANAGER")
    c.setFillColor(WARM_WHITE)
    c.setFont(FONT_BOLD, 15)
    c.drawCentredString(421.5, 332, "DutyManager")
    c.setFillColor(colors.HexColor("#BFD0CF"))
    c.setFont(FONT, 9.5)
    c.drawCentredString(421.5, 315, "任务拆解 | 风险升级 | 审批门控")
    arrow(c, 421.5, 314, 421.5, 282, INK, 1.8)
    rounded(c, 249, 221, 345, 60, fill=CYAN_LIGHT, stroke=CYAN, radius=10, line_width=1)
    c.setFillColor(CYAN)
    c.setFont(MONO, 8.6)
    c.drawCentredString(421.5, 260, "TEAM LEADER")
    c.setFillColor(INK)
    c.setFont(FONT_BOLD, 15)
    c.drawCentredString(421.5, 238, "SessionTL")
    c.setFillColor(MUTED)
    c.setFont(FONT, 9.5)
    c.drawCentredString(421.5, 221, "TaskContext 共享 | 状态机 | Worker 调度")

    workers = (
        (48, MINT_LIGHT, MINT, "ChannelIngress", "会话归一\n跨渠道去重"),
        (238, GOLD_LIGHT, GOLD, "TriageGuard", "意图分级\n风险与方案"),
        (428, CORAL_LIGHT, CORAL, "ActVerify", "执行核验\n客户确认"),
        (618, CYAN_LIGHT, CYAN, "CaseLearning", "标签检索\n匿名沉淀"),
    )
    for x, fill, accent, name, body in workers:
        arrow(c, 421.5, 221, x + 87, 186, INK, 1.4)
        rounded(c, x, 106, 174, 80, fill=fill, stroke=accent, radius=9, line_width=1)
        c.setFillColor(accent)
        c.setFont(MONO, 8.2)
        c.drawCentredString(x + 87, 165, "WORKER")
        c.setFillColor(INK)
        c.setFont(FONT_BOLD, 12.2)
        c.drawCentredString(x + 87, 143, name)
        c.setFillColor(MUTED)
        c.setFont(FONT, 8.4)
        for offset, fragment in enumerate(body.split("\n")):
            c.drawCentredString(x + 87, 124 - offset * 12, fragment)
    rounded(c, 48, 60, 746, 28, fill=WARM_WHITE, stroke=LINE, radius=6, line_width=0.8)
    paragraph(c, "共享语义层：TaskContext | Agent Identity | Skill Schema | Tool Contract | Trace | CaseDigest", 68, 80, 706, text_style("agent-strip", 10.6, 14, INK, TA_CENTER, FONT_BOLD))


def architecture(c: canvas.Canvas) -> None:
    light_header(c, 5, "04 / Layered Architecture", "一张图看清渠道、能力与证据的边界。", "设计将渠道适配与任务语义解耦，避免更换渠道时重写风险、审批和验证逻辑。")
    sections = (
        ("01", "渠道", "抖音 Runtime 已接入", MINT),
        ("02", "编排", "Manager / TL / Worker", CYAN),
        ("03", "能力", "Skill + MCP 等价契约", GOLD),
        ("04", "证据", "Trace + verify + CaseDigest", CORAL),
    )
    for index, (number, label, note, color) in enumerate(sections):
        y = 306 - index * 55
        c.setFillColor(color)
        c.setFont(MONO, 9)
        c.drawString(48, y + 16, number)
        c.setFillColor(INK)
        c.setFont(FONT_BOLD, 12)
        c.drawString(84, y + 15, label)
        c.setFillColor(MUTED)
        c.setFont(FONT, 8.8)
        c.drawString(84, y + 1, note)
        line(c, 48, y - 11, 222, y - 11, LINE, 0.7)
    if ARCH.exists():
        rounded(c, 248, 79, 546, 304, fill=WARM_WHITE, stroke=LINE, radius=12, line_width=0.9)
        c.setFillColor(INK_SOFT)
        c.roundRect(256, 353, 530, 22, 5, fill=1, stroke=0)
        c.setFillColor(colors.HexColor("#C6D1D1"))
        c.setFont(MONO, 7.3)
        c.drawString(271, 360, "AGENTDESK / LAYER BOUNDARY / VERIFIABLE VS. ROADMAP")
        image_contain(c, ARCH, 260, 88, 522, 258)
    else:
        rounded(c, 248, 79, 546, 304, fill=CORAL_LIGHT, stroke=CORAL, radius=12, line_width=0.9)
        paragraph(c, "架构图缺失：请先运行 python docs/build_arch_diagram.py", 280, 240, 482, CARD_TITLE)
    paragraph(c, "图中“初赛已接入 / 参考实现 / 离线契约 / 复赛扩展”标签直接对应仓库中的可运行与规划边界。", 48, 72, 746, BODY_SMALL)


def lifecycle(c: canvas.Canvas) -> None:
    dark_header(c, 6, "05 / Controlled Lifecycle", "会话进入的不是聊天窗口，而是一台状态机。", "低风险任务直达执行；高风险任务只在持有 ApprovalToken 后才允许继续。")
    states = (
        ("01", "接入/去重", MINT),
        ("02", "分级", CYAN),
        ("03", "规划", GOLD),
        ("04", "执行", MINT),
        ("05", "核验", CYAN),
        ("06", "确认", GOLD),
        ("07", "归档/完成", CORAL),
    )
    x0, y, w, gap = 48, 277, 94, 12
    for index, (num, label, color) in enumerate(states):
        x = x0 + index * (w + gap)
        rounded(c, x, y, w, 58, fill=INK_SOFT, stroke=color, radius=9, line_width=1.2)
        c.setFillColor(color)
        c.setFont(MONO, 8)
        c.drawCentredString(x + w / 2, y + 39, num)
        c.setFillColor(WARM_WHITE)
        c.setFont(FONT_BOLD, 9.9)
        c.drawCentredString(x + w / 2, y + 18, label)
        if index < len(states) - 1:
            arrow(c, x + w + 4, y + 29, x + w + gap - 4, y + 29, colors.HexColor("#7B8A8D"), 1.15)
    # Approval gate is drawn as a separate branch, so the exception reads as a policy decision.
    approval_x = x0 + 2 * (w + gap)
    rounded(c, approval_x, 176, 97, 52, fill=CORAL, radius=8)
    c.setFillColor(INK)
    c.setFont(MONO, 7.8)
    c.drawCentredString(approval_x + 48.5, 208, "HIGH RISK")
    c.setFont(FONT_BOLD, 10.2)
    c.drawCentredString(approval_x + 48.5, 189, "人工审批")
    arrow(c, approval_x + 48.5, y, approval_x + 48.5, 228, CORAL, 1.5)
    c.setFillColor(colors.HexColor("#B8C8C7"))
    c.setFont(FONT, 8.8)
    c.drawCentredString(approval_x + 48.5, 157, "批准: 恢复执行  |  拒绝: failed")

    dedupe_x = x0
    rounded(c, dedupe_x, 176, 112, 52, fill=CYAN, radius=8)
    c.setFillColor(INK)
    c.setFont(MONO, 7.8)
    c.drawCentredString(dedupe_x + 56, 208, "CROSS-CHANNEL")
    c.setFont(FONT_BOLD, 9.5)
    c.drawCentredString(dedupe_x + 56, 189, "重复任务 → deduplicated")
    arrow(c, dedupe_x + 47, y, dedupe_x + 47, 228, CYAN, 1.5)

    rounded(c, 48, 86, 746, 47, fill=INK_SOFT, stroke=INK_LINE, radius=8, line_width=0.8)
    paragraph(c, "TaskContext / task_id | channel | canonical_customer | dedupe | intent | risk | receipt | verify | confirm | case_digest", 66, 115, 710, text_style("state-context", 8.5, 13, colors.HexColor("#D0DEDC"), TA_CENTER, MONO))
    c.setFillColor(MINT)
    c.setFont(MONO, 8.4)
    c.drawCentredString(PAGE_W / 2, 65, "TRACE JSONL: task -> agent -> skill -> tool -> status -> input_hash -> case://")


def two_paths(c: canvas.Canvas) -> None:
    light_header(c, 7, "06 / Reproducible Scenarios", "三条剧本，证明一条可控闭环。", "它们共享同一条编排语义，只在风险级别、渠道去重、客户确认和结束状态上产生分支。")
    c.setFillColor(MINT)
    c.setFont(MONO, 9)
    c.drawString(48, 375, "A / LOW RISK / PRICE CONSULT")
    c.setFillColor(INK)
    c.setFont(FONT_BOLD, 14)
    c.drawString(48, 351, "咨询价格: 自动回复一次并进入 done")
    c.setFillColor(CORAL)
    c.setFont(MONO, 9)
    c.drawString(440, 375, "B / HIGH RISK / APPROVAL GATE")
    c.setFillColor(INK)
    c.setFont(FONT_BOLD, 14)
    c.drawString(440, 351, "退款 / 改账户: 获批后发送通知，拒绝后不发送")
    if VERIFY_TRACE.exists():
        screen(c, VERIFY_TRACE, 48, 126, 342, 211, "TRACE / Script A / OutcomeVerify", MINT)
    if APPROVAL_TRACE.exists():
        screen(c, APPROVAL_TRACE, 452, 126, 342, 211, "TRACE / Script B / Approval", CORAL)
    rounded(c, 48, 56, 746, 52, fill=INK_SOFT, radius=8)
    c.setFillColor(CYAN)
    c.setFont(MONO, 8.6)
    c.drawString(67, 87, "C / CROSS-CHANNEL / OFFLINE CONTRACT")
    paragraph(c, "抖音完成并匿名归档 → 同内容企微任务 <font color='#3F9EF4'>deduplicated</font> → 后续咨询命中 <font color='#35C9AD'>case://</font> 后再次确认与沉淀。企微仅为统一 SessionEvent 离线契约，不声明真实接入。", 67, 77, 708, text_style("path-c", 9.7, 13.5, colors.HexColor("#D1DDDC"), TA_CENTER, FONT_BOLD))


def skills(c: canvas.Canvas) -> None:
    dark_header(c, 8, "07 / Reusable Skills", "Skill 是可审查的能力，不是一次性提示词。", "每个 Skill 都有输入输出 Schema、调用条件、依赖工具、失败路径和安全边界。")
    c.setFillColor(colors.HexColor("#26343B"))
    c.setFont(FONT_BOLD, 72)
    c.drawString(48, 277, "7")
    c.setFillColor(WARM_WHITE)
    c.setFont(FONT_BOLD, 20)
    c.drawString(121, 303, "可复用 Skill")
    c.setFillColor(colors.HexColor("#AABABA"))
    c.setFont(FONT, 9.4)
    c.drawString(121, 282, "从入站会话到确认与案例沉淀")

    specs = (
        ("01", "SessionNormalize", "会话归一 / 跨渠道去重键", MINT),
        ("02", "IntentTriage", "意图识别 / 风险分级", CYAN),
        ("03", "ReplyPlan", "知识注入 / 处置草案", GOLD),
        ("04", "ChannelSend", "幂等发送 / 防串号", CORAL),
        ("05", "OutcomeVerify", "回执与内容核验", MINT),
        ("06", "CustomerConfirm", "确认 / 待跟进 / 升级", CYAN),
        ("07", "CaseDigest", "匿名归档 / 标签复用", GOLD),
    )
    for index, (num, name, note, color) in enumerate(specs):
        y = 350 - index * 40
        rounded(c, 248, y - 18, 546, 31, fill=INK_SOFT, stroke=INK_LINE, radius=6, line_width=0.8)
        c.setFillColor(color)
        c.rect(248, y - 18, 7, 31, fill=1, stroke=0)
        c.setFillColor(color)
        c.setFont(MONO, 8.8)
        c.drawString(270, y - 1, num)
        c.setFillColor(WARM_WHITE)
        c.setFont(FONT_BOLD, 11.2)
        c.drawString(309, y - 1, name)
        c.setFillColor(colors.HexColor("#B6C5C4"))
        c.setFont(FONT, 8.6)
        c.drawRightString(772, y, note)
    rounded(c, 48, 55, 746, 32, fill=colors.HexColor("#172128"), stroke=INK_LINE, radius=6, line_width=0.8)
    paragraph(c, "统一能力契约: Schema | AuthZ | Idempotency | Error Code | Audit | Fallback | Privacy", 67, 78, 708, text_style("skills-contract", 9.8, 13, colors.HexColor("#D1DDDC"), TA_CENTER, MONO))


def web_login(c: canvas.Canvas) -> None:
    light_header(c, 9, "08 / Web Login Control", "Web 端负责发起，凭据仍停留在 Runtime。", "托管账号登录可迁移至 Web 控制面，但扫码后的 Cookie / Token 不应进入 Web 或跨账号流转。")
    if LOGIN_SCREEN.exists():
        screen(c, LOGIN_SCREEN, 48, 162, 479, 186, "LOCAL WEB CONSOLE / SANITIZED DEMO", CYAN)
    else:
        rounded(c, 48, 162, 479, 186, fill=CYAN_LIGHT, stroke=CYAN, radius=10, line_width=0.9)
    c.setFillColor(INK)
    c.setFont(MONO, 8.8)
    c.drawString(562, 340, "ACCOUNT-SCOPED FLOW")
    stages = (
        ("01", "Create Job", "Web Console 选择账号并创建登录任务", CYAN),
        ("02", "Poll State", "Login Service 返回任务状态和最小展示信息", MINT),
        ("03", "Keep Secret", "本机 Runtime 启动浏览器并持有 profile", CORAL),
    )
    for index, (num, heading, body, color) in enumerate(stages):
        y = 292 - index * 58
        c.setFillColor(color)
        c.setFont(MONO, 9)
        c.drawString(562, y, num)
        c.setFillColor(INK)
        c.setFont(FONT_BOLD, 12.4)
        c.drawString(603, y - 0.5, heading)
        paragraph(c, body, 603, y - 16, 175, BODY_SMALL)
        if index < 2:
            line(c, 566, y - 37, 566, y - 51, LINE, 1)
    rounded(c, 48, 80, 746, 54, fill=INK, radius=8)
    paragraph(c, "安全边界: Web 只创建和监控 Login Job; 浏览器交互、凭据写入与 profile 隔离仅在本机 Runtime 内完成。", 70, 113, 702, text_style("login-boundary", 11.4, 16, WARM_WHITE, TA_CENTER, FONT_BOLD))


def security_evidence(c: canvas.Canvas) -> None:
    dark_header(c, 10, "09 / Safety and Evidence", "自动化能做多少，取决于它有多少道闸门。", "身份隔离、审批令牌和结果核验共同定义哪些动作可执行、何时停止、如何留下证据。")
    pillars = (
        ("01", "身份隔离", "profile_id 贯穿任务、会话和工具调用。\nclient_msg_id 是幂等键，避免重复发送。", MINT),
        ("02", "审批令牌", "高风险任务只生成方案。\nApprovalToken 与 profile scope 绑定。", CORAL),
        ("03", "核验与沉淀", "回执、内容与客户确认。\nTrace 和匿名 CaseDigest 留证。", CYAN),
    )
    for index, (num, heading, body, color) in enumerate(pillars):
        x = 48 + index * 150
        c.setFillColor(colors.HexColor("#26343B"))
        c.setFont(FONT_BOLD, 44)
        c.drawString(x, 300, num)
        c.setFillColor(color)
        c.setFont(FONT_BOLD, 14)
        c.drawString(x, 269, heading)
        paragraph(c, body.replace("\n", "<br/>"), x, 247, 134, BODY_SMALL_DARK)
    if MULTICHANNEL_TRACE.exists():
        screen(c, MULTICHANNEL_TRACE, 508, 137, 286, 195, "MULTI-CHANNEL TRACE / SANITIZED", CYAN)
    rounded(c, 48, 76, 746, 38, fill=CORAL, radius=7)
    paragraph(c, "拒绝、异常或核验失败都不伪造“已完成”: 状态进入 failed / suspended / escalated，并保留人工处置入口。", 66, 100, 710, text_style("safety-quote", 10.8, 15, INK, TA_CENTER, FONT_BOLD))


def verification(c: canvas.Canvas) -> None:
    light_header(c, 11, "10 / Local Verification", "评审要看的，不是演示话术。", "可复现命令、剧本 Trace、Skill CLI 输出和契约测试共同构成初赛阶段的工程证据。")
    c.setFillColor(CORAL)
    c.setFont(FONT_BOLD, 78)
    c.drawString(48, 252, "56")
    c.setFillColor(INK)
    c.setFont(FONT_BOLD, 20)
    c.drawString(48, 213, "tests passed")
    c.setFillColor(MUTED)
    c.setFont(FONT, 10)
    c.drawString(50, 191, "2026-08-02 local run")

    rounded(c, 295, 149, 499, 220, fill=INK, radius=10)
    c.setFillColor(MINT)
    c.setFont(MONO, 8.5)
    c.drawString(316, 343, "TERMINAL / REPRODUCE")
    commands = (
        "python -m pytest -q",
        "python -m orchestrator.demo.script_a_consult",
        "python -m orchestrator.demo.script_b_approval",
        "python -m orchestrator.demo.script_b_approval --reject",
        "python -m orchestrator.demo.script_c_multichannel_case",
        "python skills/run_skill.py intent_triage -i ... --pretty",
    )
    for index, command in enumerate(commands):
        y = 314 - index * 25
        c.setFillColor(colors.HexColor("#5E7271"))
        c.setFont(MONO, 8.7)
        c.drawString(316, y, ">")
        c.setFillColor(WARM_WHITE)
        c.drawString(333, y, command)
    c.setFillColor(MINT)
    c.setFont(MONO, 8.4)
    c.drawString(316, 164, "PASSED / TRACE WRITTEN / CONTRACT ASSERTED")

    small_label(c, 48, 131, "A", "自动闭环", "发送、核验、客户确认与匿名归档后进入 done。", MINT)
    small_label(c, 286, 131, "B", "审批分支", "批准后执行并核验; 拒绝路径不发送消息。", CORAL)
    small_label(c, 524, 131, "C", "去重与复用", "跨渠道重复拦截、case:// 命中和再次沉淀。", CYAN)
    paragraph(c, "验证产物: 56 pytest 断言 | Script A/B/C Trace | Skill CLI 输出 | Schema / 工具契约测试", 48, 73, 746, text_style("verification-evidence", 10.8, 14.5, INK, TA_CENTER, FONT_BOLD))


def open_plan(c: canvas.Canvas) -> None:
    dark_header(c, 12, "11 / Open Infrastructure", "让 Agent 团队能够证明自己完成了工作。", "AgentDesk 以真实客服闭环为落点，以可演进的多 Agent 基础设施为交付。")
    c.setFillColor(colors.HexColor("#223039"))
    c.setFont(FONT_BOLD, 110)
    c.drawRightString(PAGE_W - 42, 285, ">")
    blocks = (
        (48, MINT, "Identity", "角色、输入输出、风险边界、升级规则"),
        (301, GOLD, "Skill", "Schema、版本、失败处理、复用能力"),
        (554, CYAN, "Contract", "鉴权、幂等、错误码、审计与迁移"),
    )
    for x, color, heading, body in blocks:
        rounded(c, x, 151, 220, 113, fill=INK_SOFT, stroke=INK_LINE, radius=10, line_width=0.9)
        c.setFillColor(color)
        c.rect(x, 245, 220, 5, fill=1, stroke=0)
        c.setFillColor(color)
        c.setFont(MONO, 8.4)
        c.drawString(x + 18, 224, "REUSABLE BUILDING BLOCK")
        c.setFillColor(WARM_WHITE)
        c.setFont(FONT_BOLD, 18)
        c.drawString(x + 18, 195, heading)
        paragraph(c, body, x + 18, 174, 184, BODY_SMALL_DARK)
    c.setFillColor(colors.HexColor("#AFC1C0"))
    c.setFont(MONO, 8.6)
    c.drawString(48, 101, "PRELIMINARY: DESIGN + VERIFIABLE PATH")
    c.setFillColor(MINT)
    c.drawString(48, 82, "NEXT: REAL MULTI-CHANNEL ADAPTER + OFFICIAL AGENTTEAMS + RAG / TRACE UI")
    c.setFillColor(colors.HexColor("#C7D5D3"))
    c.setFont(MONO, 9.2)
    c.drawCentredString(PAGE_W / 2, 57, "https://github.com/icebreak119/AgentDesk")


SLIDES = (
    cover,
    scene_problem,
    phase_boundary,
    agent_team,
    architecture,
    lifecycle,
    two_paths,
    skills,
    web_login,
    security_evidence,
    verification,
    open_plan,
)


def build() -> None:
    if not HERO.exists():
        from build_ppt_assets import build as build_assets

        build_assets()
    document = canvas.Canvas(str(OUT), pagesize=landscape(A4), pageCompression=1)
    document.setTitle("AgentDesk 初赛方案")
    document.setAuthor("AgentDesk Team")
    document.setSubject("Agent Infra 智能客服自主闭环")
    for slide in SLIDES:
        slide(document)
        document.showPage()
    document.save()
    print(f"Wrote {OUT}")


if __name__ == "__main__":
    build()
