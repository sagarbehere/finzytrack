"""Pydantic models for dashboard color themes.

A dashboard theme is the single source of truth for chart/widget colors (see
`dev-docs/dashboard-color-system.md`). Each role carries a light and a dark
variant; the frontend resolves `{{theme.*}}` tokens against the active theme at
render time. Themes are plain JSON files under `config/dashboard-themes/`,
seeded from `resources/seed_config/dashboard-themes/`.
"""

from typing import Dict, List, Optional

from pydantic import BaseModel, Field


class ModeColor(BaseModel):
    """A single color with a light-mode and a dark-mode variant."""
    light: str
    dark: str


class ModePalette(BaseModel):
    """An ordered categorical palette, per mode."""
    light: List[str]
    dark: List[str]


class Valence(BaseModel):
    """The favorability band — the only place green/amber/red/complete live."""
    good: ModeColor
    warn: ModeColor
    bad: ModeColor
    complete: ModeColor  # exactly-on-budget (replaces blue)


class Thresholds(BaseModel):
    """Where the valence bands switch. Not a color, but it decides which color
    shows for a given usage fraction."""
    warnAt: float = 0.85  # amber (warn) onset, as a fraction of budget used


class Stickiness(BaseModel):
    """How categories keep their color across charts. See dev-docs §6."""
    hierarchical: str = "family-depth"  # hue = top-level family, lightness = depth
    flat: str = "hash"                  # hash(label) → slot, de-collided in-chart
    depthLightenStep: float = 0.18      # lightness added per hierarchy depth level


class Overflow(BaseModel):
    """What happens when a chart has more categories than the palette."""
    mode: str = "lightness-cycle"
    lightenStep: float = 0.15  # lightness delta applied on each palette wrap


class States(BaseModel):
    """Interaction states, derived from the base colors (not new hues)."""
    hoverLighten: float = 0.12    # lighten on hover
    muteOpacity: float = 0.35     # opacity for de-emphasized elements
    selectedLighten: float = 0.08  # lighten for a selected element


class Tints(BaseModel):
    """Fill/tint strengths for surfaces that wash a base color over a background."""
    heatmapAlpha: float = 0.30  # opacity of adherence heat-map cell fills


class DashboardTheme(BaseModel):
    """A complete dashboard color theme.

    Editing order (tiered — most users only touch tier 1):
      1. `valence` (the favorability bars) + `categorical` (pie/treemap identity) + `brand`.
      2. `baseline`, `series` (named-series mapping), `accountPins`.
      3. `thresholds`, `stickiness`, `overflow`, `states`, `tints` — fine knobs;
         adjust only if you really want to. Every one has a sensible default.
    """
    id: str
    name: str
    description: str = ""
    version: int = 1

    # A human note carried in the file itself (self-documenting; optional).
    readme: Optional[str] = None

    # ── Tier 1 — the colors most people care about ──────────────────────────
    brand: ModeColor
    baseline: ModeColor
    valence: Valence
    categorical: ModePalette

    # ── Tier 2 — mapping & pins ─────────────────────────────────────────────
    # Named fixed series (e.g. budget, actual, income) → a `{{theme.*}}` token or a hex.
    series: Dict[str, str] = Field(default_factory=dict)
    # Optional per-account color pins (account → token/hex). Accounts vary per
    # user, so pins are opt-in, not required.
    accountPins: Dict[str, str] = Field(default_factory=dict)

    # ── Tier 3 — fine knobs (all default sensibly; touch only if you want) ───
    thresholds: Thresholds = Field(default_factory=Thresholds)
    stickiness: Stickiness = Field(default_factory=Stickiness)
    overflow: Overflow = Field(default_factory=Overflow)
    states: States = Field(default_factory=States)
    tints: Tints = Field(default_factory=Tints)


class DashboardThemeSummary(BaseModel):
    """Lightweight entry for the theme picker (Phase 5)."""
    id: str
    name: str
    description: str = ""


class DashboardThemeListResponse(BaseModel):
    themes: List[DashboardThemeSummary]
    active: str


class DashboardThemeWriteRequest(BaseModel):
    content: dict


class DashboardThemeWriteResponse(BaseModel):
    path: str
