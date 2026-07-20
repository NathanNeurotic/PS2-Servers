"""Guards for the single-source-of-truth palette (launcher/theme.py).

The look used to come from two divergent palettes (main.py and asset_skin.py);
these tests lock in that there is now ONE palette and that the asset-skin layer
maps its local names onto it, so the two theme layers can never drift back into
two different-looking skins.
"""

import re
import unittest

from launcher import theme


HEX = re.compile(r"^#[0-9a-fA-F]{6}$")

# Every key both theme layers reference. If a consumer needs a new key, it goes
# here too, so a missing key fails loudly in tests instead of at paint time.
REQUIRED_KEYS = {
    "bg", "panel", "panel2", "panel3", "edge", "text", "muted",
    "accent", "accent_hover", "accent2", "ok", "warn", "error", "entry", "disabled",
}


def _wcag_contrast(fg, bg):
    """Proper WCAG 2.x contrast ratio between two #rrggbb colours."""
    def _lin(hexc):
        chan = []
        for i in (1, 3, 5):
            c = int(hexc[i:i + 2], 16) / 255.0
            chan.append(c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4)
        r, g, b = chan
        return 0.2126 * r + 0.7152 * g + 0.0722 * b
    l1, l2 = _lin(fg), _lin(bg)
    hi, lo = max(l1, l2), min(l1, l2)
    return (hi + 0.05) / (lo + 0.05)


class PaletteContractTests(unittest.TestCase):
    def test_key_set_is_exactly_required(self):
        # Exact set, not just a subset: this also catches a stale/extra key being
        # reintroduced (the SURFACE-alias problem), not only a missing one.
        self.assertEqual(set(theme.PALETTE), REQUIRED_KEYS)

    def test_all_values_are_six_digit_hex(self):
        for key, value in theme.PALETTE.items():
            self.assertRegex(value, HEX, "%s=%r is not a 6-digit hex colour" % (key, value))

    def test_white_text_on_accents_meets_wcag_aa(self):
        # White is the Accent.TButton foreground on both the base accent and the
        # hover/press accent_hover; both must clear WCAG AA (4.5:1) for normal
        # text. This is the accessibility fix the review flagged.
        for key in ("accent", "accent_hover"):
            ratio = _wcag_contrast("#ffffff", theme.PALETTE[key])
            self.assertGreaterEqual(
                ratio, 4.5,
                "white on %s (%s) is %.2f:1, below WCAG AA 4.5:1"
                % (key, theme.PALETTE[key], ratio))

    def test_accent_hover_is_no_lighter_than_accent(self):
        # Guards the hover from regressing to a lighter fill (which would drop
        # white-text contrast, the original bug).
        self.assertGreaterEqual(
            _wcag_contrast("#ffffff", theme.PALETTE["accent_hover"]),
            _wcag_contrast("#ffffff", theme.PALETTE["accent"]))


class AssetSkinSharesThePaletteTests(unittest.TestCase):
    """asset_skin._soften_styles must colour cards/buttons from theme.PALETTE,
    not a private copy -- that shared reference is the whole point of theme.py.
    A fake ttk.Style records what colours get configured; we assert they are the
    canonical ones, so a future edit that reintroduces a second palette fails."""

    def test_soften_styles_uses_theme_colours(self):
        from launcher import asset_skin

        configured = {}

        class FakeStyle:
            def configure(self, name, **kw):
                configured.setdefault(name, {}).update(kw)

            def map(self, name, **kw):
                pass

        class FakeGui:
            class ttk:
                @staticmethod
                def Style(root):
                    return FakeStyle()

        class FakeRoot:
            def configure(self, **kw):
                configured.setdefault("__root__", {}).update(kw)

        asset_skin._soften_styles(FakeRoot(), FakeGui)

        # The card surface must be the shared panel colour, and the root
        # background the shared bg colour -- proof it read from theme.PALETTE.
        self.assertEqual(configured["Card.TFrame"]["background"], theme.PALETTE["panel"])
        self.assertEqual(configured["__root__"]["background"], theme.PALETTE["bg"])


if __name__ == "__main__":
    unittest.main()
