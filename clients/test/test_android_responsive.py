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
