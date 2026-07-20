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
    "accent", "accent2", "ok", "warn", "error", "entry", "disabled",
}


class PaletteContractTests(unittest.TestCase):
    def test_required_keys_present(self):
        missing = REQUIRED_KEYS - set(theme.PALETTE)
        self.assertEqual(missing, set(), "PALETTE missing keys: %s" % missing)

    def test_all_values_are_six_digit_hex(self):
        for key, value in theme.PALETTE.items():
            self.assertRegex(value, HEX, "%s=%r is not a 6-digit hex colour" % (key, value))

    def test_surface_aliases_track_the_palette(self):
        # asset_skin maps surface/surface2 onto these; keep them honest.
        self.assertEqual(theme.SURFACE, theme.PALETTE["panel"])
        self.assertEqual(theme.SURFACE2, theme.PALETTE["panel2"])


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
