"""Single source of truth for the launcher's visual theme.

Historically the look came from TWO palettes that drifted apart: one in
``main.py``'s ``apply_theme`` and a second, slightly different one in
``asset_skin._soften_styles`` (which runs later and wins for cards, strips,
footer, buttons and tabs). That split made the UI look half-themed and made a
restyle need edits in two places that had to be kept in sync by hand.

Both layers now import ``PALETTE`` from here, so they can never diverge again.
Keeping this module pure data -- no imports from the rest of ``launcher`` -- makes
it safe to import from anywhere in the package (``main``, ``asset_skin``,
``gui``) without risking an import cycle.

The palette is a calm, modern dark theme. It keeps the PS2 blue identity but
trades the old near-black + neon look for lifted slate surfaces, a clear
elevation scale (bg -> panel -> panel2 -> panel3), subtle slate hairlines
instead of glowing borders, and one confident accent used sparingly.
"""

PALETTE = {
    "bg":       "#0d1420",   # window background (deep slate-navy)
    "panel":    "#161f2e",   # card / strip surface
    "panel2":   "#1f2a3d",   # hover / slightly elevated surface
    "panel3":   "#293850",   # selected tab / pressed surface
    "edge":     "#31415d",   # subtle hairline border (was a neon blue)
    "text":     "#e9eef7",   # primary text
    "muted":    "#93a3bd",   # secondary / helper text
    "accent":   "#3f8cff",   # primary blue -- buttons, selected accents
    "accent2":  "#74b6ff",   # lighter blue -- hints, selected-tab text
    "ok":       "#43d597",   # running / recommended (green)
    "warn":     "#e7b750",   # warning (amber)
    "error":    "#f16d7f",   # error / stopped-in-error (red)
    "entry":    "#0f1826",   # input fields + terminal background
    "disabled": "#5c6c88",   # disabled text
}

# Elevation aliases used by the asset-skin layer, so its local names
# (surface/surface2/danger) map onto the canonical palette above.
SURFACE = PALETTE["panel"]
SURFACE2 = PALETTE["panel2"]
