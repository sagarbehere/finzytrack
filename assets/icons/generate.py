#!/usr/bin/env python3
"""
Finzytrack Icon Generator
=========================
Generates all raster assets from master.svg.

Usage:
    python3 generate.py

Requirements:
    pip install Pillow

For master.svg to be the true source of truth (recommended):
    pip install cairosvg        # may also need: brew install cairo  (macOS)
    -- or --
    brew install librsvg        # provides rsvg-convert

Without a renderer, the script falls back to a PIL implementation whose
parameters mirror master.svg. In that mode, editing master.svg won't affect
the output — you'd also need to update the FALLBACK DESIGN CONSTANTS below.
"""

import io, json, os, shutil, subprocess, sys
from pathlib import Path
from PIL import Image, ImageDraw

MASTER_SVG = Path(__file__).parent / "master.svg"

# ── SVG renderer detection ────────────────────────────────────────────────────

def _try_cairosvg(size):
    try:
        import cairosvg
        data = cairosvg.svg2png(
            url=str(MASTER_SVG.resolve()),
            output_width=size, output_height=size)
        return Image.open(io.BytesIO(data)).convert("RGBA")
    except Exception:
        return None

def _try_rsvg(size):
    if not shutil.which("rsvg-convert"):
        return None
    try:
        r = subprocess.run(
            ["rsvg-convert", "-w", str(size), "-h", str(size),
             "-f", "png", str(MASTER_SVG)],
            capture_output=True, check=True)
        return Image.open(io.BytesIO(r.stdout)).convert("RGBA")
    except Exception:
        return None

def _try_inkscape(size):
    if not shutil.which("inkscape"):
        return None
    try:
        tmp = Path("/tmp/_ft_icon.png")
        subprocess.run(
            ["inkscape", str(MASTER_SVG),
             f"--export-filename={tmp}",
             f"--export-width={size}", f"--export-height={size}"],
            capture_output=True, check=True)
        return Image.open(tmp).convert("RGBA")
    except Exception:
        return None

# ── Fallback: PIL renderer ────────────────────────────────────────────────────
# These constants mirror master.svg exactly. Update them if you change the
# design without having an SVG renderer available.

BG    = (5,  12,  24)
BLUE  = (96, 165, 250)
RED   = (248, 113, 113)
RX        = 36/160
Y_CROSS   = 48/160
X_LEFT    = 18/160
X_RIGHT   = 142/160
X_DIV     = 80/160
Y_BOTTOM  = 134/160
ENTRY_Y   = [66/160, 82/160, 98/160, 114/160]
L_X1, L_X2 = 22/160, [70/160, 62/160, 67/160, 56/160]
R_X1, R_X2 = 86/160, [134/160, 108/160, 116/160, 100/160]
ENTRY_OPA   = [0.80, 0.50, 0.28, 0.14]
OPA_STRUCT, OPA_RED = 0.92, 0.92
W_STRUCT, W_RED, W_ENTRY = 2.0/160, 1.0/160, 1.4/160
RENDER_SCALE = 4

def _blend(bg, fg, a):
    return tuple(round(b + a*(f-b)) for b,f in zip(bg, fg))

def _pil_render(size):
    rs = size * RENDER_SCALE
    img = Image.new("RGB", (rs, rs), BG)
    draw = ImageDraw.Draw(img)
    rx = max(1, round(RX * rs))
    draw.rounded_rectangle([(0,0),(rs-1,rs-1)], radius=rx, fill=BG)

    lw_s = max(RENDER_SCALE, round(W_STRUCT * rs))
    lw_r = max(RENDER_SCALE, round(W_RED    * rs))
    lw_e = max(RENDER_SCALE, round(W_ENTRY  * rs))
    px = lambda r: round(r * rs)

    c_struct = _blend(BG, BLUE, OPA_STRUCT)
    c_red    = _blend(BG, RED,  OPA_RED)
    hs, hr   = lw_s // 2, lw_r // 2

    # Crossbar: flat left, round right
    xL, xR, yC = px(X_LEFT), px(X_RIGHT), px(Y_CROSS)
    draw.rectangle([xL,      yC-hs, xR,      yC+hs], fill=c_struct)
    draw.ellipse(  [xR-hs,   yC-hs, xR+hs,   yC+hs], fill=c_struct)
    # Centre divider: flat top, round bottom
    xD, yT, yB = px(X_DIV), yC-hs, px(Y_BOTTOM)
    draw.rectangle([xD-hs, yT,    xD+hs, yB   ], fill=c_struct)
    draw.ellipse(  [xD-hs, yB-hs, xD+hs, yB+hs], fill=c_struct)
    # Red stroke: flat top (crossbar top), round bottom — drawn last
    xF = px(X_LEFT)
    draw.rectangle([xF-hr, yT,    xF+hr, yB   ], fill=c_red)
    draw.ellipse(  [xF-hr, yB-hr, xF+hr, yB+hr], fill=c_red)
    # Entries: isolated lines, round caps are fine
    for opa, yr, x2r in zip(ENTRY_OPA, ENTRY_Y, L_X2):
        draw.line([(px(L_X1),px(yr)),(px(x2r),px(yr))],
                  fill=_blend(BG,BLUE,opa), width=lw_e)
    for opa, yr, x2r in zip(ENTRY_OPA, ENTRY_Y, R_X2):
        draw.line([(px(R_X1),px(yr)),(px(x2r),px(yr))],
                  fill=_blend(BG,BLUE,opa), width=lw_e)

    mask = Image.new("L", (rs,rs), 0)
    ImageDraw.Draw(mask).rounded_rectangle([(0,0),(rs-1,rs-1)],
                                           radius=rx, fill=255)
    out = img.convert("RGBA")
    out.putalpha(mask)
    return out.resize((size, size), Image.LANCZOS)

# ── Renderer selection (auto-detected once, then cached) ─────────────────────

_renderer = None

def draw_icon(size):
    global _renderer
    if _renderer is None:
        for name, fn in [("cairosvg", _try_cairosvg),
                         ("rsvg-convert", _try_rsvg),
                         ("inkscape", _try_inkscape)]:
            img = fn(size)
            if img is not None:
                _renderer = (name, fn)
                print(f"\n  Renderer: {name}  (renders master.svg directly)")
                return img
        _renderer = ("pil", None)
        print("\n  Renderer: PIL fallback")
        print("  To render master.svg directly: pip install cairosvg")
        print("  (or: brew install librsvg  for rsvg-convert)")
        return _pil_render(size)

    name, fn = _renderer
    return fn(size) if fn else _pil_render(size)

# ── File output ───────────────────────────────────────────────────────────────

def save(img, path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    img.save(path)
    print(f"  ✓  {path}")

def save_png(size, path):
    save(draw_icon(size), path)

def generate_all(out_dir="."):
    out = Path(out_dir)

    print("\n── Favicon ──────────────────────────────────────")
    save_png(16, out/"favicon/favicon-16.png")
    save_png(32, out/"favicon/favicon-32.png")
    ico = out/"favicon/favicon.ico"
    ico.parent.mkdir(parents=True, exist_ok=True)
    draw_icon(256).save(str(ico), format="ICO", sizes=[(16,16),(32,32),(48,48)])
    print(f"  ✓  {ico}")
    shutil.copy(out/"master.svg", out/"favicon/favicon.svg")
    print(f"  ✓  {out}/favicon/favicon.svg")

    print("\n── macOS iconset ────────────────────────────────")
    iconset = out/"macos/AppIcon.iconset"
    for name, size in [("icon_16x16.png",16),("icon_16x16@2x.png",32),
                       ("icon_32x32.png",32),("icon_32x32@2x.png",64),
                       ("icon_128x128.png",128),("icon_128x128@2x.png",256),
                       ("icon_256x256.png",256),("icon_256x256@2x.png",512),
                       ("icon_512x512.png",512),("icon_512x512@2x.png",1024)]:
        save_png(size, iconset/name)

    print("\n── Windows ──────────────────────────────────────")
    ico = out/"windows/app.ico"
    ico.parent.mkdir(parents=True, exist_ok=True)
    draw_icon(256).save(str(ico), format="ICO",
        sizes=[(16,16),(24,24),(32,32),(48,48),(64,64),(128,128),(256,256)])
    print(f"  ✓  {ico}")

    print("\n── Linux (hicolor) ──────────────────────────────")
    for size in [16,24,32,48,64,128,256,512]:
        save_png(size, out/f"linux/{size}x{size}/finzytrack.png")

    print("\n── iOS AppIcon.appiconset ───────────────────────")
    ios_dir = out/"ios/AppIcon.appiconset"
    for size in [20,29,40,58,60,76,80,87,120,152,167,180,1024]:
        save_png(size, ios_dir/f"{size}.png")
    (ios_dir/"Contents.json").write_text(json.dumps({"images":[
        {"filename":"20.png",   "idiom":"iphone","scale":"1x","size":"20x20"},
        {"filename":"40.png",   "idiom":"iphone","scale":"2x","size":"20x20"},
        {"filename":"60.png",   "idiom":"iphone","scale":"3x","size":"20x20"},
        {"filename":"29.png",   "idiom":"iphone","scale":"1x","size":"29x29"},
        {"filename":"58.png",   "idiom":"iphone","scale":"2x","size":"29x29"},
        {"filename":"87.png",   "idiom":"iphone","scale":"3x","size":"29x29"},
        {"filename":"80.png",   "idiom":"iphone","scale":"2x","size":"40x40"},
        {"filename":"120.png",  "idiom":"iphone","scale":"3x","size":"40x40"},
        {"filename":"120.png",  "idiom":"iphone","scale":"2x","size":"60x60"},
        {"filename":"180.png",  "idiom":"iphone","scale":"3x","size":"60x60"},
        {"filename":"20.png",   "idiom":"ipad",  "scale":"1x","size":"20x20"},
        {"filename":"40.png",   "idiom":"ipad",  "scale":"2x","size":"20x20"},
        {"filename":"29.png",   "idiom":"ipad",  "scale":"1x","size":"29x29"},
        {"filename":"58.png",   "idiom":"ipad",  "scale":"2x","size":"29x29"},
        {"filename":"40.png",   "idiom":"ipad",  "scale":"1x","size":"40x40"},
        {"filename":"80.png",   "idiom":"ipad",  "scale":"2x","size":"40x40"},
        {"filename":"76.png",   "idiom":"ipad",  "scale":"1x","size":"76x76"},
        {"filename":"152.png",  "idiom":"ipad",  "scale":"2x","size":"76x76"},
        {"filename":"167.png",  "idiom":"ipad",  "scale":"2x","size":"83.5x83.5"},
        {"filename":"1024.png", "idiom":"ios-marketing","scale":"1x","size":"1024x1024"},
    ],"info":{"author":"xcode","version":1}}, indent=2))
    print(f"  ✓  {ios_dir}/Contents.json")

    print("\n── Android ──────────────────────────────────────")
    for density, size in [("mipmap-mdpi",48),("mipmap-hdpi",72),
                          ("mipmap-xhdpi",96),("mipmap-xxhdpi",144),
                          ("mipmap-xxxhdpi",192)]:
        save_png(size, out/f"android/{density}/ic_launcher.png")
    save_png(512, out/"android/ic_launcher-web.png")

    print("\n── Done ─────────────────────────────────────────")
    print("\nNOTE — macOS .icns:")
    print("  iconutil -c icns macos/AppIcon.iconset -o macos/AppIcon.icns\n")

if __name__ == "__main__":
    import sys
    generate_all(sys.argv[1] if len(sys.argv) > 1 else ".")
