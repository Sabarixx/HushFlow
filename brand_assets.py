"""
brand_assets.py - 4x Supersampled Vector-Crisp HushFlow Brand Identity Generator
Produces anti-aliased multi-resolution Windows .ico, PNG logos, and high-DPI tray icons.
"""

import os
from typing import Tuple
from PIL import Image, ImageDraw

from app_paths import get_resource_path, SOURCE_DIR


def _lerp_color(c1: Tuple[int, int, int], c2: Tuple[int, int, int], t: float) -> Tuple[int, int, int]:
    return (
        int(c1[0] + (c2[0] - c1[0]) * t),
        int(c1[1] + (c2[1] - c1[1]) * t),
        int(c1[2] + (c2[2] - c1[2]) * t),
    )


def draw_squircle_badge(canvas_size: int = 1024, state: str = "idle", pad_ratio: float = 0.04) -> Image.Image:
    """
    Draw the crisp HushFlow squircle mark at high resolution (recommended >= 512px)
    before LANCZOS downsampling for zero jagged edges.
    """
    img = Image.new("RGBA", (canvas_size, canvas_size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    pad = int(canvas_size * pad_ratio)
    x0, y0, x1, y1 = pad, pad, canvas_size - pad, canvas_size - pad
    w = x1 - x0
    radius = int(w * 0.24)

    # Subtle ambient slate-navy vertical gradient fill inside squircle mask
    mask = Image.new("L", (canvas_size, canvas_size), 0)
    mask_draw = ImageDraw.Draw(mask)
    mask_draw.rounded_rectangle([x0, y0, x1, y1], radius=radius, fill=255)

    grad = Image.new("RGBA", (canvas_size, canvas_size), (0, 0, 0, 0))
    grad_draw = ImageDraw.Draw(grad)
    if state == "recording":
        top_c, bot_c = (48, 22, 26), (26, 13, 16)
        border_c = (200, 83, 83, 230)
    elif state == "transcribing":
        top_c, bot_c = (44, 34, 19), (24, 19, 12)
        border_c = (207, 160, 74, 230)
    else:
        top_c, bot_c = (28, 35, 52), (16, 21, 33)
        border_c = (56, 68, 96, 225)

    for yy in range(y0, y1 + 1):
        t = (yy - y0) / max(1, (y1 - y0))
        rgb = _lerp_color(top_c, bot_c, t)
        grad_draw.line([(x0, yy), (x1, yy)], fill=(*rgb, 255))

    img = Image.composite(grad, img, mask)
    draw = ImageDraw.Draw(img)

    # Crisp hairline squircle border + subtle top inner highlight
    border_w = max(2, int(canvas_size * 0.016))
    draw.rounded_rectangle([x0, y0, x1, y1], radius=radius, outline=border_c, width=border_w)

    # Five balanced precision waveform pill bars (distinct asymmetric acoustic hush rhythm)
    cx = canvas_size / 2
    cy = canvas_size / 2

    bar_w = w * 0.072
    bar_gap = w * 0.056
    # Asymmetric calm wave profile (distinct from symmetric circles or uniform bars)
    heights = [0.22, 0.44, 0.56, 0.34, 0.18]
    total_bars_w = len(heights) * bar_w + (len(heights) - 1) * bar_gap
    start_x = cx - total_bars_w / 2

    for i, h_frac in enumerate(heights):
        bx0 = start_x + i * (bar_w + bar_gap)
        bx1 = bx0 + bar_w
        bh = (w * h_frac) / 2.0
        by0 = cy - bh
        by1 = cy + bh

        if state == "recording":
            fill_rgba = (250, 228, 228, 255) if i != 2 else (218, 92, 92, 255)
        elif state == "transcribing":
            fill_rgba = (250, 238, 210, 255) if i != 2 else (207, 160, 74, 255)
        else:
            if i == 2:
                # Primary warm studio off-white bar
                fill_rgba = (242, 244, 248, 255)
            elif i == 3:
                # Signature matte studio terracotta accent bar
                fill_rgba = (217, 107, 67, 255)
            elif i == 1:
                fill_rgba = (214, 222, 235, 248)
            else:
                fill_rgba = (138, 152, 178, 235)

        draw.rounded_rectangle([bx0, by0, bx1, by1], radius=bar_w / 2, fill=fill_rgba)

    return img


def render_tray_icon(state: str = "idle", size: int = 64) -> Image.Image:
    """Render an ultra-clean 4x supersampled system tray icon."""
    hi = draw_squircle_badge(canvas_size=size * 4, state=state, pad_ratio=0.06)
    return hi.resize((size, size), Image.Resampling.LANCZOS)


def generate_brand_files(target_dir: str = None) -> Tuple[str, str]:
    """
    Generate multi-resolution assets/hushflow.ico (256..16) and assets/hushflow_logo.png (512px),
    plus refresh legacy whisper_flow.ico cleanly so all icons look razor-sharp.
    """
    base = target_dir or SOURCE_DIR
    assets_dir = os.path.join(base, "assets")
    os.makedirs(assets_dir, exist_ok=True)

    master = draw_squircle_badge(canvas_size=1024, state="idle", pad_ratio=0.04)

    png_path = os.path.join(assets_dir, "hushflow_logo.png")
    master.resize((512, 512), Image.Resampling.LANCZOS).save(png_path, "PNG")

    ico_path = os.path.join(assets_dir, "hushflow.ico")
    ico_sizes = [(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (24, 24), (16, 16)]
    master.save(ico_path, format="ICO", sizes=ico_sizes)

    # Also refresh legacy whisper_flow.ico at root so no old bullseye icon survives
    legacy_ico = os.path.join(base, "whisper_flow.ico")
    master.save(legacy_ico, format="ICO", sizes=ico_sizes)

    return ico_path, png_path


def get_app_icon_path() -> str:
    """Return the sharpest available .ico file in bundled/source directories."""
    for parts in (("assets", "hushflow.ico"), ("whisper_flow.ico",)):
        p = get_resource_path(*parts)
        if os.path.exists(p):
            return p
    ico_path, _ = generate_brand_files(SOURCE_DIR)
    return ico_path


def get_app_logo_path() -> str:
    p = get_resource_path("assets", "hushflow_logo.png")
    if os.path.exists(p):
        return p
    _, png_path = generate_brand_files(SOURCE_DIR)
    return png_path


if __name__ == "__main__":
    ico, png = generate_brand_files()
    print(f"[OK] Generated crisp brand assets:\n  ICO: {ico}\n  PNG: {png}")
