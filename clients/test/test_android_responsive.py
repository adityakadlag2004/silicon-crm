"""Static checks on the Android UI source.

The Compose UI can't be exercised from the Django test suite, but the source
can be linted for the patterns that made screens break on small devices. These
are the rules that were violated across 24 screens before the responsive pass.
"""
import pathlib
import re

from django.test import TestCase

ANDROID = pathlib.Path("mobile/android/app/src/main/java/bo/kadlaginvestment/crm")
# Sizing primitives live here and are allowed to use raw units.
EXEMPT = {"Responsive.kt", "Theme.kt"}


def _kotlin_files():
    if not ANDROID.exists():          # mobile/ not checked out
        return []
    return [p for p in sorted(ANDROID.rglob("*.kt")) if p.name not in EXEMPT]


class AndroidResponsiveTests(TestCase):
    def test_no_hardcoded_font_sizes(self):
        """`fontSize = 13.sp` is tuned for one device; rsp(13) scales."""
        offenders = []
        for p in _kotlin_files():
            for num, line in enumerate(p.read_text().splitlines(), 1):
                if re.search(r"fontSize = \d+\.sp", line):
                    offenders.append(f"{p.name}:{num}")
        self.assertEqual(offenders, [],
                         f"use rsp(n) instead of n.sp for font sizes: {offenders}")

    def test_no_fixed_heights_on_interactive_elements(self):
        """A fixed height clips its label at a large system font scale.

        Under 36dp is fine — those are bars, dots and dividers, which should
        stay exactly the size they are.
        """
        offenders = []
        for p in _kotlin_files():
            for num, line in enumerate(p.read_text().splitlines(), 1):
                for m in re.finditer(r"\.height\((\d+)\.dp\)", line):
                    if int(m.group(1)) >= 36:
                        offenders.append(f"{p.name}:{num} height({m.group(1)}.dp)")
        self.assertEqual(offenders, [],
                         f"use .heightIn(min = n.dp) so content can grow: {offenders}")

    def test_no_fixed_width_text_labels(self):
        """Modifier.width() on a Text truncates at large font scales."""
        offenders = []
        for p in _kotlin_files():
            text = p.read_text()
            for m in re.finditer(r"Text\([^)]*Modifier\.width\((\d+)\.dp\)", text, re.S):
                if int(m.group(1)) >= 48:
                    offenders.append(f"{p.name}:{text[:m.start()].count(chr(10)) + 1}")
        self.assertEqual(offenders, [],
                         f"use widthIn(min =, max =) on text labels: {offenders}")

    def test_responsive_helpers_exist(self):
        """The scale helpers the rest of the rules depend on."""
        if not ANDROID.exists():
            self.skipTest("android sources not present")
        src = (ANDROID / "ui" / "Responsive.kt").read_text()
        for symbol in ("fun rsp(", "fun rdp(", "ScreenSize", "TouchTarget"):
            self.assertIn(symbol, src)


class ScaleMathTests(TestCase):
    """Pin the numbers rsp()/rdp() actually produce on real devices.

    Mirrors the Kotlin so a change to the clamps has to be deliberate. The
    intent: smaller phones get slightly smaller type (more fits, fewer
    truncations) but never below a readable floor; tablets get a little more.
    """

    BASELINE = 392.0
    TEXT_MIN, TEXT_MAX = 0.92, 1.15
    SPACE_MIN, SPACE_MAX = 0.85, 1.30

    def _text_scale(self, width):
        return min(max(width / self.BASELINE, self.TEXT_MIN), self.TEXT_MAX)

    def _space_scale(self, width):
        return min(max(width / self.BASELINE, self.SPACE_MIN), self.SPACE_MAX)

    def test_text_stays_readable_on_the_smallest_phones(self):
        # A 320dp device would scale to 0.82 unclamped; the floor keeps 13sp
        # at roughly 12sp rather than a squinting 10.6sp.
        for width in (320, 360):
            self.assertEqual(round(self._text_scale(width), 2), 0.92, width)
            self.assertGreaterEqual(13 * self._text_scale(width), 11.9)

    def test_baseline_device_is_unchanged(self):
        self.assertEqual(self._text_scale(392), 1.0)
        self.assertEqual(self._space_scale(392), 1.0)

    def test_tablets_scale_up_but_are_capped(self):
        # Without the cap a 840dp tablet would render 13sp as 27sp.
        self.assertEqual(round(self._text_scale(840), 2), self.TEXT_MAX)
        self.assertLess(13 * self._text_scale(840), 16)

    def test_spacing_moves_more_than_type(self):
        """Whitespace should give before text does."""
        narrow = 320
        self.assertLess(self._space_scale(narrow), self._text_scale(narrow))
        wide = 840
        self.assertGreater(self._space_scale(wide), self._text_scale(wide))

    def test_kotlin_constants_match_this_test(self):
        """If the Kotlin clamps change, this test must be updated with them."""
        if not ANDROID.exists():
            self.skipTest("android sources not present")
        src = (ANDROID / "ui" / "Responsive.kt").read_text()
        for name, value in (("BASELINE_WIDTH_DP", "392f"),
                            ("TEXT_MIN", "0.92f"), ("TEXT_MAX", "1.15f"),
                            ("SPACE_MIN", "0.85f"), ("SPACE_MAX", "1.30f")):
            self.assertIn(f"{name} = {value}", src,
                          f"{name} changed in Kotlin — update ScaleMathTests")
