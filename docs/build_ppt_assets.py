"""Create original visual assets used by the AgentDesk proposal deck.

The assets are intentionally abstract system illustrations rather than stock
photography.  They make the technical narrative more memorable while keeping
the submission self-contained and free of third-party image licensing risk.
"""

from __future__ import annotations

import random
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parent
OUT_DIR = ROOT / "assets"
HERO = OUT_DIR / "agentdesk_control_plane.png"

W, H = 1800, 1040
INK = "#10141A"
PAPER = "#F4F1EA"
MINT = "#4AD6BF"
CYAN = "#55B6FF"
CORAL = "#FF6858"
GOLD = "#FFC44E"


def rounded(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], *, fill: str, outline: str | None = None, radius: int = 22, width: int = 2) -> None:
    draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=width)


def glow_line(image: Image.Image, points: list[tuple[int, int]], color: str, width: int = 5) -> None:
    """Paint a wire with a deliberately restrained halo."""
    halo = Image.new("RGBA", image.size, (0, 0, 0, 0))
    halo_draw = ImageDraw.Draw(halo)
    halo_draw.line(points, fill=color, width=width * 7, joint="curve")
    halo = halo.filter(ImageFilter.GaussianBlur(width * 3))
    image.alpha_composite(halo)
    crisp = ImageDraw.Draw(image)
    crisp.line(points, fill=color, width=width, joint="curve")


def build() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    random.seed(26)
    image = Image.new("RGBA", (W, H), INK)
    draw = ImageDraw.Draw(image)

    # Fine technical grid, held below the content so it reads as texture.
    for x in range(70, W, 70):
        draw.line((x, 0, x, H), fill="#1C242C", width=1)
    for y in range(40, H, 70):
        draw.line((0, y, W, y), fill="#1C242C", width=1)

    # Frame and system rails.
    rounded(draw, (98, 96, 1704, 940), fill="#151C23", outline="#2D3B45", radius=46, width=3)
    draw.line((180, 195, 1620, 195), fill="#30404A", width=3)
    for x in (180, 480, 875, 1285, 1620):
        draw.ellipse((x - 8, 187, x + 8, 203), fill=PAPER)

    # Left: inbound conversation, center: planner, right: verified evidence.
    rounded(draw, (175, 320, 500, 716), fill="#1E2931", outline="#38505B", radius=34, width=3)
    rounded(draw, (720, 260, 1090, 780), fill="#202D37", outline="#4C6D7A", radius=42, width=4)
    rounded(draw, (1300, 320, 1625, 716), fill="#1E2931", outline="#38505B", radius=34, width=3)

    # Inbound message fragments.
    for index, (y, width, color) in enumerate(((384, 206, CYAN), (473, 155, MINT), (562, 222, CORAL), (651, 128, GOLD))):
        rounded(draw, (220, y, 220 + width, y + 44), fill=color, radius=14)
        draw.rectangle((238, y + 15, 238 + max(38, width - 70), y + 20), fill=INK)
        if index % 2:
            draw.rectangle((238, y + 29, 238 + max(24, width - 100), y + 33), fill="#324149")

    # Planner hierarchy. No wording inside the illustration; the PDF owns labels.
    rounded(draw, (775, 324, 1035, 414), fill=PAPER, radius=20)
    draw.rectangle((817, 352, 993, 363), fill=INK)
    draw.rectangle((817, 377, 944, 387), fill="#6F7C80")
    for x, color in ((765, CYAN), (880, MINT), (995, CORAL)):
        rounded(draw, (x, 510, x + 105, 614), fill=color, radius=22)
        draw.ellipse((x + 37, 536, x + 68, 567), outline=INK, width=5)
        draw.line((x + 30, 583, x + 75, 583), fill=INK, width=5)
    rounded(draw, (775, 676, 1035, 716), fill=GOLD, radius=17)

    # Evidence receipt and the three lines of audit proof.
    rounded(draw, (1350, 372, 1575, 652), fill=PAPER, radius=22)
    draw.polygon(((1492, 372), (1575, 372), (1575, 455)), fill=GOLD)
    for y, width, color in ((451, 145, INK), (497, 117, "#59656B"), (547, 156, "#59656B"), (597, 92, MINT)):
        draw.rounded_rectangle((1389, y, 1389 + width, y + 12), radius=6, fill=color)

    # Wires show messages becoming controlled tasks then verifiable evidence.
    glow_line(image, [(500, 406), (610, 406), (610, 355), (775, 355)], CYAN)
    glow_line(image, [(500, 495), (640, 495), (640, 562), (765, 562)], MINT)
    glow_line(image, [(500, 584), (665, 584), (665, 562), (995, 562)], CORAL)
    glow_line(image, [(1035, 375), (1195, 375), (1195, 436), (1350, 436)], GOLD)
    glow_line(image, [(1035, 695), (1210, 695), (1210, 624), (1350, 624)], MINT)

    # Bright control points make the flow readable at small slide scale.
    for x, y, color in ((610, 406, CYAN), (640, 495, MINT), (665, 584, CORAL), (1195, 375, GOLD), (1210, 695, MINT)):
        draw.ellipse((x - 14, y - 14, x + 14, y + 14), fill=color)
        draw.ellipse((x - 5, y - 5, x + 5, y + 5), fill=INK)

    # A small deterministic time ruler adds a crafted editorial detail.
    for index in range(13):
        x = 190 + index * 111
        height = 10 + (index % 4) * 6
        draw.rectangle((x, 850 - height, x + 6, 850), fill="#50626B")
    draw.rectangle((190, 858, 1570, 862), fill="#45565E")

    image.convert("RGB").save(HERO, quality=95)
    print(f"Wrote {HERO}")


if __name__ == "__main__":
    build()
