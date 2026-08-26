"""Generate the Windows icon from the dashboard's existing CSS brand mark."""

from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image, ImageDraw


SIZES = (16, 20, 24, 32, 48, 64, 128, 256)
NAVY = (12, 21, 39, 255)
ACCENT = (224, 87, 79, 255)
WHITE = (255, 255, 255, 255)


def render(size: int) -> Image.Image:
    scale = size / 256
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    margin = round(18 * scale)
    radius = round(58 * scale)
    draw.rounded_rectangle(
        (margin, margin, size - margin - 1, size - margin - 1),
        radius=radius,
        fill=NAVY,
    )
    draw.ellipse(
        (round(161 * scale), round(157 * scale), round(264 * scale), round(260 * scale)),
        fill=ACCENT,
    )
    bar_width = max(2, round(29 * scale))
    gap = round(17 * scale)
    left = round(58 * scale)
    bottom = round(190 * scale)
    heights = (62, 112, 82)
    bar_radius = max(1, round(13 * scale))
    for index, height in enumerate(heights):
        x0 = left + index * (bar_width + gap)
        y0 = bottom - round(height * scale)
        draw.rounded_rectangle(
            (x0, y0, x0 + bar_width, bottom),
            radius=bar_radius,
            fill=WHITE,
        )
    return image


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    largest = render(256)
    largest.save(
        args.output,
        format="ICO",
        sizes=[(size, size) for size in SIZES],
        append_images=[render(size) for size in SIZES[:-1]],
        bitmap_format="png",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
