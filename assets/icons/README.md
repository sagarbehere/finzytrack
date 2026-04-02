# Finzytrack Icon Set

## Design

The icon is a **T-Account** — the fundamental symbol of double-entry bookkeeping.
Read it two ways simultaneously:

- **Full crossbar + centre divider** = the letter **T** (T-Account)
- **Left crossbar portion + red left stroke + first left entry** = the letter **F** (Finzytrack)

The red left stroke is historically grounded: physical double-entry ledger books
traditionally print the left margin rule in red ink.

### Palette

| Role        | Hex       | Usage                                    |
|-------------|-----------|------------------------------------------|
| Background  | `#050C18` | Near-black navy                          |
| Blue        | `#60A5FA` | Crossbar, divider, entry lines           |
| Red         | `#F87171` | F accent stroke (half structural weight) |

Entry lines fade 80% → 50% → 28% → 14% opacity, suggesting entries receding in time.

---

## Regenerating all assets

```bash
pip install Pillow
python3 generate.py
```

The canonical design source is `master.svg` (1024×1024 viewBox).
Edit it in any vector tool, then update the proportions in `generate.py` to match.

---

## File inventory

```
finzytrack-icons/
├── master.svg                          Vector source (1024×1024 viewBox)
├── generate.py                         Regenerates everything below
├── README.md
├── favicon/
│   ├── favicon.ico                     16 + 32 + 48px combined
│   ├── favicon-16.png
│   ├── favicon-32.png
│   └── favicon.svg                     SVG favicon (modern browsers)
├── macos/
│   └── AppIcon.iconset/                Ready for iconutil (see below)
│       ├── icon_16x16.png  …  icon_512x512@2x.png   (10 files)
├── windows/
│   └── app.ico                         16/24/32/48/64/128/256px combined
├── linux/                              XDG hicolor layout
│   └── {16,24,32,48,64,128,256,512}x*/finzytrack.png
├── ios/
│   └── AppIcon.appiconset/             Drop into Xcode Assets.xcassets
│       ├── Contents.json
│       └── *.png  (13 sizes: 20–1024px)
└── android/
    ├── mipmap-{mdpi,hdpi,xhdpi,xxhdpi,xxxhdpi}/ic_launcher.png
    └── ic_launcher-web.png             512px for Play Store
```

---

## Platform notes

### macOS — generating the .icns binary

```bash
iconutil -c icns macos/AppIcon.iconset -o macos/AppIcon.icns
```

Run on a Mac. The `.icns` goes in your `.app` bundle at `Contents/Resources/`.

### iOS — Xcode

Drag `ios/AppIcon.appiconset/` into `Assets.xcassets`. Xcode reads `Contents.json`
and maps each PNG automatically. Xcode 15+ also accepts just `1024.png` and
generates all other sizes itself.

### Android — adaptive icons (Android 8+)

The `android/mipmap-*/` files use the legacy format (works on all versions).
For adaptive icons you need separate foreground (transparent bg, inner 72dp safe zone)
and background (solid `#050C18`) layers. Export from `master.svg` after removing
the background rect, centred in a 108dp canvas.

### Linux — system-wide install

```bash
for size in 16 24 32 48 64 128 256 512; do
  install -Dm644 linux/${size}x${size}/finzytrack.png \
    /usr/share/icons/hicolor/${size}x${size}/apps/finzytrack.png
done
gtk-update-icon-cache /usr/share/icons/hicolor/
```

### Favicon — HTML snippet

```html
<link rel="icon" type="image/svg+xml"  href="/favicon/favicon.svg">
<link rel="icon" type="image/png" sizes="32x32" href="/favicon/favicon-32.png">
<link rel="icon" type="image/png" sizes="16x16" href="/favicon/favicon-16.png">
<link rel="shortcut icon" href="/favicon/favicon.ico">
<!-- apple-touch-icon: copy ios/AppIcon.appiconset/180.png -->
<link rel="apple-touch-icon" sizes="180x180" href="/favicon/apple-touch-icon.png">
```

---

## Why SVG as the master?

SVG is the right format: resolution-independent, plain-text (diffs are readable
in git), editable in any tool, and thematically consistent — Finzytrack is a
plain-text accounting app, so its icon source is also a plain-text format.
The only exception is the macOS `.icns` binary, which is generated from the
SVG-derived PNGs using `iconutil` on a Mac.
