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


def _balanced_args(text, open_paren_ix):
    """The argument text of a call whose "(" is at `open_paren_ix`.

    A naive `[^)]*` stops at the first ")" — which lands inside `if (d)` and
    made this rule report false positives.
    """
    depth, i = 0, open_paren_ix
    while i < len(text):
        if text[i] == "(":
            depth += 1
        elif text[i] == ")":
            depth -= 1
            if depth == 0:
                return text[open_paren_ix + 1:i]
        i += 1
    return None


def _split_top_level(args):
    """Split an argument list on commas that aren't inside (), [] or quotes."""
    out, buf, depth, quote = [], [], 0, False
    for ch in args:
        if quote:
            buf.append(ch)
            if ch == '"':
                quote = False
            continue
        if ch == '"':
            quote = True; buf.append(ch)
        elif ch in "([":
            depth += 1; buf.append(ch)
        elif ch in ")]":
            depth -= 1; buf.append(ch)
        elif ch == "," and depth == 0:
            out.append("".join(buf).strip()); buf = []
        else:
            buf.append(ch)
    if buf:
        out.append("".join(buf).strip())
    return out


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
        for symbol in ("fun rsp(", "fun rdp(", "isWideScreen", "TouchTarget"):
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


class AndroidAccessibilityTests(TestCase):
    """Rules the app broke everywhere before this pass.

    The web side has `test_theme_coverage` enforcing aria-labels and scroll
    wrappers; Android had no equivalent, so an emoji rendered as clickable text
    counted as a button and TalkBack read out "↻".
    """

    def test_no_clickable_glyph_buttons(self):
        """`Text("↻", modifier = Modifier.clickable {...})` is not a button.

        It has no ripple, no role, nothing for a screen reader to announce, and
        about 28dp of tap area. Use IconButton (48dp, labelled) instead.
        """
        glyphs = ["↻", "✕", "←", "›", "▾"]
        offenders = []
        for p in _kotlin_files():
            text = p.read_text()
            # A Text(...) whose content is a bare glyph and which is clickable
            # somewhere in the same call.
            for m in re.finditer(r"Text\(\s*\"([^\"]{1,3})\"(.{0,400}?)\)", text, re.S):
                content, rest = m.group(1), m.group(2)
                if content.strip() in glyphs and "clickable" in rest:
                    line = text[:m.start()].count("\n") + 1
                    offenders.append(f"{p.name}:{line} ({content})")
        self.assertEqual(
            offenders, [],
            "use IconButton with a contentDescription, not clickable glyph Text: "
            f"{offenders}",
        )

    def test_icons_declare_a_content_description(self):
        """Every Icon needs a label or an explicit null (decorative)."""
        offenders = []
        for p in _kotlin_files():
            text = p.read_text()
            for m in re.finditer(r"\bIcon\(", text):
                args = _balanced_args(text, m.end() - 1)
                if args is None or "contentDescription" in args:
                    continue
                # `Icon(vector, null)` (decorative) and `Icon(vector, "Label")`
                # both supply the description positionally.
                positional = _split_top_level(args)
                if len(positional) >= 2 and (
                    positional[1] == "null" or positional[1].startswith('"')
                ):
                    continue
                line = text[:m.start()].count("\n") + 1
                offenders.append(f"{p.name}:{line}")
        self.assertEqual(offenders, [],
                         f"Icon needs contentDescription (or explicit null): {offenders}")

    def test_no_free_text_date_entry(self):
        """Dates go through DateField/the platform picker, never a keyboard.

        Typing "YYYY-MM-DD" on a phone is a guaranteed source of 400s; the app
        had three such fields while also shipping native pickers elsewhere.
        """
        offenders = []
        for p in _kotlin_files():
            for num, line in enumerate(p.read_text().splitlines(), 1):
                stripped = line.lstrip()
                if stripped.startswith(("*", "//", "/*")):
                    continue          # comments may name the wire format
                if "YYYY-MM-DD" in line and ("label" in line or "placeholder" in line):
                    offenders.append(f"{p.name}:{num}")
        self.assertEqual(offenders, [],
                         f"use DateField(...) instead of a typed date: {offenders}")

    def test_time_pickers_follow_the_device_clock_setting(self):
        """`TimePickerDialog(..., false)` forces 12-hour regardless of the
        user's setting. Fields.kt passes DateFormat.is24HourFormat."""
        offenders = []
        for p in _kotlin_files():
            if p.name == "Fields.kt":
                continue
            text = p.read_text()
            for m in re.finditer(r"TimePickerDialog\(", text):
                line = text[:m.start()].count("\n") + 1
                offenders.append(f"{p.name}:{line}")
        self.assertEqual(
            offenders, [],
            f"use pickTime/pickDateTime from Fields.kt, not a raw dialog: {offenders}",
        )

    def test_writes_do_not_swallow_failures(self):
        """`when (ApiClient.post(...)) { ... else -> reload }` treats an error
        as success. Every call site must handle Result.Error explicitly."""
        offenders = []
        for p in _kotlin_files():
            text = p.read_text()
            for m in re.finditer(r"when \(ApiClient\.post\(", text):
                # Look at the branch list that follows.
                tail = text[m.start():m.start() + 700]
                if "Result.Error" not in tail:
                    line = text[:m.start()].count("\n") + 1
                    offenders.append(f"{p.name}:{line}")
        self.assertEqual(
            offenders, [],
            f"handle ApiClient.Result.Error — silent failures lose real work: {offenders}",
        )


class AndroidArchitectureTests(TestCase):
    def test_navigation_state_survives_rotation(self):
        """Tab and route state must be rememberSaveable.

        With plain `remember`, rotating the phone dropped the user back on Home
        and refetched every screen.
        """
        for name in ("ShellActivity.kt", "TasksActivity.kt"):
            src = (ANDROID / name).read_text()
            self.assertIn(
                "rememberSaveable", src,
                f"{name}: navigation state must survive rotation",
            )

    def test_role_is_not_probed_via_the_dashboard(self):
        """Four screens used to pull the full dashboard payload — ten aggregate
        queries for an admin — purely to read `role`."""
        offenders = []
        for p in _kotlin_files():
            if p.name == "DashboardScreen.kt":
                continue        # the real dashboard is allowed to
            text = p.read_text()
            code = "\n".join(
                l for l in text.splitlines()
                if not l.lstrip().startswith(("*", "//", "/*"))
            )
            if "api/app/dashboard/" in code and "ApiClient.get" in code:
                offenders.append(p.name)
        self.assertEqual(offenders, [],
                         f"use Session.load() / api/app/me/ instead: {offenders}")

    def test_notification_icons_are_monochrome_assets(self):
        """A full-colour launcher icon renders as a white square in the status
        bar on every Android 5+."""
        offenders = []
        for p in ANDROID.rglob("*.kt"):
            for num, line in enumerate(p.read_text().splitlines(), 1):
                if "setSmallIcon" in line and "ic_stat_" not in line:
                    offenders.append(f"{p.name}:{num}")
        for p in ANDROID.rglob("*.java"):
            for num, line in enumerate(p.read_text().splitlines(), 1):
                if "setSmallIcon" in line and "ic_stat_" not in line:
                    offenders.append(f"{p.name}:{num}")
        self.assertEqual(offenders, [],
                         f"notifications must use the monochrome ic_stat_ki: {offenders}")
