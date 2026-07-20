package bo.kadlaginvestment.crm.ui

import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.runtime.Composable
import androidx.compose.runtime.ReadOnlyComposable
import androidx.compose.runtime.compositionLocalOf
import androidx.compose.ui.platform.LocalConfiguration
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.TextUnit
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp

/**
 * Screen-size adaptation for the whole app.
 *
 * The problem this solves: every size in the app was a literal tuned on one
 * phone. On a narrow device (320-360dp) text wrapped badly and rows of buttons
 * squeezed; on a tablet everything stayed phone-sized in the middle of a huge
 * screen.
 *
 * Rather than hand-tune 24 screens, sizes now pass through [rsp] and [rdp],
 * which scale against a 392dp baseline (a Pixel-class phone). The scale is
 * deliberately gentle and clamped: type that shrinks proportionally with the
 * screen becomes unreadable on the small devices it was meant to help.
 *
 * Note this is separate from the user's font-scale accessibility setting.
 * Compose already applies that to `.sp`; we must not fight it, which is why
 * the clamp below is narrow and heights use minimums rather than fixed values.
 */

/** Width the current sizes were originally designed against. */
private const val BASELINE_WIDTH_DP = 392f

/** Type scales less than layout — readability floor matters more than fit. */
private const val TEXT_MIN = 0.92f
private const val TEXT_MAX = 1.15f

/** Spacing can move a little more; whitespace is the first thing to give. */
private const val SPACE_MIN = 0.85f
private const val SPACE_MAX = 1.30f

/** Width buckets, following the Material window size classes. */
enum class ScreenSize { Compact, Medium, Expanded }

val LocalScreen = compositionLocalOf { ScreenInfo() }

data class ScreenInfo(
    val widthDp: Int = 392,
    val heightDp: Int = 800,
    val size: ScreenSize = ScreenSize.Compact,
) {
    /** True on the narrow phones where two-column rows stop fitting. */
    val isNarrow: Boolean get() = widthDp < 360

    /** Tablets and unfolded foldables — worth using the extra width. */
    val isWide: Boolean get() = size != ScreenSize.Compact

    /** Sensible column count for a grid of cards at this width. */
    val gridColumns: Int
        get() = when {
            widthDp >= 900 -> 4
            widthDp >= 600 -> 3
            widthDp >= 380 -> 2
            else -> 1
        }

    /** Page padding: tighter on small screens, roomier on tablets. */
    val pagePadding: PaddingValues
        get() = when (size) {
            ScreenSize.Compact -> PaddingValues(horizontal = if (isNarrow) 12.dp else 16.dp)
            ScreenSize.Medium -> PaddingValues(horizontal = 24.dp)
            ScreenSize.Expanded -> PaddingValues(horizontal = 32.dp)
        }
}

@Composable
@ReadOnlyComposable
fun rememberScreenInfo(): ScreenInfo {
    val config = LocalConfiguration.current
    val w = config.screenWidthDp
    return ScreenInfo(
        widthDp = w,
        heightDp = config.screenHeightDp,
        size = when {
            w >= 840 -> ScreenSize.Expanded
            w >= 600 -> ScreenSize.Medium
            else -> ScreenSize.Compact
        },
    )
}

private fun scaleFor(widthDp: Int, min: Float, max: Float): Float =
    (widthDp / BASELINE_WIDTH_DP).coerceIn(min, max)

/** Responsive text size: `fontSize = rsp(13)` instead of `13.sp`. */
@Composable
@ReadOnlyComposable
fun rsp(base: Int): TextUnit =
    (base * scaleFor(LocalConfiguration.current.screenWidthDp, TEXT_MIN, TEXT_MAX)).sp

/** Responsive spacing: `rdp(12)` instead of `12.dp`. */
@Composable
@ReadOnlyComposable
fun rdp(base: Int): Dp =
    (base * scaleFor(LocalConfiguration.current.screenWidthDp, SPACE_MIN, SPACE_MAX)).dp

/**
 * Minimum touch target. Anything tappable should be at least this tall —
 * Material's accessibility guidance, and the reason several buttons in this
 * app were hard to hit.
 */
val TouchTarget: Dp = 48.dp
