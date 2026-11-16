"""Banner artwork for the card modules.

Each card leads with a wide banner carrying its title, the server tagline and
the server mark. It is drawn locally with Pillow and sent as an attachment, so
no external image host, URL allowlist or upload is involved.

The artwork is generated from the brand colour: a vertical gradient, a motif
that matches the card type, a faint oversized glyph of the module's own pack
icon as a watermark, and the server mark with spaced small caps. Ink colour is
chosen from the background luminance, so a light brand colour gets dark text
instead of white-on-yellow glare. An owner can replace any banner with an
uploaded image from the dashboard; that image is cover-cropped and gets a
scrim on the text side so the copy stays readable.
"""

from __future__ import annotations

import io
import random
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from utils.modules import text as text_utils

PROJECT_ROOT = Path(__file__).resolve().parents[2]
FONT_DIR = PROJECT_ROOT / "assets" / "fonts"
EMOJI_DIR = PROJECT_ROOT / "emojis" / "white"
BOLD_FONT = FONT_DIR / "DejaVuSans-Bold.ttf"
REGULAR_FONT = FONT_DIR / "DejaVuSans.ttf"

WIDTH = 1600
HEIGHT = 470
PADDING = 64
MARK_SIZE = 104
TEXT_LEFT = PADDING + MARK_SIZE + 30
MAX_TITLE_SIZE = 104
MIN_TITLE_SIZE = 44

# Which pack glyph is used as the watermark (and in the card's own header).
KIND_ART = {
    "infraction": "gavel",
    "promotion": "trending_up",
    "feedback": "reviews",
    "session": "sports_esports",
    "welcome": "waving_hand",
}

INK_DARK = (23, 23, 27)
INK_LIGHT = (255, 255, 255)


def hex_to_rgb(value: str) -> tuple[int, int, int]:
    raw = str(value or "#FCC200").lstrip("#")
    try:
        return tuple(int(raw[index:index + 2], 16) for index in (0, 2, 4))  # type: ignore[return-value]
    except (TypeError, ValueError):
        return (252, 194, 0)


def _mix(color: tuple[int, int, int], other: tuple[int, int, int], ratio: float) -> tuple[int, int, int]:
    return (
        round(color[0] + (other[0] - color[0]) * ratio),
        round(color[1] + (other[1] - color[1]) * ratio),
        round(color[2] + (other[2] - color[2]) * ratio),
    )


def _luminance(color: tuple[int, int, int]) -> float:
    red, green, blue = (channel / 255 for channel in color)
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def _font(path: Path, size: int):
    try:
        return ImageFont.truetype(str(path), size)
    except OSError:  # pragma: no cover - only when the vendored font is missing
        return ImageFont.load_default()


@lru_cache(maxsize=8)
def _watermark(kind: str) -> Image.Image | None:
    """The card type's own pack glyph, loaded once."""

    name = KIND_ART.get(kind)
    if not name:
        return None
    for path in sorted(EMOJI_DIR.glob(f"{name}-*.png")):
        try:
            return Image.open(path).convert("RGBA").copy()
        except OSError:  # pragma: no cover - corrupt asset
            return None
    return None


def _tracked(draw: ImageDraw.ImageDraw, xy: tuple[int, int], value: str, font, fill, spacing: int) -> int:
    """Draw text with letter spacing (Pillow has none) and return its width."""

    x, y = xy
    for char in value:
        draw.text((x, y), char, font=font, fill=fill)
        x += draw.textlength(char, font=font) + spacing
    return int(x - xy[0])


def _fit_title(draw: ImageDraw.ImageDraw, title: str, max_width: int):
    size = MAX_TITLE_SIZE
    while size > MIN_TITLE_SIZE:
        font = _font(BOLD_FONT, size)
        if draw.textlength(title, font=font) <= max_width:
            return font, size
        size -= 4
    return _font(BOLD_FONT, MIN_TITLE_SIZE), MIN_TITLE_SIZE


# --------------------------------------------------------------------------- motifs
def _motif_hazard(draw: ImageDraw.ImageDraw, ink: tuple[int, int, int], alpha: int) -> None:
    for offset in range(-HEIGHT, WIDTH + HEIGHT, 108):
        draw.polygon(
            [(offset, HEIGHT), (offset + 30, HEIGHT), (offset + 30 + HEIGHT, 0), (offset + HEIGHT, 0)],
            fill=(*ink, alpha),
        )


def _motif_chevrons(draw: ImageDraw.ImageDraw, ink: tuple[int, int, int], alpha: int) -> None:
    """Repeating upward chevrons across the lower half of the banner."""

    width, rise = 44, 30
    for row, baseline in enumerate((HEIGHT - 96, HEIGHT - 150, HEIGHT - 204)):
        points = [(x, baseline - (rise if (x // width) % 2 else 0)) for x in range(0, WIDTH + width, width)]
        draw.line(points, fill=(*ink, max(0, alpha - row * 3)), width=7, joint="curve")


def _motif_sparkle(draw: ImageDraw.ImageDraw, ink: tuple[int, int, int], alpha: int) -> None:
    rng = random.Random(7)
    for _ in range(26):
        x = rng.randint(40, WIDTH - 40)
        y = rng.randint(30, HEIGHT - 30)
        size = rng.choice((9, 13, 18, 24))
        draw.polygon(
            [(x, y - size), (x + size * 0.28, y - size * 0.28), (x + size, y),
             (x + size * 0.28, y + size * 0.28), (x, y + size),
             (x - size * 0.28, y + size * 0.28), (x - size, y),
             (x - size * 0.28, y - size * 0.28)],
            fill=(*ink, alpha),
        )


def _motif_wave(draw: ImageDraw.ImageDraw, ink: tuple[int, int, int], alpha: int) -> None:
    rng = random.Random(11)
    x = 0
    while x < WIDTH:
        width = rng.randint(8, 16)
        height = rng.randint(28, 150)
        draw.rounded_rectangle(
            [(x, HEIGHT - 30 - height), (x + width, HEIGHT - 30)],
            radius=width // 2,
            fill=(*ink, alpha),
        )
        x += width + rng.randint(10, 26)


def _motif_confetti(draw: ImageDraw.ImageDraw, ink: tuple[int, int, int], alpha: int) -> None:
    rng = random.Random(3)
    for _ in range(46):
        x = rng.randint(10, WIDTH - 10)
        y = rng.randint(10, HEIGHT - 10)
        if rng.random() < 0.5:
            radius = rng.randint(3, 8)
            draw.ellipse([(x - radius, y - radius), (x + radius, y + radius)], fill=(*ink, alpha))
        else:
            side = rng.randint(8, 16)
            draw.rounded_rectangle([(x, y), (x + side, y + side)], radius=3, fill=(*ink, alpha))


MOTIFS = {
    "infraction": _motif_hazard,
    "promotion": _motif_chevrons,
    "feedback": _motif_sparkle,
    "session": _motif_wave,
    "welcome": _motif_confetti,
}


# --------------------------------------------------------------------------- render
def _generated(kind: str, base: tuple[int, int, int]) -> tuple[Image.Image, tuple[int, int, int]]:
    """The brand-coloured background, its motif and the ink to write with."""

    ink = INK_DARK if _luminance(base) > 0.62 else INK_LIGHT
    top = _mix(base, (255, 255, 255), 0.16)
    bottom = _mix(base, (0, 0, 0), 0.22)

    canvas = Image.new("RGB", (WIDTH, HEIGHT), base)
    draw = ImageDraw.Draw(canvas)
    for y in range(HEIGHT):
        draw.line([(0, y), (WIDTH, y)], fill=_mix(top, bottom, y / max(HEIGHT - 1, 1)))

    canvas = canvas.convert("RGBA")

    # A soft glow around the watermark area keeps the right side from looking flat.
    glow = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    glow_draw = ImageDraw.Draw(glow)
    center = (WIDTH - 250, HEIGHT // 2)
    for radius, alpha in ((430, 26), (320, 30), (210, 34)):
        glow_draw.ellipse(
            [(center[0] - radius, center[1] - radius), (center[0] + radius, center[1] + radius)],
            fill=(255, 255, 255, alpha),
        )
    canvas = Image.alpha_composite(canvas, glow.filter(ImageFilter.GaussianBlur(38)))

    draw = ImageDraw.Draw(canvas)
    motif = MOTIFS.get(kind)
    if motif is not None:
        layer = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
        motif(ImageDraw.Draw(layer), ink, 150 if ink == INK_LIGHT else 26)
        canvas = Image.alpha_composite(canvas, layer)
        draw = ImageDraw.Draw(canvas)

    mark = _watermark(kind)
    if mark is not None:
        size = 330
        glyph = mark.resize((size, size), Image.LANCZOS)
        if ink == INK_DARK:
            glyph = _recolor(glyph, INK_DARK)
        glyph.putalpha(glyph.getchannel("A").point(lambda value: int(value * 0.30)))
        canvas.alpha_composite(glyph, (WIDTH - PADDING - size + 20, (HEIGHT - size) // 2))
        draw = ImageDraw.Draw(canvas)
        draw.ellipse(
            [(center[0] - 196, center[1] - 196), (center[0] + 196, center[1] + 196)],
            outline=(*ink, 46),
            width=3,
        )

    return canvas, ink


def _recolor(image: Image.Image, color: tuple[int, int, int]) -> Image.Image:
    """Replace every pixel's colour, keeping the glyph's alpha."""

    solid = Image.new("RGBA", image.size, (*color, 255))
    solid.putalpha(image.getchannel("A"))
    return solid


def _from_upload(image: Image.Image) -> Image.Image:
    """Cover-crop an uploaded banner and darken the text side for contrast."""

    source = image.convert("RGBA")
    scale = max(WIDTH / source.width, HEIGHT / source.height)
    resized = source.resize((max(WIDTH, round(source.width * scale)), max(HEIGHT, round(source.height * scale))), Image.LANCZOS)
    left = (resized.width - WIDTH) // 2
    top = (resized.height - HEIGHT) // 2
    canvas = resized.crop((left, top, left + WIDTH, top + HEIGHT))

    scrim = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    scrim_draw = ImageDraw.Draw(scrim)
    for x in range(WIDTH):
        ratio = min(1.0, max(0.0, (x - WIDTH * 0.28) / (WIDTH * 0.72)))
        scrim_draw.line([(x, 0), (x, HEIGHT)], fill=(8, 8, 12, round(214 * (1 - ratio) ** 1.35 + 26)))
    return Image.alpha_composite(canvas, scrim)


@lru_cache(maxsize=48)
def render(
    title: str,
    tagline: str,
    label: str,
    color: str,
    kind: str = "",
    image: bytes | None = None,
) -> bytes:
    """Draw one banner and return the PNG bytes (cached by its content)."""

    base = hex_to_rgb(color)
    if image:
        try:
            canvas = _from_upload(Image.open(io.BytesIO(image)))
        except (OSError, ValueError):  # a corrupt upload falls back to the artwork
            canvas, ink = _generated(kind, base)
        else:
            ink = INK_LIGHT
    else:
        canvas, ink = _generated(kind, base)

    draw = ImageDraw.Draw(canvas)
    soft = (*ink, 210)

    # Server mark.
    mark_box = (PADDING, PADDING - 2, PADDING + MARK_SIZE, PADDING - 2 + MARK_SIZE)
    if ink == INK_DARK:
        mark_fill = (*INK_DARK, 235)
        initials_fill = (255, 255, 255, 245)
    else:
        mark_fill = (255, 255, 255, 238)
        initials_fill = _mix(base, (0, 0, 0), 0.2) + (255,)
    draw.rounded_rectangle(mark_box, radius=26, fill=mark_fill)
    initials = text_utils.initials(label)
    initials_font = _font(BOLD_FONT, 46)
    initials_width = draw.textlength(initials, font=initials_font)
    draw.text(
        (PADDING + (MARK_SIZE - initials_width) / 2, PADDING - 2 + (MARK_SIZE - 50) / 2),
        initials,
        font=initials_font,
        fill=initials_fill,
    )

    label_text = (text_utils.clean(label, 42) or "Discord").upper()
    _tracked(draw, (TEXT_LEFT, PADDING + 6), label_text, _font(BOLD_FONT, 25), soft, 4)

    title_font, title_size = _fit_title(draw, title, WIDTH - TEXT_LEFT - 300)
    draw.text((TEXT_LEFT, PADDING + 40), title, font=title_font, fill=(*ink, 255))

    tagline_text = text_utils.clean(tagline, 60)
    if tagline_text:
        draw.text(
            (TEXT_LEFT, PADDING + 52 + title_size + 14),
            tagline_text,
            font=_font(REGULAR_FONT, 34),
            fill=(*ink, 222),
        )

    # Bottom accent: a hairline plus a short branded bar.
    draw.line([(PADDING, HEIGHT - 46), (WIDTH - PADDING, HEIGHT - 46)], fill=(*ink, 40), width=2)
    draw.rounded_rectangle(
        [(PADDING, HEIGHT - 50), (PADDING + 132, HEIGHT - 42)],
        radius=4,
        fill=(*ink, 190),
    )

    buffer = io.BytesIO()
    canvas.convert("RGB").save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()
