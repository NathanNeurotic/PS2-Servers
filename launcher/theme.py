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

The palette is a true-black PS2 theme: the window sits on near-black, surfaces
rise through midnight-blue elevations (bg -> panel -> panel2 -> panel3), the
hairlines are blue rather than grey, and the accent is the deep PlayStation
blue with an icy lighter blue for text that has to glow on black.
"""

PALETTE = {
    "bg":       "#05080f",   # window background (true black, blue undertone)
    "panel":    "#0a0f1c",   # card / strip surface (near-black midnight)
    "panel2":   "#10182b",   # hover / slightly elevated surface
    "panel3":   "#182446",   # selected tab / pressed surface (clear midnight blue)
    "edge":     "#223258",   # subtle midnight-blue hairline border
    "text":     "#e9eef7",   # primary text
    "muted":    "#8d9db8",   # secondary / helper text
    "accent":   "#1d4ed8",   # deep PlayStation blue -- button fill; dark enough
                             # that white text clears WCAG AA (~6:1) on it
    "accent_hover": "#1e40af",  # darker blue for button hover/press (keeps
                                # white text well above AA on the active state)
    "accent2":  "#7cc4ff",   # icy lighter blue -- used as TEXT on dark surfaces
                             # (hints, selected-tab label), never as a fill under
                             # white text, so it stays bright on black
    "ok":       "#43d597",   # running / recommended (green)
    "warn":     "#e7b750",   # warning (amber)
    "error":    "#f16d7f",   # error / stopped-in-error (red)
    "entry":    "#060a12",   # input fields + terminal background (near-black)
    "disabled": "#55637d",   # disabled text
}
